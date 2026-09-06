"""Voting: char-wise majority per camera + approximate plate, one emit per window."""
from datetime import datetime, timedelta, timezone

from anpr.normalise import normalise
from anpr.voting import Candidate, Voter, majority_string

T0 = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)


def cand(camera, raw, conf, dt_s, frame_index=0):
    n = normalise(raw)
    return Candidate(
        camera_id=camera,
        captured_at=T0 + timedelta(seconds=dt_s),
        stream_pts=dt_s,
        frame_index=frame_index,
        plate_raw=raw,
        plate_norm=n.plate_norm,
        is_valid_format=n.is_valid_format,
        confidence=conf,
        bbox=(10, 10, 200, 60),
        crop_jpeg=b"jpeg",
    )


def test_majority_string_basic():
    assert majority_string(["GJ01AB1234", "GJ01AB1234", "GJ01AB1284"]) == "GJ01AB1234"


def test_majority_string_prefers_majority_length():
    assert majority_string(["GJ01AB1234", "GJ01AB123", "GJ01AB1234"]) == "GJ01AB1234"


def test_majority_string_weight_breaks_ties():
    assert majority_string(["GJ01AB1234", "GJ01AB1284"], [0.5, 0.9]) == "GJ01AB1284"


def test_one_emit_per_window_with_majority():
    v = Voter(window_s=3.0)
    v.add(cand(1, "GJ01AB1234", 0.9, 0.0))
    v.add(cand(1, "GJ01AB1284", 0.6, 0.4))   # one confused char
    v.add(cand(1, "GJ01AB1234", 0.8, 0.8))
    v.add(cand(1, "GJ01AB1234", 0.85, 1.2))
    assert v.expire(T0 + timedelta(seconds=2.0)) == []          # window still open
    reads = v.expire(T0 + timedelta(seconds=3.0))
    assert len(reads) == 1
    r = reads[0]
    assert r.plate_norm == "GJ01AB1234"
    assert r.is_valid_format is True
    assert r.votes == 4
    assert abs(r.confidence - (0.9 + 0.6 + 0.8 + 0.85) / 4) < 1e-9
    assert r.captured_at == T0                                  # bbox/crop/time of the best member
    assert v.open_buckets() == 0


def test_far_apart_plates_get_separate_buckets():
    v = Voter(window_s=3.0)
    v.add(cand(1, "GJ01AB1234", 0.9, 0.0))
    v.add(cand(1, "MH02BZ7788", 0.9, 0.1))
    assert v.open_buckets(1) == 2
    reads = v.flush(1)
    assert sorted(r.plate_norm for r in reads) == ["GJ01AB1234", "MH02BZ7788"]


def test_buckets_are_per_camera():
    v = Voter(window_s=3.0)
    v.add(cand(1, "GJ01AB1234", 0.9, 0.0))
    v.add(cand(2, "GJ01AB1234", 0.9, 0.0))
    assert v.open_buckets() == 2
    reads = v.expire(T0 + timedelta(seconds=5))
    assert {r.camera_id for r in reads} == {1, 2}


def test_flush_on_loop_reset_emits_immediately():
    v = Voter(window_s=3.0)
    v.add(cand(3, "22BH4321AA", 0.7, 0.0))
    reads = v.flush(3)
    assert len(reads) == 1 and reads[0].plate_norm == "22BH4321AA"
    assert v.open_buckets() == 0


def test_only_best_member_keeps_its_frame():
    import numpy as np

    v = Voter(window_s=3.0)
    a = cand(1, "GJ01AB1234", 0.5, 0.0)
    a.frame = np.zeros((2, 2, 3), dtype=np.uint8)
    b = cand(1, "GJ01AB1234", 0.9, 0.5)
    b.frame = np.ones((2, 2, 3), dtype=np.uint8)
    v.add(a)
    v.add(b)
    assert a.frame is None and b.frame is not None
    read = v.flush(1)[0]
    assert read.frame is b.frame


# ---- plate_raw must agree with the voted plate_norm (CONTRACT section 7.2 re-normalises plate_raw) ----

def test_plate_raw_follows_the_vote_not_the_best_member():
    """The highest-confidence member is the mis-read; the vote corrects it and plate_raw must follow."""
    v = Voter(window_s=3.0)
    v.add(cand(1, "GJ 01 AB 1234", 0.70, 0.0))
    v.add(cand(1, "GJ 01 AB 1284", 0.95, 0.3))   # best confidence, wrong digit (the live-DB case: read 4193)
    v.add(cand(1, "GJ 01 AB 1234", 0.80, 0.6))
    v.add(cand(1, "GJ-01-AB-1234", 0.75, 0.9))
    r = v.expire(T0 + timedelta(seconds=3.0))[0]
    assert r.plate_norm == "GJ01AB1234"
    assert r.plate_raw == "GJ 01 AB 1234"                    # the 0.80 member, not the 0.95 mis-read
    assert normalise(r.plate_raw).plate_norm == r.plate_norm
    assert r.captured_at == T0 + timedelta(seconds=0.3)      # bbox/crop/frame/time still from the best member
    assert r.votes == 4


def test_plate_raw_invariant_holds_for_every_emitted_read():
    v = Voter(window_s=3.0)
    v.add(cand(2, "MH 02\nBZ 7788", 0.6, 0.0))
    v.add(cand(2, "MH 02 BZ 7768", 0.9, 0.2))
    v.add(cand(2, "MH02BZ7788", 0.7, 0.4))
    v.add(cand(3, "22 BH 4321 AA", 0.5, 0.0))
    v.add(cand(3, "22 BH 4321 AA", 0.6, 0.1))
    v.add(cand(3, "2Z BH 4321 AA", 0.99, 0.2))
    v.add(cand(4, "GJ01AB12345", 0.8, 0.0))                  # invalid format: plate_norm is the cleaned string
    for r in v.flush():
        assert normalise(r.plate_raw).plate_norm == r.plate_norm, (r.plate_raw, r.plate_norm)
    v.add(cand(5, "GJ 05 RS 9012", 0.9, 0.0))
    v.add(cand(5, "GJ 05 RS 9O12", 0.95, 0.1))               # O -> 0 substitution: same plate_norm, raw differs
    r = v.flush(5)[0]
    assert r.plate_norm == "GJ05RS9012" and r.plate_raw == "GJ 05 RS 9O12"   # agrees with the vote, so the best raw wins
    assert normalise(r.plate_raw).plate_norm == r.plate_norm


def test_raw_falls_back_to_display_form_when_no_member_agrees(monkeypatch):
    """With max_distance 1 the majority string is always a member string; simulate a wider window."""
    import anpr.voting as voting

    monkeypatch.setattr(voting, "majority_string", lambda strings, weights=None: "GJ01AB1299")
    v = Voter(window_s=3.0)
    v.add(cand(1, "GJ 01 AB 1234", 0.9, 0.0))
    v.add(cand(1, "GJ 01 AB 1284", 0.8, 0.2))
    r = v.flush(1)[0]
    assert r.plate_norm == "GJ01AB1299"
    assert r.plate_raw == "GJ 01 AB 1299"
    assert normalise(r.plate_raw).plate_norm == r.plate_norm


def test_raw_for_vote_direct():
    from anpr.voting import raw_for_vote

    members = [cand(1, "gj-01-ab-1234", 0.4, 0.0), cand(1, "GJ 01 AB 1234", 0.6, 0.1), cand(1, "GJ 01 AB 1284", 0.9, 0.2)]
    assert raw_for_vote("GJ01AB1234", members) == "GJ 01 AB 1234"
    assert raw_for_vote("GJ01AB1284", members) == "GJ 01 AB 1284"
    assert raw_for_vote("22BH4321AA", members) == "22 BH 4321 AA"
    assert raw_for_vote("GJ01AB12345", []) == "GJ01AB12345"
