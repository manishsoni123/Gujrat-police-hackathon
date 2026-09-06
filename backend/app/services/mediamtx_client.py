"""MediaMTX control-API client (CONTRACT §8.3, §8.4) plus the ffprobe active probe (§5.6).

Relay-source policy (CONTRACT Amendments 2026-09-05, "pace your load"): a camera that is actually processed —
`anpr_enabled` (the live ANPR set) or our own gate (`source='own'`) — gets a **persistent** relay source
(`sourceOnDemand: false`): MediaMTX holds ONE upstream connection per camera and reconnects itself (fixed 5 s
`retryPause` in `internal/staticsources/handler.go` of v1.20.1; there is no path-level retry option), so worker
restarts, wall viewers, probes and frame grabs never re-dial the organiser server. Every other camera stays
on demand with a 10-minute close-after (`ON_DEMAND_CLOSE_AFTER_S`) instead of 60 s, so a wall viewer or a probe
does not cause a re-dial every minute. Operations that make the relay dial upstream (creating / switching a
persistent source) are spaced `RELAY_DIAL_SPACING_S` apart through `dial_gate`, which the health poller shares
for its probes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings

log = logging.getLogger("sentinel.mediamtx")

RECORD_PATH = "/recordings/%path/%Y-%m-%d_%H-%M-%S-%f"

PERSISTENT = "persistent"
ON_DEMAND = "on_demand"
ON_DEMAND_CLOSE_AFTER_S = 600  # idle on-demand relay sources are kept 10 min after the last reader leaves
RELAY_DIAL_SPACING_S = 3.0  # minimum spacing between operations that open a new upstream connection


@dataclass
class PathResult:
    ok: bool
    status: int | None
    detail: str


class DialGate:
    """Serialises "dial-inducing" operations so that upstream sees at most one new connection per
    `spacing_s` from this process (the organiser server refuses bursts). `wait()` returns once the caller
    may dial; the caller's own work (probe, PATCH) runs outside the gate so slow dials do not block others
    beyond the spacing."""

    def __init__(self, spacing_s: float) -> None:
        self.spacing_s = float(spacing_s)
        self._lock = asyncio.Lock()
        self._last: float | None = None

    async def wait(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            if self._last is not None:
                pause = self._last + self.spacing_s - loop.time()
                if pause > 0:
                    await asyncio.sleep(pause)
            self._last = loop.time()


dial_gate = DialGate(RELAY_DIAL_SPACING_S)


def relay_policy(camera: Any) -> str:
    """`PERSISTENT` for a camera that is actually processed (`anpr_enabled`, or our own gate `source='own'`),
    `ON_DEMAND` for everything else (incl. retired cameras and cameras without a source URL). Pure."""
    if not getattr(camera, "rtsp_url", None) or getattr(camera, "status", None) == "retired":
        return ON_DEMAND
    if bool(getattr(camera, "anpr_enabled", False)) or getattr(camera, "source", None) == "own":
        return PERSISTENT
    return ON_DEMAND


def relay_policy_body(policy: str) -> dict[str, Any]:
    """The path-config keys that encode the policy (PATCHed on their own by `ensure_relay_policy`)."""
    return {"sourceOnDemand": policy != PERSISTENT, "sourceOnDemandCloseAfter": f"{ON_DEMAND_CLOSE_AFTER_S}s"}


def config_policy(item: dict[str, Any] | None) -> str | None:
    """Policy a MediaMTX path config currently encodes (None when unknown / not a source path)."""
    if not isinstance(item, dict) or "sourceOnDemand" not in item:
        return None
    return ON_DEMAND if item.get("sourceOnDemand") else PERSISTENT


_GO_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h)")
_GO_UNITS = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0}


def go_duration_s(value: Any) -> float | None:
    """Seconds for a Go duration string (`600s`, `10m0s`, `12h0m0s`, `200ms`); None when it is not one.
    MediaMTX echoes durations in Go's canonical form, so `600s` must compare equal to `10m0s`."""
    if isinstance(value, bool) or not isinstance(value, str):
        return None
    text = value.strip()
    if not text or not re.fullmatch(r"(?:\d+(?:\.\d+)?(?:ns|us|µs|ms|s|m|h))+", text):
        return None
    return sum(float(n) * _GO_UNITS[u] for n, u in _GO_DURATION.findall(text))


def config_matches(current: dict[str, Any] | None, body: dict[str, Any]) -> bool:
    """True when every key of `body` already has the same value in the path config MediaMTX reports
    (durations compared as seconds). Used to skip a PATCH — MediaMTX restarts a path (and its upstream
    connection) whenever a PATCH changes its config."""
    if not isinstance(current, dict):
        return False
    for key, want in body.items():
        if key not in current:
            return False
        have = current[key]
        if isinstance(want, bool) or isinstance(have, bool):
            if bool(want) != bool(have):
                return False
            continue
        ws, hs = go_duration_s(want), go_duration_s(have)
        if ws is not None and hs is not None:
            if abs(ws - hs) > 1e-6:
                return False
            continue
        if want != have:
            return False
    return True


def relay_path(camera_id: int) -> str:
    return f"cam_{camera_id}"


def h264_path(camera_id: int) -> str:
    return f"cam_{camera_id}_h264"


def source_b_frames(meta: dict[str, Any] | None) -> int:
    """`metadata.probe.b_frames` (ffprobe `has_b_frames`) as an int; 0 when unknown or unparsable."""
    probe = (meta or {}).get("probe") if isinstance(meta, dict) else None
    value = probe.get("b_frames") if isinstance(probe, dict) else None
    try:
        return max(0, int(value)) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def needs_transcode(codec: str | None, meta: dict[str, Any] | None = None) -> bool:
    """True when the browser path must be the `_h264` libx264 re-encode (§8.3): H.265 sources, and H.264 sources
    that use B-frames — MediaMTX refuses a WebRTC reader for them ("WebRTC doesn't support H264 streams with
    B-frames") and its HLS muxer gives up on long reorder chains ("unable to extract DTS: too many reordered
    frames"), as sandbox cam09/cam24/cam28/cam29 do. The re-encode uses `-tune zerolatency` (no B-frames)."""
    return (codec or "").upper() == "H265" or source_b_frames(meta) > 0


def play_path(camera_id: int, codec: str | None, meta: dict[str, Any] | None = None) -> str:
    return h264_path(camera_id) if needs_transcode(codec, meta) else relay_path(camera_id)


def whep_supported(codec: str | None, meta: dict[str, Any] | None) -> bool:
    """Whether the browser play path can go over WebRTC. A transcoded path (H.265, B-frame H.264) is plain
    zerolatency H.264 and always qualifies; a raw relay path qualifies unless the probe saw B-frames (which
    `needs_transcode` already routes to the `_h264` path, so this is False only for a camera whose path set has
    not been refreshed since its probe). Unknown (no probe yet) → assume WebRTC."""
    if needs_transcode(codec, meta):
        return True
    return source_b_frames(meta) == 0


def relay_rtsp_url(camera_id: int) -> str:
    return f"{settings.MEDIAMTX_RTSP_URL.rstrip('/')}/{relay_path(camera_id)}"


def _scrub(text: str) -> str:
    """MediaMTX may echo the path's `source` (which carries the sandbox credentials) in an error body; every
    detail that can reach an import warning, an audit row or a log passes through the URL-password mask."""
    from app.services.serializers import mask_secrets_in_text

    return mask_secrets_in_text(text[:500]) or ""


def _record_block(enabled: bool) -> dict[str, Any]:
    return {
        "record": bool(enabled),
        "recordPath": RECORD_PATH,
        "recordFormat": "fmp4",
        "recordSegmentDuration": "1m",
        "recordDeleteAfter": "12h",
    }


def is_external_source(rtsp_url: str | None) -> bool:
    """True when the camera URL points outside our own MediaMTX (the organiser sandbox, a vendor NVR, a phone):
    such sources are probed directly by the health poller and get a longer on-demand start timeout."""
    if not rtsp_url:
        return False
    for base in (settings.MEDIAMTX_RTSP_URL, settings.MEDIAMTX_RTSP_LOCAL_URL):
        if rtsp_url.lower().startswith(base.rstrip("/").lower() + "/"):
            return False
    return True


def external_probe_timeout_s() -> float:
    """Timeout for probing an external (sandbox) camera directly: the sandbox needs 4-38 s to answer a DESCRIBE
    (CONTRACT Amendments 2026-09-05, `sandbox.probe_timeout_s`, default 30 s)."""
    from app.services import settings_service as cfg

    try:
        return float(cfg.get("sandbox.probe_timeout_s") or settings.SANDBOX_PROBE_TIMEOUT_S)
    except (TypeError, ValueError):
        return float(settings.SANDBOX_PROBE_TIMEOUT_S)


def on_demand_start_timeout_s(rtsp_url: str | None) -> int:
    """MediaMTX `sourceOnDemandStartTimeout`: 15 s for local sources, probe timeout + 10 s (>= 15 s) for external
    ones so a slow sandbox camera can still come up for the wall/ANPR instead of timing out at 15 s."""
    if not is_external_source(rtsp_url):
        return 15
    return max(15, int(external_probe_timeout_s()) + 10)


def source_path_body(rtsp_url: str, record: bool, persistent: bool = False) -> dict[str, Any]:
    """`cam_<id>` config (§8.3). `persistent=True` → `sourceOnDemand: false` (the relay keeps one upstream
    connection and reconnects itself); otherwise on demand with the 10-minute close-after."""
    body = {
        "source": rtsp_url,
        "sourceOnDemandStartTimeout": f"{on_demand_start_timeout_s(rtsp_url)}s",
        "rtspTransport": "tcp",
    }
    body.update(relay_policy_body(PERSISTENT if persistent else ON_DEMAND))
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
    # CPU (laptop): browser tiles are capped at 720p and encoded with the cheapest x264 preset — 17 of the 30
    # sandbox cameras need the re-encode (6 H.265 + 11 B-frame H.264) and five 1080p `veryfast` encoders already
    # saturated the 4-core MediaMTX cap (runOnDemand start timeouts). `min(720,ih)` never upscales.
    return (
        f"ffmpeg -nostdin -loglevel error -rtsp_transport tcp -i {src} "
        f"-vf \"scale=-2:'min(720,ih)'\" -c:v libx264 -preset ultrafast -tune zerolatency -b:v 1200k -g 30 -an -f rtsp {dst}"
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

    async def get_config_path(self, name: str) -> dict[str, Any] | None:
        """Configured (not runtime) path, or None when absent / MediaMTX unreachable."""
        status, data = await self._req("GET", f"/v3/config/paths/get/{name}")
        return data if status == 200 and isinstance(data, dict) else None

    async def add_or_patch_path(self, name: str, body: dict[str, Any], recreate: bool = False) -> PathResult:
        """Idempotent: a path whose config already equals `body` is left alone (MediaMTX restarts a path — and
        its upstream connection — on every PATCH that changes something). A change that makes the relay dial
        upstream now (a persistent source) passes through `dial_gate`."""
        if recreate:
            await self._req("DELETE", f"/v3/config/paths/delete/{name}")
        else:
            current = await self.get_config_path(name)
            if current is not None and config_matches(current, body):
                return PathResult(True, 200, "unchanged")
        if body.get("sourceOnDemand") is False:
            await dial_gate.wait()
            if not recreate:
                # re-check after the wait: the startup pass and the first poller tick may queue the same change
                current = await self.get_config_path(name)
                if current is not None and config_matches(current, body):
                    return PathResult(True, 200, "unchanged")
        status, data = await self._req("POST", f"/v3/config/paths/add/{name}", body)
        if status == 200:
            return PathResult(True, status, "created")
        if status == 400 and "already exists" in str(data):
            status2, data2 = await self._req("PATCH", f"/v3/config/paths/patch/{name}", body)
            if status2 == 200:
                return PathResult(True, status2, "patched")
            return PathResult(False, status2, _scrub(f"MediaMTX: {status2} {data2}"))
        return PathResult(False, status, _scrub(f"MediaMTX: {status} {data}"))

    async def delete_path(self, name: str) -> PathResult:
        status, data = await self._req("DELETE", f"/v3/config/paths/delete/{name}")
        if status in (200, 404):
            return PathResult(True, status, "deleted" if status == 200 else "absent")
        return PathResult(False, status, _scrub(f"MediaMTX: {status} {data}"))

    async def ensure_camera_paths(self, camera, recreate: bool = False) -> list[PathResult]:
        """Create (or patch) `cam_<id>` — with the relay-source policy of `relay_policy(camera)` — and, for
        H.265 / B-frame H.264 sources, `cam_<id>_h264` (§8.3). Unchanged paths are not touched."""
        results: list[PathResult] = []
        if not camera.rtsp_url:
            return results
        transcode = needs_transcode(camera.codec, getattr(camera, "meta", None))
        record = bool(camera.record_enabled)
        persistent = relay_policy(camera) == PERSISTENT
        name = relay_path(camera.id)
        before = None if recreate else config_policy(await self.get_config_path(name))
        res = await self.add_or_patch_path(name, source_path_body(camera.rtsp_url, record and not transcode, persistent), recreate)
        results.append(res)
        if res.ok and res.detail != "unchanged":
            after = PERSISTENT if persistent else ON_DEMAND
            if before != after:
                log.info("relay policy %s: %s -> %s (%s)", name, before or "new", after, res.detail)
        if transcode:
            results.append(await self.add_or_patch_path(h264_path(camera.id), transcode_path_body(camera.id, record), recreate))
        return results

    async def ensure_relay_policy(self, camera) -> PathResult:
        """Apply only the relay-source policy (persistent vs on-demand, 10-minute close-after) to `cam_<id>`,
        without touching the rest of the path config. Called at import time, whenever `anpr_enabled` changes
        through the cameras API (via `ensure_camera_paths`) and by the health poller as a self-heal, so the
        policy survives API and MediaMTX restarts. A missing path is (re)created in full."""
        if not getattr(camera, "rtsp_url", None) or getattr(camera, "status", None) == "retired":
            return PathResult(True, None, "skipped")
        name = relay_path(camera.id)
        policy = relay_policy(camera)
        current = await self.get_config_path(name)
        if current is None:
            res = await self.ensure_camera_paths(camera)
            return res[0] if res else PathResult(False, None, "no path")
        body = relay_policy_body(policy)
        if config_matches(current, body):
            return PathResult(True, 200, "unchanged")
        if policy == PERSISTENT:
            await dial_gate.wait()
            latest = await self.get_config_path(name)  # re-check: another task may have applied it while we waited
            if latest is not None and config_matches(latest, body):
                return PathResult(True, 200, "unchanged")
        status, data = await self._req("PATCH", f"/v3/config/paths/patch/{name}", body)
        if status == 200:
            log.info("relay policy %s: %s -> %s (patched)", name, config_policy(current) or "?", policy)
            return PathResult(True, status, "patched")
        return PathResult(False, status, _scrub(f"MediaMTX: {status} {data}"))

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

    async def list_config_path_items(self) -> dict[str, dict[str, Any]] | None:
        """Configured paths indexed by name (their `source` carries credentials — never log an item), or None
        when MediaMTX is unreachable."""
        items: dict[str, dict[str, Any]] = {}
        page = 0
        while True:
            status, data = await self._req("GET", f"/v3/config/paths/list?page={page}&itemsPerPage=1000")
            if status != 200 or not isinstance(data, dict):
                return None
            for it in data.get("items", []):
                items[it.get("name", "")] = it
            page += 1
            if page >= int(data.get("pageCount", 1) or 1):
                break
        return items

    async def list_config_paths(self) -> set[str] | None:
        items = await self.list_config_path_items()
        return None if items is None else set(items)

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
