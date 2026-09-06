"""HTTP client for the internal API (CONTRACT.md section 7) plus the background sender thread.

Error policy (section 7 preamble): network errors and 5xx are retried with 2 -> 30 s backoff;
4xx drops the item and logs the body, except 401 which is fatal (worker exits with code 3).
With ``API_DRY_RUN=1`` nothing is sent; every call prints one JSON line to stdout instead, so
tests and the synthetic accuracy tool can read the payloads back.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import requests

log = logging.getLogger("anpr.client")

RETRY_STATUS = {500, 502, 503, 504}


class ApiError(Exception):
    """Base class for API failures."""


class RetryableError(ApiError):
    """Network failure or 5xx - retry with backoff."""


class DroppedError(ApiError):
    """4xx other than 401 - the item is dropped after logging."""


class FatalAuthError(ApiError):
    """401 from the API - the key is wrong, exit code 3."""


def iso_utc(dt: datetime | None = None) -> str:
    """ISO-8601 UTC with millisecond precision and a trailing Z."""
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class ApiClient:
    """Thin requests wrapper with the X-API-Key header and the contract's status handling."""

    def __init__(self, base_url: str, api_key: str, dry_run: bool = False, timeout_s: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.dry_run = dry_run
        self.timeout_s = timeout_s
        self._session = requests.Session()
        self._session.headers.update({"X-API-Key": api_key, "User-Agent": "sentinel-anpr/1.0.0-phase1"})
        self._print_lock = threading.Lock()

    # ---- low level ----------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.base_url}/api{path}"

    def _emit_dry_run(self, method: str, path: str, payload: Any, files: dict[str, bytes] | None) -> None:
        # Multipart bodies carry the JSON document as the form field "payload"; print it decoded so
        # tools (anpr/tools/eval_synthetic.py) read the same shape the API would receive.
        if isinstance(payload, dict) and set(payload) == {"payload"} and isinstance(payload["payload"], str):
            try:
                payload = json.loads(payload["payload"])
            except ValueError:
                pass
        line = {
            "dry_run": True,
            "method": method,
            "endpoint": path,
            "payload": payload,
            "files": {name: len(data) for name, data in (files or {}).items()},
        }
        with self._print_lock:
            print(json.dumps(line, separators=(",", ":"), default=str), flush=True)

    def _request(self, method: str, path: str, *, json_body: Any = None, data: dict[str, Any] | None = None,
                 files: dict[str, bytes] | None = None, params: dict[str, Any] | None = None) -> Any:
        if self.dry_run and method != "GET":
            self._emit_dry_run(method, path, json_body if json_body is not None else data, files)
            return {}
        try:
            multipart = None
            if files is not None:
                multipart = {name: (f"{name}.jpg", blob, "image/jpeg") for name, blob in files.items()}
            resp = self._session.request(
                method, self._url(path), json=json_body, data=data, files=multipart, params=params,
                timeout=self.timeout_s,
            )
        except requests.RequestException as exc:
            raise RetryableError(f"{method} {path}: {exc.__class__.__name__}: {exc}") from exc
        if resp.status_code == 401:
            raise FatalAuthError(f"{method} {path}: 401 {resp.text[:200]}")
        if resp.status_code in RETRY_STATUS:
            raise RetryableError(f"{method} {path}: {resp.status_code} {resp.text[:200]}")
        if resp.status_code >= 400:
            raise DroppedError(f"{method} {path}: {resp.status_code} {resp.text[:500]}")
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise DroppedError(f"{method} {path}: non-JSON response {resp.text[:200]}") from exc

    # ---- endpoints (section 7) ------------------------------------------
    def get_config(self, mode: str) -> dict[str, Any]:
        return self._request("GET", "/internal/anpr-config", params={"mode": mode})

    def post_detections(self, payload: dict[str, Any], files: dict[str, bytes]) -> dict[str, Any]:
        return self._request(
            "POST", "/internal/detections",
            data={"payload": json.dumps(payload, separators=(",", ":"))}, files=files,
        )

    def post_snapshot(self, camera_id: int, captured_at: str, jpeg: bytes) -> None:
        self._request(
            "POST", "/internal/snapshots",
            data={"camera_id": str(camera_id), "captured_at": captured_at}, files={"file": jpeg},
        )

    def post_object_counts(self, camera_id: int, counts: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/internal/object-counts", json_body={"camera_id": camera_id, "object_counts": counts})

    def post_heartbeat(self, body: dict[str, Any]) -> None:
        self._request("POST", "/internal/heartbeat", json_body=body)

    def post_events(self, camera_id: int, events: list[dict[str, Any]], files: dict[str, bytes]) -> dict[str, Any]:
        payload = {"camera_id": camera_id, "events": events}
        return self._request(
            "POST", "/internal/events",
            data={"payload": json.dumps(payload, separators=(",", ":"))}, files=files,
        )


# ---------------------------------------------------------------------------
# Background sender
# ---------------------------------------------------------------------------
@dataclass
class Job:
    kind: str                       # detections | events | object_counts | snapshot | heartbeat
    camera_id: int | None
    call: Callable[[], Any]
    attempts: int = 0
    created: float = field(default_factory=time.monotonic)


class Sender(threading.Thread):
    """Serialises all POSTs on one thread so the inference loop never blocks on the network.

    Detection batches, events and object counts are queued FIFO (bounded; the oldest is dropped
    when full). Snapshots and heartbeats are "latest wins" per key, so a slow API never builds a
    backlog of stale images.
    """

    def __init__(self, min_backoff_s: float = 2.0, max_backoff_s: float = 30.0, max_attempts: int = 6,
                 queue_size: int = 400) -> None:
        super().__init__(name="sender", daemon=True)
        self.min_backoff_s = min_backoff_s
        self.max_backoff_s = max_backoff_s
        self.max_attempts = max_attempts
        self._queue: queue.Queue[Job] = queue.Queue(maxsize=queue_size)
        self._latest: dict[str, Job] = {}
        self._latest_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self.fatal: BaseException | None = None
        self.stats = {"sent": 0, "dropped": 0, "retried": 0, "failed": 0}

    # ---- producer side ------------------------------------------------
    def submit(self, job: Job) -> None:
        while True:
            try:
                self._queue.put_nowait(job)
                break
            except queue.Full:
                try:
                    old = self._queue.get_nowait()
                    self.stats["dropped"] += 1
                    log.warning("sender queue full - dropped %s for camera %s", old.kind, old.camera_id)
                except queue.Empty:
                    pass
        self._wake.set()

    def submit_latest(self, key: str, job: Job) -> None:
        with self._latest_lock:
            self._latest[key] = job
        self._wake.set()

    def pending(self) -> int:
        with self._latest_lock:
            return self._queue.qsize() + len(self._latest)

    def stop(self, flush_timeout_s: float = 10.0) -> None:
        """Ask the thread to drain the queue and exit (SIGTERM path)."""
        self._stop_event.set()
        self._wake.set()
        self.join(timeout=flush_timeout_s)

    # ---- consumer side ------------------------------------------------
    def _next_latest(self) -> Job | None:
        with self._latest_lock:
            if not self._latest:
                return None
            key = next(iter(self._latest))
            return self._latest.pop(key)

    def _backoff(self, attempts: int) -> float:
        return min(self.max_backoff_s, self.min_backoff_s * (2 ** max(0, attempts - 1)))

    def _run_job(self, job: Job) -> None:
        while not self.fatal:
            job.attempts += 1
            try:
                job.call()
                self.stats["sent"] += 1
                return
            except FatalAuthError as exc:
                log.error("API rejected the internal key (401): %s", exc)
                self.fatal = exc
                return
            except DroppedError as exc:
                self.stats["dropped"] += 1
                log.warning("dropped %s for camera %s: %s", job.kind, job.camera_id, exc)
                return
            except RetryableError as exc:
                if job.kind in ("snapshot", "heartbeat") or job.attempts >= self.max_attempts:
                    self.stats["failed"] += 1
                    log.warning("giving up on %s for camera %s after %d attempt(s): %s",
                                job.kind, job.camera_id, job.attempts, exc)
                    return
                delay = self._backoff(job.attempts)
                self.stats["retried"] += 1
                log.warning("%s for camera %s failed (%s); retry in %.0f s", job.kind, job.camera_id, exc, delay)
                if self._stop_event.wait(delay) and self._stop_event.is_set() and job.attempts >= 2:
                    return
            except Exception as exc:  # noqa: BLE001 - never let the sender die
                self.stats["failed"] += 1
                log.exception("unexpected error sending %s: %s", job.kind, exc)
                return

    def run(self) -> None:
        while True:
            job: Job | None = None
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                job = self._next_latest()
            if job is None:
                if self._stop_event.is_set():
                    return
                self._wake.wait(timeout=0.5)
                self._wake.clear()
                continue
            self._run_job(job)
            if self.fatal:
                return
