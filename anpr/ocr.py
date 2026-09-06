"""Plate OCR backends (CONTRACT.md section 7.7; real-feed accuracy pass, 5 Sept 2026).

``ANPR_OCR`` selects the backend (:func:`build_ocr`):

* ``paddle``     - PaddleOCR (English PP-OCRv3 det + PP-OCRv4 rec) on an enhanced crop
                   (:func:`enhance_crop`: border padding, x4 cubic upscale, gamma correction for dark
                   crops, polarity inversion for white-on-black IR plates, CLAHE). Single-line crops
                   go straight to recognition (``rec`` only); tall crops run det + rec so two-line
                   plates come back as two text lines joined with ``"\\n"``.
* ``fast_plate``  - `fast-plate-ocr <https://github.com/ankandrew/fast-plate-ocr>`_ (MIT), a
                   plate-specific recogniser (``global_mobile_vit_v2_ocr`` by default, or the CCT
                   ``cct_s_v2_global`` model) run through onnxruntime on the raw crop; the model files
                   are hash-verified against ``weights/HASHES.txt`` like the detector weights.
* ``ensemble``    - both: the result they agree on (same normalised plate) wins with a confidence
                   bonus; otherwise a valid-format read beats an invalid one and the higher
                   confidence wins, subject to ``min_conf``.

Every backend returns an :class:`OcrResult` (``raw`` with lines joined by ``"\\n"``,
``confidence`` 0-1, ``backend`` tag). The accuracy of each backend on the labelled crop bank of
the organiser cameras is in ``docs/anpr-accuracy.md`` (``scripts/eval_ocr_bank.py``).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import cv2
import numpy as np

from anpr.normalise import normalise

log = logging.getLogger("anpr.ocr")

OCR_BACKENDS = ("paddle", "fast_plate", "ensemble")
#: fast-plate-ocr models shipped in /app/weights (file stem -> config file); both MIT, hashes in HASHES.txt
FAST_PLATE_MODELS = {
    "global_mobile_vit_v2_ocr": "global_mobile_vit_v2_ocr_config.yaml",
    "cct_s_v2_global": "cct_s_v2_global_plate_config.yaml",
    "cct_xs_v2_global": "cct_xs_v2_global_plate_config.yaml",
}
DEFAULT_FAST_MODEL = "global_mobile_vit_v2_ocr"


@dataclass
class OcrResult:
    raw: str                 # lines joined with "\n"
    lines: list[str]
    confidence: float        # length-weighted mean of the recognised pieces
    backend: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return " ".join(self.lines)


class OcrBackend(Protocol):
    name: str

    def read(self, crop: np.ndarray) -> OcrResult: ...


# ---------------------------------------------------------------------------
# Crop enhancement
# ---------------------------------------------------------------------------
def polarity_dark_text(gray: np.ndarray, min_light: float = 0.3) -> bool:
    """True when the plate has dark characters on a light background (normal), False for white-on-black.

    Judged on the inner 70 % of the crop (the context border is dark road at night): after Otsu's
    threshold, a normal plate keeps at least ``min_light`` of its pixels light even with bold text
    and a dark border; a white-on-black plate (IR reflection, black commercial plate) does not.
    The threshold is deliberately low: inverting a normal plate is far more harmful than leaving
    a white-on-black one alone (measured on the organiser crops, ``docs/anpr-accuracy.md``).
    """
    h, w = gray.shape[:2]
    inner = gray[int(h * 0.15):max(int(h * 0.15) + 1, int(h * 0.85)), int(w * 0.15):max(int(w * 0.15) + 1, int(w * 0.85))]
    if inner.size < 16:
        return True
    thr, _ = cv2.threshold(inner, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    light = float((inner > thr).mean())
    return light >= min_light


def gamma_for(mean: float, target: float = 0.5, lo: float = 0.4, hi: float = 1.0) -> float:
    """Gamma that lifts a crop whose mean intensity is ``mean``/255 to ``target`` (1.0 = no change)."""
    m = min(0.98, max(0.02, mean / 255.0))
    if m >= target:
        return 1.0
    return float(min(hi, max(lo, np.log(target) / np.log(m))))


def enhance_crop(crop: np.ndarray, upscale: float = 4.0, max_width: int = 640, pad: int = 6, *, gamma: bool = True,
                 invert: bool = True, clahe: bool = True) -> tuple[np.ndarray, dict[str, Any]]:
    """Preprocess a plate crop for a general-purpose OCR model; returns (BGR image, what was done).

    Steps: replicate-pad ``pad`` px (characters touching the crop edge lose their strokes in the
    resize), cubic upscale ``upscale`` x capped at ``max_width`` px, grayscale, gamma correction when
    the crop is dark (night, no plate light), inversion when the text is light on dark, CLAHE.
    """
    meta: dict[str, Any] = {"gamma": 1.0, "inverted": False, "scale": 1.0}
    if crop is None or crop.size == 0:
        return crop, meta
    h, w = crop.shape[:2]
    scale = min(float(upscale), max(1.0, max_width / max(1, w)))
    padded = cv2.copyMakeBorder(crop, pad, pad, pad, pad, cv2.BORDER_REPLICATE) if pad > 0 else crop
    big = cv2.resize(padded, (int(round(padded.shape[1] * scale)), int(round(padded.shape[0] * scale))), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY) if big.ndim == 3 else big
    meta["scale"] = round(scale, 2)
    meta["mean"] = round(float(gray.mean()), 1)
    if gamma:
        g = gamma_for(float(gray.mean()))
        if g < 1.0:
            lut = np.array([((i / 255.0) ** g) * 255 for i in range(256)], dtype=np.uint8)
            gray = cv2.LUT(gray, lut)
            meta["gamma"] = round(g, 2)
    if invert and not polarity_dark_text(gray):
        gray = cv2.bitwise_not(gray)
        meta["inverted"] = True
    if clahe:
        gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), meta


def preprocess_crop(crop: np.ndarray, upscale: int = 3) -> np.ndarray:
    """The pre-5-Sept preprocessing (x3 cubic, grayscale, CLAHE) - kept for the accuracy comparison."""
    if crop.size == 0:
        return crop
    h, w = crop.shape[:2]
    big = cv2.resize(crop, (w * upscale, h * upscale), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY) if big.ndim == 3 else big
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
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


# ---------------------------------------------------------------------------
# PaddleOCR
# ---------------------------------------------------------------------------
class PlateOCR:
    """PaddleOCR wrapper (backend ``paddle``). Construction loads the models (a few seconds).

    ``mode``: ``det`` (default: det + rec on every crop - the detection stage's tight text-line
    box is what makes PP-OCRv4 read a plate; recognition on the whole padded crop misreads),
    ``auto`` (single-line crops, h/w < ``two_line_aspect``, go straight to recognition; taller crops
    run det + rec) or ``rec`` (always recognition only). ``legacy=True`` uses the old x3/CLAHE
    preprocessing (for the accuracy comparison only).
    """

    name = "paddle"

    def __init__(self, lang: str = "en", cpu: bool = True, threads: int = 2, upscale: float = 4.0, *,
                 mode: str = "det", legacy: bool = False, two_line_aspect: float = 0.45,
                 gamma: bool = True, invert: bool = True) -> None:
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
        self.upscale = float(upscale)
        self.mode = mode if mode in ("auto", "det", "rec") else "det"
        self.legacy = bool(legacy)
        self.two_line_aspect = float(two_line_aspect)
        self.gamma = bool(gamma)
        self.invert = bool(invert)
        self.name = "paddle_legacy" if legacy else "paddle"
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
        log.info("PaddleOCR ready (lang=%s, gpu=%s, mode=%s, legacy=%s)", lang, not cpu, self.mode, self.legacy)

    def read(self, crop: np.ndarray) -> OcrResult:
        if crop is None or crop.size == 0:
            return OcrResult("", [], 0.0, self.name)
        if self.legacy:
            img, meta = preprocess_crop(crop, 3), {"legacy": True}
        else:
            img, meta = enhance_crop(crop, self.upscale, gamma=self.gamma, invert=self.invert)
        h, w = crop.shape[:2]
        single_line = h / max(1, w) < self.two_line_aspect
        if self.mode == "rec" or (self.mode == "auto" and single_line and not self.legacy):
            result = self._recognise_whole(img)
        else:
            result = self._read_det(img)
        result.backend = self.name
        result.meta = meta
        return result

    def _read_det(self, img: np.ndarray) -> OcrResult:
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
        """Recognition on the whole crop (single-line plates, or detection found nothing)."""
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


# ---------------------------------------------------------------------------
# fast-plate-ocr
# ---------------------------------------------------------------------------
class FastPlateOCR:
    """fast-plate-ocr recogniser (backend ``fast_plate``): one ONNX forward pass per crop (~5 ms CPU).

    The model resizes the crop itself (no aspect preservation, ``img_width x img_height`` from its
    config) and outputs one character per slot with a softmax; ``confidence`` is the mean softmax
    of the non-pad characters. ``enhance=True`` feeds the :func:`enhance_crop` image (gamma /
    inversion / CLAHE) instead of the raw crop. Model and config are verified against
    ``weights/HASHES.txt``; a missing or mismatched file raises ``RuntimeError``.
    """

    name = "fast_plate"

    def __init__(self, weights_dir: str, model: str = DEFAULT_FAST_MODEL, cpu: bool = True, threads: int = 2, *,
                 enhance: bool = False) -> None:
        from anpr.weights.download import verify_weight_file

        if model not in FAST_PLATE_MODELS:
            raise RuntimeError(f"ANPR_OCR_FAST_MODEL={model!r} is not one of {', '.join(FAST_PLATE_MODELS)}")
        model_path = os.path.join(weights_dir, f"{model}.onnx")
        config_path = os.path.join(weights_dir, FAST_PLATE_MODELS[model])
        for path in (model_path, config_path):
            status = verify_weight_file(path)
            if not status.ok:
                raise RuntimeError(f"fast-plate-ocr weights unavailable: {status.describe()}")
            log.info("fast-plate-ocr weights: %s", status.describe())
        import onnxruntime as ort
        from fast_plate_ocr import LicensePlateRecognizer

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, int(threads))
        opts.log_severity_level = 3
        providers = ["CPUExecutionProvider"]
        if not cpu and "CUDAExecutionProvider" in ort.get_available_providers():
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self._rec = LicensePlateRecognizer(onnx_model_path=model_path, plate_config_path=config_path,
                                           providers=providers, sess_options=opts)
        self.model = model
        self.enhance = bool(enhance)
        self.pad_char = self._rec.config.pad_char
        self.rgb = self._rec.config.image_color_mode == "rgb"
        self.name = "fast_plate" if not enhance else "fast_plate_enh"
        log.info("fast-plate-ocr ready: %s (%s input %dx%d)", model, "rgb" if self.rgb else "grayscale",
                 self._rec.config.img_width, self._rec.config.img_height)

    def _input(self, crop: np.ndarray) -> np.ndarray:
        img = crop
        if self.enhance:
            img, _ = enhance_crop(crop, upscale=2.0, max_width=320)
        if self.rgb:
            return cv2.cvtColor(img, cv2.COLOR_BGR2RGB) if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img

    def read(self, crop: np.ndarray) -> OcrResult:
        if crop is None or crop.size == 0 or crop.shape[0] < 4 or crop.shape[1] < 4:
            return OcrResult("", [], 0.0, self.name)
        try:
            pred = self._rec.run(self._input(crop), return_confidence=True)[0]
        except Exception as exc:  # noqa: BLE001
            log.warning("fast-plate-ocr failed: %s", exc)
            return OcrResult("", [], 0.0, self.name)
        text = str(pred.plate or "").replace(self.pad_char, "").strip()
        probs = np.asarray(pred.char_probs if pred.char_probs is not None else [], dtype=np.float32).ravel()
        n = len(text)
        conf = float(probs[:n].mean()) if n and probs.size >= n else (float(probs.mean()) if probs.size else 0.0)
        meta: dict[str, Any] = {"model": self.model}
        if n and probs.size >= n:
            meta["min_char_conf"] = round(float(probs[:n].min()), 3)
        if getattr(pred, "region", None):
            meta["region"] = pred.region
        if not text:
            return OcrResult("", [], 0.0, self.name, meta)
        return OcrResult(text, [text], conf, self.name, meta)


# ---------------------------------------------------------------------------
# Ensemble
# ---------------------------------------------------------------------------
class EnsembleOCR:
    """Agreement-first combination of two backends (backend ``ensemble``).

    Both read the crop. Decision on the *normalised* plates (CONTRACT section 3.4):

    1. same normalised plate -> that plate, confidence ``max + agreement bonus`` (capped at 1);
    2. only one is a valid-format plate -> that one (if its confidence >= ``min_conf``);
    3. otherwise the higher confidence, if >= ``min_conf``; else the empty result.

    ``meta`` records both raw readings so a disagreement is visible in the logs.
    """

    name = "ensemble"

    def __init__(self, primary: OcrBackend, secondary: OcrBackend, *, min_conf: float = 0.45, bonus: float = 0.1) -> None:
        self.primary = primary
        self.secondary = secondary
        self.min_conf = float(min_conf)
        self.bonus = float(bonus)
        self.agreements = 0
        self.disagreements = 0

    def read(self, crop: np.ndarray) -> OcrResult:
        a = self.primary.read(crop)
        b = self.secondary.read(crop)
        na, nb = normalise(a.raw) if a.raw else None, normalise(b.raw) if b.raw else None
        meta = {"a": {"backend": a.backend, "raw": a.raw, "conf": round(a.confidence, 3)},
                "b": {"backend": b.backend, "raw": b.raw, "conf": round(b.confidence, 3)}}
        if na and nb and na.plate_norm and na.plate_norm == nb.plate_norm:
            self.agreements += 1
            best = a if a.confidence >= b.confidence else b
            return OcrResult(best.raw, best.lines, min(1.0, max(a.confidence, b.confidence) + self.bonus), "ensemble",
                             {**meta, "agree": True})
        self.disagreements += 1
        candidates = [(a, na), (b, nb)]
        valid = [(r, n) for r, n in candidates if n is not None and n.is_valid_format]
        pool = valid if valid else [(r, n) for r, n in candidates if n is not None and n.plate_norm]
        if not pool:
            return OcrResult("", [], 0.0, "ensemble", meta)
        r, _ = max(pool, key=lambda p: p[0].confidence)
        if r.confidence < self.min_conf:
            return OcrResult("", [], 0.0, "ensemble", {**meta, "agree": False, "below_min_conf": True})
        return OcrResult(r.raw, r.lines, r.confidence, "ensemble", {**meta, "agree": False, "chosen": r.backend})


def build_ocr(backend: str, *, weights_dir: str, lang: str = "en", cpu: bool = True, threads: int = 2,
              fast_model: str = DEFAULT_FAST_MODEL, paddle_mode: str = "det", ensemble_min_conf: float = 0.45) -> OcrBackend:
    """Instantiate the ``ANPR_OCR`` backend; unknown names raise ``RuntimeError``."""
    if backend == "paddle":
        return PlateOCR(lang=lang, cpu=cpu, threads=threads, mode=paddle_mode)
    if backend == "fast_plate":
        return FastPlateOCR(weights_dir, fast_model, cpu=cpu, threads=threads)
    if backend == "ensemble":
        return EnsembleOCR(PlateOCR(lang=lang, cpu=cpu, threads=threads, mode=paddle_mode),
                           FastPlateOCR(weights_dir, fast_model, cpu=cpu, threads=threads), min_conf=ensemble_min_conf)
    raise RuntimeError(f"ANPR_OCR={backend!r} is not one of {', '.join(OCR_BACKENDS)}")
