"""ffmpeg decoder subprocess per camera (CONTRACT.md section 7.7, plan 5.5 step 1).

Command shape (live mode, CPU=1)::

    ffmpeg -nostdin -hide_banner -loglevel info -nostats -rtsp_transport tcp -timeout 10000000
           -i rtsp://mediamtx:8554/cam_12 -an -sn -dn
           -vf select=not(mod(n\\,2)),scale=960:-2,showinfo -fps_mode passthrough
           -f rawvideo -pix_fmt bgr24 -

* ``-rtsp_transport tcp`` always (sandbox rule); ``-hwaccel cuda`` is inserted before ``-i`` when
  ``CPU=0`` and removed automatically if the first attempt fails on the hardware decoder.
* Frame-rate reduction uses ``select=not(mod(n,K))`` with ``K = round(source_fps / target_fps)``
  and ``-fps_mode passthrough`` instead of the ``fps=`` filter: ``fps`` re-times on PTS and
  (a) stalls for the whole loop length when a feed's PTS jumps backwards, (b) duplicates
  frames in pre-index mode where ``-skip_frame nokey`` yields fewer than 1 fps. Timing still
  follows PTS: every frame carries the source ``pts_time`` parsed from ``showinfo`` on stderr
  (``stream_pts``); if the line is missing for a frame the value is estimated as
  ``last_pts + K / source_fps`` and flagged ``pts_estimated``.
* ``captured_at`` is the UTC wall clock at frame receipt (what the jury reads).
* Pre-index mode adds ``-skip_frame nokey`` before ``-i`` and takes every key frame (K = 1).
* File sources (``/path``, ``file:///path``) are supported for tests and soak runs: ``-re`` paces
  them in real time (``ANPR_FILE_RE=0`` disables), ``-stream_loop`` repeats them
  (``ANPR_FILE_LOOP``: 0 once, -1 forever). A clean end-of-file stops the decoder (state
  ``stopped``) for FILE sources only; for RTSP sources every exit - clean EOF included, which is
  what a relay restart looks like - restarts it with exponential backoff 2 -> 30 s.
* A stall watchdog kills ffmpeg when no frame arrives for ``stall_timeout_s``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import numpy as np

log = logging.getLogger("anpr.decode")

_SHOWINFO_RE = re.compile(r"n:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:\s*(-?[0-9.]+)")
_OUTPUT_SIZE_RE = re.compile(r"Stream #\d+:\d+.*?Video: rawvideo.*?\b(\d{2,5})x(\d{2,5})\b")
_HWACCEL_HINTS = ("cuda", "cuvid", "hwaccel", "nvdec", "No device", "device")

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


@dataclass
class Frame:
    camera_id: int
    image: np.ndarray                # BGR, height x width x 3, width = FRAME_WIDTH
    captured_at: datetime            # UTC wall clock at receipt
    stream_pts: float                # source pts_time (seconds)
    frame_index: int                 # per decoder session, 0-based
    pts_estimated: bool = False


@dataclass
class SourceInfo:
    width: int
    height: int
    fps: float
    codec: str

    @property
    def codec_label(self) -> str:
        return {"h264": "H264", "hevc": "H265", "h265": "H265", "mjpeg": "MJPEG"}.get(self.codec.lower(), self.codec.upper() or "UNKNOWN")


def is_rtsp(source: str) -> bool:
    return source.lower().startswith(("rtsp://", "rtsps://"))


def normalise_source(source: str) -> str:
    """``file:///media/x.mp4`` -> ``/media/x.mp4``; other URLs unchanged."""
    if source.lower().startswith("file://"):
        return source[7:]
    return source


def _parse_rate(value: str | None) -> float:
    if not value:
        return 0.0
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def probe(source: str, timeout_s: float = 15.0) -> SourceInfo:
    """Run ffprobe on the source; raises RuntimeError when it fails or has no video stream."""
    src = normalise_source(source)
    cmd = [FFPROBE, "-v", "error"]
    if is_rtsp(src):
        cmd += ["-rtsp_transport", "tcp", "-timeout", str(int(timeout_s * 1_000_000))]
    cmd += [
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name,r_frame_rate,avg_frame_rate",
        "-of", "json", src,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 5, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffprobe timed out after {timeout_s:.0f} s") from exc
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed ({out.returncode}): {out.stderr.strip()[-300:]}")
    try:
        streams = json.loads(out.stdout or "{}").get("streams") or []
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON: {out.stdout[:200]}") from exc
    if not streams:
        raise RuntimeError("ffprobe found no video stream")
    s = streams[0]
    fps = _parse_rate(s.get("avg_frame_rate")) or _parse_rate(s.get("r_frame_rate"))
    if not fps or fps > 240:
        fps = _parse_rate(s.get("r_frame_rate")) or 25.0
    width, height = int(s.get("width") or 0), int(s.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError("ffprobe reported no frame size")
    return SourceInfo(width=width, height=height, fps=float(fps), codec=str(s.get("codec_name") or "unknown"))


def scaled_height(src_w: int, src_h: int, out_w: int) -> int:
    """Height ffmpeg picks for ``scale=<out_w>:-2`` (nearest even value)."""
    return int(round(out_w * src_h / (src_w * 2.0))) * 2


def build_command(
    source: str,
    *,
    mode: str,
    source_fps: float,
    target_fps: float,
    out_width: int,
    cpu: bool,
    file_realtime: bool,
    file_loop: int,
    rtsp_timeout_s: float = 10.0,
) -> tuple[list[str], int]:
    """Return the ffmpeg argv and the decimation factor K."""
    src = normalise_source(source)
    rtsp = is_rtsp(src)
    if mode == "preindex":
        k = 1
    else:
        k = max(1, int(round(source_fps / max(target_fps, 0.1))))
    cmd = [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "info", "-nostats"]
    if rtsp:
        cmd += ["-rtsp_transport", "tcp", "-timeout", str(int(rtsp_timeout_s * 1_000_000))]
    else:
        if file_realtime:
            cmd += ["-re"]
        if file_loop != 0:
            cmd += ["-stream_loop", str(file_loop)]
    if mode == "preindex":
        cmd += ["-skip_frame", "nokey"]
    if not cpu:
        cmd += ["-hwaccel", "cuda"]
    cmd += ["-i", src, "-an", "-sn", "-dn"]
    filters = []
    if k > 1:
        filters.append(f"select=not(mod(n\\,{k}))")
    filters += [f"scale={out_width}:-2", "showinfo"]
    cmd += ["-vf", ",".join(filters), "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    return cmd, k


@dataclass
class DecoderStats:
    frames: int = 0
    restarts: int = 0
    last_frame_at: datetime | None = None
    last_error: str | None = None
    fps_actual: float = 0.0
    source: SourceInfo | None = None
    _recent: deque[float] = field(default_factory=lambda: deque(maxlen=64), repr=False)

    def tick(self, now_mono: float) -> None:
        self.frames += 1
        self._recent.append(now_mono)
        if len(self._recent) >= 2:
            span = self._recent[-1] - self._recent[0]
            self.fps_actual = (len(self._recent) - 1) / span if span > 0 else 0.0


class Decoder(threading.Thread):
    """One ffmpeg subprocess per camera, restarted with backoff, feeding ``on_frame``."""

    def __init__(
        self,
        camera_id: int,
        source: str,
        *,
        mode: str,
        target_fps: float,
        out_width: int,
        cpu: bool,
        on_frame: Callable[[Frame], None],
        on_discontinuity: Callable[[int, str, float | None, float | None], None] | None = None,
        reconnect_min_s: float = 2.0,
        reconnect_max_s: float = 30.0,
        file_realtime: bool = True,
        file_loop: int = 0,
        stall_timeout_s: float | None = None,
    ) -> None:
        super().__init__(name=f"decoder-{camera_id}", daemon=True)
        self.camera_id = camera_id
        self.source = source
        self.mode = mode
        self.target_fps = float(target_fps)
        self.out_width = int(out_width)
        self.cpu = bool(cpu)
        self.on_frame = on_frame
        self.on_discontinuity = on_discontinuity
        self.reconnect_min_s = reconnect_min_s
        self.reconnect_max_s = reconnect_max_s
        self.file_realtime = file_realtime
        self.file_loop = file_loop
        self.stall_timeout_s = stall_timeout_s or (30.0 if mode == "preindex" else 20.0)
        self.state = "starting"
        self.stats = DecoderStats()
        self.hwaccel_failed = False
        self.decimation = 1
        self._stop_event = threading.Event()
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    # ---- control ------------------------------------------------------
    def request_stop(self) -> None:
        """Mark the decoder as stopping without waiting (SIGTERM path).

        When the signal reaches the whole process group, ffmpeg dies before ``stop()`` is called;
        with the flag set that exit is not counted as a restart or reported as a discontinuity.
        """
        self._stop_event.set()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop_event.set()
        self._kill()
        if self.is_alive():
            self.join(timeout=timeout_s)
        self.state = "stopped"

    def _kill(self) -> None:
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    # ---- main loop ------------------------------------------------------
    def run(self) -> None:
        backoff = self.reconnect_min_s
        first = True
        dead_sessions = 0
        while not self._stop_event.is_set():
            if not first:
                if dead_sessions >= 3 and not is_rtsp(normalise_source(self.source)):
                    log.error("camera %s: file source failed %d times (%s) - giving up", self.camera_id, dead_sessions, self.stats.last_error)
                    self.state = "stopped"
                    return
                self.state = "reconnecting"
                self.stats.restarts += 1
                if self.on_discontinuity:
                    self.on_discontinuity(self.camera_id, f"decoder restart ({self.stats.last_error or 'exit'})", None, None)
                if self._stop_event.wait(backoff):
                    break
                backoff = min(self.reconnect_max_s, backoff * 2)
            first = False
            try:
                started = time.monotonic()
                frames_before = self.stats.frames
                clean_eof = self._session()
                dead_sessions = 0 if self.stats.frames > frames_before else dead_sessions + 1
                if clean_eof and not is_rtsp(normalise_source(self.source)):
                    self.state = "stopped"
                    log.info("camera %s: source finished (end of file)", self.camera_id)
                    return
                if clean_eof:
                    # An RTSP session that ends cleanly means the relay/camera closed it (relay restart,
                    # publisher gone, loop restart on the sandbox): reconnect with backoff, never stop.
                    self.stats.last_error = "stream ended (relay closed the session)"
                    log.warning("camera %s: RTSP stream ended - reconnecting with backoff", self.camera_id)
                if time.monotonic() - started > 60:
                    backoff = self.reconnect_min_s       # a healthy session resets the backoff
            except Exception as exc:  # noqa: BLE001 - keep the decoder alive
                dead_sessions += 1
                self.stats.last_error = f"{exc.__class__.__name__}: {exc}"
                self.state = "error"
                log.warning("camera %s: decoder error: %s", self.camera_id, self.stats.last_error)
        self.state = "stopped"

    def _session(self) -> bool:
        """Run one ffmpeg process until it exits. Returns True on a clean end-of-file."""
        info = probe(self.source)
        self.stats.source = info
        cmd, k = build_command(
            self.source, mode=self.mode, source_fps=info.fps, target_fps=self.target_fps,
            out_width=self.out_width, cpu=self.cpu or self.hwaccel_failed,
            file_realtime=self.file_realtime, file_loop=self.file_loop,
        )
        self.decimation = k
        expected_h = scaled_height(info.width, info.height, self.out_width)
        log.info("camera %s: %s %dx%d %.2f fps -> %dx%d, K=%d, hwaccel=%s",
                 self.camera_id, info.codec_label, info.width, info.height, info.fps,
                 self.out_width, expected_h, k, "cuda" if not (self.cpu or self.hwaccel_failed) else "none")
        log.debug("camera %s: %s", self.camera_id, " ".join(cmd))

        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        with self._lock:
            self._proc = proc
        assert proc.stdout is not None and proc.stderr is not None

        pts_map: dict[int, float] = {}
        pts_lock = threading.Lock()
        size_event = threading.Event()
        out_size: list[int] = [self.out_width, expected_h]
        err_tail: deque[str] = deque(maxlen=6)

        def read_stderr() -> None:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                m = _SHOWINFO_RE.search(line)
                if m:
                    with pts_lock:
                        pts_map[int(m.group(1))] = float(m.group(3))
                    continue
                if not size_event.is_set():
                    ms = _OUTPUT_SIZE_RE.search(line)
                    if ms:
                        out_size[0], out_size[1] = int(ms.group(1)), int(ms.group(2))
                        size_event.set()
                if line and "showinfo" not in line:
                    err_tail.append(line[:200])
                    if "error" in line.lower() or "failed" in line.lower():
                        log.debug("camera %s ffmpeg: %s", self.camera_id, line)

        err_thread = threading.Thread(target=read_stderr, name=f"ffmpeg-err-{self.camera_id}", daemon=True)
        err_thread.start()

        # The output stream line always precedes the first frame; wait briefly for the real size.
        size_event.wait(timeout=self.stall_timeout_s)
        width, height = out_size
        if width != self.out_width:
            log.warning("camera %s: ffmpeg output width %d != %d", self.camera_id, width, self.out_width)
        frame_bytes = width * height * 3
        buf = bytearray(frame_bytes)
        view = memoryview(buf)

        index = 0
        last_pts: float | None = None
        clean_eof = False
        progress = [time.monotonic()]
        watchdog_stop = threading.Event()

        def watchdog() -> None:
            while not watchdog_stop.wait(1.0):
                if time.monotonic() - progress[0] > self.stall_timeout_s and proc.poll() is None:
                    self.stats.last_error = "stalled"
                    log.warning("camera %s: no data for %.0f s - killing decoder", self.camera_id, self.stall_timeout_s)
                    self._kill()
                    return

        wd_thread = threading.Thread(target=watchdog, name=f"watchdog-{self.camera_id}", daemon=True)
        wd_thread.start()

        while not self._stop_event.is_set():
            got = 0
            while got < frame_bytes:
                chunk = proc.stdout.readinto(view[got:])
                if not chunk:
                    break
                got += chunk
                progress[0] = time.monotonic()
            if got < frame_bytes:
                try:
                    rc = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    rc = None
                if rc == 0 and got == 0 and self.stats.last_error != "stalled":
                    clean_eof = True
                elif self.stats.last_error != "stalled":
                    self.stats.last_error = f"ffmpeg exit {rc}: {' | '.join(list(err_tail)[-2:])}"
                break
            now = datetime.now(timezone.utc)
            with pts_lock:
                pts = pts_map.pop(index, None)
            if pts is None:
                # stderr can lag stdout by a few ms; give it a short grace period
                for _ in range(20):
                    time.sleep(0.002)
                    with pts_lock:
                        pts = pts_map.pop(index, None)
                    if pts is not None:
                        break
            estimated = pts is None
            if estimated:
                step = self.decimation / max(info.fps, 1.0)
                pts = (last_pts + step) if last_pts is not None else index * step
            with pts_lock:
                for stale in [n for n in pts_map if n < index - 200]:
                    pts_map.pop(stale, None)
            image = np.frombuffer(bytes(view), dtype=np.uint8).reshape((height, width, 3))
            frame = Frame(self.camera_id, image, now, float(pts), index, estimated)
            if last_pts is not None and pts < last_pts - 1.0 and self.on_discontinuity:
                self.on_discontinuity(self.camera_id, f"pts {last_pts:.1f} -> {pts:.1f} (discontinuity)", last_pts, float(pts))
            last_pts = float(pts)
            self.state = "running"
            self.stats.tick(time.monotonic())
            self.stats.last_frame_at = now
            self.stats.last_error = None
            self.on_frame(frame)
            index += 1

        watchdog_stop.set()
        self._kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        err_thread.join(timeout=2)
        with self._lock:
            self._proc = None
        if index == 0 and not clean_eof:
            tail = " | ".join(err_tail)
            if not self.cpu and not self.hwaccel_failed and any(h.lower() in tail.lower() for h in _HWACCEL_HINTS):
                self.hwaccel_failed = True
                log.warning("camera %s: CUDA decode failed (%s); falling back to software decode", self.camera_id, tail[-160:])
            self.stats.last_error = self.stats.last_error or f"no frames: {tail[-200:]}"
        return clean_eof and not self._stop_event.is_set()


def encode_jpeg(image: np.ndarray, quality: int = 80) -> bytes:
    import cv2

    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    return buf.tobytes()


def resize_width(image: np.ndarray, width: int) -> np.ndarray:
    import cv2

    h, w = image.shape[:2]
    if w == width:
        return image
    return cv2.resize(image, (width, max(1, int(round(h * width / w)))), interpolation=cv2.INTER_AREA)


def jpeg_under(image: np.ndarray, max_bytes: int, quality: int = 85, min_quality: int = 40) -> bytes:
    """JPEG-encode, lowering the quality until the result fits ``max_bytes``."""
    q = quality
    data = encode_jpeg(image, q)
    while len(data) > max_bytes and q > min_quality:
        q -= 10
        data = encode_jpeg(image, q)
    return data


def env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
