"""Worker settings.

Precedence (highest wins): CLI flags > environment variables > server settings from
``GET /internal/anpr-config`` > ``config.yml`` > built-in defaults (CONTRACT.md section 1.3).

The YAML keys are the lowercase environment variable names, e.g. ``anpr_fps: 5``.
``explicit`` records which fields were pinned by the environment or the CLI so that the
server's runtime settings never override an operator's deliberate local choice.
"""
from __future__ import annotations

import argparse
import logging
import os
import secrets
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("anpr.config")

DEFAULT_INTERNAL_KEY = "sk_internal0000000000000000000000000000000000"


@dataclass
class Settings:
    """All tunables of the worker. Field names are the YAML keys; ENV_MAP gives the env names."""

    mode: str = "live"                      # live | preindex
    cpu: bool = True                        # CPU=1: software decode + CPUExecutionProvider
    api_url: str = "http://api:8000"
    internal_api_key: str = DEFAULT_INTERNAL_KEY
    rtsp_base: str = "rtsp://mediamtx:8554"
    cameras: list[int] = field(default_factory=list)   # ANPR_CAMERAS restriction (empty = all)
    max_cameras: int = 0                    # 0 = default for the platform (3 cpu / 12 gpu)
    live_fps: float = 5.0                   # ANPR_FPS
    preindex_fps: float = 1.0
    preindex_skip_live: bool = True
    frame_width: int = 960
    detector: str = "auto"                  # auto | onnx | contour
    det_model: str = "/app/weights/yolo-v9-t-384-license-plates-end2end.onnx"
    det_conf: float = 0.4
    min_plate_w: int = 60
    vote_window_s: float = 3.0
    min_read_conf: float = 0.30
    sighting_close_s: float = 15.0
    snapshot_interval_s: float = 1.0
    snapshot_width: int = 480
    post_batch_s: float = 1.0
    config_reload_s: float = 60.0
    heartbeat_s: float = 15.0
    object_detect: bool = True
    object_model: str = "/app/weights/yolox_s.onnx"
    object_conf: float = 0.35
    object_every_n: int = 5
    ocr_lang: str = "en"
    reconnect_min_s: float = 2.0
    reconnect_max_s: float = 30.0
    log_level: str = "INFO"
    # Test / tooling knobs (not in the contract table; documented in anpr/README.md)
    api_dry_run: bool = False               # API_DRY_RUN=1 prints payloads instead of POSTing
    sources: dict[int, str] = field(default_factory=dict)  # static sources: "1=/media/synthetic/cam_1.mp4"
    file_realtime: bool = True              # ANPR_FILE_RE: add -re for file sources (real-time pacing)
    file_loop: int = 0                      # ANPR_FILE_LOOP: 0 = play once, -1 = loop forever
    max_boxes_per_frame: int = 4            # OCR budget per frame (largest boxes first)
    ocr_threads: int = 2                    # CPU threads for PaddleOCR
    worker_id: str = ""                     # generated when empty: <mode>-<6 hex>
    explicit: set[str] = field(default_factory=set, repr=False)

    # ---- derived -------------------------------------------------------
    @property
    def fps(self) -> float:
        return self.live_fps if self.mode == "live" else self.preindex_fps

    @property
    def effective_max_cameras(self) -> int:
        if self.max_cameras > 0:
            return self.max_cameras
        return 3 if self.cpu else 12

    def apply_server_settings(self, server: dict[str, Any]) -> list[str]:
        """Apply the ``settings`` object of /internal/anpr-config; returns the changed field names."""
        mapping = {
            "live_fps": "live_fps",
            "preindex_fps": "preindex_fps",
            "det_conf": "det_conf",
            "min_plate_w": "min_plate_w",
            "vote_window_s": "vote_window_s",
            "sighting_close_s": "sighting_close_s",
            "object_detect": "object_detect",
            "object_every_n": "object_every_n",
            "snapshot_interval_s": "snapshot_interval_s",
        }
        changed: list[str] = []
        for server_key, attr in mapping.items():
            if server_key not in server or attr in self.explicit:
                continue
            value = _coerce(attr, server[server_key])
            if value is None or getattr(self, attr) == value:
                continue
            setattr(self, attr, value)
            changed.append(attr)
        return changed


# env var name -> Settings field
ENV_MAP: dict[str, str] = {
    "ANPR_MODE": "mode",
    "CPU": "cpu",
    "API_URL": "api_url",
    "INTERNAL_API_KEY": "internal_api_key",
    "RTSP_BASE": "rtsp_base",
    "ANPR_CAMERAS": "cameras",
    "ANPR_MAX_CAMERAS": "max_cameras",
    "ANPR_FPS": "live_fps",
    "PREINDEX_FPS": "preindex_fps",
    "PREINDEX_SKIP_LIVE": "preindex_skip_live",
    "FRAME_WIDTH": "frame_width",
    "ANPR_DETECTOR": "detector",
    "ANPR_DET_MODEL": "det_model",
    "ANPR_DET_CONF": "det_conf",
    "ANPR_MIN_PLATE_W": "min_plate_w",
    "ANPR_VOTE_WINDOW_S": "vote_window_s",
    "ANPR_MIN_READ_CONF": "min_read_conf",
    "SIGHTING_CLOSE_S": "sighting_close_s",
    "SNAPSHOT_INTERVAL_S": "snapshot_interval_s",
    "SNAPSHOT_WIDTH": "snapshot_width",
    "POST_BATCH_S": "post_batch_s",
    "CONFIG_RELOAD_S": "config_reload_s",
    "HEARTBEAT_S": "heartbeat_s",
    "OBJECT_DETECT": "object_detect",
    "OBJECT_MODEL": "object_model",
    "OBJECT_CONF": "object_conf",
    "OBJECT_EVERY_N": "object_every_n",
    "OCR_LANG": "ocr_lang",
    "RECONNECT_MIN_S": "reconnect_min_s",
    "RECONNECT_MAX_S": "reconnect_max_s",
    "LOG_LEVEL": "log_level",
    "API_DRY_RUN": "api_dry_run",
    "ANPR_SOURCES": "sources",
    "ANPR_FILE_RE": "file_realtime",
    "ANPR_FILE_LOOP": "file_loop",
    "ANPR_MAX_BOXES": "max_boxes_per_frame",
    "OCR_THREADS": "ocr_threads",
    "ANPR_WORKER_ID": "worker_id",
    "WORKER_ID": "worker_id",               # name used by deploy/docker-compose.yml
}

_TYPES: dict[str, Any] = {f.name: f.type for f in fields(Settings)}


def parse_sources(value: Any) -> dict[int, str]:
    """``"1=/media/synthetic/cam_1.mp4,2=rtsp://host/stream/2"`` or a YAML mapping -> {id: url}."""
    if not value:
        return {}
    if isinstance(value, dict):
        return {int(k): str(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        items = [p for p in str(value).split(",") if p.strip()]
    out: dict[int, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"source {item!r} must be <camera_id>=<url or path>")
        cam, url = item.split("=", 1)
        out[int(cam.strip())] = url.strip()
    return out


def _coerce(attr: str, value: Any) -> Any:
    """Convert an env/YAML/server value into the field's Python type."""
    if value is None:
        return None
    kind = _TYPES[attr]
    if attr == "sources":
        return parse_sources(value)
    if attr == "cameras":
        if isinstance(value, (list, tuple)):
            return [int(v) for v in value]
        return [int(v) for v in str(value).split(",") if v.strip()]
    if kind in ("bool", bool):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if kind in ("int", int):
        return int(float(value))
    if kind in ("float", float):
        return float(value)
    return str(value)


def load_yaml(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{p}: top level must be a mapping")
    return {str(k).lower(): v for k, v in data.items()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anpr.pipeline",
        description="Sentinel Gujarat ANPR / analytics worker (CONTRACT.md section 7)",
    )
    parser.add_argument("--config", default=os.environ.get("ANPR_CONFIG", "anpr/config.yml"), help="YAML config path")
    parser.add_argument("--mode", choices=["live", "preindex"], help="worker mode")
    parser.add_argument("--source", action="append", default=[], metavar="ID=URL",
                        help="static camera source (file path, file:// or rtsp://); repeatable; skips the config endpoint")
    parser.add_argument("--cameras", help="comma list of camera ids to restrict to")
    parser.add_argument("--max-cameras", type=int, help="cap on simultaneous decoders")
    parser.add_argument("--fps", type=float, help="decode fps for the active mode")
    parser.add_argument("--detector", choices=["auto", "onnx", "contour"], help="plate detector backend")
    parser.add_argument("--dry-run", action="store_true", help="print API payloads instead of POSTing")
    parser.add_argument("--no-objects", action="store_true", help="disable YOLOX object counting")
    parser.add_argument("--file-loop", type=int, help="file sources: 0 = once, -1 = forever, n = n extra loops")
    parser.add_argument("--no-realtime", action="store_true", help="file sources: decode as fast as possible (no -re)")
    parser.add_argument("--log-level", help="DEBUG, INFO, WARNING, ERROR")
    return parser


def load_settings(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> Settings:
    """Build Settings from defaults < YAML < env < CLI."""
    env = os.environ if environ is None else environ
    args = build_parser().parse_args(argv)
    settings = Settings()

    for key, value in load_yaml(args.config).items():
        # YAML keys are the lowercase env names (anpr_fps, anpr_detector, ...); bare field names
        # (live_fps, detector, ...) are accepted as well.
        attr = ENV_MAP.get(key.upper()) or (key if key in _TYPES else None)
        if attr is None or attr == "explicit":
            log.warning("config.yml: unknown key %r ignored", key)
            continue
        setattr(settings, attr, _coerce(attr, value))

    for env_name, attr in ENV_MAP.items():
        raw = env.get(env_name)
        if raw is None or raw == "":
            continue
        setattr(settings, attr, _coerce(attr, raw))
        settings.explicit.add(attr)

    cli_overrides: dict[str, Any] = {}
    if args.mode:
        cli_overrides["mode"] = args.mode
    if args.source:
        cli_overrides["sources"] = parse_sources(args.source)
    if args.cameras:
        cli_overrides["cameras"] = args.cameras
    if args.max_cameras is not None:
        cli_overrides["max_cameras"] = args.max_cameras
    if args.detector:
        cli_overrides["detector"] = args.detector
    if args.dry_run:
        cli_overrides["api_dry_run"] = True
    if args.no_objects:
        cli_overrides["object_detect"] = False
    if args.file_loop is not None:
        cli_overrides["file_loop"] = args.file_loop
    if args.no_realtime:
        cli_overrides["file_realtime"] = False
    if args.log_level:
        cli_overrides["log_level"] = args.log_level
    for attr, value in cli_overrides.items():
        setattr(settings, attr, _coerce(attr, value))
        settings.explicit.add(attr)
    if args.fps is not None:
        attr = "live_fps" if settings.mode == "live" else "preindex_fps"
        setattr(settings, attr, float(args.fps))
        settings.explicit.add(attr)

    if settings.mode not in ("live", "preindex"):
        raise ValueError(f"ANPR_MODE must be live or preindex, got {settings.mode!r}")
    if settings.detector not in ("auto", "onnx", "contour"):
        raise ValueError(f"ANPR_DETECTOR must be auto, onnx or contour, got {settings.detector!r}")
    if settings.mode == "preindex":
        settings.object_detect = False  # never runs in preindex (contract section 7.7)
    if not settings.worker_id:
        settings.worker_id = f"{settings.mode}-{secrets.token_hex(3)}"
    return settings


def redacted(settings: Settings) -> dict[str, Any]:
    """Settings as a dict with the API key masked, for the start-up log line."""
    out = {f.name: getattr(settings, f.name) for f in fields(Settings) if f.name != "explicit"}
    key = out.get("internal_api_key") or ""
    out["internal_api_key"] = (key[:7] + "..." + key[-4:]) if len(key) > 12 else "********"
    out["explicit"] = sorted(settings.explicit)
    return out
