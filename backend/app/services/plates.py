"""Plate normalisation – CONTRACT §3.4. Pure functions; identical to anpr/normalise.py."""

from __future__ import annotations

import re
from dataclasses import dataclass

TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"}
TO_DIGIT = {"O": "0", "I": "1", "S": "5", "B": "8", "Z": "2", "G": "6", "Q": "0", "D": "0"}

STANDARD = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$")
BH = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")
_NON_ALNUM = re.compile(r"[^A-Z0-9]")

MAX_SUBS = 2


@dataclass(frozen=True)
class NormResult:
    plate_norm: str
    is_valid_format: bool
    pattern: str | None  # 'standard' | 'bh' | None
    substitutions: int


def fix_letter(c: str) -> str:
    return TO_LETTER.get(c, c)


def fix_digit(c: str) -> str:
    return TO_DIGIT.get(c, c)


def _count_subs(a: str, b: str) -> int:
    return sum(1 for x, y in zip(a, b) if x != y)


def _standard_candidate(s: str) -> tuple[str, int] | None:
    """Best standard-format candidate (fewest substitutions, tie → k=2) or None."""
    if len(s) < 7:
        return None
    mid = s[2:-4]
    if not 1 <= len(mid) <= 5:
        return None
    best: tuple[str, int] | None = None
    for k in (2, 1):
        digits = mid[:k]
        series = mid[k:]
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
            continue  # district 0 does not exist
        subs = _count_subs(cand, s)
        if best is None or subs < best[1]:
            best = (cand, subs)
    return best


def _bh_candidate(s: str) -> tuple[str, int] | None:
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
    return cand, _count_subs(cand, s)


def normalise(raw: str | None) -> NormResult:
    s = _NON_ALNUM.sub("", (raw or "").upper())
    if s.startswith("IND"):
        s = s[3:]
    if not s:
        return NormResult("", False, None, 0)
    if len(s) < 7 or len(s) > 10:
        return NormResult(s, False, None, 0)

    std = _standard_candidate(s)
    bh = _bh_candidate(s)
    if std is not None and std[1] > MAX_SUBS:
        std = None
    if bh is not None and bh[1] > MAX_SUBS:
        bh = None

    if std is not None and (bh is None or std[1] <= bh[1]):
        return NormResult(std[0], True, "standard", std[1])
    if bh is not None:
        return NormResult(bh[0], True, "bh", bh[1])
    return NormResult(s, False, None, 0)


def normalise_plate(raw: str | None) -> str:
    return normalise(raw).plate_norm


def format_plate(plate_norm: str | None) -> str:
    """`GJ01AB1234 → GJ 01 AB 1234`, `22BH4321AA → 22 BH 4321 AA`, invalid → unchanged."""
    if not plate_norm:
        return ""
    p = plate_norm
    if STANDARD.match(p):
        m = re.match(r"^([A-Z]{2})([0-9]{1,2})([A-Z]{1,3})([0-9]{4})$", p)
        if m:
            return " ".join(m.groups())
    if BH.match(p):
        return f"{p[0:2]} BH {p[4:8]} {p[8:]}"
    return p


def levenshtein(a: str, b: str) -> int:
    """Classic edit distance (used by the in-memory watchlist matcher)."""
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
