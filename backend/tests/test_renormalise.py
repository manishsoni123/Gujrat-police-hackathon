"""Plate re-normalisation job (`python -m app.jobs.renormalise`): DB-free planning, apply guards, CLI.

Most tests inject a table-driven normaliser so they keep their meaning whatever §3.4 rule is current;
a few pin the real `normalise` on CONTRACT vectors (step 5a)."""

from __future__ import annotations

import json
import random
from collections import Counter
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.jobs import renormalise as job
from app.jobs.renormalise import (
    AlertRow,
    ConfirmationRow,
    Plan,
    Planner,
    QaRow,
    ReadChange,
    ReadRow,
    SightingRow,
    WatchRow,
    alerts_report,
    confirmations_report,
    new_watchlist_matches,
    pick_value,
    plan_qa,
    renormalise_raw,
    renormalise_stored,
    resolve_slot,
    watchlist_report,
)
from app.services.plates import normalise

T0 = datetime(2026, 10, 12, 13, 28, 1, 293000, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 12, 13, 28, 4, tzinfo=timezone.utc)


def fake(table: dict[str, tuple[str, bool]]):
    """Normaliser stub: listed strings map to the given value, anything else is unchanged and valid."""
    return lambda s: table.get(s, (s, True))


def read(id_, raw, norm=None, valid=True, cam=1, ts=T0, sighting=None) -> ReadRow:
    return ReadRow(id_, cam, ts, sighting, raw, raw if norm is None else norm, valid)


# ---- the normalisers mirror ingestion -------------------------------------------------------------


def test_renormalise_raw_matches_contract_step_5a() -> None:
    assert renormalise_raw("G101AB1234") == ("GJ01AB1234", True)  # was GI01AB1234
    assert renormalise_raw("GI27XY3456") == ("GJ27XY3456", True)
    assert renormalise_raw("8H01AB1234") == ("8H01AB1234", False)  # was BH01AB1234, valid
    assert renormalise_raw("gj 01 ab 1234") == ("GJ01AB1234", True)


@pytest.mark.parametrize("raw", ["GJ 01 AB 1234", "IND GJ05 RS 5678", "BJ25I7596", "S5S5S5S5", "22BH4321AA", "ABC"])
def test_renormalise_raw_is_the_ingest_formula(raw: str) -> None:
    n = normalise(raw)
    assert renormalise_raw(raw) == ((n.plate_norm or raw.upper())[:16], n.is_valid_format)


def test_renormalise_raw_falls_back_like_ingest_for_no_alnum() -> None:
    # services/sightings.py: plate_norm = (norm.plate_norm or plate_raw.upper())[:16]
    assert renormalise_raw("--") == ("--", False)
    assert renormalise_raw("IND") == ("IND", False)


def test_renormalise_stored_is_idempotent_on_current_output() -> None:
    for raw in ("G101AB1234", "GJ0IAB1234", "Z2BH4321AA", "S5S5S5S5", "GJ01AB12345"):
        n = normalise(raw)
        assert renormalise_stored(n.plate_norm) == (n.plate_norm, n.is_valid_format)
    assert renormalise_stored("GI27XY3456") == ("GJ27XY3456", True)


# ---- one (camera, instant) slot and uq_reads_camera_captured_plate --------------------------------


def test_slot_without_changes_plans_nothing() -> None:
    updates, collisions = resolve_slot([read(1, "GJ01AB1234"), read(2, "MH12DE1403")], fake({}))
    assert updates == [] and collisions == []


def test_slot_plate_change_and_validity_only_change() -> None:
    norm = fake({"GI27XY3456": ("GJ27XY3456", True), "BJ25I7596": ("BJ25I7596", False)})
    updates, collisions = resolve_slot([read(1, "GI27XY3456"), read(2, "BJ25I7596")], norm)
    assert [(c.id, c.old, c.new, c.plate_changed) for c in updates] == [
        (2, ("BJ25I7596", True), ("BJ25I7596", False), False),
        (1, ("GI27XY3456", True), ("GJ27XY3456", True), True),
    ]
    assert collisions == []


def test_slot_collision_with_an_existing_read_is_skipped_not_merged() -> None:
    norm = fake({"GI27XY3456": ("GJ27XY3456", True)})
    updates, collisions = resolve_slot([read(1, "GJ27XY3456"), read(2, "GI27XY3456")], norm)
    assert updates == []
    assert [(c.id, c.holder_id, c.new) for c in collisions] == [(2, 1, ("GJ27XY3456", True))]


def test_slot_two_reads_heading_for_one_plate_lowest_id_wins() -> None:
    norm = fake({"GI01AB1234": ("GJ01AB1234", True), "GL01AB1234": ("GJ01AB1234", True)})
    updates, collisions = resolve_slot([read(7, "GL01AB1234"), read(3, "GI01AB1234")], norm)
    assert [c.id for c in updates] == [3]
    assert [(c.id, c.holder_id) for c in collisions] == [(7, 3)]


def test_slot_chain_is_ordered_so_every_statement_keeps_the_index() -> None:
    # read 1: X -> Y while read 2 still holds Y; read 2: Y -> Z frees it. Read 2 must go first.
    norm = fake({"XX01AA1111": ("YY01AA1111", True), "YY01AA1111": ("ZZ01AA1111", True)})
    updates, collisions = resolve_slot([read(1, "XX01AA1111"), read(2, "YY01AA1111")], norm)
    assert [c.id for c in updates] == [2, 1] and collisions == []


def test_slot_swap_cannot_be_applied_row_by_row_and_is_reported() -> None:
    norm = fake({"XX01AA1111": ("YY01AA1111", True), "YY01AA1111": ("XX01AA1111", True)})
    updates, collisions = resolve_slot([read(1, "XX01AA1111"), read(2, "YY01AA1111")], norm)
    assert updates == [] and sorted(c.id for c in collisions) == [1, 2]


def test_collided_read_keeps_its_validity_too() -> None:
    norm = fake({"GI27XY3456": ("GJ27XY3456", False)})
    _updates, collisions = resolve_slot([read(1, "GJ27XY3456"), read(2, "GI27XY3456", valid=True)], norm)
    assert collisions[0].old == ("GI27XY3456", True)


def test_random_slots_never_break_the_unique_index_when_applied_in_order() -> None:
    rng = random.Random(20261013)
    plates = [f"P{i}" for i in range(5)]
    for _ in range(300):
        size = rng.randint(1, 5)
        current = rng.sample(plates, size)
        rows = [read(i + 1, raw=f"raw{i}", norm=current[i]) for i in range(size)]
        table = {f"raw{i}": (rng.choice(plates), True) for i in range(size)}
        updates, collisions = resolve_slot(rows, lambda s, t=table: t[s])
        state = {r.id: r.plate_norm for r in rows}
        for c in updates:  # apply one statement at a time, like apply()
            state[c.id] = c.new[0]
            assert len(set(state.values())) == len(state), (rows, table, updates)
        assert {c.id for c in updates}.isdisjoint({c.id for c in collisions})
        for c in collisions:
            assert c.holder_id is not None and state[c.holder_id] == c.new[0]


# ---- planner: reads across slots, sightings -------------------------------------------------------


def test_planner_requires_reads_grouped_by_slot() -> None:
    p = Planner([], fake({}))
    p.add_read(read(1, "A", cam=2))
    with pytest.raises(ValueError):
        p.add_read(read(2, "B", cam=1))


def test_same_plate_at_different_instants_or_cameras_is_not_a_collision() -> None:
    norm = fake({"GI27XY3456": ("GJ27XY3456", True)})
    p = Planner([], norm)
    for r in (read(1, "GJ27XY3456", ts=T0), read(2, "GI27XY3456", ts=T1), read(3, "GI27XY3456", cam=2, ts=T0)):
        p.add_read(r)
    p.finish_reads()
    assert [c.id for c in p.updates] == [2, 3] and p.collisions == [] and p.reads_scanned == 3


def _plan(sightings, reads, table, stored=None) -> Planner:
    p = Planner(sightings, fake(table), fake(stored if stored is not None else table))
    for r in sorted(reads, key=lambda r: (r.camera_id, r.captured_at, r.id)):
        p.add_read(r)
    p.finish_reads()
    return p


def test_sighting_follows_its_reads() -> None:
    s = SightingRow(10, "GI27XY3456", True, best_read_id=1)
    reads = [read(i, "GI27XY3456", ts=t, sighting=10) for i, t in ((1, T0), (2, T1))]
    changes, mixed = _plan([s], reads, {"GI27XY3456": ("GJ27XY3456", True)}).sighting_changes()
    assert [(c.id, c.old, c.new, c.basis) for c in changes] == [(10, ("GI27XY3456", True), ("GJ27XY3456", True), "reads")]
    assert mixed == []


def test_sighting_validity_follows_its_reads() -> None:
    s = SightingRow(10, "BJ25I7596", True)
    changes, _ = _plan([s], [read(1, "BJ25I7596", sighting=10)], {"BJ25I7596": ("BJ25I7596", False)}).sighting_changes()
    assert [(c.new, c.basis) for c in changes] == [(("BJ25I7596", False), "reads")]


def test_sighting_whose_reads_now_disagree_takes_the_majority_and_is_reported() -> None:
    s = SightingRow(10, "GI01AB1234", True, best_read_id=3)
    reads = [
        read(1, "GI01AB1234", ts=T0, sighting=10),
        read(2, "GI01AB1234", ts=T1, sighting=10),
        read(3, "G101AB1234", norm="GI01AB1234", ts=datetime(2026, 10, 12, 13, 28, 7, tzinfo=timezone.utc), sighting=10),
    ]
    table = {"GI01AB1234": ("GJ01AB1234", True), "G101AB1234": ("GL01AB1234", False)}
    changes, mixed = _plan([s], reads, table).sighting_changes()
    assert [c.new for c in changes] == [("GJ01AB1234", True)]
    assert mixed == [(10, {("GJ01AB1234", True): 2, ("GL01AB1234", False): 1})]


def test_pick_value_tie_prefers_best_read_then_smallest() -> None:
    votes = Counter({("B", True): 1, ("A", True): 1})
    assert pick_value(votes, ("B", True)) == ("B", True)
    assert pick_value(votes, None) == ("A", True)
    assert pick_value(Counter({("B", True): 2, ("A", True): 1}), ("A", True)) == ("B", True)


def test_sighting_without_reads_renormalises_its_own_plate() -> None:
    s = SightingRow(10, "GI27XY3456", True)
    changes, _ = _plan([s], [], {}, stored={"GI27XY3456": ("GJ27XY3456", True)}).sighting_changes()
    assert [(c.new, c.basis) for c in changes] == [(("GJ27XY3456", True), "own_plate")]


def test_reads_with_another_plate_do_not_speak_for_the_sighting() -> None:
    s = SightingRow(10, "GJ01AB1234", True)
    p = _plan([s], [read(1, "GI27XY3456", sighting=10)], {"GI27XY3456": ("GJ27XY3456", True)}, stored={})
    assert p.sighting_changes() == ([], [])


def test_unchanged_sighting_is_not_listed() -> None:
    s = SightingRow(10, "GJ01AB1234", True)
    assert _plan([s], [read(1, "GJ01AB1234", sighting=10)], {}).sighting_changes() == ([], [])


def test_collided_read_keeps_its_sighting_unchanged() -> None:
    s = SightingRow(10, "GI27XY3456", True)
    reads = [read(1, "GJ27XY3456"), read(2, "GI27XY3456", sighting=10)]  # same camera and instant
    p = _plan([s], reads, {"GI27XY3456": ("GJ27XY3456", True)})
    assert [c.id for c in p.collisions] == [2]
    assert p.sighting_changes() == ([], [])


def test_skip_update_gives_the_vote_back() -> None:
    s = SightingRow(10, "GI27XY3456", True, best_read_id=1)
    p = _plan([s], [read(1, "GI27XY3456", sighting=10)], {"GI27XY3456": ("GJ27XY3456", True)})
    assert len(p.sighting_changes()[0]) == 1
    p.skip_update(p.updates[0], "read changed or deleted meanwhile")
    assert p.updates == [] and [why for _c, why in p.skipped] == ["read changed or deleted meanwhile"]
    assert p.sighting_changes() == ([], [])


# ---- report-only tables and QA rescoring ----------------------------------------------------------


def test_qa_labels_are_rescored_against_the_new_read_plate_and_truth_is_only_reported() -> None:
    labels = [QaRow(1, 100, "GJ27XY3456", False, 1), QaRow(2, 200, "GI01AB1234", True, 0), QaRow(3, 300, "", False, 10)]
    rescored, truth = plan_qa(labels, {100: "GJ27XY3456", 300: "GJ01AB1234"}, fake({"GI01AB1234": ("GJ01AB1234", True)}))
    assert [(q.id, q.old, q.new) for q in rescored] == [(1, (False, 1), (True, 0))]  # label 3 already scores (False, 10)
    assert truth == [{"id": 2, "read_id": 200, "true_plate": "GI01AB1234", "would_be": "GJ01AB1234"}]


def test_watchlist_is_reported_never_planned_for_writing() -> None:
    rows = [
        WatchRow(1, "vehicle", "GI27XY3456", True),   # would become GJ27XY3456, held by entry 2
        WatchRow(2, "vehicle", "GJ27XY3456", True),
        WatchRow(3, "vehicle", "BJ25I7596", False),   # same text, now not a valid registration
        WatchRow(4, "person", None, True),
        WatchRow(5, "vehicle", "GJ01AB1234", True),
    ]
    norm = fake({"GI27XY3456": ("GJ27XY3456", True), "BJ25I7596": ("BJ25I7596", False)})
    out = watchlist_report(rows, norm)
    assert out == [
        {"id": 1, "entity_type": "vehicle", "is_active": True, "plate_norm": "GI27XY3456", "would_be": "GJ27XY3456", "valid": True, "conflicts_with": 2},
        {"id": 3, "entity_type": "vehicle", "is_active": False, "plate_norm": "BJ25I7596", "would_be": "BJ25I7596", "valid": False},
    ]


def test_confirmations_and_alerts_reports() -> None:
    norm = fake({"GI27XY3456": ("GJ27XY3456", True)})
    assert confirmations_report([ConfirmationRow(1, "GI27XY3456", 9), ConfirmationRow(2, "GJ01AB1234", 9)], norm) == [
        {"id": 1, "sighting_id": 9, "query_plate": "GI27XY3456", "would_be": "GJ27XY3456"}
    ]
    change = ReadChange(read(100, "BJ25I7596"), ("BJ25I7596", False))
    alerts = [AlertRow(1, "watchlist_hit", "new", 100, "BJ25I7596"), AlertRow(2, "watchlist_hit", "closed", None, "GI27XY3456"), AlertRow(3, "watchlist_hit", "new", 5, "GJ01AB1234")]
    assert alerts_report(alerts, {100: change}, norm) == [
        {"id": 1, "type": "watchlist_hit", "status": "new", "plate_norm": "BJ25I7596", "read_id": 100, "read_now": "BJ25I7596", "read_valid_now": False},
        {"id": 2, "type": "watchlist_hit", "status": "closed", "plate_norm": "GI27XY3456", "read_id": None, "would_be": "GJ27XY3456"},
    ]


def test_new_watchlist_matches_only_counts_reads_that_did_not_match_before() -> None:
    a = ReadChange(read(1, "GI27XY3456"), ("GJ27XY3456", True))
    b = ReadChange(read(2, "GJ01AB1234"), ("GJ01AB1234", False))
    c = ReadChange(read(3, "GJ01AB1234X", norm="GJ01AB1234X", valid=False), ("GJ01AB1234", True))
    d = ReadChange(read(4, "GL01AB1234", norm="GJ01AB1234", valid=True), ("GJ01AB1234", True))
    assert [x.id for x in new_watchlist_matches([a, b, c, d], {"GJ27XY3456", "GJ01AB1234"})] == [1, 3]


# ---- report ---------------------------------------------------------------------------------------


def _sample_plan(n_reads: int = 30) -> Plan:
    table = {f"GI{i:02d}XY3456": (f"GJ{i:02d}XY3456", True) for i in range(1, 40)}
    sightings = [SightingRow(1000 + i, f"GI{i:02d}XY3456", True, best_read_id=i) for i in range(1, n_reads + 1)]
    reads = [read(i, f"GI{i:02d}XY3456", ts=T0, cam=i, sighting=1000 + i) for i in range(1, n_reads + 1)]
    reads.append(read(n_reads + 1, "GJ01XY3456", cam=1, ts=T0))
    reads.append(read(n_reads + 2, "BJ25I7596", cam=99))
    table["BJ25I7596"] = ("BJ25I7596", False)
    p = _plan(sightings, reads, table)
    return Plan(
        p,
        qa=[QaRow(1, 2, "GJ02XY3456", False, 1)],
        alerts=[AlertRow(7, "watchlist_hit", "new", n_reads + 2, "BJ25I7596")],
        watchlist=[WatchRow(1, "vehicle", "GI05XY3456", True)],
        confirmations=[ConfirmationRow(1, "GI03XY3456", 1003)],
        active_plates={"GJ04XY3456"},
    )


def test_report_counts_examples_and_is_json() -> None:
    rep = _sample_plan().report(dry_run=True)
    c = rep["counts"]
    assert rep["dry_run"] is True and rep["written"] == []
    assert c["reads_scanned"] == 32
    assert c["reads_changed"] == 30 and c["reads_plate_changed"] == 29 and c["reads_validity_only"] == 1
    assert c["reads_collision"] == 1  # read 1 (GI01→GJ01) at cam 1 / T0, where read 31 holds GJ01XY3456
    assert c["reads_became_invalid"] == 1 and c["reads_became_valid"] == 0
    assert c["reads_new_watchlist_match"] == 1
    assert c["sightings_scanned"] == 30 and c["sightings_changed"] == 29 and c["sightings_from_reads"] == 29
    assert c["qa_labels_rescored"] == 1 and c["alerts_stale"] == 1
    assert c["watchlist_would_change"] == 1 and c["route_confirmations_would_change"] == 1
    ex = rep["examples"]
    assert all(len(v) <= 20 for v in ex.values())
    assert len(ex["reads_plate_changed"]) == 20 and len(ex["sightings_changed"]) == 20
    assert ex["reads_collision"][0]["held_by_read"] == 31
    assert ex["reads_plate_changed"][0]["captured_at"] == "2026-10-12T13:28:01.293Z"
    assert ex["read_transitions"][0]["reads"] == 1
    json.dumps(rep)


def test_report_after_run_skips() -> None:
    plan = _sample_plan(3)
    plan.planner.skip_update(plan.planner.updates[0], "plate taken meanwhile (uq_reads_camera_captured_plate)")
    plan.sightings_skipped.append(1003)
    rep = plan.report(dry_run=False)
    assert rep["written"] == ["plate_reads", "sightings", "qa_labels"]
    assert rep["counts"]["reads_skipped_at_run"] == 1 and rep["counts"]["sightings_skipped_at_run"] == 1
    assert rep["examples"]["reads_skipped_at_run"][0]["reason"].startswith("plate taken")
    assert set(plan.audit_after()) == set(rep["counts"])


# ---- apply(): guarded updates, savepoints, skips (fake session) -----------------------------------


class _Result:
    def __init__(self, rowcount: int = 1, scalar=None) -> None:
        self.rowcount = rowcount
        self._scalar = scalar

    def scalar(self):
        return self._scalar


class _Nested:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    async def __aenter__(self):
        self.session.log.append("savepoint")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.session.log.append("rollback_to_savepoint" if exc_type else "release_savepoint")
        return False


class _FakeSession:
    def __init__(self, outcomes=None, lock=True) -> None:
        self.outcomes = list(outcomes or [])
        self.lock = lock
        self.log: list = []
        self.updates: list[tuple[str, dict]] = []

    def begin_nested(self):
        return _Nested(self)

    async def execute(self, stmt, params=None):
        table = getattr(getattr(stmt, "table", None), "name", None)
        if table is None:
            self.log.append(("sql", str(stmt)))
            return _Result(scalar=self.lock)
        values = {c.key: v.value for c, v in stmt._values.items()}
        self.updates.append((table, values))
        outcome = self.outcomes.pop(0) if self.outcomes else 1
        if outcome == "integrity":
            raise IntegrityError("UPDATE", {}, Exception("duplicate key value violates unique constraint"))
        return _Result(rowcount=outcome)

    async def connection(self, execution_options=None):
        self.log.append(("connection", execution_options))

    async def rollback(self):
        self.log.append("rollback")

    async def commit(self):
        self.log.append("commit")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_apply_writes_reads_then_sightings_then_qa_with_skips() -> None:
    sightings = [SightingRow(10, "GI27XY3456", True), SightingRow(11, "GI28XY3456", True), SightingRow(12, "GI29XY3456", True)]
    reads = [read(1, "GI27XY3456", sighting=10), read(2, "GI28XY3456", cam=2, sighting=11), read(3, "GI29XY3456", cam=3, sighting=12)]
    table = {f"GI{d}XY3456": (f"GJ{d}XY3456", True) for d in (27, 28, 29)}
    plan = Plan(_plan(sightings, reads, table), qa=[QaRow(5, 3, "GJ29XY3456", False, 1), QaRow(6, 2, "GJ28XY3456", False, 1)])
    # read 1 updated, read 2 hits the unique index (concurrent insert), read 3 changed meanwhile (0 rows)
    db = _FakeSession(outcomes=[1, "integrity", 0])
    await job.apply(db, plan)
    tables = [t for t, _v in db.updates]
    assert tables == ["plate_reads"] * 3 + ["sightings"]  # only sighting 10 still follows an updated read
    assert db.updates[0][1] == {"plate_norm": "GJ27XY3456", "is_valid_format": True}
    assert db.updates[3][1]["plate_norm"] == "GJ27XY3456" and "updated_at" in db.updates[3][1]
    assert db.log.count("savepoint") == 3 and db.log.count("rollback_to_savepoint") == 1
    assert [c.id for c in plan.planner.updates] == [1]
    assert sorted(c.id for c, _why in plan.planner.skipped) == [2, 3]
    rep = plan.report(dry_run=False)
    assert rep["counts"]["reads_changed"] == 1 and rep["counts"]["reads_skipped_at_run"] == 2
    assert rep["counts"]["qa_labels_rescored"] == 0  # both labelled reads were skipped


async def test_apply_rescoring_qa_and_counting_stale_sightings() -> None:
    sightings = [SightingRow(10, "GI27XY3456", True)]
    plan = Plan(_plan(sightings, [read(1, "GI27XY3456", sighting=10)], {"GI27XY3456": ("GJ27XY3456", True)}), qa=[QaRow(5, 1, "GJ27XY3456", False, 1)])
    db = _FakeSession(outcomes=[1, 0, 1])  # read ok, sighting changed meanwhile, qa ok
    await job.apply(db, plan)
    assert [t for t, _v in db.updates] == ["plate_reads", "sightings", "qa_labels"]
    assert db.updates[2][1] == {"is_match": True, "char_errors": 0}
    assert plan.sightings_skipped == [10]
    c = plan.report(dry_run=False)["counts"]
    assert c["sightings_changed"] == 0 and c["sightings_skipped_at_run"] == 1 and c["qa_labels_rescored"] == 1


# ---- run_renormalise(): dry run writes nothing, --run audits ----------------------------------------


@pytest.fixture
def wired(monkeypatch):
    calls: dict = {"apply": 0, "audit": []}
    session = _FakeSession()

    async def fake_collect(db):
        return _sample_plan(3)

    async def fake_apply(db, plan):
        calls["apply"] += 1

    async def fake_audit(action, **kw):
        calls["audit"].append((action, kw))

    monkeypatch.setattr(job, "SessionLocal", lambda: session)
    monkeypatch.setattr(job, "collect", fake_collect)
    monkeypatch.setattr(job, "apply", fake_apply)
    monkeypatch.setattr(job, "write_audit", fake_audit)
    return session, calls


async def test_dry_run_is_read_only_and_writes_no_audit(wired) -> None:
    session, calls = wired
    rep = await job.run_renormalise(dry_run=True)
    assert rep["dry_run"] is True
    assert ("connection", {"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}) in session.log
    assert "rollback" in session.log and "commit" not in session.log
    assert calls == {"apply": 0, "audit": []}


async def test_run_takes_the_lock_applies_commits_and_audits(wired) -> None:
    session, calls = wired
    rep = await job.run_renormalise(dry_run=False)
    assert any(isinstance(e, tuple) and "pg_try_advisory_xact_lock" in e[1] for e in session.log)
    assert calls["apply"] == 1 and "commit" in session.log
    [(action, kw)] = calls["audit"]
    assert action == "plates.renormalise" and kw["after"] == rep["counts"]


async def test_run_refuses_when_another_run_holds_the_lock(wired) -> None:
    session, calls = wired
    session.lock = False
    with pytest.raises(RuntimeError):
        await job.run_renormalise(dry_run=False)
    assert calls == {"apply": 0, "audit": []}


@pytest.mark.parametrize("argv,dry", [([], True), (["--dry-run"], True), (["--run"], False)])
def test_cli_defaults_to_dry_run(monkeypatch, argv, dry) -> None:
    seen = []

    async def fake_main(dry_run):
        seen.append(dry_run)
        return 0

    monkeypatch.setattr(job, "_main", fake_main)
    monkeypatch.setattr(job, "setup_logging", lambda: None)
    monkeypatch.setattr("sys.argv", ["renormalise", *argv])
    with pytest.raises(SystemExit) as exc:
        job.main()
    assert exc.value.code == 0 and seen == [dry]


def test_cli_rejects_both_flags(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["renormalise", "--dry-run", "--run"])
    with pytest.raises(SystemExit) as exc:
        job.main()
    assert exc.value.code == 2
