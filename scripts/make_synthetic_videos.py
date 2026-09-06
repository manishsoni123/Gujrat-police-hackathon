#!/usr/bin/env python3
"""Generate the synthetic "sandbox" camera videos and their ground truth (CONTRACT.md section 12.4).

Writes ``cam_<n>.mp4`` (1280x720, 10 fps, 90 s; camera 8 in H.265), ``own_gate.mp4`` (the
synthetic stand-in for the team's private society-gate camera: its own scene colour, gate
pillars and boom, and its own plate set that shares **exactly two** plates with cameras 1-3 so
the "two systems" route demo works on the laptop) and ``plates.json`` into ``SYNTH_OUT``
(default ``/media/synthetic``). Deterministic for a given ``SYNTH_SEED``: the plate set and
schedule are reproduced byte-for-byte in ``plates.json`` except ``generated_at``.

    python scripts/make_synthetic_videos.py [--cameras 8] [--seconds 90] [--seed 42] [--out DIR]
    python scripts/make_synthetic_videos.py --only 99        # re-render only own_gate.mp4 (id 99)

Burnt-in overlay: a small grey OSD strip at the bottom-right, ``cam 1 · sachivalaya gate 1 ·
loop 00:12.3`` (loop position, not a wall clock, so it never contradicts the portal's IST
header), kept away from the player's title chip (top-left) and caption (bottom-left).

Dependencies: numpy, opencv-python-headless, Pillow, an ``ffmpeg`` binary with libx264 (and
libx265 for camera 8; falls back to libx264 and records ``"codec": "H264"``), DejaVu Sans Bold
(``fonts-dejavu-core``). Runs in the ``synth`` and ``anpr`` images. Standalone: no imports from
the worker package.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT, FPS = 1280, 720, 10
APPEARANCE_S = 4.0
SLOT_S = 6.0
ROAD_TOP, ROAD_BOTTOM = 400, 700
VEHICLE_W, VEHICLE_H = 560, 260
PLATE_ONE_LINE = (480, 100)     # w, h (contract: approx. 330x80; widened so the text stays >= 40 px tall)
PLATE_TWO_LINE = (320, 150)
IND_STRIP_W = 28
PLATE_FILL = (245, 245, 245)    # RGB #F5F5F5
NOISE_SIGMA = 3.0

# Fixed anchor schedule (plate, two_line, [(camera, start_s), ...]) - never changes with the seed.
ANCHORS: list[tuple[str, bool, list[tuple[int, float]]]] = [
    ("GJ01AB1234", False, [(1, 5), (3, 25), (6, 50), (2, 72)]),
    ("GJ18CD5678", False, [(4, 10), (7, 35), (8, 60)]),
    ("GJ05RS9012", False, [(2, 15), (5, 40), (1, 65)]),
    ("GJ27XY3456", False, [(6, 8), (3, 30), (7, 55), (4, 80)]),
    ("MH02BZ7788", True, [(8, 20), (6, 45)]),
    ("22BH4321AA", True, [(5, 12), (2, 48), (8, 78)]),
]

# Plates in backend/seeds/watchlist_seed.csv (section 12.2) - fillers must never collide with these.
WATCHLIST_SEED = {
    "GJ01AB1234", "GJ18CD5678", "GJ05RS9012", "MH02BZ7788", "GJ06KL4455", "GJ27XY3456", "GJ01CJ7788",
    "GJ03BM2210", "GJ05JE9834", "GJ12AK1001", "GJ33AT5566", "GJ38CH0007", "DL3CAB9911", "RJ14CV3030",
    "MP09HB6161", "GJ10AD7070", "GJ15CQ2424", "GJ21AR8181", "KA01MJ4545", "GJ09BW0110",
}

CAMERA_LABELS = [
    "Sachivalaya Gate 1", "Sector 11 GSRTC Bus Stand", "Civil Hospital Gandhinagar OPD Gate", "Infocity Circle",
    "Pethapur Gram Panchayat Chowk", "CH-0 Circle", "Mahatma Mandir Approach", "Koba Circle Highway",
]

# Per-camera background gradients (RGB top -> bottom) so every camera looks different.
PALETTE = [
    ((70, 110, 160), (150, 175, 200)), ((120, 90, 60), (200, 170, 140)), ((60, 120, 90), (160, 200, 170)),
    ((110, 70, 120), (190, 160, 200)), ((140, 120, 50), (210, 200, 150)), ((50, 90, 130), (140, 170, 200)),
    ((130, 60, 60), (210, 160, 150)), ((80, 80, 80), (170, 170, 180)),
]
VEHICLE_COLOURS = [(35, 35, 40), (25, 40, 70), (60, 25, 25), (30, 55, 35), (45, 45, 45), (20, 30, 55), (55, 40, 20)]

# Our own private society-gate camera (CONTRACT section 12.3 "Own camera", MediaMTX path `own_gate`).
# Served by deploy/mediamtx.yml when media/own/own_gate.mp4 (the real phone recording) is absent.
# Its scene (dusk green-grey, gate pillars, striped boom) is unlike every stream/<n> palette and its
# plate set is disjoint from the sandbox cameras' except for OWN_GATE_SHARED: exactly two plates
# that also pass cameras 1-3 (the laptop's live ANPR set), so a route query shows the vehicle on
# sandbox cameras and on the own feed - the "two systems" demo. GJ27XY3456 (not active in the
# watchlist; Video 1 adds it live) is visible at 28-32 s; GJ01AB1234 (stolen/critical) at 10-14 s.
OWN_GATE_EVAL_ID = 99            # camera id used by `--only 99` and anpr.tools.eval_synthetic --own-gate
OWN_GATE_FILE = "own_gate.mp4"
OWN_GATE_LABEL = "Dynatech Office Gate"
OWN_GATE_HUD = "own gate · dynatech office gate"
OWN_GATE_PALETTE = ((38, 62, 58), (128, 158, 138))
OWN_GATE_SHARED: list[tuple[str, bool, float]] = [("GJ01AB1234", False, 10.0), ("GJ27XY3456", False, 28.0)]
OWN_GATE_FILLERS = 6             # own plates never seen on any stream/<n> camera (~2 two-line)

SERIES_LETTERS = "ABCDEFGHJKLMNPRSTUVWXYZ"   # no I, O, Q
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/DejaVuSans-Bold.ttf",
]


@dataclass
class Appearance:
    camera_id: int
    plate: str
    start_s: float
    two_line: bool
    direction: str          # ltr | rtl

    @property
    def end_s(self) -> float:
        return self.start_s + APPEARANCE_S


@dataclass
class PlateSpec:
    plate: str
    two_line: bool
    anchor: bool
    cameras: list[int] = field(default_factory=list)


def format_plate(plate: str) -> str:
    import re

    m = re.match(r"^([A-Z]{2})([0-9]{1,2})([A-Z]{1,3})([0-9]{4})$", plate)
    if m:
        return " ".join(m.groups())
    m = re.match(r"^([0-9]{2})(BH)([0-9]{4})([A-Z]{1,2})$", plate)
    if m:
        return " ".join(m.groups())
    return plate


def plate_lines(plate: str, two_line: bool) -> list[str]:
    """Text lines drawn on the plate: one line ``GJ 01 AB 1234`` or two ``MH 02`` / ``BZ 7788``."""
    parts = format_plate(plate).split(" ")
    if not two_line or len(parts) != 4:
        return [" ".join(parts)]
    return [" ".join(parts[:2]), " ".join(parts[2:])]


def random_gujarat_plate(rng: random.Random) -> str:
    district = f"{rng.randint(1, 38):02d}"
    series = "".join(rng.choice(SERIES_LETTERS) for _ in range(rng.choice([1, 2, 2])))
    number = f"{rng.randint(1, 9999):04d}"
    return f"GJ{district}{series}{number}"


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
class Schedule:
    def __init__(self, cameras: int, seconds: float, rng: random.Random, ids: list[int] | None = None) -> None:
        self.cameras = cameras
        self.seconds = seconds
        self.rng = rng
        self.by_camera: dict[int, list[Appearance]] = {c: [] for c in (ids if ids is not None else range(1, cameras + 1))}
        self.specs: dict[str, PlateSpec] = {}

    def _overlaps(self, camera: int, start: float) -> bool:
        end = start + APPEARANCE_S
        for a in self.by_camera[camera]:
            if start < a.end_s + 0.5 and end + 0.5 > a.start_s:
                return True
        return False

    def free_slots(self, camera: int, after: float = -1.0) -> list[float]:
        slots = []
        t = 0.0
        while t + SLOT_S <= self.seconds:
            start = t + 1.0
            if start > after and not self._overlaps(camera, start):
                slots.append(start)
            t += SLOT_S
        return slots

    def add(self, plate: str, two_line: bool, anchor: bool, camera: int, start: float) -> Appearance:
        if camera not in self.by_camera:
            raise ValueError(f"anchor schedule needs camera {camera}; generate at least that many cameras")
        direction = "ltr" if self.rng.random() < 0.5 else "rtl"
        app = Appearance(camera, plate, float(start), two_line, direction)
        self.by_camera[camera].append(app)
        spec = self.specs.setdefault(plate, PlateSpec(plate, two_line, anchor))
        spec.cameras.append(camera)
        return app

    def count(self, camera: int) -> int:
        return len(self.by_camera[camera])

    def appearances(self) -> list[Appearance]:
        out = [a for apps in self.by_camera.values() for a in apps]
        out.sort(key=lambda a: (a.camera_id, a.start_s))
        return out


def build_schedule(cameras: int, seconds: float, seed: int) -> Schedule:
    rng = random.Random(seed)
    sched = Schedule(cameras, seconds, rng)
    for plate, two_line, apps in ANCHORS:
        for camera, start in apps:
            if camera <= cameras:
                sched.add(plate, two_line, True, camera, start)

    # 18 seeded filler plates, distinct from anchors and from every watchlist seed row.
    fillers: list[str] = []
    taken = set(WATCHLIST_SEED) | {a[0] for a in ANCHORS}
    while len(fillers) < 18:
        p = random_gujarat_plate(rng)
        if p not in taken:
            taken.add(p)
            fillers.append(p)
    two_line_flags = [i % 10 in (0, 3, 6) for i in range(18)]     # ~30 % two-line, deterministic
    multi, unique = fillers[:13], fillers[13:]
    cam_ids = list(range(1, cameras + 1))
    max_per_camera = 12

    def place(plate: str, two_line: bool, camera: int, after: float) -> float | None:
        if sched.count(camera) >= max_per_camera:
            return None
        slots = sched.free_slots(camera, after)
        if not slots:
            return None
        start = rng.choice(slots[: max(1, len(slots) // 2)]) if after >= 0 else rng.choice(slots)
        sched.add(plate, two_line, False, camera, start)
        return start

    # multi-camera fillers: 2-4 cameras at increasing time offsets (a plausible route)
    for i, plate in enumerate(multi):
        n_cams = rng.choice([2, 3, 3, 4])
        order = rng.sample(cam_ids, min(n_cams, len(cam_ids)))
        last = -1.0
        for camera in order:
            start = place(plate, two_line_flags[i], camera, last)
            if start is None:
                continue
            last = start + SLOT_S
    for i, plate in enumerate(unique, start=13):
        for camera in rng.sample(cam_ids, len(cam_ids)):
            if place(plate, two_line_flags[i], camera, -1.0) is not None:
                break

    # top-up so every camera carries 8-12 appearances
    for camera in cam_ids:
        candidates = list(multi)
        rng.shuffle(candidates)
        for plate in candidates:
            if sched.count(camera) >= 8:
                break
            if camera in sched.specs[plate].cameras:
                continue
            spec = sched.specs[plate]
            last = max((a.start_s for c in spec.cameras for a in sched.by_camera[c] if a.plate == plate), default=-1.0)
            if place(plate, spec.two_line, camera, last + SLOT_S) is None:
                place(plate, spec.two_line, camera, -1.0)
    return sched


def build_own_gate_schedule(seconds: float, seed: int, sandbox_plates: set[str]) -> Schedule:
    """Own-gate loop: the two shared plates at fixed times plus OWN_GATE_FILLERS plates seen nowhere else.

    Independent RNG stream (``seed * 7919 + 1``) so the sandbox schedule and the own-gate schedule
    never shift each other. Raises when the "exactly two shared plates" rule would be broken.
    """
    rng = random.Random(seed * 7919 + 1)
    sched = Schedule(0, seconds, rng, ids=[OWN_GATE_EVAL_ID])
    shared = {p for p, _tl, _s in OWN_GATE_SHARED}
    for plate, two_line, start in OWN_GATE_SHARED:
        sched.add(plate, two_line, True, OWN_GATE_EVAL_ID, start)
    taken = set(WATCHLIST_SEED) | set(sandbox_plates) | shared
    fillers: list[str] = []
    while len(fillers) < OWN_GATE_FILLERS:
        p = random_gujarat_plate(rng)
        if p not in taken:
            taken.add(p)
            fillers.append(p)
    for i, plate in enumerate(fillers):
        slots = sched.free_slots(OWN_GATE_EVAL_ID, -1.0)
        if not slots:
            break
        sched.add(plate, i % 3 == 1, False, OWN_GATE_EVAL_ID, rng.choice(slots))
    overlap = set(sched.specs) & set(sandbox_plates)
    if overlap != shared:
        raise RuntimeError(f"own_gate must share exactly {sorted(shared)} with the sandbox cameras, got {sorted(overlap)}")
    return sched


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    raise FileNotFoundError("DejaVuSans-Bold.ttf not found; install fonts-dejavu-core")


class PlateRenderer:
    """Renders plate images (RGB numpy) once per (plate, two_line) and caches them."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, bool], np.ndarray] = {}
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._ind_font = load_font(13)

    def font(self, size: int) -> ImageFont.FreeTypeFont:
        if size not in self._fonts:
            self._fonts[size] = load_font(size)
        return self._fonts[size]

    def _fit(self, lines: list[str], max_w: int, max_h: int, start: int = 72, floor: int = 32) -> tuple[ImageFont.FreeTypeFont, int]:
        size = start
        while size > floor:
            f = self.font(size)
            widths = [f.getbbox(t)[2] - f.getbbox(t)[0] for t in lines]
            heights = [f.getbbox(t)[3] - f.getbbox(t)[1] for t in lines]
            if max(widths) <= max_w and sum(heights) + (len(lines) - 1) * 8 <= max_h:
                return f, size
            size -= 2
        return self.font(floor), floor

    def render(self, plate: str, two_line: bool) -> np.ndarray:
        key = (plate, two_line)
        if key in self._cache:
            return self._cache[key]
        w, h = PLATE_TWO_LINE if two_line else PLATE_ONE_LINE
        img = Image.new("RGB", (w, h), PLATE_FILL)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, w - 1, h - 1], outline=(0, 0, 0), width=3)
        draw.rectangle([3, 3, 3 + IND_STRIP_W, h - 4], fill=(20, 40, 140))
        ind = Image.new("RGBA", (40, 16), (0, 0, 0, 0))
        ImageDraw.Draw(ind).text((2, 0), "IND", font=self._ind_font, fill=(255, 255, 255, 255))
        ind = ind.rotate(90, expand=True)
        img.paste(ind, (5, h // 2 - 20), ind)
        lines = plate_lines(plate, two_line)
        text_x0 = 3 + IND_STRIP_W + 8
        text_w = w - text_x0 - 10
        font, _ = self._fit(lines, text_w, h - 16)
        boxes = [font.getbbox(t) for t in lines]
        total_h = sum(b[3] - b[1] for b in boxes) + (len(lines) - 1) * 8
        y = (h - total_h) // 2
        for text, box in zip(lines, boxes):
            tw, th = box[2] - box[0], box[3] - box[1]
            x = text_x0 + (text_w - tw) // 2
            draw.text((x - box[0], y - box[1]), text, font=font, fill=(0, 0, 0))
            y += th + 8
        arr = np.asarray(img, dtype=np.uint8)
        self._cache[key] = arr
        return arr


def make_background(palette: tuple[tuple[int, int, int], tuple[int, int, int]], gate: bool = False) -> np.ndarray:
    """Static RGB background: gradient sky, grey road band with a dashed lane line; gate pillars + boom for the own feed.

    No text lives here any more: the OSD strip (camera name + loop position) is drawn per frame at
    the bottom-right by ``render_camera`` so it neither hides under the player's title chip
    (top-left) nor contradicts the portal's IST clock.
    """
    top, bottom = palette
    t = np.linspace(0.0, 1.0, HEIGHT, dtype=np.float32)[:, None, None]
    bg = (np.array(top, dtype=np.float32) * (1 - t) + np.array(bottom, dtype=np.float32) * t)
    bg = np.repeat(bg, WIDTH, axis=1).astype(np.uint8)
    bg[ROAD_TOP:ROAD_BOTTOM, :] = (72, 72, 74)
    bg[ROAD_TOP:ROAD_TOP + 6, :] = (150, 150, 150)
    lane_y = (ROAD_TOP + ROAD_BOTTOM) // 2
    for x in range(20, WIDTH, 120):
        bg[lane_y - 3:lane_y + 3, x:x + 60] = (175, 175, 175)
    if gate:
        # Society gate: two pillars at the frame edges, a raised red/white boom across the top of the road,
        # a compound wall behind it. Dark and unstriped where plates go, so the contour detector is unaffected.
        bg[ROAD_TOP - 150:ROAD_TOP, :] = (150, 140, 125)                     # compound wall
        for y in range(ROAD_TOP - 150, ROAD_TOP, 30):                         # brick courses
            bg[y:y + 2, :] = (120, 110, 95)
        for x0 in (0, WIDTH - 70):
            bg[ROAD_TOP - 230:ROAD_TOP + 4, x0:x0 + 70] = (95, 85, 75)         # pillars
            bg[ROAD_TOP - 240:ROAD_TOP - 230, x0 - 5 if x0 else 0:x0 + 75] = (60, 55, 50)   # caps
        boom_y = ROAD_TOP - 200
        seg = 48                                                              # < ANPR_MIN_PLATE_W after the 960 px decode, so the white segments are never plate candidates
        for i, x in enumerate(range(70, WIDTH - 70, seg)):
            bg[boom_y:boom_y + 14, x:min(x + seg, WIDTH - 70)] = (200, 55, 45) if i % 2 == 0 else (225, 225, 225)
    return bg


def draw_osd(frame: np.ndarray, hud: str, t: float, font: ImageFont.FreeTypeFont) -> np.ndarray:
    """Bottom-right OSD strip: ``<hud> · loop mm:ss.d`` in small grey on a dark box (RGB in, RGB out)."""
    text = f"{hud} · loop {int(t // 60):02d}:{int(t % 60):02d}.{int((t * 10) % 10)}"
    pil = Image.fromarray(frame)
    draw = ImageDraw.Draw(pil)
    box = font.getbbox(text)
    tw, th = box[2] - box[0], box[3] - box[1]
    x1, y1 = WIDTH - 10, HEIGHT - 8
    x0, y0 = x1 - tw - 16, y1 - th - 10
    draw.rectangle([x0, y0, x1, y1], fill=(28, 28, 30))
    draw.text((x0 + 8 - box[0], y0 + 5 - box[1]), text, font=font, fill=(175, 175, 175))     # below the contour detector's 190 brightness threshold
    return np.asarray(pil, dtype=np.uint8)


def draw_vehicle(frame: np.ndarray, x: int, y: int, w: int, h: int, colour: tuple[int, int, int]) -> None:
    """Filled rounded rectangle body with a lighter window band and two wheels (in-place, RGB)."""
    r = 28
    overlay = frame
    cv2.rectangle(overlay, (x + r, y), (x + w - r, y + h), colour, -1)
    cv2.rectangle(overlay, (x, y + r), (x + w, y + h - r), colour, -1)
    for cx, cy in ((x + r, y + r), (x + w - r, y + r), (x + r, y + h - r), (x + w - r, y + h - r)):
        cv2.circle(overlay, (cx, cy), r, colour, -1)
    window = tuple(min(255, c + 60) for c in colour)
    cv2.rectangle(overlay, (x + int(w * 0.15), y + 18), (x + int(w * 0.85), y + int(h * 0.42)), window, -1)
    wheel_y = y + h - 6
    for wx in (x + int(w * 0.22), x + int(w * 0.78)):
        cv2.circle(overlay, (wx, wheel_y), 30, (15, 15, 15), -1)
        cv2.circle(overlay, (wx, wheel_y), 12, (90, 90, 90), -1)


def paste(frame: np.ndarray, img: np.ndarray, x: int, y: int) -> None:
    h, w = img.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(WIDTH, x + w), min(HEIGHT, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    frame[y0:y1, x0:x1] = img[y0 - y:y1 - y, x0 - x:x1 - x]


def open_encoder(path: Path, codec: str, ffmpeg: str) -> subprocess.Popen[bytes]:
    if codec == "H265":
        # bframes=0: MediaMTX cannot deliver B-frames over WebRTC, so the relayed loops must be B-frame free.
        vcodec = ["-c:v", "libx265", "-preset", "fast", "-crf", "26", "-tag:v", "hvc1", "-x265-params", "log-level=error:bframes=0"]
    else:
        vcodec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-bf", "0"]
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-", "-an", *vcodec, "-g", "20",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def has_encoder(ffmpeg: str, name: str) -> bool:
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(line.split()[1:2] == [name] for line in out.stdout.splitlines() if line.strip())


def render_camera(camera: int, hud: str, apps: list[Appearance], seconds: float, seed: int, out_path: Path,
                  codec: str, ffmpeg: str, plates: PlateRenderer,
                  palette: tuple[tuple[int, int, int], tuple[int, int, int]] | None = None, gate: bool = False) -> str:
    """Render one camera (``camera`` = OWN_GATE_EVAL_ID for the own feed); returns the codec actually used."""
    osd_font = load_font(18)
    background = make_background(palette or PALETTE[(camera - 1) % len(PALETTE)], gate=gate)
    rng = np.random.default_rng(seed * 1000 + camera)
    noise_planes = [rng.normal(0.0, NOISE_SIGMA, (HEIGHT, WIDTH, 3)).astype(np.int16) for _ in range(6)]
    use_codec = codec
    if codec == "H265" and not has_encoder(ffmpeg, "libx265"):
        print(f"cam_{camera}: libx265 unavailable, falling back to libx264", file=sys.stderr)
        use_codec = "H264"
    proc = open_encoder(out_path, use_codec, ffmpeg)
    assert proc.stdin is not None
    total = int(round(seconds * FPS))
    apps_sorted = sorted(apps, key=lambda a: a.start_s)
    colour_for = {a.plate: VEHICLE_COLOURS[sum(map(ord, a.plate)) % len(VEHICLE_COLOURS)] for a in apps_sorted}
    try:
        for i in range(total):
            t = i / FPS
            frame = background.copy()
            for a in apps_sorted:
                if not (a.start_s <= t < a.end_s):
                    continue
                progress = (t - a.start_s) / APPEARANCE_S
                scale = 1.0 + 0.05 * math.sin(2 * math.pi * progress)
                vw, vh = int(VEHICLE_W * scale), int(VEHICLE_H * scale)
                travel = WIDTH + vw
                x = int(-vw + travel * progress) if a.direction == "ltr" else int(WIDTH - travel * progress)
                y = ROAD_BOTTOM - vh - 20
                draw_vehicle(frame, x, y, vw, vh, colour_for[a.plate])
                plate_img = plates.render(a.plate, a.two_line)
                if scale != 1.0:
                    ph, pw = plate_img.shape[:2]
                    plate_img = cv2.resize(plate_img, (int(pw * scale), int(ph * scale)), interpolation=cv2.INTER_AREA)
                ph, pw = plate_img.shape[:2]
                px = x + vw - pw - 30 if a.direction == "ltr" else x + 30
                py = y + vh - ph - 34
                paste(frame, plate_img, px, py)
            noisy = frame.astype(np.int16) + np.roll(noise_planes[i % len(noise_planes)], (i * 37) % WIDTH, axis=1)
            frame = np.clip(noisy, 0, 255).astype(np.uint8)
            proc.stdin.write(draw_osd(frame, hud, t, osd_font).tobytes())
    finally:
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        raise RuntimeError(f"ffmpeg failed for cam_{camera} (exit {rc}): {err.strip()[-400:]}")
    return use_codec


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cameras", type=int, default=int(os.environ.get("SYNTH_CAMERAS", "8")))
    parser.add_argument("--seconds", type=float, default=float(os.environ.get("SYNTH_SECONDS", "90")))
    parser.add_argument("--seed", type=int, default=int(os.environ.get("SYNTH_SEED", "42")))
    parser.add_argument("--out", default=os.environ.get("SYNTH_OUT", "/media/synthetic"))
    parser.add_argument("--only", type=int, action="append", default=[],
                        help=f"render only these camera ids (schedule unchanged); {OWN_GATE_EVAL_ID} = own_gate.mp4")
    parser.add_argument("--no-own-gate", action="store_true", help="skip own_gate.mp4")
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    args = parser.parse_args(argv)

    if args.cameras < 1:
        parser.error("--cameras must be >= 1")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not has_encoder(args.ffmpeg, "libx264"):
        print(f"ffmpeg at {args.ffmpeg!r} has no libx264 encoder", file=sys.stderr)
        return 2

    started = time.monotonic()
    sched = build_schedule(args.cameras, args.seconds, args.seed)
    own = None if args.no_own_gate else build_own_gate_schedule(args.seconds, args.seed, set(sched.specs))
    plates = PlateRenderer()
    generated_at = datetime.now(timezone.utc)
    cameras_meta = []
    for camera in range(1, args.cameras + 1):
        label = CAMERA_LABELS[camera - 1] if camera <= len(CAMERA_LABELS) else f"Synthetic camera {camera}"
        codec = "H265" if camera == 8 else "H264"
        path = out_dir / f"cam_{camera}.mp4"
        if args.only and camera not in args.only:
            cameras_meta.append({"id": camera, "file": path.name, "codec": codec, "label": label})
            continue
        t0 = time.monotonic()
        used = render_camera(camera, f"cam {camera} · {label.lower()}", sched.by_camera[camera], args.seconds, args.seed,
                             path, codec, args.ffmpeg, plates)
        cameras_meta.append({"id": camera, "file": path.name, "codec": used, "label": label})
        print(f"cam_{camera}.mp4  {used}  {sched.count(camera):2d} appearances  {time.monotonic() - t0:5.1f} s")

    own_meta = None
    if own is not None:
        own_apps = own.by_camera[OWN_GATE_EVAL_ID]
        path = out_dir / OWN_GATE_FILE
        if not args.only or OWN_GATE_EVAL_ID in args.only:
            t0 = time.monotonic()
            render_camera(OWN_GATE_EVAL_ID, OWN_GATE_HUD, own_apps, args.seconds, args.seed, path, "H264", args.ffmpeg, plates,
                          palette=OWN_GATE_PALETTE, gate=True)
            print(f"{OWN_GATE_FILE}  H264  {len(own_apps):2d} appearances  {time.monotonic() - t0:5.1f} s")
        own_meta = {
            "file": OWN_GATE_FILE, "codec": "H264", "label": OWN_GATE_LABEL, "eval_camera_id": OWN_GATE_EVAL_ID,
            "mediamtx_path": "own_gate", "external_id": "OWN-GATE-01",
            "shared_with_streams": [
                {"plate": p, "display": format_plate(p), "start_s": s, "stream_cameras": sched.specs[p].cameras}
                for p, _tl, s in OWN_GATE_SHARED
            ],
            "plates": [
                {"plate": spec.plate, "display": format_plate(spec.plate), "two_line": spec.two_line, "shared": spec.anchor}
                for spec in sorted(own.specs.values(), key=lambda s: (not s.anchor, s.plate))
            ],
            "appearances": [
                {"plate": a.plate, "start_s": a.start_s, "end_s": a.end_s, "two_line": a.two_line, "direction": a.direction}
                for a in sorted(own_apps, key=lambda a: a.start_s)
            ],
        }

    plates_json = {
        "generated_at": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed": args.seed,
        "width": WIDTH, "height": HEIGHT, "fps": FPS, "loop_seconds": int(args.seconds),
        "osd": "bottom-right strip '<camera> · loop mm:ss.d' (loop position, not a wall clock)",
        "cameras": cameras_meta,
        "own_gate": own_meta,
        "plates": [
            {"plate": spec.plate, "display": format_plate(spec.plate), "two_line": spec.two_line,
             "anchor": spec.anchor, "cameras": spec.cameras}
            for spec in sorted(sched.specs.values(), key=lambda s: (not s.anchor, s.plate))
        ],
        "appearances": [
            {"camera_id": a.camera_id, "plate": a.plate, "start_s": a.start_s, "end_s": a.end_s,
             "two_line": a.two_line, "direction": a.direction}
            for a in sched.appearances()
        ],
    }
    (out_dir / "plates.json").write_text(json.dumps(plates_json, indent=2) + "\n", encoding="utf-8")

    print("\ncamera  appearances  plates")
    for camera in range(1, args.cameras + 1):
        apps = sorted(sched.by_camera[camera], key=lambda a: a.start_s)
        print(f"  {camera:<5} {len(apps):>11}  " + ", ".join(f"{a.plate}@{a.start_s:.0f}s" for a in apps))
    if own is not None:
        apps = sorted(own.by_camera[OWN_GATE_EVAL_ID], key=lambda a: a.start_s)
        print(f"  own   {len(apps):>11}  " + ", ".join(f"{a.plate}@{a.start_s:.0f}s" for a in apps)
              + f"   (shared with stream cameras: {', '.join(p for p, _tl, _s in OWN_GATE_SHARED)})")
    multi = sum(1 for s in sched.specs.values() if len(s.cameras) >= 3)
    unique = sum(1 for s in sched.specs.values() if len(s.cameras) == 1)
    print(f"\n{len(sched.specs)} plates ({multi} on >= 3 cameras, {unique} unique), "
          f"{len(sched.appearances())} appearances, {time.monotonic() - started:.0f} s -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
