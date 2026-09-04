"""Watchlist matching rules (CONTRACT §5.12): exact, possible, suppression, priority (W6)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.matcher import REASON_FLOOR, MatchEngine, WatchEntry, effective_priority

NOW = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)


def _engine() -> MatchEngine:
    e = MatchEngine()
    e.load(
        [
            WatchEntry(1, "GJ01AB1234", "stolen", "critical", "Swift"),
            WatchEntry(2, "GJ18CD5678", "wanted", "critical", "Creta"),
            WatchEntry(3, "GJ05RS9012", "blacklisted", "high", "Truck"),
            WatchEntry(4, "MH02BZ7788", "missing", "medium", "City"),
            WatchEntry(5, "GJ06KL4455", "suspect", "low", "Bike"),
            WatchEntry(6, "GJ06KL4456", "stolen", "low", "Near-duplicate of 5"),
        ]
    )
    return e


def test_exact_match() -> None:
    e = _engine()
    m = e.match("GJ01AB1234", 0.5, True, 0.8)
    assert m is not None and m.confidence_level == "exact"
    assert m.entry.id == 1 and m.priority == "critical"


def test_invalid_format_never_matches() -> None:
    e = _engine()
    assert e.match("GJ01AB1234", 0.99, False, 0.8) is None
    assert e.match("", 0.99, True, 0.8) is None


def test_possible_match_requires_confidence_and_distance_one() -> None:
    e = _engine()
    # one character off, high confidence → possible, priority stepped down
    m = e.match("GJ01AB1235", 0.85, True, 0.8)
    assert m is not None and m.confidence_level == "possible"
    assert m.entry.id == 1 and m.priority == "high"
    # same read below the fuzzy threshold → nothing
    assert e.match("GJ01AB1235", 0.79, True, 0.8) is None
    # two characters off → nothing even with high confidence
    assert e.match("GJ01AB1299", 0.99, True, 0.8) is None
    # threshold is inclusive
    assert e.match("GJ01AB1235", 0.8, True, 0.8) is not None


def test_possible_match_prefers_highest_priority_entry() -> None:
    e = _engine()
    # GJ06KL4457 is distance 1 from both id 5 (suspect/low) and id 6 (stolen → floor critical)
    m = e.match("GJ06KL4457", 0.9, True, 0.8)
    assert m is not None and m.entry.id == 6
    assert m.priority == "high"  # critical stepped down for possible


@pytest.mark.parametrize(
    "wl_priority,reason,level,expected",
    [
        ("low", "stolen", "exact", "critical"),
        ("low", "wanted", "exact", "critical"),
        ("medium", "blacklisted", "exact", "high"),
        ("low", "arrested", "exact", "high"),
        ("low", "missing", "exact", "medium"),
        ("low", "unidentified_body", "exact", "medium"),
        ("low", "suspect", "exact", "low"),
        ("high", "suspect", "exact", "high"),  # watchlist priority above the floor wins
        ("critical", "other", "exact", "critical"),
        ("critical", "stolen", "possible", "high"),
        ("high", "blacklisted", "possible", "medium"),
        ("medium", "missing", "possible", "low"),
        ("low", "suspect", "possible", "low"),  # cannot go below low
    ],
)
def test_effective_priority_w6(wl_priority: str, reason: str, level: str, expected: str) -> None:
    assert effective_priority(wl_priority, reason, level) == expected


def test_reason_floor_table_matches_contract() -> None:
    assert REASON_FLOOR == {
        "stolen": "critical", "wanted": "critical", "blacklisted": "high", "arrested": "high",
        "missing": "medium", "unidentified_body": "medium", "suspect": "low", "other": "low",
    }


def test_suppression_window() -> None:
    s = MatchEngine.should_suppress
    # within the suppression window → attach regardless of status
    assert s(NOW - timedelta(seconds=30), "closed", NOW, 60)
    assert s(NOW - timedelta(seconds=60), "new", NOW, 60)
    # beyond the window but still open and younger than the re-alert window (default 60 min) → attach
    assert s(NOW - timedelta(minutes=5), "new", NOW, 60)
    assert s(NOW - timedelta(minutes=9), "acknowledged", NOW, 60)
    assert s(NOW - timedelta(minutes=45), "new", NOW, 60)
    assert s(NOW - timedelta(minutes=60), "acknowledged", NOW, 60)
    # beyond the window and closed → new alert
    assert not s(NOW - timedelta(minutes=5), "closed", NOW, 60)
    # open but older than the re-alert window → new alert
    assert not s(NOW - timedelta(minutes=61), "new", NOW, 60)
    assert not s(NOW - timedelta(minutes=11), "new", NOW, 60, re_alert_minutes=10)
    assert s(NOW - timedelta(minutes=9), "new", NOW, 60, re_alert_minutes=10)
    # re-alert disabled (0) → an open alert absorbs reads indefinitely
    assert s(NOW - timedelta(days=3), "acknowledged", NOW, 60, re_alert_minutes=0)
    assert not s(NOW - timedelta(days=3), "closed", NOW, 60, re_alert_minutes=0)
    # suppression disabled (0 s) still keeps the open-alert rule
    assert not s(NOW - timedelta(seconds=1), "closed", NOW, 0)
    assert s(NOW - timedelta(seconds=1), "new", NOW, 0)


def test_reload_replaces_map_and_ignores_blank_plates() -> None:
    e = MatchEngine()
    e.load([WatchEntry(1, "GJ01AB1234", "stolen", "critical"), WatchEntry(2, "", "wanted", "critical")])
    assert len(e) == 1
    e.load([])
    assert len(e) == 0
    assert e.match("GJ01AB1234", 0.9, True, 0.8) is None
