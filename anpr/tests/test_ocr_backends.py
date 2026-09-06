"""OCR module: crop enhancement (gamma, polarity, CLAHE), the ensemble decision rule, backend selection (no models needed)."""
import cv2
import numpy as np
import pytest

from anpr.config import load_settings
from anpr.ocr import OCR_BACKENDS, EnsembleOCR, OcrResult, build_ocr, enhance_crop, gamma_for, polarity_dark_text


def plate(text: str = "GJ01AB1234", w: int = 120, dark_on_light: bool = True, level: int = 235) -> np.ndarray:
    h = int(w * 0.3)
    bg, fg = ((level, level, level), (10, 10, 10)) if dark_on_light else ((10, 10, 10), (level, level, level))
    img = np.full((h + 12, w + 12, 3), (35, 35, 35), np.uint8)          # a little dark road context
    cv2.rectangle(img, (6, 6), (6 + w, 6 + h), bg, -1)
    cv2.putText(img, text, (8, 6 + h - 5), cv2.FONT_HERSHEY_SIMPLEX, w / 210.0, fg, max(1, w // 70))
    return img


def test_polarity_detection_on_normal_and_inverted_plates():
    assert polarity_dark_text(cv2.cvtColor(plate(), cv2.COLOR_BGR2GRAY)) is True
    assert polarity_dark_text(cv2.cvtColor(plate(dark_on_light=False), cv2.COLOR_BGR2GRAY)) is False


def test_gamma_lifts_dark_crops_only():
    assert gamma_for(200.0) == 1.0
    g = gamma_for(40.0)
    assert 0.4 <= g < 1.0
    assert gamma_for(5.0) == 0.4, "clamped at the floor"


def test_enhance_crop_upscales_corrects_dark_and_inverts_white_on_black():
    normal = plate()
    out, meta = enhance_crop(normal, upscale=4.0)
    assert out.ndim == 3 and out.shape[1] == (normal.shape[1] + 12) * 4, "x4 cubic after 6 px replicate padding"
    assert meta["gamma"] == 1.0 and meta["inverted"] is False
    dark = (plate(level=90).astype(np.float32) * 0.35).astype(np.uint8)          # a night crop: mean well under 90
    out_d, meta_d = enhance_crop(dark)
    assert meta_d["gamma"] < 1.0 and out_d.mean() > dark.mean() * 1.3, "gamma correction brightened the crop"
    inv = plate(dark_on_light=False)
    out_i, meta_i = enhance_crop(inv)
    assert meta_i["inverted"] is True
    assert polarity_dark_text(cv2.cvtColor(out_i, cv2.COLOR_BGR2GRAY)) is True, "white-on-black became dark-on-light"
    wide = np.zeros((40, 400, 3), np.uint8)
    out_w, meta_w = enhance_crop(wide, upscale=4.0, max_width=640)
    assert out_w.shape[1] <= 660 and meta_w["scale"] < 4.0, "upscale is capped at max_width"
    empty, meta_e = enhance_crop(np.zeros((0, 0, 3), np.uint8))
    assert empty.size == 0 and meta_e["gamma"] == 1.0


class Fake:
    def __init__(self, name: str, raw: str, conf: float) -> None:
        self.name, self.raw, self.conf = name, raw, conf

    def read(self, crop):
        return OcrResult(self.raw, [self.raw] if self.raw else [], self.conf, self.name)


def test_ensemble_agreement_valid_beats_invalid_and_min_conf():
    crop = plate()
    agree = EnsembleOCR(Fake("a", "GJ 01 AB 1234", 0.6), Fake("b", "GJ01AB1234", 0.7), min_conf=0.45, bonus=0.1)
    r = agree.read(crop)
    assert r.raw == "GJ01AB1234" and r.confidence == pytest.approx(0.8) and r.meta["agree"] is True and agree.agreements == 1
    valid_wins = EnsembleOCR(Fake("a", "2314:16", 0.95), Fake("b", "GJ05RS9012", 0.5), min_conf=0.45)
    r = valid_wins.read(crop)
    assert r.raw == "GJ05RS9012" and r.meta["chosen"] == "b", "a valid-format plate beats a more confident invalid string"
    higher = EnsembleOCR(Fake("a", "GJ01AB1234", 0.55), Fake("b", "GJ01AB1284", 0.9), min_conf=0.45)
    assert higher.read(crop).raw == "GJ01AB1284", "both valid but different -> the higher confidence"
    weak = EnsembleOCR(Fake("a", "ABC", 0.2), Fake("b", "", 0.0), min_conf=0.45)
    r = weak.read(crop)
    assert r.raw == "" and r.meta.get("below_min_conf") is True
    nothing = EnsembleOCR(Fake("a", "", 0.0), Fake("b", "", 0.0))
    assert nothing.read(crop).raw == ""


def test_backend_selection_and_settings():
    assert OCR_BACKENDS == ("paddle", "fast_plate", "ensemble")
    with pytest.raises(RuntimeError):
        build_ocr("tesseract", weights_dir="/nowhere")
    with pytest.raises(RuntimeError):
        build_ocr("fast_plate", weights_dir="/nowhere", fast_model="no-such-model")
    with pytest.raises(RuntimeError):
        build_ocr("fast_plate", weights_dir="/nowhere")          # listed model, files missing -> refused, not degraded
    s = load_settings([], environ={"ANPR_OCR": "ensemble", "ANPR_OCR_MIN_W": "72", "ANPR_DETECT_WIDTH": "1280",
                                   "FRAME_WIDTH": "0", "ANPR_BEST_SHOT": "0", "ANPR_EVIDENCE_DIR": "/app/media/anpr_evidence"})
    assert (s.ocr_backend, s.ocr_min_w, s.detect_width, s.frame_width, s.best_shot, s.evidence_dir) == \
        ("ensemble", 72, 1280, 0, False, "/app/media/anpr_evidence")
    assert load_settings(["--ocr", "fast_plate"], environ={}).ocr_backend == "fast_plate"
    with pytest.raises(ValueError):
        load_settings([], environ={"ANPR_OCR": "easyocr"})
