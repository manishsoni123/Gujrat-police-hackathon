"""Seed files match CONTRACT §6.2 / §12 (DB-free consistency checks)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from app.services.districts import DISTRICTS, canonical_district
from app.services.plates import normalise

ANCHORS = ["GJ01AB1234", "GJ18CD5678", "GJ05RS9012", "GJ27XY3456", "MH02BZ7788", "22BH4321AA"]


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_departments_seed(seeds_dir: Path) -> None:
    rows = _rows(seeds_dir / "departments.csv")
    assert len(rows) == 27
    assert rows[0]["code"] == "UNASSIGNED"
    codes = [r["code"] for r in rows]
    for must in ("POLICE", "HEALTH", "GSRTC", "PANCHAYAT", "MUNICIPAL", "RTO", "FCS"):
        assert must in codes
    assert len(set(codes)) == 27
    assert "gujarat police" in rows[1]["aliases"].split("|")


def test_users_seed(seeds_dir: Path) -> None:
    rows = _rows(seeds_dir / "users.csv")
    assert [r["username"] for r in rows] == ["jury_admin", "jury_operator", "jury_viewer", "dept_admin_police"]
    assert [r["role"] for r in rows] == ["admin", "operator", "viewer", "dept_admin"]
    assert rows[3]["department_code"] == "POLICE"
    assert all(r["password_env"].endswith("_PASSWORD") for r in rows)


def test_watchlist_seed(seeds_dir: Path) -> None:
    rows = _rows(seeds_dir / "watchlist_seed.csv")
    assert len(rows) == 20
    plates = [normalise(r["plate"]).plate_norm for r in rows if r["entity_type"] == "vehicle"]
    # §12.2: the anchor plates lead the file; 22BH4321AA is an ANPR-only anchor (BH normalisation) and is not watchlisted
    assert plates[0] == "GJ01AB1234"
    assert set(ANCHORS) - {"22BH4321AA"} <= set(plates[:6])
    assert all(normalise(p).is_valid_format for p in plates)
    assert len(set(plates)) == len(plates)
    demo = next(r for r in rows if normalise(r["plate"]).plate_norm == "GJ27XY3456")
    assert demo["is_active"] == "false"
    # only one synthetic anchor is seeded active with a critical floor (stolen/wanted), so the looping
    # demo footage raises critical alerts for one plate, not a stream of them (seed rev 2)
    by_plate = {normalise(r["plate"]).plate_norm: r for r in rows if r["entity_type"] == "vehicle"}
    active_critical_anchors = [
        p for p in ANCHORS if p in by_plate and by_plate[p]["is_active"] != "false" and (by_plate[p]["priority"] == "critical" or by_plate[p]["reason"] in ("stolen", "wanted"))
    ]
    assert active_critical_anchors == ["GJ01AB1234"]
    assert by_plate["GJ18CD5678"]["is_active"] == "false"
    assert sum(1 for r in rows if r["entity_type"] == "person") == 3
    # seed rev 3: rows 7+ are plates actually read on the organiser cameras (government feed, 5 Sept 2026);
    # every one of them names its camera and source in the notes and none collides with a retired filler
    from app.seed import RETIRED_FILLER_PLATES

    real = [r for r in rows[6:] if r["entity_type"] == "vehicle"]
    assert len(real) == 11 and all("Government feed" in r["notes"] and "cam07" in r["notes"] for r in real)
    assert all(r["is_active"] != "false" for r in real)
    assert not set(plates) & set(RETIRED_FILLER_PLATES)
    assert len({r["priority"] for r in real}) >= 3
    # seed rev 4: a real registration is never shown next to an invented case - demo label, no reason, no
    # e-GujCop / VAHAN source, explicit disclaimer; every other row is labelled as a demo entry too
    assert all(r["name"].startswith("DEMO TEST ENTRY – no real case") for r in real)
    assert all(r["reason"] == "other" and r["source"] == "manual" and "NOT linked to any real case" in r["notes"] for r in real)
    assert all(r["name"].startswith("DEMO") for r in rows)
    assert not any(r["source"] in ("egujcop", "vahan") for r in rows if r["entity_type"] == "person")


def test_pois_seed(seeds_dir: Path) -> None:
    rows = _rows(seeds_dir / "pois.csv")
    assert len(rows) >= 45
    gnr = [r for r in rows if r["district"] == "Gandhinagar"]
    assert len(gnr) >= 8
    names = {r["name"] for r in rows}
    for must in ("Sachivalaya Main Gate", "Sector 21 Government School", "Pethapur Checkpost", "Bhilad Border Checkpost", "Somnath Temple", "Dwarkadhish Temple", "Dahod Bus Stand"):
        assert must in names
    for r in rows:
        assert canonical_district(r["district"]) in DISTRICTS
        assert 20.0 <= float(r["lat"]) <= 24.8 and 68.0 <= float(r["lon"]) <= 74.5


def test_mock_catalogue(seeds_dir: Path) -> None:
    data = json.loads((seeds_dir / "mock_catalogue.json").read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 50
    assert [c["id"] for c in data] == list(range(1, 51))
    assert sum(1 for c in data if c["live"]) == 8 and all(c["live"] for c in data[:8])
    assert sum(1 for c in data if c["codec"] == "H265") == 11
    assert data[7]["codec"] == "H265"
    first = data[0]
    assert first["name"] == "Sachivalaya Gate 1" and first["department"] == "Police" and first["district"] == "Gandhinagar"
    assert first["location"] == {"lat": 23.2236, "lon": 72.648, "address": "Sachivalaya, Sector 10, Gandhinagar"}
    assert first["rtsp_url"] == "rtsp://mediamtx:8554/stream/1"
    assert first["whep_url"] == "http://mediamtx:8889/stream/1/whep"
    assert first["hls_url"] == "http://mediamtx:8888/stream/1/index.m3u8"
    assert {c["department"] for c in data} == {"Police", "Health", "GSRTC", "Panchayat", "Municipal Corporation"}
    assert {c["district"] for c in data} == {"Gandhinagar", "Ahmedabad", "Vadodara", "Surat", "Rajkot", "Valsad", "Dahod", "Gir Somnath", "Jamnagar", "Devbhumi Dwarka"}


def test_districts_geojson(seeds_dir: Path) -> None:
    data = json.loads((seeds_dir / "gujarat_districts.geojson").read_text(encoding="utf-8"))
    names = {f["properties"]["district"] for f in data["features"]}
    assert names == set(DISTRICTS)
    for f in data["features"]:
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_cameras_sample_rows(seeds_dir: Path) -> None:
    rows = _rows(seeds_dir / "cameras_sample.csv")
    assert len(rows) == 10
    assert rows[3]["lat"] == "95.0"  # row 4 invalid latitude
    assert rows[8]["external_id"] == "CSV-002" and rows[8]["name"] == ""  # row 9 duplicate + missing name
    assert rows[6]["department_code"] == "HOUSING_SOCIETY"  # row 7 unknown department → warning
    assert {r["district"] for r in rows} >= {"Valsad", "Dahod", "Ahmedabad", "Jamnagar", "Gir Somnath", "Devbhumi Dwarka"}
    assert {r["department_code"] for r in rows} >= {"FCS", "RTO", "POLICE"}


def test_watchlist_seed_rev4_rewrites_seeded_rows_once(seeds_dir: Path) -> None:
    """A database seeded at rev 3 gets the demo labels on its existing rows and no duplicate persons."""
    import asyncio

    from app.db.models import Setting, Watchlist
    from app.seed import REV4_PERSON_RENAMES, WATCHLIST_SEED_REV, _upgrade_watchlist_seed

    rows = _rows(seeds_dir / "watchlist_seed.csv")
    old_case = Watchlist(id=7, entity_type="vehicle", plate_norm="GJ14AA3978", name="Wanted – Gir Somnath cheating case – read on cam07",
                         reason="wanted", priority="high", source="egujcop", notes="Government feed: read on cam07", is_active=True)
    anchor = Watchlist(id=1, entity_type="vehicle", plate_norm="GJ01AB1234", name="White Maruti Swift – FIR 123/2026 Sector 7 PS",
                       reason="stolen", priority="critical", source="own", notes="old", is_active=True)
    old_person = Watchlist(id=18, entity_type="person", plate_norm=None, name="Ramesh K. (wanted – Ahmedabad PS)",
                           reason="wanted", priority="critical", source="egujcop", notes="FRS roadmap – no plate", is_active=True)

    class FakeDb:
        def __init__(self) -> None:
            self.marker = Setting(key="seed.watchlist_rev", value=3, is_secret=False)
            self.added: list[object] = []

        async def get(self, model: object, key: str) -> Setting:
            return self.marker

        def add(self, obj: object) -> None:
            self.added.append(obj)

    db = FakeDb()
    vehicles = {"GJ14AA3978": old_case, "GJ01AB1234": anchor}
    persons = {old_person.name: old_person}
    n = asyncio.run(_upgrade_watchlist_seed(db, vehicles, "now", rows=rows, persons=persons))
    assert n == 3 and db.marker.value == WATCHLIST_SEED_REV == 4 and not db.added
    assert old_case.name.startswith("DEMO TEST ENTRY – no real case") and old_case.reason == "other" and old_case.source == "manual"
    assert "NOT linked to any real case" in old_case.notes and old_case.priority == "high" and old_case.is_active
    assert anchor.name.startswith("DEMO – ") and anchor.reason == "stolen" and anchor.priority == "critical"
    assert old_person.name == REV4_PERSON_RENAMES["Ramesh K. (wanted – Ahmedabad PS)"] and old_person.source == "manual"
    # second run at rev 4: nothing left to do
    assert asyncio.run(_upgrade_watchlist_seed(db, vehicles, "now", rows=rows, persons=persons)) == 0
