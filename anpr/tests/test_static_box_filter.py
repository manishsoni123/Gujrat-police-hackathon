"""StaticBoxFilter: overlays that never move are muted, moving plates pass, window 0 disables."""
from anpr.config import Settings, load_settings
from anpr.detector import Box, StaticBoxFilter


def osd(conf: float = 0.95) -> Box:
    return Box(293, 0, 220, 42, conf, "onnx")          # a burnt-in clock at the top of the frame


def test_static_overlay_is_muted_after_the_warm_up():
    f = StaticBoxFilter(window=20)
    passed = []
    for i in range(30):
        moving = Box(100 + 40 * i, 500, 90, 26, 0.7, "onnx")   # a plate crossing the frame
        kept = f.filter([osd(), moving])
        passed.append([b.x for b in kept])
    # the first 10 frames (half the window) are pass-through; from then on only the moving box survives
    assert all(293 in xs for xs in passed[:10])
    assert all(293 not in xs for xs in passed[10:])
    assert all(any(x != 293 for x in xs) for xs in passed), "the moving plate is never dropped"
    assert f.suppressed == 20
    assert len(f.history) == 20


def test_intermittent_box_is_not_static():
    f = StaticBoxFilter(window=10, min_hits=0.8)
    for i in range(20):
        boxes = [osd()] if i % 2 == 0 else []                  # present in only half the frames
        kept = f.filter(boxes)
        assert kept == boxes


def test_window_zero_disables():
    f = StaticBoxFilter(window=0)
    for _ in range(40):
        assert f.filter([osd()]) == [osd()]
    assert f.suppressed == 0 and f.history == []


def test_settings_read_window_from_env(monkeypatch):
    monkeypatch.setenv("ANPR_STATIC_BOX_WINDOW", "0")
    assert load_settings([]).static_box_window == 0
    assert Settings().static_box_window == 20
