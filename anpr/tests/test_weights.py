"""anpr/weights/download.py: HASHES.txt parsing and the start-up verifier (no network, no models)."""
import hashlib
from pathlib import Path

import pytest

from anpr.weights import download
from anpr.weights.download import WeightStatus, ensure_weight, read_specs, verify_weight_file

LEDGER = Path(download.HASHES_FILE)


def test_ledger_lists_every_model_with_licences():
    specs = {s.name: s for s in read_specs(LEDGER)}
    assert set(specs) == {
        "yolo-v9-t-384-license-plates-end2end.onnx", "yolox_s.onnx",
        # fast-plate-ocr recogniser models + their input/alphabet configs (anpr/ocr.py FastPlateOCR)
        "global_mobile_vit_v2_ocr.onnx", "global_mobile_vit_v2_ocr_config.yaml",
        "cct_s_v2_global.onnx", "cct_s_v2_global_plate_config.yaml",
        "cct_xs_v2_global.onnx", "cct_xs_v2_global_plate_config.yaml",
    }
    for spec in specs.values():
        assert len(spec.sha256) == 64 and spec.url.startswith("https://") and spec.licence
    assert all("MIT" in specs[n].licence for n in specs if "global" in n), "fast-plate-ocr models are MIT"


def test_verify_missing_unlisted_mismatch_verified(tmp_path):
    ledger = tmp_path / "HASHES.txt"
    payload = b"synthetic weight bytes"
    digest = hashlib.sha256(payload).hexdigest()
    ledger.write_text(
        "# comment\n"
        f"{digest}  good.onnx  https://example.invalid/good.onnx  MIT (test)\n"
        f"{'0' * 64}  bad.onnx  https://example.invalid/bad.onnx  MIT (test)\n",
        encoding="utf-8",
    )
    assert verify_weight_file(tmp_path / "absent.onnx", ledger).state == "missing"

    (tmp_path / "custom.onnx").write_bytes(payload)
    unlisted = verify_weight_file(tmp_path / "custom.onnx", ledger)
    assert unlisted.state == "unlisted" and unlisted.ok and unlisted.sha256 == digest

    (tmp_path / "bad.onnx").write_bytes(payload)
    bad = verify_weight_file(tmp_path / "bad.onnx", ledger)
    assert bad.state == "mismatch" and not bad.ok and "MISMATCH" in bad.describe()

    (tmp_path / "good.onnx").write_bytes(payload)
    good = verify_weight_file(tmp_path / "good.onnx", ledger)
    assert good == WeightStatus(str(tmp_path / "good.onnx"), "verified", digest, digest, "MIT (test)")
    assert good.ok and "verified" in good.describe()


def test_ensure_weight_no_download_reports_without_raising(tmp_path):
    spec = read_specs(LEDGER)[0]
    ok, message = ensure_weight(spec, tmp_path, download=False)
    assert ok is False and message.endswith("missing")


def test_main_require_exits_nonzero_when_weights_absent(tmp_path, capsys):
    """The Dockerfile relies on --require turning a missing weight into a failed build."""
    assert download.main(["--dir", str(tmp_path), "--no-download"]) == 0
    assert download.main(["--dir", str(tmp_path), "--no-download", "--require"]) == 1
    out = capsys.readouterr().out
    assert "yolox_s.onnx: missing" in out


@pytest.mark.skipif(not (LEDGER.parent / "yolox_s.onnx").is_file(), reason="weights not present (contour-only image)")
def test_shipped_weights_match_ledger():
    for spec in read_specs(LEDGER):
        assert verify_weight_file(LEDGER.parent / spec.name).state == "verified"
