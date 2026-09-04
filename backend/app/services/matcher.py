"""Watchlist matcher (CONTRACT §5.12 alert creation rules).

`MatchEngine` is pure (in-memory map, fuzzy, priority, suppression decision) and is what
`tests/test_matcher.py` exercises. `WatchlistMatcher` wraps it with DB reload + alert
persistence + WS/webhook fan-out.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tz import utcnow
from app.db.models import Alert, Camera, Event, PlateRead, Sighting, Watchlist
from app.services import serializers, settings_service as cfg
from app.services.plates import levenshtein
from app.services.ws_manager import manager

log = logging.getLogger("sentinel.matcher")

PRIORITY_ORDER = ("critical", "high", "medium", "low")
DEFAULT_RE_ALERT_MINUTES = 60
REASON_FLOOR = {
    "stolen": "critical",
    "wanted": "critical",
    "blacklisted": "high",
    "arrested": "high",
    "missing": "medium",
    "unidentified_body": "medium",
    "suspect": "low",
    "other": "low",
}


def _rank(p: str) -> int:
    return PRIORITY_ORDER.index(p) if p in PRIORITY_ORDER else len(PRIORITY_ORDER) - 1


def effective_priority(watchlist_priority: str, reason: str, confidence_level: str) -> str:
    """max(watchlist.priority, reason_floor), stepped down one level for `possible` (W6)."""
    floor = REASON_FLOOR.get(reason, "low")
    best = min(_rank(watchlist_priority), _rank(floor))
    if confidence_level == "possible":
        best = min(best + 1, len(PRIORITY_ORDER) - 1)
    return PRIORITY_ORDER[best]


@dataclass(frozen=True)
class WatchEntry:
    id: int
    plate_norm: str
    reason: str
    priority: str
    name: str | None = None
    source: str = "manual"


@dataclass(frozen=True)
class Match:
    entry: WatchEntry
    confidence_level: str  # exact | possible
    priority: str


class MatchEngine:
    def __init__(self) -> None:
        self._map: dict[str, WatchEntry] = {}

    def load(self, entries: list[WatchEntry]) -> None:
        # effective-priority order (W6 floor applied) so fuzzy ties resolve to the most serious entry
        ordered = sorted(entries, key=lambda e: (_rank(effective_priority(e.priority, e.reason, "exact")), e.id))
        self._map = {e.plate_norm: e for e in ordered if e.plate_norm}

    def __len__(self) -> int:
        return len(self._map)

    def match(self, plate_norm: str, confidence: float, is_valid_format: bool, fuzzy_min_conf: float) -> Match | None:
        if not is_valid_format or not plate_norm:
            return None
        entry = self._map.get(plate_norm)
        if entry is not None:
            return Match(entry, "exact", effective_priority(entry.priority, entry.reason, "exact"))
        if confidence >= fuzzy_min_conf:
            for key, entry in self._map.items():
                if abs(len(key) - len(plate_norm)) <= 1 and levenshtein(plate_norm, key) == 1:
                    return Match(entry, "possible", effective_priority(entry.priority, entry.reason, "possible"))
        return None

    @staticmethod
    def should_suppress(
        existing_created_at: datetime,
        existing_status: str,
        now: datetime,
        suppression_s: int,
        re_alert_minutes: int = DEFAULT_RE_ALERT_MINUTES,
    ) -> bool:
        """Attach the read to the existing alert instead of raising a new one when either

        - the existing alert is younger than `suppression_s` (any status), or
        - it is still open (`new`/`acknowledged`) and younger than `re_alert_minutes`.

        `re_alert_minutes` (`alerts.re_alert_minutes`, env `ALERT_RE_ALERT_MINUTES`, default 60)
        bounds how long an unattended open alert keeps absorbing repeat reads before the same
        plate on the same camera raises a fresh alert; `0` disables the re-alert entirely.
        """
        age = now - existing_created_at
        if age <= timedelta(seconds=suppression_s):
            return True
        if existing_status not in ("new", "acknowledged"):
            return False
        if re_alert_minutes <= 0:
            return True
        return age <= timedelta(minutes=re_alert_minutes)


class WatchlistMatcher:
    def __init__(self) -> None:
        self.engine = MatchEngine()
        self.loaded_at: datetime | None = None

    async def reload(self, db: AsyncSession) -> None:
        now = utcnow()
        rows = (
            await db.execute(
                select(Watchlist).where(
                    Watchlist.entity_type == "vehicle",
                    Watchlist.is_active.is_(True),
                    Watchlist.plate_norm.isnot(None),
                )
            )
        ).scalars().all()
        entries = [
            WatchEntry(r.id, r.plate_norm or "", r.reason, r.priority, r.name, r.source)
            for r in rows
            if r.expires_at is None or r.expires_at > now
        ]
        self.engine.load(entries)
        self.loaded_at = now
        log.info("watchlist matcher loaded %d plates", len(entries))

    async def maybe_reload(self, db: AsyncSession) -> None:
        if self.loaded_at is None or (utcnow() - self.loaded_at) > timedelta(seconds=60):
            await self.reload(db)

    async def process_read(
        self,
        db: AsyncSession,
        read: PlateRead,
        sighting: Sighting | None,
        camera: Camera,
    ) -> tuple[Alert | None, bool]:
        """Return (alert, created). Caller commits; WS/webhook fan-out happens here after flush."""
        m = self.engine.match(read.plate_norm, float(read.confidence), read.is_valid_format, cfg.get_float("alerts.fuzzy_min_conf"))
        if m is None:
            return None, False
        now = utcnow()
        suppression_s = cfg.get_int("alerts.suppression_s")
        re_alert_minutes = cfg.get_int("alerts.re_alert_minutes")
        existing = (
            await db.execute(
                select(Alert)
                .where(Alert.watchlist_id == m.entry.id, Alert.camera_id == camera.id, Alert.type == "watchlist_hit")
                .order_by(Alert.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        wl = await db.get(Watchlist, m.entry.id)
        if existing is not None and MatchEngine.should_suppress(existing.created_at, existing.status, now, suppression_s, re_alert_minutes):
            existing.read_count += 1
            existing.last_read_at = read.captured_at
            existing.updated_at = now
            if m.confidence_level == "exact" and existing.confidence_level != "exact":
                existing.confidence_level = "exact"
                existing.priority = m.priority
            if existing.sighting_id is None and sighting is not None:
                existing.sighting_id = sighting.id
            await db.flush()
            manager.broadcast("alerts", "alert_update", serializers.alert_update_payload(existing), camera.department_id, camera.district)
            return existing, False

        alert = Alert(
            type="watchlist_hit",
            status="new",
            priority=m.priority,
            confidence_level=m.confidence_level,
            camera_id=camera.id,
            watchlist_id=m.entry.id,
            sighting_id=sighting.id if sighting else None,
            read_id=read.id,
            plate_norm=read.plate_norm,
            snapshot_path=read.crop_path,
            snapshot_sha256=read.crop_sha256,
            read_count=1,
            last_read_at=read.captured_at,
            latency_ms=max(0, int((now - read.captured_at).total_seconds() * 1000)),
            created_at=now,
            updated_at=now,
        )
        db.add(alert)
        await db.flush()
        db.add(
            Event(
                camera_id=camera.id,
                occurred_at=read.captured_at,
                type="watchlist_hit",
                note=f"{m.confidence_level} match for {read.plate_norm} ({m.entry.reason}, {m.priority})",
                sighting_id=sighting.id if sighting else None,
                read_id=read.id,
                alert_id=alert.id,
                is_auto=True,
                created_at=now,
            )
        )
        if wl is not None:
            wl.hit_count = (wl.hit_count or 0) + 1
            wl.last_hit_at = now
        await db.flush()
        self.broadcast_alert(alert, camera, wl, read)
        return alert, True

    @staticmethod
    def broadcast_alert(alert: Alert, camera: Camera, wl: Watchlist | None, read: PlateRead | None) -> None:
        from app.services.notifications import dispatch_alert_created  # local import: avoids a cycle

        data = serializers.alert_item(alert, camera, wl, read)
        title, body = serializers.notify_text(alert, camera, wl, read)
        data.update({"sound": alert.type != "camera_offline", "notify_title": title, "notify_body": body})
        manager.broadcast("alerts", "alert", data, camera.department_id, camera.district)
        dispatch_alert_created(data, alert.priority)


matcher = WatchlistMatcher()
