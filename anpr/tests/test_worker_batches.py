"""What the worker puts into a detections batch, and how the sender keeps it (13 Oct 2026 review).

* An OCR string longer than any registration (a plate read together with the burnt-in caption)
  is dropped before voting; nothing longer than the API schema allows (``plate_norm`` 16,
  ``plate_raw`` 64) is ever sent - one such read got the whole batch rejected with a 422.
* ``live_counts``-only batches are "latest wins" without retry, and a full sender queue drops
  jobs without reads / sightings / events first, so an API outage cannot push read batches out.
  A camera's detections batch (newer live counts) supersedes its pending live-counts-only job,
  so the broadcast counts never go backwards.
* Every best shot keeps the frame it was cut from, so a sighting's frame shows the plate at the
  read's ``captured_at`` - not a frame after the vehicle has gone, and not none at all.

DB-free and model-free: the Pipeline is assembled without detector / OCR models or decoders.
"""
from __future__ import annotations

import gc
import time
import weakref
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
import pytest

from anpr.client import Job, RetryableError, Sender, iso_utc
from anpr.decode import Frame
from anpr.detector import Box
from anpr.evidence import EvidenceStore
from anpr.ocr import OcrResult
from anpr.tests.test_pipeline_latency import VALID, cand, detections, make_pipeline
from anpr.tracks import PlateTracker

T0 = datetime(2026, 10, 13, 4, 13, tzinfo=timezone.utc)
CAPTION = "GJ 37 Z 1140 cam 1sachivalaya gate 1loop 00"   # seen on cam 1 (13 Oct)
API_PLATE_NORM_MAX = 16       # backend/app/schemas/internal.py ReadIn / SightingIn.plate_norm
API_PLATE_RAW_MAX = 64        # ReadIn.plate_raw


class FakeOcr:
    name = "fake"

    def __init__(self, read) -> None:
        self._read = read

    def read(self, crop: np.ndarray) -> OcrResult:
        raw, conf = self._read(crop)
        return OcrResult(raw=raw, lines=raw.split("\n"), confidence=conf, backend="fake")


def fixed_ocr(raw: str, conf: float = 0.95) -> FakeOcr:
    return FakeOcr(lambda _crop: (raw, conf))


def ocr_once(p, raw: str):
    cam = p.cameras[1]
    p.ocr = fixed_ocr(raw)
    crop = np.full((30, 120, 3), 200, np.uint8)
    return p._ocr_crop(cam, crop, Box(10, 20, 120, 30, 0.9, "contour"), T0, 0.0, 0, None)


# ---- finding 1: over-long OCR strings ----------------------------------------------------
def test_ocr_string_with_the_caption_is_dropped_before_voting():
    p = make_pipeline()
    assert ocr_once(p, CAPTION) is None
    assert p.voter.open_buckets(1) == 0
    assert p.cameras[1].suppression()["too_long"] == 1
    assert p.cameras[1].suppression()["no_digit"] == 0


@pytest.mark.parametrize("raw, kept", [
    ("DL 10 CAB 1234", True),        # 11 chars: two district digits + three series letters
    ("GJ01AB12345", True),           # a plate plus one stray glyph still votes with the plate
    ("GJ01AB123456", False),         # 12: no registration is that long
    ("GJ 01 AB 1234" + " ." * 30, False),   # plate_raw over the API's 64 characters
])
def test_ocr_length_bound(raw, kept):
    p = make_pipeline()
    assert (ocr_once(p, raw) is not None) is kept
    assert p.voter.open_buckets(1) == (1 if kept else 0)
    assert p.cameras[1].suppression()["too_long"] == (0 if kept else 1)


def test_letters_only_caption_still_counts_as_text_not_as_too_long():
    p = make_pipeline()
    assert ocr_once(p, "SACHIVALAYA GATE NUMBER ONE LOOP") is None
    assert p.cameras[1].suppression()["no_digit"] == 1
    assert p.cameras[1].suppression()["too_long"] == 0


def test_over_long_read_never_reaches_a_batch_and_the_valid_read_is_sent():
    # the 13 Oct live failure: both buckets of one track OCR pass were accepted in one tick and
    # the 34-character read took the valid one (and its alert) down with the batch
    p = make_pipeline()
    long_norm = "GJ37Z1140CAM2SECTOR11GSRTCBUSSTAND"
    p.voter.add(cand(1, long_norm, age_s=3.3))
    p.voter.add(cand(1, VALID, age_s=3.2))
    p._tick_time_based(datetime.now(timezone.utc))

    sent = detections(p, 1)
    assert len(sent) == 1
    assert [r["plate_norm"] for r in sent[0]["reads"]] == [VALID]
    assert [s["plate_norm"] for s in sent[0]["sightings"]] == [VALID]
    for r in sent[0]["reads"]:
        assert len(r["plate_norm"]) <= API_PLATE_NORM_MAX
        assert len(r["plate_raw"]) <= API_PLATE_RAW_MAX
    assert p.cameras[1].suppression()["too_long"] == 1

    # and no later closure batch carries a sighting of it either
    p.client.posted.clear()
    p.cameras[1].last_flush_mono -= p.settings.post_batch_s
    p.sightings.close_all(1)
    p._tick_time_based(datetime.now(timezone.utc))
    later = detections(p, 1)
    assert [s["plate_norm"] for b in later for s in b["sightings"]] == [VALID]


# ---- finding 2: live_counts-only batches vs. read batches --------------------------------
class FakeCounter:
    def __init__(self) -> None:
        self.counts = {"car": 1}

    def live_counts(self) -> dict:
        return {"minute": "2026-10-13T04:17:00.000Z", "counts": dict(self.counts)}


def live_counts_period(p, cam) -> None:
    cam.last_flush_mono -= LIVE_PERIOD
    cam.last_live_counts_mono -= LIVE_PERIOD
    p._tick_time_based(datetime.now(timezone.utc))


LIVE_PERIOD = 10.0     # > LIVE_COUNTS_EVERY_S and > POST_BATCH_S


def test_api_outage_live_counts_cannot_push_the_read_batch_out_of_the_queue():
    p = make_pipeline()
    p.sender = Sender(queue_size=4)            # not started: the API is down, nothing drains
    cam = p.cameras[1]
    cam.counter = FakeCounter()
    p.voter.add(cand(1, VALID, age_s=3.2))
    p._tick_time_based(datetime.now(timezone.utc))     # the immediate (alert) batch is queued
    for i in range(50):                                # ~4 minutes of live counts
        cam.counter.counts = {"car": i}
        live_counts_period(p, cam)

    p.sender.start()                                   # the API is back
    p.sender.stop(flush_timeout_s=10.0)
    posted = [payload for payload, _files in p.client.posted]
    assert [r["plate_norm"] for r in posted[0]["reads"]] == [VALID]   # read batch first, kept
    assert len(posted) == 2                                           # + ONE live-counts batch
    assert posted[1]["reads"] == [] and posted[1]["sightings"] == []
    assert posted[1]["live_counts"]["counts"] == {"car": 49}          # the newest counts
    assert p.sender.stats["dropped"] == 0


def test_live_counts_only_batch_is_latest_wins_per_camera():
    p = make_pipeline(camera_ids=(1, 2))
    latest: dict = {}
    p.sender.submit_latest = lambda key, job: latest.__setitem__(key, job)
    for cid in (1, 2):
        p.cameras[cid].counter = FakeCounter()
    for _ in range(3):
        for cid in (1, 2):
            live_counts_period(p, p.cameras[cid])
    assert p.sender.jobs == []                         # nothing FIFO
    assert sorted(latest) == ["live_counts:1", "live_counts:2"]
    assert all(job.kind == "live_counts" for job in latest.values())
    latest["live_counts:2"].call()
    payload, files = p.client.posted[0]
    assert payload["camera_id"] == 2 and payload["live_counts"]["counts"] == {"car": 1}
    assert payload["reads"] == [] and files == {}


def test_live_counts_without_counts_send_nothing():
    p = make_pipeline()
    cam = p.cameras[1]
    cam.counter = FakeCounter()
    cam.counter.counts = {}
    p.sender.submit_latest = lambda key, job: pytest.fail("nothing to send")
    live_counts_period(p, cam)
    assert p.sender.jobs == []


def test_live_counts_jobs_are_not_retried():
    s = Sender(min_backoff_s=0.01, max_backoff_s=0.01)
    calls: list[int] = []

    def down() -> None:
        calls.append(1)
        raise RetryableError("POST /internal/detections: ConnectionError")

    s._run_job(Job("live_counts", 1, down))
    assert calls == [1] and s.stats["failed"] == 1 and s.stats["retried"] == 0


def test_batches_with_reads_sightings_or_events_are_essential():
    p = make_pipeline()
    cam = p.cameras[1]
    count = {"minute": "2026-10-13T04:16:00.000Z", "class": "car", "count": 3}
    cam.pending.object_counts.append(count)
    p._flush(cam, [])
    p.voter.add(cand(1, VALID, age_s=3.2))
    p._tick_time_based(datetime.now(timezone.utc))
    cam.pending.events.append(({"type": "loop_reset", "occurred_at": T0, "note": "t"}, None))
    p._flush(cam, [])
    assert [job.essential for job in p.sender.jobs] == [False, True, True]


def run_sender(s: Sender) -> None:
    s.start()
    s.stop(flush_timeout_s=10.0)


def test_full_queue_drops_jobs_without_reads_first_and_keeps_read_batches_in_order():
    s = Sender(queue_size=3)
    sent: list[str] = []

    def job(name: str, essential: bool) -> Job:
        return Job("detections", 1, lambda: sent.append(name), essential=essential)

    s.submit(job("reads-1", True))
    s.submit(job("counts-1", False))
    s.submit(job("reads-2", True))
    s.submit(job("reads-3", True))     # full: the counts-only batch goes, not reads-1
    assert s.stats["dropped"] == 1
    s.submit(job("counts-2", False))   # full: the oldest job without reads goes - itself
    s.submit(job("reads-4", True))     # only read batches left: the oldest one goes
    assert s.stats["dropped"] == 3
    assert s.pending() == 3
    run_sender(s)
    assert sent == ["reads-2", "reads-3", "reads-4"]


def test_fifo_jobs_go_before_latest_wins_jobs():
    s = Sender()
    sent: list[str] = []
    s.submit_latest("live_counts:1", Job("live_counts", 1, lambda: sent.append("counts-old")))
    s.submit(Job("detections", 1, lambda: sent.append("reads"), essential=True))
    s.submit_latest("live_counts:1", Job("live_counts", 1, lambda: sent.append("counts-new")))
    assert s.pending() == 2
    run_sender(s)
    assert sent == ["reads", "counts-new"]


def live_counts_broadcast(p, camera_id: int) -> list[int]:
    """The ``car`` live count of every batch the API got for the camera, in posting order."""
    return [payload["live_counts"]["counts"]["car"] for payload, _files in p.client.posted
            if payload["camera_id"] == camera_id and "live_counts" in payload]


@pytest.mark.parametrize("newer", ["urgent read batch", "minute rollover batch"])
def test_stale_live_counts_are_never_broadcast_after_a_newer_batch(newer):
    # review of 8fe441b: the live-counts-only job waited in "latest wins" until the FIFO was
    # empty, so it was posted AFTER the camera's newer batch: the API broadcast 7 then 5
    p = make_pipeline()
    p.sender = Sender()                     # busy elsewhere: nothing drains yet
    cam = p.cameras[1]
    cam.counter = FakeCounter()
    cam.counter.counts = {"car": 5}
    live_counts_period(p, cam)              # a live-counts-only job (latest wins)
    cam.counter.counts = {"car": 7}
    if newer == "urgent read batch":
        p.voter.add(cand(1, VALID, age_s=3.2))
        p._tick_time_based(datetime.now(timezone.utc))
    else:                                   # the finished minute goes out in a periodic batch
        cam.pending.object_counts.append(
            {"minute": "2026-10-13T04:16:00.000Z", "class": "car", "count": 5})
        cam.last_flush_mono -= LIVE_PERIOD
        p._tick_time_based(datetime.now(timezone.utc))
    run_sender(p.sender)
    broadcast = live_counts_broadcast(p, 1)
    assert broadcast[-1] == 7               # the camera page ends on the newest count
    assert broadcast == sorted(broadcast)   # and never goes backwards
    assert broadcast == [7]                 # the superseded job is not sent at all


def test_a_queued_batch_supersedes_only_its_own_cameras_pending_live_counts():
    s = Sender(queue_size=1)
    sent: list[str] = []

    def job(kind: str, cid: int, name: str, essential: bool = False, **kw) -> Job:
        return Job(kind, cid, lambda: sent.append(name), essential=essential, **kw)

    s.submit_latest("live_counts:1", job("live_counts", 1, "counts-1-old"))
    s.submit_latest("live_counts:2", job("live_counts", 2, "counts-2"))
    s.submit(job("detections", 1, "batch-1", True, supersedes="live_counts:1"))
    assert s.pending() == 2                 # counts-1-old is gone, camera 2's job stays
    s.submit_latest("live_counts:1", job("live_counts", 1, "counts-1-new"))
    # the queue is full of read batches: this counts-only batch is dropped itself, so it is
    # never sent and supersedes nothing - counts-1-new stays the newest counts that go out
    s.submit(job("detections", 1, "counts-batch-1", supersedes="live_counts:1"))
    assert s.stats["dropped"] == 1
    run_sender(s)
    assert sent == ["batch-1", "counts-2", "counts-1-new"]


# ---- finding 3: each best shot keeps its own frame ---------------------------------------
def scene(value: int) -> np.ndarray:
    return np.full((540, 960, 3), value, np.uint8)


def test_each_shot_keeps_the_frame_it_was_cut_from():
    tr = PlateTracker(1, gap_s=2.0, keep=3)
    images = [scene(20 * (n + 1)) for n in range(5)]
    for n, img in enumerate(images):
        tr.update([Box(300, 300, 100 + 10 * n, 30, 0.9, "onnx")], img,
                  T0 + timedelta(seconds=0.5 * n), 0.5 * n, n)
    t = tr.tracks[0]
    assert [s.frame_index for s in t.shots] == [4, 3, 2]
    for shot in t.shots:
        assert shot.frame is images[shot.frame_index]
        assert np.shares_memory(shot.crop, shot.frame)   # the reference costs no extra memory


# Three frames of one approaching plate, each a different grey so a JPEG tells which frame it
# shows. Widths 100 / 110 / 120 px: shot 0 is the frame-2 crop, shot 1 frame 1, shot 2 frame 0.
GREYS = (40, 80, 120)
GONE = 250                       # the frame in which the track ends: the vehicle has left


def feed_track(p, conf_by_grey: dict[int, float]) -> dict[int, np.ndarray]:
    """The three frames of the approaching plate; OCR answers ``VALID`` at ``conf_by_grey``."""
    cam = p.cameras[1]
    p.ocr = FakeOcr(lambda crop: (VALID, conf_by_grey[int(round(float(crop.mean())))]))
    p.evidence = EvidenceStore(None)
    images = {grey: scene(grey) for grey in GREYS}
    for n, grey in enumerate(GREYS):
        frame = Frame(1, images[grey], T0 + timedelta(seconds=0.5 * n), 0.5 * n, n)
        p._track_boxes(cam, frame, [Box(300, 300, 100 + 10 * n, 30, 0.9, "contour")])
    assert p.voter.open_buckets(1) == 0           # still growing: OCR waits for the track end
    return images


def end_track_by_gap(p) -> None:
    """No box for ``ANPR_TRACK_GAP_S``: the track ends in a frame the vehicle has left."""
    gone = Frame(1, scene(GONE), T0 + timedelta(seconds=3.0), 3.0, 6)
    p._track_boxes(p.cameras[1], gone, [])
    assert p.voter.open_buckets(1) == 1


def run_track(p, conf_by_grey: dict[int, float], end: str = "gap") -> None:
    cam = p.cameras[1]
    feed_track(p, conf_by_grey)
    if end == "gap":
        end_track_by_gap(p)
        p._tick_camera(cam, T0 + timedelta(seconds=5.0))
    else:                                          # decoder restart: tracks flushed, no frame
        cam._on_discontinuity(1, "decoder restart (test)", None, None)
        p._tick_camera(cam, T0 + timedelta(seconds=1.2))
    assert p._flush_if_urgent(cam, time.monotonic())


@pytest.mark.parametrize("best_grey", GREYS)
def test_sighting_frame_is_the_frame_of_the_best_read(best_grey):
    p = make_pipeline()
    confs = {g: (0.95 if g == best_grey else 0.80) for g in GREYS}
    run_track(p, confs)

    p.sender.jobs[0].call()
    payload, files = p.client.posted[0]
    (read,) = payload["reads"]
    (sighting,) = payload["sightings"]
    shot_at = T0 + timedelta(seconds=0.5 * GREYS.index(best_grey))
    assert read["captured_at"] == sighting["best_read_captured_at"] == iso_utc(shot_at)
    assert sighting["frame_file"] == "frame_0"
    frame = cv2.imdecode(np.frombuffer(files["frame_0"], np.uint8), cv2.IMREAD_COLOR)
    assert frame.shape[1] == 960
    assert abs(float(frame.mean()) - best_grey) < 3     # that shot's frame, not the GONE one


def test_flushed_track_read_still_gets_its_shot_frame():
    p = make_pipeline()
    run_track(p, {40: 0.80, 80: 0.95, 120: 0.85}, end="discontinuity")
    p.sender.jobs[0].call()
    payload, files = p.client.posted[0]
    assert [r["plate_norm"] for r in payload["reads"]] == [VALID]
    assert payload["sightings"][0]["closed"] is True
    frame = cv2.imdecode(np.frombuffer(files[payload["sightings"][0]["frame_file"]], np.uint8),
                         cv2.IMREAD_COLOR)
    assert abs(float(frame.mean()) - 80) < 3


def test_voted_read_carries_the_best_members_own_frame():
    p = make_pipeline()
    images = feed_track(p, {40: 0.80, 80: 0.95, 120: 0.85})
    end_track_by_gap(p)
    (read,) = p.voter.expire(T0 + timedelta(seconds=5.0), 1)
    assert read.captured_at == T0 + timedelta(seconds=0.5)
    assert read.frame is images[80]               # a reference, not a copy: no extra memory

    p._accept_reads(p.cameras[1], [read])
    assert read.frame is None                     # released once its JPEG is encoded


def test_shot_frames_stay_bounded_and_are_released_once_the_read_is_accepted():
    # Memory: an open track pins at most ``keep`` decoded frames (its crops are views into them
    # anyway), the vote bucket one, and no frame outlives the accepted read: the sighting keeps
    # only its JPEG.
    p = make_pipeline()
    cam = p.cameras[1]
    p.ocr = fixed_ocr(VALID)
    p.evidence = EvidenceStore(None)
    alive: list[weakref.ref] = []
    for n in range(8):                            # still growing: no OCR before the track ends
        image = scene(10 + 20 * n)
        alive.append(weakref.ref(image))
        frame = Frame(1, image, T0 + timedelta(seconds=0.25 * n), 0.25 * n, n)
        p._track_boxes(cam, frame, [Box(300, 300, 100 + 10 * n, 30, 0.9, "contour")])
        del image, frame
    gc.collect()
    assert sum(ref() is not None for ref in alive) == cam.tracker.keep

    p._track_boxes(cam, Frame(1, scene(GONE), T0 + timedelta(seconds=4.0), 4.0, 9), [])
    gc.collect()
    assert p.voter.open_buckets(1) == 1
    assert sum(ref() is not None for ref in alive) == 1          # the bucket's best member

    p._tick_camera(cam, T0 + timedelta(seconds=30.0))
    gc.collect()
    assert [r.plate_norm for r in cam.pending.reads] == [VALID]
    assert p.sightings.open_sightings(1)[0].best_frame_jpeg
    assert sum(ref() is not None for ref in alive) == 0
