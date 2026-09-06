# Sentinel Gujarat — Phase 2 on-site runbook (Grand Finale, 10–11 September 2026, i-Hub Gujarat, Gandhinagar)

Product: Sentinel Gujarat `1.0.0-phase1` (tag `v1.0-phase1`, plus any hot-fix tag cut on 8–9 September) · Model 1 + Model 2 (hybrid roadmap to 3/4). Applies if shortlisted on the evening of 7 September. Preparation days: 8–9 September. Owner: D (DevOps) for kit and environment, P for the pitch, M for ANPR sizing, B/F on call.

What the jury will test (guide §5, FAQ Q26–Q28): onboard the ~50 provided cameras; on the day they give a **vehicle registration number**; show the **complete route** and a **timestamped, location-wise movement history**, plus evidence of **interoperability, onboarding efficiency, analytics capability and end-to-end performance**.

Working assumption (until the organiser answers `ORGANISER-EMAIL.md` Q6): **the on-site feed may be a new environment** — new host, possibly new cameras and fresh footage — so nothing may depend on the Phase 1 pre-index.

---

## 1. Kit list

| Item | Spec / content | Owner | Packed |
|---|---|---|---|
| GPU laptop (primary on-site host) | RTX-class GPU (≥ 8 GB VRAM), 32 GB RAM, 1 TB SSD; Ubuntu 22.04 or Windows 11 + WSL2 with Docker Engine + NVIDIA container toolkit; full stack pulled/built at `v1.0-phase1` (+ hot-fix tag) with `deploy.sh --gpu` (adds `deploy/docker-compose.gpu.yml`: NVENC relay build + `MEDIAMTX_TRANSCODE=nvenc`); `.env` with `COMPOSE_PROFILES=gpu`, `MOCK_SANDBOX=0`; images exported with `docker save` to a USB SSD as a fallback | D | ☐ |
| Cloud GPU VM (secondary host, kept running) | The Phase 1 VM (T4, public IP), reachable over the hotspot; `anpr-live-gpu` idle and ready to take cameras via `ANPR_CAMERAS` | D | ☐ |
| Second laptop | Presenter's machine: browser only, HDMI/USB-C adapters, presentation PDF, backup videos | P | ☐ |
| 4G/5G hotspot ×2 (two operators) | Unlimited data plans; tested with WebRTC (UDP) and TCP-only fallback | D | ☐ |
| Ethernet cable + USB-Ethernet adapter, small gigabit switch | For the venue LAN / camera network | D | ☐ |
| USB SSD | Docker image tarballs, fallback clips (`media/fallback/<id>.mp4`, 10 min per chosen camera), synthetic videos, `own_gate.mp4`, VM snapshot export, weights | D | ☐ |
| Printed one-pagers ×10 | Architecture (HLD §3.1), cost table (Plan for Scale §8.4), "systems unaffected" page (HLD §17), jury-question map (§5 below) | P | ☐ |
| Power strip, laptop chargers, HDMI cable, clicker | | P | ☐ |
| Credentials sheet (sealed) | Jury accounts, `.env` secrets, cloud console, DNS | D | ☐ |
| Phone with RTSP camera app | To onboard a live phone camera as a third "system" if useful | F | ☐ |

## 2. Preparation (8–9 September)

1. Keep the pre-index running on the sandbox (in case the finale reuses it); export the plate list (`GET /detections?valid_only=true`, top plates from `/dashboard/charts`) to `docs/phase2/plates_seen.csv`.
2. Harden search: partial plates, fuzzy ranking, confirm/reject UX, route PDF in < 60 s from plate entry; rehearse with 10 plates from `plates_seen.csv`.
3. Bring the laptop stack up **offline** (hotspot off) with the synthetic feeds and the fallback clips; confirm wall, ANPR, alerts, route, PDF all work with no internet.
4. Rehearse the **5-minute onboarding drill** (§3) three times against (a) the mock catalogue, (b) the Phase 1 sandbox, (c) a CSV of the same cameras; record the timings in the table.
5. Ask the organiser (again, by phone if no reply): Phase 2 feed nature, catalogue availability on-site, venue network (NAT, UDP), power, table space, agenda, whether the sandbox stays up through 11 September.
6. Update the presentation with the Phase 1 result and measured figures; print the one-pagers.
7. Snapshot the cloud VM and the laptop stack the evening before.

## 3. The 5-minute onboarding drill (new environment → cameras on map → wall → first reads)

Target: **≤ 5 minutes** wall-clock from receiving the host/credentials to the first plate read on screen; **measured** and shown on the import summary.

| Step | Action | Where | Target time | Fallback |
|---|---|---|---|---|
| 0 | Connect the laptop to the venue camera network (or hotspot if the feeds are public); `curl http://<host>/api/ingest \| head` | shell | 0:30 | If the catalogue is missing: ask for a camera list; go to step 2b |
| 1 | `/settings` → Catalogue tab: base URL `http://<host>`, auth (none / basic / bearer / header), timeout → **Test connection** → check `count`, `mapped_sample`, `unmapped_fields`; fix the field map JSON if a field is unmapped (e.g. add `"lat": ["gps.latitude", …]`) → Save | UI | 1:30 | If the shape is unusable: step 2b |
| 2a | `/cameras/import` → Sandbox tab → **Import from catalogue** (with "measure first stream" ticked) → read out the summary: "N fetched · N added · K ANPR-enabled · onboarded in S s · first stream ready in M ms" | UI | 2:00 | |
| 2b | CSV path: fill `cameras_template.csv` from the organiser's list (id, name, department, lat/lon, `rtsp://<host>:8554/stream/<id>`, codec) → `/cameras/import` → CSV → dry-run → import | UI + spreadsheet | 4:00 | |
| 3 | `/map`: cameras appear with `unknown` → `online` within one poll (≤ 60 s; the poller probes idle paths) | UI | 2:30 | If offline: check `rtsp_transport tcp`, host firewall, `ffprobe` from the laptop |
| 4 | `/wall`: 9 tiles WebRTC; if ICE fails on the venue network the tiles fall back to HLS automatically — say so | UI | 3:00 | Snapshot mode still shows the feeds |
| 5 | ANPR: with `ANPR_AUTO_ENABLE_MAX=12` the first 12 live cameras are ANPR-enabled by the import; the worker picks them up within 60 s (`CONFIG_RELOAD_S`); `/health` shows the worker cameras `running`; `/cameras/<id>` shows the first reads | UI | 4:00–5:00 | Raise `ANPR_MAX_CAMERAS`; split cameras to the cloud VM (§4) |
| 6 | Read the measured figures aloud: onboarding time, first-stream time, first read latency | UI | 5:00 | |

Rehearsal timings measured so far (laptop, 5 Sept 2026, `docs/acceptance-log.md` check 5 and SCALE-PLAN §1.1): (a) mock catalogue, 50 cameras → registry + relay paths **2.6 s**, first stream ready **2.1 s** after the request; (b) organiser sandbox — not yet run (no credentials received); (c) CSV, `cameras_sample.csv` (10 rows → 8 added, 2 error rows) imported in check 19 — the wall-clock time was not recorded, the response is immediate for a file that size. The Day 9 rehearsal repeats all three on the venue network and the figures are written on the run sheet.

## 4. Live ANPR sizing on the day

Goal: **every** on-site camera analysed at keyframe rate (so the given plate is captured wherever it appears) plus the 8–12 most road-facing cameras at 5 fps.

| Host | Decode capacity | Assignment | How |
|---|---|---|---|
| Laptop RTX (e.g. RTX 4070 laptop, 1 NVDEC) | ≈ 10–16 × 1080p full-rate; ≈ 40 at keyframe rate | `anpr-live-gpu` on the 8–12 road-facing cameras at 5 fps (`ANPR_CAMERAS=<ids>`); `anpr-preindex-gpu` on the first half of the remaining cameras at keyframe rate | `.env`: `ANPR_MAX_CAMERAS=12` (live), `PREINDEX_SKIP_LIVE=1` |
| Cloud VM T4 | ≈ 20–30 × 1080p full-rate; ≈ 60 at keyframe rate | `anpr-preindex-gpu` on the other half at keyframe rate, or all 50 if the laptop is busy; the VM pulls RTSP **from the laptop's relay** over the hotspot only if the venue feeds are not publicly reachable — otherwise the VM pulls the venue feeds directly (add the venue host to the VM's registry via the same catalogue import, `ANPR_CAMERAS` split) | `ANPR_CAMERAS=<ids>` on the VM's worker; both workers post to **one** API (the laptop's, reachable via the hotspot's public IP, or the VM's API if the laptop pushes its registry there — decide on Day 8 and rehearse) |
| CPU fallback | 3–4 cameras at 2–3 fps per 8 cores | Only if both GPUs fail | `COMPOSE_PROFILES=cpu` |

Decision tree on Day 8: if the venue feeds are reachable from the internet → run the **cloud VM as the primary API + relay** (public URL, jury can open it on their own devices) and the laptop as an extra worker; if not → the **laptop is primary** and the VM only helps with pre-index over the hotspot-uploaded relay (bandwidth permitting; otherwise laptop only). Test both on Day 9.

Camera choice for 5 fps: run `scripts/survey_sandbox.py` in the first ten minutes (one frame per camera + `survey.csv`), pick the frontal, large-plate, daytime views; set `anpr_enabled` on them in the registry (PUT) — the worker follows within 60 s.

### 4.1 Organiser sandbox loop schedule (time the demo and Video 2 to daylight)

The 30 sandbox streams are **looped recordings** played at 1.0×; readability depends on the loop
position, not on the wall clock. Measured through the relay on 5 Sept 2026 (burnt-in OSD clock):
group A (Ahmedabad CSITMS cam01/cam02/cam05, synchronised within 5 s) showed footage
`14-06-2026 08:08:21` at 15:50:15 IST, `13-06-2026 23:50:39` at 19:34:37 IST, `14-06-2026
00:14:34` at 19:58 IST, `14-06-2026 01:17:13` at 21:01 IST and `14-06-2026 01:49:30` at 21:33 IST
→ period **12 h 02 min 08 s (± 30 s)**, playback 1.0× (every later reading landed within a minute of
the model). Footage sunrise (~06:00 on 14 June)
therefore reaches the screen at **01:44 IST 6 Sept, 13:46 IST 6 Sept, 01:48 IST 7 Sept, 13:50 IST
7 Sept** (± 2 min) and daylight lasts until the loop restart (≈ 05:54 or 06:47 IST / 17:56 or 18:49
IST depending on whether the loop ends at footage 10:10 or 11:03 — pin it from `media/loopcheck/`
frames, `docs/sandbox-progress.md`). Other groups run on their own phase (cam10 was 11 min ahead of
cam05; cam29 carries a 14-05-2026 clock), so read the OSD clock of the camera you show. Every
ANPR accuracy figure in `docs/anpr-accuracy.md` states the footage clock it was measured at; the
5 Sept bank was collected at footage 00:26-01:32 and the live windows at ≈ 01:06-01:26 and
≈ 02:06-02:26 (night, light traffic) - the daytime figure is still to be measured in the 13:46-17:56
IST window on 6 Sept with the commands in that document's §7.

## 5. Jury question → what to show (one screen each)

| Jury asks for | Screen / artefact | Talking point |
|---|---|---|
| "Track this vehicle: `<plate>`" | `/vehicles` → type the plate exactly as given → exact hits + fuzzy candidates with crops → confirm/reject → **Build route** → `/vehicles/<plate>/route` | "Normalised to `<PLATE_NORM>`; N exact sightings on M cameras in the window; here are the candidates the OCR was unsure about — I confirm these two by their crops." Never claim an exact match that is only fuzzy |
| Complete route | Route map: numbered markers, polyline, total distance/duration, flags | Legs with speed; flags explained (implausible on loops) |
| Timestamped, location-wise movement history | Route timeline table (IST first/last seen, camera, department, district, reads, confidence) + **Export PDF** | PDF in < 60 s, with map, table, crops, evidence hash |
| Evidence | Route PDF footer hash; alert drawer → **Play recording**; **Export clip** → SHA-256; `/reports` History → **Verify** | Chain of custody; every export audited |
| Interoperability | `/cameras/import` API tab → `/api/docs`; bulk API curl; `/settings` catalogue field map; webhook test to the mock sink; VAHAN mock lookup on the route page | "Any RTSP/ONVIF source, any JSON catalogue, documented REST + webhooks, no vendor SDK required" |
| Onboarding efficiency | Import summary (measured seconds) and the drill timings; `cameras_export.csv` | "50 cameras in S seconds; first stream in M ms; new environment via Settings, no code change" |
| Analytics capability | Camera page live reads + object counts; `/reports/quality` (labelled accuracy, confusions); dashboards | Measured accuracy, not claimed |
| End-to-end performance | Alert `latency_ms` on the panel; dashboard avg latency; worker fps on `/health` | "Read → alert on screen p50 3.3 s / p95 4.0 s on the CPU-only laptop (8 cameras, 3 s vote window; `docs/acceptance-log.md`), re-measured here on the venue feeds" |
| Security / privacy | Login as `dept_admin_police` (scoped), `/audit` filtered on the jury's plate search, `/settings` retention tab, export dialog notice | RBAC in SQL, append-only audit, retention, purpose limitation |
| Scale and cost | Printed one-pager; PPT slide 11; Plan for Scale §8.4 | Topology A ≈ ₹195 Cr capex / ₹46 Cr yr; B ≈ ₹315 / ₹78; B' ≈ ₹243 / ₹53; per-camera figures |
| Departments unaffected | HLD §17 page; `/health` shows one reader per relay path | One read-only connection, nothing installed |

## 6. 10-minute pitch script

| Min | Beat | Screen |
|---|---|---|
| 0:00 | **Problem**: 26 departments, many vendors, 80,000 cameras, one question — where did the vehicle go? | Slide 2 |
| 0:45 | **Model choice**: Model 1 + Model 2 delivered, 3/4 roadmap; why (everything mandatory runs on raw RTSP) | Slide 3 |
| 1:30 | **Live onboarding**: Settings → Import from catalogue → summary with measured time → map | Live UI (drill steps 1–3) |
| 3:00 | **Live viewing + ANPR**: wall (two systems), camera page reads + counts | Live UI |
| 4:15 | **Watchlist alert**: add a plate seen a minute ago → alert with crop, latency, recording | Live UI |
| 5:30 | **Route**: the jury's plate → confirm candidates → route + timeline → PDF with hash | Live UI |
| 7:30 | **Scale + cost**: topology, GPU arithmetic in one line, cost headline, phased rollout | Slide 11 + printed one-pager |
| 8:45 | **Security, privacy, unaffected departments** | Slides 10, 12 |
| 9:30 | **Close**: open source, measured figures, ready for a 500-camera pilot in Gandhinagar in 3 months | Slide 15 |

## 7. Contingencies

| Failure | Response |
|---|---|
| Venue feeds unreachable / catalogue missing | CSV path (§3 step 2b); if no feeds at all: fallback clips as `fallback_<id>` paths + synthetic feeds, declared openly |
| WebRTC blocked | Automatic HLS fallback; say it; TCP ICE on 8189 already configured |
| Hotspot down | Laptop is fully offline-capable; cloud VM only adds capacity |
| GPU laptop fails | Cloud VM primary (public URL); laptop replaced by the presenter's laptop as a browser |
| Plate given is not in the DB and not yet read live | Show fuzzy candidates honestly; keep the search window wide; check `/events?type=loop_reset` and worker health; re-run the search after the next pass — never fabricate |
| Sandbox loop makes speeds implausible | Explain the amber flags: correct behaviour on a looping timeline |
| Time overrun | Skip slides 10/12 details; never skip the live route |

## 8. Logistics still to confirm with the organiser

Travel/accommodation, hour-by-hour agenda for 10–11 September, table/power/network at the venue, whether the sandbox stays up through 11 September, whether jury devices will open our URL (need public reachability) — see `ORGANISER-EMAIL.md`.
