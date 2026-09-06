# ANPR camera selection - organiser sandbox (30 real cameras)

Survey of plate readability on the 30 organiser cameras (`cam01`..`cam30`, DB ids 61-90, relay
paths `cam_61`..`cam_90`) and the resulting choice of which cameras get `anpr_enabled` (live ANPR
worker) and which stay on the pre-index worker. Measured on 5 Sept 2026, 18:45-19:10 IST, on the
CPU laptop against the live relay. Times are IST; the survey JSON stores UTC.

* Script: `scripts/survey_anpr.py` (runs in the `anpr` image; ONNX YOLO-v9-t plate detector with
  hash-verified weights + PaddleOCR, the worker's own code).
* Data: `media/anpr_survey.json` (one row per camera, parameters, score parts) and
  `media/anpr_survey/<camXX>.jpg` contact sheets (30 frames per camera with detector boxes: green =
  valid-format read, orange = OCR text without a valid format, red = box without a read, grey = OSD /
  static text ignored), `media/anpr_survey/overview.jpg` (best frame per camera, ranked).
  Earlier passes are kept in `media/anpr_survey/run1_keyframes/` and `run2_tiles/`.
* Applied via the API as `jury_admin` (`PUT /api/cameras/{id} {"anpr_enabled": …}`) - see §5.

## 1. What the sandbox footage is

Every sandbox stream is a **looped recording of the night of 13 June 2026** (the burnt-in OSD reads
`13-06-2026 22:08`-`22:42`; cam29 shows `14-05-2026 01:49 AM`). Twenty-four cameras are colour
street-lit night scenes (mean luminance 60-110), six are infrared/grey (cam03, cam06, cam07, cam17,
cam29 and the cameras that never delivered a frame). Most views are wide-angle junction PTZ presets
(Ahmedabad CSITMS `PTZ2` / `RLVD` cameras) where a vehicle occupies 100-400 px of a 1920 px frame,
so a number plate is **25-110 px wide at the source**. Daylight is therefore not a property of the
demo hour but of the recording, and no camera in the set is a dedicated ANPR lane camera.

## 2. Method

Per camera, five cameras at a time (the next group starts pulling while the current one is analysed,
so the relay never sees more than ~10 survey readers and each sandbox source is pulled once, by
MediaMTX):

1. `ffmpeg -rtsp_transport tcp` on the relay path, one frame per second of stream time
   (`select=isnan(prev_selected_t)+gte(t-prev_selected_t,1)`), native resolution, up to 30 frames or
   45 s after the first frame, 60 s start budget (the sandbox answers a DESCRIBE in 4-38 s and the
   relay starts the source on demand); a watchdog kills a pull that keeps the socket open without a
   decodable frame. Decoder warnings on stderr (`concealing`, `corrupt`, `missing`, …) are counted.
2. Plate detection twice: **production path** - the frame resized to `FRAME_WIDTH=960` exactly as
   the worker sees it - and **tiled path** - `anpr.detector.TiledDetector` (new, `ANPR_DET_TILE=768`,
   overlap 0.15: six 768 px tiles plus the whole frame, IoU-merged) at native resolution. The survey
   keeps boxes down to 24 px so plate size is *measured* instead of dropped by the production
   `ANPR_MIN_PLATE_W=60`.
3. PaddleOCR on the best three tiled crops per frame (≥ 40 px), `anpr.normalise` on the text; a read
   counts as *valid* when it matches the Indian standard or BH pattern.
4. Static overlays are excluded: a box at the same place in ≥ 60 % of frames, or OCR text without a
   digit (camera names such as `DELIGHT P1 RLVD`, `Bhavar`), is flagged `osd` and not counted.
5. Score 0-100 = 30 × min(1, plates/frame ÷ 0.5) + 20 × min(1, mean plate width ÷ 100 px) +
   30 × min(1, valid reads/frame ÷ 0.25) + 10 × mean OCR confidence of valid reads + 5 if colour
   night/day + 5 × decode health (frames received ÷ 30, halved above 50 warnings).

## 3. Results

| # | Camera | Score | Frames | Plates/frame | Plate width (src px) | Valid reads | Codec / res | Light | Reason | Mode |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | cam07 07 hero-showroom-gir-somnath (id 67) | 60.4 | 7 | 0.286 | 87 (max 90) | 1 (1 reads) | H264 (B-frames) 1920x1080 | night / IR, 42.4 | Only valid read of the survey: GJ32X9785 at 84 px on a parked two-wheeler (GJ32 = Gir Somnath, matches the camera), IR fixed camera on a showroom forecourt; but the feed is the lossiest of the set (88 s to first frame, 7 frames, 126 warnings) (7/30 frames, 126 decode warnings; 2 plates) | **live** |
| 2 | cam16 16 Visat P2 (id 76) | 49.8 | 7 | 0.571 | 68 (max 146) | 0 (1 reads) | H264 1920x1080 | street-lit night (colour), 101.6 | RLVD junction (Visat T junction P2), vehicles stop at the line 40-146 px plates, 0.57 plates/frame; lossy feed (48 concealment warnings) delivered only 7 frames (7/30 frames, 48 decode warnings; 4 plates; 5 OSD/sign boxes ignored; 1 found by the 960 px path) | **live** |
| 3 | cam12 12 Tri Mandir Adalaj Tollnaka (id 72) | 44.6 | 16 | 0.688 | 35 (max 36) | 0 | H265 1280x720 | street-lit night (colour), 128.3 | Adalaj toll plaza lane: buses/trucks queue at the barrier, 0.69 plates/frame, but 720p so plates are 34-36 px; 16/30 frames, 0 warnings (16/30 frames, 0 decode warnings; 11 plates; 7 found by the 960 px path) | **live** |
| 4 | cam08 08 majewadi-gate-junagadh (id 68) | 42.3 | 6 | 0.5 | 34 (max 39) | 0 | H264 (B-frames) 1920x1080 | street-lit night (colour), 120.6 | Majevadi gate: 0.5 plates/frame at 25-38 px but 6 frames, headlight glare; B-frame feed (6/30 frames, 86 decode warnings; 3 plates; 1 OSD/sign boxes ignored) | preindex |
| 5 | cam05 05 Visat teen Rasta (id 65) | 35.9 | 28 | 0.25 | 56 (max 182) | 0 (2 reads) | H264 1920x1080 | street-lit night (colour), 98.9 | Visat teen rasta PTZ preset: 28/30 frames, plates 27-49 px on passing cars/rickshaws; the 182 px read is a shop phone number on a signboard (28/30 frames, 14 decode warnings; 7 plates; 3 OSD/sign boxes ignored; 1 found by the 960 px path) | **live** |
| 6 | cam02 02 Janpath (id 62) | 33.5 | 30 | 0.1 | 141 (max 233) | 0 (1 reads) | H264 1920x1080 | street-lit night (colour), 101.7 | Janpath junction PTZ: 30/30 frames, cars pass 100-160 px plates in the near lane; OSD 'GUJARAT POLICE' sign ignored; 55 concealment warnings (30/30 frames, 55 decode warnings; 3 plates; 5 OSD/sign boxes ignored) | **live** |
| 7 | cam14 14 Delight RLVD (id 74) | 33.1 | 27 | 0.296 | 41 (max 54) | 0 (3 reads) | H264 1920x1080 | street-lit night (colour), 108.9 | Delight RLVD: 27/30 frames, 0.30 plates/frame at 36-54 px, the only camera where the 960 px production path also finds plates (14 boxes) (27/30 frames, 69 decode warnings; 8 plates; 20 OSD/sign boxes ignored; 14 found by the 960 px path) | **live** |
| 8 | cam23 30 kheram (id 83) | 31.4 | 11 | 0.091 | 150 (max 150) | 0 (1 reads) | H264 1280x720 | street-lit night (colour), 88 | Village lane (kheram) at night, no vehicles in 11 frames; the 150 px 'plate' is the burnt-in 'Camera 01' label - not a plate camera (11/30 frames, 61 decode warnings; 1 plates; 1 found by the 960 px path) | preindex |
| 9 | cam26 35 TANKAL (id 86) | 21.2 | 25 | 0.04 | 73 (max 73) | 0 | H265 2560x1440 | night / IR, 128.9 | 2560x1440 H.265 with almost every frame grey/corrupt (upstream loss); the single 73 px box is a decode artefact (25/30 frames, 2 decode warnings; 1 plates; 2 OSD/sign boxes ignored) | preindex |
| 10 | cam18 18 Rajkot CCTV (id 78) | 21 | 30 | 0.033 | 70 (max 70) | 0 | H265 1920x1080 | night / IR, 125.9 | H.265 Rajkot feed, 29 of 30 frames corrupt grey (upstream loss); one real frame with a glare-lit road (30/30 frames, 0 decode warnings; 1 plates; 1 OSD/sign boxes ignored) | preindex |
| 11 | cam01 01 Chiman bhai Bridge (id 61) | 19.7 | 30 | 0.067 | 29 (max 29) | 0 | H264 1920x1080 | street-lit night (colour), 105.9 | Chiman bhai bridge PTZ: cleanest feed of the set (30/30, 0 warnings, 3.7 s start) but vehicles are 200 m away, plates 28 px (30/30 frames, 0 decode warnings; 2 plates) | **live** |
| 12 | cam15 15 Suvidha park (id 75) | 8.3 | 20 | 0 | - | 0 | H264 1920x1080 | street-lit night (colour), 119.4 | Suvidha park RLVD: the only daylight recording (overcast), 20/30 clean frames, but the crossing is far and plates stay below 24 px (20/30 frames, 0 decode warnings) | preindex |
| 13 | cam13 13 CN Vidhyalaya (id 73) | 7.2 | 13 | 0 | - | 0 | H264 1920x1080 | street-lit night (colour), 90.9 | CN Vidhyalaya junction: 13/30 frames, vehicles cross far from the camera, no plate above 24 px (13/30 frames, 21 decode warnings) | preindex |
| 14 | cam04 04 Paldi Circle (id 64) | 6.8 | 22 | 0 | - | 0 | H264 1920x1080 | street-lit night (colour), 98.3 | Paldi Circle: busiest junction of the set with buses/rickshaws 3-5 m from the camera (113 px plate in run 2), but 57-94 concealment warnings and 4-22 frames per run (22/30 frames, 57 decode warnings) | **live** |
| 15 | cam19 19 KHAPARIA GRAM PANCHAYAT , TALUKA GANDEVI, DISTRICT NAVSARI (id 79) | 6.7 | 20 | 0 | - | 0 | H264 1280x720 | street-lit night (colour), 61.8 | Village panchayat lane, 20 frames, no vehicles (20/30 frames, 74 decode warnings) | preindex |
| 16 | cam20 20 Mohanpura (id 80) | 6.6 | 19 | 0 | - | 0 | H264 1280x720 | street-lit night (colour), 91.5 | Mohanpura lane, 19 frames, no vehicles (19/30 frames, 76 decode warnings) | preindex |
| 17 | cam30 Gandhidham Rambaugh p2 (id 90) | 6 | 12 | 0 | - | 0 | H264 1920x1080 | street-lit night (colour), 91.4 | Gandhidham junction: 12/30 frames, wide view, no plate above 24 px (12/30 frames, 51 decode warnings) | preindex |
| 18 | cam25 34 dhanori (id 85) | 5.4 | 5 | 0 | - | 0 | H264 (B-frames) 1280x960 | street-lit night (colour), 81.3 | B-frame H.264, 5 corrupt frames (5/30 frames, 93 decode warnings) | preindex |
| 19 | cam11 11 dolatpara-junagadh (id 71) | 5.1 | 1 | 0 | - | 0 | H264 (B-frames) 1920x1080 | street-lit night (colour), 139.9 | B-frame H.264, one corrupt frame in 45 s (1/30 frames, 81 decode warnings) | preindex |
| 20 | cam03 03 O.N.G.C. Office (id 63) | 5 | 30 | 0 | - | 0 | H264 1280x720 | night / IR, 92.4 | IR grey compound, no vehicles (30/30 frames, 0 decode warnings) | preindex |
| 21 | cam17 17 Rajkot Bus Port CCTV (id 77) | 5 | 30 | 0 | - | 0 | H265 1920x1080 | night / IR, 128.6 | Rajkot bus port H.265: 30/30 frames but grey/corrupt (loss), buses parked (30/30 frames, 0 decode warnings) | preindex |
| 22 | cam24 33 dehgam (id 84) | 5 | 30 | 0 | - | 0 | H264 (B-frames) 960x576 | night / IR, 88.7 | dehgam IR yard, 960x576, no vehicles (30/30 frames, 0 decode warnings) | preindex |
| 23 | cam28 37 bilimora (id 88) | 5 | 30 | 0 | - | 0 | H264 (B-frames) 1280x960 | night / IR, 114.6 | bilimora IR courtyard, no vehicles (30/30 frames, 25 decode warnings; 2 OSD/sign boxes ignored) | preindex |
| 24 | cam09 09 new-bypass-near-by-circle-junagadh-2 (id 69) | 3.2 | 19 | 0 | - | 0 | H264 (B-frames) 1920x1080 | night / IR, 14.9 | IR bypass road, 15/255 brightness, only headlights visible (19/30 frames, 21 decode warnings) | preindex |
| 25 | cam29 38 bilimora (id 89) | 2.5 | 30 | 0 | - | 0 | H264 (B-frames) 1280x960 | night / IR, 108.7 | Indoor IR corridor of a building - no vehicles ever (30/30 frames, 57 decode warnings; 10 OSD/sign boxes ignored; 3 found by the 960 px path) | preindex |
| 26 | cam06 06 Timbavadi gate-Junagadh (id 66) | 0 | 0 | 0 | - | 0 | H265 1920x1080 | - | H.265, no decodable frame in 60 s (decoder errors on every NAL) (no frame in 60 s (no frame within the budget)) | preindex (retry) |
| 27 | cam10 10 char-chowk-road-2-junagadh (id 70) | 0 | 0 | 0 | - | 0 | H264 (B-frames) 1920x1080 | - | B-frame H.264, no frame in 60 s (upstream loss) (no frame in 60 s (no frame within the start budget)) | preindex (retry) |
| 28 | cam21 23 Patan Dethali Char Rasta (id 81) | 0 | 0 | 0 | - | 0 | H264 (B-frames) 1920x1080 | - | B-frame H.264, no frame in 60 s (upstream loss) (no frame in 60 s (no frame within the start budget)) | preindex (retry) |
| 29 | cam22 28 BK Mervada tran Rasta (id 82) | 0 | 0 | 0 | - | 0 | H265 1920x1080 | - | H.265, no frame in the budget (upstream loss) (no frame in 60 s (no frame within the budget)) | preindex (retry) |
| 30 | cam27 36 bilimora (id 87) | 0 | 0 | 0 | - | 0 | H264 (B-frames) 1280x960 | - | B-frame H.264, no frame in 60 s (upstream loss) (no frame in 60 s (no frame within the start budget)) | preindex (retry) |

generated 2026-09-05T13:15:21.417768Z, duration 433.4 s, params {"frames":30,"capture_s":45,"start_timeout_s":60,"group":5,"interval_s":1,"every":null,"keyframes_only":false,"frame_width":"source","production_width":960,"tile":768,"detector":"onnx","det_model":"yolo-v9-t-384-license-plates-end2end.onnx","det_conf":0.4,"survey_min_w":24,"production_min_plate_w":60,"ocr_min_w":40,"max_boxes":3,"ocr":"paddleocr","score_weights":{"plates_per_frame":30,"plate_width":20,"valid_reads":30,"confidence":10,"daylight":5,"decode":5}}

### 3.1 Headline findings

* **The 960 px production path finds almost nothing.** Across 25 cameras × up to 30 frames the
  worker's own view (`FRAME_WIDTH=960`, whole frame into the 384 px model) produced 27 boxes, 14 of
  them on cam14; the tiled full-resolution pass produced 43 plate boxes plus 45 overlay/sign boxes
  that the OSD filter removed. This is why `TiledDetector` was added (§6) - without it the live
  worker will post nothing on any sandbox camera.
* **Plates are small.** Mean detected width 29-87 px at the source (max 163 px on cam02 when a car
  passes under the camera); at 960 px that is 14-44 px, below the production `ANPR_MIN_PLATE_W=60`.
* **One valid read in the whole survey**: `GJ32X9785` (confidence 0.81, 84 px, cam07, IR, a parked
  two-wheeler); the district code matches Gir Somnath where the camera stands. Every other OCR
  result was a signboard, a phone number or a burnt-in camera label (`GUJARAT POLICE`,
  `75750 03008`, `DELIGHT P1 RLVD`, `Camera 01`, `0_PTZ2`). Night + 30-60 px plates + concealment
  artefacts are beyond PaddleOCR's general English models.
* **Decode health decides more than optics.** 5 cameras delivered no frame in 60 s (cam06 H.265,
  cam10/cam21/cam27 B-frame H.264, cam22 H.265), 6 more delivered ≤ 7 frames in 45 s; the earlier
  direct-pull test proved the loss is upstream (organiser side), not the relay. cam17/cam18/cam26
  (H.265) decode 25-30 frames but most are grey concealment frames.
* **Overlay text is a real false-positive source** for the worker too: the detector boxes burnt-in
  camera names with 0.9+ confidence and PaddleOCR reads them at 0.99. The survey's static-box /
  no-digit rule should move into the worker (`anpr/pipeline.py`) before the demo (next step).

## 4. Selection

Ranked by score, then corrected by looking at the contact sheets (top 12 and bottom 5 opened):
cam23, cam26 and cam18 owe their score to an overlay or a decode artefact and go to pre-index;
cam04 scores low in this run (4-22 usable frames) but is the busiest close-traffic junction of the
set with a 113 px plate in run 2, so it stays live. The live set on the CPU laptop is limited to
`ANPR_MAX_CAMERAS=8` and the worker takes the **lowest ids first**, so exactly these eight are
`anpr_enabled` (ids in brackets):

| Mode | Cameras | Why |
|---|---|---|
| **live** (laptop, 8) | cam01 (61), cam02 (62), cam04 (64), cam05 (65), cam07 (67), cam12 (72), cam14 (74), cam16 (76) | Ahmedabad junction PTZ/RLVD presets with cars passing 3-15 m from the camera (cam02, cam04, cam05, cam14, cam16), the Adalaj toll lane where vehicles stop (cam12), the only camera with a confirmed read (cam07), and the cleanest feed for a reliable demo tile and vehicle counts (cam01) |
| live on a GPU VM (+4 = 12) | + cam08 (68), cam13 (73), cam15 (75), cam30 (90) | next by exposure: Majevadi gate (0.5 plates/frame, glare), CN Vidhyalaya and Gandhidham junctions, the only daylight recording (Suvidha park RLVD) - set `ANPR_MAX_CAMERAS=12` and enable them with `PUT /api/cameras/{id} {"anpr_enabled": true}` |
| preindex (22) | every other sandbox camera, cam06/10/21/22/27 retried automatically | no vehicles (IR compounds, corridors, lanes), or no decodable frames; `GET /internal/anpr-config?mode=preindex` lists them with `mode: preindex`, the eight live ones as `both` |

Expected plate size for the live set: 30-60 px on cam01/cam05/cam12/cam14, 60-160 px on
cam02/cam04/cam16 when a vehicle is in the near lane, ~85 px on cam07. With `FRAME_WIDTH=1920`,
`ANPR_DET_TILE=768` and `ANPR_MIN_PLATE_W=40` all of them are inside the detector's range; at the
current `FRAME_WIDTH=960` only cam02/cam04/cam16 near-lane plates are.

## 5. Applied configuration

Applied at 19:18 IST on 5 Sept 2026 as `jury_admin` (scratchpad script `apply_anpr.mjs`, one
`PUT /api/cameras/{id} {"anpr_enabled": true|false}` per camera whose value differed):
`changed 8: cam01 cam02 cam04 cam05 cam07 cam12 cam14 cam16 = true`; the other 22 sandbox cameras
were already `false` and were left untouched. `GET /api/cameras?anpr_enabled=true` now returns
exactly ids 61, 62, 64, 65, 67, 72, 74, 76 (own gate `OWN-GATE-01` stays `anpr_enabled=false`, it
is a `record_enabled` feed only). Each PUT re-creates the camera's MediaMTX path (§5.4), which the
survey had finished using. `record_enabled` was not changed (the recording budget for eight 1080p
sandbox pulls is the deploy owner's call, see README §4.1). The RTSP URLs in every response stayed
masked (`user:***@`, credential scan of the response bodies: 0 hits).

**Observed effect with the current worker image** (`ANPR_DETECTOR=contour`, `FRAME_WIDTH=960`,
image built before this change): within 60 s the live worker picked up all eight cameras
(`GET /internal/anpr-config?mode=live` lists ids 61, 62, 64, 65, 67, 72, 74, 76; the pre-index list
shows them as `both`), CPU rose to 140-230 % and memory to 3.5 GB of the 4 GB cap (eight 1080p
decodes at 5 fps), and cam02 started posting the burnt-in clock at the top of its frame as reads
(`2314:16` → `231416`, `is_valid_format=false`, confidence 0.8-0.99, bbox `[293,0,220,42]`):
**808 invalid reads in 12 minutes** on cam02, 1 on cam05. Those rows are harmless to alerts (no
valid format, no watchlist match) but pollute the reads list and the dashboard counters. Fix =
rebuild `anpr-live` from this tree (`StaticBoxFilter` mutes that box after 20 frames,
`TiledDetector` finds real plates) with the §6 variables; until then either accept the noise or set
cam02 back to `anpr_enabled=false`.

## 6. Worker settings the selection assumes (deploy owner)

The live worker currently runs with `ANPR_DETECTOR=contour`, `FRAME_WIDTH=960`, `ANPR_FPS=5`,
`ANPR_MAX_CAMERAS=8`, `OBJECT_DETECT=0` (`deploy/.env`). On the sandbox footage that configuration
reads nothing: the contour detector looks for bright rectangles (synthetic plates) and the ONNX
detector at 960 px found 0-3 boxes per camera in 30 frames. For real reads the worker needs, in
`deploy/.env` (then `build anpr-live` / `up -d --no-deps anpr-live`):

| Variable | Laptop (CPU) | GPU VM | Why |
|---|---|---|---|
| `ANPR_DETECTOR` | `auto` | `auto` | ONNX first, contour only when ONNX finds nothing |
| `FRAME_WIDTH` | `1920` | `1920` | plates are 25-110 px at 1080p; 960 halves that |
| `ANPR_DET_TILE` | `768` | `768` | tiled detection (this change); 6 tiles + whole frame |
| `ANPR_FPS` | `1` (2 at most) | `5` | tiled ONNX costs ≈ 250-480 ms/frame on 2 CPU threads (measured while the survey ran), whole-frame 80 ms; 8 cameras × 1 fps ≈ 2-4 cores plus decode; PaddleOCR ≈ 340 ms per crop |
| `ANPR_MIN_PLATE_W` | `40` | `40` | measured plate widths; 60 would drop most of them |
| `ANPR_STATIC_BOX_WINDOW` | `20` (default) | `20` | new: mutes burnt-in clocks / camera names / signboards that the detector boxes every frame (see §5, cam02) |
| `ANPR_MAX_CAMERAS` | `8` | `12` | the eight chosen below; the worker takes the lowest ids first, so the cap must cover every enabled camera |
| `PREINDEX_MAX_CAMERAS` | `3` | `50` | on the laptop the pre-index worker only reaches the three lowest non-live ids; there is no per-worker `ANPR_CAMERAS` in the compose anchor |

**Applied on 5 Sept (stability pass, `deploy/.env`):** the laptop profile that actually runs is
`ANPR_DETECTOR=onnx`, `FRAME_WIDTH=1280` (3 tiled inferences per frame instead of 7 at 1920; plates
17-73 px at that width), `ANPR_DET_TILE=768`, `ANPR_MIN_PLATE_W=28` (= 40 px at 1920), `ANPR_FPS=2`,
`OBJECT_DETECT=0`, `PREINDEX_MAX_CAMERAS=2`, `PREINDEX_FPS=0.5`, `ANPR_LIVE_MEMORY=4g`, plus the
decoder rules for the real feeds (no pre-probe, 30 s RTSP timeout, 90 s first-frame budget, 60 s stall
watchdog, one dial at a time 3 s apart) and the OSD band / static-text filter with a cap of 10
invalid-format reads per camera per minute. The GPU VM column above stands (`FRAME_WIDTH=1920`,
`ANPR_FPS=5`, 12 cameras). Before/after figures: `docs/sandbox-progress.md`.

**Accuracy pass (5 Sept, evening, `deploy/.env`):** `FRAME_WIDTH=0` (decode at the source
resolution) with `ANPR_DETECT_WIDTH=1280` (the tiled detector still sees a 1280 px copy, OCR crops
keep native pixels), `ANPR_OCR=ensemble` with `ANPR_OCR_FAST_MODEL=cct_xs_v2_global` (PaddleOCR
det+rec on the enhanced crop + fast-plate-ocr; agreement wins), `ANPR_OCR_MIN_W=60`,
`ANPR_BEST_SHOT=1` (plates are tracked across frames and read once at their best crop), letters-only
OCR strings dropped, `ANPR_STATIC_BOX_HITS=0.5`, and `ANPR_EVIDENCE_DIR=/app/media/anpr_evidence`
(one crop + JSON line per detected vehicle; a track whose OCR only ever returns letters - the
`GUJARAT POLICE` sign on cam02, the `Bhavani` caption on cam07, the shop sign on cam04 - is a
`static text` track, counted apart and never stored as a vehicle). What each of these achieves on
the night footage, per backend and per camera, is measured in `docs/anpr-accuracy.md`; the short version is that cam07
(IR, vehicles stop on the forecourt) is the only camera whose plates are readable at night, while
cam02/cam04/cam16 mostly offer signboards and captions to the detector and cam01/cam05/cam12 plates
stay under 50 px.

## 7. Limits of this survey

* 30 frames over 45 s per camera, once. Traffic density on a looped night recording changes with the
  loop position; the score ranks *exposure* (vehicles with visible plates, plate size, decode health)
  more than OCR success, because OCR success was near zero everywhere at this plate size and light.
* The six cameras that never delivered a frame (see table) are the ones the earlier decode sweep
  found at 0-1 frames per 15 s: the sandbox drops hundreds of RTP packets per second on them. They
  are re-tried by the pre-index worker automatically and should be re-surveyed before the demo.
* PaddleOCR reads white-on-dark Indian plates poorly at night without the ×3 upscale helping; the
  reads that did come back were headlight glare and OSD text. A plate-specific recogniser or a
  daytime recording would change the OCR column, not the ranking.
* Re-run before the jury demo (10 min): `docker run --rm --network sentinel_default --cpus 6 -e
  INTERNAL_API_KEY=… -v $PWD/scripts:/app/scripts:ro -v $PWD/media:/app/media --entrypoint python
  sentinel-anpr:cpu /app/scripts/survey_anpr.py --api http://api:8000 --cameras 61-90` (add
  `-v $PWD/anpr:/app/anpr:ro` until the anpr image is rebuilt with `TiledDetector`).
