"""PaddleOCR PP-OCRv4 wrapper for plate crops (CONTRACT.md section 7.7, plan 5.5 step 5).

Pipeline per crop: pad a little context -> upscale x3 (cubic) -> grayscale -> CLAHE -> back to
3-channel -> PaddleOCR with **detection enabled** so two-line plates come back as two text boxes.
Boxes are grouped into lines by their vertical centre; lines are joined with ``"\\n"`` in
``OcrResult.raw`` (the pipeline stores ``plate_raw`` with the newline replaced by a space).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger("anpr.ocr")


@dataclass
class OcrResult:
    raw: str                 # lines joined with "\n"
    lines: list[str]
    confidence: float        # length-weighted mean of the recognised pieces

    @property
    def text(self) -> str:
        return " ".join(self.lines)


def preprocess_crop(crop: np.ndarray, upscale: int = 3) -> np.ndarray:
    """x3 upscale, grayscale, CLAHE; returned as BGR so PaddleOCR gets its expected 3 channels."""
    if crop.size == 0:
        return crop
    h, w = crop.shape[:2]
    big = cv2.resize(crop, (w * upscale, h * upscale), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY) if big.ndim == 3 else big
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)


def group_lines(items: list[tuple[float, float, float, str, float]]) -> list[list[tuple[float, float, float, str, float]]]:
    """Group (cy, cx, height, text, conf) items into text lines by vertical proximity."""
    if not items:
        return []
    items = sorted(items, key=lambda t: (t[0], t[1]))
    lines: list[list[tuple[float, float, float, str, float]]] = [[items[0]]]
    for item in items[1:]:
        current = lines[-1]
        ref_cy = sum(t[0] for t in current) / len(current)
        ref_h = sum(t[2] for t in current) / len(current)
        if abs(item[0] - ref_cy) <= 0.6 * max(ref_h, item[2], 1.0):
            current.append(item)
        else:
            lines.append([item])
    return [sorted(line, key=lambda t: t[1]) for line in lines]


class PlateOCR:
    """Thin PaddleOCR wrapper. Construction loads the models (a few seconds)."""

    def __init__(self, lang: str = "en", cpu: bool = True, threads: int = 2, upscale: int = 3) -> None:
        os.environ.setdefault("FLAGS_use_mkldnn", "0")
        # The zlib extension must be loaded (and, with LD_BIND_NOW=1, bound to libz.so.1) before
        # libpaddle.so enters the global symbol scope with its own zlib copy; otherwise every
        # gzip/tar operation after the Paddle import segfaults in inflateReset2 (README, "Known
        # issues"). The pipeline re-execs itself with LD_BIND_NOW=1; warn if that did not happen.
        import zlib

        zlib.decompress(zlib.compress(b"sentinel"))
        if os.name == "posix" and os.environ.get("LD_BIND_NOW") != "1":
            log.warning("LD_BIND_NOW is not set; PaddleOCR model extraction may crash (see README)")
        root_level = logging.getLogger().level     # paddleocr's import raises the root logger to WARNING
        from paddleocr import PaddleOCR  # imported lazily: heavy, and tests must not need it

        logging.getLogger().setLevel(root_level)
        self.upscale = int(upscale)
        self._ocr = PaddleOCR(
            lang=lang,
            use_angle_cls=False,
            use_gpu=not cpu,
            show_log=False,
            cpu_threads=max(1, int(threads)),
            det_db_box_thresh=0.3,
            det_db_unclip_ratio=1.8,
            rec_batch_num=6,
            use_dilation=True,
        )
        logging.getLogger().setLevel(root_level)
        log.info("PaddleOCR ready (lang=%s, gpu=%s)", lang, not cpu)

    def read(self, crop: np.ndarray) -> OcrResult:
        img = preprocess_crop(crop, self.upscale)
        if img.size == 0:
            return OcrResult("", [], 0.0)
        try:
            result = self._ocr.ocr(img, det=True, rec=True, cls=False)
        except Exception as exc:  # noqa: BLE001 - never kill the loop on an OCR hiccup
            log.warning("PaddleOCR failed: %s", exc)
            return OcrResult("", [], 0.0)
        page = result[0] if result else None
        img_w = img.shape[1]
        items: list[tuple[float, float, float, str, float]] = []
        spans: dict[int, tuple[float, float, float, float]] = {}      # id(item) -> x1, y1, x2, y2
        for entry in page or []:
            try:
                box, (text, conf) = entry[0], entry[1]
            except (TypeError, ValueError, IndexError):
                continue
            if not text or not str(text).strip():
                continue
            pts = np.asarray(box, dtype=np.float32)
            x1, x2 = float(pts[:, 0].min()), float(pts[:, 0].max())
            y1, y2 = float(pts[:, 1].min()), float(pts[:, 1].max())
            width, height = x2 - x1, y2 - y1
            # The Indian plate's vertical "IND" strip comes back as a narrow, taller-than-wide box
            # ("ONI", "INI") glued to the left edge; it is not part of the registration.
            if height > width or width < 0.08 * img_w:
                log.debug("ignoring vertical/narrow OCR box %r (%.0fx%.0f)", text, width, height)
                continue
            item = ((y1 + y2) / 2, (x1 + x2) / 2, height, str(text).strip(), float(conf))
            items.append(item)
            spans[id(item)] = (x1, y1, x2, y2)
        if not items:
            return self._recognise_whole(img)
        lines: list[str] = []
        pieces: list[tuple[str, float]] = []
        for line in group_lines(items):
            if len(line) > 1 and self._overlapping(line, spans):
                # The detector split one text run into overlapping boxes ("GJ 3" + "37"):
                # recognise the whole line strip once instead of concatenating duplicates.
                text, conf = self._recognise_strip(img, [spans[id(t)] for t in line])
                if text:
                    lines.append(text)
                    pieces.append((text, conf))
                    continue
            lines.append(" ".join(t[3] for t in line))
            pieces.extend((t[3], t[4]) for t in line)
        total = sum(len(t) for t, _ in pieces) or 1
        conf = sum(len(t) * c for t, c in pieces) / total
        return OcrResult("\n".join(lines), lines, float(conf))

    @staticmethod
    def _overlapping(line: list[tuple[float, float, float, str, float]], spans: dict[int, tuple[float, float, float, float]]) -> bool:
        """True when any two boxes of a line overlap horizontally by > 15 % of the narrower box."""
        boxes = [spans[id(t)] for t in line]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                overlap = min(a[2], b[2]) - max(a[0], b[0])
                narrow = min(a[2] - a[0], b[2] - b[0])
                if narrow > 0 and overlap > 0.15 * narrow:
                    return True
        return False

    def _recognise_strip(self, img: np.ndarray, boxes: list[tuple[float, float, float, float]]) -> tuple[str, float]:
        """Recognition-only pass over the union of ``boxes`` (one text line), with a little padding."""
        h, w = img.shape[:2]
        x1 = max(0, int(min(b[0] for b in boxes)) - 6)
        y1 = max(0, int(min(b[1] for b in boxes)) - 6)
        x2 = min(w, int(max(b[2] for b in boxes)) + 6)
        y2 = min(h, int(max(b[3] for b in boxes)) + 6)
        if x2 - x1 < 8 or y2 - y1 < 8:
            return "", 0.0
        try:
            result = self._ocr.ocr(img[y1:y2, x1:x2], det=False, rec=True, cls=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("PaddleOCR strip recognition failed: %s", exc)
            return "", 0.0
        page = result[0] if result else None
        if not page:
            return "", 0.0
        text, conf = str(page[0][0]).strip(), float(page[0][1])
        return text, conf

    def _recognise_whole(self, img: np.ndarray) -> OcrResult:
        """Detection found nothing (tight crop): run recognition on the whole crop."""
        try:
            result = self._ocr.ocr(img, det=False, rec=True, cls=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("PaddleOCR rec-only failed: %s", exc)
            return OcrResult("", [], 0.0)
        page = result[0] if result else None
        if not page:
            return OcrResult("", [], 0.0)
        text, conf = page[0][0], page[0][1]
        text = str(text).strip()
        if not text:
            return OcrResult("", [], 0.0)
        return OcrResult(text, [text], float(conf))
