"""Idempotent seed (CONTRACT §2.6, §2.7, §12): `python -m app.seed`, also run on API start.

Loads departments, jury users, the two API keys, POIs, district polygons, the watchlist seed and
the env-derived settings. Every step upserts on a natural key so re-running is harmless.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import DEFAULT_BULK_API_KEY, settings
from app.core.security import api_key_prefix, hash_api_key, hash_password, valid_api_key_format
from app.core.tz import parse_iso, utcnow
from app.db.models import ApiKey, Department, District, Poi, Setting, User, Watchlist
from app.db.session import SessionLocal, dispose, init_db
from app.services import settings_service as cfg
from app.services.districts import canonical_district
from app.services.plates import normalise

log = logging.getLogger("sentinel.seed")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


async def seed_departments(db: AsyncSession, seeds: Path) -> dict[str, int]:
    rows = _read_csv(seeds / "departments.csv")
    existing = {d.code: d for d in (await db.execute(select(Department))).scalars().all()}
    added = updated = 0
    for r in rows:
        code = r["code"].upper()
        aliases = sorted({a.strip().lower() for a in r.get("aliases", "").split("|") if a.strip()})
        d = existing.get(code)
        if d is None:
            db.add(Department(code=code, name=r["name"], aliases=aliases, created_at=utcnow()))
            added += 1
        elif d.name != r["name"] or sorted(d.aliases or []) != aliases:
            d.name, d.aliases = r["name"], aliases
            updated += 1
    await db.commit()
    return {"added": added, "updated": updated}


async def seed_users(db: AsyncSession, seeds: Path) -> dict[str, int]:
    rows = _read_csv(seeds / "users.csv")
    depts = {d.code: d.id for d in (await db.execute(select(Department))).scalars().all()}
    added = rehashed = 0
    for r in rows:
        username = r["username"].lower()
        password = os.environ.get(r["password_env"]) or getattr(settings, r["password_env"], None)
        if not password:
            log.warning("seed user %s: env %s empty – skipped", username, r["password_env"])
            continue
        pw_marker = seed_password_marker(password)
        marker_key = f"seed.pwhash.{username}"
        marker = await db.get(Setting, marker_key)
        user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if user is None:
            db.add(
                User(
                    username=username, password_hash=hash_password(password), full_name=r["full_name"], role=r["role"],
                    department_id=depts.get(r.get("department_code", "").upper()) if r.get("department_code") else None,
                    district=canonical_district(r["district"]) if r.get("district") else None,
                    is_active=True, created_at=utcnow(), updated_at=utcnow(),
                )
            )
            added += 1
        elif marker is not None and marker.value == _legacy_password_marker(password):
            pass  # pre-HMAC marker for the same env value: migrate the marker below, keep the password
        elif marker is None or marker.value != pw_marker:
            user.password_hash = hash_password(password)
            user.updated_at = utcnow()
            rehashed += 1
        if marker is None:
            db.add(Setting(key=marker_key, value=pw_marker, is_secret=True, updated_at=utcnow()))
        elif marker.value != pw_marker:
            marker.value = pw_marker
            marker.updated_at = utcnow()
    await db.commit()
    return {"added": added, "rehashed": rehashed}


def seed_password_marker(password: str) -> str:
    """Change marker for a seeded env password: HMAC-SHA256 keyed with `JWT_SECRET`.

    The marker only has to answer "did the env value change since the last seed run" (so a
    password changed through the UI survives restarts). A keyed hash cannot be cracked offline
    from a settings-table dump the way the earlier bare SHA-256 could.
    """
    return hmac.new(settings.JWT_SECRET.encode("utf-8"), password.encode("utf-8"), hashlib.sha256).hexdigest()


def _legacy_password_marker(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def seed_key_active(env_name: str, key: str, public: bool) -> bool:
    """A `bulk` key equal to the published repository default is seeded **inactive** on a public
    deployment: `/api/v1/cameras/bulk` is reachable from the Internet through Caddy and the default
    key is printed in README/CONTRACT. The internal key stays active (Caddy blocks `/api/internal/*`
    from outside) but is reported by `default_secrets_in_use`."""
    return not (public and env_name == "BULK_API_KEY" and key == DEFAULT_BULK_API_KEY)


async def seed_api_keys(db: AsyncSession) -> dict[str, int]:
    admin = (await db.execute(select(User).where(User.username == "jury_admin"))).scalar_one_or_none()
    added = rotated = deactivated = 0
    public = settings.is_public
    for name, scope, env_name in (("seed-bulk", "bulk", "BULK_API_KEY"), ("seed-internal", "internal", "INTERNAL_API_KEY")):
        key = getattr(settings, env_name)
        if not valid_api_key_format(key):
            log.warning("API key for %s does not match sk_[0-9a-z]{40}; skipped", name)
            continue
        active = seed_key_active(env_name, key, public)
        if not active:
            log.error("%s is the repository default on a public deployment: the seeded '%s' key is inactive until it is rotated in deploy/.env", env_name, name)
        row = (await db.execute(select(ApiKey).where(ApiKey.name == name))).scalar_one_or_none()
        h = hash_api_key(key)
        if row is None:
            db.add(ApiKey(name=name, key_hash=h, key_prefix=api_key_prefix(key), scope=scope, created_by=admin.id if admin else None, is_active=active, created_at=utcnow()))
            added += 1
        elif row.key_hash != h:
            row.key_hash, row.key_prefix, row.is_active = h, api_key_prefix(key), active
            rotated += 1
        elif not active and row.is_active:
            row.is_active = False
            deactivated += 1
    await db.commit()
    return {"added": added, "rotated": rotated, "deactivated": deactivated}


async def seed_pois(db: AsyncSession, seeds: Path) -> dict[str, int]:
    rows = _read_csv(seeds / "pois.csv")
    existing = {(p.name, p.district): p for p in (await db.execute(select(Poi))).scalars().all()}
    added = updated = 0
    for r in rows:
        district = canonical_district(r["district"])
        lat, lon = float(r["lat"]), float(r["lon"])
        p = existing.get((r["name"], district))
        geog = f"SRID=4326;POINT({lon} {lat})"
        if p is None:
            db.add(Poi(name=r["name"], type=r["type"].lower(), district=district, lat=lat, lon=lon, geog=geog))
            added += 1
        elif (p.lat, p.lon, p.type) != (lat, lon, r["type"].lower()):
            p.lat, p.lon, p.type, p.geog = lat, lon, r["type"].lower(), geog
            updated += 1
    await db.commit()
    return {"added": added, "updated": updated}


def _district_file(seeds: Path) -> Path | None:
    full = seeds / "gujarat_districts.geojson"
    if full.exists():
        return full
    fallback = seeds / "gujarat_districts_fallback.json"
    if fallback.exists():
        log.warning("gujarat_districts.geojson missing – loading the hand-drawn fallback polygons")
        return fallback
    return None


async def seed_districts(db: AsyncSession, seeds: Path) -> dict[str, int]:
    path = _district_file(seeds)
    if path is None:
        log.warning("no district GeoJSON found – district layer and gap grid will be empty")
        return {"added": 0, "updated": 0}
    data = json.loads(path.read_text(encoding="utf-8"))
    existing = {d.name: d for d in (await db.execute(select(District))).scalars().all()}
    added = updated = 0
    for feat in data.get("features", []):
        props = feat.get("properties") or {}
        raw_name = props.get("district") or props.get("DISTRICT") or props.get("name")
        if not raw_name or not feat.get("geometry"):
            continue
        name = canonical_district(str(raw_name))
        code = (props.get("code") or None)
        geom_expr = func.ST_Multi(func.ST_SetSRID(func.ST_GeomFromGeoJSON(json.dumps(feat["geometry"])), 4326))
        d = existing.get(name)
        if d is None:
            db.add(District(name=name, code=code, geom=geom_expr))
            added += 1
        else:
            d.code = code or d.code
            d.geom = geom_expr
            updated += 1
    await db.commit()
    return {"added": added, "updated": updated}


async def seed_watchlist(db: AsyncSession, seeds: Path) -> dict[str, int]:
    rows = _read_csv(seeds / "watchlist_seed.csv")
    admin = (await db.execute(select(User).where(User.username == "jury_admin"))).scalar_one_or_none()
    vehicles = {w.plate_norm: w for w in (await db.execute(select(Watchlist).where(Watchlist.entity_type == "vehicle"))).scalars().all() if w.plate_norm}
    persons = {w.name: w for w in (await db.execute(select(Watchlist).where(Watchlist.entity_type == "person"))).scalars().all() if w.name}
    added = skipped = 0
    now = utcnow()
    for r in rows:
        entity = (r.get("entity_type") or "vehicle").lower()
        reason, priority, source = (r.get("reason") or "other").lower(), (r.get("priority") or "medium").lower(), (r.get("source") or "manual").lower()
        expires = parse_iso(r["expires_at"]) if r.get("expires_at") else None
        active = (r.get("is_active") or "true").lower() in ("true", "1", "yes")
        if entity == "vehicle":
            n = normalise(r.get("plate") or "")
            if not n.is_valid_format:
                log.warning("watchlist seed: invalid plate %r skipped", r.get("plate"))
                skipped += 1
                continue
            if n.plate_norm in vehicles:
                skipped += 1
                continue
            w = Watchlist(entity_type="vehicle", plate_norm=n.plate_norm)
            vehicles[n.plate_norm] = w
        else:
            if r.get("name") in persons:
                skipped += 1
                continue
            w = Watchlist(entity_type="person", plate_norm=None)
            persons[r.get("name") or ""] = w
        w.name, w.reason, w.priority, w.source = r.get("name") or None, reason, priority, source
        w.notes, w.expires_at, w.is_active = r.get("notes") or None, expires, active
        w.added_by, w.hit_count, w.created_at, w.updated_at = (admin.id if admin else None), 0, now, now
        db.add(w)
        added += 1
    upgraded = await _upgrade_watchlist_seed(db, vehicles, now)
    await db.commit()
    return {"added": added, "skipped": skipped, "upgraded": upgraded}


# Revision of the seeded watchlist rows. Existing databases only ever *add* missing rows, so a
# change to an already-seeded anchor row is applied once here, keyed by `seed.watchlist_rev`.
WATCHLIST_SEED_REV = 3

# rev 3 (5 Sept 2026, government feed): the fourteen synthetic filler plates of seed rev 1-2 (rows 7-20 of
# the old CSV, never read by any camera) are retired - deactivated, never deleted, so alert history and
# hit counts stay - and the CSV now carries the plates actually read on the organiser cameras instead.
RETIRED_FILLER_PLATES = (
    "GJ01CJ7788", "GJ03BM2210", "GJ05JE9834", "GJ12AK1001", "GJ33AT5566", "GJ38CH0007", "DL3CAB9911",
    "RJ14CV3030", "MP09HB6161", "GJ10AD7070", "GJ15CQ2424", "GJ21AR8181", "KA01MJ4545", "GJ09BW0110",
)
RETIRED_NOTE = "retired by seed rev 3 (synthetic filler plate, never read on any feed; replaced by plates read on the government cameras)"


async def _upgrade_watchlist_seed(db: AsyncSession, vehicles: dict[str, Watchlist], now: Any) -> int:
    marker = await db.get(Setting, "seed.watchlist_rev")
    current = int(marker.value) if marker is not None and str(marker.value).isdigit() else 1
    upgraded = 0
    if current < 2:
        # rev 2: only one synthetic anchor plate is seeded active+critical (GJ01AB1234). GJ18CD5678
        # (wanted → critical floor) appears on three looping cameras and flooded the demo with a
        # fresh critical alert per camera every re-alert window; it stays in the list, inactive.
        w = vehicles.get("GJ18CD5678")
        if w is not None and w.is_active and w.id is not None:
            w.is_active, w.updated_at = False, now
            upgraded += 1
    if current < 3:
        for plate in RETIRED_FILLER_PLATES:
            w = vehicles.get(plate)
            if w is not None and w.id is not None and w.is_active:
                w.is_active, w.updated_at = False, now
                w.notes = (f"{w.notes} | " if w.notes else "") + RETIRED_NOTE
                upgraded += 1
    if marker is None:
        db.add(Setting(key="seed.watchlist_rev", value=WATCHLIST_SEED_REV, is_secret=False, updated_at=now))
    elif current < WATCHLIST_SEED_REV:
        marker.value, marker.updated_at = WATCHLIST_SEED_REV, now
    return upgraded


async def seed_settings(db: AsyncSession) -> None:
    """Seed env-derived settings and merge the department aliases into `catalogue.department_aliases`."""
    await cfg.load(db)
    row = await db.get(Setting, "catalogue.department_aliases")
    if row is not None and row.updated_by is None:
        merged: dict[str, str] = dict(cfg.DEFAULT_DEPT_ALIASES)
        for d in (await db.execute(select(Department))).scalars().all():
            for a in d.aliases or []:
                merged.setdefault(a.lower(), d.code)
        if merged != row.value:
            row.value = merged
            row.updated_at = utcnow()
            await db.commit()
            cfg._cache["catalogue.department_aliases"] = merged  # keep the in-process cache current


async def run_seed(seeds_dir: Path | None = None) -> dict[str, Any]:
    seeds = seeds_dir or settings.seeds_dir
    summary: dict[str, Any] = {}
    async with SessionLocal() as db:
        summary["departments"] = await seed_departments(db, seeds)
        summary["users"] = await seed_users(db, seeds)
        summary["api_keys"] = await seed_api_keys(db)
        summary["pois"] = await seed_pois(db, seeds)
        summary["districts"] = await seed_districts(db, seeds)
        summary["watchlist"] = await seed_watchlist(db, seeds)
        await seed_settings(db)
    log.info("seed complete: %s", json.dumps(summary))
    return summary


async def _main() -> None:
    from app.core.logging import setup_logging

    setup_logging(settings.LOG_LEVEL)
    await init_db()
    summary = await run_seed()
    print(json.dumps(summary, indent=2))
    await dispose()


if __name__ == "__main__":
    asyncio.run(_main())
