"""Relay-source policy (CONTRACT Amendments 2026-09-05, "pace your load"): persistent relay sources for the cameras
we actually process, 10-minute close-after for the rest, change detection against MediaMTX's own config echo,
dial pacing, and the health poller's per-tick decision. DB-free."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.config import settings as env
from app.services import mediamtx_client as mc
from app.services.health_poller import probe_target, select_probe_candidates, tick_action

SANDBOX = "rtsp://team%40example.in:s3cret-pass@103.250.160.189:8554/stream/cam07"


def cam(**kw):
    base = {"id": 67, "rtsp_url": SANDBOX, "anpr_enabled": False, "source": "sandbox", "status": "online", "record_enabled": False, "codec": "H264", "meta": None, "live": True}
    base.update(kw)
    return SimpleNamespace(**base)


# ---- which cameras hold a persistent upstream connection -----------------------------------------------------

def test_relay_policy_persistent_only_for_processed_cameras() -> None:
    assert mc.relay_policy(cam(anpr_enabled=True)) == mc.PERSISTENT  # the live ANPR set
    assert mc.relay_policy(cam(id=60, source="own", rtsp_url=f"{env.MEDIAMTX_RTSP_URL}/own_gate")) == mc.PERSISTENT  # own gate
    assert mc.relay_policy(cam()) == mc.ON_DEMAND  # a sandbox camera nobody processes
    assert mc.relay_policy(cam(source="csv")) == mc.ON_DEMAND
    assert mc.relay_policy(cam(anpr_enabled=True, status="retired")) == mc.ON_DEMAND  # retired: never held
    assert mc.relay_policy(cam(anpr_enabled=True, rtsp_url=None)) == mc.ON_DEMAND
    assert mc.relay_policy(cam(anpr_enabled=None)) == mc.ON_DEMAND


def test_source_path_body_encodes_the_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import settings_service as cfg

    monkeypatch.setattr(cfg, "get", lambda key: 45 if key == "sandbox.probe_timeout_s" else None)
    persistent = mc.source_path_body(SANDBOX, False, persistent=True)
    assert persistent["sourceOnDemand"] is False and persistent["rtspTransport"] == "tcp" and persistent["source"] == SANDBOX
    on_demand = mc.source_path_body(SANDBOX, False)
    assert on_demand["sourceOnDemand"] is True
    assert on_demand["sourceOnDemandCloseAfter"] == "600s"  # 10 min, was 60 s
    assert on_demand["sourceOnDemandStartTimeout"] == "55s"  # probe cap + 10 s, unchanged
    assert mc.relay_policy_body(mc.PERSISTENT) == {"sourceOnDemand": False, "sourceOnDemandCloseAfter": "600s"}
    assert mc.relay_policy_body(mc.ON_DEMAND)["sourceOnDemand"] is True
    # the _h264 re-encode keeps its short close-after: it reads the parent relay path, never upstream
    assert mc.transcode_path_body(67, False)["runOnDemandCloseAfter"] == "60s"


def test_config_policy_reads_mediamtx_echo() -> None:
    assert mc.config_policy({"name": "cam_67", "sourceOnDemand": False}) == mc.PERSISTENT
    assert mc.config_policy({"name": "cam_67", "sourceOnDemand": True}) == mc.ON_DEMAND
    assert mc.config_policy({"name": "cam_67_h264"}) is None and mc.config_policy(None) is None


# ---- change detection: a PATCH that changes nothing is skipped (MediaMTX restarts the source on every change) --

def test_go_duration_parsing() -> None:
    assert mc.go_duration_s("600s") == 600.0 and mc.go_duration_s("10m0s") == 600.0 and mc.go_duration_s("12h0m0s") == 43200.0
    assert mc.go_duration_s("1m") == 60.0 and mc.go_duration_s("200ms") == pytest.approx(0.2) and mc.go_duration_s("1.5s") == 1.5
    assert mc.go_duration_s("tcp") is None and mc.go_duration_s(True) is None and mc.go_duration_s(15) is None and mc.go_duration_s("") is None


def test_config_matches_compares_durations_as_seconds_and_bools_strictly() -> None:
    echo = {"name": "cam_67", "source": SANDBOX, "sourceOnDemand": True, "sourceOnDemandStartTimeout": "55s", "sourceOnDemandCloseAfter": "10m0s", "rtspTransport": "tcp", "record": False, "recordSegmentDuration": "1m0s", "recordDeleteAfter": "12h0m0s", "recordPath": mc.RECORD_PATH, "recordFormat": "fmp4"}
    assert mc.config_matches(echo, {"sourceOnDemand": True, "sourceOnDemandCloseAfter": "600s"})
    assert mc.config_matches(echo, {"recordSegmentDuration": "1m", "recordDeleteAfter": "12h", "rtspTransport": "tcp", "source": SANDBOX})
    assert not mc.config_matches(echo, {"sourceOnDemand": False})
    assert not mc.config_matches(echo, {"sourceOnDemandCloseAfter": "60s"})
    assert not mc.config_matches(echo, {"source": SANDBOX.replace("cam07", "cam08")})
    assert not mc.config_matches(echo, {"maxReaders": 0})  # key MediaMTX did not echo → cannot claim equality
    assert not mc.config_matches(None, {"sourceOnDemand": True})


# ---- pacing: one new upstream connection per RELAY_DIAL_SPACING_S ----------------------------------------------

def test_dial_gate_spaces_dials() -> None:
    async def run() -> list[float]:
        gate = mc.DialGate(0.05)
        loop = asyncio.get_running_loop()
        stamps: list[float] = []

        async def dial() -> None:
            await gate.wait()
            stamps.append(loop.time())

        await asyncio.gather(dial(), dial(), dial())
        return sorted(stamps)

    stamps = asyncio.run(run())
    assert len(stamps) == 3
    assert stamps[1] - stamps[0] >= 0.045 and stamps[2] - stamps[1] >= 0.045
    assert mc.dial_gate.spacing_s == mc.RELAY_DIAL_SPACING_S == 3.0


# ---- health poller: relay state only for persistent cameras, paced probes for the rest -------------------------

def test_tick_action_per_policy() -> None:
    assert tick_action(mc.PERSISTENT, True, True) == "observed"
    assert tick_action(mc.ON_DEMAND, True, True) == "observed"
    assert tick_action(mc.PERSISTENT, True, False) == "relay_only"  # the relay retries it itself: no probe, no extra dial
    assert tick_action(mc.PERSISTENT, None, False) == "relay_only"
    assert tick_action(mc.ON_DEMAND, True, False) == "probe"
    assert tick_action(mc.ON_DEMAND, None, False) == "probe"
    assert tick_action(mc.ON_DEMAND, False, False) == "catalogue"  # catalogue live=false: never probed
    assert tick_action(mc.PERSISTENT, False, False) == "catalogue"


def test_probe_target_direct_by_default_relay_on_request() -> None:
    url, t = probe_target(SANDBOX, 67, 6, 45, source="sandbox", via="direct")
    assert url == SANDBOX and t == 45.0  # one short connection, no relay copy held for 10 min
    url, t = probe_target(SANDBOX, 67, 6, 45, source="sandbox", via="relay")
    assert url == f"{env.MEDIAMTX_RTSP_URL}/cam_67" and t == 60.0  # cap + relay start margin (55 s window + 5 s)
    url, t = probe_target(SANDBOX, 67, 6, 45, source="csv", via="relay")
    assert url == SANDBOX and t == 45.0  # non-sandbox external sources keep the direct probe
    assert probe_target(f"{env.MEDIAMTX_RTSP_URL}/stream/3", 3, 6, 45, source="sandbox", via="relay") == (f"{env.MEDIAMTX_RTSP_URL}/cam_3", 6.0)
    assert env.HEALTH_PROBE_VIA in ("direct", "relay")


def test_probe_budget_two_per_tick_every_15_minutes() -> None:
    now = datetime(2026, 9, 5, 16, 30, tzinfo=timezone.utc)
    cands = [(61, None), (62, now - timedelta(minutes=20)), (63, now - timedelta(minutes=5)), (64, now - timedelta(minutes=16)), (65, None)]
    probe_now, deferred = select_probe_candidates(cands, now, min_interval_s=900, max_per_tick=2)
    assert probe_now == [61, 65]  # never probed first
    assert set(deferred) == {62, 63, 64}  # 63 too recent; 62/64 due but over budget → next tick
    probe_now, deferred = select_probe_candidates([(62, now - timedelta(minutes=20)), (64, now - timedelta(minutes=16))], now, 900, 2)
    assert probe_now == [62, 64] and deferred == []
    assert env.HEALTH_PROBE_MIN_INTERVAL_S == 900 and env.HEALTH_PROBE_PARALLEL == 2 and env.HEALTH_PROBE_MAX == 2


def test_seed_last_probe_keeps_the_latest_time() -> None:
    from app.services.health_poller import seed_last_probe

    now = datetime(2026, 9, 5, 16, 40, tzinfo=timezone.utc)
    rows = [(86, now - timedelta(minutes=5)), (89, now - timedelta(minutes=20)), (90, None)]
    seeded = seed_last_probe(rows, {86: now - timedelta(minutes=1)})
    assert 86 not in seeded  # memory is newer than the log
    assert seeded == {89: now - timedelta(minutes=20)}  # None rows are ignored, older log times are taken
    assert seed_last_probe([], {}) == {}
