"""Shared error shape (CONTRACT §3.1): {detail, code, errors[]}."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("sentinel.errors")

STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "bad_request",
    409: "conflict",
    413: "too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "upstream_error",
    503: "unavailable",
}


class ApiError(Exception):
    """Raise anywhere in a handler/service to produce the contract error body."""

    def __init__(
        self,
        status: int,
        detail: str,
        code: str | None = None,
        errors: list[dict[str, Any]] | None = None,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code or STATUS_CODES.get(status, "internal_error")
        self.errors = errors
        self.extra = extra or {}
        self.headers = headers

    def body(self) -> dict[str, Any]:
        b: dict[str, Any] = {"detail": self.detail, "code": self.code}
        if self.errors is not None:
            b["errors"] = self.errors
        b.update(self.extra)
        return b


def bad_request(detail: str) -> ApiError:
    return ApiError(400, detail)


def unauthorized(detail: str = "Missing or invalid credentials") -> ApiError:
    return ApiError(401, detail)


def forbidden(detail: str = "Insufficient role") -> ApiError:
    return ApiError(403, detail)


def not_found(detail: str = "Not found") -> ApiError:
    return ApiError(404, detail)


def conflict(detail: str, **extra: Any) -> ApiError:
    return ApiError(409, detail, extra=extra)


def validation_error(detail: str, errors: list[dict[str, Any]] | None = None) -> ApiError:
    return ApiError(422, detail, errors=errors or [])


def upstream_error(detail: str) -> ApiError:
    return ApiError(502, detail)


def _loc_to_field(loc: tuple) -> str:
    parts = [str(p) for p in loc]
    if parts and parts[0] in ("body", "query", "path", "header", "cookie"):
        parts = parts[1:]
    return ".".join(parts) if parts else "body"


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.body(), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": _loc_to_field(tuple(e.get("loc", ()))), "message": e.get("msg", "invalid")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"detail": "Validation failed", "code": "validation_error", "errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = STATUS_CODES.get(exc.status_code, "internal_error")
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        if exc.status_code == 401 and detail in ("Not authenticated", "Unauthorized"):
            detail = "Missing or invalid credentials"
        if exc.status_code == 403 and detail == "Not authenticated":
            detail = "Insufficient role"
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": detail, "code": code},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "code": "internal_error"},
        )
