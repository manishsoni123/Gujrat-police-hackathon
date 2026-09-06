"""decode.py without ffmpeg: command shape for RTSP vs file sources, ffmpeg stderr parsing, the dial gate."""
import threading
import time

from anpr.config import load_settings
from anpr.decode import (
    DEFAULT_RTSP_TIMEOUT_S,
    DialGate,
    build_command,
    parse_input_stream_line,
    scaled_height,
    time_select_expr,
)


def _vf(cmd: list[str]) -> str:
    return cmd[cmd.index("-vf") + 1]


def test_rtsp_live_command_is_time_based_with_30s_socket_timeout_and_threads():
    cmd, k, interval = build_command(
        "rtsp://mediamtx:8554/cam_61", mode="live", source_fps=0.0, target_fps=2.0, out_width=1280,
        cpu=True, file_realtime=True, file_loop=0,
    )
    assert k == 1 and interval == 0.5
    assert cmd[cmd.index("-timeout") + 1] == str(int(DEFAULT_RTSP_TIMEOUT_S * 1_000_000))
    assert cmd[cmd.index("-threads") + 1] == "2"
    assert "-rtsp_transport" in cmd and "-re" not in cmd and "-hwaccel" not in cmd
    vf = _vf(cmd)
    assert vf.startswith(time_select_expr(0.5))
    assert "gte(t-prev_selected_t\\,0.5000)" in vf and "lt(t\\,prev_selected_t)" in vf and "isnan(prev_selected_t)" in vf
    assert vf.endswith("scale=1280:-2,showinfo")
    assert cmd[cmd.index("-fps_mode") + 1] == "passthrough"


def test_rtsp_preindex_keeps_key_frames_only_and_honours_preindex_fps():
    cmd, k, interval = build_command(
        "rtsp://mediamtx:8554/cam_63", mode="preindex", source_fps=0.0, target_fps=0.5, out_width=960,
        cpu=True, file_realtime=True, file_loop=0,
    )
    assert k == 1 and interval == 2.0
    assert cmd.index("-skip_frame") < cmd.index("-i") and cmd[cmd.index("-skip_frame") + 1] == "nokey"
    assert "gte(t-prev_selected_t\\,2.0000)" in _vf(cmd)


def test_file_source_keeps_frame_count_decimation():
    cmd, k, interval = build_command(
        "file:///media/synthetic/cam_1.mp4", mode="live", source_fps=25.0, target_fps=5.0, out_width=960,
        cpu=True, file_realtime=True, file_loop=-1,
    )
    assert k == 5 and interval == 0.0
    assert "-re" in cmd and cmd[cmd.index("-stream_loop") + 1] == "-1"
    assert "-timeout" not in cmd and "-rtsp_transport" not in cmd
    assert _vf(cmd) == "select=not(mod(n\\,5)),scale=960:-2,showinfo"
    assert cmd[cmd.index("-i") + 1] == "/media/synthetic/cam_1.mp4"


def test_gpu_command_inserts_hwaccel_before_input():
    cmd, _k, _i = build_command(
        "rtsp://mediamtx:8554/cam_61", mode="live", source_fps=0.0, target_fps=5.0, out_width=1920,
        cpu=False, file_realtime=True, file_loop=0, threads=0,
    )
    assert cmd.index("-hwaccel") < cmd.index("-i") and cmd[cmd.index("-hwaccel") + 1] == "cuda"
    assert "-threads" not in cmd


def test_parse_ffmpeg_input_stream_line():
    info = parse_input_stream_line("  Stream #0:0: Video: h264 (High), yuv420p(progressive), 1920x1080 [SAR 1:1 DAR 16:9], 30 fps, 25 tbr, 90k tbn")
    assert info is not None
    assert (info.codec, info.width, info.height, info.fps) == ("h264", 1920, 1080, 30.0)
    assert info.codec_label == "H264"
    hevc = parse_input_stream_line("  Stream #0:0: Video: hevc (Main), yuv420p(tv), 2560x1440, 12.50 fps, 25 tbr, 90k tbn")
    assert hevc is not None and hevc.codec_label == "H265" and (hevc.width, hevc.height, hevc.fps) == (2560, 1440, 12.5)
    nofps = parse_input_stream_line("  Stream #0:0: Video: h264 (Main), yuv420p(progressive), 1280x720, 90k tbn")
    assert nofps is not None and nofps.fps == 0.0 and nofps.width == 1280
    assert parse_input_stream_line("  Stream #0:0 -> #0:0 (h264 (native) -> rawvideo (native))") is None
    assert parse_input_stream_line("Input #0, rtsp, from 'rtsp://mediamtx:8554/cam_61':") is None
    assert parse_input_stream_line("  Stream #0:1: Audio: aac (LC), 8000 Hz, mono, fltp") is None


def test_scaled_height_is_even():
    assert scaled_height(1920, 1080, 1280) == 720
    assert scaled_height(1280, 960, 960) == 720
    assert scaled_height(1920, 1080, 960) == 540


def test_dial_gate_serialises_and_spaces_dials():
    gate = DialGate(spacing_s=0.2, hold_max_s=5.0)
    stop = threading.Event()
    starts: list[float] = []
    lock = threading.Lock()

    def dial(cam: int) -> None:
        assert gate.acquire(stop, cam)
        with lock:
            starts.append(time.monotonic())
        assert gate.busy
        time.sleep(0.05)
        gate.release()

    threads = [threading.Thread(target=dial, args=(i,)) for i in range(4)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert len(starts) == 4 and gate.dials == 4 and not gate.busy
    starts.sort()
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    # every dial starts at least spacing_s after the previous one was released (0.05 s hold + 0.2 s gap)
    assert all(g >= 0.24 for g in gaps), gaps
    assert time.monotonic() - t0 < 4.0


def test_dial_gate_acquire_returns_false_when_stopped():
    gate = DialGate(spacing_s=10.0)
    assert gate.acquire(None)
    stop = threading.Event()
    stop.set()
    assert gate.acquire(stop) is False
    gate.release()
    gate.release()          # idempotent
    assert not gate.busy


def test_decoder_settings_from_env(tmp_path):
    env = {
        "ANPR_PROBE_TIMEOUT_S": "50", "ANPR_RTSP_PROBE": "1", "ANPR_RTSP_TIMEOUT_S": "25", "ANPR_START_TIMEOUT_S": "100",
        "ANPR_STALL_TIMEOUT_S": "45", "ANPR_DIAL_SPACING_S": "4", "ANPR_DECODE_THREADS": "3", "API_TIMEOUT_S": "20",
        "ANPR_OSD_BAND": "0.1", "ANPR_OSD_WARMUP_S": "20", "ANPR_INVALID_READS_PER_MIN": "5",
    }
    s = load_settings(["--config", str(tmp_path / "none.yml")], environ=env)
    assert (s.probe_timeout_s, s.rtsp_probe, s.rtsp_timeout_s, s.start_timeout_s, s.stall_timeout_s) == (50.0, True, 25.0, 100.0, 45.0)
    assert (s.dial_spacing_s, s.decode_threads, s.api_timeout_s) == (4.0, 3, 20.0)
    assert (s.osd_band, s.osd_warmup_s, s.invalid_reads_per_min) == (0.1, 20.0, 5)
    d = load_settings(["--config", str(tmp_path / "none.yml")], environ={})
    assert (d.probe_timeout_s, d.rtsp_probe, d.rtsp_timeout_s, d.start_timeout_s, d.stall_timeout_s) == (45.0, False, 30.0, 90.0, 60.0)
    assert (d.dial_spacing_s, d.decode_threads, d.api_timeout_s, d.osd_band, d.osd_warmup_s, d.invalid_reads_per_min) == (3.0, 2, 30.0, 0.08, 30.0, 10)
