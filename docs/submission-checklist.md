# Sentinel Gujarat — Phase 1 submission checklist

Deadline: **7 September 2026** (hour unconfirmed — submit by **12:00 IST**). Portal: https://sentinel.gujarat.gov.in/login → submission. Product name everywhere: **Sentinel Gujarat**; version tag **`v1.0-phase1`** (`1.0.0-phase1` in the UI, PDFs and README); model statement everywhere: **"Model 1 + Model 2 (hybrid roadmap to 3/4)"**.

Owner of this checklist: P (PM/Docs). Tick every box in order on Monday morning; nothing is submitted with an open box.

---

## 1. Drive folder layout ("Anyone with the link – Viewer")

Folder name: `Sentinel-Gujarat_Dynatech_Phase1_v1.0-phase1`

```
Sentinel-Gujarat_Dynatech_Phase1_v1.0-phase1/
├── 00_README-FIRST.pdf                       # one page: what is where, hosted URL, how to log in (credentials are in the portal form, not here)
├── 01_Presentation/
│   ├── Sentinel-Gujarat_Presentation.pdf     # docs/export/Sentinel-Gujarat-Presentation.pdf, built by docs/tools/build-presentation.mjs from PRESENTATION-OUTLINE.md + docs/screenshots/<nn>-*.png (live/ fallback) + diagrams
│   └── Sentinel-Gujarat_Presentation.pptx    # docs/export/Sentinel-Gujarat-Presentation.pptx (same build; speaker notes included)
├── 02_HLD/
│   └── Sentinel-Gujarat_HLD.pdf              # docs/tools/export-pdf.mjs from docs/HLD.md (mermaid fences rendered, screenshots embedded)
├── 03_Scale-Plan/
│   └── Sentinel-Gujarat_Plan-for-Scale.pdf   # from docs/SCALE-PLAN.md (GPU rows state "not measured"; no placeholder cells remain)
├── 04_Videos/
│   ├── Sentinel-Gujarat_Video1_Own-Feed_v1.0-phase1.mp4
│   ├── Sentinel-Gujarat_Video2_Government-Feed_v1.0-phase1.mp4
│   └── links.txt                             # unlisted YouTube URLs + hosted URL + repo URL
├── 05_Output-Reports/                        # generated from the hosted platform on the sandbox feed
│   ├── detections_<from>_<to>_IST.csv        # GET /reports/detections?format=csv (last 2–6 h window)
│   ├── detections_<from>_<to>_IST.pdf        # same window, format=pdf (includes counts + quality section)
│   ├── route_GJ01AB1234_<YYYYMMDD_HHMM>IST.pdf   # route report of a sandbox-seen plate
│   ├── quality_<YYYYMMDD_HHMMSS>IST_jury_admin.pdf # GET /reports/quality?format=pdf
│   └── SHA256SUMS.txt                        # sha256 of every file above (matches X-Sentinel-Sha256 / footer)
├── 06_Model1-Deliverables/
│   ├── cameras_export_<YYYYMMDD_HHMM>IST.csv # GET /cameras/export — "sample onboarded camera-metadata dataset"
│   ├── Sentinel-Gujarat_REGISTRY-API.pdf     # rendered from /api/openapi.json (docs/REGISTRY-API.md) — "registry API documentation"
│   ├── gap-analysis_<YYYYMMDD_HHMMSS>IST_jury_admin.pdf   # "sample gap-analysis report"
│   └── gap-analysis_<YYYYMMDD_HHMMSS>IST_jury_admin.csv
├── 07_Source/
│   ├── repo-link.txt                         # GitHub URL + tag v1.0-phase1 + commit hash
│   └── sentinel-gujarat_v1.0-phase1.zip      # git archive of the tag (no media/, no .env)
├── 08_Licences/
│   └── LICENCES.pdf                          # from docs/LICENCES.md
└── 09_Screenshots/                           # the same images used in PPT, HLD and README
    ├── 01-login.png … 11-output-report.png
```

## 2. Deliverables table (plan 6.3b + portal expected deliverables)

| Evaluation area / deliverable | Evidence | File / location | Owner | Done |
|---|---|---|---|---|
| 1 · Successful test case on government feed | Sandbox import, 9-camera wall, ANPR reads, watchlist alert, route, output report | Video 2; hosted URL; `05_Output-Reports/` | M + P | ☐ (import, wall, health, map, **ANPR reads with crops, quality report and the 2-hour output report (API CSV/PDF + evidence PDF of detected vehicles)** proven on the real feed 5 Sept — `acceptance-log.md` "Real sandbox", `docs/export/`; watchlist alert and multi-camera route on a real read still open — the watchlist holds the real plates, waiting for their next pass) |
| 2 · Solution presentation | 15 slides per `PRESENTATION-OUTLINE.md` incl. four-box slide and bonus slide | `01_Presentation/` | P | ☐ |
| 3 · Solution architecture | HLD with diagrams, adapter framework, principles table, security, integration-readiness matrix, "systems unaffected" page | `02_HLD/` | P + B | ☐ |
| 4 · Working platform and demonstration | Hosted HTTPS URL; 3 jury logins (`jury_admin`, `jury_operator`, `jury_viewer`) + `dept_admin_police`; Video 1 + Video 2 | Portal form (credentials); `04_Videos/` | D + P | ☐ |
| 5 · Video analytics output | Reads with confidence + crops; vehicle/person counts; IST timestamps; quality section; output + route reports | `05_Output-Reports/`; dashboards; Video 2 | M + B | ☐ (government-feed reads, detected-vehicle evidence with timestamps, quality PDF and output CSV/PDF generated 5 Sept — `docs/export/`; object counts off on the laptop; route PDF only from the mock run) |
| 6 · Scalability and PoC readiness | Plan for Scale with GPU/bandwidth/storage arithmetic, cost tables, cost-benefit, phased rollout, **measured figures filled** | `03_Scale-Plan/` | P + D | ☐ |
| 7 · Submission completeness | This checklist; consistency pass; every link opened from incognito and a phone | Portal form | P | ☐ |
| Model 1 · Working registry portal with GIS map view | Hosted URL `/cameras`, `/map` | Hosted URL | F | ☐ (on the laptop with the 30 real cameras: `sandbox/proof-cameras.png`, `proof-map.png`; hosted URL pending) |
| Model 1 · Bulk and manual onboarding demonstration | Video 2 (catalogue import + CSV import), Video 1 (manual add) | `04_Videos/` | P | ☐ (bulk onboarding of the real catalogue proven 5 Sept: 30/30 in 82–99 s incl. probes, `sandbox/proof-import.png`; video pending) |
| Model 1 · Sample onboarded camera-metadata dataset | Registry export CSV (watermarked, hashed) | `06_Model1-Deliverables/cameras_export_*.csv` | B | ☐ |
| Model 1 · Registry API documentation | `REGISTRY-API.pdf` + live `/api/docs` | `06_Model1-Deliverables/`; hosted URL | B + P | ☐ |
| Model 1 · Sample gap-analysis report | PDF + CSV export | `06_Model1-Deliverables/gap-analysis_*` | B | ☐ |
| Model 2 · Unified viewer connected to feeds from at least two systems | Wall with sandbox + own feed ("2 systems" badge) | Video 2 0:55; screenshot `09-wall-9.png` | F | ☐ |
| Model 2 · ANPR demonstration on live or recorded feeds | Videos 1 and 2 | `04_Videos/` | M | ☐ |
| Model 2 · Searchable metadata dashboard | Detections page, vehicle search, dashboards | Hosted URL; Video 2 | F | ☐ |
| Model 2 · Architecture note that existing departmental systems remain unaffected | HLD §17 (one page) + slide 10 | `02_HLD/`, `01_Presentation/` | P | ☐ |
| Open-source requirement | `LICENCES.md` covers every runtime component; AGPL fallback disclosed | `08_Licences/`; repo `docs/LICENCES.md`; `/about` page | P | ☐ |
| Watchlist correlation workflow (DB structure, matching logic, alerting, UI) | HLD §7 + slide 8 | `02_HLD/`, `01_Presentation/` | P | ☐ |

## 3. Master checklist (plan 6.6, expanded)

### 3.1 Platform

- [ ] Hosted HTTPS URL responds; `/healthz` → `status: ok`, `version: 1.0.0-phase1`
- [ ] Non-default passwords set in `deploy/.env` for `jury_admin`, `jury_operator`, `jury_viewer`, `dept_admin_police` (`deploy.sh` prints no "default password" warning; `/healthz` shows `default_secrets_in_use: false`); same passwords entered in the portal form; **never** in the repo or Drive
- [ ] Logins tested for all four accounts from an incognito window **and** from a phone on 4G; viewer sees no edit/ack/export buttons; `dept_admin_police` sees only Police cameras
- [x] Wall: 9 tiles play real sandbox footage; H.265 cameras (cam06, cam17) and B-frame H.264 cameras play via `_h264`; HLS fallback engages on its own — 5 Sept 2026 on the laptop: 9/9 tiles, first frame 6–41 s (cold on-demand pulls + transcoder start; plain H.264 tiles 6–7 s), `sandbox/proof-wall-9.png`
- [ ] Wall from outside the VM network (hosted URL): WebRTC tiles within 5 s of a warm path, HLS fallback with UDP blocked — the 5 s figure was measured on the mock; on the real sandbox a *cold* tile needs the 4–40 s source start, so state "first frame in ≤ 45 s cold, seconds when warm"
- [x] Sandbox import result: catalogue count equals registry count (the real catalogue has **30** cameras, not the ≥ 40 assumed here; `/cameras` = 30 sandbox + `OWN-GATE-01`); onboarding time visible in the import summary (82–99 s with probes, first stream 2–5 s) — 5 Sept 2026, `acceptance-log.md` "Real sandbox"; repeat once on the fresh hosted DB (`added 30`)
- [x] Credentials of the government feed never visible: `user:***@` for all four roles over every endpoint, export, audit row and page; `docker logs` 0 hits; secret scan 249/250 (no leak) — 5 Sept 2026
- [x] Health on the government feed: 31/31 online after two ticks with `sandbox.probe_timeout_s=45` (direct probes, ≤ 6 parallel, 300 s min interval) — 5 Sept 2026, `sandbox/proof-health.png`
- [x] Map from the enrichment CSV: districts (Ahmedabad 9, Navsari 7, Junagadh 5, …), departments and 7 / 16 / 7 location confidence rendered (solid / dashed / hollow markers) — 5 Sept 2026, `sandbox/proof-map.png`
- [x] ANPR camera selection on the real feed applied (`docs/anpr-camera-selection.md`: live cam01/02/04/05/07/12/14/16, 22 pre-index) — 5 Sept 2026
- [x] Real reads on the government feed: live worker on the real-feed profile (native decode, tiled ONNX, best-shot, `ensemble` OCR) — 5 Sept 2026 evening: 12 valid-format reads on cam07 20:31–22:12 IST, 7 confirmed exact by eye (`docs/anpr-accuracy.md` §5), `sandbox/detections-real.png`, `search-real.png`; watchlist seeded with the plates read (`watchlist_seed.csv` rev 3)
- [x] Quality report on the government feed: 20 crops labelled via `POST /api/qa/labels`, `GET /api/reports/quality?source=sandbox` → 35 % exact / 58 % char accuracy (night footage; daylight re-measure pending) — `docs/export/quality_*.pdf`
- [ ] Alerts: adding a sandbox-seen plate to the watchlist raises an alert within 5 s of its next read; `latency_ms` populated — seeded 5 Sept 22:03 IST with the plates read on cam07; no re-read yet (the loop returns a vehicle after 12 h 02 min: watch cam07 from ≈ 08:30 IST on 6 Sept, or add a plate seen live during the daylight window)
- [ ] Route of a sandbox-seen plate spans ≥ 2 cameras with polyline, timeline and PDF — no plate seen on two organiser cameras so far (only cam07 reads at night); proven on the mock loops + own gate (acceptance check 16)
- [x] Output report CSV row count equals the API `total` for the same filter; PDF opens; IST timestamps; hashes present — government feed 5 Sept 20:30–22:30 IST with `source=sandbox` (`docs/export/detections_*.csv/.pdf`, hashes match `X-Sentinel-Sha256`) + the evidence PDF of detected vehicles with timestamps and camera ids (`docs/export/evidence_*.pdf`), all opened and page-checked
- [ ] Recording: "Play recording" works on an alert; "Export clip" returns a hash; `/evidence/verify` → `match=true`
- [x] `pytest backend/tests` green; `npm run typecheck && npm run build` clean (acceptance check 29) — 5 Sept 2026: backend 190 passed, ANPR 63 passed, typecheck/lint/build clean (evening tree with the sandbox work: backend 214, ANPR 72, frontend clean)
- [x] All 30 acceptance checks of `CONTRACT.md` §13.3 executed and logged in `docs/acceptance-log.md` with date/time IST — on the laptop (5 Sept 2026, fresh database); repeat §3.1 on the hosted URL after `deploy.sh`
- [ ] Nightly `pg_dump` in place; VM snapshot "v1.0-phase1-submission" taken; `docker compose ps` all healthy
- [ ] `SCALE-PLAN.md` §1.1: either the GPU soak has run on the VM (`scripts/soak_run.sh`, gpu profile) and its two rows carry measured values, or the rows still say **not measured** (vendor planning values) — never a promise to fill later
- [x] `grep -rn '\[FILL\|\[MEASURE\|\[SCREENSHOT' docs/*.md` prints nothing except `ORGANISER-EMAIL.md` (the e-mail template keeps its sender fields) — verified 5 Sept 2026

### 3.2 Repository

- [ ] GitHub repo accessible (public at submission, or jury collaborator added); tag `v1.0-phase1` pushed; `git archive` zip in Drive
- [ ] `README.md`: setup (`deploy.sh`), architecture summary + diagram, jury usernames ("passwords supplied in the submission form"), how the videos were produced, weights and their SHA-256, licence pointer, `CPU=1` laptop instructions, GPU VM instructions
- [ ] `docs/` contains `HLD.md`, `SCALE-PLAN.md`, `LICENCES.md`, `REGISTRY-API.md` (+ `tools/render-registry-api.mjs`), `video-scripts.md`, `submission-checklist.md`, `PRESENTATION-OUTLINE.md`, `PHASE2-RUNBOOK.md`, `ORGANISER-EMAIL.md`, `acceptance-log.md` (filled), `screenshots/README.md`, `diagrams/*.mmd` + the ten rendered `diagrams/*.svg`
- [ ] No secrets in the repo: `.env` ignored, `.env.example` has defaults only; no credentials in screenshots (the 22 `docs/screenshots/sandbox/*.png` were scanned 5 Sept 2026: 0 hits; `SANDBOX_STREAM_PASSWORD` lives only in `deploy/.env` and the settings table)
- [ ] `media/own/*`, `media/fallback/*`, `media/synthetic/*.mp4` ignored; `media/synthetic/plates.json` committed

### 3.3 Videos

- [ ] Video 1 (own feed) ≤ 3:00, unlisted YouTube + Drive; shows onboarding → live view → ANPR → watchlist → alert on screen
- [ ] Video 2 (government feed) unlisted YouTube + Drive; shows catalogue onboarding → wall → live reads → alert → route → **output report CSV + PDF** → recording playback → Model 1 pages
- [ ] Both recorded from the hosted URL at `v1.0-phase1`; no mock-ups; trims/pauses only
- [ ] Both links open in incognito and on a phone; `links.txt` correct

### 3.4 Documents

- [ ] Moment screenshots regenerated from the hosted URL: `SG_BASE=https://<host> node docs/tools/moment-shots.mjs` → `docs/screenshots/01-login.png … 12-systems-unaffected.png`, `moment-shots.json` shows no failures (take `07-import-summary.png` at the first import on the fresh VM database)
- [x] **Build the deliverables** (`make deliverables`; the target runs exactly these commands from the repository root, Node 24, no Docker) — built 5 Sept 2026: HLD 38 pages / 999 KB, Plan for Scale 13 / 301 KB, REGISTRY-API 9 / 324 KB, LICENCES 8 / 261 KB, PHASE2-RUNBOOK 5 / 174 KB, CONTRACT 72 / 1.7 MB, presentation PPTX 5.2 MB (17 slides, 17 notes pages) + PDF 17 pages / 3.7 MB; pages 1/5/12/20 of the HLD, 1/12 of the deck and 1/2/3 of the scale plan opened in Chrome's PDF viewer. Rebuild after the hosted-URL screenshots and the team names are in:
  ```bash
  cd docs/tools && npm i --no-save && cd ../..                                  # marked, mermaid, pptxgenjs (MIT) into docs/tools/node_modules
  node docs/tools/export-pdf.mjs                                                # HLD, SCALE-PLAN, REGISTRY-API, LICENCES, PHASE2-RUNBOOK, CONTRACT → docs/export/*.pdf (A4, diagrams rendered, page x of y)
  SG_HOSTED_URL=https://<host> SG_TEAM_EMAIL=<team e-mail> SG_TEAM='[{"role":"B · Backend","name":"…"},…]' \
    node docs/tools/build-presentation.mjs                                      # docs/export/Sentinel-Gujarat-Presentation.pptx + .pdf (17 slides, speaker notes from PRESENTATION-OUTLINE.md)
  node docs/tools/preview-pdf.mjs docs/export/Sentinel-Gujarat_HLD.pdf 1 5 --zoom 70   # page PNGs into docs/export/.build/preview/ (needs Chrome/Edge); repeat per PDF
  ```
  Chromium comes from `frontend/node_modules/playwright` (else `cd docs/tools && npm i --no-save playwright@1 && npx --yes playwright@1 install chromium`). The scripts exit 1 on any missing input or unrendered diagram; `export-pdf.mjs` prints the count of `[FILL…]` / `[SCREENSHOT…]` / `[MEASURE]` markers it highlighted in yellow — **zero** is the target; `build-presentation.mjs` prints the `[FILL: …]` placeholders still on the title / team slides and any text box whose content overflows. Per-slide PNGs are in `docs/export/.build/slides/` — look at every one; open the PPTX in PowerPoint once (fonts substitute to Arial) before uploading; copy the outputs into the Drive folder under the names in §1
- [ ] Solution Presentation PDF (+ PPTX) — 17 slides (the outline's 15 plus agenda and measured-figures slides), speaker notes on every slide; four-box integration-workflow slide; bonus-items slide; "systems unaffected" slide; slide 11 speaker notes say the GPU figures are vendor values (CPU-only laptop figures are the measured ones)
- [ ] HLD PDF — all 21 sections; diagrams rendered; questionnaire table; principles table; integration-readiness matrix; privacy/retention/evidence section; recording tier; Phase 2 section; "systems unaffected" page; live-vs-roadmap table; §12 security table verified against `curl -I https://<host>/` (HSTS + CSP present, `/api/internal/*` → 404)
- [ ] Plan for Scale PDF — topology, GPU arithmetic shown, bandwidth, storage tiers, LB/HA/DR/monitoring/security, capex/opex tables for Topology A and B (and B'), cost-benefit, phased rollout 500 → 5,000 → 25,000 → 80,000, measured-figures table with GPU rows either measured or explicitly "not measured"
- [ ] `REGISTRY-API.pdf` = `docs/REGISTRY-API.md` (curated) + the appendix generated from the live schema: `node docs/tools/render-registry-api.mjs https://<host>/api/openapi.json > docs/REGISTRY-API.generated.md`; diff against the curated file and merge any endpoint the code added
- [x] `LICENCES.pdf` (8 pages, built 5 Sept 2026)
- [ ] Output report CSV + PDF, route PDF, quality PDF, gap-analysis PDF + CSV, registry export CSV in Drive with `SHA256SUMS.txt`

### 3.5 Consistency pass (do last, with all files open)

- [ ] Same product name, version tag, hosted URL, camera counts (**30 real sandbox + 1 own; 8 ANPR-live** — the 50-camera figures are the mock fixture of the scripted acceptance run and must be labelled as such wherever they remain) and screenshots in PPT, HLD, Plan for Scale, README and both videos
- [ ] Every document states "Model 1 + Model 2 (hybrid roadmap to 3/4)" identically
- [ ] Every document distinguishes live vs roadmap honestly (HLD §20 is the reference); nothing claimed that the videos do not show
- [ ] Cost headline figures identical in PPT slide 11, HLD §14 and Plan for Scale §8.4
- [ ] Measured figures identical in PPT, HLD §14 and Plan for Scale §1.1
- [ ] IST used in every timestamp shown in documents and reports; UTC only in JSON/DB references
- [ ] Contact details and team names identical everywhere

### 3.6 Drive and portal

- [ ] Drive folder shared "Anyone with the link – Viewer"; opened from an incognito window; every PDF and MP4 previews
- [ ] Portal submission form: hosted URL, credentials (4 accounts with roles), GitHub URL + tag, YouTube links (2), Drive link, document uploads (PPT/PDF, HLD PDF, Scale Plan PDF) as the form allows
- [ ] Submitted by **12:00 IST, Monday 7 September 2026**; confirmation page screenshot saved as `docs/submission-confirmation.png` and copied to Drive `00_`
- [ ] Post-submission: tag frozen; no deploys to the hosted URL except hot-fixes recorded in `docs/acceptance-log.md`; pre-index keeps running for Phase 2

## 4. Portal upload steps (Monday, 09:00–12:00 IST)

1. 09:00 — Run §3.1 platform checks (15 min). Fix only blockers.
2. 09:20 — Generate the final output reports from the hosted platform (`/reports`), the registry export and the gap-analysis export; download; compute `SHA256SUMS.txt`; copy to Drive `05_` and `06_`.
3. 09:40 — Export PPT/HLD/Scale Plan/REGISTRY-API/LICENCES to PDF; upload to Drive; run §3.5 consistency pass.
4. 10:10 — Verify the Drive folder and both YouTube links from an incognito window and a phone.
5. 10:20 — Log in to https://sentinel.gujarat.gov.in/login → submission form. Fill: team/category details, chosen models ("Model 1 + Model 2 (hybrid roadmap to 3/4)"), hosted URL, test credentials (4 accounts), GitHub URL + tag, YouTube links, Drive link; upload PDFs where fields exist.
6. 10:40 — Re-read every field; second team member reviews; submit.
7. 10:45 — Screenshot the confirmation; save and copy to Drive; e-mail the organiser only if the portal shows no confirmation (`ORGANISER-EMAIL.md` §3 template).
8. 11:00 — Tag `v1.0-phase1` if not already; VM snapshot; stop all non-essential changes.
