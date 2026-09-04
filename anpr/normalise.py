"""Indian number-plate normalisation - the exact rules of CONTRACT.md section 3.4.

This module is pure (no I/O) and is mirrored by ``backend/app/services/plates.py``; the 20
shared test vectors live in ``anpr/tests/test_normalise.py`` and must pass verbatim in both.

    >>> normalise("GJ 01 AB 1234")
    NormResult(plate_norm='GJ01AB1234', is_valid_format=True, pattern='standard', substitutions=0)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Pattern = Literal["standard", "bh"]

STANDARD = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$")
BH = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")

TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"}
TO_DIGIT = {"O": "0", "I": "1", "S": "5", "B": "8", "Z": "2", "G": "6", "Q": "0", "D": "0"}

MAX_SUBSTITUTIONS = 2
_NON_ALNUM = re.compile(r"[^A-Z0-9]")


@dataclass(frozen=True)
class NormResult:
    plate_norm: str
    is_valid_format: bool
    pattern: Pattern | None
    substitutions: int


def fix_letter(c: str) -> str:
    return TO_LETTER.get(c, c)


def fix_digit(c: str) -> str:
    return TO_DIGIT.get(c, c)


def clean(raw: str) -> str:
    """Steps 1-2: uppercase, keep [A-Z0-9] only, drop a leading ``IND``."""
    s = _NON_ALNUM.sub("", (raw or "").upper())
    if s.startswith("IND"):
        s = s[3:]
    return s


def _count_subs(cand: str, s: str) -> int:
    return sum(1 for a, b in zip(cand, s) if a != b)


def _standard_candidates(s: str) -> list[tuple[int, str, int]]:
    """Step 5: position-aware candidates for k = 2 and k = 1 district digits.

    Returns ``(subs, cand, k)`` for every k that yields a valid standard plate, k=2 first.
    """
    out: list[tuple[int, str, int]] = []
    mid = s[2:-4]
    for k in (2, 1):
        digits, series = mid[:k], mid[k:]
        if len(digits) < k or not 1 <= len(series) <= 3:
            continue
        cand = (
            fix_letter(s[0])
            + fix_letter(s[1])
            + "".join(fix_digit(c) for c in digits)
            + "".join(fix_letter(c) for c in series)
            + "".join(fix_digit(c) for c in s[-4:])
        )
        if not STANDARD.match(cand):
            continue
        if k == 1 and cand[2] == "0":
            continue  # there is no district 0
        out.append((_count_subs(cand, s), cand, k))
    return out


def _bh_candidate(s: str) -> tuple[int, str] | None:
    """Step 6: Bharat-series candidate, only for 9- or 10-character strings."""
    if len(s) not in (9, 10):
        return None
    cand = (
        fix_digit(s[0])
        + fix_digit(s[1])
        + fix_letter(s[2])
        + fix_letter(s[3])
        + "".join(fix_digit(c) for c in s[4:8])
        + "".join(fix_letter(c) for c in s[8:])
    )
    if not BH.match(cand):
        return None
    return _count_subs(cand, s), cand


def normalise(raw: str) -> NormResult:
    """Normalise an OCR string or user input into a canonical plate (CONTRACT section 3.4)."""
    s = clean(raw)
    if not s:
        return NormResult("", False, None, 0)
    if len(s) < 7 or len(s) > 10:
        return NormResult(s, False, None, 0)

    best_standard: tuple[int, str] | None = None
    for subs, cand, _k in _standard_candidates(s):  # k=2 is yielded first, so ties keep k=2
        if best_standard is None or subs < best_standard[0]:
            best_standard = (subs, cand)
    if best_standard is not None and best_standard[0] > MAX_SUBSTITUTIONS:
        best_standard = None

    best_bh = _bh_candidate(s)
    if best_bh is not None and best_bh[0] > MAX_SUBSTITUTIONS:
        best_bh = None

    if best_standard is None and best_bh is None:
        return NormResult(s, False, None, 0)
    if best_bh is None or (best_standard is not None and best_standard[0] <= best_bh[0]):
        assert best_standard is not None
        return NormResult(best_standard[1], True, "standard", best_standard[0])
    return NormResult(best_bh[1], True, "bh", best_bh[0])


def format_plate(plate_norm: str) -> str:
    """Display form: ``GJ01AB1234 -> GJ 01 AB 1234``, ``22BH4321AA -> 22 BH 4321 AA``; invalid unchanged."""
    m = re.match(r"^([A-Z]{2})([0-9]{1,2})([A-Z]{1,3})([0-9]{4})$", plate_norm)
    if m:
        return " ".join(m.groups())
    m = re.match(r"^([0-9]{2})(BH)([0-9]{4})([A-Z]{1,2})$", plate_norm)
    if m:
        return " ".join(m.groups())
    return plate_norm


def levenshtein(a: str, b: str) -> int:
    """Plain edit distance (used for plate-bucket matching in voting)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
