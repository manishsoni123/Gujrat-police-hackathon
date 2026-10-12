"""The 20 shared normalisation vectors of CONTRACT.md section 3.4 (mirrored in backend/tests)."""
import pytest

from anpr.normalise import format_plate, levenshtein, normalise

VECTORS = [
    ("GJ 01 AB 1234", "GJ01AB1234", True, "standard", 0),
    ("gj-01-ab-1234", "GJ01AB1234", True, "standard", 0),
    ("IND GJ05 RS 5678", "GJ05RS5678", True, "standard", 0),
    ("GJO1AB1234", "GJ01AB1234", True, "standard", 1),
    ("G101AB1234", "GJ01AB1234", True, "standard", 1),
    ("GJ01A81234", "GJ01AB1234", True, "standard", 1),
    ("MH12DE14O3", "MH12DE1403", True, "standard", 1),
    ("GJ1AB1234", "GJ1AB1234", True, "standard", 0),
    ("DL8CAF5030", "DL8CAF5030", True, "standard", 0),
    ("GJ 01\nAB 1234", "GJ01AB1234", True, "standard", 0),
    ("22BH4321AA", "22BH4321AA", True, "bh", 0),
    ("Z2BH4321AA", "22BH4321AA", True, "bh", 1),
    ("GJ0IAB1234", "GJ01AB1234", True, "standard", 1),
    ("KA05MK 0101", "KA05MK0101", True, "standard", 0),
    ("8H01AB1234", "8H01AB1234", False, None, 0),
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


# Step 5a state-code vectors (CONTRACT amendment 2026-10-12), mirrored in backend/tests.
STATE_VECTORS = [
    ("GI27XY3456", "GJ27XY3456", True, "standard", 1),
    ("GU03KH0407", "GJ03KH0407", True, "standard", 1),
    ("DO01AB1234", "DD01AB1234", True, "standard", 1),
    ("C601AB1234", "CG01AB1234", True, "standard", 1),
    ("G10IAB1234", "GJ01AB1234", True, "standard", 2),
    ("G10IA81234", "G10IA81234", False, None, 0),
    ("SI1A5599", "SI1A5599", False, None, 0),
    ("BJ25I7596", "BJ25I7596", False, None, 0),
    ("TG09AB1234", "TG09AB1234", True, "standard", 0),
    ("OR02AB1234", "OR02AB1234", True, "standard", 0),
    ("UA07C1234", "UA07C1234", True, "standard", 0),
    ("LA01AB1234", "LA01AB1234", True, "standard", 0),
]


@pytest.mark.parametrize("raw,plate_norm,valid,pattern,subs", STATE_VECTORS)
def test_state_vectors(raw, plate_norm, valid, pattern, subs):
    result = normalise(raw)
    assert result.plate_norm == plate_norm
    assert result.is_valid_format is valid
    assert result.pattern == pattern
    assert result.substitutions == subs


# Step 5 district-slot vectors (CONTRACT amendment 2026-10-13), mirrored in backend/tests: the k
# district chars must all be digits after fix_digit and must not be district 0 / 00.
DISTRICT_VECTORS = [
    ("GLOAE L2866", "GLOAEL2866", False, None, 0),  # k=2 "0A" is not digits; k=1 is district 0
    ("GJ00AB1234", "GJ00AB1234", False, None, 0),  # district 00
    ("GJOOAB1234", "GJOOAB1234", False, None, 0),  # OO -> district 00
    ("GJ0DAB1234", "GJ0DAB1234", False, None, 0),  # 0D -> district 00
    ("GJ0AB1234", "GJ0AB1234", False, None, 0),  # k=2 "0A" is not digits; k=1 is district 0
    ("DL0CAF5030", "DL0CAF5030", False, None, 0),  # k=2 "0C" is not digits; k=1 is district 0
    ("GJ1AB1234", "GJ1AB1234", True, "standard", 0),
    ("GJIAB1234", "GJ1AB1234", True, "standard", 1),  # mappable letter, one-digit district
    ("GJ01AB1234", "GJ01AB1234", True, "standard", 0),
    ("GJ10AB1234", "GJ10AB1234", True, "standard", 0),
    ("MH12DE1403", "MH12DE1403", True, "standard", 0),
    ("GJO1AB1234", "GJ01AB1234", True, "standard", 1),  # mappable letter, two-digit district
]


@pytest.mark.parametrize("raw,plate_norm,valid,pattern,subs", DISTRICT_VECTORS)
def test_district_vectors(raw, plate_norm, valid, pattern, subs):
    result = normalise(raw)
    assert result.plate_norm == plate_norm
    assert result.is_valid_format is valid
    assert result.pattern == pattern
    assert result.substitutions == subs


def test_district_zero_is_never_valid():
    # district 0 / 00, read directly or through fix_digit (Q, D, O -> 0), for every series length
    for raw in ("GJ0A1234", "GJ0ABC1234", "GJ00A1234", "MH00Z1234", "TN0QA1234"):
        r = normalise(raw)
        assert (r.plate_norm, r.is_valid_format, r.pattern, r.substitutions) == (raw, False, None, 0)


def test_state_codes_cover_states_uts_and_legacy():
    from anpr.normalise import STATE_CODES

    assert len(STATE_CODES) == 28 + 8 + 4
    assert {"GJ", "TG", "TS", "OD", "OR", "UK", "UA", "DD", "DN", "LA"} <= STATE_CODES


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
