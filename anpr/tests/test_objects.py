"""Tracker, per-minute de-duplicated counts and intrusion zones (no model needed)."""
from datetime import datetime, timedelta, timezone

from anpr.objects import CentroidTracker, Detection, ObjectCounter, Zone

T0 = datetime(2026, 9, 4, 10, 14, 30, tzinfo=timezone.utc)


def det(cls, x1, y1, x2, y2, conf=0.9):
    return Detection(cls, conf, x1, y1, x2, y2)


def test_tracker_keeps_id_across_rounds_and_drops_lost():
    tr = CentroidTracker(iou_thr=0.3, max_missed=2)
    a = tr.update([det("car", 100, 100, 200, 160)], T0)[0]
    b = tr.update([det("car", 110, 102, 210, 162)], T0 + timedelta(seconds=1))[0]
    assert a.id == b.id
    tr.update([], T0 + timedelta(seconds=2))
    tr.update([], T0 + timedelta(seconds=3))
    tr.update([], T0 + timedelta(seconds=4))
    assert a.id not in tr.tracks
    c = tr.update([det("car", 100, 100, 200, 160)], T0 + timedelta(seconds=5))[0]
    assert c.id != a.id


def test_parked_car_counts_once_per_minute():
    counter = ObjectCounter(camera_id=1)
    for i in range(30):
        finished, events = counter.update([det("car", 100, 100, 200, 160), det("person", 500, 300, 540, 400)], T0 + timedelta(seconds=i), (540, 960))
        assert finished == [] and events == []
    live = counter.live_counts()
    assert live["counts"] == {"car": 1, "person": 1}
    assert live["minute"] == "2026-09-04T10:14:00.000Z"
    finished, _ = counter.update([det("car", 100, 100, 200, 160)], T0 + timedelta(seconds=31), (540, 960))
    assert sorted(finished, key=lambda r: r["class"]) == [
        {"minute": "2026-09-04T10:14:00.000Z", "class": "car", "count": 1},
        {"minute": "2026-09-04T10:14:00.000Z", "class": "person", "count": 1},
    ]
    assert counter.live_counts()["minute"] == "2026-09-04T10:15:00.000Z"


def test_zone_dwell_fires_once():
    counter = ObjectCounter(camera_id=1)
    counter.set_zones([{"id": 7, "name": "Gate apron", "polygon": [[0.1, 0.5], [0.9, 0.5], [0.9, 0.95], [0.1, 0.95]],
                        "active_from": None, "active_to": None, "classes": ["person"], "dwell_s": 2.0, "priority": "medium"}])
    events_all = []
    for i in range(6):
        _, events = counter.update([det("person", 400, 350, 440, 450), det("car", 400, 350, 600, 450)], T0 + timedelta(seconds=i * 0.5), (540, 960))
        events_all += events
    assert len(events_all) == 1
    ev = events_all[0]
    assert ev["type"] == "intrusion" and ev["zone_id"] == 7 and ev["class"] == "person" and ev["dwell_s"] >= 2.0
    assert "Gate apron" in ev["note"]


def test_zone_active_window_in_ist():
    z = Zone.from_config({"id": 1, "name": "n", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]], "active_from": "22:00",
                          "active_to": "06:00", "classes": [], "dwell_s": 2, "priority": "low"})
    assert z.is_active(datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc))      # 01:30 IST
    assert not z.is_active(datetime(2026, 9, 4, 6, 0, tzinfo=timezone.utc))   # 11:30 IST
    assert z.contains(10, 10, 100, 100)
