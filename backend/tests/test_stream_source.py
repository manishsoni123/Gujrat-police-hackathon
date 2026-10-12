"""Camera source URLs beyond RTSP (CONTRACT Amendments 2026-10-12): accepted schemes, WHEP rewrite, protocol-specific
ffprobe options, relay path body, health/save-time probe targets and save-time format detection. DB-free."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.adapters.base import ProbeResult
from app.core.config import settings as env
from app.core.stream_source import ffprobe_input_args, normalise_source_url, probe_directly, source_kind
from app.schemas.cameras import CameraImportRow
from app.services import mediamtx_client as mc
from app.services import source_probe
from app.services.health_poller import probe_target


@pytest.mark.parametrize("url,kind", [
    ("rtsp://u:p@192.168.173.45:554/unicaststream/2", "rtsp"),
    ("RTSPS://cam.example.in/live", "rtsp"),
    ("rtmp://media.example.in/live/key", "rtmp"),
    ("rtmps://media.example.in/live/key", "rtmp"),
    ("srt://10.0.0.5:8890?streamid=read:cam1", "srt"),
    ("https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8", "hls"),
    ("http://nvr.local/hls/ch1/index.m3u8", "hls"),
    ("whep://10.0.0.7:8889/cam/whep", "whep"),
    ("wheps://relay.example.in/cam/whep", "whep"),
])
def test_accepted_schemes(url: str, kind: str) -> None:
    assert source_kind(url) == kind
    assert CameraImportRow(external_id="X", name="X", rtsp_url=url).rtsp_url == url


@pytest.mark.parametrize("url", ["ftp://host/file", "udp://239.0.0.1:1234", "192.168.1.10:554/stream", "rtsp://", "youtube.com/watch?v=1"])
def test_rejected_urls(url: str) -> None:
    with pytest.raises(ValidationError) as exc:
        CameraImportRow(external_id="X", name="X", rtsp_url=url)
    assert "must start with rtsp://" in str(exc.value)


def test_http_whep_endpoint_is_rewritten_to_whep_scheme() -> None:
    assert normalise_source_url("https://u:p@relay.example.in/cam1/whep") == "wheps://u:p@relay.example.in/cam1/whep"
    assert normalise_source_url("http://10.0.0.7:8889/cam1/whep/") == "whep://10.0.0.7:8889/cam1/whep/"
    assert normalise_source_url("  https://cdn.example.in/live/index.m3u8 ") == "https://cdn.example.in/live/index.m3u8"


def test_ffprobe_options_per_protocol() -> None:
    assert ffprobe_input_args("rtsp://h/s", 6) == ["-rtsp_transport", "tcp", "-timeout", "6000000"]
    # -timeout is a *listen* timeout for RTMP, so non-RTSP inputs use the generic read/write timeout
    for url in ("rtmp://h/live/k", "srt://h:8890", "https://h/i.m3u8"):
        assert ffprobe_input_args(url, 2.5) == ["-rw_timeout", "2500000"]
    assert probe_directly("srt://h:8890") and not probe_directly("whep://h/c/whep") and not probe_directly(None)


def test_relay_source_body_sets_rtsp_transport_only_for_rtsp() -> None:
    assert mc.source_path_body("rtsp://u:p@10.0.0.5:554/s", record=False)["rtspTransport"] == "tcp"
    for url in ("rtmp://h/live/k", "https://h/i.m3u8", "whep://h/c/whep", "srt://h:8890"):
        body = mc.source_path_body(url, record=False)
        assert body["source"] == url and "rtspTransport" not in body


def test_health_probe_target_sends_whep_through_the_relay() -> None:
    url, t = probe_target("whep://10.0.0.7:8889/cam/whep", 31, local_timeout_s=6, external_timeout_s=30, source="manual")
    assert url == mc.relay_rtsp_url(31) and t == 45
    url, t = probe_target("https://cdn.example.in/live/index.m3u8", 31, local_timeout_s=6, external_timeout_s=30, source="manual")
    assert url == "https://cdn.example.in/live/index.m3u8" and t == 30


def cam(**kw):
    base = {"id": 31, "rtsp_url": "rtsp://u:p@192.168.173.45:554/unicaststream/1", "codec": "H264", "resolution": None, "fps": None, "meta": {"district": "Ahmedabad"}}
    base.update(kw)
    return SimpleNamespace(**base)


def run_detect(monkeypatch, camera, result: ProbeResult) -> tuple[dict, list]:
    calls: list = []

    async def fake_probe(url, timeout_s=None, secrets=()):
        calls.append((url, timeout_s))
        return result

    async def no_wait() -> None:
        return None

    monkeypatch.setattr(source_probe, "ffprobe_details", fake_probe)
    monkeypatch.setattr(source_probe.dial_gate, "wait", no_wait)
    return asyncio.run(source_probe.detect_stream_format(camera)), calls


def test_detect_corrects_a_mistyped_codec_and_requests_transcode(monkeypatch) -> None:
    c = cam()
    report, calls = run_detect(monkeypatch, c, ProbeResult(True, codec="H265", width=1920, height=1080, fps=25.0, b_frames=0))
    assert calls == [(c.rtsp_url, source_probe.DIRECT_PROBE_TIMEOUT_S)]
    assert (c.codec, c.resolution, c.fps) == ("H265", "1920x1080", 25)
    assert report["codec_corrected_from"] == "H264" and report["transcode"] is True and report["source_kind"] == "rtsp"
    assert c.meta["district"] == "Ahmedabad" and c.meta["probe"]["via"] == "source"


def test_detect_flags_b_frame_h264_for_transcode(monkeypatch) -> None:
    c = cam(rtsp_url="https://cdn.example.in/live/index.m3u8")
    report, _ = run_detect(monkeypatch, c, ProbeResult(True, codec="H264", width=1280, height=720, fps=30.0, b_frames=2))
    assert c.codec == "H264" and report["codec_corrected_from"] is None
    assert report["transcode"] is True and mc.needs_transcode(c.codec, c.meta)


def test_detect_failure_keeps_typed_values(monkeypatch) -> None:
    c = cam(codec="H265", resolution="2560x1440")
    report, _ = run_detect(monkeypatch, c, ProbeResult(False, error="Server returned 404 Not Found"))
    assert (c.codec, c.resolution) == ("H265", "2560x1440")
    assert report["ok"] is False and c.meta["probe"]["kind"] == "definitive"


def test_detect_probes_whep_sources_through_the_relay(monkeypatch) -> None:
    c = cam(rtsp_url="whep://10.0.0.7:8889/cam/whep")
    _, calls = run_detect(monkeypatch, c, ProbeResult(True, codec="H264", width=640, height=360, fps=15.0, b_frames=0))
    assert calls == [(f"{env.MEDIAMTX_RTSP_URL}/cam_31", source_probe.RELAY_PROBE_TIMEOUT_S)]
    assert c.meta["probe"]["via"] == "relay"


def test_detect_skips_cameras_without_a_source(monkeypatch) -> None:
    report, calls = run_detect(monkeypatch, cam(rtsp_url=None), ProbeResult(True))
    assert report is None and calls == []
