"""OsdMask: top/bottom bands, static-text regions learnt from low temporal variance + strong edges."""
import cv2
import numpy as np

from anpr.detector import Box, OsdMask

W, H = 1280, 720


def frame(t: int, rng: np.random.Generator) -> np.ndarray:
    """Night road: dark noisy background, a burnt-in clock (top), a static signboard (middle), a car moving right."""
    img = np.full((H, W, 3), 40, np.uint8)
    img = np.clip(img.astype(np.int16) + rng.integers(-3, 4, (H, W, 1)), 0, 255).astype(np.uint8)
    cv2.putText(img, "13-06-2026 22:08:%02d  CAM 02 JANPATH" % (t % 60), (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2)
    cv2.rectangle(img, (900, 300), (1180, 360), (230, 230, 230), -1)          # lit signboard, never moves
    cv2.putText(img, "75750 03008", (910, 345), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    x = 40 + 12 * t                                                          # car with a plate crossing the frame
    cv2.rectangle(img, (x, 480), (x + 200, 600), (90, 90, 90), -1)
    cv2.rectangle(img, (x + 60, 560), (x + 150, 585), (240, 240, 240), -1)
    cv2.putText(img, "GJ01AB1234", (x + 62, 580), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
    return img


def test_band_rejects_top_and_bottom_boxes_immediately():
    m = OsdMask(band=0.08, warmup_s=30.0)
    clock = Box(293, 0, 220, 42, 0.95, "onnx")            # the real cam02 clock box
    bottom = Box(500, H - 30, 200, 28, 0.9, "onnx")
    plate = Box(600, 560, 90, 25, 0.8, "onnx")
    assert m.filter([clock, bottom, plate], H) == [plate]
    assert m.suppressed_band == 2 and m.suppressed_region == 0
    assert OsdMask(band=0.0).filter([clock], H) == [clock]  # 0 disables the band


def test_static_text_regions_learnt_after_warm_up():
    rng = np.random.default_rng(7)
    m = OsdMask(band=0.0, warmup_s=30.0)                   # band off: the region rule alone must catch the clock
    now = 1000.0
    for t in range(70):                                    # 35 s at 2 fps, sampled every 0.5 s
        m.observe(frame(t, rng), now + t * 0.5)
    assert m.regions, "static text regions expected after the warm-up"
    clock = Box(20, 12, 560, 34, 0.95, "onnx")
    sign = Box(905, 305, 270, 50, 0.9, "onnx")
    plate_now = Box(40 + 12 * 69 + 60, 560, 90, 25, 0.8, "onnx")
    kept = m.filter([clock, sign, plate_now], H)
    assert kept == [plate_now], [b.as_list() for b in m.regions]
    assert m.suppressed_region == 2
    # regions cover the clock and the signboard, none reaches the road the car drove on
    assert any(r.y < 60 for r in m.regions) and any(280 <= r.y <= 320 for r in m.regions)
    assert all(not (480 <= r.y <= 600 and r.x < 700) for r in m.regions)


def test_nothing_is_learnt_before_the_warm_up_or_when_disabled():
    rng = np.random.default_rng(1)
    m = OsdMask(band=0.0, warmup_s=30.0)
    for t in range(20):                                    # 10 s only
        m.observe(frame(t, rng), 500.0 + t * 0.5)
    assert m.regions == []
    off = OsdMask(band=0.0, warmup_s=0.0)
    for t in range(80):
        off.observe(frame(t, rng), 500.0 + t * 0.5)
    assert off.regions == [] and off._samples == []


def test_resolution_change_resets_the_model():
    rng = np.random.default_rng(3)
    m = OsdMask(band=0.0, warmup_s=1.0, recompute_s=1.0)
    for t in range(12):
        m.observe(frame(t, rng), 100.0 + t * 0.5)
    assert m.regions
    m.observe(np.zeros((360, 640, 3), np.uint8), 200.0)
    assert m.regions == [] and len(m._samples) == 1
