"""Settings precedence and the API client's status handling (no network: requests is stubbed)."""
import json

import pytest

from anpr import client as client_mod
from anpr.client import ApiClient, DroppedError, FatalAuthError, RetryableError, iso_utc
from anpr.config import load_settings, parse_sources


def test_env_overrides_yaml_and_cli_overrides_env(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text("anpr_fps: 2\nanpr_detector: contour\nanpr_max_cameras: 5\n", encoding="utf-8")
    env = {"ANPR_FPS": "4", "CPU": "1", "INTERNAL_API_KEY": "sk_x"}
    s = load_settings(["--config", str(cfg), "--fps", "7", "--dry-run"], environ=env)
    assert s.live_fps == 7.0 and s.detector == "contour" and s.max_cameras == 5
    assert s.api_dry_run is True and s.cpu is True
    assert {"live_fps", "cpu", "internal_api_key", "api_dry_run"} <= s.explicit
    assert s.worker_id.startswith("live-")


def test_server_settings_respect_explicit_env(tmp_path):
    s = load_settings(["--config", str(tmp_path / "none.yml")], environ={"ANPR_VOTE_WINDOW_S": "5"})
    changed = s.apply_server_settings({"vote_window_s": 3, "det_conf": 0.5, "object_detect": False, "live_fps": 8})
    assert s.vote_window_s == 5.0                      # env wins
    assert s.det_conf == 0.5 and s.object_detect is False and s.live_fps == 8.0
    assert set(changed) == {"det_conf", "object_detect", "live_fps"}


def test_preindex_disables_objects_and_defaults(tmp_path):
    s = load_settings(["--config", str(tmp_path / "none.yml"), "--mode", "preindex"], environ={})
    assert s.object_detect is False and s.fps == 1.0 and s.effective_max_cameras == 3
    s2 = load_settings(["--config", str(tmp_path / "none.yml")], environ={"CPU": "0"})
    assert s2.effective_max_cameras == 12


def test_parse_sources():
    assert parse_sources("1=/media/synthetic/cam_1.mp4, 2=rtsp://h/stream/2") == {1: "/media/synthetic/cam_1.mp4", 2: "rtsp://h/stream/2"}
    assert parse_sources({3: "file:///x.mp4"}) == {3: "file:///x.mp4"}
    with pytest.raises(ValueError):
        parse_sources("nope")


def test_iso_utc_format():
    from datetime import datetime, timezone

    assert iso_utc(datetime(2026, 9, 4, 10, 15, 30, 123456, tzinfo=timezone.utc)) == "2026-09-04T10:15:30.123Z"


class _Resp:
    def __init__(self, status, text="", body=None):
        self.status_code = status
        self.text = text
        self.content = (json.dumps(body) if body is not None else text).encode()
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def _client(monkeypatch, responder):
    c = ApiClient("http://api:8000", "sk_test")
    monkeypatch.setattr(c._session, "request", lambda method, url, **kw: responder(method, url, kw))
    return c


def test_status_mapping(monkeypatch):
    c = _client(monkeypatch, lambda m, u, kw: _Resp(401, "bad key"))
    with pytest.raises(FatalAuthError):
        c.post_heartbeat({})
    c = _client(monkeypatch, lambda m, u, kw: _Resp(503, "db down"))
    with pytest.raises(RetryableError):
        c.post_heartbeat({})
    c = _client(monkeypatch, lambda m, u, kw: _Resp(404, "unknown camera"))
    with pytest.raises(DroppedError):
        c.post_detections({"camera_id": 9}, {})
    c = _client(monkeypatch, lambda m, u, kw: _Resp(200, body={"accepted_reads": 1}))
    assert c.post_detections({"camera_id": 1}, {"crop_0": b"x"}) == {"accepted_reads": 1}


def test_network_error_is_retryable(monkeypatch):
    def boom(m, u, kw):
        raise client_mod.requests.ConnectionError("refused")

    c = _client(monkeypatch, boom)
    with pytest.raises(RetryableError):
        c.get_config("live")


def test_dry_run_prints_payload(capsys):
    c = ApiClient("http://api:8000", "sk_test", dry_run=True)
    c.post_detections({"camera_id": 1, "reads": []}, {"crop_0": b"abc"})
    line = json.loads(capsys.readouterr().out.strip())
    assert line["dry_run"] is True and line["endpoint"] == "/internal/detections"
    assert line["payload"]["camera_id"] == 1 and line["files"] == {"crop_0": 3}
