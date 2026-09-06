# ANPR accuracy on the organiser feeds (5 Sept 2026, CPU laptop)

Honest measurement of plate readability on the 30 organiser sandbox cameras after the accuracy
pass (OCR backends, best-shot tracking, native-resolution crops). Times are IST unless marked UTC;
the worker stores UTC. Everything below was measured on the night phase of the loops - see §1 -
so the numbers are a **night-time floor**, not the daytime figure the Phase 2 demo can expect.

## 1. Loop phase - what the cameras were showing

Every sandbox stream is a looped recording played at 1.0×. Read from the burnt-in OSD clock
through the relay (`media/loopcheck/`, `docs/sandbox-progress.md`):

| Wall time (IST, 5 Sept) | Footage clock (cam01, group A) |
|---|---|
| 15:50:15 | 14-06-2026 08:08:21 (daylight) |
| 19:34:37 | 13-06-2026 23:50:39 |
| 19:58 | 14-06-2026 00:14:34 |
| 21:01 | 14-06-2026 01:17:13 (lower half of the frame a concealment smear - the usual packet loss) |
| 21:33 | 14-06-2026 01:49:30 (light traffic: two cars, two rickshaws, one two-wheeler in view) |

Period **12 h 02 min 08 s** (± 30 s); playback 1.0× (63 wall minutes = 62.7 footage minutes). Footage sunrise (~06:00 on 14 June) reaches the screen at
01:44 IST 6 Sept, 13:46 IST 6 Sept, 01:48 IST 7 Sept, 13:50 IST 7 Sept (± 2 min); daylight lasts
until the loop restart (05:54 or 06:47 IST / 17:56 or 18:49 IST depending on whether the loop ends
at footage 10:10 or 11:03 - pin it from the next `media/loopcheck/` frames). Other camera groups
have their own phase (cam10 ran 11 min ahead of cam05; cam29 shows a 14-05-2026 clock). The crop
bank below was collected 20:10-21:16 IST = **footage 00:26-01:32 on 14 June (night, light
traffic)**; the live windows were measured at footage ≈ 01:06-01:26 (window 1) and ≈ 02:06-02:26
(window 3). Repeat §3 and §5 in the 13:46-17:56 IST daylight window on 6 Sept before quoting a
daytime figure.

## 2. What changed in the worker (all measured below)

| Change | Where | Why |
|---|---|---|
| Native decode + downscaled detection | `FRAME_WIDTH=0`, `ANPR_DETECT_WIDTH=1280` (`anpr/decode.py`, `pipeline._detect`) | OCR crops keep the 1080p pixels (a 60 px plate stays 60 px instead of 40) while the tiled ONNX detector still costs three 384 px inferences per frame |
| Best-shot tracking | `anpr/tracks.py` `PlateTracker`, `ANPR_BEST_SHOT=1`, `ANPR_TRACK_*` | a vehicle approaching from 30 px to 150 px is read at its largest/sharpest crop, once, instead of on every frame; the 3 best shots of a track are OCR'd and voted (3 s char-wise vote unchanged) |
| OCR floor | `ANPR_OCR_MIN_W=60` (decoded px) | below 60 px no OCR model reads Indian plates on this footage; narrower boxes are still tracked as detected vehicles |
| Detected-vehicle evidence | `anpr/evidence.py`, `ANPR_EVIDENCE_DIR=/app/media/anpr_evidence` (bind mount) | the output report needs "detected vehicles *or* plates with timestamps": every ended track is logged and stored with its best crop + UTC timestamps even when no plate could be read |
| OCR backends | `anpr/ocr.py`, `ANPR_OCR=paddle\|fast_plate\|ensemble`, `ANPR_OCR_FAST_MODEL`, `ANPR_OCR_PADDLE_MODE` | compared on the labelled bank in §3 |
| Crop enhancement for PaddleOCR | `enhance_crop`: 6 px replicate border, ×4 cubic upscale (cap 640 px), gamma when dark, conservative inversion for white-on-black, CLAHE | §3 shows which parts help |
| Static-text tracks are not vehicles | `tracks.track_kind` (`plate` \| `vehicle` \| `text`), heartbeat `extra.vehicles[camera].text`, `kind` in `vehicles.jsonl` | a detector box whose OCR only ever returns letters is a caption or a signboard (§3.2: 48 of the 71 widest bank crops); it is counted apart and gets no evidence file, so the detected-vehicle figures in §4.3 exclude them |

## 3. Crop bank and OCR backend comparison

### 3.1 Bank

`scripts/collect_crops.py` decoded the 8 live cameras (ids 61, 62, 64, 65, 67, 72, 74, 76 =
cam01/02/04/05/07/12/14/16) at the source resolution through the relay, ran the tiled detector,
the OSD-band / static-box filters and the tracker, and saved the **best crop of every ended track**
(JPEG 95, no resize) with a manifest (`media/anpr_crops/manifest.csv|jsonl`: camera, UTC time,
stream pts, bbox, width, brightness, contrast, sharpness, frames in track). Two runs, one after the
other, while the live worker kept running:

| Run | Wall time (IST) | Footage clock | Detector input | Result |
|---|---|---|---|---|
| 1 | 20:10-20:20 | 00:26-00:36 | 1280 px copy, 768 px tiles, conf 0.4, 2 fps | 16 crops, 8 ≥ 60 px (cam02 3, cam07 3, cam14 2) |
| 2 | 20:21-20:46 | 00:37-01:02 | native 1920 px, 768 px tiles (7 inferences ≈ 1.3-1.6 s/frame on 4 threads next to the live worker), conf 0.35, 1 fps | 105 crops from 140 boxes / 933 frames, 55 ≥ 60 px: cam07 33 (30 ≥ 60), cam02 18 (12), cam04 13 (10), cam16 9 (7), cam01 12 (0, max 44 px), cam05 13 (0, max 50 px), cam14 6 (1), cam12 1 |

Total: **121 crops, 63 ≥ 60 px, 42 ≥ 100 px** (`media/anpr_crops/summary.json` = run 2,
`manifest.csv` = both runs). This is far below the 300 crops the task asked for: at footage
00:30-01:00 the junctions carry a vehicle with a visible plate every 20-60 s per camera, cam05 and
cam12 offer almost nothing, cam01's plates never exceed 48 px (and are blown out by the camera's
exposure), and the detector's false positives (signboards, burnt-in captions, a traffic-signal head,
rooftops, glare) were kept in the bank on purpose so the labelling reflects what the worker really
sees. A daytime collection in the 13:46-17:56 IST window is the way to a 300+ bank (same script,
same command).

### 3.2 Labels

`scripts/label_sheets.py` renders the widest crops ×4 with an enhanced twin; the 72 widest (≥ 56 px)
were opened and read by hand (`media/anpr_crops/labels.csv`, columns `file,plate,note`). **Only
plates that could actually be read were labelled**; blurred, blown-out, cut-off or non-plate crops
were left out (their count is reported, not guessed). Result of reading the 71 widest crops
(56-188 px):

| Class | Count | What |
|---|---|---|
| **Readable plates (labelled)** | **2** | `GJ36AJ2890` (cam07, 94 px, every glyph certain); `GJ32D3783` (cam07, 92 px; 5th glyph D or 0 — D by the Indian format and the district, the other 8 glyphs certain) |
| Partial plate | 1 | cam07 118 px: `GJ 7DZ…` then cut off — not labelled |
| **Not a plate** (labelled as negatives) | **48** | cam04 shop signboard (Gujarati "Udipi Cafe") ×10, cam02 `GUJARAT POLICE` signboard ×11, cam07 burnt-in caption `Bhavani` ×19 and `LALPARI` shop sign ×1, cam16 OSD date `2026-` ×1 and caption `Junction` ×4, cam02 traffic-signal head ×2 |
| Unreadable | 20 | dark IR blocks, blown-out headlight glare, motion blur, rooftops, decode artefacts |

So the bank yields **2 labelled plates** instead of the 60 the task aimed for, and a **48-crop
negative set**. The negatives are the more important finding: at native resolution the detector
boxes static text far more often than plates on this footage, and the worker's static-box rule
(80 % presence over 20 frames) let them through because the detector finds them only
intermittently. Two fixes went into the worker as a result: an OCR string without a digit is never
voted (a registration always has digits) and `ANPR_STATIC_BOX_HITS` (0.5 on the organiser feeds)
lowers the presence share a box needs to count as static.

### 3.3 Backends on the labelled crops (`scripts/eval_ocr_bank.py`, `media/anpr_crops/eval.json`)

Exact = normalised OCR string equals the label; char = 1 − Levenshtein/len(label); valid = share
of reads that are valid Indian formats (a valid-looking *wrong* plate counts, so this column alone
is not accuracy); bank valid = valid-format reads over **all** crops ≥ 60 px of the bank
(unlabelled ones included).

Run at 21:00-21:08 IST inside the anpr image (3 threads, next to the live worker, so ms/call are
upper bounds; the first Paddle call includes warm-up).

| Backend (`ANPR_OCR` / variant) | Exact (of 2) | Char acc. | Valid-format reads | Mean conf | ms/call | Negatives (48): false valid | Negatives: letters-only reads | Bank ≥ 60 px (69): valid / reads |
|---|---|---|---|---|---|---|---|---|
| `paddle_legacy` — pre-5-Sept ×3 CLAHE, det+rec | 1 | 0.889 | 1 | 0.81 | 165 | 0 | 43 | 1 / 65 |
| **`paddle`** — enhanced crop, det+rec (production `paddle`) | 1 | 0.889 | 1 | 0.85 | 270-460 | 0 | 41 | 1 / 66 |
| `paddle_auto` — enhanced, recognition only on single-line crops | 0 | 0.05 | 0 | 0.33 | 97 | 0 | 39 | 0 / 64 |
| `paddle_noinv` — enhanced without inversion | 1 | 0.889 | 1 | 0.85 | 271 | 0 | 41 | 1 / 66 |
| `paddle_plain` — ×4 + CLAHE only (no gamma, no inversion) | 1 | 0.889 | 1 | 0.81 | 261 | 0 | 41 | 1 / 66 |
| `fast_plate` — global_mobile_vit_v2 (raw crop) | 0 | 0.533 | 0 | 0.74 | 12 | 0 | 0 | 0 / 69 |
| `fast_plate_enh` — vit_v2 on the enhanced crop | 0 | 0.533 | 0 | 0.71 | 14 | 0 | 0 | 0 / 69 |
| `fast_plate_cct` — cct_s_v2_global | 0 (`GJ36AJ289`, last digit lost) | 0.728 | 0 | 0.72 | 71 | 0 | 0 | 0 / 48 |
| **`fast_plate_cct_xs`** — cct_xs_v2_global | 1 | 0.944 | 2 | 0.90 | 30 | 0 | 0 | 2 / 45 |
| `ensemble` — paddle + vit_v2 | 1 | 0.833 | 1 | 0.90 | 348 | 0 | 36 | 1 / 53 |
| `ensemble_cct` — paddle + cct_s | 1 | 0.889 | 1 | 0.85 | 164 | 0 | 36 | 1 / 50 |
| **`ensemble_cct_xs`** — paddle + cct_xs (**production**) | 1 (conf 1.0, both agree) | 0.944 | 2 | 0.95 | 191 | 0 | 36 | 2 / 51 |

Per-crop detail (`eval.json`, `per_crop` / `per_negative`): on `GJ32D3783` PaddleOCR returns
`G.3203783` (drops the `J`, 8 characters, invalid format) in every variant; cct_xs returns
`GJ32B3783` (valid; the disputed 5th glyph read as `B`); vit_v2 returns `G3328783`.

Reading of the table:

* **Two labelled plates are not a benchmark**; they are the whole readable yield of an hour of
  night footage from eight cameras. What the table does establish: (1) PaddleOCR must run its
  detection stage on the crop - recognition on the padded crop fails on both plates (`COGAR`), so
  `ANPR_OCR_PADDLE_MODE=det` is the default and the "rec-only mode" idea from the task is
  rejected by the data; (2) the extra preprocessing (gamma, inversion) neither helps nor hurts
  PaddleOCR on these two crops - the ×4 cubic upscale and CLAHE were already in the legacy path;
  the conservative inversion threshold matters because the first heuristic inverted a normal
  plate and broke the read (`FORGARED`); (3) of the fast-plate-ocr models only **cct_xs_v2** reads
  Indian plates here, at 12-30 ms - 10× cheaper than PaddleOCR - while the vit_v2 model named
  in the task reads neither; (4) **no backend produced a false valid plate on the 48 signboard /
  caption crops**, but PaddleOCR happily returns their letters (`GUJARAT POLICE`, `Bhavani`,
  `Junction`) at 0.9+, which is exactly the invalid-read noise the survey saw - hence the digit
  rule in the worker; the fast-plate models output digits for everything and are useless as
  text detectors, which is fine because they only ever see detector boxes.

**Chosen backend: `ANPR_OCR=ensemble` with `ANPR_OCR_FAST_MODEL=cct_xs_v2_global`** (PaddleOCR
det+rec on the enhanced crop + fast-plate cct_xs; agreement wins, else valid-format, else the
higher confidence ≥ 0.45). On this bank it is the best row on every column that matters (both
plates in valid format, agreement confidence 1.0 on the certain plate, 0 false valid) and it keeps
PaddleOCR's two-line handling for the square plates the fast models cannot parse. The cost
(≈ 200-450 ms per call on this CPU) is affordable because best-shot tracking limits OCR to a few
calls per vehicle. `fast_plate` + `cct_xs_v2_global` alone is the lean alternative for a CPU host
that must cover more cameras (same reads here at 30 ms, no two-line support).

## 4. Live run on the running stack

Both runs: the compose stack on the laptop (8 live cameras, `deploy/.env` profile of §2,
`ANPR_FPS=2`, `ANPR_LIVE_CPUS=8`), reads taken from `GET /api/detections?mode=live` for cameras
61-76 only (the pre-index worker's synthetic own-gate loop, camera 60, is excluded - it produced
67 reads of its drawn plates in the same window and must not be mistaken for sandbox reads),
vehicles/timings from the worker log, resources from `docker stats` once a minute.

### 4.1 Window 1 - 20:50-21:10 IST (15:20-15:40 UTC), footage ≈ 01:06-01:26

Image `68687d6cce35` (ensemble + cct_xs, native decode, best-shot; **without** the digit rule and
with the 80 % static-box rule), started 20:43 IST. The worker log of this window was lost when
the container was replaced at 21:11 for window 2, so only API figures and resource samples exist:

| Metric | Value |
|---|---|
| Reads posted (live cameras) | **10** (cam04 7, cam07 2, cam14 1) |
| Valid-format reads | **2** (20 %), both cam07: `GJ14AA3978` conf 0.83, `SI9E5995` conf 0.60 |
| Unique valid plates | 2 |
| Invalid strings | 8: cam04 `0G94751`, `0G94151`, `OSUL SI`, `OG941I`, `OGSLUL SI`, `OESLUL SI` (a blown-out near-lane plate read six times at 0.38-0.51), cam14 `11A1450` (0.38) |
| Mean confidence | 0.48 all, 0.72 valid |
| Worker resources | 1.5-1.9 GB RSS, 215-380 % CPU (8-CPU cap); VM MemAvailable 1.6-4.1 GB, SwapFree 1.42-1.48 GB (the crop-bank evaluation container ran alongside for 8 of the 20 minutes) |

Spot check (both valid crops opened): `GJ14AA3978` is a legible plate and the read matches;
`SI9E5995` is a blurred smear - the string passed the Indian format by accident (`SI` is not a
state code), so window 1 is honestly **1 correct plate, 1 false valid read, 8 invalid strings**.

### 4.2 Window 2 - 21:16-21:36 IST (15:46-16:06 UTC), footage ≈ 01:32-01:52 - **void (organiser outage)**

Image `5e03279977b9` (adds the digit rule and `ANPR_STATIC_BOX_HITS=0.5`), started 21:11:09 IST;
all 8 cameras handshaked within 2 minutes. Three minutes into the window the organiser server went
away: MediaMTX logged `dial tcp 103.250.160.189:8554: connect: connection refused` on every relay
path from **15:49:11 to 15:57:25 UTC (21:19-21:27 IST, 245 refusals)**. The worker behaved as
designed - every camera sat in the 2 → 30 s backoff, 0 decoder kills, 0 API errors - but the
window has no data: the frame counter stood at 1935 from 15:49:28 to 15:57:28 UTC, the API holds
**0 reads** for 15:46-16:06 UTC, and the evidence store has 15 vehicle files (4 ≥ 60 px) for it.
When the feeds returned, the eight simultaneous reconnects stalled the whole Docker VM for four
minutes (API requests 1.3-1.9 s, its scheduler jobs missed by 4-14 s; the worker's main loop showed
gaps of 35 / 155 / 83 s between its 30 s stats lines at 15:57:59-16:02:32 UTC, and one PaddleOCR
call inside the stall took ≈ 121 s, which is why later stats lines print a cumulative
`ocr=… 11255 ms/call` - `scripts/measure_live_anpr.mjs` now reports the per-window delta instead).
The outage is the "network cut" that ended the first attempt at this stage. Nothing in the pipeline
was changed because of it; the reconnect-storm stall is a reliability item (§6, item 6).

### 4.3 Window 3 - 21:50-22:10 IST (16:20-16:40 UTC), footage ≈ 02:06-02:26 - **final image**

Image `a06ae5d4232f` (window-2 image + `tracks.track_kind`: a track whose OCR only ever returned
letters is a `static text` track, counted in `extra.vehicles[camera].text` and stored nowhere, so
the detected-vehicle counts exclude captions and signboards; unit tests 98 passed), both workers
restarted 16:15:07 UTC, cameras dialled one at a time (cam01 handshake 16:15:29, cam16 16:15:48,
cam02 16:15:54, cam12 16:16:12, cam14 16:16:28, cam04 16:16:46, cam05 / cam07 after) - all 8
running before the window opened. Measured with `scripts/measure_live_anpr.mjs` (API reads for the
8 live cameras, worker log deltas, evidence store, 30 s `docker stats` + VM memory samples).

| Metric | Value |
|---|---|
| Reads posted (live cameras) | **11** (cam07 6, cam04 5); worker log: 12 reads logged, 4 642 frames in 20 min = **3.96 frames/s** over 8 cameras |
| Valid-format reads | **4** (36 %): cam07 `GJ10F0032` 0.88, `GJ1ZAA5453` 0.71, `GI27EW9901` 0.60; cam04 `OG9I7515` 0.35 |
| Correct by eye (§5) | **1 of 4** — `GJ10F0032` matches the crop; `GJ1ZAA5453` is `GJ32AA5453` (3→1, 2→Z), `GI27EW9901` starts `GJ`, `OG9I7515` is the cam04 shop sign; so window 3 is honestly 1 correct plate, 3 false valid reads, 7 invalid strings |
| Invalid strings | 7: cam07 `F0366C07314`, `107130`, `GJ187T30`; cam04 `OG9475`, `069144`, `O5991EE`, `0G4111` (the blown-out near-lane plate and the Udipi Cafe signboard, as in window 1) |
| Mean confidence | 0.55 all, 0.64 valid |
| Detected vehicles (evidence store, best shot in the window) | **50** tracks / 50 evidence files, 33 at ≥ 60 px, 4 with a valid plate; per camera cam02 14, cam07 10-11, cam04 8-9, cam01 6, cam12 4, cam16 3, cam05 2, cam14 2; 3 static-text tracks (all cam04 signboard) excluded |
| Pipeline cost | detect **218 ms/frame** (3 tiled ONNX inferences at 1280 px), OCR ensemble **474 ms/call** × 49 calls (≈ 2.5 calls/min, best-shot tracking working as intended) |
| Stability | 0 decoder kills, 1 decoder error, 9 reconnects (the one-at-a-time start-up dials after the 16:15 restart plus one cam07 re-dial), 0 API timeouts, 0 exceptions, 0 main-loop gaps > 45 s; all 8 cameras `running` at the end |
| Per-camera fps at the end | cam01 1.94 · cam02 1.54 · cam04 0.50 · cam05 1.36 · cam07 1.42 · cam12 1.24 · cam14 0.90 · cam16 0.23 (target 2; cam04/cam16 are the lossy feeds, decoder-limited) |
| Traffic seen (plates ≥ 60 px per camera in 20 min) | cam04 9, cam02 8, cam07 7, cam16 3, cam12 2, cam14 2, cam01 1, cam05 1 — night footage ≈ 02:06-02:26 |

Files: `media/anpr_measure/w3/summary.json` (+ the 4 valid and 7 invalid crops it downloaded), the
per-camera table above is `worker.per_camera` of that file.

## 5. Spot check of live crops

Twenty reads on the organiser cameras from 20:31-22:12 IST were opened one by one (the crops
served by `/media/crops/...`) and labelled through `POST /api/qa/labels` as `jury_admin`, so the
figures are reproducible from `GET /api/reports/quality?source=sandbox` for that window (the
labels are audited as `qa.label`). Labelling rule: the true plate as a person reads it; an empty
label means *not a readable plate* (a smear, a signboard, a caption).

| Read | Camera | OCR string | Conf | By eye | Verdict |
|---|---|---|---|---|---|
| 18502 | cam07 | `GJ14AA3978` | 0.83 | GJ 14 AA 3978 | **exact** |
| 18608 | cam07 | `GJ32K0225` | 0.70 | GJ 32 K 0225 | **exact** |
| 18623 / 18624 | cam07 | `GJ32AG2319` ×2 | 0.91 / 0.90 | GJ 32 AG 2319 | **exact** (same vehicle, two tracks 2 s apart) |
| 18625 | cam07 | `GJ10OG9093` | 0.90 | GJ 10 OG 9093 (O/D ambiguous) | **exact** |
| 18727 | cam07 | `GJ10F0032` | 0.88 | GJ 10 F 0032 | **exact** |
| 18737 | cam07 | `GJ32AA4959` | 0.88 | GJ 32 AA 4959 | **exact** |
| 18376 | cam07 | `GJ3B7160` | 0.84 | GJ 38 ? 7160 (series letter unclear) | 1-2 character errors |
| 18701 | cam07 | `GJ1ZAA5453` | 0.71 | GJ 32 AA 5453 | 2 character errors (3→1, 2→Z) |
| 18655 | cam07 | `GI27EW9901` | 0.60 | GJ 27 EW 9901 (rest uncertain) | ≥ 1 error (I→J); the format check let `GI` through |
| 18322 | cam07 | `FGJ32AG5511` (invalid) | 0.64 | GJ 32 AG 5511 | readable plate, one spurious leading `F` |
| 18513 | cam07 | `SI9E5995` | 0.60 | smear | false valid |
| 18338, 18668, 18721 | cam07 | `603209833`, `107130`, `F0366C07314` (invalid) | 0.54-0.69 | unreadable at 84-96 px | invalid, correctly not plates |
| 18697 | cam04 | `OG9I7515` | 0.35 | Udipi Cafe signboard | false valid (`OG` is not a state code either) |
| 18249, 18261, 18490, 18695 | cam04 | `0SLS15`, `ESLULSI`, `0G94151`, `O5991EE` (invalid) | 0.34-0.51 | the same signboard / blown-out plate | invalid, correctly not plates |

Result over the 20 labelled reads: **7 exact (35 %)**, character accuracy ≈ 58 % — but on the
**valid-format** reads alone it is 7 exact of 12 (58 %), and on cam07 (the only camera whose
night plates are legible) 7 exact of 16 labelled. Every false *valid* read (`SI9E5995`,
`GI27EW9901`, `OG9I7515`) passes the syntactic Indian-format check with a non-existent state
code; a state-code table in `plates.normalise` would have rejected all three (roadmap, §6).
`GET /api/reports/quality?from=…&to=…&source=sandbox` reproduces the figure for any window.

## 6. Why the numbers are what they are (evidence)

The night-time read rate on these feeds is low, and the evidence says it is the footage, not a
tunable:

1. **Night.** Every measurement here was taken at footage 00:26-01:52 (§1). Twenty-four cameras are
   street-lit colour scenes in which the retro-reflective plate is the brightest object in the
   frame: on cam01/cam05 every plate crop is a blown-out white blob (`label_sheets/sheet_9`,
   brightness 150-240, no strokes left to read); on cam04 the near-lane plates are blown out too
   (window 1: one vehicle read six times as `0G94751`/`OSUL SI`). The only readable plates came
   from cam07, an IR camera looking at vehicles that *stop* on a showroom forecourt.
2. **Plate size.** Wide-angle junction presets put plates at 25-50 px on cam01/cam05/cam12/cam14
   (max 44/50/77/104 px in the bank) - under the 60 px floor below which no backend read anything
   (`eval.json`: 0 valid reads on the 26 bank crops between 56 and 92 px other than the two cam07
   plates). Native-resolution decode (`FRAME_WIDTH=0`) keeps every pixel there is; it cannot
   create the ones the lens did not deliver.
3. **Packet loss.** cam04/cam07/cam16 deliver 0.4-0.7 fps of intact frames (decoder `fps` in
   `summary.json`, concealment smears like the one in the 21:01 loop-check frame); a vehicle
   crossing the near lane in two seconds is seen in one frame or none, so most tracks in the bank
   have `track_frames=1` and best-shot selection has nothing to choose from on those cameras.
4. **What the detector offers at night is mostly text that is not a plate.** 48 of the 71 widest
   boxes were signboards and burnt-in captions (§3.2). They cost OCR time and, before the digit
   rule, produced invalid reads; they never produced a false valid plate with any backend.
5. **Traffic.** At footage 00:30-02:30 the junctions carry a vehicle with a visible plate every
   20-60 s per camera (bank: 121 tracks in 35 min across 8 cameras).
6. **The organiser server itself goes away.** On 5 Sept it refused every connection for 8 minutes
   (15:49-15:57 UTC, §4.2). The worker survives it (backoff, no kills), but the eight reconnects
   that follow start eight 1080p `ffmpeg` decoders at once and stall the 8 GB Docker VM for
   minutes (API requests 1.3-1.9 s, worker main loop paused 35-155 s, one OCR call ≈ 121 s).
   Reliability follow-up, not an accuracy tunable: space reconnects through the `DialGate` with a
   longer spacing after an outage, or cap the number of decoders restarting per minute.

What would change the figure: the daylight window (13:46-17:56 IST on 6 Sept: plates lit by the
sun instead of blown out by headlights, traffic ×5-10), which is why every accuracy claim for the
demo must be re-measured there with exactly these scripts; a GPU host (`ANPR_DETECT_WIDTH=1920`,
7 tiled inferences, 5 fps) so the 40-60 px plates are detected and tracked over several frames;
and, structurally, camera presets pointed at a stop line instead of a whole junction.

## 7. How to reproduce

```bash
# crop bank (native resolution, 25 min, 8 live cameras; runs next to the live worker)
MSYS_NO_PATHCONV=1 docker run --rm --network sentinel_default --cpus 4 --memory 1500m \
  -e INTERNAL_API_KEY="$(grep ^INTERNAL_API_KEY= deploy/.env | cut -d= -f2-)" \
  -v $PWD/anpr:/app/anpr:ro -v $PWD/scripts:/app/scripts:ro -v $PWD/media:/app/media \
  --entrypoint python sentinel-anpr:cpu /app/scripts/collect_crops.py --api http://api:8000 \
  --duration-s 1500 --fps 1 --threads 4 --detect-width 0 --det-conf 0.35 --gap-s 3
# labelling sheets, then fill media/anpr_crops/labels.csv by hand
... /app/scripts/label_sheets.py --bank /app/media/anpr_crops --top 72 --per-sheet 8 --min-w 56
# backend comparison
... /app/scripts/eval_ocr_bank.py --bank /app/media/anpr_crops --threads 4
# resource sampler during the window (docker stats + VM MemAvailable/SwapFree every 30 s)
while true; do echo "== $(date -u +%H:%M:%S)"; docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' | grep sentinel; \
  docker run --rm alpine sh -c 'grep -E "MemAvailable|SwapFree" /proc/meminfo; grep -E "pswpin|pswpout" /proc/vmstat'; sleep 30; done > stats.txt
# live measurement (API reads of the 8 live cameras, worker-log deltas, evidence store, resources, random valid +
# invalid crops for the by-eye check), from the repo root with Node 24; SENTINEL_USER / SENTINEL_PASSWORD override the login
node scripts/measure_live_anpr.mjs 2026-09-05T16:20:00Z 2026-09-05T16:40:00Z media/anpr_measure/w3 20 \
  --footage "14-06-2026 02:06-02:26 (cam01 clock)" --stats stats.txt
```
