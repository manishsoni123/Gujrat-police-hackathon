# Sentinel Gujarat – ANPR / analytics worker

Team Dynatech Consultancy. One Python process per worker: N `ffmpeg` decoder subprocesses (one per
camera), **one** inference loop (plate detection → OCR → normalisation → voting → sightings, plus
YOLOX object counting), one HTTP sender thread and one control thread (config reload, heartbeat).
The worker never touches the database or MediaMTX's API: it reads RTSP from the relay and POSTs to
the API's `/api/internal/*` endpoints (`docs/CONTRACT.md` §7).

```
rtsp://mediamtx:8554/cam_<id> ──ffmpeg──▶ 960 px BGR frames (newest only, older dropped)
      │  decode.py                                   │
      ▼                                              ▼
 detector.py (onnx | contour | auto) ──▶ crops ──▶ ocr.py (PaddleOCR det+rec, two-line aware)
      ▼                                              ▼
 normalise.py (CONTRACT §3.4) ──▶ voting.py (3 s char-wise majority) ──▶ sightings.py (open/extend/close 15 s)
      ▼
 objects.py (YOLOX-s every 5th frame → tracker → per-minute counts, intrusion zones)
      ▼
 client.py  POST /internal/detections (1 s batches, one camera per request), /snapshots (1 s),
            /heartbeat (15 s), /events; GET /internal/anpr-config every 60 s
```

## Files

| File | Purpose |
|---|---|
| `pipeline.py` | Main loop, camera reconciliation, batching, loop-reset handling, SIGTERM flush. `python -m anpr.pipeline` |
| `decode.py` | `ffmpeg` wrapper: `-rtsp_transport tcp`, `-hwaccel cuda` unless `CPU=1`, frame decimation, `scale=960:-2`, raw `bgr24` pipe, PTS from `showinfo`, exponential restart backoff 2→30 s, stall watchdog, file sources for tests |
| `detector.py` | `onnx` (open-image-models YOLO-v9-t-384 end-to-end), `contour` (bright quadrilaterals, 0.15 ≤ h/w ≤ 0.6, w ≥ `ANPR_MIN_PLATE_W`, fill ≥ 0.6), `auto` (ONNX, contour only when ONNX finds nothing) |
| `ocr.py` | PaddleOCR (English det PP-OCRv3 + rec PP-OCRv4) on ×3 upscaled, grayscale, CLAHE crops; drops the vertical `IND` strip box; re-recognises a line when the detector split it into overlapping boxes; two lines joined with `\n` |
| `normalise.py` | The exact §3.4 algorithm (shared 20 test vectors with the backend) |
| `voting.py` | Per (camera, plate bucket) window; position-wise majority string, mean confidence, best member's bbox/crop/frame |
| `sightings.py` | `camera:plate:first_seen_ms` keys, best-read tracking, dirty flags for idempotent upserts |
| `objects.py` | YOLOX-s COCO ONNX, greedy IoU tracker (≥ 0.3), once-per-minute-per-track counts, polygon zones with IST active windows and dwell ≥ `dwell_s` |
| `client.py` | `X-API-Key` client, status policy (retry 5xx/network, drop 4xx, exit 3 on 401), background sender, `API_DRY_RUN` |
| `config.py` / `config.yml` | Settings: CLI > env > server `settings` > `config.yml` > defaults |
| `tools/eval_synthetic.py` | Accuracy/throughput scorer against `media/synthetic/plates.json` |
| `weights/` | `HASHES.txt` (SHA-256 + source + licence), `download.py` (build-time download with `--require`, `verify_weight_file` at worker start); `.onnx` files are downloaded at build time and git-ignored |
| `tests/` | `pytest` suite (no models needed): normalisation vectors, voting, sightings, contour detector, objects/zones, config/client |

## Modes

| `ANPR_MODE` | Cameras (from `GET /internal/anpr-config?mode=`) | Decode | Objects | Snapshots |
|---|---|---|---|---|
| `live` | `anpr_enabled` cameras, capped by `ANPR_MAX_CAMERAS` (3 with `CPU=1`, 12 with GPU) | `ANPR_FPS` (5) via `select=not(mod(n,K))` | yes, every `OBJECT_EVERY_N` frames | every `SNAPSHOT_INTERVAL_S` |
| `preindex` | every camera with an RTSP URL (skips `mode=both` when `PREINDEX_SKIP_LIVE=1`) | `-skip_frame nokey` (key frames only, ≈1 fps) | never | never |

Both modes post voted reads, sightings and `loop_reset` events. The worker re-fetches the camera list
every `CONFIG_RELOAD_S`; vanished cameras are flushed and stopped, new ones started, changed
`rtsp_url`s restarted, zone polygons refreshed in place.

## Detector backends (`ANPR_DETECTOR`)

- `onnx` – `yolo-v9-t-384-license-plates-end2end.onnx` (open-image-models, MIT) through
  onnxruntime (`CUDAExecutionProvider` when available and `CPU=0`, else CPU). Fails hard (exit 2)
  if the weights are missing or their SHA-256 does not match `weights/HASHES.txt`.
- `contour` – classical CV, no weights. Reads the **synthetic** plates (white rectangles) and is
  a weak fallback on real feeds.
- `auto` (default) – ONNX first; the contour detector runs only for frames where ONNX returned
  no box. When the ONNX weights are missing or corrupt the worker still starts, but **degraded**:
  an ERROR line at start-up, heartbeat `detector` = `contour-fallback` (shown on the Health page
  and in `GET /api/health` `anpr_workers[].detector`) and `extra.detector.degraded = true`.

The active backend is reported in the heartbeat (`detector`: `onnx` | `auto` | `contour` |
`contour-fallback`) together with `extra.detector` (`requested`, `active`, `degraded`, onnxruntime
`providers`, `weights` = `{state, sha256, licence}`) and `extra.object_weights`.

Weights are verified twice: `docker build` runs `weights/download.py --require`, so a failed
GitHub download or a wrong hash **fails the build** (`DOWNLOAD_WEIGHTS=0` verifies pre-placed files
for offline builds; `DOWNLOAD_WEIGHTS=none` knowingly builds a contour-only image), and the worker
re-hashes every model file it loads at start-up (`verify_weight_file`), refusing a mismatched file.

## Environment (CONTRACT §1.3) and test knobs

All contract variables are honoured (`CPU`, `ANPR_MODE`, `API_URL`, `INTERNAL_API_KEY`, `RTSP_BASE`,
`ANPR_CAMERAS`, `ANPR_MAX_CAMERAS`, `ANPR_FPS`, `PREINDEX_FPS`, `PREINDEX_SKIP_LIVE`, `FRAME_WIDTH`,
`ANPR_DETECTOR`, `ANPR_DET_MODEL`, `ANPR_DET_CONF`, `ANPR_MIN_PLATE_W`, `ANPR_VOTE_WINDOW_S`,
`ANPR_MIN_READ_CONF`, `SIGHTING_CLOSE_S`, `SNAPSHOT_INTERVAL_S`, `SNAPSHOT_WIDTH`, `POST_BATCH_S`,
`CONFIG_RELOAD_S`, `HEARTBEAT_S`, `OBJECT_DETECT`, `OBJECT_MODEL`, `OBJECT_CONF`, `OBJECT_EVERY_N`,
`OCR_LANG`, `RECONNECT_MIN_S`, `RECONNECT_MAX_S`, `LOG_LEVEL`). `config.yml` uses the same names in
lowercase. Values pinned by env or CLI are never overridden by the server's `settings` object.

Extra (testing / tuning):

| Variable / flag | Purpose |
|---|---|
| `API_DRY_RUN=1` / `--dry-run` | Print every POST as one JSON line (`{"dry_run":true,"endpoint":…,"payload":…,"files":{…}}`) instead of sending |
| `ANPR_SOURCES=1=/media/synthetic/cam_1.mp4,2=rtsp://…` / `--source ID=URL` | Static sources (file path, `file://` or `rtsp://`); skips the config endpoint |
| `ANPR_FILE_RE` / `--no-realtime` | File sources are paced with `-re` by default; disable for throughput runs |
| `ANPR_FILE_LOOP` / `--file-loop` | `0` once, `-1` forever |
| `ANPR_MAX_BOXES` | OCR budget per frame (default 4) |
| `OCR_THREADS` | PaddleOCR / onnxruntime CPU threads (default 2) |
| `WORKER_ID` / `ANPR_WORKER_ID` | Heartbeat id (default `<mode>-<6 hex>`) |
| `ANPR_NO_REEXEC=1` | Do not re-exec with `LD_BIND_NOW=1` (see Known issues) |

## Running

```bash
# CPU image (laptop); build context is the repository root
docker build -f anpr/Dockerfile -t sentinel-anpr .
# GPU image (VM with NVIDIA driver >= 535 + nvidia-container-toolkit; not testable on the laptop)
docker build -f anpr/Dockerfile --build-arg ANPR_BASE=gpu -t sentinel-anpr:gpu .

# Unit tests (no models needed)
docker run --rm sentinel-anpr python -m pytest -q anpr/tests

# File mode against the synthetic videos, no API needed
docker run --rm -v "$PWD/media:/media:ro" sentinel-anpr \
  python -m anpr.pipeline --dry-run --detector contour --source 1=/media/synthetic/cam_1.mp4

# Accuracy + throughput against plates.json
docker run --rm -v "$PWD/media:/media:ro" sentinel-anpr \
  python -m anpr.tools.eval_synthetic --media /media/synthetic --cameras 1 --detector contour
```

In compose the worker runs as `anpr-live` / `anpr-preindex` (profile `cpu`) or
`anpr-live-gpu` / `anpr-preindex-gpu` (profile `gpu`). Exit codes: 0 normal/SIGTERM (pending
batches and counts are flushed first), 2 configuration error, 3 the API rejected the internal key.

## Measured on the laptop (CPU=1)

Host: Intel Core i5-1345U (10 cores / 12 threads), Windows 11, Docker Desktop (WSL2), no GPU;
image `sentinel-anpr` (python:3.11-slim-bookworm, onnxruntime 1.19.2 CPU, paddlepaddle 2.6.2,
paddleocr 2.8.1, `OMP_NUM_THREADS=2`, `OCR_THREADS=2`). Measured 4–5 September 2026.

| Measurement | Result |
|---|---|
| Synthetic accuracy, `cam_1.mp4`, real time, `ANPR_DETECTOR=contour` | **8/8 appearances read exactly (100 %), 8 voted reads, 0 false reads** (`GJ01AB1234` at ≈5 s and `GJ05RS9012` at ≈65 s included; both two-line fillers read) |
| Same with `ANPR_DETECTOR=auto` | 8/8 (100 %), 0 false – ONNX finds nothing on the drawn plates, contour fallback takes over |
| Plate detector, 960×540 frame | ONNX YOLO-v9-t: **≈43 ms/frame** (2 threads); contour: **≈1 ms/frame** |
| ONNX detector on real photos (Wikimedia Commons, CC BY-SA 4.0) | `BH- Series number plate.png` → box conf 0.94; `Car number plate in Tamil.jpg` → conf 0.91 (Tamil-script plate, not OCR-able in English); `My Mad Number Plate In My WeGo.JPG` → conf 0.84 |
| PaddleOCR per crop (×3 upscale) | one-line 398×92 crop: **≈270 ms/read**; two-line 254×143 crop: **≈600 ms/read** (det + rec + one re-recognition of the split line). 4 threads is not faster on this CPU |
| Real-time run, 1 camera, contour, no objects (final image) | **4.67 frames/s processed** against the 5 fps target, detect 4.7 ms/frame, OCR 291 ms/call × 52 calls in 90 s |
| Throughput, 1 file source decoded as fast as possible, contour, no objects | decoder 75 fps, inference loop **33 frames/s** with 280 of 450 decoded frames dropped (newest-frame policy working); OCR ≈580 ms/call under that contention |
| H.265 source (`cam_8.mp4`, real time, contour) | software decode fine: 4.1 frames/s processed, reads incl. the two-line `MH02BZ7788` (10 votes) |
| 3 cameras real time, `auto` detector, objects on (70 s) | decoders hold 5.0 fps each; inference loop 5.9 frames/s total (≈2 fps per camera, 60 % of decoded frames dropped); ONNX 57 ms/frame, YOLOX 318 ms/call, OCR 392 ms/call; all anchor plates still read. Set `OBJECT_DETECT=0` or `ANPR_DETECTOR=contour` on this class of CPU to recover ~4 fps per camera |
| Synthetic generator (`scripts/make_synthetic_videos.py`, 8 × 90 s) | 224 s inside Docker on this laptop (contract target ≤ 180 s on a laptop CPU; 20–37 s per camera, libx265 for cam_8) |

CPU budget: on this laptop OCR is the bottleneck (2–4 reads/s), so a plate visible for 4 s at 5 fps
yields 5–10 OCR calls and one voted read per 3 s window – sufficient for the demo with
`ANPR_MAX_CAMERAS=3`. On a server-class CPU with MKL (or the T4 VM with `onnxruntime-gpu`) the plan's
20–40 ms OCR figure applies and 8–12 live cameras at 5 fps are realistic.

## Known issues

- **paddlepaddle 2.6 + system zlib.** `libpaddle.so` exports its own (older) zlib symbols and is
  loaded `RTLD_GLOBAL`. With lazy binding the system `libz.so.1` resolves its internal
  `inflateInit2_ → inflateReset2` call to Paddle's copy and every gzip/tar operation after
  `import paddle` segfaults (`C++ Traceback: inflateReset2`, exit 139) – including PaddleOCR's own
  model download. Fix shipped here: the image sets `LD_BIND_NOW=1`, `import zlib` precedes the
  Paddle import (`ocr.py`, Dockerfile warm-up), and `pipeline.py` re-execs itself with
  `LD_BIND_NOW=1` when the variable is missing.
- `paddleocr` sets the root logger to WARNING on import; the worker restores its level afterwards.
- PaddleOCR ships no PP-OCRv4 **English detection** model; the English det model is PP-OCRv3
  (recognition is PP-OCRv4). Models are fetched at build time into `/root/.paddleocr/whl/`.
- Route legs on the 90 s synthetic loop are always flagged `implausible_speed` by the API – expected.
- **RTSP end-of-stream is a reconnect, not a stop.** A clean ffmpeg EOF only stops *file* sources;
  for RTSP it means the relay/camera closed the session (relay restart, publisher gone, loop restart
  on the sandbox) and the decoder reconnects with the 2 → 30 s backoff and emits `loop_reset`.
  Before 5 Sept a MediaMTX restart left every camera in state `stopped`.
- **Memory grows slowly with 8 CPU decoders.** The worker starts at ≈ 2.0 GB RSS and was at
  ≈ 2.3 GB after 40 min (PaddlePaddle arena growth); `ANPR_LIVE_MEMORY=4g` on the laptop, and the
  `restart: unless-stopped` policy covers a cgroup kill during a 12 h soak. Watch `docker stats`
  during the soak and record the figure in `docs/acceptance-log.md`.
- The synthetic loops are encoded **without B-frames** (`-bf 0` / `bframes=0`): MediaMTX cannot
  deliver B-frame H.264 over WebRTC, so browser tiles stayed black with the earlier files.

## Licences (all open source)

| Component | Licence |
|---|---|
| ffmpeg (Debian / jrottenberg build) | LGPL-2.1+ / GPL-2+ |
| onnxruntime, numpy, requests, PyYAML, Pillow (HPND), opencv-python-headless | MIT / BSD / Apache-2.0 |
| PaddlePaddle, PaddleOCR (+ PP-OCR models) | Apache-2.0 |
| open-image-models `yolo-v9-t-384-license-plates-end2end.onnx` | MIT – SHA-256 in `weights/HASHES.txt` |
| Megvii YOLOX-s `yolox_s.onnx` | Apache-2.0 – SHA-256 in `weights/HASHES.txt` |
| pytorch/pytorch CUDA runtime image (GPU stage only) | BSD-3-Clause (PyTorch) |
