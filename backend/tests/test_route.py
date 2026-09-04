"""Route reconstruction (CONTRACT §5.10): ordering, same-camera merge, haversine legs, speed flags."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.route_builder import RoutePoint, build_route, haversine_km, merge_same_camera

T0 = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)

# Gandhinagar mock cameras (CONTRACT §6.2)
SACHIVALAYA = (23.2236, 72.6480)
CIVIL_HOSPITAL = (23.2275, 72.6485)
CH0 = (23.2170, 72.6360)
BUS_STAND = (23.2300, 72.6300)


def _pt(sid: int, cam: int, name: str, latlon: tuple[float, float] | None, start_s: float, dur_s: float = 4.0, **kw) -> RoutePoint:
    lat, lon = latlon if latlon else (None, None)
    return RoutePoint(
        sighting_id=sid, camera_id=cam, camera_name=name,
        first_seen=T0 + timedelta(seconds=start_s), last_seen=T0 + timedelta(seconds=start_s + dur_s),
        read_count=kw.get("read_count", 2), best_conf=kw.get("best_conf", 0.9), lat=lat, lon=lon,
        match=kw.get("match", "exact"), confirmation=kw.get("confirmation"),
        camera={"id": cam, "name": name, "lat": lat, "lon": lon, "district": "Gandhinagar"},
    )


def test_haversine_known_distance() -> None:
    # Sachivalaya → Civil Hospital ≈ 0.44 km
    d = haversine_km(*SACHIVALAYA, *CIVIL_HOSPITAL)
    assert 0.40 < d < 0.48
    assert haversine_km(0, 0, 0, 0) == 0.0


def test_ordering_by_first_seen() -> None:
    pts = [
        _pt(3, 6, "CH-0 Circle", CH0, 50),
        _pt(1, 1, "Sachivalaya Gate 1", SACHIVALAYA, 5),
        _pt(2, 3, "Civil Hospital", CIVIL_HOSPITAL, 25),
    ]
    r = build_route(pts)
    assert [s["sighting_id"] for s in r["sightings"]] == [1, 2, 3]
    assert [s["seq"] for s in r["sightings"]] == [1, 2, 3]
    assert len(r["polyline"]) == 3
    assert r["polyline"][0] == [23.2236, 72.648]
    assert r["cameras_count"] == 3
    assert r["total_distance_km"] > 0
    assert len(r["legs"]) == 2


def test_same_camera_merge_within_60s() -> None:
    pts = [
        _pt(1, 1, "Sachivalaya Gate 1", SACHIVALAYA, 0, 4, read_count=3),
        _pt(2, 1, "Sachivalaya Gate 1", SACHIVALAYA, 30, 4, read_count=2, best_conf=0.95),
        _pt(3, 1, "Sachivalaya Gate 1", SACHIVALAYA, 200, 4, read_count=1),  # > 60 s gap → new stop
    ]
    stops = merge_same_camera(pts)
    assert len(stops) == 2
    assert stops[0].read_count == 5
    assert stops[0].last_seen == T0 + timedelta(seconds=34)
    assert stops[0].best_conf == 0.95
    assert stops[0].merged_ids == [2]
    r = build_route(pts)
    assert len(r["sightings"]) == 2
    assert r["sightings"][0]["merged_sighting_ids"] == [2]
    assert r["sightings"][0]["read_count"] == 5


def test_speed_flag_on_looping_feed() -> None:
    # 20 s between two cameras 0.44 km apart → ≈ 80 km/h, below 150 → no flag
    slow = build_route([_pt(1, 1, "A", SACHIVALAYA, 0), _pt(2, 3, "B", CIVIL_HOSPITAL, 24)])
    assert slow["legs"][0]["flags"] == []
    assert slow["flags"] == []
    # 4 s between the same cameras → ≈ 400 km/h → flagged
    fast = build_route([_pt(1, 1, "A", SACHIVALAYA, 0), _pt(2, 3, "B", CIVIL_HOSPITAL, 8)])
    leg = fast["legs"][0]
    assert "implausible_speed" in leg["flags"]
    assert leg["speed_kmh"] > 150
    assert fast["flags"][0]["type"] == "implausible_speed"
    assert fast["flags"][0]["from_seq"] == 1 and fast["flags"][0]["to_seq"] == 2
    assert "km/h between A and B" in fast["flags"][0]["message"]
    # configurable threshold
    relaxed = build_route([_pt(1, 1, "A", SACHIVALAYA, 0), _pt(2, 3, "B", CIVIL_HOSPITAL, 8)], speed_flag_kmh=1000)
    assert relaxed["flags"] == []


def test_overlap_and_long_gap_flags() -> None:
    overlapping = build_route([_pt(1, 1, "A", SACHIVALAYA, 0, 10), _pt(2, 3, "B", CIVIL_HOSPITAL, 5, 10)])
    leg = overlapping["legs"][0]
    assert leg["speed_kmh"] is None
    assert "overlap" in leg["flags"]
    gap = build_route([_pt(1, 1, "A", SACHIVALAYA, 0), _pt(2, 3, "B", CIVIL_HOSPITAL, 8 * 3600)], max_gap_h=6)
    assert "long_gap" in gap["legs"][0]["flags"]
    assert any(f["type"] == "long_gap" for f in gap["flags"])


def test_camera_without_coordinates_is_listed_but_not_in_polyline() -> None:
    r = build_route([_pt(1, 1, "A", SACHIVALAYA, 0), _pt(2, 9, "No GPS", None, 30), _pt(3, 3, "B", CIVIL_HOSPITAL, 60)])
    assert len(r["sightings"]) == 3
    assert len(r["polyline"]) == 2
    assert r["sightings"][1]["in_polyline"] is False
    assert "no_coordinates" in r["legs"][0]["flags"]
    assert r["legs"][0]["distance_km"] is None


def test_fuzzy_marker_and_confirmation_survive() -> None:
    r = build_route([_pt(1, 1, "A", SACHIVALAYA, 0), _pt(2, 3, "B", CIVIL_HOSPITAL, 30, match="fuzzy", confirmation="confirmed")])
    assert r["sightings"][1]["match"] == "fuzzy"
    assert r["sightings"][1]["confirmation"] == "confirmed"
    merged = build_route([_pt(1, 1, "A", SACHIVALAYA, 0, match="fuzzy"), _pt(2, 1, "A", SACHIVALAYA, 10, match="exact")])
    assert merged["sightings"][0]["match"] == "exact"


def test_empty_route() -> None:
    r = build_route([])
    assert r == {"sightings": [], "polyline": [], "legs": [], "flags": [], "total_distance_km": 0.0, "total_duration_min": 0.0, "cameras_count": 0}
