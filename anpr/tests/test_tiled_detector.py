"""TiledDetector: tile grid, coordinate offsets, plausibility filter, duplicate merging, wiring."""
import numpy as np

from anpr.config import Settings, load_settings
from anpr.detector import (
    Box,
    ContourPlateDetector,
    TileBox,
    TiledDetector,
    absorb,
    build_detector,
    cut_sides,
    detector_status,
    merge_boxes,
    same_plate,
    tile_grid,
)


class FakeDetector:
    """Finds a 'plate' only in images narrower than ``max_w`` (a 384 px model misses small ones)."""

    name = "fake"

    def __init__(self, max_w: int = 800) -> None:
        self.max_w = max_w
        self.calls: list[tuple[int, int]] = []

    def detect(self, image: np.ndarray) -> list[Box]:
        h, w = image.shape[:2]
        self.calls.append((w, h))
        if w > self.max_w:
            return []
        boxes = [Box(10, 20, 80, 24, 0.9, "fake")]               # plausible plate near the origin
        if h >= 300:
            boxes.append(Box(5, 5, w - 10, h - 10, 0.95, "fake"))  # bogus full-tile box
        return boxes


def test_tile_grid_covers_1080p_with_768_tiles():
    tiles = tile_grid(1920, 1080, 768, 0.15)
    assert len(tiles) == 6
    assert all(w == 768 and h == 768 for _, _, w, h in tiles)
    assert max(x + w for x, _, w, _ in tiles) == 1920
    assert max(y + h for _, y, _, h in tiles) == 1080
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
            x, y = int(cols.min()), int(rows.min())
            w, h = int(cols.max() - cols.min() + 1), int(rows.max() - rows.min() + 1)
            return [Box(x, y, w, h, 0.5 + image.shape[1] / 10000, "m")]

    det = TiledDetector(Marker(), tile=400, overlap=0.5)
    frame = np.zeros((400, 600, 3), dtype=np.uint8)
    frame[188:212, 250:330] = 255     # one plate, in both tiles (x=0, x=200) and the whole frame
    boxes = det.detect(frame)
    assert len(boxes) == 1
    assert (boxes[0].x, boxes[0].y, boxes[0].w, boxes[0].h) == (250, 188, 80, 24)
    assert boxes[0].confidence == 0.5 + 600 / 10000, "the most confident duplicate (whole frame)"


def test_build_detector_wraps_when_tile_set():
    plain = build_detector("contour", "/nonexistent.onnx", conf=0.4, min_w=60, cpu=True)
    assert isinstance(plain, ContourPlateDetector)
    tiled = build_detector("contour", "/nonexistent.onnx", conf=0.4, min_w=60, cpu=True,
                           tile=640, tile_overlap=0.2)
    assert isinstance(tiled, TiledDetector) and tiled.name == "contour"
    assert tiled.tile == 640 and tiled.overlap == 0.2
    status = detector_status(tiled, "contour")
    assert status["active"] == "contour" and status["degraded"] is False and status["tile"] == 640
    assert detector_status(plain, "contour")["tile"] == 0


def test_settings_read_tile_from_env(monkeypatch):
    monkeypatch.setenv("ANPR_DET_TILE", "768")
    monkeypatch.setenv("ANPR_DET_TILE_OVERLAP", "0.2")
    s = load_settings([])
    assert s.det_tile == 768 and s.det_tile_overlap == 0.2 and "det_tile" in s.explicit
    assert Settings().det_tile == 0


# ---- plates cut at tile edges ----------------------------------------------------------------
# The live profile: ANPR_DETECT_WIDTH=1280, ANPR_DET_TILE=768, overlap 0.15 -> on a 1280 x 720
# detector image the tiles are x 0-768 and x 512-1280 (interior edges at x=512 and x=768).
# Boxes quoted with a camera and time are the real ONNX detector's on the synthetic videos.


def P(x1: int, x2: int, y1: int, y2: int, conf: float = 0.8) -> Box:
    """A box from frame-pixel corners."""
    return Box(x1, y1, x2 - x1, y2 - y1, conf, "onnx")


def corners(b: Box) -> tuple[int, int, int, int]:
    return b.x, b.x + b.w, b.y, b.y + b.h


class Scripted:
    """Inner detector returning prescribed boxes: ``script[None]`` for the whole frame,
    ``script[(x, y)]`` for the tile at that origin. Boxes are written in frame pixels and handed
    out in the coordinates of the image asked about (the TiledDetector calls the whole frame
    first, then the tiles in ``tile_grid`` order)."""

    name = "scripted"

    def __init__(self, width: int, height: int, script: dict, tile: int = 768,
                 overlap: float = 0.15) -> None:
        self.regions = [None] + [(x, y) for x, y, _, _ in tile_grid(width, height, tile, overlap)]
        self.script = script
        self.calls = 0

    def detect(self, image: np.ndarray) -> list[Box]:
        key = self.regions[self.calls]
        self.calls += 1
        ox, oy = key or (0, 0)
        return [Box(b.x - ox, b.y - oy, b.w, b.h, b.confidence, b.source)
                for b in self.script.get(key, [])]


def run(script: dict, width: int = 1280, height: int = 720) -> list[Box]:
    det = TiledDetector(Scripted(width, height, script), tile=768, overlap=0.15)
    return det.detect(np.zeros((height, width, 3), dtype=np.uint8))


def test_cut_sides_only_count_interior_tile_edges():
    left, right = (0, 0, 768, 720), (512, 0, 768, 720)
    assert cut_sides(P(500, 768, 300, 340), left, 1280, 720, 15) == "r"
    assert cut_sides(P(0, 200, 300, 340), left, 1280, 720, 15) == "", "a frame edge is no cut"
    assert cut_sides(P(512, 714, 300, 340), right, 1280, 720, 15) == "l"
    assert cut_sides(P(522, 800, 300, 340), right, 1280, 720, 15) == "l", "a few px short of it"
    assert cut_sides(P(530, 800, 300, 340), right, 1280, 720, 15) == ""
    assert cut_sides(P(1000, 1280, 0, 40), right, 1280, 720, 15) == "", "frame edges right, top"
    # 1920 x 1080, 768 px tiles: rows at y=0 and y=312 -> interior edges at y=768 and y=312
    assert cut_sides(P(800, 900, 740, 768), (652, 0, 768, 768), 1920, 1080, 15) == "b"
    assert cut_sides(P(800, 900, 312, 360), (652, 312, 768, 768), 1920, 1080, 15) == "t"


def test_edge_tolerance_defaults_to_two_percent_of_the_tile():
    assert TiledDetector(FakeDetector(), tile=768).edge_px == 15
    assert TiledDetector(FakeDetector(), tile=100).edge_px == 4
    assert TiledDetector(FakeDetector(), tile=768, edge_px=3).edge_px == 3


def test_cut_piece_inside_the_complete_box_gives_way():
    """Cam 3 at 26.6 s (GJ01AB1234): the whole frame boxes the plate at x 214-717, the right tile
    only the part right of its left edge, 512-714. IoU 0.35 < 0.4 kept both, and the cut piece
    went first whenever it was the more confident one."""
    whole = P(214, 717, 535, 659, 0.52)
    boxes = run({None: [whole], (512, 0): [P(512, 714, 539, 647, 0.43)]})
    assert [corners(b) for b in boxes] == [(214, 717, 535, 659)]
    boxes = run({None: [whole], (512, 0): [P(512, 714, 539, 647, 0.9)]})
    assert [corners(b) for b in boxes] == [(214, 717, 535, 659)], "even when more confident"
    assert boxes[0].confidence == 0.9


def test_two_cut_pieces_across_the_overlap_become_the_whole_plate():
    """Plate x 500-800: tile 1 sees 500-768, tile 2 sees 512-800; the whole frame missed it. The
    more confident piece keeps its height, its cut side moves out to the other piece's."""
    boxes = run({(0, 0): [P(500, 768, 540, 650, 0.7)], (512, 0): [P(512, 800, 545, 648, 0.75)]})
    assert [corners(b) for b in boxes] == [(500, 800, 545, 648)]
    assert boxes[0].confidence == 0.75


def test_wide_uncut_tile_box_is_kept_alone():
    """Cam 2 at 2.2 s, the bus GJ30MF6066: tile 1 boxes the complete plate at x 24-545 (521 px,
    wider than half the tile) and nothing else sees it. It used to be dropped as implausible."""
    boxes = run({(0, 0): [P(24, 545, 528, 664, 0.46)]})
    assert [corners(b) for b in boxes] == [(24, 545, 528, 664)]


def test_wide_cut_tile_box_is_dropped_alone():
    """Cam 2 at 2.8 s: tile 1 boxes x 309-764 (455 px, 4 px short of its right edge) and nothing
    else; wide and running into the neighbour tile, like a smear - dropped without a partner."""
    assert run({(0, 0): [P(309, 764, 536, 660, 0.49)]}) == []


def test_wide_uncut_box_of_one_tile_absorbs_the_cut_piece_of_the_other():
    """Cam 3 at 32.4 s: the complete plate (473 px) in tile 1 was dropped as wider than half the
    tile, the left-cut piece of tile 2 won and was read without its left part (``01AB1234``)."""
    boxes = run({(0, 0): [P(213, 686, 542, 661, 0.80)], (512, 0): [P(513, 684, 546, 648, 0.65)]})
    assert [corners(b) for b in boxes] == [(213, 686, 542, 661)]


def test_wide_cut_box_completes_a_cut_piece_of_the_other_tile():
    """Own gate at 12.0 s: tile 1 boxed 412-766 (right part missing -> ``GJ01AB1``), tile 2 boxed
    518-910 (392 px, wide and 6 px short of its left edge)."""
    boxes = run({(0, 0): [P(412, 766, 542, 654, 0.53)], (512, 0): [P(518, 910, 533, 660, 0.43)]})
    assert [corners(b) for b in boxes] == [(412, 910, 542, 654)]
    assert boxes[0].confidence == 0.53


def test_whole_frame_and_both_cut_pieces_merge_into_one_box():
    """Own gate at 74.8 s: whole frame 477-801, tile 1 474-768 (cut), tile 2 515-813 (cut). The
    most confident piece (tile 2) takes the left side of tile 1; the whole-frame box is its
    IoU duplicate."""
    boxes = run({None: [P(477, 801, 490, 652, 0.87)], (0, 0): [P(474, 768, 488, 654, 0.89)],
                 (512, 0): [P(515, 813, 489, 654, 0.92)]})
    assert [corners(b) for b in boxes] == [(474, 813, 489, 654)]
    assert boxes[0].confidence == 0.92


def test_truncated_box_without_partner_is_kept():
    boxes = run({(0, 0): [P(501, 768, 545, 648, 0.73)]})
    assert [corners(b) for b in boxes] == [(501, 768, 545, 648)]


def test_containment_duplicate_keeps_the_complete_box():
    """Uncut boxes, one inside the other (IoU 0.36 < 0.4 kept both before): the larger stays."""
    boxes = run({None: [P(400, 500, 300, 350, 0.5)], (0, 0): [P(410, 470, 310, 340, 0.9)]})
    assert [corners(b) for b in boxes] == [(400, 500, 300, 350)]
    assert boxes[0].confidence == 0.9


def test_larger_cut_box_keeps_its_cut_side_over_a_partial_box_inside():
    """A partial uncut box inside a larger left-cut box: the larger one is the more complete; the
    partial box's left side lies inside the cut one, so it does not move it."""
    boxes = run({(0, 0): [P(600, 700, 300, 330, 0.9)], (512, 0): [P(512, 760, 298, 334, 0.6)]})
    assert [corners(b) for b in boxes] == [(512, 760, 298, 334)]
    assert boxes[0].confidence == 0.9


def test_iou_duplicates_keep_the_most_confident_box():
    boxes = run({None: [P(300, 400, 300, 340, 0.6)], (0, 0): [P(306, 404, 298, 338, 0.8)]})
    assert [corners(b) for b in boxes] == [(306, 404, 298, 338)]
    assert boxes[0].confidence == 0.8


def test_jittery_duplicates_do_not_grow_the_box():
    """Three boxes of one 100 px plate, each IoU 0.43 with the next: one box, still 100 px."""
    boxes = run({None: [P(300, 400, 400, 430, 0.6), P(380, 480, 400, 430, 0.7)],
                 (0, 0): [P(340, 440, 400, 430, 0.9)]})
    assert [corners(b) for b in boxes] == [(340, 440, 400, 430)]


def test_blob_over_two_neighbouring_plates_does_not_join_them():
    """Two plates 20 px apart and one whole-frame box over both (IoU 0.45 with each): both plates
    stay; the blob is a duplicate of the first, not a bridge between them."""
    boxes = run({None: [P(300, 520, 400, 430, 0.5)],
                 (0, 0): [P(300, 400, 400, 430, 0.9), P(420, 520, 400, 430, 0.85)]})
    assert [corners(b) for b in boxes] == [(300, 400, 400, 430), (420, 520, 400, 430)]


def test_large_unrelated_box_does_not_swallow_a_plate():
    """A vehicle-sized whole-frame box contains a plate; their heights disagree, so both stay as
    they are, cut or not."""
    big = P(380, 900, 200, 600, 0.5)
    boxes = run({None: [big], (0, 0): [P(420, 520, 400, 430, 0.9)],
                 (512, 0): [P(512, 600, 450, 480, 0.8)]})
    expected = [corners(big), (420, 520, 400, 430), (512, 600, 450, 480)]
    assert sorted(corners(b) for b in boxes) == sorted(expected)


def test_tall_tile_box_is_never_a_partner():
    """Plate-shaped but taller than half the tile (a smeared night tile): dropped."""
    boxes = run({(0, 0): [P(300, 768, 100, 500, 0.95)], (512, 0): [P(512, 600, 300, 330, 0.8)]})
    assert [corners(b) for b in boxes] == [(512, 600, 300, 330)]


def test_bottom_cut_is_completed_by_the_lower_tile_row():
    """1920 x 1080, 768 px tiles: rows y 0-768 and 312-1080; a plate at y 740-800 is cut by the
    top row."""
    boxes = run({(652, 0): [P(800, 900, 740, 768, 0.9)],
                 (652, 312): [P(800, 900, 740, 800, 0.7)]}, 1920, 1080)
    assert [corners(b) for b in boxes] == [(800, 900, 740, 800)]
    assert boxes[0].confidence == 0.9


def test_same_plate_needs_one_uncut_axis_that_agrees():
    plate = TileBox(P(512, 600, 300, 330), cut="lt")         # cut on both axes (a tile corner)
    big = TileBox(P(400, 900, 100, 700))
    assert not same_plate(plate, big), "nothing to check the containment against: kept apart"
    assert same_plate(TileBox(P(512, 600, 300, 330), cut="l"), TileBox(P(450, 700, 298, 334)))
    assert not same_plate(TileBox(P(512, 600, 300, 330), cut="l"), TileBox(P(450, 700, 250, 400)))


def test_absorb_moves_a_cut_side_out_only():
    piece = TileBox(P(512, 800, 545, 648, 0.75), cut="l")
    out = absorb(piece, TileBox(P(500, 768, 540, 650, 0.7), cut="r"))
    assert corners(out.box) == (500, 800, 545, 648) and out.cut == ""
    inside = absorb(piece, TileBox(P(530, 790, 545, 648, 0.7)))
    assert corners(inside.box) == (512, 800, 545, 648) and inside.cut == "l", "still cut"
    both_cut = absorb(piece, TileBox(P(512, 790, 545, 648, 0.7), cut="l"))
    assert both_cut.cut == "l"


def test_group_of_oversized_boxes_only_is_dropped():
    wide = TileBox(P(300, 760, 500, 620, 0.6), cut="r", oversized=True)
    other = TileBox(P(520, 990, 505, 615, 0.5), cut="l", oversized=True)
    assert merge_boxes([wide, other]) == []
    assert [corners(b) for b in merge_boxes([wide, TileBox(P(520, 700, 505, 615, 0.5), "l")])] \
        == [(300, 760, 500, 620)]
