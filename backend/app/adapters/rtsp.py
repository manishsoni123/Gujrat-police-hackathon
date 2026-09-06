"""Plain RTSP adapter: one camera per URL; probe with ffprobe over TCP."""

from __future__ import annotations

import asyncio
import json
import re
import time
from fractions import Fraction
from typing import Any

from app.adapters.base import CameraSourceAdapter, ProbeResult, SourceCamera
from app.core.config import settings

CODEC_NAMES = {"h264": "H264", "hevc": "H265", "h265": "H265", "mjpeg": "MJPEG"}
# ffprobe/ffmpeg answers that mean "the server answered and said no" (a camera that is not there / not allowed),
# as opposed to a timeout or a network error (unknown, worth retrying later).
_DEFINITIVE_RE = re.compile(r"(Server returned (4\d\d|5\d\d)|401 Unauthorized|403 Forbidden|404 Not Found|400 Bad Request|path not configured|Unauthorized)", re.I)
_TIMEOUT_RE = re.compile(r"(timed? ?out|Connection timed out|Operation timed out|I/O error)", re.I)


def sane_fps(value: str | float | int | None) -> float | None:
    """`avg_frame_rate`/`r_frame_rate` (`25/1`, `30000/1001`, `90000/1`) → float only when plausible (1–60).

    The organiser sandbox reports bogus rates (cam06 says 90000/1), and timing must come from PTS anyway
    (organiser rule), so an implausible value is dropped rather than stored.
    """
    if value is None:
        return None
    try:
        f = float(Fraction(str(value))) if "/" in str(value) else float(value)
    except (ValueError, ZeroDivisionError, TypeError):
        return None
    if not 1.0 <= f <= 60.0:
        return None
    return round(f, 2)


def classify_probe_error(error: str | None) -> str:
    """`definitive` (server refused: 4xx/5xx, unknown path), `timeout`, or `network` (unreachable/unknown)."""
    if not error:
        return "network"
    if _DEFINITIVE_RE.search(error):
        return "definitive"
    if _TIMEOUT_RE.search(error) or error.strip().lower() == "timeout":
        return "timeout"
    return "network"


def _scrub(text: str, secrets: tuple[str, ...]) -> str:
    from app.services.serializers import mask_secrets_in_text

    return mask_secrets_in_text(text, secrets) or ""


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def ffprobe_details(rtsp_url: str, timeout_s: float | None = None, secrets: tuple[str, ...] = ()) -> ProbeResult:
    """`ffprobe -rtsp_transport tcp` → codec, resolution, sane fps, duration. Every error string is scrubbed of
    URL passwords and of `secrets` before it leaves this function (ffmpeg echoes the input URL in messages)."""
    timeout_s = timeout_s or settings.HEALTH_PROBE_TIMEOUT_S
    started = time.perf_counter()
    cmd = [
        "ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-timeout", str(int(timeout_s * 1_000_000)),
        "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height,avg_frame_rate,r_frame_rate,profile,has_b_frames",
        "-of", "json", rtsp_url,
    ]

    def _done(res: ProbeResult) -> ProbeResult:
        res.duration_ms = int((time.perf_counter() - started) * 1000)
        if res.error:
            res.error = _scrub(res.error, secrets)[:300]
        return res

    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s + 4)
        except asyncio.TimeoutError:
            proc.kill()
            return _done(ProbeResult(False, error="timeout"))
        if proc.returncode != 0:
            msg = (err or b"").decode("utf-8", "ignore").strip().splitlines()
            return _done(ProbeResult(False, error=(msg[-1] if msg else "") or f"ffprobe exit {proc.returncode}"))
        data = json.loads(out.decode("utf-8", "ignore") or "{}")
        streams = data.get("streams") or []
        if not streams:
            return _done(ProbeResult(False, error="no video stream"))
        s = streams[0]
        codec = CODEC_NAMES.get(str(s.get("codec_name", "")).lower(), "UNKNOWN")
        fps = sane_fps(s.get("avg_frame_rate")) or sane_fps(s.get("r_frame_rate"))
        return _done(ProbeResult(True, codec=codec, width=s.get("width"), height=s.get("height"), fps=fps, profile=s.get("profile"), b_frames=_int_or_none(s.get("has_b_frames"))))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        return _done(ProbeResult(False, error=str(exc)))


class RtspAdapter(CameraSourceAdapter):
    name = "rtsp"

    def __init__(self, cameras: list[SourceCamera] | None = None) -> None:
        self._cameras = cameras or []

    async def list_cameras(self) -> list[SourceCamera]:
        return list(self._cameras)

    def stream_url(self, camera: SourceCamera) -> str | None:
        return camera.rtsp_url

    async def probe(self, camera: SourceCamera) -> ProbeResult:
        if not camera.rtsp_url:
            return ProbeResult(False, error="no rtsp_url")
        return await ffprobe_details(camera.rtsp_url)
