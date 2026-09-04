"""Mock organiser sandbox (CONTRACT §5.22, §6): catalogue + webhook sink, only when MOCK_SANDBOX=1."""

from __future__ import annotations

from collections import deque
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from app.api.deps import require_permission
from app.core.config import settings
from app.core.errors import not_found
from app.core.tz import iso_z, utcnow
from app.services import mock_catalogue

router = APIRouter(tags=["mock-sandbox"])

_sink: deque[dict[str, Any]] = deque(maxlen=20)


def _enabled() -> None:
    if not settings.MOCK_SANDBOX:
        raise not_found("Mock sandbox is disabled (MOCK_SANDBOX=0)")


@router.get("/mock-sandbox/", include_in_schema=True)
async def mock_root():
    _enabled()
    return mock_catalogue.summary()


@router.get("/mock-sandbox", include_in_schema=False)
async def mock_root_noslash():
    _enabled()
    return mock_catalogue.summary()


@router.get("/mock-sandbox/api/ingest")
async def mock_ingest():
    """Organiser-shaped catalogue: a bare JSON array of 50 cameras (§6.1)."""
    _enabled()
    return JSONResponse(content=mock_catalogue.load())


@router.post("/mock-sandbox/webhook-sink", status_code=204)
async def webhook_sink(request: Request):
    _enabled()
    try:
        body: Any = await request.json()
    except ValueError:
        body = (await request.body()).decode("utf-8", "ignore")
    _sink.appendleft({"received_at": iso_z(utcnow()), "headers": {k.lower(): v for k, v in request.headers.items() if k.lower().startswith("x-sentinel") or k.lower() in ("content-type", "user-agent")}, "body": body})
    return Response(status_code=204)


@router.get("/mock-sandbox/webhook-sink", dependencies=[Depends(require_permission("admin.settings"))])
async def webhook_sink_list():
    _enabled()
    return {"deliveries": list(_sink)}
