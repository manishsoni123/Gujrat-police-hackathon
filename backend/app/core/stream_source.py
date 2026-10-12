"""Camera source URLs other than RTSP (CONTRACT Amendments 2026-10-12).

`cameras.rtsp_url` keeps its name but holds any source MediaMTX v1.20.1 can pull as a static source:
RTSP/RTSPS, RTMP/RTMPS, SRT, HLS over HTTP(S) and WebRTC (WHEP). Browsers, the ANPR worker and recordings
always read the relay path `cam_<id>`, so only the relay and the probes ever see the source URL itself.

Pure helpers (no I/O) shared by the schema validator, the ffprobe callers and the health poller.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

# scheme -> kind; the order is the one the validator error message lists
SOURCE_KINDS: dict[str, str] = {
    "rtsp": "rtsp", "rtsps": "rtsp",
    "rtmp": "rtmp", "rtmps": "rtmp",
    "srt": "srt",
    "http": "hls", "https": "hls",
    "whep": "whep", "wheps": "whep",
}
SCHEMES_TEXT = "rtsp://, rtsps://, rtmp://, rtmps://, srt://, http(s):// (HLS) or whep(s):// (WebRTC)"


def source_kind(url: str | None) -> str | None:
    """`rtsp` | `rtmp` | `srt` | `hls` | `whep`, or None for an empty / unsupported URL."""
    if not url:
        return None
    return SOURCE_KINDS.get(urlsplit(url.strip()).scheme.lower())


def normalise_source_url(url: str) -> str:
    """Validate a camera source URL; returns the value to store or raises ValueError.

    `http(s)://…/whep` is the spelling WHEP endpoints are usually published with; MediaMTX would treat any
    http(s) source as HLS, so such a URL is rewritten to `whep(s)://` (same host, path and credentials)."""
    s = url.strip()
    parts = urlsplit(s)
    scheme = parts.scheme.lower()
    if scheme not in SOURCE_KINDS or not parts.netloc:
        raise ValueError(f"must start with {SCHEMES_TEXT}")
    if scheme in ("http", "https") and parts.path.rstrip("/").lower().endswith("/whep"):
        return urlunsplit(("whep" if scheme == "http" else "wheps",) + tuple(parts)[1:])
    return s


def ffprobe_input_args(url: str, timeout_s: float) -> list[str]:
    """Protocol-specific ffprobe input options. `-timeout` means a socket timeout only for RTSP (for RTMP it is
    a *listen* timeout), so every other protocol uses the generic `-rw_timeout`."""
    micros = str(int(timeout_s * 1_000_000))
    if source_kind(url) == "rtsp":
        return ["-rtsp_transport", "tcp", "-timeout", micros]
    return ["-rw_timeout", micros]


def probe_directly(url: str | None) -> bool:
    """False for WHEP: ffprobe has no WebRTC client, so such a camera is probed through its relay path."""
    return source_kind(url) not in (None, "whep")
