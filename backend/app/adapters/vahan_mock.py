"""Deterministic VAHAN mock (CONTRACT §5.19)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.adapters.external_base import ExternalLookupAdapter, pick, stable_bucket
from app.services.plates import normalise

OWNERS = ["R. Patel", "S. Shah", "M. Desai", "K. Solanki", "A. Mehta", "P. Chaudhary", "V. Rathod", "N. Joshi", "H. Parmar", "D. Vaghela"]
MODELS = [
    ("Motor Car", "Maruti Swift VXI", "Petrol"), ("Motor Car", "Hyundai Creta SX", "Diesel"), ("Motor Car", "Honda City ZX", "Petrol"),
    ("Motor Car", "Toyota Fortuner 4x2", "Diesel"), ("Motor Cycle", "Bajaj Pulsar 150", "Petrol"), ("Motor Cycle", "Honda Activa 6G", "Petrol"),
    ("Goods Carrier", "Tata 407", "Diesel"), ("Goods Carrier", "Ashok Leyland Dost", "Diesel"), ("Motor Car", "Tata Nexon EV", "Electric"),
    ("Motor Cab", "Maruti Dzire Tour", "CNG"),
]
COLOURS = ["White", "Silver", "Grey", "Black", "Red", "Blue"]
RTOS = {
    "GJ01": "GJ-01 Ahmedabad", "GJ05": "GJ-05 Surat", "GJ06": "GJ-06 Vadodara", "GJ18": "GJ-18 Gandhinagar", "GJ03": "GJ-03 Rajkot",
    "GJ10": "GJ-10 Jamnagar", "GJ12": "GJ-12 Kutch", "GJ15": "GJ-15 Valsad", "GJ20": "GJ-20 Dahod", "GJ27": "GJ-27 Ahmedabad East",
    "GJ33": "GJ-33 Botad", "GJ38": "GJ-38 Bavla", "MH02": "MH-02 Mumbai West", "DL3": "DL-03 Delhi South", "RJ14": "RJ-14 Jaipur",
    "MP09": "MP-09 Indore", "KA01": "KA-01 Bengaluru Central", "KA05": "KA-05 Bengaluru South",
}


class VahanMockAdapter(ExternalLookupAdapter):
    system = "VAHAN"
    adapter = "vahan_mock"
    NOTE = "Mock data – integration-ready adapter; real VAHAN access requires NIC gateway credentials"

    async def lookup(self, key: str) -> dict[str, Any]:
        plate = normalise(key).plate_norm
        base = {"source": "VAHAN (mock adapter)", "adapter": self.adapter, "plate": plate, "note": self.NOTE}
        if not plate or stable_bucket(plate, 5) == 0:
            return {**base, "found": False}
        cls, model, fuel = pick(plate, "model", MODELS)
        reg_day = 5000 + stable_bucket(plate + "reg", 2500)
        reg = date(2026, 1, 1) - timedelta(days=reg_day)
        prefix = plate[:4] if plate[2:4].isdigit() else plate[:3]
        rto = RTOS.get(prefix) or RTOS.get(plate[:3]) or f"{plate[:2]}-{plate[2:4]} Regional Transport Office"
        return {
            **base,
            "found": True,
            "owner_name": pick(plate, "owner", OWNERS),
            "vehicle_class": cls,
            "maker_model": model,
            "fuel": fuel,
            "colour": pick(plate, "colour", COLOURS),
            "registration_date": reg.isoformat(),
            "rto": rto,
            "insurance_valid_till": (date(2026, 9, 1) + timedelta(days=30 + stable_bucket(plate + "ins", 400))).isoformat(),
            "fitness_valid_till": (reg + timedelta(days=15 * 365)).isoformat(),
        }
