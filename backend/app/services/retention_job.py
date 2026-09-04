"""Nightly retention purge (CONTRACT §10.3). Also runnable as `python -m app.jobs.retention`."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.hashing import abs_path, data_root
from app.core.tz import utcnow
from app.db.models import CameraHealthLog, Clip, ObjectCount, PlateRead, ReportFile, Sighting
from app.db.session import SessionLocal
from app.services import settings_service as cfg
from app.services.audit import write_audit

log = logging.getLogger("sentinel.retention")


def _unlink(rel: str | None, counts: dict[str, Any]) -> None:
    if not rel:
        return
    p = abs_path(rel)
    try:
        size = p.stat().st_size
        p.unlink()
        counts["bytes_freed"] += size
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("could not delete %s: %s", rel, exc)


async def run_retention(dry_run: bool = False) -> dict[str, Any]:
    counts: dict[str, Any] = {"reads": 0, "crops": 0, "frames": 0, "health_log": 0, "object_counts": 0, "clips": 0, "reports": 0, "bytes_freed": 0, "dry_run": dry_run}
    now = utcnow()
    reads_before = now - timedelta(days=cfg.get_int("retention.days_reads"))
    frames_before = now - timedelta(days=cfg.get_int("retention.days_frames"))
    clips_before = now - timedelta(days=cfg.get_int("retention.days_clips"))
    async with SessionLocal() as db:
        await _purge_reads(db, reads_before, counts, dry_run)
        await _purge_frames(db, frames_before, counts, dry_run)
        await _purge_rows(db, CameraHealthLog, CameraHealthLog.checked_at, now - timedelta(days=7), "health_log", counts, dry_run)
        await _purge_rows(db, ObjectCount, ObjectCount.minute, now - timedelta(days=90), "object_counts", counts, dry_run)
        await _purge_files(db, Clip, Clip.created_at, Clip.path, clips_before, "clips", counts, dry_run)
        await _purge_files(db, ReportFile, ReportFile.created_at, ReportFile.path, clips_before, "reports", counts, dry_run)
        if not dry_run:
            await db.commit()
    if not dry_run:
        _remove_empty_dirs()
        await write_audit("retention.purge", after=counts)
    log.info("retention purge %s: %s", "dry-run" if dry_run else "done", counts)
    return counts


async def _purge_reads(db: AsyncSession, before, counts, dry_run) -> None:
    rows = (await db.execute(select(PlateRead.id, PlateRead.crop_path).where(PlateRead.captured_at < before))).all()
    counts["reads"] = len(rows)
    counts["crops"] = sum(1 for r in rows if r[1])
    if dry_run or not rows:
        return
    for _id, crop in rows:
        _unlink(crop, counts)
    ids = [r[0] for r in rows]
    for i in range(0, len(ids), 5000):
        await db.execute(delete(PlateRead).where(PlateRead.id.in_(ids[i : i + 5000])))


async def _purge_frames(db: AsyncSession, before, counts, dry_run) -> None:
    rows = (await db.execute(select(Sighting.id, Sighting.frame_path).where(Sighting.first_seen < before, Sighting.frame_path.isnot(None)))).all()
    counts["frames"] = len(rows)
    if dry_run or not rows:
        return
    for _id, frame in rows:
        _unlink(frame, counts)
    ids = [r[0] for r in rows]
    await db.execute(update(Sighting).where(Sighting.id.in_(ids)).values(frame_path=None, frame_sha256=None))


async def _purge_rows(db: AsyncSession, model, col, before, key, counts, dry_run) -> None:
    from sqlalchemy import func

    n = int((await db.execute(select(func.count()).select_from(model).where(col < before))).scalar() or 0)
    counts[key] = n
    if not dry_run and n:
        await db.execute(delete(model).where(col < before))


async def _purge_files(db: AsyncSession, model, col, path_col, before, key, counts, dry_run) -> None:
    rows = (await db.execute(select(model.id, path_col).where(col < before))).all()
    counts[key] = len(rows)
    if dry_run or not rows:
        return
    for _id, path in rows:
        _unlink(path, counts)
    await db.execute(delete(model).where(model.id.in_([r[0] for r in rows])))


def _remove_empty_dirs() -> None:
    root = data_root()
    for sub in ("crops", "frames", "reports", "exports", "clips"):
        base = root / sub
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base, topdown=False):
            if dirpath == str(base):
                continue
            if not dirnames and not filenames:
                try:
                    os.rmdir(dirpath)
                except OSError:
                    pass
