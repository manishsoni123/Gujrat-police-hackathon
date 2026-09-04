"""`GET /media/<relative path>` – authenticated file serving from DATA_DIR (CONTRACT §10.2)."""

from __future__ import annotations

import mimetypes
import os
import re
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select

from app.api.deps import DbDep, extract_token, remember_actor, user_from_token, user_scope
from app.core.errors import not_found, unauthorized
from app.core.hashing import ALLOWED_MEDIA_PREFIXES, abs_path, safe_relative
from app.db.models import Alert, Clip, Event, PlateRead, ReportFile, Sighting
from app.services.audit import audit_from_request, set_audit
from app.services.scope import scoped_camera

router = APIRouter(tags=["media"])

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_download_audit: dict[tuple[int, str], float] = {}
DOWNLOAD_AUDIT_WINDOW_S = 600
CHUNK = 1 << 16


def _camera_id_from(rel: str) -> int | None:
    parts = rel.split("/")
    if parts[0] in ("crops", "frames", "clips") and len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    if parts[0] == "snapshots" and len(parts) == 2:
        m = re.match(r"^cam_(\d+)\.jpg$", parts[1])
        return int(m.group(1)) if m else None
    return None


async def _stored_sha(db, rel: str) -> str | None:
    for model, col, sha in ((PlateRead, PlateRead.crop_path, PlateRead.crop_sha256), (Sighting, Sighting.frame_path, Sighting.frame_sha256), (Clip, Clip.path, Clip.sha256), (ReportFile, ReportFile.path, ReportFile.sha256), (Alert, Alert.snapshot_path, Alert.snapshot_sha256), (Event, Event.frame_path, Event.frame_sha256)):
        row = (await db.execute(select(sha).where(col == rel).limit(1))).first()
        if row and row[0]:
            return row[0]
    return None


def _iter_file(path: str, start: int, end: int):
    with open(path, "rb") as fh:
        fh.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            block = fh.read(min(CHUNK, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block


@router.get("/media/{rel_path:path}", include_in_schema=False)
async def serve_media(rel_path: str, request: Request, db: DbDep):
    set_audit(request, skip=True)
    user = await user_from_token(db, extract_token(request))
    if user is None:
        raise unauthorized("Missing or invalid credentials")
    remember_actor(request, user)
    rel = safe_relative(rel_path)
    if rel is None or not rel.startswith(ALLOWED_MEDIA_PREFIXES):
        raise not_found("File not found")
    path = abs_path(rel)
    if not path.is_file() or path.is_symlink():
        raise not_found("File not found")

    top = rel.split("/")[0]
    if top in ("reports", "exports"):
        rf = (await db.execute(select(ReportFile).where(ReportFile.path == rel).limit(1))).scalar_one_or_none()
        if user.role != "admin" and (rf is None or rf.created_by != user.id):
            raise not_found("File not found")
    else:
        cam_id = _camera_id_from(rel)
        if cam_id is not None and not user_scope(user).unrestricted:
            if await scoped_camera(db, user_scope(user), cam_id) is None:
                raise not_found("File not found")

    size = path.stat().st_size
    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    headers: dict[str, Any] = {"Accept-Ranges": "bytes"}
    if top == "snapshots":
        headers["Cache-Control"] = "no-store"
    elif top in ("crops", "frames", "clips"):
        headers["Cache-Control"] = "private, max-age=31536000, immutable"
    else:
        headers["Cache-Control"] = "private, no-cache"
    sha = await _stored_sha(db, rel)
    if sha:
        headers["X-Sentinel-Sha256"] = sha
    if top in ("reports", "exports"):
        headers["Content-Disposition"] = f'attachment; filename="{os.path.basename(rel)}"'
        key = (user.id, rel)
        now = time.monotonic()
        if now - _download_audit.get(key, 0.0) > DOWNLOAD_AUDIT_WINDOW_S:
            _download_audit[key] = now
            await audit_from_request(request, "report.download", entity="report_file", entity_id=rel, after={"path": rel, "sha256": sha})
    else:
        headers["Content-Disposition"] = f'inline; filename="{os.path.basename(rel)}"'

    if top == "snapshots" and request.method != "HEAD":
        # The worker replaces a snapshot every second (tmp -> rename). Serve the whole small file from
        # memory so Content-Length always matches the bytes sent (a stat-then-stream would race the rename).
        try:
            data = path.read_bytes()
        except OSError:
            raise not_found("File not found")
        headers["Content-Length"] = str(len(data))
        return Response(content=data, media_type=media_type, headers=headers)

    range_header = request.headers.get("range")
    start, end = 0, size - 1
    status = 200
    if range_header:
        m = _RANGE_RE.match(range_header.strip())
        if not m:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        s, e = m.group(1), m.group(2)
        if s == "" and e == "":
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        if s == "":
            start, end = max(0, size - int(e)), size - 1
        else:
            start = int(s)
            end = min(int(e), size - 1) if e else size - 1
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1)
    if request.method == "HEAD":
        return Response(status_code=status, headers=headers, media_type=media_type)
    return StreamingResponse(_iter_file(str(path), start, end), status_code=status, media_type=media_type, headers=headers)
