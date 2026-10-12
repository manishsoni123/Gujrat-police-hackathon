"""CLI: `python -m app.jobs.renormalise --dry-run | --run` - re-apply CONTRACT §3.4 to stored plates.

Rows stored before a §3.4 change (step 5a, real state codes, 2026-10-12) keep the `plate_norm` /
`is_valid_format` they were written with, so a search for `GJ27XY3456` misses reads stored as
`GI27XY3456` and garbage such as `BJ25I7596` stays "valid". This job recomputes them with
`app.services.plates.normalise`, whatever rule that function implements when the job runs.

What `--run` writes, per table:

* `plate_reads` - `plate_norm`, `is_valid_format` := §3.4 of the stored `plate_raw`, exactly as
  ingestion computes them (`services/sightings.py`). A new plate that another read of the same camera
  and instant already holds would violate `uq_reads_camera_captured_plate`: that read is reported
  (`reads_collision`) and keeps both stored values; it is never merged or deleted.
* `sightings` - `plate_norm`, `is_valid_format` follow the sighting's own reads (linked by
  `sighting_id`, carrying the sighting's plate), after the read updates: their common value, or the
  majority when they now disagree (tie -> the best read's value, then the smallest), reported as
  `sightings_mixed` and never split. A sighting with no such read (reads purged by retention)
  re-normalises its own stored plate, as ingestion does with the worker's plate. `worker_key` (the
  worker's idempotency key) and `frame_path` (a hashed evidence file) keep the old plate text.
* `qa_labels` - `is_match` / `char_errors` are recomputed for labels whose read plate changed (they
  compare `true_plate` with the read's plate, as `POST /qa/labels` does). `true_plate` is the
  labeller's input and is never rewritten (`qa_true_plate` reports the ones §3.4 would change).

Report only, never written: `watchlist` (user data), `alerts` (an alert keeps the plate that matched
its watchlist entry; `alerts_stale`), `route_confirmations.query_plate` (the user's query), and reads
whose new plate equals an active watchlist plate (`reads_new_watchlist_match`; no alert is raised
retroactively). `events.note` and `audit_log` are history and are not read.

`--dry-run` (default) runs in a read-only transaction, prints per-category counts plus up to 20
examples and writes nothing. `--run` takes an advisory lock, applies every update in one transaction
(one savepoint per read, guarded by the stored values, so a row changed or a plate taken meanwhile is
reported instead of failing the run) and then writes the audit row `plates.renormalise`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import setup_logging
from app.core.tz import iso_z, utcnow
from app.db.models import Alert, PlateRead, QaLabel, RouteConfirmation, Sighting, Watchlist
from app.db.session import SessionLocal, dispose
from app.services.audit import write_audit
from app.services.plates import levenshtein, normalise

log = logging.getLogger("sentinel.renormalise")

AUDIT_ACTION = "plates.renormalise"
MAX_EXAMPLES = 20
LOCK_KEY = 0x53504C52  # pg advisory lock id: one --run at a time
STREAM_BATCH = 5000

Value = tuple[str, bool]  # (plate_norm, is_valid_format)
Renorm = Callable[[str], Value]


def renormalise_raw(plate_raw: str) -> Value:
    """§3.4 of a read's stored `plate_raw`, with ingestion's fallback for a string without letters or digits."""
    n = normalise(plate_raw)
    return (n.plate_norm or (plate_raw or "").upper())[:16], n.is_valid_format


def renormalise_stored(plate_norm: str) -> Value:
    """§3.4 applied again to an already normalised string (sighting without reads, watchlist, query, QA truth)."""
    n = normalise(plate_norm)
    return (n.plate_norm or (plate_norm or "").upper())[:16], n.is_valid_format


# ---- rows (plain values, so the planning below never needs a database) ---------------------------


@dataclass(frozen=True)
class ReadRow:
    id: int
    camera_id: int
    captured_at: Any  # datetime from the DB; any orderable value in tests
    sighting_id: int | None
    plate_raw: str
    plate_norm: str
    is_valid_format: bool


@dataclass(frozen=True)
class SightingRow:
    id: int
    plate_norm: str
    is_valid_format: bool
    best_read_id: int | None = None


@dataclass(frozen=True)
class QaRow:
    id: int
    read_id: int
    true_plate: str
    is_match: bool
    char_errors: int


@dataclass(frozen=True)
class AlertRow:
    id: int
    type: str
    status: str
    read_id: int | None
    plate_norm: str | None


@dataclass(frozen=True)
class WatchRow:
    id: int
    entity_type: str
    plate_norm: str | None
    is_active: bool


@dataclass(frozen=True)
class ConfirmationRow:
    id: int
    query_plate: str
    sighting_id: int


@dataclass
class ReadChange:
    row: ReadRow
    new: Value
    holder_id: int | None = None  # collision: the read already holding `new` at this camera/instant

    @property
    def id(self) -> int:
        return self.row.id

    @property
    def old(self) -> Value:
        return (self.row.plate_norm, self.row.is_valid_format)

    @property
    def plate_changed(self) -> bool:
        return self.new[0] != self.row.plate_norm


@dataclass(frozen=True)
class SightingChange:
    id: int
    old: Value
    new: Value
    basis: str  # 'reads' | 'own_plate'


@dataclass(frozen=True)
class QaChange:
    id: int
    read_id: int
    true_plate: str
    read_plate: str
    old: tuple[bool, int]  # (is_match, char_errors)
    new: tuple[bool, int]


# ---- planning ------------------------------------------------------------------------------------


def resolve_slot(rows: list[ReadRow], renorm: Renorm = renormalise_raw) -> tuple[list[ReadChange], list[ReadChange]]:
    """Plan the reads of one (camera_id, captured_at) slot -> (updates in a safe order, collisions).

    Every update must keep `uq_reads_camera_captured_plate` intact after each statement (the index is
    not deferrable): a read moves to its new plate only when no read of the slot holds that plate at
    that point. Reads freed by an earlier move are retried until nothing moves, so `A: X->Y` with
    `B: Y->Z` applies B first. What still cannot move (the plate is kept by another read, or a swap)
    is a collision and keeps its stored plate *and* validity. Validity-only changes never collide.
    """
    holders = {r.plate_norm: r.id for r in rows}
    updates: list[ReadChange] = []
    moving: list[ReadChange] = []
    for r in sorted(rows, key=lambda x: x.id):
        new = renorm(r.plate_raw)
        if new == (r.plate_norm, r.is_valid_format):
            continue
        change = ReadChange(r, new)
        (moving if change.plate_changed else updates).append(change)
    while moving:
        blocked: list[ReadChange] = []
        for c in moving:
            if c.new[0] in holders:  # checked per move: two reads heading for one plate cannot both get it
                blocked.append(c)
                continue
            if holders.get(c.row.plate_norm) == c.id:
                del holders[c.row.plate_norm]
            holders[c.new[0]] = c.id
            updates.append(c)
        if len(blocked) == len(moving):
            break
        moving = blocked
    for c in moving:
        c.holder_id = holders.get(c.new[0])
    return updates, moving


def pick_value(votes: Counter[Value], best: Value | None) -> Value:
    """Majority value of a sighting's reads; tie -> the best read's value, then the smallest."""
    top = max(votes.values())
    tied = sorted(v for v, n in votes.items() if n == top)
    return best if best in tied else tied[0]


class Planner:
    """Incremental, DB-free plan for reads and sightings.

    Give it every sighting, then every read ordered by `(camera_id, captured_at)` (all reads of one
    slot must arrive together, the collision check works per slot), then call `finish_reads()`.
    """

    def __init__(self, sightings: Iterable[SightingRow] = (), renorm_raw: Renorm = renormalise_raw, renorm_stored: Renorm = renormalise_stored) -> None:
        self.renorm_raw = renorm_raw
        self.renorm_stored = renorm_stored
        self.sightings: dict[int, SightingRow] = {s.id: s for s in sightings}
        self.reads_scanned = 0
        self.updates: list[ReadChange] = []
        self.collisions: list[ReadChange] = []
        self.skipped: list[tuple[ReadChange, str]] = []  # planned updates that did not happen in --run
        self._votes: dict[int, Counter[Value]] = {}
        self._best: dict[int, Value] = {}
        self._evidence: dict[int, tuple[int, Value, Value]] = {}  # updated read id -> (sighting, old, new)
        self._slot: tuple[Any, Any] | None = None
        self._rows: list[ReadRow] = []

    def add_read(self, row: ReadRow) -> None:
        slot = (row.camera_id, row.captured_at)
        if slot != self._slot:
            if self._slot is not None and slot < self._slot:
                raise ValueError("reads must be ordered by (camera_id, captured_at)")
            self._flush()
            self._slot = slot
        self._rows.append(row)
        self.reads_scanned += 1

    def finish_reads(self) -> None:
        self._flush()

    def _flush(self) -> None:
        if not self._rows:
            return
        updates, collisions = resolve_slot(self._rows, self.renorm_raw)
        self.updates.extend(updates)
        self.collisions.extend(collisions)
        new_by_id = {c.id: c.new for c in updates}
        for r in self._rows:
            self._vote(r, new_by_id.get(r.id, (r.plate_norm, r.is_valid_format)))
        self._rows = []

    def _vote(self, r: ReadRow, value: Value) -> None:
        s = self.sightings.get(r.sighting_id) if r.sighting_id is not None else None
        if s is None or r.plate_norm != s.plate_norm:
            return  # only reads that carried the sighting's plate speak for it
        self._votes.setdefault(s.id, Counter())[value] += 1
        if r.id == s.best_read_id:
            self._best[s.id] = value
        old = (r.plate_norm, r.is_valid_format)
        if value != old:
            self._evidence[r.id] = (s.id, old, value)

    def skip_update(self, change: ReadChange, reason: str) -> None:
        """A planned read update did not happen (`--run`): drop it and give its sighting vote back."""
        self.updates = [c for c in self.updates if c.id != change.id]
        self.skipped.append((change, reason))
        ev = self._evidence.pop(change.id, None)
        if ev is None:
            return
        sid, old, new = ev
        votes = self._votes[sid]
        votes[new] -= 1
        if votes[new] <= 0:
            del votes[new]
        votes[old] += 1
        if self.sightings[sid].best_read_id == change.id:
            self._best[sid] = old

    def sighting_changes(self) -> tuple[list[SightingChange], list[tuple[int, dict[Value, int]]]]:
        """(sightings whose plate/validity must change, sightings whose reads now disagree)."""
        changes: list[SightingChange] = []
        mixed: list[tuple[int, dict[Value, int]]] = []
        for s in self.sightings.values():
            current = (s.plate_norm, s.is_valid_format)
            votes = self._votes.get(s.id)
            if votes:
                if len(votes) > 1:
                    mixed.append((s.id, dict(votes)))
                new, basis = pick_value(votes, self._best.get(s.id)), "reads"
            else:
                new, basis = self.renorm_stored(s.plate_norm), "own_plate"
            if new != current:
                changes.append(SightingChange(s.id, current, new, basis))
        return changes, mixed


def plan_qa(labels: Iterable[QaRow], read_plates: dict[int, str], renorm_stored: Renorm = renormalise_stored) -> tuple[list[QaChange], list[dict[str, Any]]]:
    """(labels to rescore against their read's new plate, labels whose `true_plate` §3.4 would change)."""
    rescored: list[QaChange] = []
    truth: list[dict[str, Any]] = []
    for q in labels:
        plate = read_plates.get(q.read_id)
        if plate is not None:
            new = (bool(q.true_plate) and q.true_plate == plate, levenshtein(q.true_plate, plate))
            if new != (q.is_match, q.char_errors):
                rescored.append(QaChange(q.id, q.read_id, q.true_plate, plate, (q.is_match, q.char_errors), new))
        if q.true_plate:
            would_be = renorm_stored(q.true_plate)[0]
            if would_be != q.true_plate:
                truth.append({"id": q.id, "read_id": q.read_id, "true_plate": q.true_plate, "would_be": would_be})
    return rescored, truth


def watchlist_report(rows: Iterable[WatchRow], renorm_stored: Renorm = renormalise_stored) -> list[dict[str, Any]]:
    """Entries §3.4 would now store differently, or (vehicles) reject as not a valid registration."""
    rows = list(rows)
    active = {r.plate_norm: r.id for r in rows if r.entity_type == "vehicle" and r.is_active and r.plate_norm}
    out: list[dict[str, Any]] = []
    for r in rows:
        if not r.plate_norm:
            continue
        new, valid = renorm_stored(r.plate_norm)
        vehicle = r.entity_type == "vehicle"
        if new == r.plate_norm and (valid or not vehicle):
            continue
        item: dict[str, Any] = {"id": r.id, "entity_type": r.entity_type, "is_active": r.is_active, "plate_norm": r.plate_norm, "would_be": new, "valid": valid}
        other = active.get(new)
        if vehicle and r.is_active and new != r.plate_norm and other is not None and other != r.id:
            item["conflicts_with"] = other  # uq_watchlist_active_vehicle_plate would refuse the edit
        out.append(item)
    return out


def confirmations_report(rows: Iterable[ConfirmationRow], renorm_stored: Renorm = renormalise_stored) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        would_be = renorm_stored(r.query_plate)[0]
        if would_be != r.query_plate:
            out.append({"id": r.id, "sighting_id": r.sighting_id, "query_plate": r.query_plate, "would_be": would_be})
    return out


def alerts_report(alerts: Iterable[AlertRow], changes_by_read: dict[int, ReadChange], renorm_stored: Renorm = renormalise_stored) -> list[dict[str, Any]]:
    """Alerts whose read this job updates, or whose own plate §3.4 would now store differently."""
    out = []
    for a in alerts:
        change = changes_by_read.get(a.read_id) if a.read_id is not None else None
        would_be = renorm_stored(a.plate_norm)[0] if a.plate_norm else None
        if change is None and (would_be is None or would_be == a.plate_norm):
            continue
        item: dict[str, Any] = {"id": a.id, "type": a.type, "status": a.status, "plate_norm": a.plate_norm, "read_id": a.read_id}
        if change is not None:
            item["read_now"] = change.new[0]
            item["read_valid_now"] = change.new[1]
        if would_be is not None and would_be != a.plate_norm:
            item["would_be"] = would_be
        out.append(item)
    return out


def new_watchlist_matches(updates: Iterable[ReadChange], active_plates: set[str]) -> list[ReadChange]:
    """Updated reads that now exactly equal an active watchlist plate and did not before (no alert is raised)."""
    return [
        c for c in updates
        if c.new[1] and c.new[0] in active_plates and not (c.row.is_valid_format and c.row.plate_norm in active_plates)
    ]


def _iso(v: Any) -> Any:
    return iso_z(v) if isinstance(v, datetime) else v


def _read_example(c: ReadChange) -> dict[str, Any]:
    item = {"id": c.id, "camera_id": c.row.camera_id, "captured_at": _iso(c.row.captured_at), "plate_raw": c.row.plate_raw, "old": list(c.old), "new": list(c.new)}
    if c.holder_id is not None:
        item["held_by_read"] = c.holder_id
    return item


@dataclass
class Plan:
    """Everything one run needs; `report()` reflects the planner's current state (after `--run` skips)."""

    planner: Planner
    qa: list[QaRow] = field(default_factory=list)
    alerts: list[AlertRow] = field(default_factory=list)
    watchlist: list[WatchRow] = field(default_factory=list)
    confirmations: list[ConfirmationRow] = field(default_factory=list)
    active_plates: set[str] = field(default_factory=set)
    sightings_skipped: list[int] = field(default_factory=list)  # --run: changed meanwhile
    qa_skipped: list[int] = field(default_factory=list)

    def read_plates(self) -> dict[int, str]:
        return {c.id: c.new[0] for c in self.planner.updates if c.plate_changed}

    def report(self, dry_run: bool, max_examples: int = MAX_EXAMPLES) -> dict[str, Any]:
        p = self.planner
        n = max_examples
        updates = p.updates
        plate_changed = [c for c in updates if c.plate_changed]
        validity_only = [c for c in updates if not c.plate_changed]
        sighting_changes, mixed = p.sighting_changes()
        s_skipped, q_skipped = set(self.sightings_skipped), set(self.qa_skipped)
        sighting_changes = [s for s in sighting_changes if s.id not in s_skipped]
        rescored, truth = plan_qa(self.qa, self.read_plates(), p.renorm_stored)
        rescored = [q for q in rescored if q.id not in q_skipped]
        alerts = alerts_report(self.alerts, {c.id: c for c in updates}, p.renorm_stored)
        watch = watchlist_report(self.watchlist, p.renorm_stored)
        confirmations = confirmations_report(self.confirmations, p.renorm_stored)
        matches = new_watchlist_matches(updates, self.active_plates)
        transitions = Counter((c.old, c.new) for c in updates)
        counts = {
            "reads_scanned": p.reads_scanned,
            "reads_changed": len(updates),
            "reads_plate_changed": len(plate_changed),
            "reads_validity_only": len(validity_only),
            "reads_became_valid": sum(1 for c in updates if c.new[1] and not c.old[1]),
            "reads_became_invalid": sum(1 for c in updates if c.old[1] and not c.new[1]),
            "reads_collision": len(p.collisions),
            "reads_skipped_at_run": len(p.skipped),
            "reads_new_watchlist_match": len(matches),
            "sightings_scanned": len(p.sightings),
            "sightings_changed": len(sighting_changes),
            "sightings_from_reads": sum(1 for s in sighting_changes if s.basis == "reads"),
            "sightings_from_own_plate": sum(1 for s in sighting_changes if s.basis == "own_plate"),
            "sightings_mixed": len(mixed),
            "sightings_skipped_at_run": len(self.sightings_skipped),
            "qa_labels_rescored": len(rescored),
            "qa_labels_skipped_at_run": len(self.qa_skipped),
            "qa_true_plate": len(truth),
            "alerts_stale": len(alerts),
            "watchlist_would_change": sum(1 for w in watch if w["would_be"] != w["plate_norm"]),
            "watchlist_now_invalid": sum(1 for w in watch if w["entity_type"] == "vehicle" and not w["valid"]),
            "watchlist_conflicts": sum(1 for w in watch if "conflicts_with" in w),
            "route_confirmations_would_change": len(confirmations),
        }
        examples: dict[str, list[Any]] = {
            "read_transitions": [
                {"old": list(old), "new": list(new), "reads": k} for (old, new), k in sorted(transitions.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
            ],
            "reads_plate_changed": [_read_example(c) for c in plate_changed[:n]],
            "reads_validity_only": [_read_example(c) for c in validity_only[:n]],
            "reads_collision": [_read_example(c) for c in p.collisions[:n]],
            "reads_skipped_at_run": [{**_read_example(c), "reason": why} for c, why in p.skipped[:n]],
            "reads_new_watchlist_match": [_read_example(c) for c in matches[:n]],
            "sightings_changed": [{"id": s.id, "old": list(s.old), "new": list(s.new), "basis": s.basis} for s in sighting_changes[:n]],
            "sightings_mixed": [{"id": sid, "reads": [{"value": list(v), "reads": k} for v, k in sorted(votes.items())]} for sid, votes in mixed[:n]],
            "qa_labels_rescored": [{"id": q.id, "read_id": q.read_id, "true_plate": q.true_plate, "read_plate": q.read_plate, "old": list(q.old), "new": list(q.new)} for q in rescored[:n]],
            "qa_true_plate": truth[:n],
            "alerts_stale": alerts[:n],
            "watchlist": watch[:n],
            "route_confirmations": confirmations[:n],
        }
        return {
            "dry_run": dry_run,
            "written": [] if dry_run else ["plate_reads", "sightings", "qa_labels"],
            "report_only": ["watchlist", "alerts", "route_confirmations", "qa_labels.true_plate"],
            "counts": counts,
            "examples": {k: v for k, v in examples.items() if v},
        }

    def audit_after(self) -> dict[str, Any]:
        return self.report(dry_run=False, max_examples=0)["counts"]


# ---- database ------------------------------------------------------------------------------------


async def collect(db: AsyncSession) -> Plan:
    """Read every plate-bearing row and plan (SELECTs only)."""
    sightings = [
        SightingRow(*r)
        async for r in await db.stream(
            select(Sighting.id, Sighting.plate_norm, Sighting.is_valid_format, Sighting.best_read_id).execution_options(yield_per=STREAM_BATCH)
        )
    ]
    planner = Planner(sightings)
    reads = await db.stream(
        select(PlateRead.id, PlateRead.camera_id, PlateRead.captured_at, PlateRead.sighting_id, PlateRead.plate_raw, PlateRead.plate_norm, PlateRead.is_valid_format)
        .order_by(PlateRead.camera_id, PlateRead.captured_at, PlateRead.id)
        .execution_options(yield_per=STREAM_BATCH)
    )
    async for r in reads:
        planner.add_read(ReadRow(*r))
    planner.finish_reads()
    qa = [QaRow(*r) for r in (await db.execute(select(QaLabel.id, QaLabel.read_id, QaLabel.true_plate, QaLabel.is_match, QaLabel.char_errors))).all()]
    alerts = [AlertRow(*r) for r in (await db.execute(select(Alert.id, Alert.type, Alert.status, Alert.read_id, Alert.plate_norm).where(Alert.plate_norm.isnot(None)))).all()]
    watch = [WatchRow(*r) for r in (await db.execute(select(Watchlist.id, Watchlist.entity_type, Watchlist.plate_norm, Watchlist.is_active))).all()]
    confirmations = [ConfirmationRow(*r) for r in (await db.execute(select(RouteConfirmation.id, RouteConfirmation.query_plate, RouteConfirmation.sighting_id))).all()]
    active = set(
        (
            await db.execute(
                select(Watchlist.plate_norm).where(
                    Watchlist.entity_type == "vehicle", Watchlist.is_active.is_(True), Watchlist.plate_norm.isnot(None),
                    or_(Watchlist.expires_at.is_(None), Watchlist.expires_at > func.now()),
                )
            )
        ).scalars().all()
    )
    return Plan(planner, qa, alerts, watch, confirmations, active)


async def apply(db: AsyncSession, plan: Plan) -> None:
    """Write the plan inside the caller's transaction. Every UPDATE is guarded by the values it was planned from."""
    p = plan.planner
    for c in list(p.updates):
        try:
            async with db.begin_nested():
                res = await db.execute(
                    update(PlateRead)
                    .where(PlateRead.id == c.id, PlateRead.plate_norm == c.row.plate_norm, PlateRead.is_valid_format == c.row.is_valid_format)
                    .values(plate_norm=c.new[0], is_valid_format=c.new[1])
                    .execution_options(synchronize_session=False)
                )
            if res.rowcount != 1:
                p.skip_update(c, "read changed or deleted meanwhile")
        except IntegrityError:
            p.skip_update(c, "plate taken meanwhile (uq_reads_camera_captured_plate)")
    now = utcnow()
    sighting_changes, _mixed = p.sighting_changes()
    for s in sighting_changes:
        res = await db.execute(
            update(Sighting)
            .where(Sighting.id == s.id, Sighting.plate_norm == s.old[0], Sighting.is_valid_format == s.old[1])
            .values(plate_norm=s.new[0], is_valid_format=s.new[1], updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:
            plan.sightings_skipped.append(s.id)
    rescored, _truth = plan_qa(plan.qa, plan.read_plates(), p.renorm_stored)
    for q in rescored:
        res = await db.execute(
            update(QaLabel)
            .where(QaLabel.id == q.id, QaLabel.true_plate == q.true_plate)
            .values(is_match=q.new[0], char_errors=q.new[1])
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:
            plan.qa_skipped.append(q.id)


async def run_renormalise(dry_run: bool = True) -> dict[str, Any]:
    async with SessionLocal() as db:
        if dry_run:
            # one consistent snapshot, and the database itself refuses any write
            await db.connection(execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True})
        elif not (await db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_KEY})).scalar():
            raise RuntimeError("another plates.renormalise run holds the lock")
        plan = await collect(db)
        if dry_run:
            await db.rollback()
        else:
            await apply(db, plan)
            await db.commit()
    result = plan.report(dry_run)
    if not dry_run:
        await write_audit(AUDIT_ACTION, after=plan.audit_after())
    log.info("plates renormalise %s: %s", "dry-run" if dry_run else "done", result["counts"])
    return result


async def _main(dry_run: bool) -> int:
    try:
        result = await run_renormalise(dry_run=dry_run)
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    finally:
        await dispose()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentinel Gujarat: re-apply plate normalisation (CONTRACT §3.4) to stored rows")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="default: report counts and examples, write nothing, no audit row")
    g.add_argument("--run", action="store_true", help="update reads, sightings and QA scores, then write audit plates.renormalise")
    args = parser.parse_args()
    setup_logging()
    sys.exit(asyncio.run(_main(not args.run)))


if __name__ == "__main__":
    main()
