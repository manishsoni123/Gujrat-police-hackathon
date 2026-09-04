# Screenshots for the submission documents

Two sets live here (PNG files are git-ignored; keep the final set in the Drive folder `09_Screenshots/`):

1. **Automated full-page captures** — `make screenshots` (Playwright container, logs in as `jury_admin`, visits every route) writes `<nn>_<route>.png`, e.g. `01_login.png`, `02_dashboard.png`, `03_cameras.png`, `05_map.png`, `06_wall.png`. Use them for the HLD and README where a whole page is wanted.
2. **Moment shots** taken by hand (Chrome, 1920×1080, zoom 100 %, no bookmarks bar) at the instants the automated crawl cannot catch. These are the names referenced by `PRESENTATION-OUTLINE.md`:

3. **Live-stack captures** — `cd frontend && node e2e/live_screenshots.mjs` (Playwright + the local Google Chrome, real login form, 1440×900, IST) writes `live/<key>.png`: every route, the 4/9/16 wall with WebRTC state, the alert toast for a plate added live, a 1024 px tablet pass and the `jury_viewer` role (45 files on 5 Sept 2026). OSM tiles are fetched by the script with an identifying User-Agent because the tile servers refuse automated browsers. Moment-shot equivalents: `01` → `live/login.png`, `02`/`08` → `live/map.png`, `03` → `live/dashboard.png`, `04` → `live/camera-detail.png` (and `camera-detail-h265.png`), `05` → `live/route.png` (`GJ 27 XY 3456`, four Gandhinagar cameras), `09` → `live/wall-9.png`, `10` → `live/alert-toast.png`, `12` → `live/health.png`; `06`, `07` and `11` remain manual (PDF footer, import summary after the click, report cover).

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
| `09-wall-9.png` | 9-tile wall: 8 sandbox cameras + own gate, "Two systems" badge, tile captions "WebRTC" | after the wall layout is saved |
| `10-alert-toast.png` | Alert toast (critical, with crop) plus the alert panel row with `latency_ms` | at the moment of a watchlist hit (use `GJ 27 XY 3456` per the video script) |
| `11-output-report.png` | Output report PDF cover: window in IST, counts per class, quality section | after generating the report |
| `12-systems-unaffected.png` (optional) | Health page showing one reader per relay path | any time |

Rules: same version tag as the videos (`v1.0-phase1`), same hosted URL, no credentials or `.env` values visible, IST timestamps visible where they occur, and the same files reused in PPT, HLD and README (consistency item in `submission-checklist.md`).
