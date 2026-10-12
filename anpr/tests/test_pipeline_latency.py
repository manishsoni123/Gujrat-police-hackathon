"""Alert latency of the worker loop: when an accepted read leaves the worker.

``alerts.latency_ms`` = alert ``created_at`` - read ``captured_at`` (CONTRACT section 5.12). The
worker part of it is: track OCR -> vote window -> accept -> batch POST. These tests pin the two
loop changes of 13 Oct 2026 without touching what is voted:

* a batch holding a valid-format (alertable) read is posted in the tick that accepts it instead
  of waiting for the next ``POST_BATCH_S`` period, with everything else pending for the camera;
  a batch without one keeps the periodic tick;
* a camera's vote expiry also runs right after its own frame (``Pipeline._after_frame``), so a
  read does not wait for the other cameras' frames - but a window is never closed early.

DB-free and model-free: the Pipeline is assembled without detector / OCR / decoder threads.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from anpr.config import Settings
from anpr.pipeline import CameraConfig, CameraWorker, Pipeline
from anpr.sightings import SightingTracker
from anpr.voting import Candidate, Voter

VALID = "GJ01AB1234"
INVALID = "GJ05RS"          # no digits after the series: never a registration, never alertable


class FakeSender:
    def __init__(self) -> None:
        self.jobs: list = []

    def submit(self, job) -> None:
        self.jobs.append(job)

    def submit_latest(self, key, job) -> None:  # snapshots / overlay - not under test
        pass


class FakeClient:
    def __init__(self) -> None:
        self.posted: list[tuple[dict, dict]] = []

    def post_detections(self, payload, files):
        self.posted.append((payload, files))
        return {}


def make_pipeline(camera_ids=(1,), **overrides) -> Pipeline:
    settings = Settings(mode="live", worker_id="live-test", **overrides)
    p = Pipeline.__new__(Pipeline)
    p.settings = settings
    p.client = FakeClient()
    p.sender = FakeSender()
    p.objects = None
    p.dial_gate = None
    p.voter = Voter(window_s=settings.vote_window_s)
    p.sightings = SightingTracker(close_after_s=settings.sighting_close_s)
    p.cameras = {}
    p.timing = {"detect_ms": 0.0, "ocr_ms": 0.0, "objects_ms": 0.0, "frames": 0,
                "ocr_calls": 0, "object_calls": 0}
    for cid in camera_ids:
        cfg = CameraConfig(cid, str(cid), f"cam {cid}", f"/media/none/cam_{cid}.mp4")
        cam = CameraWorker(cfg, settings, p)
        # a periodic batch has just gone: the next one is POST_BATCH_S away
        cam.last_flush_mono = time.monotonic()
        p.cameras[cid] = cam
    return p


def cand(camera_id: int, plate: str, age_s: float, conf: float = 0.9,
         now: datetime | None = None) -> Candidate:
    now = now or datetime.now(timezone.utc)
    return Candidate(
        camera_id=camera_id, captured_at=now - timedelta(seconds=age_s), stream_pts=0.0,
        frame_index=0, plate_raw=plate, plate_norm=plate, is_valid_format=False, confidence=conf,
        bbox=(10, 20, 120, 30), crop_jpeg=b"\xff\xd8\xff" + plate.encode(), frame=None,
    )


def detections(p: Pipeline, camera_id: int | None = None) -> list[dict]:
    """Run the queued detection jobs (what the sender thread would do) and return their payloads."""
    for job in p.sender.jobs:
        assert job.kind == "detections"
        job.call()
    p.sender.jobs.clear()
    return [payload for payload, _files in p.client.posted
            if camera_id is None or payload["camera_id"] == camera_id]


# ---- immediate batch for alertable reads ------------------------------------------------
def test_valid_read_is_posted_in_the_tick_that_accepts_it():
    p = make_pipeline()
    cam = p.cameras[1]
    p.voter.add(cand(1, VALID, age_s=3.2))           # window (3 s) elapsed
    before = cam.last_flush_mono

    # POST_BATCH_S has NOT elapsed since the last batch
    p._tick_time_based(datetime.now(timezone.utc))

    sent = detections(p, 1)
    assert len(sent) == 1
    payload = sent[0]
    assert [r["plate_norm"] for r in payload["reads"]] == [VALID]
    assert payload["reads"][0]["crop_file"] == "crop_0"
    # the sighting travels in the same batch, so the API can link the read to it
    assert [s["key"] for s in payload["sightings"]] == [payload["reads"][0]["sighting_key"]]
    assert payload["sightings"][0]["best_crop_file"] == "crop_0"
    _, files = p.client.posted[0]
    assert files["crop_0"].startswith(b"\xff\xd8\xff")
    assert cam.pending.empty() and cam.pending.urgent is False
    assert cam.last_flush_mono > before               # the periodic timer restarts


def test_invalid_read_keeps_the_periodic_batch():
    p = make_pipeline()
    cam = p.cameras[1]
    p.voter.add(cand(1, INVALID, age_s=3.2))
    p._tick_time_based(datetime.now(timezone.utc))
    assert p.sender.jobs == []                        # not alertable: waits for POST_BATCH_S
    assert [r.plate_norm for r in cam.pending.reads] == [INVALID] and cam.pending.urgent is False

    cam.last_flush_mono -= p.settings.post_batch_s    # the period elapses
    p._tick_time_based(datetime.now(timezone.utc))
    sent = detections(p, 1)
    assert [r["plate_norm"] for r in sent[0]["reads"]] == [INVALID]


def test_pending_invalid_read_rides_with_the_next_valid_one():
    p = make_pipeline()
    p.voter.add(cand(1, INVALID, age_s=3.5))
    p._tick_time_based(datetime.now(timezone.utc))
    assert p.sender.jobs == []
    p.voter.add(cand(1, VALID, age_s=3.1))
    p._tick_time_based(datetime.now(timezone.utc))
    sent = detections(p, 1)
    assert len(sent) == 1
    assert [r["plate_norm"] for r in sent[0]["reads"]] == [INVALID, VALID]
    assert [r["crop_file"] for r in sent[0]["reads"]] == ["crop_0", "crop_1"]


def test_urgent_batch_carries_everything_pending_for_the_camera():
    # the CONTRACT 7.2 amendment of 13 Oct 2026: the immediate batch is a normal batch - it takes
    # every pending item of the camera, not just the alertable read
    p = make_pipeline()
    cam = p.cameras[1]
    p.voter.add(cand(1, INVALID, age_s=3.6))
    p._tick_time_based(datetime.now(timezone.utc))
    count = {"minute": "2026-10-13T10:00:00Z", "class": "car", "count": 3}
    cam.pending.object_counts.append(count)
    cam.pending.events.append(({"type": "loop_reset", "occurred_at": datetime.now(timezone.utc),
                                "note": "test"}, None))
    assert p.sender.jobs == []                        # nothing alertable yet

    p.voter.add(cand(1, VALID, age_s=3.2))
    p._after_frame(cam)

    sent = detections(p, 1)
    assert len(sent) == 1
    payload = sent[0]
    assert [r["plate_norm"] for r in payload["reads"]] == [INVALID, VALID]
    assert sorted(s["plate_norm"] for s in payload["sightings"]) == sorted([INVALID, VALID])
    assert payload["object_counts"] == [count]
    assert [e["type"] for e in payload["events"]] == ["loop_reset"]
    assert cam.pending.empty() and p.sightings.drain_dirty(1) == []


def test_urgent_batch_does_not_add_a_periodic_batch_within_the_period():
    p = make_pipeline()
    cam = p.cameras[1]
    cam.last_flush_mono -= 10.0                      # periodic batch due as well
    p.voter.add(cand(1, VALID, age_s=3.2))
    p._tick_time_based(datetime.now(timezone.utc))
    assert len(detections(p, 1)) == 1                 # one batch, not urgent + periodic

    p.voter.add(cand(1, INVALID, age_s=3.2))
    p._tick_time_based(datetime.now(timezone.utc))
    assert p.sender.jobs == []                        # the urgent batch restarted the period


def test_reads_below_min_conf_are_not_urgent():
    p = make_pipeline()
    p.voter.add(cand(1, VALID, age_s=3.2, conf=0.1))  # below ANPR_MIN_READ_CONF (0.30): never sent
    p._tick_time_based(datetime.now(timezone.utc))
    assert p.sender.jobs == [] and p.cameras[1].pending.empty()


# ---- per-camera tick right after the camera's frame -------------------------------------
def test_after_frame_posts_the_cameras_elapsed_read_and_leaves_other_cameras_alone():
    p = make_pipeline(camera_ids=(1, 2))
    p.voter.add(cand(1, VALID, age_s=3.3))
    p.voter.add(cand(2, "GJ27XY3456", age_s=3.3))

    p._after_frame(p.cameras[1])

    sent = detections(p)
    assert [(s["camera_id"], [r["plate_norm"] for r in s["reads"]]) for s in sent] == [(1, [VALID])]
    assert p.voter.open_buckets(1) == 0
    assert p.voter.open_buckets(2) == 1   # camera 2's frame may still add to its bucket
    p._tick_time_based(datetime.now(timezone.utc))     # the loop tick still covers every camera
    assert [[r["plate_norm"] for r in s["reads"]] for s in detections(p, 2)] == [["GJ27XY3456"]]


def test_after_frame_never_closes_a_vote_window_early():
    p = make_pipeline()
    now = datetime.now(timezone.utc)
    p.voter.add(cand(1, VALID, age_s=1.0, conf=0.8, now=now))
    p._after_frame(p.cameras[1])
    assert p.sender.jobs == [] and p.voter.open_buckets(1) == 1

    # a later OCR pass of the same plate still joins the same bucket and is voted with it
    p.voter.add(cand(1, VALID, age_s=0.5, conf=1.0, now=now))
    p._tick_camera(p.cameras[1], now + timedelta(seconds=2.5))     # 3.5 s after the first member
    p._flush_if_urgent(p.cameras[1], time.monotonic())
    sent = detections(p, 1)
    assert len(sent) == 1 and len(sent[0]["reads"]) == 1
    assert abs(sent[0]["reads"][0]["confidence"] - 0.9) < 1e-6    # mean of both members: one vote
    assert sent[0]["sightings"][0]["read_count"] == 1


def test_after_frame_handles_a_pending_discontinuity_before_expiry():
    p = make_pipeline()
    cam = p.cameras[1]
    p.voter.add(cand(1, VALID, age_s=1.0))             # window still open
    cam._on_discontinuity(1, "decoder restart (test)", None, None)

    p._after_frame(cam)   # same order as the loop tick: flush votes, close sightings

    sent = detections(p, 1)
    assert len(sent) == 1
    assert [r["plate_norm"] for r in sent[0]["reads"]] == [VALID]
    assert [e["type"] for e in sent[0]["events"]] == ["loop_reset"]
    assert sent[0]["sightings"][0]["closed"] is True


def test_process_ready_frames_sends_a_read_before_the_next_cameras_frame():
    p = make_pipeline(camera_ids=(1, 2))
    order: list[tuple[int, int]] = []

    def fake_process_frame(cam, frame):
        # what a track ending in this frame does: its best shots (captured 3.2 s ago) are
        # OCR'd and voted
        order.append((cam.cfg.id, len(p.sender.jobs)))
        p.voter.add(cand(cam.cfg.id, VALID if cam.cfg.id == 1 else "GJ27XY3456", age_s=3.2))

    p.process_frame = fake_process_frame
    for cam in p.cameras.values():
        cam._slot = object()                           # a decoded frame is waiting

    assert p._process_ready_frames() is True
    # camera 1's read was queued before camera 2's frame was processed
    assert order == [(1, 0), (2, 1)]
    sent = detections(p)
    assert [(s["camera_id"], [r["plate_norm"] for r in s["reads"]]) for s in sent] == [
        (1, [VALID]), (2, ["GJ27XY3456"])]
    assert p._process_ready_frames() is False          # no new frames


def test_process_ready_frames_survives_a_failing_frame():
    p = make_pipeline()
    p.voter.add(cand(1, VALID, age_s=3.2))

    def boom(cam, frame):
        raise RuntimeError("bad frame")

    p.process_frame = boom
    p.cameras[1]._slot = object()
    assert p._process_ready_frames() is True
    assert [[r["plate_norm"] for r in s["reads"]] for s in detections(p, 1)] == [[VALID]]
