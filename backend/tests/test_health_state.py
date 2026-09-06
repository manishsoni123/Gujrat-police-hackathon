"""Health poller state machine (CONTRACT §5.6 + Amendments 2026-09-05: `not_streaming`, alert only on online→offline)."""

from __future__ import annotations

import pytest

from app.services.health_poller import STEADY_STATES, decide_status, worker_degraded, worker_item
from app.core.tz import utcnow

OFFLINE_AFTER = 3


def _run(prev: str, checks: list[tuple[bool, bool, int, str]], ever_seen: bool, fail: int = 0) -> list[str]:
    out = []
    for is_ready, has_video, delta, flag in checks:
        prev, fail = decide_status(prev, is_ready, has_video, delta, flag, fail, ever_seen, OFFLINE_AFTER)
        out.append(prev)
    return out


def test_catalogue_not_live_camera_never_goes_offline() -> None:
    # a catalogue live=false camera: not ready, source_flag='catalogue', never seen
    seq = _run("unknown", [(False, False, 0, "catalogue")] * 5, ever_seen=False)
    assert seq == ["unknown", "unknown", "not_streaming", "not_streaming", "not_streaming"]
    assert "offline" not in seq


def test_never_answering_source_is_not_streaming_not_offline() -> None:
    seq = _run("unknown", [(False, False, 0, "probe")] * 3, ever_seen=False)
    assert seq[-1] == "not_streaming"


def test_online_camera_that_stops_goes_offline_after_three_checks() -> None:
    seq = _run("online", [(False, False, 0, "mediamtx")] * 3, ever_seen=True)
    assert seq == ["online", "online", "offline"]


def test_degraded_camera_that_stops_goes_offline() -> None:
    seq = _run("degraded", [(False, False, 0, "probe")] * 3, ever_seen=True)
    assert seq[-1] == "offline"


def test_not_streaming_camera_that_comes_up_is_online_then_offline_on_loss() -> None:
    status, fail = decide_status("not_streaming", True, True, 0, "probe", 3, False, OFFLINE_AFTER)
    assert (status, fail) == ("online", 0)
    # once seen, a later loss is a real outage
    seq = _run("online", [(False, False, 0, "mediamtx")] * 3, ever_seen=True)
    assert seq[-1] == "offline"


def test_ready_check_resets_fail_count_and_keeps_state_below_threshold() -> None:
    assert decide_status("offline", True, True, 10, "mediamtx", 7, True, OFFLINE_AFTER) == ("online", 0)
    assert decide_status("online", True, False, 10, "mediamtx", 0, True, OFFLINE_AFTER) == ("degraded", 0)
    assert decide_status("online", True, True, 0, "mediamtx", 0, True, OFFLINE_AFTER) == ("degraded", 0)
    assert decide_status("online", True, True, 0, "probe", 0, True, OFFLINE_AFTER) == ("online", 0)
    assert decide_status("online", False, False, 0, "mediamtx", 0, True, OFFLINE_AFTER) == ("online", 1)
    assert decide_status("unknown", False, False, 0, "probe", 0, False, OFFLINE_AFTER) == ("unknown", 1)


@pytest.mark.parametrize("state", STEADY_STATES)
def test_steady_states_are_kept_below_threshold(state: str) -> None:
    assert decide_status(state, False, False, 0, "mediamtx", 0, state != "not_streaming", OFFLINE_AFTER)[0] == state


def test_worker_degraded_flag() -> None:
    assert worker_degraded(None) is False
    assert worker_degraded({}) is False
    assert worker_degraded({"detector": {"requested": "contour", "active": "contour", "degraded": False}}) is False
    assert worker_degraded({"detector": {"requested": "onnx", "active": "contour", "degraded": True}}) is True
    assert worker_degraded({"detector": {"requested": "onnx", "active": "contour"}}) is True
    assert worker_degraded({"detector": {"requested": "auto", "active": "contour"}}) is False
    item = worker_item("live-1", "live", False, "1.0", "contour", [{"id": 1, "fps_actual": 4.5}, {"id": 2, "fps_actual": 5}], utcnow(), False, {"detector": {"requested": "onnx", "active": "contour", "degraded": True}})
    assert item["degraded"] is True and item["cameras"] == 2 and item["fps_total"] == 9.5 and item["extra"]["detector"]["active"] == "contour"
