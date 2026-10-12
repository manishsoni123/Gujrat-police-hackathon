"""Playback time handling (CONTRACT §5.16, §8.5): clips, "play at" and segment URLs at a recorded timespan's start.

MediaMTX v1.20.1 lists timespan starts with microseconds (they come from the `%f` in the recording file names) and its
`/get` serves from the recording file whose start is <= the requested start. The API used to truncate starts to the
millisecond, so a request at a timespan's start (`.355768` -> `.355`) landed 768 us inside the gap before it: MediaMTX
picked the file before the gap, wrote no parts and answered 404 "no recording segments found" (POST /clips -> 502,
every segment `url` of GET /recordings -> 404). Measured on the running stack: `.355Z` 404, `.355767Z` 404,
`.355768Z` 200, `.356Z` 200. DB-free."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.api import clips as clips_api
from app.api import recordings as rec_api
from app.api.recordings import BOUNDARY_TOLERANCE, _segments, covering_segment
from app.core.errors import ApiError
from app.core.tz import IST, iso_z, parse_iso
from app.schemas.analytics import ClipCreate
from app.services import mediamtx_client as mc

UTC = timezone.utc
PATH = "cam_1"

# `GET mediamtx:9996/list?path=cam_1` on the running stack (12 Oct): each entry is a run of contiguous 1-minute
# files; a new entry starts after a gap (source reconnect, reader kick).
RAW = [
    {"start": "2026-10-12T07:01:26.313179Z", "duration": 116.692825},  # the oldest file still on disk
    {"start": "2026-10-12T18:18:35.204648Z", "duration": 683.952954},
    {"start": "2026-10-12T18:30:02.355768Z", "duration": 614.860604},  # the timespan check 23 clipped at
    {"start": "2026-10-12T18:40:20.715256Z", "duration": 1011.636353},  # still being written
]
EDGE = datetime(2026, 10, 12, 18, 30, 2, 355768, tzinfo=UTC)


def mediamtx_get_status(files: list[tuple[datetime, float]], start: datetime) -> int:
    """Model of MediaMTX v1.20.1 `/get` (recordstore.FindSegments + segmentFMP4SeekAndMuxParts): serve from the
    latest file starting at or before `start`; 404 when there is none or `start` lies past that file's end (no
    part gets written)."""
    before = [(s, d) for s, d in files if s <= start]
    if not before:
        return 404
    s, d = max(before)
    return 200 if start < s + timedelta(seconds=d) else 404


FILES = [(parse_iso(r["start"]), r["duration"]) for r in RAW]


def url_start(url: str) -> datetime:
    return parse_iso(parse_qs(urlsplit(url).query)["start"][0])


# ---- formatting -------------------------------------------------------------------------------------------------

def test_rfc3339_floors_by_default_and_rounds_up_for_get() -> None:
    assert mc._rfc3339(EDGE) == "2026-10-12T18:30:02.355Z"  # /list window start: floor (inclusive)
    assert mc._rfc3339(EDGE, round_up=True) == "2026-10-12T18:30:02.356Z"
    whole_ms = EDGE.replace(microsecond=355000)
    assert mc._rfc3339(whole_ms) == mc._rfc3339(whole_ms, round_up=True) == "2026-10-12T18:30:02.355Z"
    assert mc._rfc3339(EDGE.replace(microsecond=0), round_up=True) == "2026-10-12T18:30:02.000Z"


def test_rfc3339_round_up_carries_over_and_renders_utc() -> None:
    assert mc._rfc3339(datetime(2026, 10, 12, 23, 59, 59, 999001, tzinfo=UTC), round_up=True) == "2026-10-13T00:00:00.000Z"
    assert mc._rfc3339(datetime(2026, 10, 13, 0, 0, 2, 355768, tzinfo=IST), round_up=True) == "2026-10-12T18:30:02.356Z"
    assert mc._rfc3339(datetime(2026, 10, 12, 18, 30, 2, 355768), round_up=True) == "2026-10-12T18:30:02.356Z"  # naive = UTC


def test_round_up_is_never_earlier_and_less_than_a_millisecond_later() -> None:
    for us in range(0, 1_000_000, 997):
        t = EDGE.replace(microsecond=us)
        got = parse_iso(mc._rfc3339(t, round_up=True))
        assert t <= got < t + timedelta(milliseconds=1), (t, got)
        assert parse_iso(mc._rfc3339(t)) <= t  # floor stays a lower bound


def test_get_urls_round_the_start_up() -> None:
    internal = mc.playback_get_url(PATH, EDGE, 10)
    assert internal.startswith(mc.settings.MEDIAMTX_PLAYBACK_URL.rstrip("/") + "/get?")
    assert "start=2026-10-12T18:30:02.356Z&duration=10&format=mp4" in internal
    assert mc.public_playback_url(PATH, EDGE, 614.86) == "/playback/get?path=cam_1&start=2026-10-12T18:30:02.356Z&duration=614&format=mp4"


# ---- segment list -----------------------------------------------------------------------------------------------

def test_each_segment_url_plays_its_own_timespan() -> None:
    segs = _segments(RAW, PATH)
    assert [s["start"] for s in segs] == [iso_z(parse_iso(r["start"])) for r in RAW]  # rendering unchanged (ms)
    assert segs[2]["start"] == "2026-10-12T18:30:02.355Z" and segs[2]["_start"] == EDGE
    for seg in segs:
        got = url_start(seg["url"])
        assert seg["_start"] <= got < seg["_start"] + timedelta(milliseconds=1)
        assert mediamtx_get_status(FILES, got) == 200, seg["url"]
        # the truncated start every segment URL used to carry falls before the timespan (404 on the stack)
        assert mediamtx_get_status(FILES, parse_iso(mc._rfc3339(seg["_start"]))) == 404


# ---- boundary resolution ----------------------------------------------------------------------------------------

def test_covering_segment_clamps_a_rendered_start_to_the_recorded_one() -> None:
    segs = _segments(RAW, PATH)
    echoed = parse_iso("2026-10-12T18:30:02.355Z")  # what GET /recordings renders and check 23 posts back
    seg, start = covering_segment(segs, echoed)
    assert seg is segs[2] and start == EDGE
    seg, start = covering_segment(segs, EDGE)  # the exact microsecond start
    assert seg is segs[2] and start == EDGE
    inside = EDGE + timedelta(seconds=5, microseconds=123)
    assert covering_segment(segs, inside) == (segs[2], inside)  # inside a timespan: untouched


def test_covering_segment_tolerance_gap_and_end() -> None:
    segs = _segments(RAW, PATH)
    near = EDGE - BOUNDARY_TOLERANCE + timedelta(microseconds=1)
    assert covering_segment(segs, near) == (segs[2], EDGE)
    assert covering_segment(segs, EDGE - BOUNDARY_TOLERANCE) == (segs[2], EDGE)  # inclusive bound
    in_gap = EDGE - timedelta(seconds=1.5)  # 18:29:59.157 .. 18:30:02.355 is not recorded
    assert covering_segment(segs, in_gap) == (None, in_gap)
    end_a = segs[1]["_start"] + timedelta(seconds=segs[1]["_dur"])
    assert covering_segment(segs, end_a) == (None, end_a)  # the end is exclusive
    tail_a = end_a - timedelta(milliseconds=200)
    assert covering_segment(segs, tail_a) == (segs[1], tail_a)
    assert covering_segment([], EDGE) == (None, EDGE)


def test_tail_of_a_timespan_within_tolerance_of_the_next_stays_in_the_tail() -> None:
    a = datetime(2026, 10, 12, 10, 0, 0, 100000, tzinfo=UTC)
    raw = [{"start": iso_z(a), "duration": 60.0}, {"start": (a + timedelta(seconds=60.5)).isoformat(), "duration": 60.0}]
    segs = _segments(raw, PATH)
    t = a + timedelta(seconds=59.8)  # inside A and 0.7 s before B
    assert covering_segment(segs, t) == (segs[0], t)


def test_every_timespan_start_round_trips_to_a_served_request() -> None:
    """The check-23 flow for every timespan: render -> client echoes -> resolve -> /get request served."""
    segs = _segments(RAW, PATH)
    for seg in segs:
        seg_found, start = covering_segment(segs, parse_iso(seg["start"]))
        assert seg_found is seg
        assert mediamtx_get_status(FILES, url_start(mc.playback_get_url(PATH, start, 10))) == 200


# ---- handlers (MediaMTX and the DB faked out) ---------------------------------------------------------------------

class _Stop(Exception):
    pass


def _cam() -> SimpleNamespace:
    return SimpleNamespace(id=1, codec="H264", meta=None, rtsp_url="rtsp://mediamtx:8554/src_1", record_enabled=True)


def _fake_list(calls: list):
    async def fake(path, start, end):
        calls.append((path, start, end))
        return RAW

    return fake


async def test_clip_at_the_rendered_segment_start_requests_the_timespan(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[str] = []

    async def fake_scoped_camera(db, scope, camera_id, include_retired=False):
        return _cam()

    async def fake_download(path, start, duration_s):
        requested.append(mc.playback_get_url(path, start, duration_s))
        raise _Stop  # stop before the DB insert
        yield b""  # pragma: no cover

    monkeypatch.setattr(clips_api, "scoped_camera", fake_scoped_camera)
    monkeypatch.setattr(clips_api, "playback_list", _fake_list([]))
    monkeypatch.setattr(clips_api, "playback_download", fake_download)
    user = SimpleNamespace(id=1, role="operator", department_id=None, district=None)
    with pytest.raises(_Stop):
        await clips_api.create_clip(ClipCreate(camera_id=1, start_at="2026-10-12T18:30:02.355Z", duration_s=10), user, None, None)
    assert len(requested) == 1 and "start=2026-10-12T18:30:02.356Z&duration=10&format=mp4" in requested[0]
    assert mediamtx_get_status(FILES, url_start(requested[0])) == 200

    # documented behaviour kept: a start in the gap (more than 1 s before the timespan) is a 409, not a 502
    with pytest.raises(ApiError) as exc:
        await clips_api.create_clip(ClipCreate(camera_id=1, start_at="2026-10-12T18:30:00.500Z", duration_s=10), user, None, None)
    assert exc.value.status == 409 and len(requested) == 1


async def test_play_at_a_boundary_or_in_a_gap_points_into_the_timespan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rec_api, "playback_list", _fake_list([]))
    # at - before_s = the rendered timespan start -> clamped to the recorded start
    res = await rec_api.resolve_play(_cam(), parse_iso("2026-10-12T18:30:12.355Z"), 10, 30)
    assert res["available"] is True and res["start"] == "2026-10-12T18:30:02.355Z"
    assert "start=2026-10-12T18:30:02.356Z&duration=30" in res["url"]
    assert mediamtx_get_status(FILES, url_start(res["url"])) == 200
    # at - before_s inside the gap -> jumps to the next timespan, whose start is also rounded up
    res = await rec_api.resolve_play(_cam(), parse_iso("2026-10-12T18:30:10.000Z"), 10, 30)
    assert res["available"] is True and url_start(res["url"]) == parse_iso("2026-10-12T18:30:02.356Z")
    assert mediamtx_get_status(FILES, url_start(res["url"])) == 200
