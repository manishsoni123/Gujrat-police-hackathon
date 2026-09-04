"""Deterministic SARTHI (driving licence) mock (CONTRACT §5.19)."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from app.adapters.external_base import ExternalLookupAdapter, pick, stable_bucket

HOLDERS = ["Rakesh Patel", "Sunita Shah", "Mahesh Desai", "Kiran Solanki", "Anil Mehta", "Pooja Chaudhary", "Vijay Rathod", "Nita Joshi"]
CLASS_SETS = [["LMV"], ["LMV", "MCWG"], ["MCWG"], ["LMV", "MCWG", "TRANS"], ["HMV", "LMV"]]


class SarthiMockAdapter(ExternalLookupAdapter):
    system = "SARTHI"
    adapter = "sarthi_mock"
    NOTE = "Mock data – integration-ready adapter; real SARTHI access requires NIC gateway credentials"

    async def lookup(self, key: str) -> dict[str, Any]:
        dl = re.sub(r"[^A-Z0-9]", "", key.upper())
        base = {"source": "SARTHI (mock adapter)", "adapter": self.adapter, "dl_number": dl, "note": self.NOTE}
        if len(dl) < 8 or stable_bucket(dl, 5) == 0:
            return {**base, "found": False}
        dob = date(1970, 1, 1) + timedelta(days=stable_bucket(dl + "dob", 12000))
        valid_from = date(2005, 1, 1) + timedelta(days=stable_bucket(dl + "from", 6000))
        rto_code = dl[:4] if dl[2:4].isdigit() else dl[:2]
        return {
            **base,
            "found": True,
            "holder_name": pick(dl, "holder", HOLDERS),
            "dob": dob.isoformat(),
            "valid_from": valid_from.isoformat(),
            "valid_till": (valid_from + timedelta(days=20 * 365)).isoformat(),
            "classes": pick(dl, "classes", CLASS_SETS),
            "rto": f"{rto_code[:2]}-{rto_code[2:] or '01'} RTO",
            "status": pick(dl, "status", ["active", "active", "active", "suspended"]),
        }
