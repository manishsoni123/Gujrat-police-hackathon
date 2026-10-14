"""Every organiser probe passes the shared DialGate (CLAUDE.md, organiser etiquette).

The Settings → Catalogue → Test probe (`SentinelPortalAdapter.probe_one`) waits on `dial_gate`
right before its ffprobe dial, so repeated Test clicks, or a Test during an import or a relay
switch, cannot open an upstream connection inside the 3 s spacing; the import dials the real
adapter's raw `probe` through `sentinel_import.probe_paced`, one gate pass right before each dial
(see also test_import_probes.py); and the adapter keeps no un-gated batch helper. DB-free;
ffprobe and the gate are faked, nothing is dialled.
"""

from __future__ import annotations

import asyncio
import json
from itertools import pairwise
from types import SimpleNamespace

import pytest

from app.adapters import rtsp
from app.adapters.base import SourceCamera
from app.adapters.sentinel_portal import PortalConfig, SentinelPortalAdapter
from app.api import settings as settings_api
from app.schemas.admin import CatalogueTestRequest
from app.services import mediamtx_client as mc
from app.services import sentinel_import as si
from app.services.sentinel_catalogue import StreamConfig, build_rtsp_url

SECRET = "s3cret-pass"
HOST = "198.51.100.7"  # TEST-NET-2: never the organiser, even if a fake were missing
STREAM = StreamConfig(
    host=HOST, rtsp_port=8554, whep_port=8889, email="team@example.in", password=SECRET,
)
NO_PORTAL = PortalConfig(url="", email="", password="")
FFPROBE_OK = {"streams": [{
    "codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "25/1",
    "r_frame_rate": "25/1", "profile": "High", "has_b_frames": 0,
}]}


class FakeProc:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return json.dumps(FFPROBE_OK).encode(), b""


def _stream_id(argv: tuple[str, ...]) -> str:
    return argv[-1].rsplit("/", 1)[-1]  # the URL is ffprobe's last argument


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Order of gate passes and ffprobe dials: "gate", "dial <id>"."""
    seen: list[str] = []

    async def fake_wait() -> None:
        seen.append("gate")

    async def fake_exec(*argv: str, **_kw: object) -> FakeProc:
        seen.append(f"dial {_stream_id(argv)}")
        return FakeProc()

    monkeypatch.setattr(mc.dial_gate, "wait", fake_wait)
    monkeypatch.setattr(rtsp.asyncio, "create_subprocess_exec", fake_exec)
    return seen


def _adapter(stream: StreamConfig = STREAM) -> SentinelPortalAdapter:
    return SentinelPortalAdapter(
        stream=stream, portal=NO_PORTAL, probe_timeout_s=10, probe_parallel=1,
    )


def test_probe_one_waits_on_the_shared_gate_before_it_dials(events: list[str]) -> None:
    res = asyncio.run(_adapter().probe_one("cam01"))
    assert res.ok and res.codec == "H264" and res.resolution == "1920x1080"
    assert events == ["gate", "dial cam01"]


def test_probe_one_without_credentials_neither_takes_a_gate_slot_nor_dials(
    events: list[str],
) -> None:
    no_password = StreamConfig(
        host=HOST, rtsp_port=8554, whep_port=8889, email="team@example.in", password="",
    )
    res = asyncio.run(_adapter(no_password).probe_one("cam01"))
    assert not res.ok and "must all be set" in (res.error or "")
    assert events == []


def test_catalogue_test_endpoint_probe_waits_on_the_shared_gate(
    events: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /settings/catalogue/test with `source: sentinel_portal` (the handler body)."""
    audits: list[dict] = []

    def fake_audit(_request: object, **kw: object) -> None:
        audits.append(dict(kw))

    monkeypatch.setattr(settings_api, "set_audit", fake_audit)
    body = CatalogueTestRequest(
        source="sentinel_portal", camera_id="cam07", stream_host=HOST,
        stream_email="team@example.in", stream_password=SECRET, portal_password="",
        check_portal=False,
    )
    request = SimpleNamespace(state=SimpleNamespace())
    result = asyncio.run(settings_api._test_sentinel(request, body))  # type: ignore[arg-type]
    assert events == ["gate", "dial cam07"]
    assert result["ok"] is True
    assert result["probe"]["summary"] == "cam07: H.264 1920x1080 @ 25 fps"
    assert len(audits) == 1 and SECRET not in json.dumps([result, audits], default=str)


def test_repeated_test_probes_are_spaced_by_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three Test clicks at once: the dials leave the process one gate spacing apart."""
    spacing = 0.05
    dialled_at: list[float] = []

    async def fake_exec(*_argv: str, **_kw: object) -> FakeProc:
        dialled_at.append(asyncio.get_running_loop().time())
        return FakeProc()

    async def main() -> list[bool]:
        gate = mc.DialGate(spacing)  # created inside the loop that uses it
        monkeypatch.setattr(mc.dial_gate, "wait", gate.wait)
        adapter = _adapter()
        res = await asyncio.gather(*(adapter.probe_one(f"cam0{i}") for i in (1, 2, 3)))
        return [r.ok for r in res]

    monkeypatch.setattr(rtsp.asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(main()) == [True, True, True]
    assert len(dialled_at) == 3
    assert all(b - a >= spacing * 0.9 for a, b in pairwise(dialled_at)), dialled_at


def test_import_dials_the_real_adapter_probe_once_per_gate_pass(events: list[str]) -> None:
    """The import (`probe_catalogue` with its default, shared gate) drives the real adapter's
    `probe`: exactly one gate pass right before every dial, never none and never two (the raw
    `probe` must not pass the gate a second time, which would halve the import's dial rate)."""
    ids = ("cam01", "cam02", "cam03")
    cams = [SourceCamera(external_id=c, name=c, rtsp_url=build_rtsp_url(STREAM, c)) for c in ids]
    res = asyncio.run(si.probe_catalogue(cams, _adapter().probe, parallel=1))
    assert res.probed == 3 and [r["ok"] for r in res.rows] == [True, True, True]
    assert events == ["gate", "dial cam01", "gate", "dial cam02", "gate", "dial cam03"]


def test_adapter_has_no_unpaced_batch_probe() -> None:
    """The old `probe_all` dialled up to six cameras at once without the gate; the only batch path
    is `sentinel_import.probe_paced` (gate before every dial, stop on repeated refusals)."""
    assert not hasattr(SentinelPortalAdapter, "probe_all")
