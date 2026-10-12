"""Live overlay relay (CONTRACT amendment 2026-10-12): payload validation and the watched-camera list. DB-free."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.rbac import Scope
from app.schemas.internal import OverlayBatch
from app.services.ws_manager import Connection, WsManager


class _FakeWs:
    pass


def _conn(channel: str, camera_id: int | None = None) -> Connection:
    return Connection(ws=_FakeWs(), channel=channel, username="u", role="admin", scope=Scope(), camera_id=camera_id)


FRAME = {
    "camera_id": 95, "captured_at": "2026-10-12T10:00:00.000Z", "frame_w": 2560, "frame_h": 1440,
    "plates": [{"box": [0.5, 0.5, 0.1, 0.05], "conf": 0.81, "label": "GJ01WC0824", "track": 3}],
    "objects": [{"box": [0.05, 0.18, 0.25, 0.5], "conf": 0.9, "cls": "car"}],
    "objects_at": "2026-10-12T09:59:59.500Z",
}


def test_overlay_batch_accepts_worker_frame() -> None:
    b = OverlayBatch.model_validate({"frames": [FRAME]})
    f = b.frames[0]
    assert f.plates[0].label == "GJ01WC0824" and f.objects[0].cls == "car" and f.objects[0].box == [0.05, 0.18, 0.25, 0.5]
    assert OverlayBatch.model_validate({}).frames == []


@pytest.mark.parametrize("bad", [
    {"plates": [{"box": [0.5, 0.5, 0.1], "conf": 0.5}]},          # three values
    {"plates": [{"box": [0.5, 0.5, 1.5, 0.1], "conf": 0.5}]},     # pixels, not fractions
    {"objects": [{"box": [0, 0, 0.1, 0.1], "conf": 0.5, "cls": "dog"}]},
    {"objects": [{"box": [0, 0, 0.1, 0.1], "conf": 1.5, "cls": "car"}]},
    {"frame_w": 0},
    {"plates": [{"box": [0, 0, 0.1, 0.1], "conf": 0.5}] * 17},     # more than 16 plates
])
def test_overlay_batch_rejects_bad_frames(bad: dict) -> None:
    with pytest.raises(ValidationError):
        OverlayBatch.model_validate({"frames": [{**FRAME, **bad}]})


def test_watched_cameras_lists_reads_subscribers_only() -> None:
    m = WsManager()
    assert m.watched_cameras() == []
    for c in (_conn("reads:95", 95), _conn("reads:95", 95), _conn("reads:91", 91), _conn("alerts"), _conn("health")):
        m._conns.add(c)
    assert m.watched_cameras() == [91, 95]
