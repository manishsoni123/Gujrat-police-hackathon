"""Stream-format detection for manually added / edited cameras (CONTRACT Amendments 2026-10-12).

The sandbox import already probes every camera (`sentinel_import.apply_probe`); a camera entered through the
form or `POST /cameras` used to trust the typed `codec`, so an H.265 or B-frame H.264 source entered as `H264`
got no `_h264` re-encode and played black. `detect_stream_format` probes the source once on save and stores
codec, resolution, fps and `metadata.probe` (with `b_frames`, which `needs_transcode` reads).
"""

from __future__ import annotations

import logging
from typing import Any

from app.adapters.rtsp import classify_probe_error, ffprobe_details
from app.core.stream_source import probe_directly, source_kind
from app.core.tz import iso_z, utcnow
from app.services.mediamtx_client import dial_gate, needs_transcode, relay_rtsp_url

log = logging.getLogger(__name__)

# Cap for the save-time probe: ffprobe returns as soon as it sees the stream, so this only bounds a dead or
# unreachable source. A WHEP source is read through its on-demand relay path, which first has to dial upstream.
DIRECT_PROBE_TIMEOUT_S = 15.0
RELAY_PROBE_TIMEOUT_S = 25.0


def probe_target(camera: Any) -> tuple[str, float]:
    """(url, timeout_s) for the save-time probe (pure)."""
    if probe_directly(camera.rtsp_url):
        return camera.rtsp_url, DIRECT_PROBE_TIMEOUT_S
    return relay_rtsp_url(camera.id), RELAY_PROBE_TIMEOUT_S


async def detect_stream_format(camera: Any) -> dict[str, Any] | None:
    """Probe `camera.rtsp_url` and fold the result into the (unsaved) camera: on success `codec` (unless
    ffprobe reports an unknown codec), `resolution`, `fps` and `metadata.probe`; on failure only
    `metadata.probe`, so the typed values stay. Returns the report for the response and audit row, or None when
    the camera has no source URL. The caller commits and re-applies the relay paths."""
    if not camera.rtsp_url:
        return None
    url, timeout_s = probe_target(camera)
    await dial_gate.wait()  # same pacing as every other upstream dial
    res = await ffprobe_details(url, timeout_s=timeout_s)
    previous_codec = camera.codec
    if res.ok:
        if res.codec and res.codec != "UNKNOWN":
            camera.codec = res.codec
        if res.resolution:
            camera.resolution = res.resolution
        if res.fps and 1 <= res.fps <= 60:
            camera.fps = int(round(res.fps))
    kind = "ok" if res.ok else classify_probe_error(res.error)
    camera.meta = {**(camera.meta or {}), "probe": {
        "at": iso_z(utcnow()), "ok": res.ok, "codec": res.codec, "resolution": res.resolution, "fps": res.fps,
        "profile": res.profile, "b_frames": res.b_frames, "duration_ms": res.duration_ms, "error": res.error,
        "kind": kind, "via": "source" if url == camera.rtsp_url else "relay",
    }}
    report = {
        "ok": res.ok, "source_kind": source_kind(camera.rtsp_url), "codec": res.codec, "resolution": res.resolution,
        "fps": res.fps, "b_frames": res.b_frames, "transcode": needs_transcode(camera.codec, camera.meta),
        "codec_corrected_from": previous_codec if res.ok and camera.codec != previous_codec else None,
        "error": res.error, "duration_ms": res.duration_ms,
    }
    log.info("stream probe camera %s: ok=%s codec=%s transcode=%s (%s ms)", camera.id, res.ok, res.codec, report["transcode"], res.duration_ms)
    return report
