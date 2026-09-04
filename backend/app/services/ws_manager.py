"""In-process WebSocket fan-out with per-connection scope and a bounded queue (CONTRACT §9)."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from app.core.rbac import Scope
from app.core.tz import iso_z, utcnow

log = logging.getLogger("sentinel.ws")

QUEUE_MAX = 200


@dataclass(eq=False)  # identity hash: connections live in a set
class Connection:
    ws: WebSocket
    channel: str  # 'alerts' | 'health' | 'reads:<camera_id>'
    username: str
    role: str
    scope: Scope
    camera_id: int | None = None
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAX))
    dropped: int = 0

    def in_scope(self, dept_id: int | None, district: str | None) -> bool:
        return self.scope.allows(dept_id, district)


class WsManager:
    def __init__(self) -> None:
        self._conns: set[Connection] = set()
        self._lock = asyncio.Lock()

    async def register(self, conn: Connection) -> None:
        async with self._lock:
            self._conns.add(conn)

    async def unregister(self, conn: Connection) -> None:
        async with self._lock:
            self._conns.discard(conn)

    def count(self, channel: str | None = None) -> int:
        return sum(1 for c in self._conns if channel is None or c.channel == channel)

    @staticmethod
    def envelope(msg_type: str, data: Any) -> str:
        return json.dumps({"type": msg_type, "ts": iso_z(utcnow()), "data": data}, default=str)

    def _enqueue(self, conn: Connection, payload: str) -> None:
        try:
            conn.queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                conn.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            conn.dropped += 1
            if conn.dropped % 50 == 1:
                log.warning("ws slow client %s on %s: dropped %d", conn.username, conn.channel, conn.dropped)
            try:
                conn.queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def broadcast(
        self,
        channel: str,
        msg_type: str,
        data: Any,
        camera_dept: int | None = None,
        camera_district: str | None = None,
        scoped: bool = True,
    ) -> None:
        """Queue a message for every connection on `channel` whose scope allows the camera."""
        payload = self.envelope(msg_type, data)
        for conn in list(self._conns):
            if conn.channel != channel:
                continue
            if scoped and not conn.in_scope(camera_dept, camera_district):
                continue
            self._enqueue(conn, payload)

    def send(self, conn: Connection, msg_type: str, data: Any) -> None:
        """Queue one message for a single connection (e.g. `pong`)."""
        self._enqueue(conn, self.envelope(msg_type, data))

    def broadcast_scoped_fn(self, channel: str, msg_type: str, data_fn) -> None:
        """Per-connection payload (e.g. stats computed per scope). data_fn(conn) -> data | None."""
        for conn in list(self._conns):
            if conn.channel != channel:
                continue
            data = data_fn(conn)
            if data is None:
                continue
            self._enqueue(conn, self.envelope(msg_type, data))

    def connections(self, channel: str | None = None) -> list[Connection]:
        return [c for c in self._conns if channel is None or c.channel == channel]


manager = WsManager()
