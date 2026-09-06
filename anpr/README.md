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
| `pipeline.py` | Main loop, camera reconciliation, batching, loop-reset handling, SIGTERM flush, `ANPR_INVALID_READS_PER_MIN` cap (at most N invalid-format reads per camera per minute; valid plates never capped), heartbeat `extra.suppression` counters. `python -m anpr.pipeline` |
| `decode.py` | `ffmpeg` wrapper: `-rtsp_transport tcp`, `-timeout` 30 s (`ANPR_RTSP_TIMEOUT_S`), `-threads 2` (`ANPR_DECODE_THREADS`), `-hwaccel cuda` unless `CPU=1`, time-based frame selection for RTSP (`1/ANPR_FPS`, loop-jump safe, no source fps needed) / `select=not(mod(n,K))` for files, `scale=<FRAME_WIDTH>:-2`, raw `bgr24` pipe, PTS from `showinfo`; **no `ffprobe` before an RTSP dial** (stream info parsed from ffmpeg's stderr; `ANPR_RTSP_PROBE=1` + `ANPR_PROBE_TIMEOUT_S` restore it); 90 s first-frame budget (`ANPR_START_TIMEOUT_S`), 60 s stall watchdog (`ANPR_STALL_TIMEOUT_S`), restart backoff 2→30 s reset only after a session that delivered frames for > 60 s; `DialGate` = one decoder dials at a time, `ANPR_DIAL_SPACING_S` (3 s) apart; file sources for tests |
| `detector.py` | `TiledDetector` (`ANPR_DET_TILE` px tiles + whole frame, IoU merge, plausibility filter; off by default), `OsdMask` (`ANPR_OSD_BAND`: boxes centred in the top/bottom 8 % of the frame are never plates; `ANPR_OSD_WARMUP_S`: static-text regions = low temporal variance + strong edges in ≥ 80 % of 30 s of samples, refreshed every 30 s), `StaticBoxFilter` (`ANPR_STATIC_BOX_WINDOW`: boxes present in 80 % of the last N frames are overlays/signboards, muted), `onnx` (open-image-models YOLO-v9-t-384 end-to-end), `contour` (bright quadrilaterals, 0.15 ≤ h/w ≤ 0.6, w ≥ `ANPR_MIN_PLATE_W`, fill ≥ 0.6), `auto` (ONNX, contour only when ONNX finds nothing) |
| `ocr.py` | OCR backends (`ANPR_OCR`): `paddle` = PaddleOCR (English det PP-OCRv3 + rec PP-OCRv4) on an enhanced crop (`enhance_crop`: 6 px border, ×4 cubic upscale, gamma for dark crops, conservative inversion for white-on-black, CLAHE), det+rec on every crop by default (`ANPR_OCR_PADDLE_MODE=det`; the detector's tight text-line box is what makes PP-OCRv4 read a plate, recognition on the whole padded crop misreads; `auto` = rec-only on single-line crops), drops the vertical `IND` strip box, re-recognises a split line, lines joined with `\n`; `fast_plate` = fast-plate-ocr 1.1.0 (MIT) `global_mobile_vit_v2_ocr` / `cct_s_v2_global` ONNX recogniser, hash-verified; `ensemble` = both, agreement wins. Numbers per backend on the organiser crop bank: `docs/anpr-accuracy.md` |
| `tracks.py` | `PlateTracker` (`ANPR_BEST_SHOT`): per-camera IoU / centre-distance association of plate boxes across frames, the 3 best shots per track (width × Laplacian sharpness), OCR only when a track settles, grows ≥ 25 % or ends; `track_kind` classifies every ended track as `plate` (valid-format read), `vehicle` (box never read / invalid string) or `text` (letters-only OCR on every pass = signboard or burnt-in caption - counted separately, never a vehicle) |
| `evidence.py` | `EvidenceStore` (`ANPR_EVIDENCE_DIR`): one JPEG crop + JSON line per detected vehicle (UTC timestamps, bbox, width, frames, `kind` = `plate` \| `vehicle`, plate if read) under `<dir>/<camera>/<date>/`, capped per camera per hour; `text` tracks get no file |
| `normalise.py` | The exact §3.4 algorithm (shared 20 test vectors with the backend) |
| `voting.py` | Per (camera, plate bucket) window; position-wise majority string, mean confidence, best member's bbox/crop/frame. `plate_raw` comes from the highest-confidence member that **agrees with the vote** (fallback: display form of the voted string), so the API's re-normalisation of `plate_raw` (CONTRACT §7.2) reproduces the voted `plate_norm` instead of undoing the correction |
| `sightings.py` | `camera:plate:first_seen_ms` keys, best-read tracking, dirty flags for idempotent upserts |
| `objects.py` | YOLOX-s COCO ONNX, greedy IoU tracker (≥ 0.3), once-per-minute-per-track counts, polygon zones with IST active windows and dwell ≥ `dwell_s` |
| `client.py` | `X-API-Key` client, status policy (retry 5xx/network, drop 4xx, exit 3 on 401), background sender, `API_DRY_RUN` |
| `config.py` / `config.yml` | Settings: CLI > env > server `settings` > `config.yml` > defaults |
| `tools/eval_synthetic.py` | Accuracy/throughput scorer against `media/synthetic/plates.json` (`--cameras 1 2 …`, `--own-gate` for the synthetic own-gate loop) |
| `weights/` | `HASHES.txt` (SHA-256 + source + licence), `download.py` (build-time download with `--require`, `verify_weight_file` at worker start); `.onnx` files are downloaded at build time and git-ignored |
| `tests/` | `pytest` suite (no models needed): normalisation vectors, voting, sightings, contour detector, objects/zones, config/client, decoder command/dial gate, OSD mask, tracks, OCR enhancement/ensemble rule, evidence store |

## Modes

| `ANPR_MODE` | Cameras (from `GET /internal/anpr-config?mode=`) | Decode | Objects | Snapshots |
|---|---|---|---|---|
| `live` | `anpr_enabled` cameras, capped by `ANPR_MAX_CAMERAS` (3 with `CPU=1`, 12 with GPU) | `ANPR_FPS` (5) via `select=not(mod(n,K))` | yes, every `OBJECT_EVERY_N` frames | every `SNAPSHOT_INTERVAL_S` |
| `preindex` | every camera with an RTSP URL (skips `mode=both` when `PREINDEX_SKIP_LIVE=1`) | `-skip_frame nokey` (key frames only, ≈1 fps) | never | never |

Both modes post voted reads, sightings and `loop_reset` events. The worker re-fetches the camera list
every `CONFIG_RELOAD_S`; vanished cameras are flushed and stopped, new ones started, changed
`rtsp_url`s restarted, zone polygons refreshed in place.

## OCR backends (`ANPR_OCR`) and best-shot tracking

- `paddle` (default) – PaddleOCR on the enhanced crop; ≈ 300 ms per call on the laptop CPU.
- `fast_plate` – fast-plate-ocr `global_mobile_vit_v2_ocr` (MIT; `ANPR_OCR_FAST_MODEL=cct_s_v2_global` for the
  newer CCT model); one ONNX pass, ≈ 5-10 ms per call; weights + config hash-verified, a missing file is fatal
  (`RuntimeError`) rather than a silent fallback.
- `ensemble` – both; the plate they agree on wins (+0.1 confidence), else a valid-format read beats an invalid
  one, else the higher confidence above `ANPR_OCR_ENSEMBLE_MIN_CONF`.

With `ANPR_BEST_SHOT=1` the worker no longer OCRs every frame: `tracks.PlateTracker` follows each plate box,
keeps its three best crops (largest, discounted for blur) and reads them once the track settles or ends, then the
3 s char-wise vote runs over those reads. Boxes narrower than `ANPR_OCR_MIN_W` are tracked but not read; every
ended track is logged and (with `ANPR_EVIDENCE_DIR`) stored as a detected vehicle with its timestamp and crop -
except tracks whose OCR only ever returned letters (a signboard or burnt-in caption; `tracks.track_kind` = `text`),
which are logged as `static text`, counted in heartbeat `extra.vehicles[camera].text` and stored nowhere.
`FRAME_WIDTH=0` + `ANPR_DETECT_WIDTH=1280` decodes natively, detects on a 1280 px copy (3 tiled inferences) and
crops from the native frame. Accuracy per backend on the organiser crop bank: `docs/anpr-accuracy.md`;
bank collection: `scripts/collect_crops.py`, labelling sheets: `scripts/label_sheets.py`, scoring:
`scripts/eval_ocr_bank.py`.

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
`ANPR_DETECTOR`, `ANPR_DET_MODEL`, `ANPR_DET_CONF`, `ANPR_MIN_PLATE_W`, `ANPR_DET_TILE`, `ANPR_DET_TILE_OVERLAP`, `ANPR_STATIC_BOX_WINDOW`, `ANPR_VOTE_WINDOW_S`,
`ANPR_MIN_READ_CONF`, `SIGHTING_CLOSE_S`, `SNAPSHOT_INTERVAL_S`, `SNAPSHOT_WIDTH`, `POST_BATCH_S`,
`CONFIG_RELOAD_S`, `HEARTBEAT_S`, `OBJECT_DETECT`, `OBJECT_MODEL`, `OBJECT_CONF`, `OBJECT_EVERY_N`,
`OCR_LANG`, `RECONNECT_MIN_S`, `RECONNECT_MAX_S`, `LOG_LEVEL`), plus the real-feed variables added
on 5 Sept 2026 (CONTRACT Amendments): `ANPR_OSD_BAND` (0.08), `ANPR_OSD_WARMUP_S` (30),
`ANPR_INVALID_READS_PER_MIN` (10), `ANPR_PROBE_TIMEOUT_S` (45), `ANPR_RTSP_PROBE` (0),
`ANPR_RTSP_TIMEOUT_S` (30), `ANPR_START_TIMEOUT_S` (90), `ANPR_STALL_TIMEOUT_S` (60),
`ANPR_DIAL_SPACING_S` (3), `ANPR_DECODE_THREADS` (2), `API_TIMEOUT_S` (30), and the accuracy-pass variables:
`ANPR_OCR` (paddle | fast_plate | ensemble), `ANPR_OCR_FAST_MODEL` (global_mobile_vit_v2_ocr), `ANPR_OCR_PADDLE_MODE`
(det | auto | rec), `ANPR_OCR_ENSEMBLE_MIN_CONF` (0.45), `ANPR_OCR_MIN_W` (60 px), `ANPR_DETECT_WIDTH` (0 = decoded
width), `ANPR_BEST_SHOT` (1), `ANPR_TRACK_GAP_S` (2), `ANPR_TRACK_KEEP` (3), `ANPR_TRACK_STALL` (3),
`ANPR_EVIDENCE_DIR` (empty), `ANPR_EVIDENCE_PER_HOUR` (200), `ANPR_STATIC_BOX_HITS` (0.8; 0.5 on the organiser
feeds, whose captions are boxed intermittently); `FRAME_WIDTH=0` decodes at the source resolution. An OCR string
without a digit (signboard, burnt-in caption) is never voted (`extra.suppression.no_digit`).
`config.yml` uses the same names in lowercase. Values pinned by env or CLI are never overridden by the server's `settings`
object.

### Real organiser feeds (laptop profile, 5 Sept 2026)

`deploy/.env`: `ANPR_MAX_CAMERAS=8 PREINDEX_MAX_CAMERAS=2 ANPR_FPS=2 PREINDEX_FPS=0.5 FRAME_WIDTH=0
ANPR_DETECT_WIDTH=1280 ANPR_DETECTOR=onnx ANPR_DET_TILE=768 ANPR_MIN_PLATE_W=28 ANPR_OCR=ensemble
ANPR_OCR_FAST_MODEL=cct_xs_v2_global ANPR_OCR_MIN_W=60 ANPR_BEST_SHOT=1 ANPR_STATIC_BOX_HITS=0.5
ANPR_EVIDENCE_DIR=/app/media/anpr_evidence OBJECT_DETECT=0 ANPR_LIVE_MEMORY=4g` (GPU VM:
`ANPR_MAX_CAMERAS=12 ANPR_FPS=5 ANPR_DETECT_WIDTH=1920 ANPR_MIN_PLATE_W=40 OBJECT_DETECT=1`). Why each
decoder rule exists: the organiser server answers a DESCRIBE in 4-38 s and refuses bursts of dials,
several feeds deliver 0-7 frames per minute (upstream packet loss), and every camera burns a clock /
caption into the top or bottom band. Measured before/after figures are in `docs/sandbox-progress.md`.

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

# The image runs as the unprivileged user `anpr` (uid 10001, HOME=/app, PaddleOCR models in /app/.paddleocr)
docker run --rm sentinel-anpr id            # uid=10001(anpr) gid=10001(anpr)

# File mode against the synthetic videos, no API needed
docker run --rm -v "$PWD/media:/media:ro" sentinel-anpr \
  python -m anpr.pipeline --dry-run --detector contour --source 1=/media/synthetic/cam_1.mp4

# Accuracy + throughput against plates.json (add --own-gate to score media/synthetic/own_gate.mp4 as well)
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
| OCR backends on the organiser crop bank (5 Sept, 3 threads next to the live worker; `scripts/eval_ocr_bank.py`, `docs/anpr-accuracy.md`) | `paddle` (enhanced crop, det+rec) 165-460 ms/crop; `fast_plate` cct_xs_v2 **12-30 ms**, vit_v2 12 ms, cct_s 71 ms; `ensemble` (paddle + cct_xs) ≈ 190 ms. Accuracy on the 2 readable night plates: paddle 1/2, cct_xs 1/2 + 1 valid near-miss, ensemble the same at agreement confidence 1.0; 0 false valid plates on 48 sign/caption crops for every backend |
| Live worker, 8 organiser cameras, native decode + 1280 px tiled detection + best-shot tracking (5 Sept, 21:16-21:36 IST) | detect ≈ 165-175 ms/frame, 3-4 frames/s over 8 cameras at the 2 fps target, OCR only a few calls per minute (best-shot), 1.5-1.9 GB RSS, 215-380 % CPU; reads and vehicle counts in `docs/anpr-accuracy.md` §4 |
| Real-time run, 1 camera, contour, no objects (final image) | **4.67 frames/s processed** against the 5 fps target, detect 4.7 ms/frame, OCR 291 ms/call × 52 calls in 90 s |
| Throughput, 1 file source decoded as fast as possible, contour, no objects | decoder 75 fps, inference loop **33 frames/s** with 280 of 450 decoded frames dropped (newest-frame policy working); OCR ≈580 ms/call under that contention |
| H.265 source (`cam_8.mp4`, real time, contour) | software decode fine: 4.1 frames/s processed, reads incl. the two-line `MH02BZ7788` (10 votes) |
| 3 cameras real time, `auto` detector, objects on (70 s) | decoders hold 5.0 fps each; inference loop 5.9 frames/s total (≈2 fps per camera, 60 % of decoded frames dropped); ONNX 57 ms/frame, YOLOX 318 ms/call, OCR 392 ms/call; all anchor plates still read. Set `OBJECT_DETECT=0` or `ANPR_DETECTOR=contour` on this class of CPU to recover ~4 fps per camera |
| Synthetic generator (`scripts/make_synthetic_videos.py`, 8 × 90 s + `own_gate.mp4`) | 224 s inside Docker on this laptop for the 8 stream loops (contract target ≤ 180 s on a laptop CPU; 20–37 s per camera, libx265 for cam_8) plus ≈ 30 s for the own-gate loop |

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
- **Voted `plate_norm` vs `plate_raw` (fixed 5 Sept).** The read's `plate_raw` used to be the best member's raw OCR even when the vote had out-voted it; the API re-normalises `plate_raw` and overrode the vote, so a corrected mis-read (`GJ 01 AB 1284` voted to `GJ01AB1234`) was stored under the wrong plate while its sighting carried the voted one. `voting.raw_for_vote` now takes the raw string of a member that agrees with the vote; `test_voting.py` asserts `normalise(read.plate_raw).plate_norm == read.plate_norm` for every emitted read.
- The container runs as uid 10001 (`USER anpr`), writes nothing at run time and works with `read_only: true` + `tmpfs: /tmp`; `docker exec sentinel-anpr-live id` must not print `uid=0`.
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
| fast-plate-ocr 1.1.0 + its `global_mobile_vit_v2_ocr` / `cct_s_v2_global` / `cct_xs_v2_global` models (SHA-256 in `weights/HASHES.txt`); rich, markdown-it-py, mdurl (MIT), pygments (BSD-2) | MIT |
| open-image-models `yolo-v9-t-384-license-plates-end2end.onnx` | MIT – SHA-256 in `weights/HASHES.txt` |
| Megvii YOLOX-s `yolox_s.onnx` | Apache-2.0 – SHA-256 in `weights/HASHES.txt` |
| pytorch/pytorch CUDA runtime image (GPU stage only) | BSD-3-Clause (PyTorch) |
