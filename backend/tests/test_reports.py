"""CSV export spelling and row-hash consistency (report_builder.py)."""

from __future__ import annotations

from app.services.report_builder import build_csv, csv_cell, rows_hash


def test_csv_cell_spells_booleans_lowercase_only() -> None:
    assert csv_cell(True) == "true" and csv_cell(False) == "false"
    assert csv_cell(1) == 1 and csv_cell(0) == 0  # ints are not booleans
    assert csv_cell(None) is None and csv_cell("True") == "True"


def test_build_csv_uses_lowercase_booleans_and_hash_matches_file() -> None:
    header = ["plate", "is_valid_format", "confidence"]
    rows = [["GJ01AB1234", True, 0.93], ["GJ01AB12", False, 0.5]]
    h = rows_hash(rows)
    data = build_csv(header, rows, f"# sha256(rows)={h}").decode("utf-8")
    lines = data.splitlines()
    assert lines[0] == "plate,is_valid_format,confidence"
    assert lines[1] == "GJ01AB1234,true,0.93"
    assert lines[2] == "GJ01AB12,false,0.5"
    assert "True" not in data and "False" not in data
    # the trailer hash is computed over the same spelling that is written
    assert rows_hash([["GJ01AB1234", "true", 0.93], ["GJ01AB12", "false", 0.5]]) == h
    assert lines[3].endswith(h)
