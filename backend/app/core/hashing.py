"""Evidence-integrity file writes (CONTRACT §10.1): tmp → fsync → sha256 → os.replace."""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from app.core.config import settings

ALLOWED_MEDIA_PREFIXES = ("crops/", "frames/", "snapshots/", "clips/", "reports/", "exports/", "watchlist/")


def data_root() -> Path:
    return settings.data_dir


def abs_path(rel_path: str) -> Path:
    return data_root() / rel_path


def safe_relative(rel_path: str) -> str | None:
    """Return a normalised relative path under DATA_DIR, or None when unsafe."""
    if not rel_path or rel_path.startswith(("/", "\\")) or ".." in rel_path.replace("\\", "/").split("/"):
        return None
    norm = os.path.normpath(rel_path).replace("\\", "/")
    if norm.startswith("../") or norm == ".." or os.path.isabs(norm):
        return None
    root = data_root().resolve()
    try:
        target = (root / norm).resolve()
    except OSError:
        return None
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return norm


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def write_bytes_hashed(rel_path: str, data: bytes) -> tuple[str, int]:
    """Write `data` atomically to DATA_DIR/rel_path; return (sha256, size_bytes)."""
    root = data_root()
    final = root / rel_path
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = root / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{uuid.uuid4().hex}.part"
    h = hashlib.sha256()
    with open(tmp, "wb") as fh:
        fh.write(data)
        h.update(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, final)
    return h.hexdigest(), len(data)


def write_stream_hashed(rel_path: str, chunks) -> tuple[str, int]:
    """Same as write_bytes_hashed for an iterable of byte chunks (large downloads)."""
    root = data_root()
    final = root / rel_path
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = root / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{uuid.uuid4().hex}.part"
    h = hashlib.sha256()
    size = 0
    with open(tmp, "wb") as fh:
        for block in chunks:
            fh.write(block)
            h.update(block)
            size += len(block)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, final)
    return h.hexdigest(), size


def ensure_layout() -> None:
    root = data_root()
    for sub in ("crops", "frames", "snapshots", "clips", "reports", "exports", "watchlist", "tmp"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    tmp = root / "tmp"
    for f in tmp.iterdir():
        try:
            if f.is_file():
                f.unlink()
        except OSError:
            pass


def media_url(rel_path: str | None) -> str | None:
    return f"/media/{rel_path}" if rel_path else None
