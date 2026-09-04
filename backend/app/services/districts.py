"""Canonical Gujarat district names (CONTRACT §12.3)."""

from __future__ import annotations

DISTRICTS = (
    "Ahmedabad", "Amreli", "Anand", "Aravalli", "Banaskantha", "Bharuch", "Bhavnagar", "Botad",
    "Chhota Udaipur", "Dahod", "Dang", "Devbhumi Dwarka", "Gandhinagar", "Gir Somnath", "Jamnagar",
    "Junagadh", "Kheda", "Kutch", "Mahisagar", "Mehsana", "Morbi", "Narmada", "Navsari", "Panchmahal",
    "Patan", "Porbandar", "Rajkot", "Sabarkantha", "Surat", "Surendranagar", "Tapi", "Vadodara", "Valsad",
)

_VARIANTS = {
    "devbhoomi dwarka": "Devbhumi Dwarka",
    "dev bhumi dwarka": "Devbhumi Dwarka",
    "dwarka": "Devbhumi Dwarka",
    "somnath": "Gir Somnath",
    "gir-somnath": "Gir Somnath",
    "ahmadabad": "Ahmedabad",
    "amdavad": "Ahmedabad",
    "panch mahals": "Panchmahal",
    "panchmahals": "Panchmahal",
    "kachchh": "Kutch",
    "kachch": "Kutch",
    "dangs": "Dang",
    "the dangs": "Dang",
    "chhotaudepur": "Chhota Udaipur",
    "chota udaipur": "Chhota Udaipur",
    "chhota udepur": "Chhota Udaipur",
    "banas kantha": "Banaskantha",
    "sabar kantha": "Sabarkantha",
    "mahesana": "Mehsana",
    "dohad": "Dahod",
    "baroda": "Vadodara",
    "surendra nagar": "Surendranagar",
}

_LOWER = {d.lower(): d for d in DISTRICTS}


def canonical_district(value: str) -> str:
    """Title-case input and map common spelling variants to the canonical name."""
    s = " ".join(value.strip().split())
    key = s.lower()
    if key in _LOWER:
        return _LOWER[key]
    if key in _VARIANTS:
        return _VARIANTS[key]
    return s.title()
