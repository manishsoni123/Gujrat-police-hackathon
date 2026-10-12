"""Camera `anpr_status` (CONTRACT Amendments 2026-10-13): the real live-ANPR processing state, not `anpr_enabled`.

Regression: on 12 Oct camera 81 showed "ANPR live" while no worker processed it — 13 cameras were enabled and the
live worker only takes the lowest-id `ANPR_MAX_CAMERAS` (8) of them.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import settings
from app.db.models import Camera
from app.services import serializers
from app.services.health_poller import heartbeat_stale, live_anpr_view
from app.services.serializers import AnprLiveView, anpr_live_view, anpr_status

NOW = datetime(2026, 10, 12, 19, 0, tzinfo=timezone.utc)


def _cam(cid: int, enabled: bool = True, status: str = "online", url: str | None = "rtsp://relay/x") -> Camera:
    return Camera(
        id=cid, source="manual", external_id=str(cid), name=f"cam {cid}", department_id=1, type="ip", ownership="govt_dept",
        rtsp_url=url, codec="H264", status=status, maintenance_status="ok", anpr_enabled=enabled, record_enabled=False,
        created_via="manual", created_at=NOW, updated_at=NOW,
    )


def _entry(cid: Any, state: str | None = "running") -> dict[str, Any]:
    e: dict[str, Any] = {"id": cid, "fps_actual": 4.0, "frames": 100, "reads": 0, "decoder_restarts": 0, "last_error": None}
    if state is not None:
        e["state"] = state
    return e


# ---- anpr_live_view ----------------------------------------------------------------------------------------------
def test_live_view_maps_decoder_states() -> None:
    view = anpr_live_view([[_entry(1), _entry(2, "starting"), _entry(3, "reconnecting"), _entry(4, "error"), _entry(5, "stopped"), _entry(6, "weird"), _entry(7, None)]])
    assert view.workers == 1
    assert view.states == {1: "live", 2: "starting", 3: "reconnecting", 4: "reconnecting", 6: "reconnecting", 7: "live"}
    assert 5 not in view.states  # a stopped decoder is not processing


def test_live_view_keeps_the_best_state_across_workers() -> None:
    view = anpr_live_view([[_entry(1, "reconnecting"), _entry(2)], [_entry(1), _entry(2, "starting"), _entry(3, "reconnecting")], [_entry(3, "starting")]])
    assert view.workers == 3
    assert view.states == {1: "live", 2: "live", 3: "starting"}


def test_live_view_drops_a_worker_that_sent_its_shutdown_heartbeat() -> None:
    # Pipeline.shutdown posts one last heartbeat with every camera `stopped`; the row stays fresh for 3 × HEARTBEAT_S
    view = anpr_live_view([[_entry(1, "stopped"), _entry(2, "stopped")]])
    assert view.workers == 0 and view.states == {}


def test_live_view_counts_an_idle_worker_and_skips_bad_entries() -> None:
    view = anpr_live_view([[], None, [_entry("x"), _entry(None), "junk", _entry("9")]])  # type: ignore[list-item]
    assert view.workers == 3  # an idle worker (no cameras yet) is up
    assert view.states == {9: "live"}


# ---- anpr_status -------------------------------------------------------------------------------------------------
def test_status_off_when_the_worker_is_never_handed_the_camera() -> None:
    view = AnprLiveView(1, {1: "live", 2: "live", 3: "live"})
    assert anpr_status(_cam(1, enabled=False), view) == "off"
    assert anpr_status(_cam(2, status="retired"), view) == "off"  # /internal/anpr-config skips retired cameras
    assert anpr_status(_cam(3, url=None), view) == "off"  # ... and cameras without a source URL
    assert anpr_status(_cam(1, enabled=False), None) == "off"


def test_status_follows_the_worker_state() -> None:
    view = AnprLiveView(1, {1: "live", 2: "starting", 3: "reconnecting"})
    assert [anpr_status(_cam(i), view) for i in (1, 2, 3)] == ["live", "starting", "reconnecting"]


def test_status_queued_over_anpr_max_cameras() -> None:
    """13 enabled cameras, cap 8: the worker lists the 8 lowest ids; the other 5 (camera 81 on 12 Oct) are queued."""
    enabled = [11, 12, 13, 14, 15, 16, 17, 18, 81, 82, 83, 84, 85]
    view = anpr_live_view([[_entry(i) for i in sorted(enabled)[:8]]])
    got = {i: anpr_status(_cam(i), view) for i in enabled}
    assert [i for i, s in got.items() if s == "live"] == enabled[:8]
    assert [i for i, s in got.items() if s == "queued"] == [81, 82, 83, 84, 85]
    assert got[81] != "live"


def test_status_no_worker_when_no_live_worker_is_up() -> None:
    assert anpr_status(_cam(1), AnprLiveView()) == "no_worker"
    assert anpr_status(_cam(1), anpr_live_view([[_entry(1, "stopped")]])) == "no_worker"


def test_status_unknown_without_a_view() -> None:
    assert anpr_status(_cam(1), None) is None


def test_every_status_is_declared() -> None:
    view = AnprLiveView(1, {1: "live", 2: "starting", 3: "reconnecting"})
    seen = {anpr_status(_cam(1), view), anpr_status(_cam(2), view), anpr_status(_cam(3), view), anpr_status(_cam(4), view),
            anpr_status(_cam(5), AnprLiveView()), anpr_status(_cam(6, enabled=False), view)}
    assert seen == set(serializers.ANPR_STATUSES)


def test_camera_full_carries_anpr_status_next_to_anpr_enabled() -> None:
    view = AnprLiveView(1, {1: "live"})
    assert serializers.camera_full(_cam(1), True, None, view)["anpr_status"] == "live"
    assert serializers.camera_full(_cam(2), False, anpr_view=view)["anpr_status"] == "queued"
    off = serializers.camera_full(_cam(3, enabled=False))
    assert off["anpr_status"] == "off" and off["anpr_enabled"] is False
    assert serializers.camera_full(_cam(4))["anpr_status"] is None  # caller loaded no view


# ---- staleness (one rule for /health/summary and the camera status) -----------------------------------------------
def test_heartbeat_stale_after_three_missed_heartbeats() -> None:
    limit = timedelta(seconds=3 * settings.HEARTBEAT_S)
    assert heartbeat_stale(NOW - limit, NOW) is False
    assert heartbeat_stale(NOW - limit - timedelta(seconds=1), NOW) is True
    assert heartbeat_stale(NOW, NOW) is False
    assert heartbeat_stale(None, NOW) is True


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeDb:
    """Stands in for AsyncSession: returns the given (cameras, last_heartbeat_at) rows and records the statement."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        return _Result(self.rows)


def test_live_anpr_view_reads_once_and_drops_stale_workers(monkeypatch) -> None:
    from app.services import health_poller

    monkeypatch.setattr(health_poller, "utcnow", lambda: NOW)
    fresh = NOW - timedelta(seconds=5)
    stale = NOW - timedelta(seconds=3 * settings.HEARTBEAT_S + 1)
    db = _FakeDb([([_entry(91), _entry(92, "reconnecting")], fresh), ([_entry(93)], stale)])
    view = asyncio.run(live_anpr_view(db))  # type: ignore[arg-type]
    assert len(db.statements) == 1
    sql = str(db.statements[0])
    assert "anpr_workers.mode" in sql  # only live-mode workers
    assert view.workers == 1 and view.states == {91: "live", 92: "reconnecting"}
    assert anpr_status(_cam(93), view) == "queued"  # listed only by a stale worker
    assert anpr_status(_cam(93), asyncio.run(live_anpr_view(_FakeDb([([_entry(93)], stale)])))) == "no_worker"  # type: ignore[arg-type]
