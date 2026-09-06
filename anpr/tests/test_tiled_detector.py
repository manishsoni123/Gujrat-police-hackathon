"""TiledDetector: tile grid, coordinate offsets, plausibility filter, duplicate merging, build_detector wiring."""
import numpy as np

from anpr.config import Settings, load_settings
from anpr.detector import Box, ContourPlateDetector, TiledDetector, build_detector, detector_status, tile_grid


class FakeDetector:
    """Finds a 'plate' only in images narrower than ``max_w`` (like a 384 px model that misses small plates)."""

    name = "fake"

    def __init__(self, max_w: int = 800) -> None:
        self.max_w = max_w
        self.calls: list[tuple[int, int]] = []

    def detect(self, image: np.ndarray) -> list[Box]:
        h, w = image.shape[:2]
        self.calls.append((w, h))
        if w > self.max_w:
            return []
        boxes = [Box(10, 20, 80, 24, 0.9, "fake")]                  # plausible plate near the tile origin
        if h >= 300:
            boxes.append(Box(5, 5, w - 10, h - 10, 0.95, "fake"))    # bogus full-tile box
        return boxes


def test_tile_grid_covers_1080p_with_768_tiles():
    tiles = tile_grid(1920, 1080, 768, 0.15)
    assert len(tiles) == 6
    assert all(w == 768 and h == 768 for _, _, w, h in tiles)
    assert max(x + w for x, _, w, _ in tiles) == 1920 and max(y + h for _, y, _, h in tiles) == 1080
    assert tile_grid(640, 480, 768, 0.15) == [(0, 0, 640, 480)]
    assert tile_grid(1920, 1080, 0, 0.15) == [(0, 0, 1920, 1080)]


def test_tiled_boxes_are_offset_merged_and_filtered():
    inner = FakeDetector()
    det = TiledDetector(inner, tile=768, overlap=0.15)
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    boxes = det.detect(frame)
    assert inner.calls[0] == (1920, 1080), "whole frame is tried first"
    assert len(inner.calls) == 7
    # one plausible box per tile, offset into frame coordinates; the full-tile box is dropped
    origins = sorted((b.x - 10, b.y - 20) for b in boxes)
    assert origins == sorted((x, y) for x, y, _, _ in tile_grid(1920, 1080, 768, 0.15))
    assert all(b.w == 80 and b.h == 24 and b.confidence == 0.9 for b in boxes)
    assert det.name == "fake"


def test_duplicates_across_overlapping_tiles_are_merged():
    class Marker:
        """Reports the bright rectangle wherever it is in the image it is given (any size)."""

        name = "marker"

        def detect(self, image):
            cols = np.where(image[:, :, 0].max(axis=0) > 0)[0]
            rows = np.where(image[:, :, 0].max(axis=1) > 0)[0]
            if not len(cols) or not len(rows):
                return []
            return [Box(int(cols.min()), int(rows.min()), int(cols.max() - cols.min() + 1), int(rows.max() - rows.min() + 1),
                        0.5 + image.shape[1] / 10000, "m")]

    det = TiledDetector(Marker(), tile=400, overlap=0.5)
    frame = np.zeros((400, 600, 3), dtype=np.uint8)
    frame[188:212, 250:330] = 255                     # one plate, visible in both tiles (x=0 and x=200) and the whole frame
    boxes = det.detect(frame)
    assert len(boxes) == 1
    assert (boxes[0].x, boxes[0].y, boxes[0].w, boxes[0].h) == (250, 188, 80, 24)
    assert boxes[0].confidence == 0.5 + 600 / 10000, "the most confident duplicate (the whole-frame one here) wins"


def test_build_detector_wraps_when_tile_set():
    plain = build_detector("contour", "/nonexistent.onnx", conf=0.4, min_w=60, cpu=True)
    assert isinstance(plain, ContourPlateDetector)
    tiled = build_detector("contour", "/nonexistent.onnx", conf=0.4, min_w=60, cpu=True, tile=640, tile_overlap=0.2)
    assert isinstance(tiled, TiledDetector) and tiled.name == "contour" and tiled.tile == 640 and tiled.overlap == 0.2
    status = detector_status(tiled, "contour")
    assert status["active"] == "contour" and status["degraded"] is False and status["tile"] == 640
    assert detector_status(plain, "contour")["tile"] == 0


def test_settings_read_tile_from_env(monkeypatch):
    monkeypatch.setenv("ANPR_DET_TILE", "768")
    monkeypatch.setenv("ANPR_DET_TILE_OVERLAP", "0.2")
    s = load_settings([])
    assert s.det_tile == 768 and s.det_tile_overlap == 0.2 and "det_tile" in s.explicit
    assert Settings().det_tile == 0
