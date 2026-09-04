"""Tolerant catalogue fetch + field mapping (CONTRACT §6.1, §6.3)."""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.services import settings_service as cfg

log = logging.getLogger("sentinel.catalogue")

WRAPPER_KEYS = ("cameras", "data", "items", "results", "streams")


@dataclass
class CatalogueConfig:
    base_url: str
    auth_type: str = "none"
    auth_username: str = ""
    auth_password: str = ""
    auth_header: str = ""
    timeout_s: float = 30.0
    field_map: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, overrides: dict[str, Any] | None = None) -> "CatalogueConfig":
        o = overrides or {}
        return cls(
            base_url=str(o.get("base_url") or cfg.get("catalogue.base_url")).rstrip("/"),
            auth_type=str(o.get("auth_type") or cfg.get("catalogue.auth_type") or "none"),
            auth_username=str(o.get("auth_username") if o.get("auth_username") is not None else cfg.get("catalogue.auth_username") or ""),
            auth_password=str(o.get("auth_password") if o.get("auth_password") is not None else cfg.get("catalogue.auth_password") or ""),
            auth_header=str(o.get("auth_header") if o.get("auth_header") is not None else cfg.get("catalogue.auth_header") or ""),
            timeout_s=float(o.get("timeout_s") or cfg.get("catalogue.timeout_s") or 30),
            field_map=dict(o.get("field_map") or cfg.get("catalogue.field_map") or {}),
        )

    @property
    def ingest_url(self) -> str:
        return f"{self.base_url}/api/ingest"

    def headers(self) -> dict[str, str]:
        h = {"Accept": "application/json", "User-Agent": "SentinelGujarat/1.0.0-phase1"}
        if self.auth_type == "basic" and self.auth_username:
            token = base64.b64encode(f"{self.auth_username}:{self.auth_password}".encode()).decode()
            h["Authorization"] = f"Basic {token}"
        elif self.auth_type == "bearer" and self.auth_password:
            h["Authorization"] = f"Bearer {self.auth_password}"
        elif self.auth_type == "header" and self.auth_header and ":" in self.auth_header:
            name, value = self.auth_header.split(":", 1)
            h[name.strip()] = value.strip()
        return h


class CatalogueError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def unwrap(data: Any) -> list[dict[str, Any]]:
    """Bare list, or the first list-valued wrapper key."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in WRAPPER_KEYS:
            v = data.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return [x for x in v if isinstance(x, dict)]
    raise CatalogueError("catalogue response is neither a list nor an object with a camera list")


def _get_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _present(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str) and v.strip() == "":
        return False
    return True


def _flatten_keys(obj: Any, prefix: str = "") -> list[str]:
    keys: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}{k}"
            if isinstance(v, dict):
                keys.extend(_flatten_keys(v, p + "."))
            else:
                keys.append(p)
    return keys


def map_item(item: dict[str, Any], field_map: dict[str, list[str]]) -> tuple[dict[str, Any], list[str]]:
    """Map one catalogue object into a CameraImportRow dict; returns (row, unmapped_source_keys)."""
    row: dict[str, Any] = {}
    used: set[str] = set()
    for target, candidates in field_map.items():
        for cand in candidates:
            v = _get_path(item, cand)
            if _present(v):
                row[target] = v
                used.add(cand)
                break
    if "external_id" in row:
        row["external_id"] = str(row["external_id"]).strip()
    if "live" in row and isinstance(row["live"], str):
        row["live"] = row["live"].strip().lower()
    # a mapped object path (e.g. `stream_properties.resolution` → {width, height}) covers its whole subtree
    unmapped = [k for k in _flatten_keys(item) if k not in used and not any(k.startswith(u + ".") for u in used)]
    return row, unmapped


async def fetch_catalogue(config: CatalogueConfig) -> tuple[list[dict[str, Any]], int, int]:
    """Return (items, http_status, duration_ms). Raises CatalogueError on failure."""
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=config.timeout_s, follow_redirects=True) as client:
            r = await client.get(config.ingest_url, headers=config.headers())
    except httpx.HTTPError as exc:
        raise CatalogueError(f"catalogue {config.ingest_url} unreachable: {exc.__class__.__name__}: {exc}") from exc
    duration = int((time.perf_counter() - started) * 1000)
    if r.status_code >= 300:
        raise CatalogueError(f"catalogue {config.ingest_url} returned HTTP {r.status_code}", r.status_code)
    try:
        data = r.json()
    except ValueError as exc:
        raise CatalogueError(f"catalogue {config.ingest_url} returned non-JSON content", r.status_code) from exc
    return unwrap(data), r.status_code, duration
