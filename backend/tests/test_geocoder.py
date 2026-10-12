"""Geocoder proxy (services/geocoder.py): Nominatim parsing, cache, 1 req/s spacing, offline 503. DB-free."""

from __future__ import annotations

import time

import httpx
import pytest

from app.core.errors import ApiError
from app.services import geocoder

SACHIVALAYA = {
    "lat": "23.2189472",
    "lon": "72.6566543",
    "display_name": "Sachivalaya, Sector 10, Gandhinagar, Gandhinagar Taluka, Gandhinagar District, Gujarat, 382010, India",
    "address": {
        "road": "Ch Road",
        "suburb": "Sector 10",
        "city": "Gandhinagar",
        "state_district": "Gandhinagar District",
        "state": "Gujarat",
        "postcode": "382010",
        "country_code": "in",
    },
}


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    geocoder.clear_cache()
    monkeypatch.setattr(geocoder, "_last_call", 0.0)
    monkeypatch.setattr(geocoder, "MIN_INTERVAL_S", 0.0)
    yield
    geocoder.transport = None
    geocoder.clear_cache()


def _mock(handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    geocoder.transport = httpx.MockTransport(wrapped)
    return seen


def test_parse_place_maps_district_and_ward():
    p = geocoder.parse_place(SACHIVALAYA)
    assert p["lat"] == 23.218947 and p["lon"] == 72.656654
    assert p["district"] == "Gandhinagar"
    assert p["ward"] == "Sector 10"
    assert p["postcode"] == "382010"


def test_parse_place_canonicalises_variant_district():
    raw = {**SACHIVALAYA, "address": {"state_district": "Kachchh District"}}
    assert geocoder.parse_place(raw)["district"] == "Kutch"


async def test_search_sends_policy_headers_and_caches():
    seen = _mock(lambda r: httpx.Response(200, json=[SACHIVALAYA]))
    first = await geocoder.search("Sachivalaya  Gandhinagar")
    again = await geocoder.search("sachivalaya gandhinagar")
    assert first == again and first[0]["district"] == "Gandhinagar"
    assert len(seen) == 1
    req = seen[0]
    assert req.url.path == "/search"
    assert req.url.params["countrycodes"] == "in"
    assert req.url.params["format"] == "jsonv2"
    assert req.headers["User-Agent"].startswith("SentinelGujarat")


async def test_search_drops_duplicate_places():
    twin = {**SACHIVALAYA, "lat": "23.2189500"}
    _mock(lambda r: httpx.Response(200, json=[SACHIVALAYA, twin]))
    assert len(await geocoder.search("Sachivalaya")) == 1


async def test_reverse_caches_rounded_coordinates():
    seen = _mock(lambda r: httpx.Response(200, json=SACHIVALAYA))
    a = await geocoder.reverse(23.2189472, 72.6566543)
    b = await geocoder.reverse(23.2189470, 72.6566540)
    assert a == b and a is not None and a["ward"] == "Sector 10"
    assert len(seen) == 1


async def test_reverse_nothing_found_returns_none():
    _mock(lambda r: httpx.Response(200, json={"error": "Unable to geocode"}))
    assert await geocoder.reverse(21.0, 68.0) is None


async def test_calls_are_spaced(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(geocoder, "MIN_INTERVAL_S", 0.3)
    _mock(lambda r: httpx.Response(200, json=[]))
    t0 = time.monotonic()
    await geocoder.search("first place")
    await geocoder.search("second place")
    assert time.monotonic() - t0 >= 0.3


async def test_network_failure_is_503():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    _mock(boom)
    with pytest.raises(ApiError) as e:
        await geocoder.search("Sachivalaya")
    assert e.value.status == 503 and e.value.code == "geocoder_unavailable"


async def test_empty_url_disables(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(geocoder.cfg._cache, "geocoder.url", "")
    with pytest.raises(ApiError) as e:
        await geocoder.search("Sachivalaya")
    assert e.value.status == 503
