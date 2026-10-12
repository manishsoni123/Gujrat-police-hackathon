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
from app.db.models import Camera
from app.services import mediamtx_client as mc
from app.services import sentinel_import as si
from app.services.camera_importer import ImportOutcome, _pending_changes, validate_row
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
    assert refused.live is None and row["live"] is None
    assert refused.extra["metadata"]["probe"]["kind"] == "auth" and SECRET not in json.dumps(row)
    import_row = si.import_row(refused)
    assert "live" in import_row and import_row["live"] is None  # explicit null, not a missing key
    gone = SourceCamera(external_id="CAM03", name="03", rtsp_url="rtsp://x")
    si.apply_probe(gone, ProbeResult(False, error="rtsp://h/stream/CAM03: Server returned 404 Not Found"), "t")
    assert gone.live is False and gone.extra["metadata"]["probe"]["kind"] == "definitive"
    unknown_path = SourceCamera(external_id="cam31", name="31", rtsp_url="rtsp://x")
    si.apply_probe(unknown_path, ProbeResult(False, error="path not configured"), "t")
    assert unknown_path.live is False


def _stored(cam: SourceCamera, live: bool | None, probe: dict | None = None) -> Camera:
    meta: dict = {"catalogue": {"source": "sentinel_portal", "mode": "file"}}
    if probe is not None:
        meta["probe"] = probe
    return Camera(id=61, source="sandbox", external_id=cam.external_id, name=cam.name, department_id=1,
                  rtsp_url=cam.rtsp_url, codec="UNKNOWN", live=live, status="offline", meta=meta)


def test_auth_refused_import_row_clears_a_stored_live_false_on_update() -> None:
    """The 12 Oct rows (ids 61-90) hold live=false from a 401: re-imported and refused again, the
    importer's update must turn that into null. A missing key would leave the false in place."""
    cam = cams(2)[1]
    si.apply_probe(cam, ProbeResult(False, error=REFUSED), "t")
    row = si.import_row(cam)
    parsed, issues = validate_row(row, 2, 1)
    assert issues == [] and parsed is not None and parsed.live is None
    stored = _stored(cam, False, {"ok": False, "kind": "definitive", "error": REFUSED})
    changes = _pending_changes(stored, parsed, set(row))
    assert "live" in changes and changes["live"] is None
    assert changes["meta"]["probe"]["kind"] == "auth"
    # a stored true becomes unknown too: a refusal cannot confirm the camera
    assert _pending_changes(_stored(cam, True), parsed, set(row))["live"] is None


def test_other_failures_keep_the_stored_live_value() -> None:
    """Timeouts and network errors are unknown and carry no `live` key (stored value kept); a 404 is
    a definitive false. Neither is affected by the explicit null of a credential refusal."""
    timed_out = cams(3)[2]
    si.apply_probe(timed_out, ProbeResult(False, error="timeout"), "t")
    row = si.import_row(timed_out)
    assert "live" not in row and row == source_camera_row(timed_out)
    parsed, _ = validate_row(row, 3, 2)
    assert parsed is not None
    assert "live" not in _pending_changes(_stored(timed_out, False), parsed, set(row))
    gone = cams(4)[3]
    si.apply_probe(gone, ProbeResult(False, error="rtsp://h/stream/cam04: Server returned 404 Not Found"), "t")
    assert si.import_row(gone)["live"] is False


def test_held_by_auth_refusal_matches_the_rows_left_by_the_12_oct_import() -> None:
    old = {"probe": {"ok": False, "kind": "definitive", "error": REFUSED}}  # what 12 Oct stored
    assert si.held_by_auth_refusal(False, old)
    assert si.held_by_auth_refusal(False, {"probe": {"ok": False, "kind": "auth", "error": "x"}})
    assert si.held_by_auth_refusal(False, {"probe": {"ok": False, "error": "Server returned 403 Forbidden"}})
    assert not si.held_by_auth_refusal(None, old) and not si.held_by_auth_refusal(True, old)
    not_found = {"probe": {"ok": False, "kind": "definitive", "error": "Server returned 404 Not Found"}}
    others = (not_found, {"probe": {"ok": False, "error": "path not configured"}}, {"probe": None}, {}, None,
              {"probe": {"ok": True, "kind": "ok", "error": None}})
    for meta in others:
        assert not si.held_by_auth_refusal(False, meta), meta


class _Result:
    def __init__(self, rows: list[Camera]) -> None:
        self.rows = rows

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Camera]:
        return self.rows


class FakeSession:
    """Answers every `execute` with the given rows (the WHERE clause is SQLAlchemy's job)."""

    def __init__(self, rows: list[Camera]) -> None:
        self.rows = rows
        self.commits = 0
        self.statements: list[str] = []

    async def execute(self, stmt: object) -> _Result:
        self.statements.append(str(stmt))
        return _Result(self.rows)

    async def commit(self) -> None:
        self.commits += 1


def test_release_auth_refusals_sets_only_refusal_held_rows_to_unknown() -> None:
    c = cams(3)
    held = _stored(c[0], False, {"ok": False, "kind": "definitive", "error": REFUSED})
    gone = _stored(c[1], False, {"ok": False, "kind": "definitive", "error": "Server returned 404 Not Found"})
    catalogue_false = _stored(c[2], False)  # live=false from the catalogue, never probed
    db = FakeSession([held, gone, catalogue_false])
    released = asyncio.run(si.release_auth_refusals(db))  # type: ignore[arg-type]
    assert released == ["cam01"] and db.commits == 1
    assert held.live is None and held.updated_at is not None
    assert gone.live is False and catalogue_false.live is False
    sql = db.statements[0]
    assert "cameras.source" in sql and "cameras.live IS false" in sql and "cameras.status !=" in sql
    nothing = FakeSession([gone])
    assert asyncio.run(si.release_auth_refusals(nothing)) == []  # type: ignore[arg-type]
    assert nothing.commits == 0


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


class CountingGate:
    """A real `DialGate` that also records the waits that completed (a cancelled wait claims nothing)."""

    def __init__(self, spacing_s: float) -> None:
        self.inner = mc.DialGate(spacing_s)
        self.spacing_s = spacing_s
        self.passed: list[float] = []

    async def wait(self) -> None:
        await self.inner.wait()
        self.passed.append(asyncio.get_running_loop().time())


def test_probe_paced_stop_stays_latched_when_an_in_flight_probe_succeeds_late() -> None:
    """cam01 answers OK only after the stop: the stop must hold (no further dial, the skipped cameras
    stay skipped), and the OK result is still recorded."""

    async def run() -> tuple[si.PacedProbes, list[tuple[str, float]], float, CountingGate]:
        loop = asyncio.get_running_loop()
        dials: list[tuple[str, float]] = []
        refused_at: list[float] = []

        async def probe(cam: SourceCamera) -> ProbeResult:
            dials.append((cam.external_id, loop.time()))
            if cam.external_id == "cam01":
                await asyncio.sleep(0.4)
                return ProbeResult(True, codec="H264", width=1920, height=1080)
            await asyncio.sleep(0.01)
            refused_at.append(loop.time())
            return ProbeResult(False, error=REFUSED)

        gate = CountingGate(0.05)
        out = await si.probe_paced(cams(10), probe, parallel=6, gate=gate)
        return out, dials, refused_at[2], gate

    out, dials, third_refusal, gate = asyncio.run(run())
    assert [cid for cid, _t in dials] == ["cam01", "cam02", "cam03", "cam04"]
    assert all(t <= third_refusal for _cid, t in dials)
    assert out.results["cam01"].ok is True and len(out.results) == 4
    assert out.skipped == [f"cam{i:02d}" for i in range(5, 11)]
    assert len(gate.passed) == 4  # the gate wait of cam05 was cancelled, not completed


def test_probe_paced_does_not_hold_the_shared_gate_for_cameras_it_will_not_dial() -> None:
    """All refused (answer after 0.02 s, spacing 0.05 s, parallel 6): three dials, then the stop.
    The pending gate wait is cancelled, so the gate's last dial is the third one and the import
    returns right after the third answer instead of ticking the gate once per waiting camera."""

    async def run() -> tuple[si.PacedProbes, float, CountingGate, float]:
        loop = asyncio.get_running_loop()
        t0 = loop.time()

        async def probe(_cam: SourceCamera) -> ProbeResult:
            await asyncio.sleep(0.02)
            return ProbeResult(False, error=REFUSED)

        gate = CountingGate(0.05)
        out = await si.probe_paced(cams(30), probe, parallel=6, gate=gate)
        return out, loop.time() - t0, gate, t0

    out, wall, gate, t0 = asyncio.run(run())
    assert len(out.results) == 3 and len(out.skipped) == 27
    assert len(gate.passed) == 3
    last = gate.inner._last
    assert last is not None and last - t0 < 0.15  # the third dial at ~0.10 s was the gate's last
    assert wall < 0.2, wall  # third refusal at ~0.12 s; no extra 0.05 s per waiting camera


def test_probe_paced_turns_a_probe_exception_into_a_failed_result() -> None:
    async def probe(cam: SourceCamera) -> ProbeResult:
        if cam.external_id == "cam02":
            raise RuntimeError("boom " + SECRET)
        return ProbeResult(True, codec="H264")

    out = asyncio.run(si.probe_paced(cams(3), probe, parallel=2, gate=mc.DialGate(0)))
    assert sorted(out.results) == ["cam01", "cam02", "cam03"] and out.skipped == []
    assert out.results["cam02"].ok is False and SECRET not in (out.results["cam02"].error or "")


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
        await asyncio.sleep(0.01)  # like a gate pause: a stop arriving meanwhile cancels the wait
        gate_passes.append(1)

    upserted: list[dict] = []

    async def fake_upsert(_db: object, rows: list, **_kw: object) -> ImportOutcome:
        upserted.extend(raw for _r, _i, raw in rows)
        return ImportOutcome(unchanged=len(rows))

    monkeypatch.setattr(si, "SentinelPortalAdapter", FakeAdapter)
    monkeypatch.setattr(si, "upsert_cameras", fake_upsert)
    monkeypatch.setattr(mc.dial_gate, "wait", fake_wait)
    summary = asyncio.run(si.run_sentinel_import(None, dry_run=True, measure_first=False))  # type: ignore[arg-type]
    assert dialled == ["cam01", "cam02", "cam03", "cam04"] and len(gate_passes) == 4
    assert summary["probed"] == 4 and summary["probe_ok"] == 1 and len(summary["probes"]) == 10
    assert any(w["field"] == "probe" for w in summary["warnings"])
    assert upserted[0]["live"] is True
    assert all("live" in r and r["live"] is None for r in upserted[1:4])  # refused: clears a stored false
    assert all("live" not in r for r in upserted[4:])  # not dialled: stored value kept


def test_run_sentinel_import_releases_refusal_held_rows_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeAdapter:
        stream = SimpleNamespace(configured=True)
        probe_parallel = 6
        info = SimpleNamespace(mode="file", source="/app/media/cameras.json", warnings=[])
        enrichment = None
        enrichment_origin = None
        missing_enrichment: list[str] = []
        warnings: list[dict] = []

        def __init__(self, **_kw: object) -> None:
            pass

        async def list_cameras(self) -> list[SourceCamera]:
            return cams(2)

    async def fake_upsert(_db: object, rows: list, **_kw: object) -> ImportOutcome:
        calls.append("upsert")
        return ImportOutcome(unchanged=len(rows))

    async def fake_release(_db: object) -> list[str]:
        calls.append("release")
        return ["cam07", "cam08"]

    async def fake_policies(_db: object) -> dict[str, int]:
        calls.append("policies")
        return {"persistent": 0, "applied": 0, "failed": 0}

    monkeypatch.setattr(si, "SentinelPortalAdapter", FakeAdapter)
    monkeypatch.setattr(si, "upsert_cameras", fake_upsert)
    monkeypatch.setattr(si, "release_auth_refusals", fake_release)
    monkeypatch.setattr(si, "ensure_relay_policies", fake_policies)
    db = object()
    summary = asyncio.run(si.run_sentinel_import(db, probe=False, measure_first=False))  # type: ignore[arg-type]
    assert calls == ["upsert", "release", "policies"]
    live = [w for w in summary["warnings"] if w["field"] == "live"]
    assert len(live) == 1 and live[0]["message"].startswith("2 cameras marked live=false by an earlier")
    calls.clear()
    asyncio.run(si.run_sentinel_import(db, probe=False, measure_first=False, dry_run=True))  # type: ignore[arg-type]
    assert calls == ["upsert"]  # a dry run changes nothing
