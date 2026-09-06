"""EvidenceStore: layout, JSON manifest, per-hour cap, disabled/unwritable directories."""
import json
from datetime import datetime, timezone

import numpy as np

from anpr.evidence import EvidenceStore


def test_evidence_layout_manifest_and_cap(tmp_path):
    store = EvidenceStore(tmp_path / "ev", per_hour=2)
    assert store.enabled
    crop = np.full((30, 100, 3), 200, np.uint8)
    at = datetime(2026, 9, 5, 14, 30, 12, 345000, tzinfo=timezone.utc)
    rel = store.write(62, crop, {"track": 7, "width_px": 100, "plate_norm": ""}, at)
    assert rel == "62/2026-09-05/143012_345_t7_w100.jpg"
    assert (tmp_path / "ev" / rel).stat().st_size > 100
    lines = (tmp_path / "ev" / "62" / "2026-09-05" / "vehicles.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["file"] == rel and json.loads(lines[0])["track"] == 7
    assert store.write(62, crop, {"track": 8, "width_px": 90}, at) is not None
    assert store.write(62, crop, {"track": 9, "width_px": 90}, at) is None, "third file in the hour is capped"
    assert store.write(63, crop, {"track": 1, "width_px": 90}, at) is not None, "the cap is per camera"
    assert store.stats() == {"enabled": True, "dir": str(tmp_path / "ev"), "written": 3, "skipped": 1}


def test_disabled_and_unwritable_store(tmp_path):
    off = EvidenceStore(None)
    assert not off.enabled and off.write(1, np.zeros((4, 4, 3), np.uint8), {}, datetime.now(timezone.utc)) is None
    blocked = tmp_path / "file-not-dir"
    blocked.write_text("x", encoding="utf-8")
    bad = EvidenceStore(blocked / "sub")
    assert not bad.enabled, "a directory that cannot be created disables the store instead of raising"
    assert bad.write(1, np.zeros((4, 4, 3), np.uint8), {}, datetime.now(timezone.utc)) is None
