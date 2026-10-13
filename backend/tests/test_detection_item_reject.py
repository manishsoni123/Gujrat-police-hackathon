"""POST /internal/detections rejects a bad item, not the whole batch (CONTRACT §7.2). DB-free.

Regression of 13 Oct 2026: a two-line plate read together with the burnt-in caption gave
`GJ37Z1140CAM1SACHIVALAYAGATE1LOOP00` (35 characters). `ReadIn.plate_norm` and
`SightingIn.plate_norm` cap at 16, so `DetectionBatch` failed with a 422 for the whole batch; the
worker drops a 4xx batch, so the valid `GJ37Z1140` read, its alert and the closure of unrelated
sightings were lost too.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import internal
from app.core.errors import install_exception_handlers
from app.db.models import Event, PlateRead, Sighting
from app.db.session import get_db
from app.schemas.internal import DetectionBatch
from app.services import sightings as svc
from app.services.plates import NormResult

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
LONG_RAW = "GJ 37 Z 1140 cam 1sachivalaya gate 1loop 00"
LONG_NORM = "GJ37Z1140CAM1SACHIVALAYAGATE1LOOP00"
T0 = "2026-10-13T04:13:08.000Z"
TOO_LONG = "plate_norm: String should have at most 16 characters"


def _read(plate_raw: str, plate_norm: str | None, crop: str, **kw: Any) -> dict[str, Any]:
    key_plate = plate_norm or plate_raw.replace(" ", "")
    return {"captured_at": T0, "plate_raw": plate_raw, "plate_norm": plate_norm,
            "is_valid_format": False, "confidence": 0.8, "bbox": [1, 2, 3, 4], "crop_file": crop,
            "sighting_key": f"1:{key_plate}:1760328788000", **kw}


def _sighting(plate_norm: str, closed: bool = False, **kw: Any) -> dict[str, Any]:
    return {"key": f"1:{plate_norm}:1760328788000", "plate_norm": plate_norm, "first_seen": T0,
            "last_seen": T0, "read_count": 2, "best_conf": 0.8, "closed": closed, **kw}


def _finding_batch() -> dict[str, Any]:
    """The batch seen on the stack: the caption read rides with the valid read and a closure."""
    return {
        "worker_id": "live-gpu", "mode": "live", "camera_id": 1,
        "reads": [_read(LONG_RAW, LONG_NORM, "crop_0"),
                  _read("GJ 37 Z 1140", "GJ37Z1140", "crop_1")],
        "sightings": [_sighting(LONG_NORM, closed=True), _sighting("GJ37Z1140"),
                      _sighting("GJ22EG5515", closed=True)],
        "object_counts": [{"minute": "2026-10-13T04:12:00.000Z", "class": "car", "count": 3}],
        "events": [{"type": "loop_reset", "occurred_at": T0, "stream_pts_before": 3600.0,
                    "stream_pts_after": 0.2}],
    }


# ---------------------------------------------------------------- schema: per item vs envelope


def test_long_plate_items_are_rejected_one_by_one() -> None:
    batch = DetectionBatch.model_validate(_finding_batch())
    assert [r.plate_norm for r in batch.reads] == ["GJ37Z1140"]
    assert batch.positions("reads") == [1]
    assert [s.plate_norm for s in batch.sightings] == ["GJ37Z1140", "GJ22EG5515"]
    assert batch.positions("sightings") == [1, 2]
    assert len(batch.object_counts) == 1 and len(batch.events) == 1
    assert batch.rejected_items == [
        {"index": 0, "kind": "read", "reason": TOO_LONG},
        {"index": 0, "kind": "sighting", "reason": TOO_LONG},
    ]


@pytest.mark.parametrize(("field", "item", "kind", "reason"), [
    ("reads", _read("X" * 65, None, "crop_0", sighting_key="1:X:1"), "read",
     "plate_raw: String should have at most 64 characters"),
    ("reads", _read("GJ01AB1234", "GJ01AB1234", "crop_0", confidence=1.5), "read",
     "confidence: Input should be less than or equal to 1"),
    ("reads", _read("GJ01AB1234", "GJ01AB1234", "crop_0", bbox=[1, 2, 3]), "read",
     "bbox: bbox must be [x, y, w, h]"),
    ("reads", "junk", "read", "Input should be a valid dictionary or instance of ReadIn"),
    ("sightings", _sighting("GJ01AB1234", read_count=-1), "sighting",
     "read_count: Input should be greater than or equal to 0"),
    ("object_counts", {"minute": T0, "class": "dog", "count": 1}, "object_count",
     "class: must be one of person, bicycle, car, motorcycle, bus, truck"),
    ("events", {"type": "bogus", "occurred_at": T0}, "event",
     "type: must be loop_reset or intrusion"),
])
def test_invalid_item_is_rejected_and_its_neighbours_kept(field: str, item: Any, kind: str,
                                                          reason: str) -> None:
    good = _finding_batch()[field][-1]
    batch = DetectionBatch.model_validate({"camera_id": 1, field: [good, item, good]})
    assert len(getattr(batch, field)) == 2
    assert batch.positions(field) == [0, 2]
    assert batch.rejected_items == [{"index": 1, "kind": kind, "reason": reason}]


def test_valid_batch_has_no_rejections_and_identity_positions() -> None:
    body = _finding_batch()
    body["reads"] = body["reads"][1:]
    body["sightings"] = body["sightings"][1:]
    batch = DetectionBatch.model_validate(body)
    assert batch.rejected_items == []
    assert batch.positions("reads") == [0] and batch.positions("sightings") == [0, 1]
    assert batch.positions("events") == [0]
    # built without a payload dict: positions still line up with the lists
    direct = DetectionBatch(camera_id=1, reads=batch.reads)
    assert direct.positions("reads") == [0] and direct.positions("sightings") == []


@pytest.mark.parametrize("envelope", [
    {"camera_id": None},
    {"camera_id": "abc"},
    {"mode": "replay"},
    {"worker_id": "w" * 65},
    {"reads": "not-a-list"},
    {"sightings": {"key": "x"}},
    {"live_counts": {"minute": T0, "counts": {"car": "many"}}},
])
def test_bad_envelope_still_fails_the_whole_batch(envelope: dict[str, Any]) -> None:
    body = {**_finding_batch(), **envelope}
    if envelope.get("camera_id", 0) is None:
        body.pop("camera_id")
    with pytest.raises(ValidationError):
        DetectionBatch.model_validate(body)


# ---------------------------------------------------------------- ingest with a fake session


class _Result:
    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return []

    def scalar_one_or_none(self) -> None:
        return None


class FakeDb:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.objects: dict[tuple[type, int], Any] = {}
        self.statements: list[Any] = []
        self.commits = 0
        self._next_id = 100

    async def execute(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        return _Result()

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                self._next_id += 1
                obj.id = self._next_id

    async def get(self, model: type, ident: int) -> Any:
        return self.objects.get((model, ident))

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture()
def fake_ingest(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Patch every DB/media/WS side effect of `ingest_batch`; record the stored rows."""
    cam = SimpleNamespace(id=1, department_id=None, district=None, status="online")
    rec = SimpleNamespace(cam=cam, reads=[], events=[], files=[], broadcasts=[])

    async def get_camera(db: Any, camera_id: int) -> Any:
        assert camera_id == 1
        return cam

    async def noop(*_a: Any, **_k: Any) -> None:
        return None

    async def process_read(*_a: Any, **_k: Any) -> tuple[None, bool]:
        return None, False

    async def insert_read(db: FakeDb, values: dict[str, Any]) -> int:
        rid = len(rec.reads) + 1
        rec.reads.append(values)
        db.objects[(PlateRead, rid)] = PlateRead(id=rid, **values)
        return rid

    async def insert_event(db: FakeDb, values: dict[str, Any]) -> int:
        eid = len(rec.events) + 1
        rec.events.append(values)
        db.objects[(Event, eid)] = Event(id=eid, **values)
        return eid

    monkeypatch.setattr(svc, "get_camera_for_ingest", get_camera)
    monkeypatch.setattr(svc.serializers, "warm", noop)
    monkeypatch.setattr(svc.matcher, "maybe_reload", noop)
    monkeypatch.setattr(svc.matcher, "process_read", process_read)
    monkeypatch.setattr(svc, "_insert_read_once", insert_read)
    monkeypatch.setattr(svc, "_insert_event_once", insert_event)
    def write_file(rel: str, data: bytes) -> tuple[str, int]:
        rec.files.append(rel)
        return "0" * 64, len(data)

    monkeypatch.setattr(svc, "write_bytes_hashed", write_file)
    monkeypatch.setattr(svc.serializers, "read_item", lambda r, *_a: {"plate": r.plate_norm})
    monkeypatch.setattr(svc.serializers, "sighting_item", lambda s, *_a: {"plate": s.plate_norm})
    monkeypatch.setattr(svc.serializers, "event_item", lambda e, *_a: {"type": e.type})
    monkeypatch.setattr(svc.manager, "broadcast", lambda *a: rec.broadcasts.append(a[:2]))
    return rec


def _ingest(body: dict[str, Any], files: dict[str, bytes]) -> tuple[dict[str, Any], FakeDb]:
    db = FakeDb()
    batch = DetectionBatch.model_validate(body)
    out = asyncio.run(svc.ingest_batch(db, batch, files))  # type: ignore[arg-type]
    return out, db


def test_finding_batch_ingests_the_valid_read_and_both_closures(
        fake_ingest: SimpleNamespace) -> None:
    out, db = _ingest(_finding_batch(), {"crop_0": JPEG, "crop_1": JPEG})
    assert [r["plate_norm"] for r in fake_ingest.reads] == ["GJ37Z1140"]
    stored = {s.plate_norm: s for s in db.added if isinstance(s, Sighting)}
    assert sorted(stored) == ["GJ22EG5515", "GJ37Z1140"] and stored["GJ22EG5515"].closed is True
    assert out["accepted_reads"] == 1 and out["accepted_sightings"] == 2
    assert out["object_counts_upserted"] == 1 and out["events_created"] == 1
    assert out["rejected"] == [
        {"index": 0, "kind": "read", "reason": TOO_LONG},
        {"index": 0, "kind": "sighting", "reason": TOO_LONG},
    ]
    assert db.commits == 1


def test_read_normalising_past_16_chars_is_rejected_not_truncated(
        fake_ingest: SimpleNamespace) -> None:
    # the worker left plate_norm empty: the API's own §3.4 result is 35 characters
    body = {"camera_id": 1, "reads": [_read(LONG_RAW, None, "crop_0"),
                                      _read("GJ 37 Z 1140", "GJ37Z1140", "crop_1")]}
    out, _ = _ingest(body, {"crop_0": JPEG, "crop_1": JPEG})
    # never stored cut down to GJ37Z1140CAM1SAC, a plate nobody saw
    assert [r["plate_norm"] for r in fake_ingest.reads] == ["GJ37Z1140"]
    assert out["accepted_reads"] == 1
    reason = "plate_norm has 35 characters after normalisation (max 16)"
    assert out["rejected"] == [{"index": 0, "kind": "read", "reason": reason}]
    assert len(fake_ingest.files) == 1  # no crop written for the rejected read


def test_sighting_normalising_past_16_chars_is_rejected_not_truncated(
        fake_ingest: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    def normalise(raw: str) -> NormResult:  # a §3.4 that would lengthen a plate
        return NormResult("A" * 17 if raw == "LONGER" else raw, False, None, 0)

    monkeypatch.setattr(svc, "normalise", normalise)
    body = {"camera_id": 1, "sightings": [_sighting("LONGER"), _sighting("GJ01AB1234")]}
    out, db = _ingest(body, {})
    assert [s.plate_norm for s in db.added if isinstance(s, Sighting)] == ["GJ01AB1234"]
    reason = "plate_norm has 17 characters after normalisation (max 16)"
    assert out["rejected"] == [{"index": 0, "kind": "sighting", "reason": reason}]


def test_later_rejections_report_payload_indices(fake_ingest: SimpleNamespace) -> None:
    good_read = _read("GJ 01 AB 1234", "GJ01AB1234", "crop_2")
    body = {
        "camera_id": 1,
        "reads": [_read("GJ01AB1234", "GJ01AB1234", "crop_0", confidence=2.0),   # schema
                  _read("GJ 01 AB 1235", "GJ01AB1235", "crop_9"),                # crop missing
                  good_read],
        "sightings": [_sighting("GJ01AB1234", best_conf=-1),
                      _sighting("GJ01AB1235", first_seen="x"),
                      _sighting("GJ01AB1234")],
        "object_counts": [{"minute": T0, "class": "dog", "count": 1},
                          {"minute": "never", "class": "car", "count": 1},
                          {"minute": T0, "class": "car", "count": 4}],
        "events": [{"type": "bogus", "occurred_at": T0},
                   {"type": "loop_reset", "occurred_at": "x"},
                   {"type": "loop_reset", "occurred_at": T0}],
    }
    out, _ = _ingest(body, {"crop_2": JPEG})
    got = sorted((r["kind"], r["index"]) for r in out["rejected"])
    assert got == [("event", 0), ("event", 1), ("object_count", 0), ("object_count", 1),
                   ("read", 0), ("read", 1), ("sighting", 0), ("sighting", 1)]
    by_key = {(r["kind"], r["index"]): r["reason"] for r in out["rejected"]}
    assert by_key[("read", 1)] == "crop_file 'crop_9' missing"
    assert by_key[("sighting", 1)] == "invalid timestamps"
    assert by_key[("object_count", 1)] == "invalid minute"
    assert by_key[("event", 1)] == "invalid occurred_at"
    assert out["accepted_reads"] == 1 and out["accepted_sightings"] == 1
    assert out["object_counts_upserted"] == 1 and out["events_created"] == 1


# ---------------------------------------------------------------- the HTTP endpoint


@pytest.fixture()
def client(fake_ingest: SimpleNamespace) -> TestClient:
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(internal.router, prefix="/api")
    for dep in internal.router.dependencies:
        app.dependency_overrides[dep.dependency] = lambda: None  # type: ignore[index]

    async def fake_db() -> Any:
        yield FakeDb()

    app.dependency_overrides[get_db] = fake_db
    return TestClient(app)


def _post(client: TestClient, body: dict[str, Any], files: dict[str, bytes]) -> Any:
    parts = {"payload": (None, json.dumps(body))}
    parts.update({name: (f"{name}.jpg", data, "image/jpeg") for name, data in files.items()})
    return client.post("/api/internal/detections", files=parts)


def test_endpoint_answers_200_with_the_long_plate_rejected(
        client: TestClient, fake_ingest: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="sentinel.internal"):
        resp = _post(client, _finding_batch(), {"crop_0": JPEG, "crop_1": JPEG})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["accepted_reads"] == 1 and data["accepted_sightings"] == 2
    assert data["rejected"] == [
        {"index": 0, "kind": "read", "reason": TOO_LONG},
        {"index": 0, "kind": "sighting", "reason": TOO_LONG},
    ]
    assert [r["plate_norm"] for r in fake_ingest.reads] == ["GJ37Z1140"]
    assert "2 item(s) failed the schema" in caplog.text and f"read[0] {TOO_LONG}" in caplog.text


def test_endpoint_keeps_422_for_a_bad_envelope(
        client: TestClient, fake_ingest: SimpleNamespace) -> None:
    body = _finding_batch()
    body.pop("camera_id")
    resp = _post(client, body, {"crop_0": JPEG, "crop_1": JPEG})
    assert resp.status_code == 422
    assert "camera_id" in resp.text
    assert fake_ingest.reads == []
