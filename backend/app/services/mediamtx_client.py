"""MediaMTX control-API client (CONTRACT §8.3, §8.4) plus the ffprobe active probe (§5.6)."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings

log = logging.getLogger("sentinel.mediamtx")

RECORD_PATH = "/recordings/%path/%Y-%m-%d_%H-%M-%S-%f"


@dataclass
class PathResult:
    ok: bool
    status: int | None
    detail: str


def relay_path(camera_id: int) -> str:
    return f"cam_{camera_id}"


def h264_path(camera_id: int) -> str:
    return f"cam_{camera_id}_h264"


def play_path(camera_id: int, codec: str | None) -> str:
    return h264_path(camera_id) if (codec or "").upper() == "H265" else relay_path(camera_id)


def relay_rtsp_url(camera_id: int) -> str:
    return f"{settings.MEDIAMTX_RTSP_URL.rstrip('/')}/{relay_path(camera_id)}"


def _record_block(enabled: bool) -> dict[str, Any]:
    return {
        "record": bool(enabled),
        "recordPath": RECORD_PATH,
        "recordFormat": "fmp4",
        "recordSegmentDuration": "1m",
        "recordDeleteAfter": "12h",
    }


def source_path_body(rtsp_url: str, record: bool) -> dict[str, Any]:
    body = {
        "source": rtsp_url,
        "sourceOnDemand": True,
        "sourceOnDemandStartTimeout": "15s",
        "sourceOnDemandCloseAfter": "60s",
        "rtspTransport": "tcp",
    }
    body.update(_record_block(record))
    return body


def transcode_command(camera_id: int) -> str:
    local = settings.MEDIAMTX_RTSP_LOCAL_URL.rstrip("/")
    src = f"{local}/{relay_path(camera_id)}"
    dst = f"{local}/{h264_path(camera_id)}"
    if settings.MEDIAMTX_TRANSCODE == "nvenc":
        return (
            f"ffmpeg -nostdin -loglevel error -rtsp_transport tcp -hwaccel cuda -i {src} "
            f"-c:v h264_nvenc -preset p1 -b:v 1500k -g 30 -an -f rtsp {dst}"
        )
    return (
        f"ffmpeg -nostdin -loglevel error -rtsp_transport tcp -i {src} "
        f"-c:v libx264 -preset veryfast -tune zerolatency -b:v 1500k -g 30 -an -f rtsp {dst}"
    )


def transcode_path_body(camera_id: int, record: bool) -> dict[str, Any]:
    body = {
        "runOnDemand": transcode_command(camera_id),
        "runOnDemandRestart": True,
        "runOnDemandStartTimeout": "20s",
        "runOnDemandCloseAfter": "60s",
    }
    body.update(_record_block(record))
    return body


class MediaMtxClient:
    def __init__(self, base_url: str | None = None, timeout: float = 5.0) -> None:
        self.base_url = (base_url or settings.MEDIAMTX_API_URL).rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def _req(self, method: str, path: str, body: dict | None = None) -> tuple[int | None, Any]:
        try:
            r = await self._client.request(method, f"{self.base_url}{path}", json=body)
        except httpx.HTTPError as exc:
            return None, str(exc)
        try:
            data = r.json() if r.content else {}
        except json.JSONDecodeError:
            data = r.text
        return r.status_code, data

    async def add_or_patch_path(self, name: str, body: dict[str, Any], recreate: bool = False) -> PathResult:
        if recreate:
            await self._req("DELETE", f"/v3/config/paths/delete/{name}")
        status, data = await self._req("POST", f"/v3/config/paths/add/{name}", body)
        if status == 200:
            return PathResult(True, status, "created")
        if status == 400 and "already exists" in str(data):
            status2, data2 = await self._req("PATCH", f"/v3/config/paths/patch/{name}", body)
            if status2 == 200:
                return PathResult(True, status2, "patched")
            return PathResult(False, status2, f"MediaMTX: {status2} {data2}")
        return PathResult(False, status, f"MediaMTX: {status} {data}")

    async def delete_path(self, name: str) -> PathResult:
        status, data = await self._req("DELETE", f"/v3/config/paths/delete/{name}")
        if status in (200, 404):
            return PathResult(True, status, "deleted" if status == 200 else "absent")
        return PathResult(False, status, f"MediaMTX: {status} {data}")

    async def ensure_camera_paths(self, camera, recreate: bool = False) -> list[PathResult]:
        """Create (or patch) `cam_<id>` and, for H.265, `cam_<id>_h264` (§8.3)."""
        results: list[PathResult] = []
        if not camera.rtsp_url:
            return results
        is_h265 = (camera.codec or "").upper() == "H265"
        record = bool(camera.record_enabled)
        results.append(
            await self.add_or_patch_path(relay_path(camera.id), source_path_body(camera.rtsp_url, record and not is_h265), recreate)
        )
        if is_h265:
            results.append(await self.add_or_patch_path(h264_path(camera.id), transcode_path_body(camera.id, record), recreate))
        return results

    async def remove_camera_paths(self, camera_id: int) -> list[PathResult]:
        return [await self.delete_path(relay_path(camera_id)), await self.delete_path(h264_path(camera_id))]

    async def list_paths(self) -> dict[str, dict[str, Any]] | None:
        """All runtime paths indexed by name, or None when MediaMTX is unreachable."""
        items: dict[str, dict[str, Any]] = {}
        page = 0
        while True:
            status, data = await self._req("GET", f"/v3/paths/list?page={page}&itemsPerPage=1000")
            if status != 200 or not isinstance(data, dict):
                return None
            for it in data.get("items", []):
                items[it.get("name", "")] = it
            page += 1
            if page >= int(data.get("pageCount", 1) or 1):
                break
        return items

    async def list_config_paths(self) -> set[str] | None:
        names: set[str] = set()
        page = 0
        while True:
            status, data = await self._req("GET", f"/v3/config/paths/list?page={page}&itemsPerPage=1000")
            if status != 200 or not isinstance(data, dict):
                return None
            for it in data.get("items", []):
                names.add(it.get("name", ""))
            page += 1
            if page >= int(data.get("pageCount", 1) or 1):
                break
        return names

    async def get_path(self, name: str) -> dict[str, Any] | None:
        status, data = await self._req("GET", f"/v3/paths/get/{name}")
        return data if status == 200 and isinstance(data, dict) else None

    async def ping(self) -> bool:
        status, _ = await self._req("GET", "/v3/paths/list?itemsPerPage=1")
        return status == 200

    async def trigger_hls(self, path: str) -> None:
        """Request the HLS manifest to start an on-demand pull (used by first-stream timing)."""
        url = f"{settings.MEDIAMTX_HLS_URL.rstrip('/')}/{path}/index.m3u8"
        try:
            # MediaMTX >= 1.20 answers the first request with a 302 to ?cookieCheck=1; the muxer (and
            # therefore the on-demand source) only starts when that redirect is followed.
            await self._client.get(url, timeout=5.0, follow_redirects=True)
        except httpx.HTTPError:
            pass

    async def wait_ready(self, path: str, timeout_s: float = 15.0, interval_s: float = 0.25) -> float | None:
        """Poll /v3/paths/get until ready; returns elapsed ms or None on timeout."""
        loop = asyncio.get_event_loop()
        start = loop.time()
        while loop.time() - start < timeout_s:
            item = await self.get_path(path)
            if item and item.get("ready"):
                return (loop.time() - start) * 1000.0
            await asyncio.sleep(interval_s)
        return None


async def ffprobe_ok(rtsp_url: str, timeout_s: float | None = None) -> bool:
    """Active probe: `ffprobe -rtsp_transport tcp` on the relay path; True when a video stream is found."""
    timeout_s = timeout_s or settings.HEALTH_PROBE_TIMEOUT_S
    cmd = [
        "ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-timeout", str(int(timeout_s * 1_000_000)),
        "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "json", rtsp_url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s + 4)
        except asyncio.TimeoutError:
            proc.kill()
            return False
        if proc.returncode != 0:
            return False
        data = json.loads(out.decode("utf-8", "ignore") or "{}")
        return bool(data.get("streams"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False


client = MediaMtxClient()


# ---- playback server (CONTRACT §5.16, §8.5) -----------------------------------------------------


def _rfc3339(dt) -> str:
    from app.core.tz import to_utc

    # Millisecond precision: MediaMTX lists segment starts with fractions, so a whole-second start can precede
    # the segment by up to 999 ms and the playback server then answers "no recording segments found".
    u = to_utc(dt)
    return u.strftime("%Y-%m-%dT%H:%M:%S") + f".{u.microsecond // 1000:03d}Z"


async def playback_list(path: str, start, end) -> list[dict[str, Any]] | None:
    """`GET {MEDIAMTX_PLAYBACK_URL}/list?path=&start=&end=` → [{start, duration, url}] or None when unavailable."""
    url = f"{settings.MEDIAMTX_PLAYBACK_URL.rstrip('/')}/list"
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(url, params={"path": path, "start": _rfc3339(start), "end": _rfc3339(end)})
    except httpx.HTTPError as exc:
        log.warning("playback list failed for %s: %s", path, exc)
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def playback_get_url(path: str, start, duration_s: float) -> str:
    """Internal (server-side) URL of a playback segment."""
    return f"{settings.MEDIAMTX_PLAYBACK_URL.rstrip('/')}/get?path={path}&start={_rfc3339(start)}&duration={int(duration_s)}&format=mp4"


def public_playback_url(path: str, start, duration_s: float) -> str:
    """Browser URL through Caddy (`/playback/*`, forward_auth)."""
    return f"/playback/get?path={path}&start={_rfc3339(start)}&duration={int(duration_s)}&format=mp4"


async def playback_download(path: str, start, duration_s: float, max_bytes: int = 500 * 1024 * 1024):
    """Async generator of MP4 chunks from the playback server; raises RuntimeError on a non-200 status."""
    url = playback_get_url(path, start, duration_s)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as c:
        async with c.stream("GET", url) as r:
            if r.status_code != 200:
                body = (await r.aread())[:200].decode("utf-8", "ignore")
                raise RuntimeError(f"playback {r.status_code}: {body}")
            total = 0
            async for chunk in r.aiter_bytes(1 << 16):
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("clip exceeds the 500 MB limit")
                yield chunk
