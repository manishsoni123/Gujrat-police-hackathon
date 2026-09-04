"""Plain RTSP adapter: one camera per URL; probe with ffprobe over TCP."""

from __future__ import annotations

import asyncio
import json

from app.adapters.base import CameraSourceAdapter, ProbeResult, SourceCamera
from app.core.config import settings


async def ffprobe_details(rtsp_url: str, timeout_s: float | None = None) -> ProbeResult:
    timeout_s = timeout_s or settings.HEALTH_PROBE_TIMEOUT_S
    cmd = [
        "ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-timeout", str(int(timeout_s * 1_000_000)),
        "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height", "-of", "json", rtsp_url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s + 4)
        except asyncio.TimeoutError:
            proc.kill()
            return ProbeResult(False, error="timeout")
        if proc.returncode != 0:
            return ProbeResult(False, error=(err or b"").decode("utf-8", "ignore").strip()[:200] or f"ffprobe exit {proc.returncode}")
        data = json.loads(out.decode("utf-8", "ignore") or "{}")
        streams = data.get("streams") or []
        if not streams:
            return ProbeResult(False, error="no video stream")
        s = streams[0]
        codec = {"h264": "H264", "hevc": "H265", "mjpeg": "MJPEG"}.get(str(s.get("codec_name", "")).lower(), "UNKNOWN")
        return ProbeResult(True, codec=codec, width=s.get("width"), height=s.get("height"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        return ProbeResult(False, error=str(exc))


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
