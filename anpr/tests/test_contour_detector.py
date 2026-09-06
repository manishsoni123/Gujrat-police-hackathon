"""Contour plate detector on a generated road scene with a synthetic plate."""
import cv2
import numpy as np
import pytest

from anpr.detector import CONTOUR_FALLBACK, ContourPlateDetector, build_detector, detector_status, iou, Box


def make_scene(plate_w=250, plate_h=60, two_line=False, x=400, y=380):
    """960x540 frame: gradient sky, road band, dark vehicle, white plate with black text."""
    h, w = 540, 960
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    for row in range(h):
        frame[row, :] = (120 + row // 6, 100 + row // 8, 80)
    frame[300:520, :] = (72, 72, 74)
    for lx in range(20, w, 90):
        frame[409:413, lx:lx + 45] = (175, 175, 175)
    cv2.rectangle(frame, (x - 120, y - 150), (x + plate_w + 120, y + plate_h + 40), (30, 30, 35), -1)
    cv2.rectangle(frame, (x, y), (x + plate_w, y + plate_h), (245, 245, 245), -1)
    cv2.rectangle(frame, (x, y), (x + plate_w, y + plate_h), (0, 0, 0), 2)
    if two_line:
        cv2.putText(frame, "MH 02", (x + 40, y + plate_h // 2 - 4), cv2.FONT_HERSHEY_DUPLEX, 1.0, (0, 0, 0), 2)
        cv2.putText(frame, "BZ 7788", (x + 30, y + plate_h - 8), cv2.FONT_HERSHEY_DUPLEX, 1.0, (0, 0, 0), 2)
    else:
        cv2.putText(frame, "GJ 01 AB 1234", (x + 12, y + plate_h - 16), cv2.FONT_HERSHEY_DUPLEX, 1.2, (0, 0, 0), 2)
    # a bright-ish timestamp string that must not be detected as a plate
    cv2.putText(frame, "10:15:30.1", (760, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (230, 230, 200), 2)
    return frame


def test_detects_one_line_plate():
    frame = make_scene()
    boxes = ContourPlateDetector(min_w=60).detect(frame)
    assert boxes, "no plate found"
    expected = Box(400, 380, 250, 60, 1.0, "contour")
    best = max(boxes, key=lambda b: iou(b, expected))
    assert iou(best, expected) > 0.8
    assert 0.15 <= best.h / best.w <= 0.6
    assert best.confidence >= 0.6


def test_detects_two_line_plate():
    frame = make_scene(plate_w=200, plate_h=100, two_line=True)
    boxes = ContourPlateDetector(min_w=60).detect(frame)
    expected = Box(400, 380, 200, 100, 1.0, "contour")
    assert boxes and max(iou(b, expected) for b in boxes) > 0.8


def test_ignores_small_and_text_regions():
    frame = make_scene(plate_w=40, plate_h=12)     # narrower than ANPR_MIN_PLATE_W
    boxes = ContourPlateDetector(min_w=60).detect(frame)
    assert all(b.w >= 60 for b in boxes)
    assert all(b.y > 100 for b in boxes), "the timestamp text at the top must not be a plate"


def test_no_plate_on_empty_road():
    frame = make_scene()
    frame[380:440, 400:650] = (30, 30, 35)          # paint the plate away
    assert ContourPlateDetector(min_w=60).detect(frame) == []


def test_build_detector_contour_and_missing_onnx(tmp_path):
    det = build_detector("contour", str(tmp_path / "missing.onnx"), conf=0.4, min_w=60, cpu=True)
    assert det.name == "contour"
    assert detector_status(det, "contour") == {
        "requested": "contour", "active": "contour", "degraded": False, "providers": [], "weights": None, "tile": 0,
    }
    auto = build_detector("auto", str(tmp_path / "missing.onnx"), conf=0.4, min_w=60, cpu=True)
    # graceful but *visible* fallback when the weights are absent
    assert auto.name == CONTOUR_FALLBACK == "contour-fallback"
    assert len(CONTOUR_FALLBACK) <= 16          # anpr_workers.detector is VARCHAR(16)
    status = detector_status(auto, "auto")
    assert status["degraded"] is True and status["active"] == "contour-fallback" and status["requested"] == "auto"
    assert auto.detect(make_scene())            # the fallback still reads the synthetic plate
    with pytest.raises(RuntimeError):
        build_detector("onnx", str(tmp_path / "missing.onnx"), conf=0.4, min_w=60, cpu=True)
    with pytest.raises(RuntimeError):
        build_detector("yolo", str(tmp_path / "missing.onnx"), conf=0.4, min_w=60, cpu=True)


def test_build_detector_refuses_corrupt_listed_weight(tmp_path):
    """A file named like a ledger entry but with the wrong SHA-256 is never loaded."""
    bogus = tmp_path / "yolo-v9-t-384-license-plates-end2end.onnx"
    bogus.write_bytes(b"not an onnx model")
    with pytest.raises(RuntimeError, match="SHA-256 MISMATCH"):
        build_detector("onnx", str(bogus), conf=0.4, min_w=60, cpu=True)
    auto = build_detector("auto", str(bogus), conf=0.4, min_w=60, cpu=True)
    assert auto.name == CONTOUR_FALLBACK
