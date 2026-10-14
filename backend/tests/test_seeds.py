"""Seed files match CONTRACT §6.2 / §12 (DB-free consistency checks)."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest

from app.db.models import Setting, Watchlist
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




# ---------------------------------------------------------------- watchlist seed upgrades (DB-free)
# Fixtures never pair a plate read on the government feed with a case: the anchors are synthetic,
# and every other stand-in is an obviously fictional, invalid-format `ZZ…` plate with neutral text
# (guarded by test_no_file_pairs_a_government_feed_plate_with_a_case).


class _Result:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return list(self._items)

    def scalar_one_or_none(self) -> Any:
        return self._items[0] if self._items else None


class FakeSession:
    """Just enough AsyncSession for `seed_watchlist`. `heap` is the order an unordered SELECT
    returns the rows in; an ORDER BY sorts them by id (the only order the seed asks for). No users
    exist."""

    def __init__(self, heap: list[Watchlist], rev: int | None) -> None:
        self.heap = heap
        self.marker = Setting(key="seed.watchlist_rev", value=rev, is_secret=False) if rev else None
        self.added: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        if stmt.column_descriptions[0]["entity"] is not Watchlist:
            return _Result([])
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        rows = [
            w for w in self.heap
            if "entity_type =" not in sql or f"entity_type = '{w.entity_type}'" in sql
        ]
        if "ORDER BY" in sql:
            rows.sort(key=lambda w: w.id)
        return _Result(rows)

    async def get(self, model: Any, key: str) -> Any:
        return self.marker if model is Setting else None

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        if isinstance(obj, Setting):
            self.marker = obj

    async def commit(self) -> None:
        return None


def _entry(id_: int, plate: str | None, name: str, reason: str, source: str, notes: str | None,
           active: bool = True, priority: str = "medium") -> Watchlist:
    return Watchlist(id=id_, entity_type="vehicle" if plate else "person", plate_norm=plate,
                     name=name, reason=reason, priority=priority, source=source, notes=notes,
                     is_active=active)


def _text(w: Watchlist) -> tuple[Any, ...]:
    return (w.name, w.reason, w.priority, w.source, w.notes, w.is_active)


def _csv_row(seeds_dir: Path, plate: str) -> dict[str, str]:
    return next(r for r in _rows(seeds_dir / "watchlist_seed.csv") if r["plate"] == plate)


def _fp(*parts: str | None) -> str:
    """The fingerprint format app.seed documents: sha256 of the `|`-joined parts, 16 hex digits."""
    return hashlib.sha256("|".join(p or "" for p in parts).encode("utf-8")).hexdigest()[:16]


def _add_seed_fingerprint(monkeypatch: Any, seed: Any, plate: str, *old: str) -> None:
    """Register a fictional plate's earlier seed text (name, reason, source) with the upgrade."""
    table = dict(getattr(seed, "PRE_REV4_SEED_FINGERPRINTS", {}))
    table[plate] = _fp(plate, *old)
    monkeypatch.setattr(seed, "PRE_REV4_SEED_FINGERPRINTS", table, raising=False)


DEMO_ROW_REV3 = ("Demo plate (added live during the jury demo)", "suspect", "manual",
                 "Not active by default – the demo adds it")


def test_watchlist_seed_upgrade_rewrites_the_seed_row_not_an_operator_entry(
        seeds_dir: Path) -> None:
    """Review finding 4: the jury re-adds GJ27XY3456 while its seed row is inactive (§12 step 6),
    so the plate has two rows; the upgrade relabels the seed row only, never the operator's entry
    nor a seed row an operator renamed, whatever order the rows come back in."""
    from app.seed import WATCHLIST_SEED_REV, seed_watchlist

    seed_row = _entry(6, "GJ27XY3456", *DEMO_ROW_REV3, active=False, priority="low")
    operator = _entry(21, "GJ27XY3456", "Operator: suspect seen at Infocity", "suspect", "manual",
                      "added live by jury_operator", priority="high")
    renamed = _entry(3, "GJ05RS9012", "Goods carrier - operator's own case", "blacklisted",
                     "manual", "edited by jury_operator", priority="high")
    before_operator, before_renamed = _text(operator), _text(renamed)
    db = FakeSession([seed_row, renamed, operator], rev=3)  # the operator row comes back last

    out = asyncio.run(seed_watchlist(db, seeds_dir))

    assert _text(operator) == before_operator and _text(renamed) == before_renamed
    want = _csv_row(seeds_dir, "GJ27XY3456")
    assert (seed_row.name, seed_row.notes) == (want["name"], want["notes"])
    assert (seed_row.reason, seed_row.source, seed_row.is_active) == ("suspect", "manual", False)
    assert out["upgraded"] == 1 and db.marker.value == WATCHLIST_SEED_REV
    assert not any(isinstance(w, Watchlist) and w.plate_norm in ("GJ27XY3456", "GJ05RS9012")
                   for w in db.added)  # both plates already exist: nothing re-added


def test_watchlist_seed_upgrade_repairs_a_database_already_at_rev4(seeds_dir: Path,
                                                                   caplog: Any) -> None:
    """A rev-4 upgrade that rewrote the operator's row left the seed row with its old text; the
    next seed revision relabels the seed row, leaves the operator's row as it is now and names it
    in a warning (its own text is only in the audit log)."""
    from app.seed import WATCHLIST_SEED_REV, seed_watchlist

    want = _csv_row(seeds_dir, "GJ27XY3456")
    seed_row = _entry(6, "GJ27XY3456", *DEMO_ROW_REV3, active=False, priority="low")
    operator = _entry(21, "GJ27XY3456", want["name"], "suspect", "manual", want["notes"],
                      priority="high")
    before_operator = _text(operator)
    db = FakeSession([seed_row, operator], rev=4)
    caplog.set_level(logging.WARNING, logger="sentinel.seed")

    out = asyncio.run(seed_watchlist(db, seeds_dir))

    assert (seed_row.name, seed_row.notes) == (want["name"], want["notes"])
    assert _text(operator) == before_operator
    assert out["upgraded"] == 1 and db.marker.value == WATCHLIST_SEED_REV == 5
    warned = [r.getMessage() for r in caplog.records if r.name == "sentinel.seed"]
    assert len(warned) == 1 and "entry 21 (GJ27XY3456)" in warned[0] and "audit log" in warned[0]
    caplog.clear()
    assert asyncio.run(seed_watchlist(db, seeds_dir))["upgraded"] == 0
    assert not [r for r in caplog.records if r.name == "sentinel.seed"]


def test_watchlist_seed_relabels_retired_fillers_on_a_rev4_database(seeds_dir: Path,
                                                                    monkeypatch: Any) -> None:
    """Review finding 5: a database seeded at rev 1-2 still holds the retired filler rows with
    their invented cases after rev 4; the next revision labels them as demo data (name, reason
    `other`, source `manual`, disclaimer), keeps them inactive and touches no other row."""
    import app.seed as seed

    plate = "ZZ99ZZ9999"  # fictional stand-in for one of RETIRED_FILLER_PLATES
    monkeypatch.setattr(seed, "RETIRED_FILLER_PLATES", (*seed.RETIRED_FILLER_PLATES, plate))
    _add_seed_fingerprint(monkeypatch, seed, plate, "OLD FILLER TEXT", "stolen", "egujcop")
    filler = _entry(9, plate, "OLD FILLER TEXT", "stolen", "egujcop", seed.RETIRED_NOTE,
                    active=False, priority="critical")
    own = _entry(30, plate, "Operator entry for the same plate", "stolen", "manual", None)
    before_own = _text(own)
    db = FakeSession([own, filler], rev=4)

    out = asyncio.run(seed.seed_watchlist(db, seeds_dir))

    assert filler.name.startswith("DEMO – ") and "no real case" in filler.name
    assert (filler.reason, filler.source) == ("other", "manual")
    assert filler.notes.startswith("DEMO/TEST watchlist entry - this registration is NOT linked")
    assert filler.notes.endswith(seed.RETIRED_NOTE)
    assert (filler.is_active, filler.priority) == (False, "critical")
    assert _text(own) == before_own
    assert out["upgraded"] == 1 and db.marker.value == seed.WATCHLIST_SEED_REV
    assert asyncio.run(seed.seed_watchlist(db, seeds_dir))["upgraded"] == 0


def test_watchlist_seed_rev3_retires_the_seed_row_not_an_operator_entry(seeds_dir: Path,
                                                                        monkeypatch: Any) -> None:
    """Rev 3 retires a filler plate's seed row (lowest id), never an operator's active entry for
    the plate that an unordered SELECT happens to return last; the seed row is then relabelled."""
    import app.seed as seed

    plate = "ZZ98ZZ9998"  # fictional stand-in for one of RETIRED_FILLER_PLATES
    monkeypatch.setattr(seed, "RETIRED_FILLER_PLATES", (*seed.RETIRED_FILLER_PLATES, plate))
    _add_seed_fingerprint(monkeypatch, seed, plate, "OLD FILLER TEXT", "suspect", "manual")
    filler = _entry(9, plate, "OLD FILLER TEXT", "suspect", "manual", None, active=False)
    own = _entry(30, plate, "Operator entry for the same plate", "suspect", "own", "seen today")
    before_own = _text(own)
    db = FakeSession([filler, own], rev=2)

    asyncio.run(seed.seed_watchlist(db, seeds_dir))

    assert _text(own) == before_own and own.is_active
    assert filler.name.startswith("DEMO – ") and not filler.is_active


def test_watchlist_seed_relabels_seeded_rows_once(seeds_dir: Path) -> None:
    """A database seeded at rev 3 (or earlier) gets the demo labels on its seed rows once, and no
    duplicate persons. Notes worded as an earlier seed revision wrote them (the rev 1-2 anchor
    wording too) become the CSV notes; an operator's own notes on a seed row stay."""
    from app.seed import REV4_PERSON_RENAMES, WATCHLIST_SEED_REV, _upgrade_watchlist_seed

    rows = _rows(seeds_dir / "watchlist_seed.csv")
    anchor = _entry(1, "GJ01AB1234", "White Maruti Swift – FIR 123/2026 Sector 7 PS", "stolen",
                    "own", "Reported stolen 2026-08-30 from Sector 7 Gandhinagar",  # rev 1-2 notes
                    priority="critical")
    carrier_csv = _csv_row(seeds_dir, "GJ05RS9012")
    carrier = _entry(3, "GJ05RS9012", "Blacklisted goods carrier (overloading)", "blacklisted",
                     "manual", carrier_csv["notes"], priority="high")
    honda = _entry(4, "MH02BZ7788", "Silver Honda City – missing person's vehicle", "missing",
                   "own", "operator: seen near Kalol", priority="medium")
    old_person = _entry(18, None, "Ramesh K. (wanted – Ahmedabad PS)", "wanted", "egujcop",
                        "FRS roadmap – no plate", priority="critical")
    db = FakeSession([anchor, carrier, honda, old_person], rev=3)

    n = asyncio.run(_upgrade_watchlist_seed(db, db.heap, "now", rows=rows))

    assert n == 4 and db.marker.value == WATCHLIST_SEED_REV and not db.added
    anchor_csv = _csv_row(seeds_dir, "GJ01AB1234")
    assert (anchor.name, anchor.notes) == (anchor_csv["name"], anchor_csv["notes"])
    assert anchor.name == "DEMO – White Maruti Swift – FIR 123/2026 Sector 7 PS"
    assert (anchor.reason, anchor.priority, anchor.source) == ("stolen", "critical", "own")
    assert (carrier.name, carrier.notes) == (carrier_csv["name"], carrier_csv["notes"])
    assert honda.name.startswith("DEMO – ") and honda.notes == "operator: seen near Kalol"
    assert old_person.name == REV4_PERSON_RENAMES["Ramesh K. (wanted – Ahmedabad PS)"]
    assert (old_person.reason, old_person.source) == ("wanted", "manual")
    db.marker.value = 4  # even a re-run of the relabel finds nothing left to do
    assert asyncio.run(_upgrade_watchlist_seed(db, db.heap, "now", rows=rows)) == 0


def test_watchlist_relabel_keeps_operator_notes_behind_the_disclaimer(monkeypatch: Any) -> None:
    """Notes that still read as the earlier seed wrote them (or are empty) become the seed's notes;
    an operator's annotation on a seed row is kept, with the disclaimer in front when the seed row
    carries one. Only the lowest-id row per plate that still carries the earlier text changes."""
    import app.seed as seed

    notes = f"{seed.DEMO_DISCLAIMER} Fictional test feed: read on camera X"
    plates = ("ZZ01ZZ0001", "ZZ01ZZ0002", "ZZ01ZZ0003")
    rows = [
        {"plate": p, "entity_type": "vehicle", "name": "DEMO TEST ENTRY – no real case",
         "reason": "other", "source": "manual", "notes": notes}
        for p in plates
    ]
    for p in plates:
        _add_seed_fingerprint(monkeypatch, seed, p, "OLD NAME", "suspect", "own")
    untouched = _entry(1, plates[0], "OLD NAME", "suspect", "own",
                       "Fictional test feed: read on camera X")
    copy = _entry(5, plates[0], "OLD NAME", "suspect", "own", "a later copy")
    annotated = _entry(2, plates[1], "OLD NAME", "suspect", "own", "seen again by jury_operator")
    empty = _entry(3, plates[2], "OLD NAME", "suspect", "own", None)

    assert seed._relabel_seed_rows([copy, annotated, empty, untouched], rows, "now") == 3

    assert (untouched.name, untouched.reason, untouched.source) == (
        "DEMO TEST ENTRY – no real case", "other", "manual")
    assert untouched.notes == notes and empty.notes == notes
    assert annotated.notes == f"{seed.DEMO_DISCLAIMER} seen again by jury_operator"
    assert (copy.name, copy.notes) == ("OLD NAME", "a later copy")


def test_pre_rev4_fingerprints_cover_every_earlier_seed_plate(seeds_dir: Path) -> None:
    """Every vehicle plate an earlier seed revision wrote has a fingerprint of its old identity; the
    anchors' old text is the current one without the `DEMO – ` prefix (their rev 1-2 notes lacked
    the "synthetic anchor" remark), and no row the current CSV writes matches, so a relabelled row
    is never relabelled again."""
    from app.seed import (DEMO_DISCLAIMER, PRE_REV4_NOTE_FINGERPRINTS, PRE_REV4_SEED_FINGERPRINTS,
                          RETIRED_FILLER_PLATES, seed_fingerprint)

    assert seed_fingerprint("A", None, "b") == _fp("A", "", "b")
    vehicles = [r for r in _rows(seeds_dir / "watchlist_seed.csv") if r["entity_type"] == "vehicle"]
    plates = [normalise(r["plate"]).plate_norm for r in vehicles]
    assert set(PRE_REV4_SEED_FINGERPRINTS) == set(plates) | set(RETIRED_FILLER_PLATES)
    assert len(PRE_REV4_SEED_FINGERPRINTS) == 31
    assert all(normalise(p).plate_norm == p for p in RETIRED_FILLER_PLATES)
    for plate, r in zip(plates[:6], vehicles[:6]):
        old_name = r["name"].removeprefix("DEMO – ")
        assert old_name != r["name"]
        assert PRE_REV4_SEED_FINGERPRINTS[plate] == _fp(plate, old_name, r["reason"], r["source"])
        rev12_notes = re.sub(r" \(synthetic anchor[^)]*\)|synthetic anchor \(mock loops\); ", "",
                             r["notes"])
        if rev12_notes != r["notes"]:
            assert PRE_REV4_NOTE_FINGERPRINTS[plate] == _fp(plate, rev12_notes)
    assert set(PRE_REV4_NOTE_FINGERPRINTS) == set(plates[:5])
    for plate, r in zip(plates, vehicles):
        assert _fp(plate, r["name"], r["reason"], r["source"]) != PRE_REV4_SEED_FINGERPRINTS[plate]
    assert all(r["notes"].startswith(DEMO_DISCLAIMER + " ") for r in vehicles[6:])


MOCK_DATA_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "mock" / "data.ts"
_TS_TOKEN = re.compile(r"""'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|null|true|false""")
# Words that attach a case to a plate; the demo labels themselves ("no real case") go first.
_CASE_WORDS = re.compile(
    r"\b(wanted|stolen|blacklist\w*|suspect\w*|missing|arrest\w*|e-?gujcop|vahan|ncrb|fir"
    r"|cases?)\b",
    re.IGNORECASE,
)
_DEMO_LABELS = ("DEMO TEST ENTRY – no real case", "NOT linked to any real case or person")


def test_no_file_pairs_a_government_feed_plate_with_a_case(seeds_dir: Path) -> None:
    """Review finding 6: no seed, backend test fixture or frontend mock line names a plate read on
    the government feed together with a case (reason, e-GujCop/VAHAN source, "case" wording); the
    only such lines are the seed's own `DEMO TEST ENTRY – no real case` rows and their mirror."""
    rows = _rows(seeds_dir / "watchlist_seed.csv")
    real = {
        normalise(r["plate"]).plate_norm for r in rows if r["name"].startswith("DEMO TEST ENTRY")
    }
    assert len(real) == 11
    files = [seeds_dir / "watchlist_seed.csv", *sorted(Path(__file__).parent.glob("*.py"))]
    if MOCK_DATA_TS.exists():
        files.append(MOCK_DATA_TS)
    offenders = []
    for f in files:
        for no, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            compact = re.sub(r"[\s-]", "", line).upper()
            if not any(p in compact for p in real):
                continue
            text = line
            for label in _DEMO_LABELS:
                text = text.replace(label, "")
            if _CASE_WORDS.search(text):
                offenders.append(f"{f.name}:{no}")
    assert not offenders


def _ts_value(tok: str) -> Any:
    if tok in ("null", "true", "false"):
        return {"null": None, "true": True, "false": False}[tok]
    return json.loads(tok) if tok.startswith('"') else tok[1:-1].replace("\\'", "'")


def test_frontend_mock_watchlist_mirrors_the_seed(seeds_dir: Path) -> None:
    """Review finding 7: the VITE_MOCK watchlist (`npm run dev:mock`, the mock screenshots) shows
    the seed's demo-labelled rows, not the old invented cases or the person "Ramesh K."."""
    if not MOCK_DATA_TS.exists():
        pytest.skip("frontend/src/mock/data.ts is not mounted next to backend/")
    src = MOCK_DATA_TS.read_text(encoding="utf-8")
    block = src[src.index("const WL: WlRow[] = [") :]
    block = block[: block.index("\n];")]
    mock = [
        [_ts_value(t) for t in _TS_TOKEN.findall(line)]
        for line in block.splitlines()[1:]
        if line.strip().startswith("[")
    ]
    seed = _rows(seeds_dir / "watchlist_seed.csv")
    assert [m[0] for m in mock] == [r["plate"] for r in seed]
    for m, r in zip(mock, seed):
        plate, entity, name, reason, priority, source, notes, expires, active = m
        assert (entity, name, reason, priority, source) == (
            r["entity_type"], r["name"], r["reason"], r["priority"], r["source"]), plate
        assert expires == (r["expires_at"] or None), plate
        if plate != "GJ18CD5678":  # kept active in the mock for a second critical anchor
            assert (notes, active) == (r["notes"], r["is_active"] != "false"), plate
    assert all(m[2].startswith("DEMO") for m in mock)
    assert "Ramesh" not in src and "cheating case" not in src
