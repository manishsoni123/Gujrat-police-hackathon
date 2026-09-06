#!/usr/bin/env python3
"""Score the OCR backends on the labelled crop bank (``media/anpr_crops/labels.csv``).

Runs inside the ``anpr`` image with ``media/`` mounted (models: PaddleOCR from the image,
fast-plate-ocr weights from ``/app/weights``)::

    docker run --rm -v $PWD/media:/app/media -v $PWD/anpr:/app/anpr:ro -v $PWD/scripts:/app/scripts:ro \\
        --entrypoint python sentinel-anpr:cpu /app/scripts/eval_ocr_bank.py \\
        --bank /app/media/anpr_crops --out /app/media/anpr_crops/eval.json

``labels.csv`` columns: ``file`` (relative to the bank), ``plate`` (what a person read, normalised
with ``anpr.normalise`` before scoring), optional ``note``. Rows whose ``plate`` is empty are
skipped (unreadable crops are *not* labelled). Per backend the script reports exact match on the
normalised plate, character accuracy (``1 - levenshtein / len(truth)``, floored at 0), the share of
reads that are valid-format, mean confidence, ms per call, and - on every crop of the bank, not
only the labelled ones - how many produce a valid-format string (a weak signal: a valid-looking
wrong plate counts too, which is why the labelled exact-match figure is the one that matters).

Backends compared (``--backends`` to restrict): ``paddle_legacy`` (pre-5-Sept x3/CLAHE, det+rec),
``paddle`` (enhanced crop, det+rec = the production default), ``paddle_auto`` (enhanced, rec-only on single-line crops),
``fast_plate`` (global_mobile_vit_v2), ``fast_plate_enh`` (same on the enhanced crop),
``fast_plate_cct`` (cct_s_v2_global), ``ensemble`` (paddle + fast_plate), ``ensemble_cct``.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any

import cv2

sys.path.insert(0, "/app")
from anpr.normalise import levenshtein, normalise  # noqa: E402


def load_labels(path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(positives with a plate text, negatives = crops a person judged 'not a plate': signs, captions, structures)."""
    with path.open("r", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    positives = [r for r in rows if (r.get("plate") or "").strip()]
    negatives = [r for r in rows if not (r.get("plate") or "").strip() and (r.get("note") or "").lower().startswith("not a plate")]
    return positives, negatives


def load_manifest(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build(name: str, weights: str, threads: int) -> Any:
    from anpr.ocr import EnsembleOCR, FastPlateOCR, PlateOCR

    if name == "paddle_legacy":
        return PlateOCR(threads=threads, legacy=True, mode="det")
    if name == "paddle":
        return PlateOCR(threads=threads, mode="det")              # production default: enhanced crop, det+rec
    if name == "paddle_auto":
        return PlateOCR(threads=threads, mode="auto")             # recognition only on single-line crops
    if name == "paddle_rec":
        return PlateOCR(threads=threads, mode="rec")
    if name == "paddle_noinv":
        return PlateOCR(threads=threads, mode="det", invert=False)
    if name == "paddle_plain":
        return PlateOCR(threads=threads, mode="det", invert=False, gamma=False)
    if name == "fast_plate":
        return FastPlateOCR(weights, "global_mobile_vit_v2_ocr", threads=threads)
    if name == "fast_plate_enh":
        return FastPlateOCR(weights, "global_mobile_vit_v2_ocr", threads=threads, enhance=True)
    if name == "fast_plate_cct":
        return FastPlateOCR(weights, "cct_s_v2_global", threads=threads)
    if name == "fast_plate_cct_xs":
        return FastPlateOCR(weights, "cct_xs_v2_global", threads=threads)
    if name == "ensemble":
        return EnsembleOCR(PlateOCR(threads=threads, mode="det"), FastPlateOCR(weights, "global_mobile_vit_v2_ocr", threads=threads))
    if name == "ensemble_cct":
        return EnsembleOCR(PlateOCR(threads=threads, mode="det"), FastPlateOCR(weights, "cct_s_v2_global", threads=threads))
    if name == "ensemble_cct_xs":
        return EnsembleOCR(PlateOCR(threads=threads, mode="det"), FastPlateOCR(weights, "cct_xs_v2_global", threads=threads))
    raise SystemExit(f"unknown backend {name}")


ALL = ["paddle_legacy", "paddle", "paddle_auto", "paddle_noinv", "paddle_plain", "fast_plate", "fast_plate_enh", "fast_plate_cct",
       "fast_plate_cct_xs", "ensemble", "ensemble_cct", "ensemble_cct_xs"]


def score(backend: Any, name: str, labels: list[dict[str, str]], negatives: list[dict[str, str]], bank: Path,
          manifest: list[dict[str, str]], min_w: int) -> dict[str, Any]:
    # negatives: signboards / burnt-in captions the detector boxed - a backend must not turn them into plates
    neg: list[dict[str, Any]] = []
    for row in negatives:
        img = cv2.imread(str(bank / row["file"]))
        if img is None:
            continue
        res = backend.read(img)
        norm = normalise(res.raw) if res.raw else None
        neg.append({"file": row["file"], "raw": res.raw.replace("\n", " ") if res.raw else "", "got": norm.plate_norm if norm else "",
                    "valid": bool(norm and norm.is_valid_format), "has_digit": any(ch.isdigit() for ch in (norm.plate_norm if norm else "")),
                    "conf": round(float(res.confidence), 3), "note": row.get("note", "")})
    per: list[dict[str, Any]] = []
    for row in labels:
        img = cv2.imread(str(bank / row["file"]))
        if img is None:
            continue
        truth = normalise(row["plate"]).plate_norm
        t0 = time.perf_counter()
        res = backend.read(img)
        ms = (time.perf_counter() - t0) * 1000
        norm = normalise(res.raw) if res.raw else None
        got = norm.plate_norm if norm else ""
        dist = levenshtein(truth, got)
        per.append({"file": row["file"], "truth": truth, "raw": res.raw.replace("\n", " ") if res.raw else "", "got": got,
                    "valid": bool(norm and norm.is_valid_format), "exact": got == truth,
                    "char_acc": max(0.0, 1.0 - dist / max(1, len(truth))), "conf": round(float(res.confidence), 3), "ms": round(ms, 1),
                    "width": int(row.get("width_px") or img.shape[1]), "meta": res.meta})
    n = len(per)
    wide = [p for p in per if p["width"] >= min_w]
    # unlabelled bank: valid-format share (weak signal)
    bank_valid = bank_reads = bank_n = 0
    bank_ms: list[float] = []
    for row in manifest:
        img = cv2.imread(str(bank / row["file"]))
        if img is None or int(row.get("width_px") or 0) < min_w:
            continue
        bank_n += 1
        t0 = time.perf_counter()
        res = backend.read(img)
        bank_ms.append((time.perf_counter() - t0) * 1000)
        if res.raw:
            bank_reads += 1
            if normalise(res.raw).is_valid_format:
                bank_valid += 1
    return {
        "backend": name, "labelled": n, "exact": sum(p["exact"] for p in per), "exact_rate": round(sum(p["exact"] for p in per) / n, 3) if n else None,
        "char_acc": round(mean(p["char_acc"] for p in per), 3) if n else None,
        "valid_format_rate": round(sum(p["valid"] for p in per) / n, 3) if n else None,
        "mean_conf": round(mean(p["conf"] for p in per), 3) if n else None,
        "mean_conf_exact": round(mean(p["conf"] for p in per if p["exact"]), 3) if any(p["exact"] for p in per) else None,
        "mean_conf_wrong": round(mean(p["conf"] for p in per if not p["exact"]), 3) if any(not p["exact"] for p in per) else None,
        "ms_per_call": round(mean(p["ms"] for p in per), 1) if n else None,
        f"exact_rate_ge{min_w}": round(sum(p["exact"] for p in wide) / len(wide), 3) if wide else None,
        f"labelled_ge{min_w}": len(wide),
        "bank_crops_ge_min_w": bank_n, "bank_reads": bank_reads, "bank_valid_format": bank_valid,
        "bank_ms_per_call": round(mean(bank_ms), 1) if bank_ms else None,
        "negatives": len(neg), "neg_reads": sum(1 for p in neg if p["raw"]), "neg_false_valid": sum(p["valid"] for p in neg),
        "neg_false_valid_conf_ge_045": sum(1 for p in neg if p["valid"] and p["conf"] >= 0.45),
        "neg_reads_without_digit": sum(1 for p in neg if p["raw"] and not p["has_digit"]),
        "per_crop": per, "per_negative": neg,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bank", default="/app/media/anpr_crops")
    parser.add_argument("--labels", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--weights", default=os.environ.get("ANPR_WEIGHTS_DIR", "/app/weights"))
    parser.add_argument("--backends", default=",".join(ALL))
    parser.add_argument("--min-w", type=int, default=60)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args(argv)
    bank = Path(args.bank)
    labels, negatives = load_labels(Path(args.labels) if args.labels else bank / "labels.csv")
    manifest = load_manifest(bank / "manifest.csv")
    widths = {r["file"]: r.get("width_px") for r in manifest}
    for row in labels:
        row.setdefault("width_px", widths.get(row["file"], ""))
        if not row["width_px"]:
            row["width_px"] = widths.get(row["file"], "")
    if not labels:
        print("no labelled crops", file=sys.stderr)
        return 1
    results = []
    for name in [b.strip() for b in args.backends.split(",") if b.strip()]:
        try:
            backend = build(name, args.weights, args.threads)
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: unavailable ({exc})", file=sys.stderr)
            continue
        r = score(backend, name, labels, negatives, bank, manifest, args.min_w)
        results.append(r)
        print(f"{name:<18} labelled {r['labelled']:>3}  exact {r['exact']:>3} ({r['exact_rate']})  char {r['char_acc']}  valid {r['valid_format_rate']}  "
              f"conf {r['mean_conf']} (exact {r['mean_conf_exact']} / wrong {r['mean_conf_wrong']})  {r['ms_per_call']} ms  "
              f"negatives {r['negatives']}: false-valid {r['neg_false_valid']} (conf>=0.45: {r['neg_false_valid_conf_ge_045']}), reads w/o digit {r['neg_reads_without_digit']}  "
              f"bank valid {r['bank_valid_format']}/{r['bank_reads']} reads of {r['bank_crops_ge_min_w']} crops")
        for p in r["per_crop"]:
            print(f"    {p['file'][-34:]}  truth {p['truth']}  got {p['got'] or '-':<12} raw {p['raw'][:24]!r} conf {p['conf']} {'EXACT' if p['exact'] else ''} {p['ms']} ms")
    doc = {"bank": str(bank), "labelled": len(labels), "negatives": len(negatives), "min_w": args.min_w, "results": results}
    out = Path(args.out) if args.out else bank / "eval.json"
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
