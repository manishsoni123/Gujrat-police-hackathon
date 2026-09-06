# Screenshots for the submission documents

Four sets live here (PNG files are git-ignored; keep the final set in the Drive folder `09_Screenshots/`):

1. **Automated full-page captures** — `make screenshots` (Playwright container, logs in as `jury_admin`, visits every route) writes `<nn>_<route>.png`, e.g. `01_login.png`, `02_dashboard.png`, `03_cameras.png`, `05_map.png`, `06_wall.png`. Use them for the HLD and README where a whole page is wanted.
2. **Moment shots** — `node docs/tools/moment-shots.mjs` (Playwright driving the local Google Chrome at 1920×1080, IST, logged in as `jury_admin`) writes the eleven files named below plus `moment-shots.json` (what each shot contains: playing tiles, toast delay, PDF page used). It presses **Import from catalogue** for `07`, adds and removes a filler plate for `10`, and renders the route PDF's last page (`06`, evidence hash in the footer) and the output-report cover (`11`) through Chrome's PDF viewer from `docs/screenshots/.build/*.pdf`. Re-run it on the hosted URL (`SG_BASE=https://<host>`) before building the deck; `07` shows "50 added" only at the first import on a fresh database, so take that one right after `deploy.sh`. These are the names referenced by `PRESENTATION-OUTLINE.md`:

3. **Live-stack captures** — `cd frontend && node e2e/live_screenshots.mjs` (Playwright + the local Google Chrome, real login form, 1440×900, IST) writes `live/<key>.png`: every route, the 4/9/16 wall with WebRTC state, the alert toast for a plate added live, a 1024 px tablet pass and the `jury_viewer` role (48 files on 5 Sept 2026). OSM tiles are fetched by the script with an identifying User-Agent because the tile servers refuse automated browsers. Moment-shot equivalents: `01` → `live/login.png`, `02`/`08` → `live/map.png`, `03` → `live/dashboard.png`, `04` → `live/camera-detail.png` (and `camera-detail-h265.png`), `05` → `live/route.png` (`GJ 27 XY 3456`, four Gandhinagar cameras), `09` → `live/wall-9.png`, `10` → `live/alert-toast.png`, `12` → `live/health.png`. The moment-shot script (item 2) supersedes these equivalents, including `06`, `07` and `11`, which used to be manual.

4. **Final acceptance journey** — `cd frontend && node e2e/final_journey.mjs` (Playwright + the local Google Chrome, 1440×900, IST, real login form) writes `final/01-login.png … 28-audit.png` — the jury journey of MVP-PLAN §3.6 step by step (import summary, `/api/docs`, registry + drawer + export hash, map layers + "View live" popup, 9-grid wall + one tile switched to HLS, live reads, watchlist add → toast → acknowledge with note, search → route → export hash → PDF page, output report CSV hash + PDF cover, Play recording → Export clip hash, History → Verify, health, gap analysis, dashboard, audit) plus `29-31-landing-<role>.png` for the three jury roles, with `console-errors.json` (must be empty) and `journey.json` (what each shot contained). Taken on 5 Sept 2026 for `docs/acceptance-log.md` check 28; re-run on the hosted URL with `SG_BASE=https://<host>` before the videos are cut.

| File | What must be visible | When to take it |
|---|---|---|
| `01-login.png` | Login page with tagline and product name | any time |
| `02-map-districts.png` | GIS map zoomed to Gujarat with district boundaries and department layer on | after import |
| `03-dashboard.png` | Dashboard tiles with non-zero reads, alerts and object counts | after ≥ 30 min of reads |
| `04-camera-live-reads.png` | Camera page: player caption "WebRTC", live reads list with crops, confidence, IST time; object counts ticking | during a plate pass |
| `05-route.png` | Route page: numbered markers, polyline across ≥ 3 cameras, timeline table, amber speed flags | after the pre-index / soak |
| `06-report-hash.png` | Route or output-report PDF footer showing the evidence hash and watermark | after exporting a PDF |
| `07-import-summary.png` | Sandbox import summary "50 fetched · 50 added · 8 ANPR-enabled · onboarded in N s · first stream in M ms" | first import on the fresh hosted DB |
| `08-map-layers.png` | Map with layer control open: department, status, coverage circles, POIs | after import |
| `09-wall-9.png` | 9-tile wall: 8 sandbox cameras + own gate, "2 systems" badge, tile captions "WebRTC" | after the wall layout is saved |
| `10-alert-toast.png` | Alert toast (critical, with crop) plus the alert panel row with `latency_ms` | at the moment of a watchlist hit (use `GJ 27 XY 3456` per the video script) |
| `11-output-report.png` | Output report PDF cover: window in IST, counts per class, quality section | after generating the report |
| `12-systems-unaffected.png` (optional) | Health page showing one reader per relay path | any time |

Rules: same version tag as the videos (`v1.0-phase1`), same hosted URL, no credentials or `.env` values visible, IST timestamps visible where they occur, and the same files reused in PPT, HLD and README (consistency item in `submission-checklist.md`).
