"""ffmpeg decoder subprocess per camera (CONTRACT.md section 7.7, plan 5.5 step 1).

Command shape (live mode, CPU=1, RTSP source)::

    ffmpeg -nostdin -hide_banner -loglevel info -nostats -rtsp_transport tcp -timeout 30000000 -threads 2
           -i rtsp://mediamtx:8554/cam_12 -an -sn -dn
           -vf select=isnan(prev_selected_t)+gte(t-prev_selected_t\\,0.5)+lt(t\\,prev_selected_t),scale=1280:-2,showinfo
           -fps_mode passthrough -f rawvideo -pix_fmt bgr24 -

* ``-rtsp_transport tcp`` always (sandbox rule); ``-timeout`` is the RTSP socket I/O timeout
  (``ANPR_RTSP_TIMEOUT_S``, 30 s: the organiser relay answers a DESCRIBE in 4-38 s); ``-hwaccel cuda``
  is inserted before ``-i`` when ``CPU=0`` and removed automatically if the first attempt fails on
  the hardware decoder.
* **No pre-probe for RTSP sources** (``ANPR_RTSP_PROBE=0``, the default): an ``ffprobe`` dial costs a
  second RTSP session per camera against a server that refuses bursts of dials and used to time out
  at 15 s while the relay was still starting the source on demand (restart storms on the real
  feeds). Codec, size and nominal fps are parsed from ffmpeg's own ``Input #0`` / ``Stream #0:0``
  stderr lines instead. ``ANPR_RTSP_PROBE=1`` restores the probe with ``ANPR_PROBE_TIMEOUT_S`` (45 s).
  File sources are always probed (the synthetic loops need the exact source fps).
* Frame-rate reduction for RTSP sources is **time based**: ``select`` keeps a frame when at least
  ``1 / ANPR_FPS`` seconds of stream time passed since the previously selected frame (``gte``), when
  nothing was selected yet (``isnan``) and when the PTS jumped backwards (``lt``, the sandbox loop
  restart - without it the filter would wait for the clock to catch up). It needs no source fps and
  copes with feeds whose nominal rate is wrong (a "10 fps" feed delivering 25). File sources keep the
  frame-count decimation ``select=not(mod(n,K))`` with ``K = round(source_fps / target_fps)``.
  ``-fps_mode passthrough`` instead of the ``fps=`` filter: ``fps`` re-times on PTS and (a) stalls for
  the whole loop length when a feed's PTS jumps backwards, (b) duplicates frames in pre-index mode.
  Every frame carries the source ``pts_time`` parsed from ``showinfo`` on stderr (``stream_pts``); if
  the line is missing for a frame the value is estimated from the selection interval and flagged
  ``pts_estimated``.
* ``captured_at`` is the UTC wall clock at frame receipt (what the jury reads).
* Pre-index mode adds ``-skip_frame nokey`` before ``-i`` (key frames only) and the same time-based
  select with ``1 / PREINDEX_FPS`` so ``PREINDEX_FPS=0.5`` really halves the load.
* File sources (``/path``, ``file:///path``) are supported for tests and soak runs: ``-re`` paces
  them in real time (``ANPR_FILE_RE=0`` disables), ``-stream_loop`` repeats them
  (``ANPR_FILE_LOOP``: 0 once, -1 forever). A clean end-of-file stops the decoder (state
  ``stopped``) for FILE sources only; for RTSP sources every exit - clean EOF included, which is
  what a relay restart looks like - restarts it with exponential backoff 2 -> 30 s. The backoff is
  reset only after a session that delivered frames for more than 60 s, so a source that fails to
  dial or stalls immediately never restarts faster than the backoff allows.
* Watchdog: a session gets ``ANPR_START_TIMEOUT_S`` (90 s) to deliver its first frame (the lossiest
  sandbox feeds need up to 88 s) and is killed after ``ANPR_STALL_TIMEOUT_S`` (60 s) without a frame
  afterwards.
* :class:`DialGate` (one per worker process) lets only **one** decoder dial at a time and spaces
  dials ``ANPR_DIAL_SPACING_S`` (3 s) apart - at start-up and on every reconnect - so eight cameras
  never hit the relay/organiser server as a burst. The gate is released as soon as ffmpeg has
  parsed the input stream (RTSP handshake done), on the first frame, after ``hold_max_s`` or when
  the session ends, whichever comes first.
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
# "Stream #0:0: Video: h264 (High), yuv420p(progressive), 1920x1080 [SAR 1:1 DAR 16:9], 30 fps, 25 tbr, 90k tbn"
_INPUT_STREAM_RE = re.compile(
    r"Stream #\d+:\d+(?:\[[^\]]*\])?(?:\([^)]*\))?:\s*Video:\s*([A-Za-z0-9_]+).*?\b(\d{2,5})x(\d{2,5})\b(?:.*?\b(\d+(?:\.\d+)?)\s*fps)?"
)
_HWACCEL_HINTS = ("cuda", "cuvid", "hwaccel", "nvdec", "No device", "device")

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

DEFAULT_PROBE_TIMEOUT_S = 45.0
DEFAULT_RTSP_TIMEOUT_S = 30.0
DEFAULT_START_TIMEOUT_S = 90.0
DEFAULT_STALL_TIMEOUT_S = 60.0
DEFAULT_DIAL_SPACING_S = 3.0
DEFAULT_DIAL_HOLD_MAX_S = 45.0
DEFAULT_DECODE_THREADS = 2


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


def parse_input_stream_line(line: str) -> SourceInfo | None:
    """``SourceInfo`` from ffmpeg's input ``Stream #0:0: Video: ...`` stderr line (None when it is not one)."""
    m = _INPUT_STREAM_RE.search(line)
    if not m:
        return None
    codec, width, height, fps = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
    if width <= 0 or height <= 0:
        return None
    return SourceInfo(width=width, height=height, fps=float(fps) if fps else 0.0, codec=codec.lower())


def probe(source: str, timeout_s: float = DEFAULT_PROBE_TIMEOUT_S) -> SourceInfo:
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


def time_select_expr(interval_s: float) -> str:
    """``select`` expression keeping one frame per ``interval_s`` of stream time, loop-jump safe."""
    return f"select=isnan(prev_selected_t)+gte(t-prev_selected_t\\,{interval_s:.4f})+lt(t\\,prev_selected_t)"


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
    rtsp_timeout_s: float = DEFAULT_RTSP_TIMEOUT_S,
    time_select: bool | None = None,
    threads: int = DEFAULT_DECODE_THREADS,
) -> tuple[list[str], int, float]:
    """Return the ffmpeg argv, the frame-count decimation factor K (1 when time based) and the
    selection interval in seconds (0 when frame-count based).

    ``time_select`` defaults to True for RTSP sources and False for files.
    """
    src = normalise_source(source)
    rtsp = is_rtsp(src)
    if time_select is None:
        time_select = rtsp
    interval = 1.0 / max(target_fps, 0.05) if time_select else 0.0
    if time_select or mode == "preindex":
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
    if threads and threads > 0:
        cmd += ["-threads", str(int(threads))]
    cmd += ["-i", src, "-an", "-sn", "-dn"]
    filters = []
    if time_select:
        filters.append(time_select_expr(interval))
    elif k > 1:
        filters.append(f"select=not(mod(n\\,{k}))")
    if out_width > 0:
        filters.append(f"scale={out_width}:-2")       # out_width <= 0: native resolution (no scale)
    filters.append("showinfo")
    cmd += ["-vf", ",".join(filters), "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    return cmd, k, interval


class DialGate:
    """Serialises RTSP dials across the decoders of one worker process.

    ``acquire`` blocks until no other decoder is dialling and at least ``spacing_s`` passed since
    the previous dial was released; it returns False when ``stop`` is set meanwhile. The holder
    releases the gate once ffmpeg has parsed the input stream (handshake done), on the first frame,
    after ``hold_max_s`` (a dial that neither fails nor answers must not block the others forever)
    or when the session ends.
    """

    def __init__(self, spacing_s: float = DEFAULT_DIAL_SPACING_S, hold_max_s: float = DEFAULT_DIAL_HOLD_MAX_S) -> None:
        self.spacing_s = max(0.0, float(spacing_s))
        self.hold_max_s = max(1.0, float(hold_max_s))
        self._cond = threading.Condition()
        self._busy = False
        self._last_release = -1e9
        self.dials = 0

    def acquire(self, stop: threading.Event | None = None, camera_id: int | None = None) -> bool:
        waited_log = False
        with self._cond:
            while True:
                if stop is not None and stop.is_set():
                    return False
                wait = self.spacing_s - (time.monotonic() - self._last_release)
                if not self._busy and wait <= 0:
                    self._busy = True
                    self.dials += 1
                    return True
                if not waited_log and camera_id is not None:
                    log.debug("camera %s: waiting for the dial gate", camera_id)
                    waited_log = True
                self._cond.wait(timeout=0.25 if self._busy else max(0.01, wait))

    def release(self) -> None:
        with self._cond:
            if self._busy:
                self._busy = False
                self._last_release = time.monotonic()
                self._cond.notify_all()

    @property
    def busy(self) -> bool:
        return self._busy


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
        start_timeout_s: float = DEFAULT_START_TIMEOUT_S,
        probe_timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
        rtsp_probe: bool = False,
        rtsp_timeout_s: float = DEFAULT_RTSP_TIMEOUT_S,
        decode_threads: int = DEFAULT_DECODE_THREADS,
        dial_gate: DialGate | None = None,
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
        self.rtsp = is_rtsp(normalise_source(source))
        self.stall_timeout_s = float(stall_timeout_s or (DEFAULT_STALL_TIMEOUT_S if self.rtsp else 30.0))
        self.start_timeout_s = max(float(start_timeout_s), self.stall_timeout_s)
        self.probe_timeout_s = float(probe_timeout_s)
        self.rtsp_probe = bool(rtsp_probe)
        self.rtsp_timeout_s = float(rtsp_timeout_s)
        self.decode_threads = int(decode_threads)
        self.dial_gate = dial_gate if self.rtsp else None
        self.state = "starting"
        self.stats = DecoderStats()
        self.hwaccel_failed = False
        self.decimation = 1
        self.interval_s = 0.0
        self._stop_event = threading.Event()
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._gate_held = False

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
        self._release_gate()
        self.state = "stopped"

    def _kill(self) -> None:
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def _release_gate(self) -> None:
        with self._lock:
            held, self._gate_held = self._gate_held, False
        if held and self.dial_gate is not None:
            self.dial_gate.release()

    # ---- main loop ------------------------------------------------------
    def run(self) -> None:
        backoff = self.reconnect_min_s
        first = True
        dead_sessions = 0
        while not self._stop_event.is_set():
            if not first:
                if dead_sessions >= 3 and not self.rtsp:
                    log.error("camera %s: file source failed %d times (%s) - giving up", self.camera_id, dead_sessions, self.stats.last_error)
                    self.state = "stopped"
                    return
                self.state = "reconnecting"
                self.stats.restarts += 1
                if self.on_discontinuity:
                    self.on_discontinuity(self.camera_id, f"decoder restart ({self.stats.last_error or 'exit'})", None, None)
                log.info("camera %s: reconnect in %.0f s (%s)", self.camera_id, backoff, self.stats.last_error or "exit")
                if self._stop_event.wait(backoff):
                    break
                backoff = min(self.reconnect_max_s, backoff * 2)
            first = False
            try:
                started = time.monotonic()
                frames_before = self.stats.frames
                clean_eof = self._session()
                delivered = self.stats.frames > frames_before
                dead_sessions = 0 if delivered else dead_sessions + 1
                if clean_eof and not self.rtsp:
                    self.state = "stopped"
                    log.info("camera %s: source finished (end of file)", self.camera_id)
                    return
                if clean_eof:
                    # An RTSP session that ends cleanly means the relay/camera closed it (relay restart,
                    # publisher gone, loop restart on the sandbox): reconnect with backoff, never stop.
                    self.stats.last_error = "stream ended (relay closed the session)"
                    log.warning("camera %s: RTSP stream ended - reconnecting with backoff", self.camera_id)
                if delivered and time.monotonic() - started > 60:
                    backoff = self.reconnect_min_s       # a healthy session resets the backoff
            except Exception as exc:  # noqa: BLE001 - keep the decoder alive
                dead_sessions += 1
                self.stats.last_error = f"{exc.__class__.__name__}: {exc}"
                self.state = "error"
                log.warning("camera %s: decoder error: %s", self.camera_id, self.stats.last_error)
            finally:
                self._release_gate()
        self.state = "stopped"

    def _session(self) -> bool:
        """Run one ffmpeg process until it exits. Returns True on a clean end-of-file."""
        info: SourceInfo | None = None
        if not self.rtsp or self.rtsp_probe:
            if self.dial_gate is not None and not self._acquire_gate():
                return False
            info = probe(self.source, self.probe_timeout_s)
            self.stats.source = info
        elif self.dial_gate is not None and not self._acquire_gate():
            return False
        cmd, k, interval = build_command(
            self.source, mode=self.mode, source_fps=info.fps if info else 0.0, target_fps=self.target_fps,
            out_width=self.out_width, cpu=self.cpu or self.hwaccel_failed,
            file_realtime=self.file_realtime, file_loop=self.file_loop,
            rtsp_timeout_s=self.rtsp_timeout_s, threads=self.decode_threads,
        )
        self.decimation = k
        self.interval_s = interval
        expected_h = (scaled_height(info.width, info.height, self.out_width) if self.out_width > 0 else info.height) if info else 0
        if info:
            log.info("camera %s: %s %dx%d %.2f fps -> %dx%d, K=%d, interval=%.2fs, hwaccel=%s",
                     self.camera_id, info.codec_label, info.width, info.height, info.fps,
                     self.out_width, expected_h, k, interval, "cuda" if not (self.cpu or self.hwaccel_failed) else "none")
        else:
            log.info("camera %s: dialling %s (no pre-probe, interval=%.2fs, hwaccel=%s)", self.camera_id,
                     self.source, interval, "cuda" if not (self.cpu or self.hwaccel_failed) else "none")
        log.debug("camera %s: %s", self.camera_id, " ".join(cmd))

        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        with self._lock:
            self._proc = proc
        assert proc.stdout is not None and proc.stderr is not None

        pts_map: dict[int, float] = {}
        pts_lock = threading.Lock()
        size_event = threading.Event()
        input_event = threading.Event()
        out_size: list[int] = [self.out_width, expected_h]
        err_tail: deque[str] = deque(maxlen=6)
        source_info: list[SourceInfo | None] = [info]

        def read_stderr() -> None:
            phase = "input"
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                m = _SHOWINFO_RE.search(line)
                if m:
                    with pts_lock:
                        pts_map[int(m.group(1))] = float(m.group(3))
                    continue
                stripped = line.lstrip()
                if stripped.startswith("Output #") or stripped.startswith("Stream mapping"):
                    phase = "output"
                if phase == "input" and not input_event.is_set():
                    parsed = parse_input_stream_line(line)
                    if parsed:
                        source_info[0] = parsed
                        self.stats.source = parsed
                        input_event.set()
                        self._release_gate()          # RTSP handshake done: the next camera may dial
                        log.info("camera %s: %s %dx%d %s fps (from ffmpeg) -> %d px, interval=%.2fs",
                                 self.camera_id, parsed.codec_label, parsed.width, parsed.height,
                                 f"{parsed.fps:.0f}" if parsed.fps else "?", self.out_width, interval)
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

        # The output stream line always precedes the first frame; wait for the real size (the process
        # exiting early - refused dial, timeout - ends the wait as well).
        deadline = time.monotonic() + self.start_timeout_s
        while not size_event.is_set() and proc.poll() is None and time.monotonic() < deadline:
            size_event.wait(timeout=0.5)
        width, height = out_size
        if not size_event.is_set() or height <= 0:
            self._kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            err_thread.join(timeout=2)
            with self._lock:
                self._proc = None
            tail = " | ".join(list(err_tail)[-3:])
            if proc.returncode not in (None, 0):
                raise RuntimeError(f"ffmpeg exit {proc.returncode} before any stream info: {tail[-240:]}")
            raise RuntimeError(f"no stream info within {self.start_timeout_s:.0f} s: {tail[-240:]}")
        if self.out_width > 0 and width != self.out_width:
            log.warning("camera %s: ffmpeg output width %d != %d", self.camera_id, width, self.out_width)
        frame_bytes = width * height * 3
        buf = bytearray(frame_bytes)
        view = memoryview(buf)

        index = 0
        last_pts: float | None = None
        clean_eof = False
        progress = [time.monotonic()]
        first_frame_at: list[float | None] = [None]
        gate_deadline = time.monotonic() + (self.dial_gate.hold_max_s if self.dial_gate else 0.0)
        watchdog_stop = threading.Event()

        def watchdog() -> None:
            while not watchdog_stop.wait(1.0):
                now_mono = time.monotonic()
                if self.dial_gate is not None and now_mono > gate_deadline:
                    self._release_gate()
                budget = self.stall_timeout_s if first_frame_at[0] is not None else self.start_timeout_s
                if now_mono - progress[0] > budget and proc.poll() is None:
                    self.stats.last_error = "stalled" if first_frame_at[0] is not None else f"no frame within {budget:.0f} s"
                    log.warning("camera %s: no data for %.0f s - killing decoder", self.camera_id, budget)
                    self._kill()
                    return

        wd_thread = threading.Thread(target=watchdog, name=f"watchdog-{self.camera_id}", daemon=True)
        wd_thread.start()

        fps_for_estimate = (source_info[0].fps if source_info[0] and source_info[0].fps else 0.0)
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
                elif self.stats.last_error not in ("stalled",) and not (self.stats.last_error or "").startswith("no frame within"):
                    self.stats.last_error = f"ffmpeg exit {rc}: {' | '.join(list(err_tail)[-2:])}"
                break
            now = datetime.now(timezone.utc)
            if first_frame_at[0] is None:
                first_frame_at[0] = time.monotonic()
                self._release_gate()
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
                if self.interval_s > 0:
                    step = self.interval_s
                else:
                    step = self.decimation / max(fps_for_estimate, 1.0)
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

    def _acquire_gate(self) -> bool:
        assert self.dial_gate is not None
        self.state = "starting" if self.stats.restarts == 0 else "reconnecting"
        if not self.dial_gate.acquire(self._stop_event, self.camera_id):
            return False
        with self._lock:
            self._gate_held = True
        return True


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
