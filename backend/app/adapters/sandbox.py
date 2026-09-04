"""Organiser sandbox catalogue adapter (`GET {base_url}/api/ingest`) with tolerant field mapping."""

from __future__ import annotations

from typing import Any

from app.adapters.base import CameraSourceAdapter, ProbeResult, SourceCamera
from app.adapters.rtsp import ffprobe_details
from app.services.sandbox_catalogue import CatalogueConfig, fetch_catalogue, map_item


class SandboxCatalogueAdapter(CameraSourceAdapter):
    name = "sandbox"

    def __init__(self, config: CatalogueConfig | None = None) -> None:
        self.config = config or CatalogueConfig.from_settings()
        self.last_status: int | None = None
        self.last_duration_ms: int | None = None
        self.unmapped_fields: list[str] = []
        self.raw_items: list[dict[str, Any]] = []

    async def list_cameras(self) -> list[SourceCamera]:
        items, status, duration = await fetch_catalogue(self.config)
        self.last_status, self.last_duration_ms, self.raw_items = status, duration, items
        cams: list[SourceCamera] = []
        unmapped: set[str] = set()
        for item in items:
            row, um = map_item(item, self.config.field_map)
            unmapped.update(um)
            if not row.get("external_id") or not row.get("name"):
                # keep the row so the importer reports a proper row error
                row.setdefault("external_id", "")
                row.setdefault("name", "")
            cams.append(SourceCamera(
                external_id=str(row.get("external_id", "")),
                name=str(row.get("name", "")),
                rtsp_url=row.get("rtsp_url"),
                department_code=row.get("department_code"),
                lat=row.get("lat"),
                lon=row.get("lon"),
                district=row.get("district"),
                address=row.get("address"),
                codec=row.get("codec"),
                resolution=row.get("resolution"),
                fps=row.get("fps"),
                live=row.get("live"),
                type=row.get("type"),
                whep_url=row.get("whep_url"),
                hls_url=row.get("hls_url"),
                extra={k: v for k, v in row.items() if k in ("police_station", "install_date")},
            ))
        self.unmapped_fields = sorted(unmapped)
        return cams

    def stream_url(self, camera: SourceCamera) -> str | None:
        return camera.rtsp_url

    async def probe(self, camera: SourceCamera) -> ProbeResult:
        if not camera.rtsp_url:
            return ProbeResult(False, error="no rtsp_url")
        return await ffprobe_details(camera.rtsp_url)
