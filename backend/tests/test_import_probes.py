"""Organiser-sandbox import probes: the exact ffprobe command, pacing through the shared DialGate, bounded
parallelism, the stop on repeated credential refusals, and what a refusal does to `live` (DB-free)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.adapters import rtsp
from app.adapters.base import ProbeResult, SourceCamera
from app.adapters.sentinel_portal import PortalConfig, SentinelPortalAdapter
from app.services import mediamtx_client as mc
from app.services import sentinel_import as si
from app.services.camera_importer import ImportOutcome
from app.services.sentinel_catalogue import StreamConfig, build_rtsp_url, source_camera_row

SECRET = "s3cret-pass"
STREAM = StreamConfig(host="103.250.160.189", rtsp_port=8554, whep_port=8889, email="team@example.in", password=SECRET)
REFUSED = "rtsp://team%40example.in:***@103.250.160.189:8554/stream/cam02: Server returned 401 Unauthorized (authorization failed)"
FFPROBE_OK = {"streams": [{"codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "30/1", "r_frame_rate": "30/1", "profile": "High", "has_b_frames": 0}]}


def cams(n: int) -> list[SourceCamera]:
    return [SourceCamera(external_id=f"cam{i:02d}", name=f"{i:02d}", rtsp_url=build_rtsp_url(STREAM, f"cam{i:02d}")) for i in range(1, n + 1)]


class FakeProc:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return json.dumps(FFPROBE_OK).encode(), b""


# ---- the command the import runs is the one that works by hand ---------------------------------------------------


def test_import_probe_runs_the_manual_ffprobe_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ffprobe -rtsp_transport tcp -timeout <µs>` against `rtsp://<e-mail %40-encoded>:<pw>@host:8554/stream/<id>`
    (the command that answered h264 1920x1080 by hand); the 45 s setting becomes 45 000 000 µs."""
    argv: list[list[str]] = []

    async def fake_exec(*cmd: str, **_kw: object) -> FakeProc:
        argv.append(list(cmd))
        return FakeProc()

    monkeypatch.setattr(rtsp.asyncio, "create_subprocess_exec", fake_exec)
    adapter = SentinelPortalAdapter(stream=STREAM, portal=PortalConfig(url="", email="", password=""), probe_timeout_s=45, probe_parallel=6)
    cam = cams(1)[0]
    res = asyncio.run(adapter.probe(cam))
    assert (res.ok, res.codec, res.resolution, res.fps, res.b_frames) == (True, "H264", "1920x1080", 30.0, 0)
    url = "rtsp://team%40example.in:" + SECRET + "@103.250.160.189:8554/stream/cam01"
    assert cam.rtsp_url == url
    assert argv == [[
        "ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-timeout", "45000000",
        "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height,avg_frame_rate,r_frame_rate,profile,has_b_frames",
        "-of", "json", url,
    ]]


# ---- what a refusal means ----------------------------------------------------------------------------------------


def test_auth_refusal_is_recognised() -> None:
    assert si.is_auth_refusal(REFUSED)
    assert si.is_auth_refusal("Server returned 403 Forbidden (access denied)")
    for other in ("rtsp://h/stream/CAM03: Server returned 400 Bad Request", "Server returned 404 Not Found", "path not configured",
                  "timeout", "Connection refused", "", None, "cam401: Connection timed out"):
        assert not si.is_auth_refusal(other), other


def test_credential_refusal_keeps_live_unknown_so_the_health_poller_still_probes() -> None:
    refused = cams(2)[1]
    row = si.apply_probe(refused, ProbeResult(False, error=REFUSED, duration_ms=900), "t")
    assert refused.live is None and row["live"] is None and "live" not in source_camera_row(refused)
    assert refused.extra["metadata"]["probe"]["kind"] == "auth" and SECRET not in json.dumps(row)
    gone = SourceCamera(external_id="CAM03", name="03", rtsp_url="rtsp://x")
    si.apply_probe(gone, ProbeResult(False, error="rtsp://h/stream/CAM03: Server returned 404 Not Found"), "t")
    assert gone.live is False and gone.extra["metadata"]["probe"]["kind"] == "definitive"
    unknown_path = SourceCamera(external_id="cam31", name="31", rtsp_url="rtsp://x")
    si.apply_probe(unknown_path, ProbeResult(False, error="path not configured"), "t")
    assert unknown_path.live is False


# ---- pacing ------------------------------------------------------------------------------------------------------


def test_import_probes_default_to_the_shared_dial_gate() -> None:
    import inspect

    for fn in (si.probe_paced, si.probe_catalogue):
        assert inspect.signature(fn).parameters["gate"].default is mc.dial_gate
    assert mc.dial_gate.spacing_s == 3.0


def test_probe_paced_spaces_dials_and_bounds_parallelism() -> None:
    async def run() -> tuple[si.PacedProbes, list[tuple[str, float]], int]:
        loop = asyncio.get_running_loop()
        starts: list[tuple[str, float]] = []
        state = {"now": 0, "max": 0}

        async def probe(cam: SourceCamera) -> ProbeResult:
            starts.append((cam.external_id, loop.time()))
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
            await asyncio.sleep(0.15)  # slower than the spacing, so parallel dials overlap
            state["now"] -= 1
            return ProbeResult(True, codec="H264", width=1920, height=1080)

        out = await si.probe_paced(cams(8), probe, parallel=2, gate=mc.DialGate(0.04))
        return out, starts, state["max"]

    out, starts, max_in_flight = asyncio.run(run())
    assert sorted(out.results) == [f"cam{i:02d}" for i in range(1, 9)] and out.skipped == []
    assert [cid for cid, _t in starts] == [f"cam{i:02d}" for i in range(1, 9)]  # catalogue order
    gaps = [b[1] - a[1] for a, b in zip(starts, starts[1:])]
    assert min(gaps) >= 0.035, gaps  # never two dials in the same instant
    assert max_in_flight == 2  # bounded, and the bound is actually used
    assert len(out.dial_offsets_s) == 8


def test_probe_paced_gate_is_passed_once_per_dial_before_the_dial() -> None:
    events: list[str] = []

    class Gate:
        async def wait(self) -> None:
            events.append("gate")

    async def probe(cam: SourceCamera) -> ProbeResult:
        events.append(cam.external_id)
        return ProbeResult(True, codec="H264")

    asyncio.run(si.probe_paced(cams(3), probe, parallel=1, gate=Gate()))
    assert events == ["gate", "cam01", "gate", "cam02", "gate", "cam03"]


# ---- stop dialling once the sandbox keeps refusing the credentials ----------------------------------------------


def _scripted(answers: dict[str, ProbeResult], delay: float = 0.0):
    dialled: list[str] = []

    async def probe(cam: SourceCamera) -> ProbeResult:
        dialled.append(cam.external_id)
        if delay:
            await asyncio.sleep(delay)
        return answers.get(cam.external_id, ProbeResult(False, error=REFUSED))

    return probe, dialled


def test_probe_paced_stops_after_three_consecutive_refusals() -> None:
    probe, dialled = _scripted({})
    out = asyncio.run(si.probe_paced(cams(30), probe, parallel=1, gate=mc.DialGate(0)))
    assert dialled == ["cam01", "cam02", "cam03"]
    assert out.skipped == [f"cam{i:02d}" for i in range(4, 31)] and len(out.results) == 3


def test_probe_paced_refusal_streak_resets_on_any_other_answer() -> None:
    ok = ProbeResult(True, codec="H264")
    slow = ProbeResult(False, error="timeout")
    probe, dialled = _scripted({"cam03": ok, "cam06": slow})  # 401 401 ok 401 401 timeout 401 401
    out = asyncio.run(si.probe_paced(cams(8), probe, parallel=1, gate=mc.DialGate(0)))
    assert len(dialled) == 8 and out.skipped == []
    probe, dialled = _scripted({"cam01": ok})  # ok, then refused from cam02 on
    out = asyncio.run(si.probe_paced(cams(6), probe, parallel=1, gate=mc.DialGate(0)))
    assert dialled == ["cam01", "cam02", "cam03", "cam04"] and out.skipped == ["cam05", "cam06"]


def test_probe_paced_lets_in_flight_probes_finish_but_dials_nothing_new() -> None:
    async def run() -> tuple[si.PacedProbes, list[float], list[float]]:
        loop = asyncio.get_running_loop()
        dial_at: list[float] = []
        refused_at: list[float] = []

        async def probe(_cam: SourceCamera) -> ProbeResult:
            dial_at.append(loop.time())
            await asyncio.sleep(0.05)
            refused_at.append(loop.time())
            return ProbeResult(False, error=REFUSED)

        out = await si.probe_paced(cams(12), probe, parallel=3, gate=mc.DialGate(0))
        return out, dial_at, refused_at

    out, dial_at, refused_at = asyncio.run(run())
    third_refusal = sorted(refused_at)[2]
    assert all(t <= third_refusal for t in dial_at)  # no dial after the streak reached 3
    assert len(out.results) == len(dial_at) <= 5  # every dial that was in flight finished and was recorded
    assert len(out.skipped) == 12 - len(dial_at)


# ---- report rows and summary warning -----------------------------------------------------------------------------


def test_probe_catalogue_rows_cover_every_camera_in_order() -> None:
    probe, _ = _scripted({"cam01": ProbeResult(True, codec="H265", width=1920, height=1080, fps=25.0)})
    cs = cams(6)
    res = asyncio.run(si.probe_catalogue(cs, probe, parallel=1, gate=mc.DialGate(0)))
    assert [r["external_id"] for r in res.rows] == [c.external_id for c in cs]
    assert res.probed == 4 and res.rows[0]["ok"] is True and res.rows[0]["transcode"] is True
    assert [r["ok"] for r in res.rows[1:4]] == [False, False, False] and all(r["live"] is None for r in res.rows[1:])
    assert [r["ok"] for r in res.rows[4:]] == [None, None] and res.rows[4]["error"].startswith("not probed: the sandbox refused")
    assert "metadata" not in cs[5].extra and cs[5].live is None  # a skipped camera keeps its stored state
    assert len(res.warnings) == 1 and res.warnings[0]["field"] == "probe" and res.warnings[0]["message"].startswith("2 cameras not probed")
    assert SECRET not in json.dumps(res.rows) + json.dumps(res.warnings)


def test_probe_catalogue_without_refusals_has_no_warning() -> None:
    probe, dialled = _scripted({c.external_id: ProbeResult(True, codec="H264") for c in cams(4)})
    res = asyncio.run(si.probe_catalogue(cams(4), probe, parallel=6, gate=mc.DialGate(0)))
    assert len(dialled) == 4 and res.probed == 4 and res.warnings == []


def test_run_sentinel_import_probes_through_the_paced_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The import uses `probe_catalogue` with the shared gate (one gate pass per dial), never the un-gated
    `probe_all`, and puts the stop warning into the summary."""
    gate_passes: list[int] = []
    probe, dialled = _scripted({"cam01": ProbeResult(True, codec="H264", width=1920, height=1080)})

    class FakeAdapter:
        stream = SimpleNamespace(configured=True)
        probe_parallel = 6
        info = SimpleNamespace(mode="file", source="/app/media/cameras.json", warnings=[])
        enrichment = None
        enrichment_origin = None
        missing_enrichment: list[str] = []
        warnings: list[dict] = []

        def __init__(self, **_kw: object) -> None:
            self.cams = cams(10)

        async def list_cameras(self) -> list[SourceCamera]:
            return self.cams

        async def probe(self, cam: SourceCamera) -> ProbeResult:
            return await probe(cam)

        async def probe_all(self, _cams: list[SourceCamera]) -> dict:
            raise AssertionError("un-gated probe_all must not be used by the import")

    async def fake_wait() -> None:
        gate_passes.append(1)

    async def fake_upsert(_db: object, rows: list, **_kw: object) -> ImportOutcome:
        return ImportOutcome(unchanged=len(rows))

    monkeypatch.setattr(si, "SentinelPortalAdapter", FakeAdapter)
    monkeypatch.setattr(si, "upsert_cameras", fake_upsert)
    monkeypatch.setattr(mc.dial_gate, "wait", fake_wait)
    summary = asyncio.run(si.run_sentinel_import(None, dry_run=True, measure_first=False))  # type: ignore[arg-type]
    assert dialled == ["cam01", "cam02", "cam03", "cam04"] and len(gate_passes) == 4
    assert summary["probed"] == 4 and summary["probe_ok"] == 1 and len(summary["probes"]) == 10
    assert any(w["field"] == "probe" for w in summary["warnings"])
