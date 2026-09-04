"""Authorisation of MediaMTX requests behind Caddy `forward_auth` (CONTRACT §2.3, §2.5).

Caddy asks `GET /api/auth/verify` before proxying `/mtx/*` (WHEP, HLS) and `/playback/*`
(recordings) and passes the original request in `X-Forwarded-Uri`. Authentication alone is
not enough: a `dept_admin` only sees the cameras of their department (and district), and
that camera set must also bound the video they can watch or download. This module resolves
the MediaMTX path named by the forwarded URI to a camera id and applies the same
`scoped_camera` rule the REST API uses; a miss is answered `404` so existence is not leaked.

The pure helpers (`media_path_from_uri`, `camera_id_from_path`, `token_from_uri`) are covered
by `tests/test_rbac.py`.
"""

from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import Scope
from app.services.scope import scoped_camera

_CAM_PATH_RE = re.compile(r"^cam_(\d+)(?:_h264)?$")
_PROXY_PREFIXES = ("mtx", "playback")
_PLAYBACK_VERBS = ("list", "get")
DECISION_TTL_S = 30.0

# (user_id, camera_id) → (allowed, expires_at monotonic). HLS fetches one segment every few
# seconds per tile, so the scoped lookup is cached briefly instead of hitting the DB each time.
_decisions: dict[tuple[int, int], tuple[bool, float]] = {}


def media_path_from_uri(uri: str) -> str | None:
    """MediaMTX path named by a forwarded URI, or None when there is none.

    Accepts both the browser-facing form (`/mtx/cam_2/index.m3u8`, `/playback/list?path=cam_2&…`)
    and the prefix-stripped form Caddy's `handle_path` produces (`/cam_2/whep`, `/get?path=cam_2`).
    HLS/WHEP URIs are `/<path…>/<file-or-verb>[/<session>]`; the path may itself contain slashes
    (`stream/3`), so everything before the trailing file/verb is the path.
    """
    parts = urlsplit(uri or "")
    segs = [s for s in parts.path.split("/") if s]
    if segs and segs[0] in _PROXY_PREFIXES:
        segs = segs[1:]
    if not segs:
        return None
    if segs[0] in _PLAYBACK_VERBS and len(segs) == 1:
        value = parse_qs(parts.query).get("path", [""])[0].strip().strip("/")
        return value or None
    if "whep" in segs:
        segs = segs[: segs.index("whep")]
    elif len(segs) > 1:
        segs = segs[:-1]
    path = "/".join(segs)
    return path or None


def camera_id_from_path(path: str | None) -> int | None:
    """`cam_<id>` / `cam_<id>_h264` → id; anything else (own_*, stream/*, garbage) → None."""
    if not path:
        return None
    m = _CAM_PATH_RE.match(path)
    return int(m.group(1)) if m else None


def token_from_uri(uri: str) -> str | None:
    """`?token=` of the forwarded URI (Caddy rewrites the sub-request URI, so the API only sees it here)."""
    if not uri:
        return None
    return parse_qs(urlsplit(uri).query).get("token", [None])[0] or None


def forget_decisions() -> None:
    _decisions.clear()


async def camera_allowed(db: AsyncSession, user_id: int, scope: Scope, uri: str) -> bool:
    """True when `scope` may access the media named by `uri`.

    Statewide roles pass unconditionally (including `own_*` / `stream/*` paths). A restricted
    scope must name a `cam_<id>` path whose camera is in scope; decisions are cached for
    `DECISION_TTL_S` per (user, camera).
    """
    if scope.unrestricted:
        return True
    camera_id = camera_id_from_path(media_path_from_uri(uri))
    if camera_id is None:
        return False
    key = (user_id, camera_id)
    now = time.monotonic()
    cached = _decisions.get(key)
    if cached is not None and cached[1] > now:
        return cached[0]
    allowed = await scoped_camera(db, scope, camera_id) is not None
    if len(_decisions) > 5000:
        _decisions.clear()
    _decisions[key] = (allowed, now + DECISION_TTL_S)
    return allowed
