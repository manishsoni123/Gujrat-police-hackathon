"""The 20 shared normalisation vectors of CONTRACT.md section 3.4 (mirrored in backend/tests)."""
import pytest

from anpr.normalise import format_plate, levenshtein, normalise

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


@pytest.mark.parametrize("raw,plate_norm,valid,pattern,subs", VECTORS)
def test_vectors(raw, plate_norm, valid, pattern, subs):
    result = normalise(raw)
    assert result.plate_norm == plate_norm
    assert result.is_valid_format is valid
    assert result.pattern == pattern
    assert result.substitutions == subs


def test_vector_count_is_twenty():
    assert len(VECTORS) == 20


def test_format_plate():
    assert format_plate("GJ01AB1234") == "GJ 01 AB 1234"
    assert format_plate("22BH4321AA") == "22 BH 4321 AA"
    assert format_plate("GJ1AB1234") == "GJ 1 AB 1234"
    assert format_plate("S5S5S5S5") == "S5S5S5S5"


def test_levenshtein():
    assert levenshtein("GJ01AB1234", "GJ01AB1234") == 0
    assert levenshtein("GJ01AB1234", "GJ01AB1235") == 1
    assert levenshtein("GJ01AB1234", "GJ1AB1234") == 1
    assert levenshtein("", "AB") == 2
