"""Sightings: open on first read, extend, close after silence, new key after closure."""
from datetime import datetime, timedelta, timezone

from anpr.sightings import SightingTracker, epoch_ms, make_key
from anpr.voting import VotedRead

T0 = datetime(2026, 9, 4, 10, 15, 28, tzinfo=timezone.utc)


def read(camera, plate, conf, dt_s, votes=3):
    t = T0 + timedelta(seconds=dt_s)
    return VotedRead(
        camera_id=camera, captured_at=t, stream_pts=dt_s, frame_index=int(dt_s * 5),
        plate_raw=plate, plate_norm=plate, is_valid_format=True, confidence=conf,
        bbox=(1, 2, 3, 4), crop_jpeg=b"c" + str(dt_s).encode(), frame=None, votes=votes,
        first_seen=t, last_seen=t + timedelta(seconds=2),
    )


def test_key_format():
    assert make_key(12, "GJ01AB1234", T0) == f"12:GJ01AB1234:{epoch_ms(T0)}"
    assert epoch_ms(T0) == 1788516928000


def test_open_extend_close():
    tr = SightingTracker(close_after_s=15)
    r1 = read(12, "GJ01AB1234", 0.9, 0)
    s = tr.add_read(r1, "crop_0", b"frame1")
    assert r1.sighting_key == s.key
    assert s.read_count == 1 and s.best_changed and s.best_crop_field == "crop_0"

    dirty = tr.drain_dirty(12)
    assert dirty == [s]
    tr.finalize_batch(dirty)
    assert s.best_changed is False and s.best_crop_field is None

    r2 = read(12, "GJ01AB1234", 0.95, 3)
    s2 = tr.add_read(r2, "crop_0", b"frame2")
    assert s2 is s and s.read_count == 2 and s.best_conf == 0.95
    assert s.best_changed and s.best_frame_jpeg == b"frame2"
    assert s.last_seen == r2.last_seen

    r3 = read(12, "GJ01AB1234", 0.5, 6)
    tr.add_read(r3, "crop_1", None)
    tr.drain_dirty(12)
    tr.finalize_batch([s])
    assert s.best_changed is False        # lower confidence does not replace the best

    assert tr.expire(T0 + timedelta(seconds=20)) == []        # last_seen = 8 s -> silence 12 s
    closed = tr.expire(T0 + timedelta(seconds=23.1))
    assert closed == [s] and s.closed is True
    assert tr.open_count() == 0
    assert [x.key for x in tr.drain_dirty(12)] == [s.key]


def test_new_key_after_closure():
    tr = SightingTracker(close_after_s=15)
    s1 = tr.add_read(read(5, "22BH4321AA", 0.8, 0), "crop_0", None)
    tr.close_all(5)
    s2 = tr.add_read(read(5, "22BH4321AA", 0.8, 90), "crop_0", None)
    assert s1.key != s2.key and s1.closed and not s2.closed


def test_close_all_is_per_camera():
    tr = SightingTracker()
    tr.add_read(read(1, "GJ01AB1234", 0.8, 0), "crop_0", None)
    tr.add_read(read(2, "GJ01AB1234", 0.8, 0), "crop_0", None)
    closed = tr.close_all(1)
    assert len(closed) == 1 and closed[0].camera_id == 1
    assert tr.open_count() == 1


def test_payload_shape():
    tr = SightingTracker()
    s = tr.add_read(read(12, "GJ01AB1234", 0.9, 0), "crop_0", b"f")
    p = s.to_payload("frame_0")
    assert set(p) == {"key", "plate_norm", "is_valid_format", "first_seen", "last_seen", "read_count",
                      "best_conf", "best_read_captured_at", "best_crop_file", "frame_file", "closed"}
    assert p["first_seen"] == "2026-09-04T10:15:28.000Z"
    assert p["frame_file"] == "frame_0" and p["best_crop_file"] == "crop_0"
