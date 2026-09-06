"""PlateTracker: association across frames, best-shot memory, OCR readiness, ended tracks (= detected vehicles)."""
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np

from anpr.detector import Box
from anpr.tracks import PlateTracker, crop_with_context, sharpness, shot_score

T0 = datetime(2026, 9, 5, 14, 0, tzinfo=timezone.utc)


def scene(plate_x: int, plate_y: int, w: int, blur: int = 0) -> np.ndarray:
    """1080p night frame with one white plate carrying black text at (plate_x, plate_y), ``w`` px wide."""
    img = np.full((1080, 1920, 3), 30, np.uint8)
    h = int(w * 0.3)
    cv2.rectangle(img, (plate_x, plate_y), (plate_x + w, plate_y + h), (235, 235, 235), -1)
    cv2.putText(img, "GJ01AB1234", (plate_x + 2, plate_y + h - 4), cv2.FONT_HERSHEY_SIMPLEX, w / 200.0, (0, 0, 0), max(1, w // 60))
    if blur:
        img = cv2.GaussianBlur(img, (blur * 2 + 1, blur * 2 + 1), 0)
    return img


def test_approaching_plate_is_one_track_with_the_largest_shot_kept():
    tr = PlateTracker(61, gap_s=2.0, keep=3, stall_updates=3)
    widths = [30, 45, 65, 90, 120, 150]
    ended_all = []
    for n, w in enumerate(widths):
        x, y = 600 + 40 * n, 400 + 60 * n
        img = scene(x, y, w)
        touched, ended = tr.update([Box(x, y, w, int(w * 0.3), 0.8, "onnx")], img, T0 + timedelta(seconds=0.5 * n), 0.5 * n, n)
        ended_all += ended
        assert len(touched) == 1 and touched[0].id == 1, "consecutive boxes of one approaching plate share a track"
    assert tr.opened == 1 and not ended_all
    t = tr.tracks[0]
    assert [s.box.w for s in t.shots] == [150, 120, 90], "the three widest shots are kept, best first"
    assert t.best_width == 150 and t.frames == 6 and t.stall == 0
    # the vehicle leaves: no box for > gap_s -> the track ends exactly once
    for n in range(6, 12):
        _, ended = tr.update([], scene(0, 0, 10), T0 + timedelta(seconds=0.5 * n), 0.5 * n, n)
        ended_all += ended
    assert [t.id for t in ended_all] == [1]
    assert tr.open == 0 and tr.ended_total == 1


def test_ocr_policy_settled_then_regrow_then_ended():
    tr = PlateTracker(62, gap_s=2.0, keep=3, stall_updates=3, regrow=1.25)
    img = scene(500, 500, 80)
    box = Box(500, 500, 80, 24, 0.9, "onnx")
    t = None
    for n in range(4):
        (t,), _ = tr.update([box], img, T0 + timedelta(seconds=0.5 * n), 0.5 * n, n)
        if n < 3:
            assert not tr.ready_for_ocr(t, 60), "not yet settled"
    assert tr.ready_for_ocr(t, 60), "three updates without growth -> settled -> OCR"
    assert not tr.ready_for_ocr(t, 100), "below the OCR minimum width it is a vehicle detection only"
    t.ocr_width, t.ocr_runs = t.best_width, 1
    assert not tr.ready_for_ocr(t, 60), "already read at this size"
    (t,), _ = tr.update([Box(520, 520, 110, 33, 0.9, "onnx")], scene(520, 520, 110), T0 + timedelta(seconds=2.0), 2.0, 4)
    assert tr.ready_for_ocr(t, 60), "the best shot grew by more than 25 % -> read again"
    t.ocr_width = t.best_width
    small = PlateTracker(63, gap_s=1.0, keep=2, stall_updates=5)
    (u,), _ = small.update([box], img, T0, 0.0, 0)
    assert not small.ready_for_ocr(u, 60) and small.ready_for_ocr(u, 60, ended=True), "an ending track gets its last chance"


def test_two_vehicles_side_by_side_stay_separate_and_fast_mover_is_matched_by_centre():
    tr = PlateTracker(64, gap_s=2.0, keep=2)
    img = scene(0, 0, 10)
    a0, b0 = Box(300, 600, 80, 24, 0.9, "onnx"), Box(1300, 600, 80, 24, 0.9, "onnx")
    touched, _ = tr.update([a0, b0], img, T0, 0.0, 0)
    assert sorted(t.id for t in touched) == [1, 2]
    # next frame: A moved 100 px (no IoU overlap at all, but within 1.5 widths), B stayed
    a1 = Box(400, 610, 84, 25, 0.9, "onnx")
    touched, _ = tr.update([a1, b0], img, T0 + timedelta(seconds=0.5), 0.5, 1)
    by_x = {t.box.x: t.id for t in touched}
    assert by_x == {400: 1, 1300: 2}, "centre-distance fallback keeps the fast mover on its own track"


def test_flush_ends_every_open_track():
    tr = PlateTracker(65)
    tr.update([Box(100, 100, 70, 20, 0.8, "onnx"), Box(900, 100, 70, 20, 0.8, "onnx")], scene(0, 0, 10), T0, 0.0, 0)
    ended = tr.flush()
    assert len(ended) == 2 and all(t.ended for t in ended) and tr.open == 0 and tr.tracks == []


def test_sharpness_prefers_the_sharp_crop_and_crop_has_context():
    sharp_img, blurred = scene(500, 500, 120), scene(500, 500, 120, blur=4)
    box = Box(500, 500, 120, 36, 0.9, "onnx")
    s1, s2 = sharpness(crop_with_context(sharp_img, box)), sharpness(crop_with_context(blurred, box))
    assert s1 > s2 * 2
    assert shot_score(120, s1) > shot_score(120, s2)
    assert shot_score(90, 1000.0) > shot_score(120, 0.0), "a sharp 90 px crop beats a fully blurred 120 px one"
    crop = crop_with_context(sharp_img, box)
    assert crop.shape[1] == 120 + 2 * int(120 * 0.08) and crop.shape[0] == 36 + 2 * int(36 * 0.15)
    assert crop_with_context(sharp_img, Box(1900, 1070, 100, 40, 0.5, "onnx")).size > 0, "boxes at the frame edge are clipped, not empty"


def test_track_kind_separates_plates_vehicles_and_static_text():
    from anpr.tracks import Track, track_kind

    def track(**kw):
        return Track(id=1, camera_id=67, box=Box(0, 0, 90, 27, 0.9, "onnx"), first_seen=T0, last_seen=T0, last_pts=0.0, **kw)

    assert track_kind(track()) == "vehicle", "a plate box that was never read is still a detected vehicle"
    assert track_kind(track(reads=["GJ36AJ2890"])) == "plate"
    assert track_kind(track(reads=["0G94751"])) == "vehicle", "an invalid string is a vehicle whose plate could not be read"
    assert track_kind(track(text_only=2)) == "text", "letters-only OCR on every pass = caption / signboard, not a vehicle"
    assert track_kind(track(text_only=1, reads=["0G94751"])) == "vehicle", "one digit-bearing read outweighs a letters-only pass"
    assert track_kind(track(text_only=1, reads=["GJ36AJ2890"])) == "plate"
