"""Live overlay feed: normalised boxes, track labels, watched-camera gating and the poll cadence."""
from datetime import datetime, timezone

from anpr.detector import Box
from anpr.objects import Detection
from anpr.overlay import MAX_PLATES, OverlayFeed, overlay_frame, track_label
from anpr.tracks import Track

T0 = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)


def _track(tid, box, reads=()):
    t = Track(id=tid, camera_id=7, box=box, first_seen=T0, last_seen=T0, last_pts=0.0)
    t.reads = list(reads)
    return t


def test_boxes_are_normalised_and_clamped():
    plate = _track(3, Box(960, 540, 192, 54, 0.81, "onnx"), ["GJ01WC0824", "GJ01WC0824", "GJ01VC0824"])
    edge = _track(4, Box(1900, 1060, 40, 40, 0.5, "onnx"))           # runs past the 1920x1080 frame
    car = Detection("car", 0.9, 100.0, 200.0, 580.0, 740.0)
    f = overlay_frame(7, "2026-10-12T10:00:00.000Z", (1080, 1920, 3), [plate, edge], [car], "2026-10-12T09:59:59.500Z")
    assert f["frame_w"] == 1920 and f["frame_h"] == 1080
    assert f["plates"][0] == {"box": [0.5, 0.5, 0.1, 0.05], "conf": 0.81, "label": "GJ01WC0824", "track": 3}
    x, y, w, h = f["plates"][1]["box"]
    assert x + w <= 1.0 and y + h <= 1.0 and f["plates"][1]["label"] is None
    assert f["objects"] == [{"box": [0.0521, 0.1852, 0.25, 0.5], "conf": 0.9, "cls": "car"}]
    assert f["objects_at"] == "2026-10-12T09:59:59.500Z"


def test_objects_at_is_null_without_objects_and_plates_are_capped():
    tracks = [_track(i, Box(10 * i, 10, 50, 20, 0.6, "onnx")) for i in range(MAX_PLATES + 5)]
    f = overlay_frame(7, "t", (720, 1280), tracks, [], "2026-10-12T09:59:59.500Z")
    assert len(f["plates"]) == MAX_PLATES and f["objects"] == [] and f["objects_at"] is None


def test_track_label_ignores_invalid_reads():
    assert track_label([]) is None
    assert track_label(["8G3O1NCO824", "FSJO1NCO824"]) is None
    assert track_label(["GJ01AB1", "GJ01AB1234"]) == "GJ01AB1234"


def test_feed_only_keeps_watched_cameras_latest_frame():
    feed = OverlayFeed(enabled=True, poll_s=2.0)
    feed.offer({"camera_id": 1, "n": 1})
    assert feed.take() == []                                   # nobody watching yet
    feed.done([1, 2], now=0.0)
    assert feed.watched(1) and not feed.watched(3)
    feed.offer({"camera_id": 1, "n": 1})
    feed.offer({"camera_id": 1, "n": 2})                       # latest wins
    feed.offer({"camera_id": 3, "n": 9})                       # not watched
    assert feed.take() == [{"camera_id": 1, "n": 2}]


def test_due_polls_every_poll_s_and_never_twice_in_flight():
    feed = OverlayFeed(enabled=True, poll_s=2.0)
    feed.done([], now=100.0)
    assert not feed.due(now=101.0)                             # idle, poll not due
    assert feed.due(now=102.5)                                 # poll due
    assert not feed.due(now=102.6)                             # one request in flight
    feed.done([5], now=103.0)
    feed.offer({"camera_id": 5})
    assert feed.due(now=103.1)                                 # frames waiting: send now
    feed.done(None, now=103.5)                                 # failed call keeps the watched set
    assert feed.watched(5)
    feed.done([], now=104.0)                                   # viewer left: pending frames dropped
    assert not feed.watched(5) and feed.take() == []


def test_disabled_feed_never_sends():
    feed = OverlayFeed(enabled=False)
    assert not feed.due(now=1e9)
    on = OverlayFeed(enabled=True)
    on.done([1], now=0.0)
    on.disable()
    assert not on.watched(1) and not on.due(now=1e9)
