"""Tolerant catalogue mapping (CONTRACT §6.1, §6.3) and WebSocket envelope/scoping helpers (§9)."""

from __future__ import annotations

import json

import pytest

from app.core.rbac import Scope
from app.schemas.cameras import CameraImportRow
from app.services.sandbox_catalogue import CatalogueError, map_item, unwrap
from app.services.settings_service import DEFAULT_FIELD_MAP
from app.services.ws_manager import Connection, WsManager

ORGANISER_ITEM = {
    "id": 3, "name": "Civil Hospital Gandhinagar OPD Gate", "department": "Health", "district": "Gandhinagar", "type": "bullet",
    "location": {"lat": 23.2275, "lon": 72.6485, "address": "Civil Hospital, Gandhinagar"}, "codec": "H264", "resolution": "1280x720", "fps": 10,
    "live": True, "rtsp_url": "rtsp://mediamtx:8554/stream/3", "whep_url": "http://mediamtx:8889/stream/3/whep", "hls_url": "http://mediamtx:8888/stream/3/index.m3u8",
}


def test_unwrap_shapes() -> None:
    assert unwrap([ORGANISER_ITEM]) == [ORGANISER_ITEM]
    assert unwrap({"cameras": [ORGANISER_ITEM]}) == [ORGANISER_ITEM]
    assert unwrap({"meta": {}, "data": [ORGANISER_ITEM]}) == [ORGANISER_ITEM]
    assert unwrap({"count": 1, "streams": [ORGANISER_ITEM]}) == [ORGANISER_ITEM]
    assert unwrap({"whatever": [ORGANISER_ITEM]}) == [ORGANISER_ITEM]  # any list of objects
    with pytest.raises(CatalogueError):
        unwrap({"count": 0})
    with pytest.raises(CatalogueError):
        unwrap("nope")


def test_map_mock_item_is_fully_mapped() -> None:
    row, unmapped = map_item(ORGANISER_ITEM, DEFAULT_FIELD_MAP)
    assert unmapped == []
    assert row["external_id"] == "3" and row["lat"] == 23.2275 and row["lon"] == 72.6485
    assert row["department_code"] == "Health" and row["address"] == "Civil Hospital, Gandhinagar"
    parsed = CameraImportRow.model_validate(row)
    assert parsed.codec == "H264" and parsed.resolution == "1280x720" and parsed.live is True and parsed.type == "bullet"


def test_map_variant_shape_and_unmapped_fields() -> None:
    item = {
        "camera_id": "GNR-07", "camera_name": "Sector 7 PS", "dept": "Gujarat Police", "city": "gandhinagar",
        "gps": {"lat": "23.2260", "lng": "72.6450"}, "stream_properties": {"codec": "hevc", "resolution": {"width": 1920, "height": 1080}, "fps": "25", "bitrate": 4000},
        "streams": {"rtsp": "rtsp://10.0.0.5:8554/live/7", "hls": "http://10.0.0.5/live/7.m3u8"}, "status": {"live": "false"}, "vendor": "Hikvision",
    }
    row, unmapped = map_item(item, DEFAULT_FIELD_MAP)
    assert row["external_id"] == "GNR-07" and row["name"] == "Sector 7 PS" and row["department_code"] == "Gujarat Police"
    assert row["rtsp_url"].startswith("rtsp://") and row["hls_url"].endswith(".m3u8") and row["live"] == "false"
    assert sorted(unmapped) == ["stream_properties.bitrate", "vendor"]
    parsed = CameraImportRow.model_validate(row)
    assert parsed.codec == "H265" and parsed.resolution == "1920x1080" and parsed.fps == 25 and parsed.live is False
    assert parsed.district == "Gandhinagar" and parsed.lat == 23.226


def test_map_missing_fields_are_tolerated() -> None:
    row, _ = map_item({"id": 9, "name": "Bare"}, DEFAULT_FIELD_MAP)
    parsed = CameraImportRow.model_validate(row)
    assert parsed.codec is None and parsed.live is None and parsed.lat is None and parsed.external_id == "9"


class _FakeWs:
    pass


def _conn(channel: str, scope: Scope) -> Connection:
    return Connection(ws=_FakeWs(), channel=channel, username="u", role="dept_admin" if not scope.unrestricted else "admin", scope=scope)


def test_ws_envelope_and_scoped_fanout() -> None:
    m = WsManager()
    admin = _conn("alerts", Scope())
    police = _conn("alerts", Scope(department_id=2))
    health = _conn("health", Scope())
    for c in (admin, police, health):
        m._conns.add(c)
    m.broadcast("alerts", "alert", {"id": 1}, camera_dept=3, camera_district="Surat")
    assert admin.queue.qsize() == 1 and police.queue.qsize() == 0 and health.queue.qsize() == 0
    m.broadcast("alerts", "alert", {"id": 2}, camera_dept=2, camera_district="Surat")
    assert police.queue.qsize() == 1
    env = json.loads(admin.queue.get_nowait())
    assert env["type"] == "alert" and env["data"] == {"id": 1} and env["ts"].endswith("Z")
    m.broadcast("health", "anpr_status", {"worker_id": "x"}, scoped=False)
    assert health.queue.qsize() == 1
    m.send(police, "pong", {})
    assert json.loads(police.queue.get_nowait())["type"] == "alert"
    assert json.loads(police.queue.get_nowait())["type"] == "pong"


def test_ws_slow_client_drops_oldest() -> None:
    m = WsManager()
    c = _conn("alerts", Scope())
    m._conns.add(c)
    for i in range(250):
        m.broadcast("alerts", "alert", {"i": i})
    assert c.queue.qsize() == 200 and c.dropped == 50
    assert json.loads(c.queue.get_nowait())["data"]["i"] == 50
