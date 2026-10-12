"""Address <-> coordinate lookup for the camera form, proxied to OpenStreetMap Nominatim.

Nominatim's usage policy (https://operations.osmfoundation.org/policies/nominatim/) asks for an
identifying User-Agent, at most one request per second and client-side caching; all three live here
so the browser never calls the public service directly. `geocoder.url` empty disables the lookup.
The district is resolved from the PostGIS `districts` polygons when a database is at hand, so it
matches the registry's canonical names and still works without Internet access.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.services import settings_service as cfg
from app.services.districts import canonical_district

log = logging.getLogger("sentinel.geocoder")

# Gujarat bounding box (lon_min, lat_max, lon_max, lat_min) - a preference, not a hard filter
GUJARAT_VIEWBOX = "68.1,24.8,74.6,20.0"
MIN_INTERVAL_S = 1.0
TIMEOUT_S = 5.0
CACHE_MAX = 1000
CACHE_TTL_S = 24 * 3600

_cache: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
_lock = asyncio.Lock()
_last_call = 0.0
# tests swap in an httpx.MockTransport; None = real network
transport: httpx.AsyncBaseTransport | None = None


def unavailable(detail: str = "Address lookup is unavailable; set the location on the map") -> ApiError:
    return ApiError(503, detail, code="geocoder_unavailable", errors=[])


def _cache_get(key: tuple) -> Any | None:
    hit = _cache.get(key)
    if not hit:
        return None
    if time.monotonic() - hit[0] > CACHE_TTL_S:
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return hit[1]


def _cache_put(key: tuple, value: Any) -> None:
    _cache[key] = (time.monotonic(), value)
    _cache.move_to_end(key)
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)


def clear_cache() -> None:
    _cache.clear()


def parse_place(raw: dict[str, Any]) -> dict[str, Any]:
    """One Nominatim jsonv2 result -> the shape the camera form uses."""
    a = raw.get("address") or {}
    district = a.get("state_district") or a.get("county") or a.get("district")
    if district:
        district = canonical_district(str(district).removesuffix(" District").removesuffix(" district"))
    return {
        "display_name": raw.get("display_name"),
        "lat": round(float(raw["lat"]), 6),
        "lon": round(float(raw["lon"]), 6),
        "district": district,
        "ward": a.get("suburb") or a.get("neighbourhood") or a.get("quarter") or a.get("city_district"),
        "road": a.get("road"),
        "city": a.get("city") or a.get("town") or a.get("village"),
        "postcode": a.get("postcode"),
        "source": "osm-nominatim",
    }


async def _fetch(path: str, params: dict[str, Any]) -> Any:
    """GET {geocoder.url}{path}, spaced at least MIN_INTERVAL_S apart across the whole process."""
    global _last_call
    base = str(cfg.get("geocoder.url") or "").rstrip("/")
    if not base:
        raise unavailable("Address lookup is disabled (geocoder.url is empty)")
    headers = {"User-Agent": str(cfg.get("geocoder.user_agent")), "Accept-Language": "en"}
    async with _lock:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=transport) as client:
                r = await client.get(f"{base}{path}", params={**params, "format": "jsonv2", "addressdetails": 1}, headers=headers)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("geocoder %s failed: %s", path, type(e).__name__)
            raise unavailable() from e
        finally:
            _last_call = time.monotonic()


async def district_at(db: AsyncSession, lat: float, lon: float) -> str | None:
    row = (
        await db.execute(
            text("SELECT name FROM districts WHERE ST_Covers(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)) LIMIT 1"),
            {"lat": lat, "lon": lon},
        )
    ).first()
    return row[0] if row else None


async def search(q: str) -> list[dict[str, Any]]:
    key = ("search", " ".join(q.lower().split()))
    hit = _cache_get(key)
    if hit is not None:
        return hit
    raw = await _fetch("/search", {"q": q, "countrycodes": "in", "viewbox": GUJARAT_VIEWBOX, "bounded": 0, "limit": 5})
    places: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in raw:
        if not (isinstance(p, dict) and "lat" in p and "lon" in p):
            continue
        place = parse_place(p)
        name = place["display_name"] or f"{place['lat']},{place['lon']}"
        if name not in seen:  # OSM often has a node and a building for the same place
            seen.add(name)
            places.append(place)
    _cache_put(key, places)
    return places


async def reverse(lat: float, lon: float) -> dict[str, Any] | None:
    key = ("reverse", round(lat, 5), round(lon, 5))
    hit = _cache_get(key)
    if hit is not None:
        return hit or None
    raw = await _fetch("/reverse", {"lat": lat, "lon": lon, "zoom": 18})
    place = parse_place(raw) if isinstance(raw, dict) and "lat" in raw else None
    _cache_put(key, place or {})
    return place
