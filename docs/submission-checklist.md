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
│   ├── Sentinel-Gujarat_Presentation.pdf     # from docs/presentation.pptx (PRESENTATION-OUTLINE.md)
│   └── Sentinel-Gujarat_Presentation.pptx
├── 02_HLD/
│   └── Sentinel-Gujarat_HLD.pdf              # from docs/HLD.md (diagrams rendered as SVG/PNG)
├── 03_Scale-Plan/
│   └── Sentinel-Gujarat_Plan-for-Scale.pdf   # from docs/SCALE-PLAN.md ([MEASURE] cells filled)
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
| 1 · Successful test case on government feed | Sandbox import, 9-camera wall, ANPR reads, watchlist alert, route, output report | Video 2; hosted URL; `05_Output-Reports/` | M + P | ☐ |
| 2 · Solution presentation | 15 slides per `PRESENTATION-OUTLINE.md` incl. four-box slide and bonus slide | `01_Presentation/` | P | ☐ |
| 3 · Solution architecture | HLD with diagrams, adapter framework, principles table, security, integration-readiness matrix, "systems unaffected" page | `02_HLD/` | P + B | ☐ |
| 4 · Working platform and demonstration | Hosted HTTPS URL; 3 jury logins (`jury_admin`, `jury_operator`, `jury_viewer`) + `dept_admin_police`; Video 1 + Video 2 | Portal form (credentials); `04_Videos/` | D + P | ☐ |
| 5 · Video analytics output | Reads with confidence + crops; vehicle/person counts; IST timestamps; quality section; output + route reports | `05_Output-Reports/`; dashboards; Video 2 | M + B | ☐ |
| 6 · Scalability and PoC readiness | Plan for Scale with GPU/bandwidth/storage arithmetic, cost tables, cost-benefit, phased rollout, **measured figures filled** | `03_Scale-Plan/` | P + D | ☐ |
| 7 · Submission completeness | This checklist; consistency pass; every link opened from incognito and a phone | Portal form | P | ☐ |
| Model 1 · Working registry portal with GIS map view | Hosted URL `/cameras`, `/map` | Hosted URL | F | ☐ |
| Model 1 · Bulk and manual onboarding demonstration | Video 2 (catalogue import + CSV import), Video 1 (manual add) | `04_Videos/` | P | ☐ |
| Model 1 · Sample onboarded camera-metadata dataset | Registry export CSV (watermarked, hashed) | `06_Model1-Deliverables/cameras_export_*.csv` | B | ☐ |
| Model 1 · Registry API documentation | `REGISTRY-API.pdf` + live `/api/docs` | `06_Model1-Deliverables/`; hosted URL | B + P | ☐ |
| Model 1 · Sample gap-analysis report | PDF + CSV export | `06_Model1-Deliverables/gap-analysis_*` | B | ☐ |
| Model 2 · Unified viewer connected to feeds from at least two systems | Wall with sandbox + own feed ("Two systems" badge) | Video 2 0:55; screenshot `09-wall-9.png` | F | ☐ |
| Model 2 · ANPR demonstration on live or recorded feeds | Videos 1 and 2 | `04_Videos/` | M | ☐ |
| Model 2 · Searchable metadata dashboard | Detections page, vehicle search, dashboards | Hosted URL; Video 2 | F | ☐ |
| Model 2 · Architecture note that existing departmental systems remain unaffected | HLD §17 (one page) + slide 10 | `02_HLD/`, `01_Presentation/` | P | ☐ |
| Open-source requirement | `LICENCES.md` covers every runtime component; AGPL fallback disclosed | `08_Licences/`; repo `docs/LICENCES.md`; `/about` page | P | ☐ |
| Watchlist correlation workflow (DB structure, matching logic, alerting, UI) | HLD §7 + slide 8 | `02_HLD/`, `01_Presentation/` | P | ☐ |

## 3. Master checklist (plan 6.6, expanded)

### 3.1 Platform

- [ ] Hosted HTTPS URL responds; `/healthz` → `status: ok`, `version: 1.0.0-phase1`
- [ ] Non-default passwords set in `deploy/.env` for `jury_admin`, `jury_operator`, `jury_viewer`, `dept_admin_police` (`deploy.sh` prints no "default password" warning); same passwords entered in the portal form; **never** in the repo or Drive
- [ ] Logins tested for all four accounts from an incognito window **and** from a phone on 4G; viewer sees no edit/ack/export buttons; `dept_admin_police` sees only Police cameras
- [ ] Wall: 9 tiles play via WebRTC within 5 s from outside the VM network; HLS fallback works when UDP is blocked; H.265 camera plays via `_h264`
- [ ] Sandbox import result: ≥ 40 cameras registered; catalogue count equals registry count; onboarding time visible in the import summary
- [ ] Alerts: adding a sandbox-seen plate to the watchlist raises an alert within 5 s of its next read; `latency_ms` populated
- [ ] Route of a sandbox-seen plate spans ≥ 2 cameras with polyline, timeline and PDF
- [ ] Output report CSV row count equals the API `total` for the same filter; PDF opens; IST timestamps; hashes present
- [ ] Recording: "Play recording" works on an alert; "Create clip" returns a hash; `/evidence/verify` → `match=true`
- [ ] `pytest backend/tests` green; `npm run typecheck && npm run build` clean (acceptance check 29)
- [ ] All 30 acceptance checks of `CONTRACT.md` §13.3 executed and logged in `docs/acceptance-log.md` with date/time IST
- [ ] Nightly `pg_dump` in place; VM snapshot "v1.0-phase1-submission" taken; `docker compose ps` all healthy
- [ ] Soak figures captured for `SCALE-PLAN.md` §1.1 (`[MEASURE]` cells replaced)

### 3.2 Repository

- [ ] GitHub repo accessible (public at submission, or jury collaborator added); tag `v1.0-phase1` pushed; `git archive` zip in Drive
- [ ] `README.md`: setup (`deploy.sh`), architecture summary + diagram, jury usernames ("passwords supplied in the submission form"), how the videos were produced, weights and their SHA-256, licence pointer, `CPU=1` laptop instructions, GPU VM instructions
- [ ] `docs/` contains `HLD.md`, `SCALE-PLAN.md`, `LICENCES.md`, `REGISTRY-API.md` (+ `tools/render-registry-api.mjs`), `video-scripts.md`, `submission-checklist.md`, `PRESENTATION-OUTLINE.md`, `PHASE2-RUNBOOK.md`, `ORGANISER-EMAIL.md`, `acceptance-log.md` (filled), `screenshots/README.md`, `diagrams/*.mmd` + the ten rendered `diagrams/*.svg`
- [ ] No secrets in the repo: `.env` ignored, `.env.example` has defaults only; no credentials in screenshots
- [ ] `media/own/*`, `media/fallback/*`, `media/synthetic/*.mp4` ignored; `media/synthetic/plates.json` committed

### 3.3 Videos

- [ ] Video 1 (own feed) ≤ 3:00, unlisted YouTube + Drive; shows onboarding → live view → ANPR → watchlist → alert on screen
- [ ] Video 2 (government feed) unlisted YouTube + Drive; shows catalogue onboarding → wall → live reads → alert → route → **output report CSV + PDF** → recording playback → Model 1 pages
- [ ] Both recorded from the hosted URL at `v1.0-phase1`; no mock-ups; trims/pauses only
- [ ] Both links open in incognito and on a phone; `links.txt` correct

### 3.4 Documents

- [ ] Solution Presentation PDF (+ PPTX) — 15 slides per outline; four-box integration-workflow slide; bonus-items slide; "systems unaffected" slide
- [ ] HLD PDF — all 21 sections; diagrams rendered; questionnaire table; principles table; integration-readiness matrix; privacy/retention/evidence section; recording tier; Phase 2 section; "systems unaffected" page; live-vs-roadmap table
- [ ] Plan for Scale PDF — topology, GPU arithmetic shown, bandwidth, storage tiers, LB/HA/DR/monitoring/security, capex/opex tables for Topology A and B (and B'), cost-benefit, phased rollout 500 → 5,000 → 25,000 → 80,000, measured-figures table filled
- [ ] `REGISTRY-API.pdf` = `docs/REGISTRY-API.md` (curated) + the appendix generated from the live schema: `node docs/tools/render-registry-api.mjs https://<host>/api/openapi.json > docs/REGISTRY-API.generated.md`; diff against the curated file and merge any endpoint the code added
- [ ] `LICENCES.pdf`
- [ ] Output report CSV + PDF, route PDF, quality PDF, gap-analysis PDF + CSV, registry export CSV in Drive with `SHA256SUMS.txt`

### 3.5 Consistency pass (do last, with all files open)

- [ ] Same product name, version tag, hosted URL, camera counts (50 sandbox + 1 own; 8 ANPR-live) and screenshots in PPT, HLD, Plan for Scale, README and both videos
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
