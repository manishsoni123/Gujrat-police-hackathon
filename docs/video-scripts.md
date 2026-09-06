# Sentinel Gujarat — demonstration video scripts

Two videos are mandatory (guide §7.3): **Video 1** on our own feed (≤ 3 minutes, screen recording) and **Video 2** on the government sandbox feed with the **output report**. Both must show real working software — no mock-ups, animations or concept footage. Both are recorded from the **hosted URL** (not localhost) at version `1.0.0-phase1`, uploaded unlisted to YouTube and copied to the Drive folder.

Naming: `Sentinel-Gujarat_Video1_Own-Feed_v1.0-phase1.mp4`, `Sentinel-Gujarat_Video2_Government-Feed_v1.0-phase1.mp4`.

---

## 0. Pre-recording checklist (run 30 minutes before recording)

| # | Check | How |
|---|---|---|
| 1 | Hosted stack healthy | `curl -s https://<domain>/healthz` → `status: ok`, `db: ok`, `mediamtx: ok`, `anpr_workers ≥ 1`; `/health` page shows the live worker not stale |
| 2 | Video 2 import segment shows `added=50` | The import segment (0:08–0:35) is recorded **at the first real import on the fresh hosted database** (Day 1/2, right after `deploy.sh`), from the hosted URL at the final version tag; the remaining segments are recorded on Day 3 after the pre-index soak so routes span several cameras. If a single-take recording is required instead: restore the "pre-import" VM snapshot, record the import, then wait ≥ 15 min for live reads before the alert/route segments (routes will rely on live reads only). Either way, note the method in `README.md` |
| 3 | Own feed live | `own_gate` path plays on `/wall`; `media/own/own_gate.mp4` is the real phone recording (not the synthetic fallback) |
| 4 | Plates for the demo known | Video 1: a plate visible in `own_gate.mp4` at ≈ 20–40 s that is **not** in the watchlist — this is **plate 1** below; it is chosen from the phone recording on the day (`media/own/own_gate.mp4` does not exist yet, so it cannot be fixed in advance) and written on the run sheet before recording; Video 2: `GJ 27 XY 3456` (inactive seed row) for the live add; `GJ 01 AB 1234` (stolen, critical) for the seeded alert; both verified in `/vehicles` search beforehand |
| 5 | Alert sound audible in the recording | OBS desktop-audio source enabled; browser sound toggle on; test with one alert |
| 6 | Desktop notifications granted | Chrome site permission "Allow"; test button on `/about` |
| 7 | Browser | Chrome, 1920×1080 window, zoom 100 %, bookmarks bar hidden, no extensions, incognito-like profile with only Sentinel Gujarat logged in; second tab prepared with `/api/docs` |
| 8 | Wall layout saved | 9-grid with mock cameras 1–8 + own gate, saved via `PUT /me/wall-layout` (just arrange it and it persists) |
| 9 | Reports folder | Download directory empty; PDF viewer set to Chrome |
| 10 | Timing | Stopwatch; Video 1 hard limit 3:00; rehearse both once end-to-end |
| 11 | Network | Wired or strong Wi-Fi; WebRTC verified (tiles show "WebRTC" caption); if HLS fallback occurs it is fine but say so |
| 12 | Backup | Fallback clips (`fallback_<id>` paths) enabled if the sandbox is down; note in README how the video was produced |

## OBS Studio settings

| Setting | Value |
|---|---|
| Canvas / output resolution | 1920×1080, 30 fps |
| Recording format | MKV (auto-remux to MP4 after recording: File → Remux Recordings) |
| Encoder | x264 (or NVENC H.264 on a GPU laptop), rate control CBR 8,000 kbps, keyframe interval 2 s, preset `veryfast`, profile `high` |
| Audio | 48 kHz, 160 kbps AAC; mic track 1 + desktop audio track 1 (single mixed track); filters on mic: Noise Suppression (RNNoise), Noise Gate, Compressor; desktop audio at −10 dB so the alert sound is audible but not loud |
| Sources | Window Capture (Chrome) with cursor shown; no webcam overlay; optional small "Sentinel Gujarat · v1.0.0-phase1 · <date IST>" text source bottom-left |
| Hotkeys | Start/stop recording F9; pause F10 (use pause instead of cutting when a page loads slowly) |
| Post | Trim head/tail in OBS remux or a free editor; no speed-ups; if a wait exceeds 5 s (e.g. next loop pass), cut it and say "a few seconds later" in the voice-over |

---

## 1. Video 1 — own feed (target 2:45, hard limit 3:00)

Story: onboard our private society gate camera by RTSP, watch it live, see plates read, put one plate on the watchlist, receive the alert, acknowledge, search, route, export.

| Time | Screen / action | Voice-over |
|---|---|---|
| 0:00–0:10 | Login page (`jury_admin`). Title card overlay: "Sentinel Gujarat — Video 1: own feed". Log in | "This is Sentinel Gujarat, Dynatech Consultancy's submission for the Gujarat Police CCTV Integration Hackathon. Video one shows our own feed: a private society gate camera, onboarded and analysed end-to-end. Everything you see is live software at the hosted URL." |
| 0:10–0:30 | `/cameras` → **Add camera**. Fill: name "Dynatech Office Gate (private society camera)", department Police, type IP, ownership **Private**, click the map near Satellite, Ahmedabad; RTSP URL `rtsp://mediamtx:8554/own_gate`; codec H264; ANPR enabled; Record enabled → Save. Drawer shows the camera; map marker appears | "Onboarding a private camera takes one form: name, department, ownership set to private, a click on the map for the location, and its RTSP URL with a read-only credential. Sentinel creates the relay path, and within seconds the camera is on the map and picked up by the ANPR worker." |
| 0:30–0:55 | Open the camera page. Player shows "WebRTC" caption; live reads list fills on the right with plates, confidence and IST time; object counts tick | "The camera page plays over WebRTC with HLS fallback. On the right, plates are read live: every read shows the plate, the OCR confidence and the IST timestamp, with the crop. Below, vehicle and person counts per minute from the object detector." |
| 0:55–1:25 | `/watchlist` → **Add**: plate **plate 1** (from the run sheet, pre-flight check 4), entity vehicle, reason **stolen**, priority medium (priority will be forced to critical by the reason floor). Save. Switch to `/alerts`. Within the next pass of the vehicle: toast + sound + desktop notification; alert row critical, exact, latency shown | "Now I add that plate to the watchlist as a stolen vehicle. Note the priority: 'stolen' has a critical floor, so the alert will be critical regardless of what I typed. On its next pass the vehicle is read again — and here is the alert: toast, sound, desktop notification, with the crop, camera, location and the read-to-alert latency in milliseconds." |
| 1:25–1:45 | Open the alert drawer: crop, camera card, mini-map; click **Play recording** → 10 s of recording before the read; **Acknowledge** with note "Unit dispatched" | "From the alert I can play the recording from ten seconds before the read, and acknowledge with a note. Every step is written to the audit log." |
| 1:45–2:10 | `/vehicles` → search the same plate → exact hits → **Build route** → timeline table, numbered markers, crops. (Own feed has one camera; say so) | "Vehicle search normalises whatever is typed — spaces, hyphens, common OCR confusions — and returns exact and possible sightings with crops. Build route orders them in time on the map. With a single own camera this is one stop; video two shows a multi-camera route on the government feed." |
| 2:10–2:30 | `/reports` → Output report, last 1 h, CSV and PDF → open the PDF: IST timestamps, camera id, plate, confidence, SHA-256 column, footer hash and watermark | "The output report exports the detections as CSV and PDF with IST timestamps, camera IDs, confidence and a SHA-256 for every crop. The footer carries the evidence hash and a watermark with the exporting user." |
| 2:30–2:45 | `/dashboard`: tiles and charts; end card with URL, repo, version | "Dashboards summarise reads, alerts and object counts per camera. Sentinel Gujarat, version 1.0.0-phase1, Model 1 plus Model 2 with a roadmap to Models 3 and 4. Thank you." |

Cut rules: if the vehicle's next pass takes longer than 15 s, pause the recording (F10) and resume when the alert fires; total must remain under 3:00.

---

## 2. Video 2 — government feed + output report (target 3:45, limit ≤ 5:00 unless the organiser states otherwise)

Story: import the sandbox catalogue, show the cameras on the map with health, the wall, live reads, a watchlist alert on a sandbox-seen plate, a multi-camera route with PDF, the **output report**, recording playback, and the Model 1 pages.

| Time | Screen / action | Voice-over |
|---|---|---|
| 0:00–0:08 | Login as `jury_admin`. Title card: "Sentinel Gujarat — Video 2: government sandbox feed and output report" | "Video two runs on the organiser's sandbox: about fifty government cameras from Police, Health, GSRTC, Panchayat and Municipal Corporation." |
| 0:08–0:35 | `/cameras/import` → **Sandbox catalogue** tab: host shown from Settings → **Import from catalogue** → summary "50 fetched · 50 added · 8 ANPR-enabled · onboarded in N s · first stream ready in M ms"; `/cameras` table fills; open one drawer (metadata, codec H265); click **Export** → CSV with hash | "Onboarding is one click against the catalogue endpoint. The importer maps the catalogue's fields and department names, creates a relay path per camera — a transcoded path for the H.265 ones — and prints the measured onboarding time. The registry export is the sample metadata dataset in our submission." |
| 0:35–0:55 | `/map`: toggle department layer, status layer, district boundaries, coverage circles; hover a cluster; click a Gandhinagar camera → **View live** | "The GIS map layers by department, camera type, status and maintenance, with district boundaries, points of interest and coverage circles. Offline cameras are red; the health poller updates them every minute." |
| 0:55–1:15 | `/wall`: 9-grid with sandbox cameras 1–8 and the own gate; "2 systems" badge; switch one tile to HLS manually; 16-grid shows snapshot tiles | "The video wall shows nine live WebRTC tiles — eight sandbox cameras and our own private camera, two different systems in one viewer. Any tile can switch to HLS; the sixteen-grid uses one-second snapshots for the extra tiles so a laptop is never asked to decode sixteen streams." |
| 1:15–1:35 | Camera page for mock camera 1 (Sachivalaya Gate 1): live reads with confidence and IST time; two-line plate read shown if visible; object counts | "Plates are read on the government feed at five frames per second on the road-facing cameras and at keyframe rate on all of them, so the entire loop is indexed. Two-line plates are handled; each read carries confidence, timestamp and crop." |
| 1:35–2:05 | `/watchlist`: add `GJ 27 XY 3456`, reason suspect, priority high. `/alerts`: alert arrives on the next pass (toast + sound + notification); open drawer; a second read within 60 s attaches to the same alert (read count 2) rather than duplicating | "I add a plate seen a moment ago. On its next pass the alert arrives within seconds of the read, with camera, location and snapshot. Repeated reads within a minute attach to the same alert instead of flooding the panel." |
| 2:05–2:45 | `/vehicles`: search `GJ 01 AB 1234` → exact sightings on 3–4 cameras + fuzzy candidates with crops → confirm one, reject one → **Build route**: numbered markers, polyline across ≥ 3 cameras, timeline in IST, amber speed-flag badges (explain) → **Export PDF** → open | "Searching the stolen plate returns exact sightings on four cameras and fuzzy candidates with crops; the operator confirms or rejects each — decisions are audited. The route orders the sightings in time with distance and speed per leg. On the looping sandbox the speeds are implausible, which the system flags in amber rather than hiding. The route PDF carries the map, the timeline, the crops and the evidence hash." |
| 2:45–3:25 | **Output report**: `/reports` → Output report, last 2 h, CSV + PDF. Open the CSV: columns `captured_at_ist, camera_id, camera_name, department, lat, lon, plate, confidence, crop_sha256…`, scroll, show the `#` trailer line with the row hash. Open the PDF: cover summary (cameras, reads, valid %, unique plates, **object-count totals**, alerts), table, thumbnails with hashes, **quality section** with spot-check accuracy | "This is the output report required by the challenge: every detected plate on the government feed with IST timestamp, camera ID, department, coordinates, confidence and crop hash, as CSV and PDF. The PDF adds the vehicle and person counts and the analytics-quality section with the measured accuracy from labelled crops." |
| 3:25–3:40 | Back to `/alerts` → **Play recording** on the alert → recorded footage from 10 s before the read; **Export clip** → clip with SHA-256; `/reports` History → **Verify** → match | "Every alert and sighting links to the recording; a thirty-second evidence clip is stored with its hash, and any file can be re-verified." |
| 3:40–4:00 | Quick pass: `/health` (uptime, down > 5 min, AMC expiring, workers), `/gap-analysis` (zero-coverage cells, uncovered POIs, ageing table, recommendations, export), `/dashboard`, `/audit` (login, import, export, search, route, report rows). End card | "Health and maintenance monitoring, the gap-analysis and ageing report, dashboards, and the append-only audit log complete Model 1. Sentinel Gujarat, version 1.0.0-phase1. Thank you." |

Timing rules: the alert in 1:35–2:05 depends on the next loop pass (≤ 90 s on the synthetic loop; on the real sandbox use a plate that recurs frequently — pick it from the top-plates chart). Pause the recording while waiting; never speed up footage.

---

## 3. After recording

1. Remux to MP4; check audio levels (alert sound audible; voice −16 LUFS approx.).
2. Watch both videos once end-to-end; confirm no credentials or secrets are visible (Settings page is not shown; `.env` never opened).
3. Upload to YouTube as **Unlisted**, titles `Sentinel Gujarat – Video 1 (own feed) – Dynatech Consultancy` and `… Video 2 (government feed + output report) …`; description: product, version, hosted URL, "Recorded from the hosted platform on <date IST>; no mock-ups".
4. Copy MP4s to the Drive folder `04_Videos/`; write `links.txt` with both YouTube URLs.
5. Add the "how the videos were produced" paragraph to `README.md` (OBS, hosted URL, sandbox feeds, no edits other than trims and pauses while waiting for the next loop pass).
6. Open both YouTube links in an incognito window and on a phone before entering them in the portal.
