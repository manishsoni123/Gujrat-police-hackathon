"""External lookup readiness (X1, CONTRACT §5.19): mock VAHAN and SARTHI adapters."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.adapters.sarthi_mock import SarthiMockAdapter
from app.adapters.vahan_mock import VahanMockAdapter
from app.api.deps import require_permission
from app.core.errors import validation_error
from app.services.audit import set_audit
from app.services.plates import normalise

router = APIRouter(tags=["external"], dependencies=[Depends(require_permission("external.lookup"))])

_vahan = VahanMockAdapter()
_sarthi = SarthiMockAdapter()

READINESS = [
    {"system": "VAHAN", "data": "vehicle owner, class, maker/model, insurance/fitness validity", "auth_model": "NIC API gateway credentials (API key + IP whitelist)", "adapter": "vahan_mock", "status": "mock"},
    {"system": "SARTHI", "data": "driving-licence holder, validity, classes", "auth_model": "NIC API gateway credentials", "adapter": "sarthi_mock", "status": "mock"},
    {"system": "eGujCop", "data": "FIR/wanted/stolen lists → watchlist import", "auth_model": "department-issued API key or CSV export", "adapter": "watchlist CSV import mapping", "status": "interface"},
    {"system": "AFIS", "data": "fingerprint hits for arrested persons", "auth_model": "SCRB internal", "adapter": "roadmap (person watchlist entries)", "status": "roadmap"},
    {"system": "NAFIS", "data": "national fingerprint / unidentified body matches", "auth_model": "NCRB gateway", "adapter": "roadmap", "status": "roadmap"},
]


@router.get("/external/readiness")
async def readiness():
    """Integration-readiness matrix (also reproduced in the HLD)."""
    return {"systems": READINESS}


@router.get("/external/vahan/{plate}")
async def vahan(plate: str, request: Request):
    n = normalise(plate)
    if not n.plate_norm:
        raise validation_error("Plate is empty", [{"field": "plate", "message": "enter a registration number"}])
    result = await _vahan.lookup(n.plate_norm)
    set_audit(request, action="external.lookup", entity="vahan", entity_id=n.plate_norm, after={"found": result.get("found")})
    return result


@router.get("/external/sarthi/{dl_number}")
async def sarthi(dl_number: str, request: Request):
    if not dl_number.strip():
        raise validation_error("DL number is empty", [{"field": "dl_number", "message": "enter a licence number"}])
    result = await _sarthi.lookup(dl_number)
    set_audit(request, action="external.lookup", entity="sarthi", entity_id=result.get("dl_number"), after={"found": result.get("found")})
    return result
