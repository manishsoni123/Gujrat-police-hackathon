"""`CameraSourceAdapter` – the visible seed of Model 3 (plan S5).

Implementations: `RtspAdapter` (any RTSP URL, incl. own/phone feeds) and
`SandboxCatalogueAdapter` (organiser `/api/ingest`). An ONVIF adapter is roadmap (S8).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceCamera:
    """Vendor-neutral camera description produced by an adapter (maps 1:1 to CameraImportRow)."""

    external_id: str
    name: str
    rtsp_url: str | None = None
    department_code: str | None = None
    lat: float | None = None
    lon: float | None = None
    district: str | None = None
    address: str | None = None
    codec: str | None = None
    resolution: str | None = None
    fps: int | None = None
    live: bool | None = None
    type: str | None = None
    whep_url: str | None = None
    hls_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "extra" and v is not None}
        return d


@dataclass
class ProbeResult:
    ok: bool
    codec: str | None = None
    width: int | None = None
    height: int | None = None
    error: str | None = None
    fps: float | None = None  # only when plausible (1–60); never trusted for timing (organiser rule: PTS)
    profile: str | None = None
    b_frames: int | None = None  # ffprobe `has_b_frames`: WebRTC cannot carry B-frame H.264 (MediaMTX refuses the reader)
    duration_ms: int | None = None

    @property
    def resolution(self) -> str | None:
        return f"{self.width}x{self.height}" if self.width and self.height else None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "codec": self.codec, "resolution": self.resolution, "fps": self.fps, "profile": self.profile, "b_frames": self.b_frames, "duration_ms": self.duration_ms, "error": self.error}


class CameraSourceAdapter(ABC):
    """Contract every camera source must implement (RTSP, catalogue, ONVIF, vendor SDK …)."""

    name: str = "base"

    @abstractmethod
    async def list_cameras(self) -> list[SourceCamera]:
        """Enumerate cameras known to the source."""

    @abstractmethod
    def stream_url(self, camera: SourceCamera) -> str | None:
        """Return the RTSP URL the relay should pull."""

    @abstractmethod
    async def probe(self, camera: SourceCamera) -> ProbeResult:
        """Check that the stream is reachable and report codec/resolution."""
