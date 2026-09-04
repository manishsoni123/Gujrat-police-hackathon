"""Route reconstruction (CONTRACT §5.10): ordering, same-camera merge, haversine legs, flags. Pure."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

EARTH_RADIUS_KM = 6371.0088
MERGE_WINDOW_S = 60


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass
class RoutePoint:
    sighting_id: int
    camera_id: int
    camera_name: str
    first_seen: datetime
    last_seen: datetime
    read_count: int
    best_conf: float
    lat: float | None
    lon: float | None
    match: str = "exact"  # exact | fuzzy
    confirmation: str | None = None
    crop_url: str | None = None
    frame_url: str | None = None
    camera: dict[str, Any] = field(default_factory=dict)
    recording_available: bool = False
    merged_ids: list[int] = field(default_factory=list)


def merge_same_camera(points: list[RoutePoint], window_s: int = MERGE_WINDOW_S) -> list[RoutePoint]:
    """Consecutive sightings on the same camera within `window_s` become one stop (inputs are not mutated)."""
    ordered = sorted((replace(p, merged_ids=list(p.merged_ids)) for p in points), key=lambda p: (p.first_seen, p.sighting_id))
    out: list[RoutePoint] = []
    for p in ordered:
        if out and out[-1].camera_id == p.camera_id and (p.first_seen - out[-1].last_seen) <= timedelta(seconds=window_s):
            prev = out[-1]
            prev.last_seen = max(prev.last_seen, p.last_seen)
            prev.read_count += p.read_count
            prev.merged_ids.append(p.sighting_id)
            if p.best_conf > prev.best_conf:
                prev.best_conf = p.best_conf
                prev.crop_url = p.crop_url or prev.crop_url
                prev.frame_url = p.frame_url or prev.frame_url
            if prev.match == "fuzzy" and p.match == "exact":
                prev.match = "exact"
        else:
            out.append(p)
    return out


def build_route(points: list[RoutePoint], speed_flag_kmh: float = 150.0, max_gap_h: float = 6.0) -> dict[str, Any]:
    stops = merge_same_camera(points)
    sightings: list[dict[str, Any]] = []
    polyline: list[list[float]] = []
    legs: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    total_km = 0.0

    for seq, p in enumerate(stops, start=1):
        in_poly = p.lat is not None and p.lon is not None
        if in_poly:
            polyline.append([round(p.lat, 6), round(p.lon, 6)])
        sightings.append(
            {
                "seq": seq,
                "sighting_id": p.sighting_id,
                "merged_sighting_ids": p.merged_ids,
                "camera": p.camera or {"id": p.camera_id, "name": p.camera_name, "lat": p.lat, "lon": p.lon},
                "first_seen": p.first_seen,
                "last_seen": p.last_seen,
                "read_count": p.read_count,
                "best_conf": round(p.best_conf, 3),
                "crop_url": p.crop_url,
                "frame_url": p.frame_url,
                "match": p.match,
                "confirmation": p.confirmation,
                "recording_available": p.recording_available,
                "in_polyline": in_poly,
            }
        )

    for i in range(1, len(stops)):
        a, b = stops[i - 1], stops[i]
        leg: dict[str, Any] = {"from_seq": i, "to_seq": i + 1, "distance_km": None, "minutes": None, "speed_kmh": None, "flags": []}
        minutes = (b.first_seen - a.last_seen).total_seconds() / 60.0
        leg["minutes"] = round(minutes, 2)
        if a.lat is not None and b.lat is not None:
            d = haversine_km(a.lat, a.lon, b.lat, b.lon)
            leg["distance_km"] = round(d, 3)
            total_km += d
            if minutes <= 0:
                leg["speed_kmh"] = None
                leg["flags"].append("overlap")
            else:
                speed = d / (minutes / 60.0)
                leg["speed_kmh"] = round(speed, 1)
                if speed > speed_flag_kmh:
                    leg["flags"].append("implausible_speed")
                    flags.append(
                        {
                            "from_seq": i,
                            "to_seq": i + 1,
                            "type": "implausible_speed",
                            "distance_km": round(d, 2),
                            "minutes": round(minutes, 2),
                            "speed_kmh": round(speed, 1),
                            "message": f"{speed:.0f} km/h between {a.camera_name} and {b.camera_name} ({minutes:.1f} min for {d:.1f} km)",
                        }
                    )
        else:
            leg["flags"].append("no_coordinates")
        if minutes > max_gap_h * 60:
            leg["flags"].append("long_gap")
            flags.append(
                {
                    "from_seq": i,
                    "to_seq": i + 1,
                    "type": "long_gap",
                    "distance_km": leg["distance_km"],
                    "minutes": round(minutes, 2),
                    "speed_kmh": leg["speed_kmh"],
                    "message": f"{minutes / 60:.1f} h gap between {a.camera_name} and {b.camera_name}",
                }
            )
        if minutes <= 0 and "overlap" not in leg["flags"]:
            leg["flags"].append("overlap")
        legs.append(leg)

    duration_min = 0.0
    if stops:
        duration_min = (stops[-1].last_seen - stops[0].first_seen).total_seconds() / 60.0
    return {
        "sightings": sightings,
        "polyline": polyline,
        "legs": legs,
        "flags": flags,
        "total_distance_km": round(total_km, 2),
        "total_duration_min": round(duration_min, 2),
        "cameras_count": len({p.camera_id for p in stops}),
    }
