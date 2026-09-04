"""Plate normalisation vectors (CONTRACT §3.4) – shared verbatim with anpr/tests/test_normalise.py."""

from __future__ import annotations

import pytest

from app.services.plates import format_plate, levenshtein, normalise

VECTORS = [
    ("GJ 01 AB 1234", "GJ01AB1234", True, "standard", 0),
    ("gj-01-ab-1234", "GJ01AB1234", True, "standard", 0),
    ("IND GJ05 RS 5678", "GJ05RS5678", True, "standard", 0),
    ("GJO1AB1234", "GJ01AB1234", True, "standard", 1),
    ("G101AB1234", "GI01AB1234", True, "standard", 1),
    ("GJ01A81234", "GJ01AB1234", True, "standard", 1),
    ("MH12DE14O3", "MH12DE1403", True, "standard", 1),
    ("GJ1AB1234", "GJ1AB1234", True, "standard", 0),
    ("DL8CAF5030", "DL8CAF5030", True, "standard", 0),
    ("GJ 01\nAB 1234", "GJ01AB1234", True, "standard", 0),
    ("22BH4321AA", "22BH4321AA", True, "bh", 0),
    ("Z2BH4321AA", "22BH4321AA", True, "bh", 1),
    ("GJ0IAB1234", "GJ01AB1234", True, "standard", 1),
    ("KA05MK 0101", "KA05MK0101", True, "standard", 0),
    ("8H01AB1234", "BH01AB1234", True, "standard", 1),
    ("GJ01AB12345", "GJ01AB12345", False, None, 0),
    ("GJ01ABCD1234", "GJ01ABCD1234", False, None, 0),
    ("ABC", "ABC", False, None, 0),
    ("IND", "", False, None, 0),
    ("S5S5S5S5", "S5S5S5S5", False, None, 0),
]


@pytest.mark.parametrize("raw,expected,valid,pattern,subs", VECTORS)
def test_contract_vectors(raw: str, expected: str, valid: bool, pattern: str | None, subs: int) -> None:
    r = normalise(raw)
    assert r.plate_norm == expected
    assert r.is_valid_format is valid
    assert r.pattern == pattern
    assert r.substitutions == subs


def test_two_line_join_with_dot_and_middle_dot() -> None:
    assert normalise("MH 02\n·BZ 7788").plate_norm == "MH02BZ7788"
    assert normalise("22 BH\n4321 AA").plate_norm == "22BH4321AA"


def test_confusion_only_applied_positionally() -> None:
    # letters in digit slots and digits in letter slots are fixed, but not the other way round
    assert normalise("GJ01AB1Z34").plate_norm == "GJ01AB1234"
    assert normalise("GJ01AB12S4").plate_norm == "GJ01AB1254"
    # more than two fixes is rejected (kept raw, invalid)
    r = normalise("O1OIAB1234")
    assert r.is_valid_format is False
    assert r.plate_norm == "O1OIAB1234"


def test_none_and_empty() -> None:
    assert normalise(None).plate_norm == ""
    assert normalise("").is_valid_format is False
    assert normalise("   ").plate_norm == ""


def test_format_plate() -> None:
    assert format_plate("GJ01AB1234") == "GJ 01 AB 1234"
    assert format_plate("GJ1AB1234") == "GJ 1 AB 1234"
    assert format_plate("DL8CAF5030") == "DL 8 CAF 5030"
    assert format_plate("22BH4321AA") == "22 BH 4321 AA"
    assert format_plate("GJ01AB12345") == "GJ01AB12345"
    assert format_plate("") == ""
    assert format_plate(None) == ""


def test_levenshtein() -> None:
    assert levenshtein("GJ01AB1234", "GJ01AB1234") == 0
    assert levenshtein("GJ01AB1234", "GJ01AB1235") == 1
    assert levenshtein("GJ01AB1234", "GJ01A1234") == 1
    assert levenshtein("GJ01AB1234", "GJ01CD1234") == 2
    assert levenshtein("", "ABC") == 3
    assert levenshtein("ABC", "") == 3
