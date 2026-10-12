# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Sentinel Gujarat: a unified CCTV registry + GIS, live viewing, ANPR, watchlist alerts and vehicle route reconstruction platform built by Dynatech Consultancy for the Gujarat Police Innovation Challenge 2026 ("Sentinel", Models 1 and 2). Everything runs as one Docker Compose project; no local Python is needed. Node 24 is only needed for frontend development and the docs tooling.

`README.md` is the jury-facing document (architecture, measured figures, hosted deployment). `SETUP.md` is the team clone-to-running guide.

## Sources of truth

- **`docs/CONTRACT.md` is the frozen integration contract** (services, ports, env vars, JSON shapes, DB tables, MediaMTX path naming, WebSocket messages, file layout, acceptance checks). If code, README or the plan disagree with it, the contract wins. Any deviation or extension must be **appended as a dated row in the Amendments table at the bottom** (date, builder, section, change, reason), never made silently; nothing above that table is edited.
- `MVP-PLAN.md` and `Sentinel-Gujarat-Hackathon-Guide.md` (one directory above the repo on the laptop, not committed) are the plan the contract was derived from.
- `docs/sandbox-progress.md` is an **append-only, IST-timestamped log** of verified steps against the real organiser sandbox. `docs/acceptance-log.md` records acceptance runs. Add entries; do not rewrite history.

## Commands

Every command runs from the repository root. The compose prefix is always the same:

```bash
DC="docker compose -f deploy/docker-compose.yml --env-file deploy/.env --project-directory ."
# PowerShell: function dc { docker compose -f deploy/docker-compose.yml --env-file deploy/.env --project-directory . @args }
```

The `Makefile` wraps these (`make help`). When `deploy/.env` says `COMPOSE_PROFILES=gpu`, **every** compose command must also pass `-f deploy/docker-compose.gpu.yml` (the Makefile, `deploy.sh` and `reset-db.sh` add it automatically); without it `up` recreates MediaMTX without NVENC and every H.265 camera goes black.

```bash
cp deploy/.env.example deploy/.env                # once; never commit deploy/.env
$DC --profile tools run --rm synth                # generate media/synthetic/cam_1..8.mp4 (once)
$DC up -d --build --wait --wait-timeout 600       # build + start; the API runs create_all + seed itself
$DC up -d --build api                             # rebuild one service after a code change (web, anpr-live anpr-preindex, ...)
$DC logs -f --tail 200 api
bash deploy/reset-db.sh --yes                     # drop the DB volume and re-seed (there are NO migrations)
$DC down                                          # keep data; `down --volumes` wipes everything
```

Tests and checks:

```bash
# Backend: pytest inside the api image. No database needed; tests marked `db` skip unless DATABASE_URL is set.
$DC run --rm --no-deps api pytest -q
$DC run --rm --no-deps api pytest -q tests/test_rbac.py -k scope        # one file / one test

# ANPR worker: pytest inside the anpr image (no models needed)
$DC run --rm --no-deps anpr-live python -m pytest -q anpr/tests
$DC run --rm --no-deps anpr-live python -m pytest -q anpr/tests/test_tracks.py

# Frontend: no unit tests; typecheck + lint (--max-warnings=0, no-explicit-any is an error) + build
cd frontend && npm ci && npm run typecheck && npm run lint && npm run build

# Frontend hot reload against the running stack (proxies /api, /ws, /mtx, /media, /playback)
cd frontend && VITE_API_TARGET=http://localhost npm run dev             # http://localhost:5173
cd frontend && npm run dev:mock                                         # in-memory mock API (VITE_MOCK=1), no stack needed

# Scripted acceptance checks against a running stack (CONTRACT §13.3; Node 24, no deps)
node scripts/integration_checks.mjs a     # fresh database: registry, streams, imports, RBAC, gap, health
node scripts/integration_checks.mjs b     # after the ANPR workers ran >= 2 min: reads, alerts, route, reports

# Live-stack scripts inside the api container (not collected by pytest)
$DC exec api python tests/live/phase1_api.py    # also phase2_streams.py, phase3_offline_online.py, phase4_sandbox_secrets.py

# Browser journeys / screenshots (running stack + local Google Chrome)
cd frontend && node e2e/final_journey.mjs       # -> docs/screenshots/final/
make deliverables                               # PDFs + PPTX into docs/export (Node 24, no Docker)
make gpu-config                                 # validate the gpu profile on a machine without a GPU
```

Default logins (from `deploy/.env`): `jury_admin`, `jury_operator`, `jury_viewer`, `dept_admin_police`. API docs at `http://localhost/api/docs`.

## Architecture

Compose project `sentinel` (`deploy/docker-compose.yml`; every Dockerfile's build context is the repository root):

| Service | Role |
|---|---|
| `postgres` | PostGIS 16, the only datastore (`postgis`, `pg_trgm`, `fuzzystrmatch`) |
| `mediamtx` | relay, digest-pinned v1.20.1: RTSP in over TCP, WHEP/HLS out, fMP4 recording + playback, `cam_<id>_h264` transcode paths |
| `api` | FastAPI, **single process**: REST + WebSocket fan-out + APScheduler (health poller, WS stats, matcher reload, retention cron) + in-process watchlist matcher |
| `web` | Vite build served by Caddy 2, which also reverse-proxies everything (`deploy/Caddyfile`) |
| `anpr-live`, `anpr-preindex` | plate-reading workers, profile `cpu` (default); `*-gpu` twins under profile `gpu` |
| `synth` | one-shot synthetic video generator, profile `tools` |

Data flow: camera sources (organiser sandbox RTSP or own feed) → MediaMTX path `cam_<id>` → browsers through Caddy `/mtx/*` (WHEP, HLS fallback) **and** the ANPR worker over RTSP → worker POSTs to `/api/internal/*` (`X-API-Key`, scope `internal`) → API stores `plate_reads` / `sightings` → matcher creates `alerts` → `/ws/alerts` + signed webhooks. The API drives MediaMTX through its control API (`:9997`) to create paths and to poll health; it never touches video itself.

Caddy routing: `/api/*`, `/ws/*`, `/media/*` → `api:8000`; `/mtx/*` → MediaMTX WHEP `8889` / HLS `8888` and `/playback/*` → `9996`, both behind `forward_auth` to `/api/auth/verify`; `/api/internal/*` answers **404 from the Internet** (workers reach `api:8000` inside the compose network); everything else is the SPA. MediaMTX diagnostic ports are bound to `127.0.0.1` on the host.

### Backend (`backend/app`)

- `main.py`: app factory + lifespan (extensions, `create_all`, seed when `SEED_ON_START`, scheduler). One router per resource in `api/`; `api/deps.py` provides `get_current_user`, `require_permission`, `require_role`, `require_api_key`.
- `core/config.py`: pydantic-settings, laptop-safe defaults; **refuses to start a public deployment (`COOKIE_SECURE=1` / https) on default secrets**. `core/rbac.py` is the pure permission matrix (`PERMISSIONS`); only `dept_admin` is department-scoped. `core/audit_middleware.py` writes an audit row for every mutating `/api` call. `core/urlguard.py` guards outbound URLs. `core/hashing.py`: every file under `/data` is written tmp → fsync → sha256 → rename and served via `/media/*` with `X-Sentinel-Sha256`.
- `services/` holds the logic; routers stay thin. Key pieces: `camera_importer.py` (one upsert shared by sandbox, CSV, bulk API and manual paths), `sentinel_import.py` + `adapters/sentinel_portal.py` (real organiser sandbox: catalogue → ffprobe → upsert → relay paths), `mediamtx_client.py` (relay path policy, see below), `health_poller.py` (pure `decide_status` state machine: `online / degraded / offline / not_streaming`; a camera that never streamed is `not_streaming` and never alerts), `matcher.py` (pure `MatchEngine` + DB/WS wrapper), `serializers.py` (ORM → contract JSON, masks credentials in every URL), `settings_service.py` (DB `settings` table seeded from env on first start; **afterwards the settings table wins over env**, secrets returned masked).
- `db/models.py` has every table of CONTRACT §4. **No migration tool**: `create_all` plus a few targeted start-up migrations; a schema change means `reset-db.sh`. `audit_log` is append-only through a DB trigger.
- Timestamps are stored UTC and rendered IST (`Asia/Kolkata`) in the UI and every export. Pure logic (RBAC, matcher, health state, normalisation, route) is kept DB-free so the unit tests need no PostgreSQL.

### Relay path policy and organiser etiquette

The organiser relay (`103.250.160.189`) refuses bursts of connections and answers a DESCRIBE in 4–38 s. `mediamtx_client.py` therefore gives cameras that are actually processed (`anpr_enabled`, own gate) a **persistent** relay source (one upstream connection, MediaMTX reconnects itself every 5 s) and every other camera an on-demand source with a 10-minute close-after. Every operation that opens an upstream connection (path create/switch, health probe, worker decoder dial) goes through a `DialGate` that spaces dials 3 s apart; the health poller probes at most `HEALTH_PROBE_MAX` idle cameras per tick and one camera at most every `HEALTH_PROBE_MIN_INTERVAL_S`. Do not add retry loops, parallel probes or bypasses that dial the organiser directly. H.265 and B-frame H.264 cameras get a `cam_<id>_h264` re-encode because WebRTC/HLS cannot play them raw.

### ANPR worker (`anpr/`)

One process = N ffmpeg decoder threads (`decode.py`: `-rtsp_transport tcp`, no ffprobe before an RTSP dial, newest frame only, 2→30 s backoff, `DialGate`) + **one** inference loop (`pipeline.py`) + one HTTP sender thread (`client.py`) + one control thread (config reload, heartbeat). Per frame: `detector.py` (`onnx` YOLO-v9-t | `contour` | `auto`, plus `TiledDetector`, `OsdMask`, `StaticBoxFilter`) → `tracks.py` best-shot `PlateTracker` → `ocr.py` (`paddle` | `fast_plate` | `ensemble`) → `normalise.py` → `voting.py` (3 s char-wise vote) → `sightings.py` → batched POSTs. The worker never touches the database or the MediaMTX API.

Config precedence (`config.py`): CLI > env > server `/internal/anpr-config` settings > `config.yml` > defaults; a value pinned by env or CLI is never overridden by the server. A new tunable needs an entry in `ENV_MAP`, the compose `x-anpr-env` block and `deploy/.env.example`. Model weights are SHA-256 verified at build (`weights/HASHES.txt`, `weights/download.py`) and again at start; missing ONNX weights start the worker **degraded** (`contour-fallback` in the heartbeat) rather than crashing.

`normalise.py`, `backend/app/services/plates.py` and `frontend/src/utils/plate.ts` implement the same CONTRACT §3.4 algorithm (incl. the step 5a state-code rule); the Python pair shares 20 contract vectors + `STATE_VECTORS` (`anpr/tests/test_normalise.py`, `backend/tests/test_normalise.py`); change all three or none.

### Frontend (`frontend/src`)

React 18 + Vite + strict TypeScript + Ant Design 5, zustand stores (`store/`), TanStack Query, hls.js + WHEP in `components/StreamPlayer.tsx`. Leaflet is used through the in-repo bindings in `components/leaflet.tsx` (react-leaflet was deliberately dropped for licence reasons; do not add it back). `api/client.ts` is the single fetch wrapper (Bearer token, contract error shape `{detail, code, errors[]}`, 401 → logout). `router.tsx` guards routes with `RequirePermission` using the same permission names as backend `rbac.PERMISSIONS`. `ws/` hooks wrap `/ws/alerts`, `/ws/reads/{id}`, `/ws/health`. `mock/` is the in-memory API for `VITE_MOCK=1`. Route list and design tokens are specified in CONTRACT §11.

### Mock sandbox versus the real one

`MOCK_SANDBOX=1` (laptop default) makes the API serve an organiser-shaped catalogue at `/api/mock-sandbox/api/ingest` and MediaMTX publish `media/synthetic/cam_<n>.mp4` as `stream/<n>`, so the exact import → relay → ANPR → alert → route path runs locally; `scripts/integration_checks.mjs` and the measured-figures table rely on it. `CATALOGUE_SOURCE=sentinel_portal` (or Settings → Catalogue) switches to the real organiser sandbox: 30 cameras `cam01..cam30`, catalogue copy in `media/cameras.json`, team location enrichment in `media/cameras_enrichment.csv`. The real feeds are looped night recordings; see README "Known limitations" and `docs/anpr-accuracy.md` before quoting any accuracy figure.

## Conventions and gotchas

- **Secrets.** `deploy/.env` is git-ignored and holds everything, including the organiser stream credentials (`SANDBOX_STREAM_EMAIL` / `SANDBOX_STREAM_PASSWORD`). Never print, log, commit, or screenshot them, and keep the masking (`rtsp://<user>:***@…`) intact in every response, export, audit row, import warning and log line; `tests/live/phase4_sandbox_secrets.py` scans for leaks.
- JSON keys are `snake_case`, IDs are integers, timestamps are ISO-8601 UTC with a trailing `Z`; error bodies are `{detail, code, errors[]}` (CONTRACT §3).
- A new runtime setting needs: an env default in `core/config.py`, a `settings_service` entry, `deploy/.env.example` documentation and a CONTRACT amendment row.
- Line endings are LF everywhere, enforced by `.gitattributes`; do not set `core.autocrlf`. Python: 4 spaces, 100 columns. TS/JS/YAML: 2 spaces. Makefile: tabs.
- Large binaries (videos, ONNX weights, crops, PDFs, screenshots) are git-ignored by design; see SETUP.md §9 for how each is regenerated.
- The GPU profile cannot run on the laptop; validate compose changes with `make gpu-config` and keep `deploy/docker-compose.gpu.yml` in sync with the cpu services.
- `scripts/` holds one-off measurement tools for the real feed (`collect_crops.py`, `eval_ocr_bank.py`, `measure_live_anpr.mjs`, `survey_anpr.py`, `loopcheck.mjs`); they write under `media/anpr_*` and `media/loopcheck`, which stay on the laptop.
