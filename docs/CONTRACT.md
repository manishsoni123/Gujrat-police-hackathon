# Sentinel Gujarat – Integration Contract

**Product:** Sentinel Gujarat · **Team:** Dynatech Consultancy · **Version:** 1.0.0-phase1 · **Status:** frozen for the Phase 1 build (4–7 Sept 2026)

This file is the single source of truth for the five parallel builders (backend, frontend, ANPR worker, deploy, docs). It is derived from `MVP-PLAN.md` sections 1.3, 1.4, 2.1–2.3, 3.x and 5.1–5.11 and fills in every detail the plan leaves open. **If this file and your own assumption disagree, this file wins. If this file and `MVP-PLAN.md` disagree, this file wins (it is the later, more specific document).** Anything not covered here is a decision for the builder who owns the directory, and must be added here in a short "Amendments" section at the bottom, never silently.

Directory ownership:

| Builder | Owns | Reads |
|---|---|---|
| backend | `backend/**` (API, DB, seeds, tests, mock catalogue) | §1–§10, §12, §13 |
| frontend | `frontend/**` | §2, §3, §5, §9, §10, §11, §13 |
| anpr | `anpr/**`, `scripts/make_synthetic_videos.py`, `scripts/survey_sandbox.py` | §1, §3.4, §7, §8, §10, §12 |
| deploy | `deploy/**`, every `Dockerfile`, `frontend/Dockerfile`, `scripts/Dockerfile`, `media/` layout | §1, §8, §10, §13 |
| docs | `docs/**` except this file, `README.md` | everything |

Global conventions (apply everywhere):

- All JSON keys are `snake_case`. Enums are lowercase strings unless stated (codec values are uppercase, e.g. `H264`). IDs are integers (`bigint`). Booleans are JSON booleans, never `"1"`.
- All timestamps in JSON are ISO-8601 **UTC** with millisecond precision and a trailing `Z`: `2026-09-04T10:15:30.123Z`. Inputs accept any ISO-8601 string; a naive input (no offset) is interpreted as UTC. The UI and every CSV/PDF render **IST** (`Asia/Kolkata`, `+05:30`) as `dd MMM yyyy, HH:mm:ss IST` (e.g. `04 Sep 2026, 15:45:30 IST`). Date-only values (`install_date`, `amc_expiry`) are `YYYY-MM-DD`.
- Latitude/longitude are decimal degrees, WGS-84, 6 decimals max, `lat` then `lon` in every pair/array (`[lat, lon]`). GeoJSON is the exception and follows the GeoJSON spec (`[lon, lat]`).
- Sizes: `_bytes`, durations: `_s` (seconds, float or int), `_ms` (milliseconds, int), distances: `_m` / `_km`.
- Every REST path below is relative to the prefix `/api` unless it starts with `/ws`, `/mtx`, `/playback`, `/media` or `/healthz`.
- Product name in every UI string, title, PDF header and document: **Sentinel Gujarat**. Version string everywhere: `1.0.0-phase1` (git tag `v1.0-phase1`).
- Open source only. Every runtime component and its licence is listed in `docs/LICENCES.md` (docs builder). No cloud AI APIs anywhere.

---

## 1. Services, ports and environment

### 1.1 Compose services

Compose file: `deploy/docker-compose.yml`, project name `sentinel`. **Service names are the hostnames** inside the compose network. Build context for every image is the repository root; the Dockerfile path is `<dir>/Dockerfile`. The canonical invocation is run **from the repository root**:

```
docker compose -f deploy/docker-compose.yml --env-file deploy/.env --project-directory . up -d --build
```

`deploy/deploy.sh` wraps this (copies `.env.example` → `.env` if missing, builds, starts, waits for `api` healthy, prints the URL and jury usernames).

| Service | Image / build | Internal ports | Published (host) ports | Profile | Purpose |
|---|---|---|---|---|---|
| `postgres` | `postgis/postgis:16-3.4` | 5432 | none (a local `docker-compose.override.yml` may publish 5432) | always | Only datastore. Extensions `postgis`, `pg_trgm`, `fuzzystrmatch` created by the API at startup. Volume `pgdata:/var/lib/postgresql/data` |
| `mediamtx` | `bluenviron/mediamtx:latest-ffmpeg` (deploy pins the digest in a `docker-compose.yml` comment and in `docs/LICENCES.md`) | 8554 RTSP, 8889 WHEP/WebRTC, 8888 HLS, 9996 playback, 9997 API, 8189 ICE mux | **8189/udp + 8189/tcp** (WebRTC ICE); 8554/tcp for debugging only (commented out in prod) | always | Relay. Config `deploy/mediamtx.yml` mounted at `/mediamtx.yml`; volumes `recordings:/recordings`, `./media:/media:ro` |
| `api` | build `backend/Dockerfile` (`python:3.11-slim` + apt `ffmpeg`, `curl`) | 8000 | none | always | FastAPI: REST + WebSocket + APScheduler (health poller, retention purge, webhook retries) + watchlist matcher. Volumes `sentinel_data:/data`, `./media:/media:ro`, `recordings:/recordings:ro`. Depends on `postgres` (healthy) and `mediamtx` (started) |
| `web` | build `frontend/Dockerfile` (stage 1 `node:24-alpine` runs `npm ci && npm run build`; stage 2 `caddy:2` copies `dist` to `/srv` and `deploy/Caddyfile` to `/etc/caddy/Caddyfile`) | 80, 443 | **80/tcp, 443/tcp** | always | Caddy: static SPA + reverse proxy (§1.2). Volumes `caddy_data:/data`, `caddy_config:/config` |
| `anpr-live` | build `anpr/Dockerfile`, build arg `ANPR_BASE=cpu` | none | none | `cpu` (default) | ANPR worker, `ANPR_MODE=live`, `CPU=1` |
| `anpr-preindex` | build `anpr/Dockerfile`, `ANPR_BASE=cpu` | none | none | `cpu` (default) | ANPR worker, `ANPR_MODE=preindex`, `CPU=1` |
| `anpr-live-gpu` | build `anpr/Dockerfile`, `ANPR_BASE=gpu` (`pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime` base, ffmpeg binary copied from `jrottenberg/ffmpeg:6.1-nvidia2204`) | none | none | `gpu` | Same worker, `CPU=0`, `deploy.resources.reservations.devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]` |
| `anpr-preindex-gpu` | as above | none | none | `gpu` | `ANPR_MODE=preindex`, GPU |
| `synth` | build `scripts/Dockerfile` (`python:3.11-slim` + `opencv-python-headless`, `numpy`, `pillow`; apt `ffmpeg`, `fonts-dejavu-core`) | none | none | `tools` | One-shot: `python scripts/make_synthetic_videos.py` writing to `./media/synthetic` (bind `./media:/media`). Run: `docker compose -f deploy/docker-compose.yml --project-directory . --profile tools run --rm synth` |

Rules:

- `COMPOSE_PROFILES` in `deploy/.env` selects `cpu` (default) or `gpu`. Exactly one ANPR pair runs, never both.
- The ANPR image has **two build stages** selected by build arg `ANPR_BASE=cpu|gpu`. `cpu` → `python:3.11-slim` + apt `ffmpeg` + `onnxruntime` (CPU) + `paddlepaddle` (CPU) + `paddleocr==2.8.*`. `gpu` → CUDA base + `onnxruntime-gpu` + the same PaddleOCR CPU wheel + NVIDIA ffmpeg. Both stages install the same `anpr/requirements.txt` except the runtime wheels. `CPU=1` at run time removes `-hwaccel cuda` and forces `CPUExecutionProvider` even inside the GPU image.
- `restart: unless-stopped` on every long-running service. Healthchecks: `postgres` → `pg_isready -U $POSTGRES_USER -d $POSTGRES_DB`; `api` → `curl -fsS http://localhost:8000/healthz`; `mediamtx` → `wget -qO- http://127.0.0.1:9997/v3/paths/list` (drop if the image lacks wget); `web` → `wget -qO- http://127.0.0.1/healthz`.
- Named volumes: `pgdata`, `sentinel_data` (mounted at `/data` in `api`), `recordings` (mounted at `/recordings` in `mediamtx`, read-only in `api`), `caddy_data`, `caddy_config`. Bind mount: `./media` (repo root; contains `synthetic/` and `own/`) at `/media` in `mediamtx`, `api`, `synth`.
- Docker Desktop on the laptop: if `docker info` fails, wait `Start-Sleep -Seconds 15` in a loop up to 20 tries before giving up.
- Local frontend dev: `npm run dev` on `http://localhost:5173`; `vite.config.ts` proxies `/api`, `/ws` (`ws: true`), `/mtx`, `/playback`, `/media`, `/healthz` to `http://localhost` (Caddy). The API's `CORS_ORIGINS` default includes `http://localhost:5173`.
- Every container logs JSON lines to stdout (`docker compose logs`). No log files inside containers.

### 1.2 Caddy routing (`deploy/Caddyfile`)

Site address is `{$CADDY_SITE_ADDRESS}` (default `:80`, plain HTTP; on the VM set it to the domain, e.g. `sentinel.dynatech.in`, and Caddy obtains a Let's Encrypt certificate automatically). Handle blocks in this order:

| Public path | Upstream | Notes |
|---|---|---|
| `/api/*` | `api:8000` (prefix kept) | REST, `/api/docs`, `/api/openapi.json`, `/api/redoc`, `/api/mock-sandbox/*` |
| `/healthz` | `api:8000/healthz` | Unauthenticated liveness |
| `/ws/*` | `api:8000` (prefix kept) | WebSocket upgrade passes through |
| `/media/*` | `api:8000` (prefix kept) | Files under `/data`; auth checked by the API (§10) |
| `/mtx/<path>/whep` and `/mtx/<path>/whep/<session>` (`@whep path_regexp whep ^/mtx/(.+)/whep(/[^/]+)?$`) | `mediamtx:8889/{re.whep.1}/whep{re.whep.2}` | WHEP `POST` (offer), `PATCH` (ICE trickle), `DELETE` (teardown), `OPTIONS`. **Guarded by `forward_auth api:8000 { uri /api/auth/verify }`** (§2.5) |
| `/mtx/*` (everything else) | `mediamtx:8888/*` (strip `/mtx`) | HLS: `index.m3u8`, `*.mp4`, `*.m4s`, `*.ts`. Same `forward_auth` |
| `/playback/*` | `mediamtx:9996/*` (strip `/playback`) | `list`, `get`. Same `forward_auth` |
| `/*` | `file_server` from `/srv` with `try_files {path} /index.html` | SPA. `Cache-Control: no-cache` for `index.html`; `max-age=31536000, immutable` for `/assets/*` |

Headers on every response: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin`. `encode gzip zstd`. `request_body { max_size 200MB }`. The ANPR worker posts straight to `api:8000` inside the network, never through Caddy.

### 1.3 Environment variables

`deploy/.env.example` is generated from this table (deploy builder). Every variable has a default that works on the laptop with `COMPOSE_PROFILES=cpu`. Values marked **(secret)** must be changed for the hosted demo; `deploy.sh` warns when they still equal the default.

**Compose / Postgres**

| Variable | Default | Used by | Purpose |
|---|---|---|---|
| `COMPOSE_PROFILES` | `cpu` | compose | `cpu` or `gpu` |
| `POSTGRES_USER` | `sentinel` | postgres, api | DB user (owner) |
| `POSTGRES_PASSWORD` (secret) | `sentinel` | postgres, api | DB password |
| `POSTGRES_DB` | `sentinel` | postgres, api | DB name |
| `DATABASE_URL` | `postgresql+asyncpg://sentinel:sentinel@postgres:5432/sentinel` | api | SQLAlchemy async URL (compose interpolates from the three above) |

**API (`api` service)**

| Variable | Default | Purpose |
|---|---|---|
| `APP_VERSION` | `1.0.0-phase1` | Returned by `/healthz`, printed in PDFs and the About page |
| `JWT_SECRET` (secret) | `change-me-sentinel-gujarat-2026-please` | HS256 signing key (min 32 chars) |
| `JWT_EXPIRE_HOURS` | `8` | Access token lifetime |
| `PUBLIC_BASE_URL` | `http://localhost` | Absolute links in webhooks, PDFs, Telegram |
| `CORS_ORIGINS` | `http://localhost,http://localhost:5173,http://127.0.0.1:5173` | Comma list. On the VM: `https://<domain>` only |
| `COOKIE_SECURE` | `0` | `1` on HTTPS: adds `Secure` to the session cookie |
| `DATA_DIR` | `/data` | Root of stored files (§10) |
| `MEDIA_DIR` | `/media` | Read-only synthetic/own videos |
| `RECORDINGS_DIR` | `/recordings` | Read-only view of MediaMTX recordings (disk-usage tile only) |
| `MEDIAMTX_API_URL` | `http://mediamtx:9997` | Control API |
| `MEDIAMTX_RTSP_URL` | `rtsp://mediamtx:8554` | Base for relay URLs handed to the ANPR worker |
| `MEDIAMTX_RTSP_LOCAL_URL` | `rtsp://localhost:8554` | Base used inside `runOnDemand` commands that MediaMTX itself executes |
| `MEDIAMTX_HLS_URL` | `http://mediamtx:8888` | Internal HLS base (server-side probes only) |
| `MEDIAMTX_PLAYBACK_URL` | `http://mediamtx:9996` | Playback server |
| `MEDIAMTX_TRANSCODE` | `cpu` | `cpu` → `-c:v libx264 -preset veryfast -tune zerolatency`; `nvenc` → `-hwaccel cuda -c:v h264_nvenc -preset p1` in `_h264` paths. Set `nvenc` on the GPU VM |
| `MOCK_SANDBOX` | `1` | `1` serves `GET /api/mock-sandbox/api/ingest` (§6). `0` on the VM once real credentials exist |
| `SANDBOX_BASE_URL` | `http://api:8000/mock-sandbox` | Initial value of setting `catalogue.base_url`; the importer calls `{base_url}/api/ingest` |
| `SANDBOX_AUTH_TYPE` | `none` | Initial `catalogue.auth_type`: `none`, `basic`, `bearer`, `header` |
| `SANDBOX_USERNAME` | (empty) | Initial `catalogue.auth_username` (basic) |
| `SANDBOX_PASSWORD` (secret) | (empty) | Initial `catalogue.auth_password` (basic) or token (bearer) |
| `SANDBOX_AUTH_HEADER` | (empty) | Initial `catalogue.auth_header` as `Name: value` when `header` |
| `SANDBOX_TIMEOUT_S` | `30` | Catalogue HTTP timeout |
| `INTERNAL_API_KEY` (secret) | `sk_internal0000000000000000000000000000000000` | Seeded API key, scope `internal` (ANPR worker → API). Must match `sk_[0-9a-z]{40}` |
| `BULK_API_KEY` (secret) | `sk_bulk00000000000000000000000000000000000000` | Seeded API key, scope `bulk` (`POST /api/v1/cameras/bulk`) |
| `JURY_ADMIN_PASSWORD` (secret) | `Sentinel@Admin2026` | Seed user `jury_admin` |
| `JURY_OPERATOR_PASSWORD` (secret) | `Sentinel@Ops2026` | Seed user `jury_operator` |
| `JURY_VIEWER_PASSWORD` (secret) | `Sentinel@View2026` | Seed user `jury_viewer` |
| `DEPT_ADMIN_PASSWORD` (secret) | `Sentinel@Police2026` | Seed user `dept_admin_police` |
| `SEED_ON_START` | `1` | API runs `create_all` + extensions + idempotent seed on startup. `python -m app.seed` does the same manually |
| `HEALTH_POLL_SECONDS` | `60` | Health poller period |
| `HEALTH_OFFLINE_AFTER` | `3` | Consecutive not-ready checks before `offline` |
| `HEALTH_PROBE_MAX` | `20` | Idle on-demand cameras probed with `ffprobe` per tick (§5.6) |
| `HEALTH_PROBE_TIMEOUT_S` | `6` | ffprobe timeout |
| `ANPR_AUTO_ENABLE_MAX` | `12` | On import, **new** cameras with catalogue `live=true` get `anpr_enabled=true` until this many cameras are enabled |
| `ALERT_SUPPRESSION_SECONDS` | `60` | Initial `alerts.suppression_s` |
| `FUZZY_ALERT_MIN_CONF` | `0.8` | Initial `alerts.fuzzy_min_conf` |
| `ALERT_ESCALATE_MINUTES` | `5` | Unacknowledged for longer → `escalated=true` |
| `ROUTE_SPEED_FLAG_KMH` | `150` | Initial `route.speed_flag_kmh` |
| `ROUTE_DEFAULT_WINDOW_HOURS` | `24` | Initial `route.default_window_h` |
| `GAP_COVERAGE_RADIUS_M` | `150` | Initial `gap.coverage_radius_m` |
| `GAP_POI_RADIUS_M` | `300` | Initial `gap.poi_radius_m` |
| `GAP_GRID_M` | `500` | Initial `gap.grid_m` |
| `AGEING_YEARS` | `5` | Initial `gap.ageing_years` |
| `GAP_CACHE_SECONDS` | `300` | Gap-analysis cache TTL |
| `RETENTION_DAYS_FRAMES` | `7` | Initial `retention.days_frames` |
| `RETENTION_DAYS_READS` | `30` | Initial `retention.days_reads` |
| `RETENTION_DAYS_CLIPS` | `90` | Initial `retention.days_clips` (also reports and exports) |
| `RETENTION_JOB_HOUR_UTC` | `21` | Daily purge at 21:00 UTC = 02:30 IST |
| `SNAPSHOT_STALE_SECONDS` | `10` | Snapshot older than this is `stale` in `/streams/{id}` |
| `WS_STATS_INTERVAL_S` | `10` | `stats` envelope period on `/ws/alerts` and `/ws/health` |
| `TELEGRAM_BOT_TOKEN` (secret) | (empty) | Initial `notify.telegram_bot_token` (P2; empty = disabled) |
| `TELEGRAM_CHAT_ID` | (empty) | Initial `notify.telegram_chat_id` |
| `LOGIN_RATE_LIMIT_PER_MIN` | `10` | Per-IP login attempts → 429 |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `TZ_DISPLAY` | `Asia/Kolkata` | Time zone for CSV/PDF rendering (never change) |

**ANPR worker (`anpr-*` services)** — `anpr/config.yml` holds the same keys in YAML (lowercase); environment overrides YAML; CLI flags override both.

| Variable | Default (cpu / gpu) | Purpose |
|---|---|---|
| `CPU` | `1` / `0` | `1`: software decode, ONNX CPU provider, `ANPR_MAX_CAMERAS` default 3 |
| `ANPR_MODE` | `live` | `live` or `preindex` |
| `API_URL` | `http://api:8000` | Base URL of the API (internal network) |
| `INTERNAL_API_KEY` | same as API | Sent as `X-API-Key` |
| `RTSP_BASE` | `rtsp://mediamtx:8554` | Fallback if the config endpoint omits `rtsp_url` (it never does) |
| `ANPR_CAMERAS` | (empty = all from config endpoint) | Comma list of camera ids to restrict this worker to (Phase 2: split cameras across two hosts) |
| `ANPR_MAX_CAMERAS` | `3` / `12` | Hard cap on simultaneous decoders; lowest ids first |
| `ANPR_FPS` | `5` | Live decode fps (`fps=` filter) |
| `PREINDEX_FPS` | `1` | Pre-index decode fps with `-skip_frame nokey` |
| `PREINDEX_SKIP_LIVE` | `1` | Pre-index worker skips cameras whose config `mode` is `both` (already covered by the live worker) |
| `FRAME_WIDTH` | `960` | `scale=960:-1` |
| `ANPR_DETECTOR` | `auto` | `auto` (ONNX, then contour fallback when ONNX returns no box), `onnx`, `contour` (§7.7) |
| `ANPR_DET_MODEL` | `/app/weights/yolo-v9-t-384-license-plates-end2end.onnx` | Primary detector (open-image-models, Apache-2.0) |
| `ANPR_DET_CONF` | `0.4` | Detector confidence threshold |
| `ANPR_MIN_PLATE_W` | `60` | Skip boxes narrower than this (pixels at 960 px width) |
| `ANPR_VOTE_WINDOW_S` | `3` | Char-wise voting window |
| `ANPR_MIN_READ_CONF` | `0.30` | Reads below this are not posted |
| `SIGHTING_CLOSE_S` | `15` | Silence before a sighting is closed |
| `SNAPSHOT_INTERVAL_S` | `1` | Snapshot cadence per camera (live mode only) |
| `SNAPSHOT_WIDTH` | `480` | Snapshot JPEG width |
| `POST_BATCH_S` | `1` | Batch period for `/internal/detections` |
| `CONFIG_RELOAD_S` | `60` | Re-fetch `/internal/anpr-config` |
| `HEARTBEAT_S` | `15` | `POST /internal/heartbeat` period |
| `OBJECT_DETECT` | `1` | `0` disables YOLOX counting (never runs in preindex) |
| `OBJECT_MODEL` | `/app/weights/yolox_s.onnx` | YOLOX-s COCO (Apache-2.0) |
| `OBJECT_CONF` | `0.35` | Object detector threshold |
| `OBJECT_EVERY_N` | `5` | Object detection every Nth decoded frame |
| `OCR_LANG` | `en` | PaddleOCR language |
| `RECONNECT_MIN_S` / `RECONNECT_MAX_S` | `2` / `30` | Exponential backoff bounds for decoder restart |
| `LOG_LEVEL` | `INFO` | |

**MediaMTX (`mediamtx` service)** — MediaMTX reads `MTX_<UPPERCASEKEY>` env vars as overrides of `mediamtx.yml`.

| Variable | Default | Purpose |
|---|---|---|
| `MTX_WEBRTCADDITIONALHOSTS` | `127.0.0.1` | Public IP (or `127.0.0.1` on the laptop) advertised in ICE candidates |
| `MTX_LOGLEVEL` | `info` | |

**Web (`web` service)**

| Variable | Default | Purpose |
|---|---|---|
| `CADDY_SITE_ADDRESS` | `:80` | `:80` locally; the FQDN on the VM (auto-TLS) |
| `CADDY_EMAIL` | (empty) | ACME contact |

**Synthetic generator (`synth` service)**

| Variable | Default | Purpose |
|---|---|---|
| `SYNTH_CAMERAS` | `8` | Number of videos (ids 1..N, must equal the `live=true` mock cameras) |
| `SYNTH_SECONDS` | `90` | Loop length |
| `SYNTH_SEED` | `42` | Deterministic plate set |
| `SYNTH_OUT` | `/media/synthetic` | Output directory |

---

## 2. Authentication and authorisation

### 2.1 Login

`POST /api/auth/login` — unauthenticated, rate-limited (`LOGIN_RATE_LIMIT_PER_MIN` per IP → `429 rate_limited`).

Request:

```json
{"username": "jury_admin", "password": "Sentinel@Admin2026"}
```

Response `200`:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_at": "2026-09-04T18:15:30.000Z",
  "user": {
    "id": 1,
    "username": "jury_admin",
    "full_name": "Jury Administrator",
    "role": "admin",
    "department_id": null,
    "department_name": null,
    "district": null
  }
}
```

The response **also sets a cookie** `sg_session=<same JWT>; Path=/; HttpOnly; SameSite=Lax; Max-Age=28800` (+ `Secure` when `COOKIE_SECURE=1`). The cookie exists only so that `<img>`, `<video>`, hls.js and WHEP requests (which cannot set an `Authorization` header) are authenticated on `/media/*`, `/mtx/*` and `/playback/*`. The SPA keeps the token in memory + `localStorage` key `sg.token` and sends `Authorization: Bearer <token>` on every XHR/WS. `POST /api/auth/logout` clears the cookie (`Max-Age=0`) and writes audit `auth.logout`; the SPA also drops the token.

Failure: `401 {"detail":"Invalid username or password","code":"unauthorized"}` (same message for unknown user, wrong password and inactive user; audit `auth.login_failed` with the attempted username). Usernames are case-insensitive and stored lowercase.

`GET /api/auth/me` → the `user` object above plus `"permissions": [...]` (the permission names from §2.4 the role holds) so the UI hides actions without hard-coding the matrix.

`POST /api/auth/change-password` `{current_password, new_password}` → `204`. Any authenticated user. Policy: ≥ 10 chars, at least one letter and one digit. Audit `user.change_password`.

### 2.2 JWT

- HS256, key `JWT_SECRET`, lifetime `JWT_EXPIRE_HOURS` (8 h). No refresh tokens; the SPA redirects to `/login` on the first `401`.
- Claims: `{"sub": "<user id as string>", "username": "jury_admin", "role": "admin", "department_id": null, "district": null, "iat": 1725444930, "exp": 1725473730, "jti": "<uuid4>"}`.
- Accepted from, in order: `Authorization: Bearer <jwt>`, cookie `sg_session`, query `?token=<jwt>` (WebSocket and download links only — the SPA never uses it for API calls).
- The user row is re-read on each request (single process, cheap); an inactive user or a changed role takes effect immediately.

### 2.3 Roles and data scoping

Roles: `admin` · `dept_admin` · `operator` · `viewer` (column `users.role`).

Scoping is applied in SQL, not only in the UI:

- `dept_admin` sees and edits only cameras where `cameras.department_id = user.department_id` **and**, if `user.district` is not null, `cameras.district = user.district`. Every camera-derived resource (health log, reads, sightings, alerts, events, object counts, recordings, clips, streams, dashboard numbers, gap analysis, reports, `/ws/*` broadcasts) is filtered through that camera set. The watchlist is statewide and **not** scoped. Vehicle search / route for a dept_admin only returns sightings from their cameras.
- `admin`, `operator`, `viewer` are statewide regardless of `department_id` (informational for them).

### 2.4 Permission matrix (exact)

Permission names are what `GET /auth/me` returns and what the backend dependency `require_permission("...")` checks.

| Permission | Endpoints | admin | dept_admin | operator | viewer |
|---|---|---|---|---|---|
| `cameras.read` | `GET /cameras*`, `GET /geo/*`, `GET /streams/*`, `GET /health/*`, `GET /gap-analysis`, `GET /recordings/*`, `GET /object-counts`, `GET /zones*` | ✓ | ✓ (scoped) | ✓ | ✓ |
| `cameras.write` | `POST/PUT/DELETE /cameras*`, `POST /cameras/import/*`, `PUT /cameras/{id}/maintenance` | ✓ | ✓ (scoped; cannot move a camera to another department) | – | – |
| `cameras.export` | `GET /cameras/export`, `GET /gap-analysis/export` | ✓ | ✓ (scoped) | ✓ | – |
| `analytics.read` | `GET /detections*`, `GET /vehicles/search`, `GET /vehicles/{plate}/route`, `GET /alerts*`, `GET /events*`, `GET /dashboard/*`, `GET /watchlist*`, `GET /clips*`, `GET /evidence/verify`, `GET /reports/quality` (JSON), `GET /qa/sample` | ✓ | ✓ (scoped) | ✓ | ✓ |
| `watchlist.write` | `POST/PUT/DELETE /watchlist*`, `POST /watchlist/import/csv` | ✓ | ✓ | ✓ | – |
| `alerts.ack` | `POST /alerts/{id}/ack`, `POST /alerts/{id}/close` | ✓ | ✓ (scoped) | ✓ | – |
| `route.confirm` | `POST /vehicles/{plate}/confirm` | ✓ | ✓ (scoped) | ✓ | – |
| `events.write` | `POST /events`, `POST /qa/labels`, `POST /clips` | ✓ | ✓ (scoped) | ✓ | – |
| `reports.export` | `GET /reports/detections`, `GET /vehicles/{plate}/route.pdf`, `GET /reports/quality?format=pdf`, `GET /reports/history` | ✓ | ✓ (scoped) | ✓ | – |
| `external.lookup` | `GET /external/*` | ✓ | ✓ | ✓ | – |
| `zones.write` | `POST/PUT/DELETE /zones*` | ✓ | ✓ (scoped) | – | – |
| `admin.users` | `/users*` | ✓ | – | – | – |
| `admin.audit` | `GET /audit*` | ✓ | – | – | – |
| `admin.apikeys` | `/api-keys*` | ✓ | – | – | – |
| `admin.settings` | `GET/PUT /settings`, `/webhooks*` | ✓ | – | – | – |
| `settings.read_public` | `GET /settings/public` | ✓ | ✓ | ✓ | ✓ |

A `403` is `{"detail":"Insufficient role","code":"forbidden"}`. A scoped miss (dept_admin asking for another department's camera) is `404 not_found`, so existence is not leaked.

### 2.5 `GET /api/auth/verify` (Caddy forward_auth)

Returns `204` when a valid JWT is present (header, cookie or `?token=`), else `401`. On success adds response headers `X-Sentinel-User: <username>` and `X-Sentinel-Role: <role>` (Caddy `copy_headers` them upstream). Not audited (called for every HLS segment). Used by Caddy for `/mtx/*` and `/playback/*`.

### 2.6 API keys (`X-API-Key`)

- Format: `sk_` + 40 lowercase alphanumerics (`^sk_[0-9a-z]{40}$`). Stored as `sha256(key)` hex in `api_keys.key_hash`; shown **once** at creation.
- Scopes: `bulk` (only `POST /api/v1/cameras/bulk`) and `internal` (only `/api/internal/*`). Wrong scope → `403 forbidden`. Missing/invalid → `401 unauthorized`.
- Seed creates two keys from `BULK_API_KEY` and `INTERNAL_API_KEY` (names `seed-bulk`, `seed-internal`, `created_by` = jury_admin). Re-running the seed with a changed env value rotates the hash of the same row.
- Requests authenticated by API key have no user; audit rows record `user_id = null`, `role = 'apikey'`, `actor = 'apikey:<name>'`.

### 2.7 Seed users

| username | role | department | district | password env (default) | purpose |
|---|---|---|---|---|---|
| `jury_admin` | admin | – | – | `JURY_ADMIN_PASSWORD` (`Sentinel@Admin2026`) | Jury: everything |
| `jury_operator` | operator | – | – | `JURY_OPERATOR_PASSWORD` (`Sentinel@Ops2026`) | Jury: watchlist, alerts, route, reports; no camera edits |
| `jury_viewer` | viewer | – | – | `JURY_VIEWER_PASSWORD` (`Sentinel@View2026`) | Jury: read-only |
| `dept_admin_police` | dept_admin | POLICE | – | `DEPT_ADMIN_PASSWORD` (`Sentinel@Police2026`) | Shows department scoping (sees only Police cameras) |

Passwords are bcrypt (cost 12). The README (docs builder) prints the usernames and says the passwords are in `deploy/.env`; the hosted demo uses non-default passwords written into the submission form, never into the repo.

---

## 3. Shared shapes: errors, pagination, filters, plate normalisation

### 3.1 Error shape

Every non-2xx JSON response:

```json
{
  "detail": "Human-readable message",
  "code": "validation_error",
  "errors": [
    {"row": 4, "field": "lat", "message": "must be between -90 and 90"}
  ]
}
```

`errors` is present only for validation/import errors; `row` is present only for CSV rows (1-based **data** row number: header = row 0, first data row = 1); bulk JSON uses `index` (0-based) instead of `row`.

| HTTP | `code` | When |
|---|---|---|
| 400 | `bad_request` | Malformed multipart, unsupported file type, bad `format=` |
| 401 | `unauthorized` | Missing/expired token or API key |
| 403 | `forbidden` | Role/scope lacks permission |
| 404 | `not_found` | Missing or out-of-scope entity |
| 409 | `conflict` | Duplicate username / `external_id` / plate, alert already closed, no recording for range |
| 413 | `too_large` | Upload > 200 MB (CSV) or > 50 MB (internal batch) |
| 422 | `validation_error` | Pydantic/body/query validation. FastAPI's default 422 body is **replaced** by this shape via an exception handler; `field` is the dotted location without the `body.` prefix |
| 429 | `rate_limited` | Login |
| 502 | `upstream_error` | Catalogue host or MediaMTX unreachable / non-2xx (`detail` names the upstream URL and status) |
| 503 | `unavailable` | DB down (`/healthz`) |
| 500 | `internal_error` | Unhandled; generic message, stack in logs |

### 3.2 Pagination and sorting

Every list endpoint accepts `page` (1-based, default 1), `page_size` (default 25, max 200; `/detections` and `/audit` allow max 500), `sort` (a column from that endpoint's whitelist; unknown → 422), `order` (`asc`|`desc`; default `desc` for time-ordered lists, `asc` for name-ordered lists). Response:

```json
{"items": [...], "total": 1234, "page": 1, "page_size": 25}
```

`total` is the count after filters. Time filters `from`/`to` are inclusive ISO-8601; `to` defaults to now; `from` defaults per endpoint. Text filter `q` is case-insensitive `ILIKE %q%` over the endpoint's listed columns.

### 3.3 Common query filters

| Filter | Type | Applies to |
|---|---|---|
| `camera_id` | int | detections, sightings, alerts, events, object-counts, clips, recordings, dashboard charts |
| `department_id` | int | cameras, detections, alerts, events, reports, gap |
| `district` | string (exact, title case) | cameras, geo, gap |
| `from`, `to` | ISO-8601 | any time-ordered list |
| `status` | enum | cameras, alerts |
| `type` | enum | cameras, alerts, events, pois |
| `q` | string | cameras (name, external_id, address, police_station), watchlist (plate_norm, name), users (username, full_name), audit (entity_id, action, actor) |

### 3.4 Plate normalisation contract (ANPR worker, API search, watchlist import, tests)

Implemented **identically** in `anpr/normalise.py` and `backend/app/services/plates.py` (pure functions, no I/O). `backend/tests/test_normalise.py` and `anpr/tests/test_normalise.py` share the vectors below.

`normalise(raw: str) -> NormResult(plate_norm: str, is_valid_format: bool, pattern: 'standard'|'bh'|None, substitutions: int)`

1. `s = raw.upper()`; remove every character not in `[A-Z0-9]` (drops spaces, hyphens, dots, `·`, newlines from two-line OCR).
2. If `s` starts with `IND`, drop that prefix. If the result is empty, return `("", false, None, 0)`.
3. If `len(s) < 7` or `len(s) > 10`: return `(s, false, None, 0)` — kept for search, never alertable.
4. Confusion maps (exactly these):
   - `TO_LETTER = {"0":"O","1":"I","5":"S","8":"B","2":"Z","6":"G"}`
   - `TO_DIGIT = {"O":"0","I":"1","S":"5","B":"8","Z":"2","G":"6","Q":"0","D":"0"}`
   - `fix_letter(c) = TO_LETTER.get(c, c)`; `fix_digit(c) = TO_DIGIT.get(c, c)`.
5. **Standard candidate.** Slots: `s[0:2]` letters (state), `s[-4:]` digits (number), `mid = s[2:-4]` (length 1..5). For `k in (2, 1)` (district-digit count): `digits = mid[:k]`, `series = mid[k:]`; require `1 <= len(series) <= 3`; `cand = fix_letter(s[0]) + fix_letter(s[1]) + fix_digit(each of digits) + fix_letter(each of series) + fix_digit(each of s[-4:])`; `subs` = number of positions where `cand[i] != s[i]`; keep the candidate if it matches `STANDARD = ^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$` **and**, when `k=1`, the single district digit is `1`–`9` (there is no district `0`; this is what forces `GJ0IAB1234 → GJ01AB1234` instead of `GJ 0 IAB 1234`). Among valid `k` candidates choose the fewest substitutions; tie → `k=2`.
6. **BH candidate** (only when `len(s)` is 9 or 10): `cand = fix_digit(s[0]) + fix_digit(s[1]) + fix_letter(s[2]) + fix_letter(s[3]) + fix_digit(each of s[4:8]) + fix_letter(each of s[8:])`; valid if it matches `BH = ^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$`.
7. A candidate is accepted only if `subs <= 2` (more than two confusion fixes means the OCR string is not a plate; `S5S5S5S5` would otherwise "become" `SS5S5555`). Choose between the accepted standard and BH candidates by fewest substitutions; tie → standard. If neither is accepted: `plate_norm = s` (uppercase, stripped, **no** confusion mapping), `is_valid_format = false`, `substitutions = 0`.
8. Search input (`/vehicles/search?q=`) uses the same function; when `is_valid_format` is false the search still runs with `plate_norm = s`.

Test vectors (must pass verbatim):

| input | plate_norm | valid | pattern | subs |
|---|---|---|---|---|
| `GJ 01 AB 1234` | `GJ01AB1234` | true | standard | 0 |
| `gj-01-ab-1234` | `GJ01AB1234` | true | standard | 0 |
| `IND GJ05 RS 5678` | `GJ05RS5678` | true | standard | 0 |
| `GJO1AB1234` | `GJ01AB1234` | true | standard | 1 |
| `G101AB1234` | `GI01AB1234` | true | standard | 1 |
| `GJ01A81234` | `GJ01AB1234` | true | standard | 1 |
| `MH12DE14O3` | `MH12DE1403` | true | standard | 1 |
| `GJ1AB1234` | `GJ1AB1234` | true | standard | 0 |
| `DL8CAF5030` | `DL8CAF5030` | true | standard | 0 |
| `GJ 01` + newline + `AB 1234` (two-line) | `GJ01AB1234` | true | standard | 0 |
| `22BH4321AA` | `22BH4321AA` | true | bh | 0 |
| `Z2BH4321AA` | `22BH4321AA` | true | bh | 1 |
| `GJ0IAB1234` | `GJ01AB1234` | true | standard | 1 |
| `KA05MK 0101` | `KA05MK0101` | true | standard | 0 |
| `8H01AB1234` | `BH01AB1234` | true | standard | 1 |
| `GJ01AB12345` | `GJ01AB12345` | false | – | 0 |
| `GJ01ABCD1234` | `GJ01ABCD1234` | false | – | 0 |
| `ABC` | `ABC` | false | – | 0 |
| `IND` | `` (empty) | false | – | 0 |
| `S5S5S5S5` | `S5S5S5S5` | false | – | 0 |

Display formatting (UI + PDF): `format_plate("GJ01AB1234") → "GJ 01 AB 1234"` (state, district, series, number separated by spaces), `"22BH4321AA" → "22 BH 4321 AA"`, invalid → unchanged.

Fuzzy matching helpers (API only): `levenshtein(a, b)` from `fuzzystrmatch`, `similarity(a, b)` from `pg_trgm`. Watchlist fuzzy: `levenshtein(read.plate_norm, wl.plate_norm) <= 1` computed in Python over the in-memory map. Search fuzzy: SQL `levenshtein(plate_norm, :q) <= 2 OR similarity(plate_raw, :q) > 0.6`.

---

## 4. Data model (PostgreSQL 16, SQLAlchemy 2 `create_all`)

General rules:

- Every table has `id BIGSERIAL PRIMARY KEY` unless stated. All timestamps are `timestamptz` stored in UTC. `created_at` defaults to `now()`; `updated_at` is set by the application on every update.
- Enums are `VARCHAR` columns with a `CHECK (col IN (...))` constraint (no PostgreSQL `ENUM` types — simpler with `create_all` and easy to extend). Values are lowercase.
- Foreign keys: `ON DELETE` behaviour is stated per column. Nothing hard-deletes cameras, users or watchlist rows; they are retired/deactivated.
- Extensions created at startup (idempotent): `CREATE EXTENSION IF NOT EXISTS postgis; pg_trgm; fuzzystrmatch;`.
- Schema changes during the hackathon: drop the `pgdata` volume and restart (no Alembic). The seed is idempotent.
- `audit_log` is append-only: trigger `audit_log_immutable BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION raise_exception('audit_log is append-only')` created right after `create_all`.
- File path columns store paths **relative to `DATA_DIR`** (e.g. `crops/12/2026-09-04/3f9a….jpg`); the API turns them into `/media/<relative path>` URLs in responses (§10).

### 4.1 Enumerations

| Enum | Values | Used by |
|---|---|---|
| `role` | `admin`, `dept_admin`, `operator`, `viewer` | users |
| `api_key_scope` | `bulk`, `internal` | api_keys |
| `camera.source` | `sandbox`, `csv`, `api`, `manual`, `own` | cameras |
| `camera.type` | `analog`, `ip`, `ptz`, `dome`, `bullet`, `anpr`, `other` | cameras |
| `camera.ownership` | `govt_dept`, `private`, `public_facing` | cameras |
| `camera.connectivity_type` | `lan`, `fibre`, `4g`, `5g`, `leased_line`, `wifi`, `other` | cameras |
| `camera.codec` | `H264`, `H265`, `MJPEG`, `UNKNOWN` (uppercase) | cameras |
| `camera.status` | `unknown`, `online`, `degraded`, `offline`, `retired` | cameras |
| `camera.maintenance_status` | `ok`, `under_maintenance`, `faulty`, `decommissioned` | cameras |
| `health.source_flag` | `mediamtx`, `probe`, `catalogue`, `manual` | camera_health_log |
| `watchlist.entity_type` | `vehicle`, `person` | watchlist |
| `watchlist.reason` | `stolen`, `wanted`, `blacklisted`, `missing`, `suspect`, `arrested`, `unidentified_body`, `other` | watchlist |
| `watchlist.priority` | `critical`, `high`, `medium`, `low` | watchlist, alerts |
| `watchlist.source` | `own`, `egujcop`, `vahan`, `manual`, `import` | watchlist |
| `alert.type` | `watchlist_hit`, `camera_offline`, `intrusion`, `frs_hit` (reserved, never emitted in Phase 1) | alerts |
| `alert.status` | `new`, `acknowledged`, `closed` | alerts |
| `alert.confidence_level` | `exact`, `possible`, `null` (non-plate alerts) | alerts |
| `alert.outcome` | `resolved`, `false_positive`, `duplicate`, `other`, `null` | alerts |
| `event.type` | manual: `accident`, `suspicious`, `checkpoint`, `other`; automatic: `watchlist_hit`, `loop_reset`, `intrusion`, `camera_offline`, `camera_online` | events |
| `route_confirmation.decision` | `confirmed`, `rejected` | route_confirmations |
| `poi.type` | `checkpost`, `bus_stand`, `hospital`, `school`, `highway`, `railway_station`, `temple`, `market`, `govt_office`, `border`, `other` | pois |
| `object.class` | `person`, `bicycle`, `car`, `motorcycle`, `bus`, `truck` | object_counts, zones.classes |
| `webhook.event_types` | `alert.created`, `alert.updated`, `camera.offline`, `camera.online`, `event.created` | webhooks |
| `report.type` | `detections_csv`, `detections_pdf`, `route_pdf`, `gap_csv`, `gap_pdf`, `quality_pdf`, `cameras_csv`, `import_errors_csv` | report_files |
| `audit.action` | see §4.4 | audit_log |

### 4.2 Tables

**departments**

| column | type | null | default | notes |
|---|---|---|---|---|
| id | bigserial PK | | | |
| code | varchar(32) unique | no | | uppercase, e.g. `POLICE` |
| name | varchar(120) | no | | display name |
| aliases | text[] | no | `{}` | lowercase catalogue names mapped to this dept, e.g. `{police,gujarat police}` |
| created_at | timestamptz | no | now() | |

**users**

| column | type | null | default | notes |
|---|---|---|---|---|
| id | bigserial PK | | | |
| username | varchar(64) unique | no | | lowercase |
| password_hash | varchar(128) | no | | bcrypt |
| full_name | varchar(120) | no | | |
| role | varchar(16) | no | `viewer` | enum `role` |
| department_id | bigint FK departments ON DELETE SET NULL | yes | | |
| district | varchar(64) | yes | | title case, matches `cameras.district` |
| is_active | boolean | no | true | |
| last_login_at | timestamptz | yes | | |
| wall_layout | jsonb | yes | | saved video-wall layout per user (§5.7) |
| created_at / updated_at | timestamptz | no | now() | |

**api_keys**

| column | type | null | default | notes |
|---|---|---|---|---|
| id | bigserial PK | | | |
| name | varchar(64) unique | no | | |
| key_hash | varchar(64) unique | no | | sha256 hex |
| key_prefix | varchar(12) | no | | first 8 chars after `sk_`, shown in lists |
| scope | varchar(16) | no | | enum `api_key_scope` |
| created_by | bigint FK users ON DELETE SET NULL | yes | | |
| last_used_at | timestamptz | yes | | |
| is_active | boolean | no | true | |
| created_at | timestamptz | no | now() | |

**cameras**

| column | type | null | default | notes |
|---|---|---|---|---|
| id | bigserial PK | | | |
| source | varchar(16) | no | `manual` | enum `camera.source` |
| external_id | varchar(64) | no | | catalogue id / CSV id / user id; `OWN-<slug>` for own feeds |
| name | varchar(160) | no | | |
| department_id | bigint FK departments ON DELETE RESTRICT | no | UNASSIGNED id | |
| type | varchar(16) | no | `ip` | enum `camera.type` |
| ownership | varchar(16) | no | `govt_dept` | enum `camera.ownership` |
| lat | double precision | yes | | -90..90 |
| lon | double precision | yes | | -180..180 |
| geog | geography(Point,4326) | yes | | maintained by the app on insert/update from lat/lon; used for `ST_DWithin`/`ST_Buffer` |
| address | varchar(255) | yes | | |
| district | varchar(64) | yes | | title case |
| police_station | varchar(120) | yes | | |
| ward | varchar(64) | yes | | |
| rtsp_url | varchar(512) | yes | | source URL (sandbox or own); credentials allowed in URL, masked in responses for non-admins (`rtsp://user:***@host/...`) |
| whep_url | varchar(512) | yes | | catalogue value, stored only |
| hls_url | varchar(512) | yes | | catalogue value, stored only |
| relay_path | varchar(64) | yes | | `cam_<id>`; set right after insert |
| codec | varchar(8) | no | `UNKNOWN` | enum `camera.codec` |
| resolution | varchar(16) | yes | | `WIDTHxHEIGHT`, e.g. `1920x1080` |
| fps | smallint | yes | | 1..60 |
| live | boolean | yes | | catalogue `live` flag as of the last import (null for non-sandbox) |
| storage_location | varchar(120) | yes | | free text (NVR/DVR location) |
| retention_days | smallint | yes | | 0..3650 |
| install_date | date | yes | | |
| vendor | varchar(80) | yes | | |
| model | varchar(80) | yes | | |
| heading_deg | smallint | yes | | 0..359 |
| fov_deg | smallint | yes | | 1..360 |
| connectivity_type | varchar(16) | yes | | enum `camera.connectivity_type` |
| bandwidth_kbps | integer | yes | | |
| vms_platform | varchar(80) | yes | | e.g. `Milestone`, `Hikvision NVR`, `none` |
| nvr_id | varchar(64) | yes | | |
| onvif_host | varchar(120) | yes | | `host:port` (S8, P2) |
| anpr_enabled | boolean | no | false | live ANPR on this camera |
| record_enabled | boolean | no | false | MediaMTX `record: yes` on the relay path |
| status | varchar(16) | no | `unknown` | enum `camera.status` |
| health_fail_count | smallint | no | 0 | consecutive not-ready checks |
| last_seen_at | timestamptz | yes | | last check with `is_ready=true` |
| last_status_change_at | timestamptz | yes | | |
| maintenance_status | varchar(24) | no | `ok` | enum |
| last_maintenance_at | timestamptz | yes | | |
| maintenance_note | varchar(255) | yes | | |
| amc_vendor | varchar(120) | yes | | |
| amc_expiry | date | yes | | |
| created_by | bigint FK users ON DELETE SET NULL | yes | | null for API-key / catalogue imports (`created_via` says which) |
| created_via | varchar(16) | no | `manual` | same values as `source` |
| created_at / updated_at | timestamptz | no | now() | |
| retired_at | timestamptz | yes | | set by `DELETE /cameras/{id}` |

Constraints: `UNIQUE (source, external_id)`; `CHECK (lat IS NULL OR lat BETWEEN -90 AND 90)`; same for lon. Indexes: `(department_id)`, `(district)`, `(status)`, `(anpr_enabled)`, `GIST (geog)`, `gin (name gin_trgm_ops)`.

**settings**

| column | type | null | notes |
|---|---|---|---|
| key | varchar(64) PK | no | dotted, e.g. `catalogue.base_url` |
| value | jsonb | no | JSON value (string, number, bool, object) |
| is_secret | boolean | no | masked in `GET /settings` |
| updated_by | bigint FK users ON DELETE SET NULL | yes | |
| updated_at | timestamptz | no | |

Seeded keys (from env on first start; later edits win): `catalogue.base_url`, `catalogue.auth_type`, `catalogue.auth_username`, `catalogue.auth_password` (secret), `catalogue.auth_header` (secret), `catalogue.timeout_s`, `catalogue.field_map` (object, §6.3), `catalogue.department_aliases` (object), `retention.days_frames`, `retention.days_reads`, `retention.days_clips`, `gap.coverage_radius_m`, `gap.poi_radius_m`, `gap.grid_m`, `gap.ageing_years`, `alerts.suppression_s`, `alerts.fuzzy_min_conf`, `alerts.escalate_minutes`, `route.speed_flag_kmh`, `route.default_window_h`, `notify.telegram_bot_token` (secret), `notify.telegram_chat_id`, `notify.telegram_min_priority` (`high`), `ui.product_name` (`Sentinel Gujarat`), `ui.map_center` (`[23.2156, 72.6369]`), `ui.map_zoom` (`8`).

**camera_health_log**

| column | type | null | notes |
|---|---|---|---|
| id | bigserial PK | | |
| camera_id | bigint FK cameras ON DELETE CASCADE | no | |
| checked_at | timestamptz | no | |
| is_ready | boolean | no | MediaMTX `ready` or probe success |
| has_video | boolean | no | a video track present |
| bytes_delta | bigint | no | `bytesReceived` delta since previous check (0 for probe) |
| readers | smallint | no | MediaMTX reader count |
| source_flag | varchar(16) | no | enum `health.source_flag` |
| status_after | varchar(16) | no | camera status after applying the state machine |

Index `(camera_id, checked_at DESC)`. Rows older than 7 days are purged by the retention job.

**pois**

| column | type | null |
|---|---|---|
| id | bigserial PK | |
| name | varchar(160) | no |
| type | varchar(24) | no (enum `poi.type`) |
| district | varchar(64) | no |
| lat / lon | double precision | no |
| geog | geography(Point,4326) | no |

**districts**

| column | type | null |
|---|---|---|
| id | bigserial PK | |
| name | varchar(64) unique | no (title case; equals `cameras.district`) |
| code | varchar(8) | yes |
| geom | geometry(MultiPolygon,4326) | no |

**plate_reads**

| column | type | null | notes |
|---|---|---|---|
| id | bigserial PK | | |
| camera_id | bigint FK cameras ON DELETE CASCADE | no | |
| sighting_id | bigint FK sightings ON DELETE SET NULL | yes | |
| captured_at | timestamptz | no | worker UTC wall clock |
| stream_pts | double precision | yes | seconds |
| frame_index | bigint | yes | |
| plate_raw | varchar(32) | no | OCR output before normalisation (spaces kept, uppercased) |
| plate_norm | varchar(16) | no | §3.4 |
| is_valid_format | boolean | no | |
| confidence | real | no | 0..1 (mean of voted chars) |
| bbox | integer[4] | yes | `[x, y, w, h]` in decoded-frame pixels (960-wide frame) |
| crop_path | varchar(255) | yes | relative path |
| crop_sha256 | char(64) | yes | |
| mode | varchar(8) | no | `live` or `preindex` |
| created_at | timestamptz | no | |

Indexes: `(plate_norm, captured_at DESC)`, `(camera_id, captured_at DESC)`, `(captured_at)`, `gin (plate_raw gin_trgm_ops)`, `gin (plate_norm gin_trgm_ops)`.

**sightings**

| column | type | null | notes |
|---|---|---|---|
| id | bigserial PK | | |
| worker_key | varchar(80) unique | no | worker-generated idempotency key `"{camera_id}:{plate_norm}:{first_seen_epoch_ms}"` |
| camera_id | bigint FK cameras ON DELETE CASCADE | no | |
| plate_norm | varchar(16) | no | |
| is_valid_format | boolean | no | |
| first_seen / last_seen | timestamptz | no | |
| read_count | integer | no | |
| best_conf | real | no | |
| best_read_id | bigint FK plate_reads ON DELETE SET NULL | yes | |
| best_crop_path | varchar(255) | yes | copy of the best read's crop path |
| frame_path | varchar(255) | yes | full frame of the best read |
| frame_sha256 | char(64) | yes | |
| closed | boolean | no | |
| mode | varchar(8) | no | `live` / `preindex` |
| created_at / updated_at | timestamptz | no | |

Indexes: `(plate_norm, first_seen DESC)`, `(camera_id, first_seen DESC)`, `(closed)`.

**watchlist**

| column | type | null | notes |
|---|---|---|---|
| id | bigserial PK | | |
| entity_type | varchar(8) | no | enum |
| plate_norm | varchar(16) | yes | required when `entity_type='vehicle'`; unique among active vehicle rows |
| name | varchar(160) | yes | person name / vehicle owner |
| reason | varchar(24) | no | enum |
| priority | varchar(8) | no | enum, default `medium` |
| source | varchar(16) | no | enum, default `manual` |
| notes | text | yes | |
| photo_path | varchar(255) | yes | person photo (F1, P2) |
| added_by | bigint FK users ON DELETE SET NULL | yes | |
| is_active | boolean | no | true |
| expires_at | timestamptz | yes | null = never |
| hit_count | integer | no | 0; incremented per alert |
| last_hit_at | timestamptz | yes | |
| created_at / updated_at | timestamptz | no | |

Partial unique index: `UNIQUE (plate_norm) WHERE entity_type='vehicle' AND is_active`. Index `(is_active, expires_at)`.

**alerts**

| column | type | null | notes |
|---|---|---|---|
| id | bigserial PK | | |
| type | varchar(16) | no | enum |
| status | varchar(16) | no | `new` |
| priority | varchar(8) | no | enum |
| confidence_level | varchar(8) | yes | `exact` / `possible` |
| camera_id | bigint FK cameras ON DELETE CASCADE | no | |
| watchlist_id | bigint FK watchlist ON DELETE SET NULL | yes | |
| sighting_id | bigint FK sightings ON DELETE SET NULL | yes | |
| read_id | bigint FK plate_reads ON DELETE SET NULL | yes | first matching read |
| plate_norm | varchar(16) | yes | denormalised for display after retention |
| snapshot_path | varchar(255) | yes | crop or intrusion frame at alert time |
| snapshot_sha256 | char(64) | yes | |
| read_count | integer | no | 1; reads attached by suppression |
| last_read_at | timestamptz | yes | |
| latency_ms | integer | yes | `created_at − read.captured_at` |
| acknowledged_by | bigint FK users ON DELETE SET NULL | yes | |
| acknowledged_at | timestamptz | yes | |
| closed_by | bigint FK users ON DELETE SET NULL | yes | |
| closed_at | timestamptz | yes | |
| outcome | varchar(16) | yes | enum |
| note | text | yes | last note (ack or close) |
| created_at / updated_at | timestamptz | no | |

Indexes: `(status, created_at DESC)`, `(camera_id, created_at DESC)`, `(watchlist_id, camera_id, created_at DESC)`, `(type, status)`.

**events**

| column | type | null | notes |
|---|---|---|---|
| id | bigserial PK | | |
| camera_id | bigint FK cameras ON DELETE CASCADE | no | |
| occurred_at | timestamptz | no | |
| type | varchar(16) | no | enum |
| note | text | yes | |
| sighting_id | bigint FK sightings ON DELETE SET NULL | yes | |
| read_id | bigint FK plate_reads ON DELETE SET NULL | yes | |
| alert_id | bigint FK alerts ON DELETE SET NULL | yes | |
| frame_path | varchar(255) | yes | intrusion frame |
| frame_sha256 | char(64) | yes | |
| is_auto | boolean | no | true for machine-generated |
| created_by | bigint FK users ON DELETE SET NULL | yes | null when auto |
| created_at | timestamptz | no | |

Index `(camera_id, occurred_at DESC)`, `(type, occurred_at DESC)`.

**route_confirmations**

| column | type | null |
|---|---|---|
| id | bigserial PK | |
| query_plate | varchar(16) | no |
| sighting_id | bigint FK sightings ON DELETE CASCADE | no |
| decision | varchar(10) | no |
| user_id | bigint FK users ON DELETE SET NULL | yes |
| created_at | timestamptz | no |

`UNIQUE (query_plate, sighting_id)` — a later decision replaces the earlier one (upsert).

**object_counts**

| column | type | null | notes |
|---|---|---|---|
| id | bigserial PK | | |
| camera_id | bigint FK cameras ON DELETE CASCADE | no | |
| minute | timestamptz | no | minute bucket start, UTC, seconds = 0 |
| class | varchar(16) | no | enum `object.class` |
| count | integer | no | unique tracks seen in that minute |

`UNIQUE (camera_id, minute, class)`; index `(minute)`.

**zones** (A7, P2 — table exists from day one so the worker config is stable)

| column | type | null | notes |
|---|---|---|---|
| id | bigserial PK | | |
| camera_id | bigint FK cameras ON DELETE CASCADE | no | |
| name | varchar(80) | no | |
| polygon_json | jsonb | no | `[[x,y],...]` in **normalised** coordinates 0..1 of the frame |
| active_from / active_to | time | yes | IST clock times; null = always |
| classes | text[] | no | subset of `object.class`; empty = all |
| dwell_s | real | no | 2.0 |
| priority | varchar(8) | no | `medium` |
| is_active | boolean | no | true |
| created_by | bigint FK users | yes | |
| created_at / updated_at | timestamptz | no | |

**clips**

| column | type | null | notes |
|---|---|---|---|
| id | bigserial PK | | |
| camera_id | bigint FK cameras ON DELETE CASCADE | no | |
| alert_id | bigint FK alerts ON DELETE SET NULL | yes | |
| sighting_id | bigint FK sightings ON DELETE SET NULL | yes | |
| start_at | timestamptz | no | |
| duration_s | smallint | no | 1..120 |
| path | varchar(255) | no | `clips/<camera_id>/<id>.mp4` |
| sha256 | char(64) | no | |
| size_bytes | bigint | no | |
| created_by | bigint FK users ON DELETE SET NULL | yes | |
| created_at | timestamptz | no | |

**qa_labels**

| column | type | null |
|---|---|---|
| id | bigserial PK | |
| read_id | bigint FK plate_reads ON DELETE CASCADE, unique | no |
| true_plate | varchar(16) | no (normalised; empty string = "unreadable") |
| is_match | boolean | no (computed: `true_plate = read.plate_norm`) |
| char_errors | smallint | no (levenshtein) |
| labelled_by | bigint FK users ON DELETE SET NULL | yes |
| created_at | timestamptz | no |

**webhooks**

| column | type | null | notes |
|---|---|---|---|
| id | bigserial PK | | |
| name | varchar(80) | no | |
| url | varchar(512) | no | https or http |
| secret | varchar(128) | yes | HMAC-SHA256 key; masked in GET |
| event_types | text[] | no | subset of `webhook.event_types` |
| is_active | boolean | no | true |
| last_status | smallint | yes | last HTTP status |
| last_delivered_at | timestamptz | yes | |
| last_error | varchar(255) | yes | |
| created_by | bigint FK users | yes | |
| created_at | timestamptz | no | |

**report_files** (generated files with hashes — the evidence ledger)

| column | type | null |
|---|---|---|
| id | bigserial PK | |
| type | varchar(24) | no (enum `report.type`) |
| path | varchar(255) | no |
| sha256 | char(64) | no |
| size_bytes | bigint | no |
| params | jsonb | no (filters used) |
| row_count | integer | yes |
| created_by | bigint FK users ON DELETE SET NULL | yes |
| created_at | timestamptz | no |

**anpr_workers** (heartbeat state; one row per worker instance)

| column | type | null |
|---|---|---|
| id | varchar(64) PK | (`"{mode}-{hostname}"`) |
| mode | varchar(8) | no |
| version | varchar(32) | no |
| gpu | boolean | no |
| cameras | jsonb | no (`[{id, state, fps_actual, frames, last_frame_at, decoder_restarts}]`) |
| last_heartbeat_at | timestamptz | no |

**audit_log**

| column | type | null | notes |
|---|---|---|---|
| id | bigserial PK | | |
| ts | timestamptz | no | |
| user_id | bigint (no FK, so rows survive user deletion) | yes | |
| actor | varchar(80) | no | `username`, `apikey:<name>`, or `system` |
| role | varchar(16) | no | role or `apikey` / `system` |
| action | varchar(48) | no | §4.4 |
| entity | varchar(32) | yes | table/resource name |
| entity_id | varchar(64) | yes | |
| before | jsonb | yes | |
| after | jsonb | yes | |
| ip | inet | yes | `X-Forwarded-For` first hop (Caddy sets it) |
| user_agent | varchar(255) | yes | |
| request_id | uuid | yes | |

Indexes: `(ts DESC)`, `(user_id, ts DESC)`, `(action, ts DESC)`, `(entity, entity_id)`.

### 4.3 Derived values (computed in SQL/Python, never stored)

- `cameras.uptime_24h_pct` = `100 * count(is_ready) / count(*)` over `camera_health_log` in the last 24 h (null if no rows).
- `alerts.escalated` = `status='new' AND now() - created_at > alerts.escalate_minutes`.
- `watchlist.is_effective` = `is_active AND (expires_at IS NULL OR expires_at > now())`.
- `cameras.age_years` = `(current_date - install_date) / 365.25`.

### 4.4 Audit action names (exhaustive)

`auth.login`, `auth.login_failed`, `auth.logout`, `user.change_password`, `user.create`, `user.update`, `user.deactivate`, `user.reset_password`, `apikey.create`, `apikey.delete`, `camera.create`, `camera.update`, `camera.retire`, `camera.maintenance`, `camera.import_sandbox`, `camera.import_csv`, `camera.import_bulk`, `camera.export`, `stream.view`, `recording.play`, `clip.create`, `watchlist.create`, `watchlist.update`, `watchlist.delete`, `watchlist.import`, `alert.ack`, `alert.close`, `alert.auto_close`, `event.create`, `vehicle.search`, `vehicle.route`, `vehicle.confirm`, `report.detections`, `report.route`, `report.gap`, `report.quality`, `report.download`, `evidence.verify`, `settings.update`, `webhook.create`, `webhook.delete`, `zone.create`, `zone.update`, `zone.delete`, `qa.label`, `retention.purge`, `external.lookup`.

Audit middleware rules: every request with method `POST/PUT/PATCH/DELETE` writes one row (action derived from the route; `before/after` filled by the handler where it knows the diff); GETs write a row only for `stream.view`, `recording.play`, `vehicle.search`, `vehicle.route`, `camera.export`, `report.*`, `evidence.verify`, `external.lookup`. `/internal/*` and `/auth/verify` are not audited. Health poller and retention job write rows with `actor='system'`.

---

## 5. REST endpoints

Prefix `/api`. OpenAPI is served at `/api/docs` (Swagger UI with `bearerAuth` and `apiKeyAuth` security schemes), `/api/redoc`, `/api/openapi.json`. The docs builder renders `openapi.json` to `docs/REGISTRY-API.md`. Unless stated, a successful mutation returns the full updated object (`200`), a creation returns `201` with the object, a deletion returns `204`.

Shared object shapes used below:

`CameraSummary` (embedded in reads, alerts, events, sightings):

```json
{"id": 12, "external_id": "3", "name": "Civil Hospital Gandhinagar", "department_id": 2, "department_code": "HEALTH", "department_name": "Health & Family Welfare", "district": "Gandhinagar", "police_station": "Sector 7", "lat": 23.2275, "lon": 72.6485, "status": "online", "type": "bullet"}
```

`Camera` (full; returned by `/cameras` list and detail): every column of `cameras` (§4.2) except `geog`, `health_fail_count`, plus `department_code`, `department_name`, `relay_path`, `uptime_24h_pct`, `age_years`, `amc_status` (`expired`|`expiring`|`ok`|`none`), `created_by_username`. `rtsp_url` is masked (`rtsp://user:***@…`) for non-admin roles.

`CameraImportRow` (input for CSV rows, bulk API and manual form; every field optional except `external_id` and `name`; unknown keys are ignored with a warning):

| field | type | validation | default |
|---|---|---|---|
| external_id | string ≤ 64 | required, trimmed | |
| name | string ≤ 160 | required | |
| department_code | string | must exist in `departments.code` (case-insensitive) or match an alias; otherwise `UNASSIGNED` + warning | `UNASSIGNED` |
| type | enum | | `ip` |
| ownership | enum | | `govt_dept` |
| lat, lon | number | both or neither; ranges; warning (not error) when outside the Gujarat bbox `20.1..24.8 N, 68.1..74.5 E` | null |
| address, district, police_station, ward | string | district title-cased | null |
| rtsp_url | string ≤ 512 | must start with `rtsp://` or `rtsps://` when present | null |
| whep_url, hls_url | string | `http(s)://` | null |
| codec | string | normalised: `h264/avc/h.264 → H264`, `h265/hevc/h.265 → H265`, `mjpeg → MJPEG`, else `UNKNOWN` | `UNKNOWN` |
| resolution | string | `^\d{2,5}x\d{2,5}$` | null |
| fps | int | 1..60 | null |
| live | bool | | null |
| storage_location, vendor, model, vms_platform, nvr_id, amc_vendor | string | | null |
| retention_days | int | 0..3650 | null |
| install_date, amc_expiry | date `YYYY-MM-DD` | | null |
| heading_deg | int | 0..359 | null |
| fov_deg | int | 1..360 | null |
| connectivity_type | enum | | null |
| bandwidth_kbps | int | ≥ 0 | null |
| maintenance_status | enum | | `ok` |
| anpr_enabled, record_enabled | bool (`true/false/1/0/yes/no`) | | see import rules |

Import rules common to sandbox, CSV, bulk and manual: upsert key `(source, external_id)`; on update only the fields present in the input are changed (CSV: a blank cell means "leave unchanged" on update and "null" on insert); a duplicate `external_id` within the same file/batch → error on the second occurrence; after commit, for each added/updated camera the API calls MediaMTX (§8.3) and records `relay_paths_created/failed`; a MediaMTX failure is a **warning**, not a row error (the camera is still saved with `status='unknown'`). New sandbox cameras get `anpr_enabled = live AND (enabled_count < ANPR_AUTO_ENABLE_MAX)` and `record_enabled = anpr_enabled`; updates never touch `anpr_enabled`/`record_enabled` unless the input contains them explicitly.

### 5.1 Auth

Covered in §2: `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/change-password`, `GET /auth/verify`.

### 5.2 Cameras

| Method & path | Permission | Notes |
|---|---|---|
| `GET /cameras` | cameras.read | Filters: `q`, `department_id`, `district`, `police_station`, `type`, `status` (comma list allowed), `source`, `ownership`, `maintenance_status`, `anpr_enabled`, `include_retired` (default false). Sort whitelist: `name`, `external_id`, `status`, `district`, `department_name`, `last_seen_at`, `updated_at`, `created_at`, `install_date`, `amc_expiry` (default `name asc`). Items are `Camera` |
| `POST /cameras` | cameras.write | Body `CameraImportRow` + optional `source` (`manual` default; `own` allowed). `201 Camera`. Creates the relay path. Audit `camera.create` |
| `GET /cameras/{id}` | cameras.read | `Camera` + `"recent_alerts": 3`, `"reads_24h": n`, `"sightings_24h": n`, `"snapshot_url"`, `"zones": [...]` |
| `PUT /cameras/{id}` | cameras.write | Partial update (only supplied fields). If `rtsp_url`, `codec`, `record_enabled` or `anpr_enabled` changed → MediaMTX path re-created (delete + add). Audit with `before/after` diff |
| `DELETE /cameras/{id}` | cameras.write | Soft retire: `status='retired'`, `retired_at=now()`, MediaMTX path deleted, ANPR stops on next config reload. `204`. Audit `camera.retire` |
| `PUT /cameras/{id}/maintenance` | cameras.write | Body `{maintenance_status, last_maintenance_at?, maintenance_note?, amc_vendor?, amc_expiry?}` → `Camera`. Audit `camera.maintenance` |
| `GET /cameras/{id}/health?hours=24` | cameras.read | `{camera_id, status, uptime_pct, last_seen_at, last_status_change_at, checks: n, log: [{checked_at, is_ready, has_video, bytes_delta, readers, source_flag, status_after}], transitions: [{at, from, to}]}` (log limited to 1500 rows newest first) |
| `GET /cameras/export?format=csv` | cameras.export | Same filters as the list. Streams a CSV with the template columns (§12.1) plus `id, source, status, last_seen_at_ist, relay_path, created_at_ist`. Header row 1; watermark row appended at the end: `# Exported by <username> at <IST> from Sentinel Gujarat 1.0.0-phase1; sha256 of rows above: <hex>`. Stored under `exports/` and logged in `report_files` (`cameras_csv`) and audit `camera.export`. Response headers `Content-Disposition: attachment; filename="cameras_export_<YYYYMMDD_HHMM>IST.csv"`, `X-Sentinel-Sha256` |
| `GET /cameras/import/template` | cameras.write | CSV with the header from §12.1 and two example rows. `Content-Disposition: attachment; filename="cameras_template.csv"` |
| `POST /cameras/import/sandbox` | cameras.write **and role admin** (a catalogue spans departments, so dept_admin gets `403`) | Body optional `{"measure_first_stream": true, "dry_run": false}`. Reads `catalogue.*` settings, fetches `{base_url}/api/ingest`, maps (§6.3), upserts. Response below. Audit `camera.import_sandbox` with `after = summary` |
| `POST /cameras/import/csv` | cameras.write | `multipart/form-data`: `file` (CSV, UTF-8 with or without BOM, comma delimiter, ≤ 200 MB, ≤ 20 000 rows), `dry_run` (`true/false`, default false). For a dept_admin every row whose resolved department is not their own is a row error `"outside your department scope"`. Response below. Audit `camera.import_csv` |
| `POST /v1/cameras/bulk` | API key scope `bulk` | Body `{"cameras": [CameraImportRow, ...]}` (a bare JSON array is also accepted); max 1000. `source='api'`. Response below. Audit `camera.import_bulk` (actor `apikey:<name>`) |

Sandbox import response (`200`):

```json
{
  "source_url": "http://api:8000/mock-sandbox/api/ingest",
  "started_at": "2026-09-04T10:00:00.000Z",
  "finished_at": "2026-09-04T10:00:03.412Z",
  "duration_ms": 3412,
  "fetched": 50,
  "added": 50,
  "updated": 0,
  "unchanged": 0,
  "errors": [],
  "warnings": [{"row": 17, "external_id": "17", "field": "department", "message": "unknown department 'Roads' mapped to UNASSIGNED"}],
  "relay_paths_created": 50,
  "relay_paths_failed": 0,
  "anpr_enabled": 8,
  "first_stream_ready_ms": 2210,
  "dry_run": false
}
```

`first_stream_ready_ms` is null unless `measure_first_stream` was true: after the upsert the API requests `GET {MEDIAMTX_HLS_URL}/cam_<id>/index.m3u8` for the first `live=true` camera (this triggers the on-demand pull) and polls `/v3/paths/get/cam_<id>` every 250 ms up to 15 s until `ready`; the elapsed time is the "onboarding time" figure for R12. The UI shows "50 cameras onboarded in 3.4 s; first stream in 2.2 s".

CSV import response (`200`, even when some rows failed; `422` only if the header is unusable):

```json
{
  "job_id": "2026-09-04T10-05-11_7f3a",
  "dry_run": false,
  "rows_total": 10,
  "added": 7,
  "updated": 1,
  "errors": [
    {"row": 4, "external_id": "CSV-004", "field": "lat", "message": "must be between -90 and 90 (got 95.0)"},
    {"row": 9, "external_id": "CSV-002", "field": "external_id", "message": "duplicate external_id in file (first seen at row 2)"}
  ],
  "warnings": [],
  "error_report_url": "/media/exports/2026-09-04/import_errors_2026-09-04T10-05-11_7f3a.csv",
  "relay_paths_created": 8,
  "relay_paths_failed": 0,
  "duration_ms": 240
}
```

The error CSV has columns `row,external_id,field,message,original_line` and is served via `/media` (auth required). `error_report_url` is null when there are no errors.

Bulk API response (`200`; `422` if the body is not a list/object):

```json
{
  "added": 2, "updated": 1, "errors": [{"index": 3, "external_id": "X9", "field": "name", "message": "field required"}],
  "warnings": [],
  "results": [
    {"index": 0, "external_id": "GSRTC-101", "id": 61, "action": "added"},
    {"index": 1, "external_id": "GSRTC-102", "id": 62, "action": "added"},
    {"index": 2, "external_id": "3", "id": 12, "action": "updated"},
    {"index": 3, "external_id": "X9", "id": null, "action": "error"}
  ],
  "duration_ms": 88
}
```

### 5.3 Geo

| Method & path | Permission | Response |
|---|---|---|
| `GET /geo/cameras` | cameras.read | GeoJSON `FeatureCollection` of all non-retired (scoped) cameras with lat/lon; properties `{id, external_id, name, department_code, department_name, district, police_station, type, ownership, status, maintenance_status, anpr_enabled, live, codec, heading_deg, fov_deg, last_seen_at}`. Filters: `department_id`, `district`, `status`, `type`. No pagination (≤ 5 000 features) |
| `GET /geo/districts` | cameras.read | GeoJSON `FeatureCollection`, properties `{id, name, code, camera_count, online_count}`. Geometry simplified with `ST_SimplifyPreserveTopology(geom, 0.002)` |
| `GET /geo/pois?district&type` | cameras.read | GeoJSON points, properties `{id, name, type, district, nearest_camera_m}` |
| `GET /geo/coverage?radius=150&district=` | cameras.read | GeoJSON `FeatureCollection`: one `MultiPolygon` feature per district = `ST_Union(ST_Buffer(geog, radius))` of that district's cameras (online + degraded + unknown; offline cameras excluded, `include_offline=true` overrides). Properties `{district, radius_m, camera_count}`. Cached 5 min per (radius, district, include_offline) |

### 5.4 Streams and snapshots

`GET /streams/{camera_id}` — cameras.read; audit `stream.view` (one row per call; the UI calls it once per player mount).

```json
{
  "camera_id": 12,
  "name": "Civil Hospital Gandhinagar",
  "codec": "H264",
  "relay_path": "cam_12",
  "play_path": "cam_12",
  "whep_url": "/mtx/cam_12/whep",
  "hls_url": "/mtx/cam_12/index.m3u8",
  "snapshot_url": "/media/snapshots/cam_12.jpg",
  "snapshot_updated_at": "2026-09-04T10:15:29.000Z",
  "snapshot_stale": false,
  "ready": true,
  "readers": 2,
  "record_enabled": true,
  "playback_path": "cam_12",
  "status": "online"
}
```

`play_path` is `cam_<id>_h264` when `codec == 'H265'` (both `whep_url` and `hls_url` use it), else `cam_<id>`. `ready`/`readers` come from a live `GET /v3/paths/get/<play_path>` (null on MediaMTX error, never a 502). `snapshot_url` is null if no snapshot file exists.

### 5.5 Health

`GET /health/summary` — cameras.read (scoped).

```json
{
  "checked_at": "2026-09-04T10:15:00.000Z",
  "cameras": {"total": 51, "online": 8, "degraded": 1, "offline": 41, "unknown": 1, "retired": 0},
  "uptime_24h_pct": 17.3,
  "anpr_live_cameras": 8,
  "down_over_5min": [{"id": 30, "name": "Hazira Road Ichchhapore", "district": "Surat", "department_name": "Panchayat", "offline_since": "2026-09-04T09:40:00.000Z", "minutes": 35}],
  "amc_expiring_30d": [{"id": 37, "name": "Vapi GIDC Checkpost", "amc_vendor": "…", "amc_expiry": "2026-09-20", "days_left": 16}],
  "maintenance": [{"id": 41, "name": "Zalod Panchayat", "maintenance_status": "faulty", "since": "2026-08-30T00:00:00.000Z"}],
  "disk": {"data_used_bytes": 1234567890, "data_free_bytes": 98765432100, "recordings_used_bytes": 456789012},
  "mediamtx": {"ok": true, "paths": 51, "ready": 9},
  "anpr_workers": [{"id": "live-a1b2c3", "mode": "live", "gpu": false, "cameras": 3, "fps_total": 12.4, "last_heartbeat_at": "2026-09-04T10:14:55.000Z", "stale": false}]
}
```

A worker is `stale` when `last_heartbeat_at` is older than 3 × `HEARTBEAT_S`.

### 5.6 Health poller behaviour (backend, APScheduler, every `HEALTH_POLL_SECONDS`)

1. `GET {MEDIAMTX_API_URL}/v3/paths/list?itemsPerPage=1000` (pages until `pageCount`). Index by `name`. Also `GET /v3/config/paths/list` to know which relay paths exist at all (missing path → treat as not ready, `source_flag='mediamtx'`, and re-create it if the camera is not retired — self-healing after a MediaMTX restart with a lost runtime config).
2. For every non-retired camera, look up `relay_path` (and `_h264` for H.265). `is_ready = item.ready`; `has_video = any(track startswith 'H264'|'H265'|'MJPEG'|'AV1'|'VP')`; `bytes_delta = bytesReceived − previous` (previous kept in memory; on restart or counter reset use 0); `readers = len(item.readers)`.
3. **Idle on-demand paths report `ready=false` when nobody is reading**. Therefore, for cameras that are not ready, whose catalogue `live` is not `false`, and which have an `rtsp_url`, run an active probe (max `HEALTH_PROBE_MAX` per tick, least-recently-probed first, 4 in parallel): `ffprobe -v error -rtsp_transport tcp -timeout 6000000 -select_streams v:0 -show_entries stream=codec_name -of json rtsp://mediamtx:8554/<relay_path>`. Success → `is_ready=true, has_video=true, bytes_delta=0, source_flag='probe'`. Cameras with catalogue `live=false` are not probed and get `is_ready=false, source_flag='catalogue'`. Cameras **without** an `rtsp_url` have no relay path, are never checked, and stay `status='unknown'` (they surface in the gap report as "missing stream URL").
4. State machine per camera: `online` when `is_ready && has_video && (bytes_delta > 0 || source_flag == 'probe')`; `degraded` when `is_ready && (!has_video || bytes_delta == 0)` (and not probe); otherwise increment `health_fail_count`, and when it reaches `HEALTH_OFFLINE_AFTER` → `offline`. Any ready check resets `health_fail_count = 0` and sets `last_seen_at`. `unknown` only before the first check.
5. Transition to `offline` → insert `alerts(type='camera_offline', priority='low', status='new')` unless an open (`new`/`acknowledged`) camera_offline alert exists for that camera; insert `events(type='camera_offline')`. Transition from `offline` to `online`/`degraded` → auto-close that alert (`status='closed', outcome='resolved', note='Camera back online', closed_by=null`, audit `alert.auto_close`), insert `events(type='camera_online')`.
6. Write `camera_health_log` rows; update `cameras.status/last_seen_at/last_status_change_at`. Broadcast `health` on `/ws/health` for every status change and `stats` every `WS_STATS_INTERVAL_S`.
7. Cameras with `maintenance_status in ('under_maintenance','decommissioned')` are still checked but never raise `camera_offline` alerts.

### 5.7 Video wall layout (per user)

`GET /me/wall-layout` → `{"grid": 9, "tiles": [{"slot": 0, "camera_id": 12}, ...]}` (default: `grid=4`, first four `anpr_enabled` cameras). `PUT /me/wall-layout` same body → `200`. Stored in `users.wall_layout`. Any authenticated user.

### 5.8 Gap analysis

`GET /gap-analysis?coverage_radius_m=&poi_radius_m=&grid_m=&ageing_years=&district=` — cameras.read (scoped). Parameters default to the `gap.*` settings. Cached `GAP_CACHE_SECONDS` per parameter tuple + scope; `?refresh=1` bypasses.

```json
{
  "generated_at": "2026-09-04T10:20:00.000Z",
  "cached": false,
  "params": {"coverage_radius_m": 150, "poi_radius_m": 300, "grid_m": 500, "ageing_years": 5},
  "summary": {"cameras_total": 51, "with_location": 51, "online_pct": 17.6, "districts_with_cameras": 10, "districts_total": 33, "zero_coverage_cells": 412, "uncovered_pois": 23, "ageing_cameras": 6, "metadata_gaps": 12, "offline_hotspots": 2},
  "by_area": [
    {"district": "Gandhinagar", "police_station": "Sector 7", "ward": null, "total": 4, "online": 4, "degraded": 0, "offline": 0, "unknown": 0, "online_pct": 100.0, "by_department": {"POLICE": 2, "HEALTH": 1, "MUNICIPAL": 1}}
  ],
  "by_district": [{"district": "Gandhinagar", "total": 8, "online": 8, "online_pct": 100.0, "zero_coverage_cells": 40, "uncovered_pois": 2, "ageing": 1}],
  "department_gaps": [{"department_code": "GSRTC", "department_name": "GSRTC", "district": "Dahod"}],
  "uncovered_pois": [{"id": 7, "name": "Sector 21 School", "type": "school", "district": "Gandhinagar", "lat": 23.2301, "lon": 72.6410, "nearest_camera_id": 2, "nearest_camera_m": 812.4}],
  "zero_coverage": {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[72.64, 23.22], [72.645, 23.22], [72.645, 23.2245], [72.64, 23.2245], [72.64, 23.22]]]}, "properties": {"district": "Gandhinagar", "cell": "r12c7"}}]},
  "offline_hotspots": [{"district": "Surat", "police_station": "Athwa", "count": 2, "camera_ids": [28, 29]}],
  "metadata_gaps": [{"camera_id": 40, "name": "Dahod Bus Stand", "missing": ["retention_days", "install_date"]}],
  "ageing": [{"camera_id": 39, "name": "Bhilad Checkpost", "department_code": "POLICE", "district": "Valsad", "type": "analog", "install_date": "2018-03-01", "age_years": 8.5, "amc_expiry": "2026-06-30", "amc_status": "expired", "maintenance_status": "ok", "offline_pct_24h": 100.0, "near_poi": true, "priority_score": 82.5, "reasons": ["older than 5 years", "analog", "AMC expired", "offline 100% of 24 h"]}],
  "recommendations": [{"district": "Gandhinagar", "text": "Add 2 cameras at uncovered POIs (Sector 21 School, Pethapur Checkpost); replace 1 analog camera older than 5 years (Koba Circle)."}]
}
```

Zero-coverage grid: for each district polygon build a `grid_m` square grid in EPSG:3857 (`ST_SquareGrid`), keep cells whose centroid is inside the district and which do not intersect any coverage circle; capped at 5 000 cells per response (cells beyond the cap are counted but not returned, `zero_coverage_truncated: true`). Coverage circles include cameras in every status except `retired`. `priority_score = min(100, 10*age_years_over_threshold + 30*is_analog + 25*amc_expired + 15*amc_expiring + 0.2*offline_pct_24h + 10*near_poi)`.

`GET /gap-analysis/export?format=csv|pdf` — cameras.export. CSV = a zip? **No**: one CSV with a `section` column (`by_area`, `by_district`, `department_gaps`, `uncovered_pois`, `offline_hotspots`, `metadata_gaps`, `ageing`, `recommendations`) and the union of the section columns. PDF = summary page, tables, static map image of zero-coverage cells + cameras (matplotlib-free: reportlab drawing of scaled lat/lon on a white canvas with district outline), recommendations. Stored in `report_files` (`gap_csv`/`gap_pdf`), audit `report.gap`, headers as for `/cameras/export`.

### 5.9 Detections (plate reads)

`GET /detections` — analytics.read. Filters: `plate` (normalised with §3.4, exact on `plate_norm`; add `fuzzy=1` for `levenshtein ≤ 2 OR similarity(plate_raw) > 0.6`), `camera_id`, `department_id`, `district`, `from` (default now − 24 h), `to`, `min_conf` (0..1), `valid_only` (default false), `mode`, `sighting_id`. Sort: `captured_at` (default desc), `confidence`, `plate_norm`, `camera_name`. Items:

```json
{
  "id": 9876, "camera": {"…CameraSummary…"}, "sighting_id": 512, "captured_at": "2026-09-04T10:15:30.123Z",
  "stream_pts": 3214.2, "frame_index": 16071, "plate_raw": "GJ 01 AB 1234", "plate_norm": "GJ01AB1234", "plate_display": "GJ 01 AB 1234",
  "is_valid_format": true, "confidence": 0.93, "bbox": [412, 388, 236, 64], "crop_url": "/media/crops/12/2026-09-04/3f9a1c….jpg",
  "crop_sha256": "e3b0c442…", "mode": "live", "watchlist_hit": true, "alert_id": 77, "qa_label": null
}
```

`GET /detections/{id}` → the item above plus `"sighting": {…Sighting…}`, `"frame_url"`, `"frame_sha256"`, `"events": [...]`.

`GET /sightings` — analytics.read. Filters `plate`, `camera_id`, `from`, `to`, `closed`, `min_conf`. Items = `Sighting`:

```json
{
  "id": 512, "camera": {"…CameraSummary…"}, "plate_norm": "GJ01AB1234", "plate_display": "GJ 01 AB 1234", "is_valid_format": true,
  "first_seen": "2026-09-04T10:15:28.000Z", "last_seen": "2026-09-04T10:15:33.400Z", "read_count": 3, "best_conf": 0.95,
  "best_read_id": 9876, "best_crop_url": "/media/crops/12/2026-09-04/3f9a1c….jpg", "frame_url": "/media/frames/12/2026-09-04/12-GJ01AB1234-1725444928000.jpg",
  "frame_sha256": "…", "closed": true, "mode": "live", "recording_available": true
}
```

### 5.10 Vehicles: search, route, confirm, route PDF

`GET /vehicles/search?q=&from=&to=&camera_id=&department_id=&limit=50` — analytics.read; audit `vehicle.search` (`after={q, normalised, exact, fuzzy}` counts). Default window: last `route.default_window_h` hours. `limit` caps each of the two arrays (max 200).

```json
{
  "query": {"raw": "GJ 01 AB 1234", "normalised": "GJ01AB1234", "is_valid_format": true, "from": "2026-09-03T10:20:00.000Z", "to": "2026-09-04T10:20:00.000Z"},
  "exact": [{"…Sighting…", "match": "exact", "score": 1.0, "confirmation": null}],
  "fuzzy": [{"…Sighting…", "match": "fuzzy", "distance": 1, "similarity": 0.82, "score": 0.74, "confirmation": "confirmed", "sample_reads": [{"id": 9901, "plate_raw": "GJ 01 A8 1234", "confidence": 0.71, "crop_url": "…"}]}],
  "cameras_seen": 3
}
```

Fuzzy ranking: SQL over `plate_reads` in the window with `levenshtein(plate_norm, :q) <= 2 OR similarity(plate_raw, :q) > 0.6`, grouped by `sighting_id`; `score = (1 − distance/3) * 0.7 + best_conf * 0.3`; ordered by score desc. Sightings already in `exact` are excluded from `fuzzy`. `confirmation` reflects `route_confirmations` for `(query_plate = normalised, sighting_id)`.

`POST /vehicles/{plate}/confirm` — route.confirm; body `{"decisions": [{"sighting_id": 640, "decision": "confirmed"}, {"sighting_id": 641, "decision": "rejected"}]}` → `{"saved": 2}`. Upserts `route_confirmations`; audit `vehicle.confirm` with the decisions.

`GET /vehicles/{plate}/route?from=&to=&include=confirmed|all&max_gap_h=` — analytics.read; audit `vehicle.route`. `{plate}` is normalised. `include=confirmed` (default): exact sightings + fuzzy sightings with decision `confirmed`, minus any with decision `rejected`; `include=all`: exact + all fuzzy candidates (marked). Sightings are ordered by `first_seen`; consecutive sightings on the **same camera** within 60 s are merged into one stop (`read_count` summed, `last_seen` extended). For each consecutive pair: `distance_km = haversine`, `minutes = (first_seen_j − last_seen_i)`, `speed_kmh = distance_km / hours` (if `minutes <= 0` → speed = null, flag `overlap`); flag `implausible_speed` when `speed_kmh > route.speed_flag_kmh`; flag `long_gap` when `minutes > max_gap_h*60` (default 6 h).

```json
{
  "plate": "GJ01AB1234",
  "plate_display": "GJ 01 AB 1234",
  "window": {"from": "2026-09-03T10:20:00.000Z", "to": "2026-09-04T10:20:00.000Z", "include": "confirmed"},
  "sightings": [
    {"seq": 1, "sighting_id": 512, "camera": {"…CameraSummary…"}, "first_seen": "2026-09-04T10:15:28.000Z", "last_seen": "2026-09-04T10:15:33.400Z", "read_count": 3, "best_conf": 0.95, "crop_url": "…", "frame_url": "…", "match": "exact", "confirmation": null, "recording_available": true},
    {"seq": 2, "sighting_id": 530, "camera": {"…"}, "first_seen": "2026-09-04T10:15:48.000Z", "last_seen": "2026-09-04T10:15:52.000Z", "read_count": 2, "best_conf": 0.90, "crop_url": "…", "frame_url": "…", "match": "fuzzy", "confirmation": "confirmed", "recording_available": true}
  ],
  "polyline": [[23.2236, 72.648], [23.2275, 72.6485], [23.217, 72.636]],
  "legs": [{"from_seq": 1, "to_seq": 2, "distance_km": 0.44, "minutes": 0.24, "speed_kmh": 108.6, "flags": []}],
  "flags": [{"from_seq": 2, "to_seq": 3, "type": "implausible_speed", "distance_km": 1.7, "minutes": 0.3, "speed_kmh": 340.0, "message": "340 km/h between Civil Hospital Gandhinagar and CH-0 Circle (0.3 min for 1.7 km)"}],
  "total_distance_km": 2.14,
  "total_duration_min": 0.9,
  "cameras_count": 3,
  "loop_resets_in_window": 2
}
```

`polyline` has one `[lat, lon]` per sighting (straight segments); cameras without coordinates are listed but skipped in the polyline (`"in_polyline": false`). On synthetic feeds (90 s loop, cameras 1–7 km apart) every leg is flagged `implausible_speed`; that is correct behaviour and the UI shows it as an amber badge, not an error.

`GET /vehicles/{plate}/route.pdf?from=&to=&include=` — reports.export; audit `report.route`. `application/pdf`, `Content-Disposition: attachment; filename="route_GJ01AB1234_<YYYYMMDD_HHMM>IST.pdf"`. Content: header (product, plate display, window in IST, generated by/at IST, version), summary line, static map (reportlab canvas: district outline if available, numbered markers, straight polyline), table `# | Camera | Department | District | First seen IST | Last seen IST | Reads | Conf | Match | SHA-256 (crop)`, then a page of crops (max 24, 4 per row) with sighting numbers, flags section, footer `Evidence hash: <sha256 of the PDF bytes before the footer>` on each page plus the watermark `Sentinel Gujarat · <username> · <IST timestamp> · purpose-limited to law-enforcement use`. Recorded in `report_files` (`route_pdf`) with `X-Sentinel-Sha256` header.

### 5.11 Watchlist

| Method & path | Permission | Notes |
|---|---|---|
| `GET /watchlist` | analytics.read | Filters `q`, `entity_type`, `reason`, `priority`, `source`, `is_active` (default `true`; `all` for both), `expired` (bool). Sort: `created_at` (default desc), `plate_norm`, `priority`, `hit_count`, `last_hit_at`. Items: every column + `plate_display`, `added_by_username`, `is_effective`, `alerts_24h` |
| `POST /watchlist` | watchlist.write | Body `{entity_type, plate?, name?, reason, priority?, source?, notes?, expires_at?, is_active?}`. `plate` is normalised; if `entity_type='vehicle'` and `is_valid_format` is false → `422 field plate "not a valid Indian registration format"`; an active duplicate → `409 conflict` with `{"existing_id": n}`. `201`. Reloads the matcher. Audit `watchlist.create` |
| `PUT /watchlist/{id}` | watchlist.write | Partial update; same validation. Audit with diff |
| `DELETE /watchlist/{id}` | watchlist.write | Hard delete (alerts keep `watchlist_id` null via SET NULL and the denormalised `plate_norm`). `204`. Audit `watchlist.delete` |
| `GET /watchlist/import/template` | watchlist.write | CSV template (§12.2) |
| `POST /watchlist/import/csv` | watchlist.write | multipart `file`, `dry_run`. Rows upsert on `plate_norm` for vehicles (update reason/priority/notes/expiry; reactivate); persons insert. Response `{rows_total, added, updated, errors: [{row, field, message}], error_report_url, duration_ms}`. Audit `watchlist.import` |

### 5.12 Alerts

`GET /alerts` — analytics.read (scoped). Filters: `status` (comma list; default `new,acknowledged`), `type`, `priority`, `camera_id`, `department_id`, `plate`, `from` (default now − 24 h), `to`, `escalated` (bool). Sort: `priority_rank` (critical→low) then `created_at desc` when `sort=priority` (**default**); or `created_at`. Items = `Alert`:

```json
{
  "id": 77, "type": "watchlist_hit", "status": "new", "priority": "critical", "confidence_level": "exact", "escalated": false,
  "created_at": "2026-09-04T10:15:29.400Z", "updated_at": "2026-09-04T10:15:29.400Z", "latency_ms": 1277,
  "camera": {"…CameraSummary…"},
  "watchlist": {"id": 5, "entity_type": "vehicle", "plate_norm": "GJ01AB1234", "plate_display": "GJ 01 AB 1234", "name": "Stolen Swift – FIR 123/2026", "reason": "stolen", "priority": "critical", "source": "own"},
  "read": {"id": 9876, "plate_raw": "GJ 01 AB 1234", "plate_norm": "GJ01AB1234", "confidence": 0.93, "captured_at": "2026-09-04T10:15:28.123Z", "crop_url": "/media/crops/12/2026-09-04/3f9a1c….jpg", "crop_sha256": "…"},
  "sighting_id": 512, "plate_norm": "GJ01AB1234", "snapshot_url": "/media/crops/12/2026-09-04/3f9a1c….jpg", "snapshot_sha256": "…",
  "read_count": 3, "last_read_at": "2026-09-04T10:15:33.400Z",
  "acknowledged_by": null, "acknowledged_by_username": null, "acknowledged_at": null, "closed_by": null, "closed_at": null, "outcome": null, "note": null,
  "recording_available": true
}
```

For `camera_offline` alerts `watchlist`, `read`, `sighting_id` are null and `snapshot_url` is the last snapshot. `GET /alerts/{id}` → `Alert` + `"reads": [...]` (all attached reads) + `"events": [...]`.

`POST /alerts/{id}/ack` — alerts.ack; body `{"note": "Unit dispatched"}` (note optional). Only from `new` → `acknowledged`; otherwise `409`. Returns `Alert`. Broadcast `alert_update`. Audit `alert.ack`.

`POST /alerts/{id}/close` — alerts.ack; body `{"note": "Vehicle recovered", "outcome": "resolved"}` (`outcome` default `resolved`). From `new` or `acknowledged` → `closed`; closing from `new` also sets `acknowledged_*`. `409` if already closed. Broadcast `alert_update`. Audit `alert.close`.

**Alert creation rules (matcher, in-process, §5.6 of the plan):**

1. On every accepted read with `is_valid_format=true`: exact lookup in the in-memory map `{plate_norm → watchlist row}` (only effective vehicle rows; rebuilt on any watchlist change and every 60 s). Hit → `confidence_level='exact'`.
2. Else if `read.confidence >= alerts.fuzzy_min_conf`: any map key with `levenshtein(read.plate_norm, key) == 1` → `confidence_level='possible'` (first key in priority order if several).
3. Suppression: an alert with the same `(watchlist_id, camera_id)` created within `alerts.suppression_s` (or still `new`/`acknowledged` and created within 10 min) → attach the read (`read_count += 1`, `last_read_at`, upgrade `confidence_level` to `exact` if the new read is exact) and broadcast `alert_update` instead of creating a new alert.
4. Priority (W6): `reason_floor = {stolen: critical, wanted: critical, blacklisted: high, arrested: high, missing: medium, unidentified_body: medium, suspect: low, other: low}`; `effective = max(watchlist.priority, reason_floor)` on the order `critical > high > medium > low`; if `confidence_level == 'possible'` step down one level (`critical→high→medium→low→low`).
5. Insert `alerts`, `events(type='watchlist_hit', is_auto=true)`, update `watchlist.hit_count/last_hit_at`, set `latency_ms`, broadcast `alert` on `/ws/alerts` (scoped), enqueue webhooks (`alert.created`) and Telegram (if configured and priority ≥ `notify.telegram_min_priority`).
6. `camera_offline` (§5.6) and `intrusion` (priority = zone priority, suppression 120 s per zone) use the same channel.

### 5.13 Events

`GET /events` — analytics.read. Filters `camera_id`, `department_id`, `type` (comma list), `is_auto`, `from` (default now − 24 h), `to`, `sighting_id`, `alert_id`. Sort `occurred_at` (default desc). Items: all columns + `camera: CameraSummary`, `created_by_username`, `frame_url`, `type_label`.

`POST /events` — events.write; body `{"camera_id": 12, "type": "suspicious", "occurred_at": "2026-09-04T10:15:00.000Z", "note": "Vehicle circling twice", "sighting_id": 512, "read_id": 9876}` (`occurred_at` defaults to now; only manual types accepted: `accident|suspicious|checkpoint|other`; `422` otherwise). `201`. Audit `event.create`. Broadcast nothing (events are pulled).

### 5.14 Dashboard

`GET /dashboard/stats` — analytics.read (scoped):

```json
{
  "generated_at": "…",
  "cameras": {"total": 51, "online": 8, "degraded": 1, "offline": 41, "unknown": 1, "anpr_live": 8, "recording": 8},
  "reads": {"last_1h": 412, "last_24h": 9000, "total": 15321, "last_read_at": "…"},
  "sightings": {"last_24h": 1420, "total": 2001, "valid_format_pct_24h": 88.4},
  "alerts": {"new": 3, "acknowledged": 1, "last_24h": 12, "critical_open": 1, "avg_latency_ms_24h": 1450},
  "watchlist": {"active": 24, "vehicles": 22, "persons": 2},
  "object_counts_24h": {"person": 1200, "car": 3400, "motorcycle": 2100, "bus": 90, "truck": 210, "bicycle": 60},
  "events_24h": 34,
  "disk": {"data_used_bytes": 1, "data_free_bytes": 2, "recordings_used_bytes": 3},
  "anpr_workers": [{"…as in /health/summary…"}]
}
```

`GET /dashboard/charts?from=&to=&camera_id=&department_id=&bucket=hour|day` — analytics.read. Buckets are in **IST** (`date_trunc` on `captured_at AT TIME ZONE 'Asia/Kolkata'`); each bucket carries `bucket_start` (UTC ISO) and `label_ist` (`04 Sep 15:00`).

```json
{
  "window": {"from": "…", "to": "…", "bucket": "hour"},
  "vehicles_per_hour": [{"camera_id": 12, "camera_name": "…", "bucket_start": "…", "label_ist": "04 Sep 15:00", "sightings": 42}],
  "top_plates": [{"plate_norm": "GJ01AB1234", "plate_display": "GJ 01 AB 1234", "sightings": 9, "cameras": 3, "last_seen": "…"}],
  "alerts_per_camera_day": [{"camera_id": 12, "camera_name": "…", "bucket_start": "…", "label_ist": "04 Sep", "alerts": 3}],
  "object_counts": [{"camera_id": 12, "camera_name": "…", "bucket_start": "…", "label_ist": "04 Sep 15:00", "class": "car", "count": 130}],
  "reads_by_confidence": [{"bin": "0.9-1.0", "reads": 500}, {"bin": "0.8-0.9", "reads": 300}]
}
```

`top_plates` max 20; other series max 500 points (the API widens the bucket to `day` automatically when `hour` would exceed that and says so with `"bucket": "day"`).

### 5.15 Reports

| Method & path | Permission | Notes |
|---|---|---|
| `GET /reports/detections?from=&to=&camera_id=&department_id=&district=&min_conf=&valid_only=&format=csv|pdf` | reports.export | **Output report (O1)**. `from` required, `to` default now, window ≤ 7 days. Audit `report.detections`. Stored in `report_files`, header `X-Sentinel-Sha256`, `Content-Disposition: attachment; filename="detections_<from>_<to>_IST.csv"`. Sync generation; the API caps CSV at 200 000 rows and PDF at 5 000 rows (thumbnails for the first 50) and reports the cap in the summary |
| `GET /reports/quality?from=&to=&camera_id=&format=json|pdf` | analytics.read (json) / reports.export (pdf) | A8 analytics-quality section (below) |
| `GET /reports/history` | reports.export | Paginated `report_files` (scoped by `created_by` for non-admins): `{id, type, path, url, sha256, size_bytes, params, row_count, created_by_username, created_at}`. Download via `url` (`/media/reports/...`); a download writes audit `report.download` |
| `GET /qa/sample?camera_id=&n=30&from=&to=` | analytics.read | `n` random unlabelled reads (`valid_only` not applied) with crop URLs for spot-checking |
| `POST /qa/labels` | events.write | Body `{"labels": [{"read_id": 9876, "true_plate": "GJ 01 AB 1234"}]}` (`true_plate` normalised; `""` = unreadable). Upsert per read. Returns `{"saved": n, "exact": n, "char_accuracy_pct": 96.2}`. Audit `qa.label` |

Detections CSV columns (exact order): `captured_at_ist, captured_at_utc, camera_id, camera_external_id, camera_name, department, district, lat, lon, plate, plate_raw, is_valid_format, confidence, sighting_id, read_id, crop_path, crop_sha256, mode`. Last line: `# Sentinel Gujarat 1.0.0-phase1 | rows=<n> | generated <IST> by <username> | filters=<json> | sha256(rows)=<hex>`. The PDF has: cover summary (window in IST, cameras, departments, reads, valid %, sightings, unique plates, object-count totals per class, alerts in window), a table (≤ 5 000 rows, 40 per page), thumbnails of the first 50 reads with SHA-256 (first 16 hex chars + "…"), quality section (from `/reports/quality` for the same window), footer watermark as in the route PDF.

Quality JSON:

```json
{
  "window": {"from": "…", "to": "…", "camera_id": null},
  "reads_total": 9000, "reads_valid_format": 7956, "valid_format_pct": 88.4, "sightings_total": 1420, "unique_plates": 610,
  "mean_confidence": 0.87, "reads_per_camera": [{"camera_id": 12, "camera_name": "…", "reads": 1200, "valid_pct": 91.0, "mean_conf": 0.9}],
  "labelled": 90, "exact_match_pct": 84.4, "char_accuracy_pct": 96.7,
  "per_camera_accuracy": [{"camera_id": 12, "camera_name": "…", "labelled": 30, "exact_pct": 86.7, "char_accuracy_pct": 97.1}],
  "confusions": [{"expected": "B", "got": "8", "count": 5}],
  "sample": [{"read_id": 1, "plate_norm": "…", "true_plate": "…", "is_match": true, "crop_url": "…"}]
}
```

### 5.16 Recordings and clips (V4)

| Method & path | Permission | Notes |
|---|---|---|
| `GET /recordings/{camera_id}?from=&to=` | cameras.read | Proxies `GET {MEDIAMTX_PLAYBACK_URL}/list?path=<playback_path>&start=<from>&end=<to>` (defaults: last 12 h). Response `{camera_id, playback_path, record_enabled, retention_h: 12, segments: [{start, duration_s, end, url}]}` where `url` is `/playback/get?path=cam_12&start=<RFC3339>&duration=<s>&format=mp4`. Empty list when `record_enabled=false` or MediaMTX returns 404 (never a 502) |
| `GET /recordings/{camera_id}/play?at=&before_s=10&duration_s=30` | cameras.read | Resolves the segment containing `at − before_s` and returns `{"url": "/playback/get?path=cam_12&start=…&duration=30&format=mp4", "start": "…", "duration_s": 30, "available": true}`; `available=false` with `url=null` if nothing is recorded there. Audit `recording.play` |
| `POST /clips` | events.write | Body `{"camera_id": 12, "start_at": "…", "duration_s": 30, "alert_id": 77, "sighting_id": 512}` (`duration_s` 1..120, default 30). The API downloads `GET {MEDIAMTX_PLAYBACK_URL}/get?path=…&start=…&duration=…&format=mp4` to `clips/<camera_id>/<id>.mp4`, computes sha256, inserts `clips`. `201 Clip` (`{…columns…, url: "/media/clips/12/7.mp4", camera: CameraSummary, created_by_username}`). `409 conflict` when no recording covers `start_at`. Audit `clip.create` |
| `GET /clips?camera_id=&alert_id=&sighting_id=` | analytics.read | Paginated `Clip` |
| `GET /clips/{id}` | analytics.read | `Clip` |

`playback_path` = `cam_<id>_h264` for H.265 cameras (they record the transcoded path), else `cam_<id>`. "Play recording" on an alert/sighting uses `at = first_seen` (sighting) or `read.captured_at` (alert), `before_s = 10`.

### 5.17 Object counts and zones

`GET /object-counts?camera_id=&from=&to=&class=&bucket=minute|hour` — cameras.read. Default `from` = now − 1 h, `bucket=minute`. Response `{"items": [{"camera_id": 12, "bucket_start": "…", "label_ist": "…", "class": "car", "count": 7}], "totals": {"car": 340, "person": 120}}` (no pagination; ≤ 10 000 points, else the API forces `bucket=hour`).

Zones (P2, backend implements CRUD from day one; the worker honours them when present):

| Method & path | Permission | Body / response |
|---|---|---|
| `GET /zones?camera_id=` | cameras.read | list of zones |
| `POST /zones` | zones.write | `{camera_id, name, polygon: [[0.1,0.5],[0.9,0.5],[0.9,0.95],[0.1,0.95]], active_from: "22:00", active_to: "06:00", classes: ["person"], dwell_s: 2, priority: "medium", is_active: true}` → `201` |
| `PUT /zones/{id}` / `DELETE /zones/{id}` | zones.write | partial update / `204` |

### 5.18 Evidence

`GET /evidence/verify?path=crops/12/2026-09-04/3f9a1c….jpg` — analytics.read; audit `evidence.verify`.

```json
{"path": "crops/12/2026-09-04/3f9a1c….jpg", "exists": true, "entity": "plate_read", "entity_id": 9876, "stored_sha256": "e3b0…", "computed_sha256": "e3b0…", "match": true, "size_bytes": 18234, "checked_at": "…"}
```

`entity` is found by looking the path up in `plate_reads.crop_path`, `sightings.frame_path`, `alerts.snapshot_path`, `events.frame_path`, `clips.path`, `report_files.path` (first hit); `entity=null, stored_sha256=null, match=null` when unknown. Path traversal (`..`, absolute) → `422`.

### 5.19 External lookup (X1 mocks)

`GET /external/vahan/{plate}` — external.lookup; audit `external.lookup`. Deterministic mock derived from a hash of the plate (so the same plate always returns the same owner):

```json
{"source": "VAHAN (mock adapter)", "adapter": "vahan_mock", "plate": "GJ01AB1234", "found": true, "owner_name": "R. Patel", "vehicle_class": "Motor Car", "maker_model": "Maruti Swift VXI", "fuel": "Petrol", "colour": "White", "registration_date": "2019-06-12", "rto": "GJ-01 Ahmedabad", "insurance_valid_till": "2027-03-31", "fitness_valid_till": "2034-06-11", "note": "Mock data – integration-ready adapter; real VAHAN access requires NIC gateway credentials"}
```

`GET /external/sarthi/{dl_number}` — same pattern: `{source: "SARTHI (mock adapter)", dl_number, found, holder_name, dob, valid_from, valid_till, classes: ["LMV","MCWG"], rto, status: "active", note}`. Both adapters implement `ExternalLookupAdapter.lookup(key) -> dict` in `backend/app/adapters/`. Unknown plates (hash bucket 1 in 5) return `found: false`.

### 5.20 Settings, webhooks, users, API keys

| Method & path | Permission | Notes |
|---|---|---|
| `GET /settings` | admin.settings | `{"items": [{"key": "catalogue.base_url", "value": "http://api:8000/mock-sandbox", "is_secret": false, "updated_by_username": "jury_admin", "updated_at": "…"}, …]}`; secret values are returned as `"********"` (empty secret → `""`) |
| `PUT /settings` | admin.settings | Body `{"values": {"catalogue.base_url": "http://10.0.0.5", "catalogue.auth_type": "basic", "catalogue.auth_username": "team", "catalogue.auth_password": "s3cret"}}`. Unknown key → 422; a secret submitted as `"********"` is left unchanged. Validation per key (URL, enum, number ranges, `field_map` object). Returns the `GET` shape. Audit `settings.update` with before/after (secrets redacted). Side effects: retention/gap/alert settings apply immediately; the matcher reloads |
| `POST /settings/catalogue/test` | admin.settings | Body optional (uses the saved settings or `{base_url, auth_type, …}` overrides). Fetches the catalogue and returns `{"ok": true, "status": 200, "count": 50, "sample": {…first item…}, "mapped_sample": {…CameraImportRow…}, "unmapped_fields": ["camera_type"], "duration_ms": 120}` or `{"ok": false, "error": "…"}` (200 either way) |
| `GET /settings/public` | settings.read_public | Non-secret keys only, as `{key: value}`; the UI reads `ui.*`, `route.*`, `gap.*`, `alerts.escalate_minutes` |
| `GET /webhooks` | admin.settings | list (secret masked) |
| `POST /webhooks` | admin.settings | `{name, url, secret?, event_types: [...], is_active?}` → `201` |
| `PUT /webhooks/{id}` / `DELETE /webhooks/{id}` | admin.settings | |
| `POST /webhooks/{id}/test` | admin.settings | Sends a `ping` event; returns `{status, duration_ms, error}` |
| `GET /users` | admin.users | Filters `q`, `role`, `is_active`. Items: all columns except `password_hash`, plus `department_name` |
| `POST /users` | admin.users | `{username, password, full_name, role, department_id?, district?}` → `201`. `409` on duplicate username |
| `PUT /users/{id}` | admin.users | Partial: `full_name, role, department_id, district, is_active` |
| `POST /users/{id}/reset-password` | admin.users | `{password}` → `204`. Audit `user.reset_password` |
| `DELETE /users/{id}` | admin.users | Deactivate (`is_active=false`). Cannot deactivate yourself → 409 |
| `GET /api-keys` | admin.apikeys | `{items: [{id, name, key_prefix, scope, is_active, created_by_username, last_used_at, created_at}]}` |
| `POST /api-keys` | admin.apikeys | `{name, scope}` → `201 {id, name, scope, key: "sk_…", key_prefix, created_at}` — the only time `key` is returned |
| `DELETE /api-keys/{id}` | admin.apikeys | Sets `is_active=false`. `204` |
| `GET /audit` | admin.audit | Filters `user` (username or id), `action` (prefix match allowed, e.g. `camera.`), `entity`, `entity_id`, `from` (default now − 7 d), `to`, `q`. Sort `ts` only. Items: all columns + `username`. `GET /audit/export?format=csv` with the same filters (audit `report.download`) |

Webhook delivery: `POST <url>` with body `{"event": "alert.created", "ts": "…", "delivery_id": "<uuid>", "data": {…Alert…}}`, headers `Content-Type: application/json`, `X-Sentinel-Event`, `X-Sentinel-Delivery`, `X-Sentinel-Signature: sha256=<hex hmac of body with secret>` (omitted if no secret), `User-Agent: SentinelGujarat/1.0.0-phase1`. Timeout 5 s; retries after 2, 4, 8 s; `last_status/last_error` updated; deliveries never block the request path (background task).

### 5.21 Internal (ANPR worker) — full contract in §7

`GET /internal/anpr-config`, `POST /internal/detections`, `POST /internal/snapshots`, `POST /internal/object-counts`, `POST /internal/heartbeat`, `POST /internal/events`. All require `X-API-Key` with scope `internal`.

### 5.22 Mock sandbox and health

`GET /mock-sandbox/api/ingest` — no auth, only when `MOCK_SANDBOX=1` (else 404). Returns the §6 list. Also `GET /mock-sandbox/` → `{"name": "Sentinel Gujarat mock sandbox", "cameras": 50, "live": 8}`.

`POST /mock-sandbox/webhook-sink` — no auth (it is the target of a test webhook; only when `MOCK_SANDBOX=1`): stores the last 20 request bodies + headers in memory and returns `204`. `GET /mock-sandbox/webhook-sink` — admin.settings: `{"deliveries": [{"received_at": "…", "headers": {"x-sentinel-event": "alert.created", "x-sentinel-signature": "sha256=…"}, "body": {…}}]}`.

`GET /settings/public` also returns `"mock_sandbox": true|false` (from `MOCK_SANDBOX`) so the UI can show the environment badge.

`GET /healthz` (also `/api/healthz`) — no auth. `200 {"status": "ok", "version": "1.0.0-phase1", "time": "…", "db": "ok", "mediamtx": "ok"|"down", "anpr_workers": 2, "uptime_s": 1234}`; `503 {"status": "degraded", "db": "down", …}` when the DB query fails (MediaMTX being down does **not** make it 503).

---

## 6. Mock sandbox catalogue (`GET /api/mock-sandbox/api/ingest`)

### 6.1 Shape

Served by the API when `MOCK_SANDBOX=1`, modelled on the organiser's description (camera id, name, department, location, district, codec, resolution, fps, live flag, RTSP/WHEP/HLS URLs). It is a JSON **array** (no wrapper object), one object per camera, `Content-Type: application/json`:

```json
[
  {
    "id": 1,
    "name": "Sachivalaya Gate 1",
    "department": "Police",
    "district": "Gandhinagar",
    "type": "bullet",
    "location": {"lat": 23.2236, "lon": 72.648, "address": "Sachivalaya, Sector 10, Gandhinagar"},
    "codec": "H264",
    "resolution": "1280x720",
    "fps": 10,
    "live": true,
    "rtsp_url": "rtsp://mediamtx:8554/stream/1",
    "whep_url": "http://mediamtx:8889/stream/1/whep",
    "hls_url": "http://mediamtx:8888/stream/1/index.m3u8"
  }
]
```

The organiser's real catalogue **will differ** (field names, nesting, wrapper object, extra properties such as `stream_properties`, HLS on port 80 under `/live/…`). The importer must therefore:

- accept a bare list **or** an object whose first list-valued key among `cameras`, `data`, `items`, `results`, `streams` is the list;
- map fields through `catalogue.field_map` (§6.3), where every target has an ordered list of candidate source paths (dotted, e.g. `location.lat`); the first present, non-empty candidate wins;
- treat `id` as a string `external_id` (`str(value).strip()`);
- tolerate missing codec/resolution/fps/live (nulls, `codec='UNKNOWN'`, `live=null`);
- record every source key it did not use in `unmapped_fields` of `POST /settings/catalogue/test` so the operator can fix the map in Settings within minutes (R12);
- never use the catalogue's `whep_url`/`hls_url` for playback — they are stored for reference only; every stream goes through our relay.

### 6.2 The 50 mock cameras

Ids 1–8 are `live=true` and stream synthetic video from `stream/<id>` (§12.4); 9–50 are `live=false` with the same URL pattern (the path exists in MediaMTX config only for 1–8, so the others are legitimately offline). Department strings are deliberately the organiser's display names so alias mapping is exercised. Address = `"<name>, <district>"` unless stated. Camera 8 is H.265.

| id | name | department | district | lat | lon | codec | resolution | fps | live | type |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Sachivalaya Gate 1 | Police | Gandhinagar | 23.2236 | 72.6480 | H264 | 1280x720 | 10 | true | bullet |
| 2 | Sector 11 GSRTC Bus Stand | GSRTC | Gandhinagar | 23.2300 | 72.6300 | H264 | 1280x720 | 10 | true | dome |
| 3 | Civil Hospital Gandhinagar OPD Gate | Health | Gandhinagar | 23.2275 | 72.6485 | H264 | 1280x720 | 10 | true | bullet |
| 4 | Infocity Circle | Municipal Corporation | Gandhinagar | 23.1890 | 72.6350 | H264 | 1280x720 | 10 | true | ptz |
| 5 | Pethapur Gram Panchayat Chowk | Panchayat | Gandhinagar | 23.2670 | 72.6520 | H264 | 1280x720 | 10 | true | bullet |
| 6 | CH-0 Circle | Police | Gandhinagar | 23.2170 | 72.6360 | H264 | 1280x720 | 10 | true | anpr |
| 7 | Mahatma Mandir Approach | Municipal Corporation | Gandhinagar | 23.2247 | 72.6540 | H264 | 1280x720 | 10 | true | dome |
| 8 | Koba Circle Highway | Police | Gandhinagar | 23.1650 | 72.6550 | H265 | 1280x720 | 10 | true | anpr |
| 9 | Kalupur Railway Station East | Police | Ahmedabad | 23.0269 | 72.6013 | H264 | 1920x1080 | 25 | false | bullet |
| 10 | Civil Hospital Asarwa Trauma Gate | Health | Ahmedabad | 23.0532 | 72.6067 | H265 | 1920x1080 | 25 | false | dome |
| 11 | Gita Mandir Central Bus Station | GSRTC | Ahmedabad | 23.0090 | 72.5960 | H264 | 1920x1080 | 25 | false | ptz |
| 12 | Iskcon Cross Roads SG Highway | Municipal Corporation | Ahmedabad | 23.0280 | 72.5070 | H264 | 1920x1080 | 25 | false | anpr |
| 13 | Sabarmati Riverfront Ellis Bridge | Municipal Corporation | Ahmedabad | 23.0225 | 72.5745 | H265 | 1920x1080 | 25 | false | ptz |
| 14 | Narol Circle | Police | Ahmedabad | 22.9660 | 72.6160 | H264 | 1280x720 | 25 | false | anpr |
| 15 | Vastral Gram Panchayat Road | Panchayat | Ahmedabad | 23.0060 | 72.6630 | H264 | 1280x720 | 15 | false | bullet |
| 16 | Pakwan Cross Roads SG Highway | Police | Ahmedabad | 23.0470 | 72.5150 | H265 | 1920x1080 | 25 | false | anpr |
| 17 | Airport Road Hansol | Police | Ahmedabad | 23.0740 | 72.6300 | H264 | 1920x1080 | 25 | false | bullet |
| 18 | Lal Darwaja AMTS Terminus | Municipal Corporation | Ahmedabad | 23.0250 | 72.5820 | H264 | 1280x720 | 15 | false | dome |
| 19 | Vadodara Railway Station Circle | Police | Vadodara | 22.3103 | 73.1815 | H264 | 1920x1080 | 25 | false | anpr |
| 20 | SSG Hospital Emergency Gate | Health | Vadodara | 22.3086 | 73.1898 | H265 | 1920x1080 | 25 | false | dome |
| 21 | Vadodara Central Bus Stand | GSRTC | Vadodara | 22.3125 | 73.1795 | H264 | 1920x1080 | 25 | false | ptz |
| 22 | Alkapuri Circle | Municipal Corporation | Vadodara | 22.3105 | 73.1690 | H264 | 1280x720 | 25 | false | bullet |
| 23 | Makarpura GIDC Gate | Panchayat | Vadodara | 22.2540 | 73.1880 | H264 | 1280x720 | 15 | false | bullet |
| 24 | Akota Stadium Road | Municipal Corporation | Vadodara | 22.2960 | 73.1650 | H265 | 1920x1080 | 25 | false | dome |
| 25 | Surat Railway Station Approach | Police | Surat | 21.2049 | 72.8411 | H264 | 1920x1080 | 25 | false | anpr |
| 26 | New Civil Hospital Majura Gate | Health | Surat | 21.1820 | 72.8207 | H264 | 1920x1080 | 25 | false | dome |
| 27 | Surat Central Bus Station | GSRTC | Surat | 21.2020 | 72.8380 | H265 | 1920x1080 | 25 | false | ptz |
| 28 | Athwa Gate Circle | Municipal Corporation | Surat | 21.1790 | 72.8112 | H264 | 1920x1080 | 25 | false | anpr |
| 29 | Udhna Darwaja | Police | Surat | 21.1830 | 72.8380 | H264 | 1280x720 | 25 | false | bullet |
| 30 | Hazira Road Ichchhapore | Panchayat | Surat | 21.1600 | 72.7100 | H264 | 1280x720 | 15 | false | bullet |
| 31 | Rajkot Junction Forecourt | Police | Rajkot | 22.3095 | 70.7949 | H264 | 1920x1080 | 25 | false | anpr |
| 32 | PDU Civil Hospital Rajkot | Health | Rajkot | 22.2988 | 70.7929 | H265 | 1920x1080 | 25 | false | dome |
| 33 | Rajkot ST Bus Stand | GSRTC | Rajkot | 22.2940 | 70.7980 | H264 | 1920x1080 | 25 | false | ptz |
| 34 | Trikon Baug | Municipal Corporation | Rajkot | 22.2975 | 70.7990 | H264 | 1280x720 | 25 | false | bullet |
| 35 | Kalawad Road Panchayat Nagar | Panchayat | Rajkot | 22.2900 | 70.7600 | H264 | 1280x720 | 15 | false | bullet |
| 36 | Gondal Road Chowkdi | Police | Rajkot | 22.2700 | 70.8000 | H265 | 1920x1080 | 25 | false | anpr |
| 37 | Vapi GIDC Checkpost | Police | Valsad | 20.3893 | 72.9106 | H264 | 1280x720 | 15 | false | anpr |
| 38 | Valsad Civil Hospital | Health | Valsad | 20.6097 | 72.9342 | H264 | 1280x720 | 15 | false | dome |
| 39 | Bhilad Border Checkpost | Police | Valsad | 20.2617 | 72.9147 | H264 | 704x576 | 12 | false | analog |
| 40 | Dahod Bus Stand | GSRTC | Dahod | 22.8347 | 74.2555 | H264 | 1280x720 | 15 | false | bullet |
| 41 | Zalod Gram Panchayat | Panchayat | Dahod | 23.1005 | 74.1685 | H264 | 704x576 | 12 | false | analog |
| 42 | Limkheda NH-47 Checkpost | Police | Dahod | 22.8176 | 73.9773 | H265 | 1280x720 | 15 | false | anpr |
| 43 | Somnath Temple Road | Police | Gir Somnath | 20.8880 | 70.4013 | H264 | 1920x1080 | 25 | false | ptz |
| 44 | Veraval GSRTC Depot | GSRTC | Gir Somnath | 20.9077 | 70.3665 | H264 | 1280x720 | 15 | false | bullet |
| 45 | Veraval Civil Hospital | Health | Gir Somnath | 20.9100 | 70.3700 | H264 | 704x576 | 12 | false | analog |
| 46 | GG Hospital Jamnagar | Health | Jamnagar | 22.4707 | 70.0577 | H265 | 1920x1080 | 25 | false | dome |
| 47 | Bedi Port Road Checkpost | Police | Jamnagar | 22.5000 | 70.0500 | H264 | 1280x720 | 15 | false | anpr |
| 48 | Lalpur Gram Panchayat | Panchayat | Jamnagar | 22.1900 | 70.0300 | H264 | 704x576 | 12 | false | analog |
| 49 | Dwarka Temple Gate | Police | Devbhumi Dwarka | 22.2376 | 68.9674 | H264 | 1920x1080 | 25 | false | ptz |
| 50 | Khambhaliya GSRTC Depot | GSRTC | Devbhumi Dwarka | 22.2095 | 69.6519 | H264 | 1280x720 | 15 | false | bullet |

The backend keeps this table in `backend/app/services/mock_catalogue.py` as a Python list (or `backend/seeds/mock_catalogue.json`) and serves it verbatim. URLs are built as `rtsp://mediamtx:8554/stream/{id}`, `http://mediamtx:8889/stream/{id}/whep`, `http://mediamtx:8888/stream/{id}/index.m3u8`.

### 6.3 Default field map and department aliases (settings)

`catalogue.field_map` (targets are `CameraImportRow` fields; each value is an ordered candidate list):

```json
{
  "external_id": ["id", "camera_id", "cameraId", "stream_id", "uid"],
  "name": ["name", "camera_name", "title", "label"],
  "department_code": ["department", "dept", "department_name", "owner_department", "agency"],
  "district": ["district", "location.district", "city"],
  "lat": ["location.lat", "lat", "latitude", "gps.lat", "geo.lat", "coordinates.lat"],
  "lon": ["location.lon", "location.lng", "lon", "lng", "longitude", "gps.lon", "gps.lng", "geo.lon", "coordinates.lon"],
  "address": ["location.address", "address", "location.name", "place"],
  "type": ["type", "camera_type", "kind"],
  "codec": ["codec", "video_codec", "stream_properties.codec", "properties.codec"],
  "resolution": ["resolution", "stream_properties.resolution", "properties.resolution"],
  "fps": ["fps", "frame_rate", "stream_properties.fps", "properties.fps"],
  "live": ["live", "is_live", "online", "status.live", "active"],
  "rtsp_url": ["rtsp_url", "rtsp", "urls.rtsp", "streams.rtsp", "rtspUrl"],
  "whep_url": ["whep_url", "whep", "urls.whep", "streams.webrtc", "webrtc_url"],
  "hls_url": ["hls_url", "hls", "urls.hls", "streams.hls", "hlsUrl"],
  "police_station": ["police_station", "ps"],
  "install_date": ["install_date", "installed_on"]
}
```

`resolution` accepts `"1920x1080"`, `"1920×1080"`, `{"width":1920,"height":1080}` or `[1920,1080]`. `live` accepts booleans, `"true"/"false"`, `"live"/"offline"`, `1/0`.

`catalogue.department_aliases` (lowercase alias → department code); seeded from `departments.aliases` and editable:

```json
{"police": "POLICE", "gujarat police": "POLICE", "home": "POLICE", "health": "HEALTH", "health & family welfare": "HEALTH", "health and family welfare": "HEALTH", "hospital": "HEALTH", "gsrtc": "GSRTC", "transport corporation": "GSRTC", "panchayat": "PANCHAYAT", "gram panchayat": "PANCHAYAT", "rural development": "PANCHAYAT", "municipal corporation": "MUNICIPAL", "municipal": "MUNICIPAL", "amc": "MUNICIPAL", "urban development": "MUNICIPAL", "smart city": "MUNICIPAL", "rto": "RTO", "transport": "RTO", "food & civil supplies": "FCS", "food and civil supplies": "FCS", "fcs": "FCS"}
```

---

## 7. Internal ingestion contract (ANPR worker → API)

All endpoints under `/api/internal/*`, header `X-API-Key: <internal key>`. JSON bodies unless multipart is stated. The worker never talks to the DB or MediaMTX API; it reads RTSP from the relay and POSTs to the API. Every response is JSON; the worker logs and retries (2→30 s backoff) on network errors and `5xx`; it drops the batch on `4xx` (logs the body) except `401` (fatal: exit code 3).

### 7.1 `GET /internal/anpr-config?mode=live|preindex`

Returns what this worker must process. `live`: non-retired cameras with `anpr_enabled=true` and an `rtsp_url`. `preindex`: every non-retired camera with an `rtsp_url` whose catalogue `live` is not `false`. Both ordered by `id`.

```json
{
  "generated_at": "2026-09-04T10:00:00.000Z",
  "mode": "live",
  "settings": {"live_fps": 5, "preindex_fps": 1, "det_conf": 0.4, "min_plate_w": 60, "vote_window_s": 3, "sighting_close_s": 15, "object_detect": true, "object_every_n": 5, "snapshot_interval_s": 1},
  "cameras": [
    {
      "id": 12, "external_id": "3", "name": "Civil Hospital Gandhinagar OPD Gate", "relay_path": "cam_12",
      "rtsp_url": "rtsp://mediamtx:8554/cam_12", "codec": "H264", "lat": 23.2275, "lon": 72.6485,
      "anpr_enabled": true, "record_enabled": true, "mode": "live", "status": "online",
      "zones": [{"id": 1, "name": "Gate apron", "polygon": [[0.1, 0.5], [0.9, 0.5], [0.9, 0.95], [0.1, 0.95]], "active_from": "22:00", "active_to": "06:00", "classes": ["person"], "dwell_s": 2.0, "priority": "medium"}]
    }
  ]
}
```

`mode` per camera is `live`, `preindex` or `both` (a live camera also appears in the preindex list as `both`, so the preindex worker can skip it when `PREINDEX_SKIP_LIVE=1`, default `1`, to avoid double reads). `rtsp_url` always points at the relay (`cam_<id>`, the original codec — the worker decodes H.265 itself). The worker re-fetches every `CONFIG_RELOAD_S`; cameras that disappear are stopped, new ones started, changed `rtsp_url` restarted. It applies `ANPR_CAMERAS` and `ANPR_MAX_CAMERAS` after this list.

### 7.2 `POST /internal/detections` (multipart/form-data)

Sent every `POST_BATCH_S` per camera when there is anything to send (reads, sighting changes, finished object-count minutes, events). One camera per request. Fields:

- `payload` — JSON string (see below), always present.
- `<crop field>` — JPEG files referenced by `reads[].crop_file` (field names `crop_0`, `crop_1`, …).
- `<frame field>` — JPEG referenced by `sightings[].frame_file` (`frame_<index>`), only when the sighting's best read changed in this batch.
- Any file referenced by `events[].frame_file` (`event_frame_<index>`).

Payload:

```json
{
  "worker_id": "live-a1b2c3",
  "mode": "live",
  "camera_id": 12,
  "camera_external_id": "3",
  "sent_at": "2026-09-04T10:15:34.000Z",
  "reads": [
    {
      "captured_at": "2026-09-04T10:15:30.123Z",
      "stream_pts": 3214.2,
      "frame_index": 16071,
      "plate_raw": "GJ 01 AB 1234",
      "plate_norm": "GJ01AB1234",
      "is_valid_format": true,
      "confidence": 0.93,
      "bbox": [412, 388, 236, 64],
      "crop_file": "crop_0",
      "sighting_key": "12:GJ01AB1234:1725444928000"
    }
  ],
  "sightings": [
    {
      "key": "12:GJ01AB1234:1725444928000",
      "plate_norm": "GJ01AB1234",
      "is_valid_format": true,
      "first_seen": "2026-09-04T10:15:28.000Z",
      "last_seen": "2026-09-04T10:15:33.400Z",
      "read_count": 3,
      "best_conf": 0.95,
      "best_read_captured_at": "2026-09-04T10:15:30.123Z",
      "best_crop_file": "crop_0",
      "frame_file": "frame_0",
      "closed": false
    }
  ],
  "object_counts": [
    {"minute": "2026-09-04T10:14:00.000Z", "class": "car", "count": 7},
    {"minute": "2026-09-04T10:14:00.000Z", "class": "person", "count": 3}
  ],
  "live_counts": {"minute": "2026-09-04T10:15:00.000Z", "counts": {"car": 2, "person": 1}},
  "events": [
    {"type": "loop_reset", "occurred_at": "2026-09-04T10:15:00.000Z", "note": "pts 3600.0 -> 0.2 (discontinuity)", "stream_pts_before": 3600.0, "stream_pts_after": 0.2},
    {"type": "intrusion", "occurred_at": "2026-09-04T10:15:12.000Z", "note": "person in zone 'Gate apron' for 2.4 s", "zone_id": 1, "class": "person", "dwell_s": 2.4, "frame_file": "event_frame_0"}
  ]
}
```

Rules:

- `camera_id` is required (`camera_external_id` is informational; if `camera_id` is unknown or retired the whole batch is rejected with `404`).
- `reads` contain **accepted (voted) reads** only — one per plate per vote window (≈ every 3 s while the plate is visible), never per frame. Reads with `confidence < ANPR_MIN_READ_CONF` are not sent. `plate_norm`/`is_valid_format` come from §3.4 (the API re-normalises and overrides if different, logging a warning — the two implementations must agree).
- `sighting_key` = `f"{camera_id}:{plate_norm}:{first_seen_epoch_ms}"` (≤ 80 chars). The API upserts `sightings` by `worker_key` (insert on first sight; update `last_seen, read_count, best_conf, closed, best_crop_path/frame_path` when newer), **before** inserting reads, then links reads by key. Sending the same sighting state twice is harmless (idempotent).
- Every read must reference a crop file (JPEG, ≤ 200 KB, longest side ≤ 320 px). Frames: JPEG ≤ 400 KB, full decoded frame (960 px wide). The API writes files (§10), computes SHA-256, stores paths.
- `object_counts` are **final** per-minute counts (sent once when the minute rolls over); the API upserts `object_counts` (replace). `live_counts` is the running current minute, broadcast only (never stored).
- `events[].type` ∈ `loop_reset`, `intrusion`. Intrusion events create an `alerts(type='intrusion')` (suppressed 120 s per zone) with the frame as snapshot.
- Max request size 50 MB (`413` beyond). The API answers within 2 s for a normal batch; the worker uses a 15 s timeout.

Response `200`:

```json
{"accepted_reads": 1, "accepted_sightings": 1, "alerts_created": 1, "alerts_updated": 0, "object_counts_upserted": 2, "events_created": 2, "rejected": [{"index": 3, "kind": "read", "reason": "crop_file 'crop_3' missing"}], "server_time": "2026-09-04T10:15:34.210Z"}
```

`rejected` lists items skipped without failing the batch. Status codes: `200` (even with rejections), `400` bad multipart / invalid JSON, `401` bad key, `403` wrong scope, `404` unknown camera, `413` too large, `422` schema error (whole batch), `500/503` (worker retries).

### 7.3 `POST /internal/snapshots` (multipart/form-data)

Fields `camera_id` (int), `captured_at` (ISO), `file` (JPEG ≤ 150 KB, width 480 px). The API writes `snapshots/cam_<id>.jpg` atomically (`.tmp` then rename), keeps the mtime = now, and broadcasts `snapshot` on `/ws/reads/{camera_id}`. Response `204`. Sent every `SNAPSHOT_INTERVAL_S` per live camera; not in preindex mode.

### 7.4 `POST /internal/object-counts` (JSON)

`{"camera_id": 12, "object_counts": [{"minute": "…", "class": "car", "count": 7}]}` → `{"object_counts_upserted": 1}`. Same handler as the `object_counts` part of §7.2; exists so counts can be flushed separately (e.g. on shutdown).

### 7.5 `POST /internal/heartbeat` (JSON)

```json
{"worker_id": "live-a1b2c3", "mode": "live", "version": "1.0.0-phase1", "gpu": false, "cpu_flag": true, "started_at": "…", "cameras": [{"id": 12, "state": "running", "fps_actual": 4.8, "frames": 12345, "reads": 321, "last_frame_at": "…", "decoder_restarts": 1, "last_error": null}], "detector": "onnx", "object_detect": true}
```

`state` ∈ `starting`, `running`, `reconnecting`, `stopped`, `error`. Response `204`. Every `HEARTBEAT_S`. The API upserts `anpr_workers` and broadcasts `anpr_status` on `/ws/health`.

### 7.6 `POST /internal/events` (multipart)

Same event objects as `payload.events` for cases without a detection batch (`payload` JSON `{camera_id, events:[...]}` + optional frame files). Response `{"events_created": n}`.

### 7.7 Worker behaviour the API relies on

- **Decode** per camera: `ffmpeg -nostdin -loglevel error -rtsp_transport tcp -i <rtsp_url> [-hwaccel cuda when CPU=0] -vf fps=<fps>,scale=960:-1 -f rawvideo -pix_fmt bgr24 -` (`-skip_frame nokey` before `-i` in preindex). Frame timestamps: `captured_at` = UTC wall clock at frame receipt; `stream_pts` from `-vf showinfo` parsing on stderr or estimated as `frame_index / fps` if unavailable (documented in `decode.py`).
- **Loop reset**: `stream_pts < last_pts − 1.0` or a decoder restart → close open sightings (send `closed: true`), clear vote buffers, emit `loop_reset` event.
- **Detector selection** (`ANPR_DETECTOR`): `onnx` runs the YOLO-v9-t ONNX detector; `contour` finds bright quadrilaterals (adaptive threshold → contours → `0.15 ≤ h/w ≤ 0.6`, `w ≥ ANPR_MIN_PLATE_W`, fill ratio ≥ 0.6) — this is what reads the **synthetic** plates on the laptop and is a weak fallback on real feeds; `auto` runs ONNX and falls back to contour only for frames where ONNX returned no box. The active detector name is reported in the heartbeat.
- **OCR**: crop upscaled ×3, grayscale + CLAHE, PaddleOCR (det + rec) so two-line plates produce two text lines joined with `\n` in `plate_raw` before normalisation (`plate_raw` is stored with the newline replaced by a space).
- **Voting**: per `(camera, plate bucket)` where the bucket is the normalised string with confusions resolved; keep reads for `ANPR_VOTE_WINDOW_S`; emit the char-wise majority string with mean confidence once per window; `bbox` and crop of the highest-confidence member.
- **Sightings**: open on first accepted read, extend while reads arrive with the same `plate_norm`, close after `SIGHTING_CLOSE_S` of silence; `best_*` track the highest-confidence read. A new sighting of the same plate after closure gets a new key (that is how a loop shows up as a second, truthful sighting).
- **Object counting**: YOLOX-s on every `OBJECT_EVERY_N`th frame; centroid tracker with IoU ≥ 0.3 across consecutive detections; a track counts once per minute per class. Zones: a track centroid inside an active zone polygon (normalised coordinates × frame size) for ≥ `dwell_s` → one intrusion event per track per zone.
- **Snapshots**: every `SNAPSHOT_INTERVAL_S`, 480 px wide JPEG quality 70, live mode only.
- Frames are dropped, never queued, when inference lags (keep only the newest frame per camera).
- On SIGTERM: flush pending batches and counts, then exit 0.

---

## 8. MediaMTX contract

### 8.1 Path naming

| Path | Created by | Source | Purpose |
|---|---|---|---|
| `stream/<n>` (n = 1..8) | static in `deploy/mediamtx.yml` | `runOnInit` ffmpeg loop of `/media/synthetic/cam_<n>.mp4` (`runOnInitRestart: yes`) | Synthetic "sandbox" cameras for local testing; same URL shape as the organiser's `stream/<id>` |
| `own_gate` | static in `mediamtx.yml` | `runOnInit` ffmpeg loop of `/media/own/own_gate.mp4`, falling back to `/media/synthetic/cam_1.mp4` when the own file is absent (`sh -c` wrapper) | Our own "private society camera" feed (V3). Registered manually as `external_id=OWN-GATE-01`, `rtsp_url=rtsp://mediamtx:8554/own_gate`, `source=own`, `ownership=private` |
| `own_<slug>` | static (deploy adds more if needed) | any own file / phone RTSP | Additional own feeds |
| `fallback_<id>` | static, optional | `runOnInit` loop of `/media/fallback/<id>.mp4` | Recorded clips of chosen sandbox cameras when the sandbox is down (S4) |
| `cam_<id>` | **API**, via `POST /v3/config/paths/add/cam_<id>` after every insert/update | `cameras.rtsp_url`, on demand, TCP | Relay used by browsers (H.264 cameras) and by the ANPR worker (all cameras) |
| `cam_<id>_h264` | **API**, only when `codec == 'H265'` | `runOnDemand` ffmpeg transcode of `cam_<id>` | Browser path for H.265 cameras; also the recorded path for them |

`<id>` is always **our** `cameras.id`, never the catalogue id. The synthetic `stream/<n>` ids are catalogue ids and only appear inside `rtsp_url`.

### 8.2 `deploy/mediamtx.yml` (global settings that matter)

```yaml
logLevel: info
readTimeout: 20s
writeTimeout: 20s
writeQueueSize: 1024

api: yes
apiAddress: :9997
metrics: no
playback: yes
playbackAddress: :9996

rtsp: yes
rtspAddress: :8554
rtspTransports: [tcp]          # server side; clients must use TCP (the sandbox rule, and our workers do)
rtspEncryption: "no"

hls: yes
hlsAddress: :8888
hlsAlwaysRemux: yes
hlsVariant: mpegts
hlsSegmentCount: 7
hlsSegmentDuration: 1s
hlsAllowOrigin: '*'

webrtc: yes
webrtcAddress: :8889
webrtcAllowOrigin: '*'
webrtcLocalUDPAddress: :8189
webrtcLocalTCPAddress: :8189
webrtcAdditionalHosts: [127.0.0.1]   # overridden by MTX_WEBRTCADDITIONALHOSTS
webrtcICEServers2: []

recordPath: /recordings/%path/%Y-%m-%d_%H-%M-%S-%f
recordFormat: fmp4
recordPartDuration: 1s
recordSegmentDuration: 1m
recordDeleteAfter: 12h

authMethod: internal
authInternalUsers:
  - user: any
    pass:
    ips: []                      # empty = every IP; the ports are not exposed outside the compose network except via Caddy forward_auth
    permissions:
      - action: publish
      - action: read
      - action: playback
      - action: api

pathDefaults:
  sourceOnDemand: yes
  sourceOnDemandStartTimeout: 15s
  sourceOnDemandCloseAfter: 60s
  rtspTransport: tcp
  record: no

paths:
  stream/1:
    runOnInit: ffmpeg -re -stream_loop -1 -i /media/synthetic/cam_1.mp4 -c copy -f rtsp rtsp://localhost:8554/stream/1
    runOnInitRestart: yes
  # … stream/2 … stream/8 identical with the id substituted
  own_gate:
    runOnInit: sh -c 'f=/media/own/own_gate.mp4; [ -f "$f" ] || f=/media/synthetic/cam_1.mp4; exec ffmpeg -re -stream_loop -1 -i "$f" -c copy -f rtsp rtsp://localhost:8554/own_gate'
    runOnInitRestart: yes
```

Deploy verifies the exact key names against the pinned MediaMTX version's `mediamtx.yml` reference (`webrtcLocalUDPAddress`/`webrtcLocalTCPAddress` were named `webrtcICEUDPMuxAddress`/`webrtcICETCPMuxAddress` before v1.9; `rtspTransports` was `protocols`). If a key differs in the pinned version, deploy fixes the YAML **and** the JSON in §8.3, and notes it in the Amendments section.

### 8.3 Relay path creation (API → MediaMTX)

`POST {MEDIAMTX_API_URL}/v3/config/paths/add/cam_<id>` with JSON:

```json
{
  "source": "rtsp://mediamtx:8554/stream/3",
  "sourceOnDemand": true,
  "sourceOnDemandStartTimeout": "15s",
  "sourceOnDemandCloseAfter": "60s",
  "rtspTransport": "tcp",
  "record": true,
  "recordPath": "/recordings/%path/%Y-%m-%d_%H-%M-%S-%f",
  "recordFormat": "fmp4",
  "recordSegmentDuration": "1m",
  "recordDeleteAfter": "12h"
}
```

`record` is `cameras.record_enabled` (and `false` for H.265 cameras — their `_h264` path records instead). For `codec == 'H265'` a second call `POST /v3/config/paths/add/cam_<id>_h264`:

```json
{
  "runOnDemand": "ffmpeg -nostdin -loglevel error -rtsp_transport tcp -i rtsp://localhost:8554/cam_12 -c:v libx264 -preset veryfast -tune zerolatency -b:v 1500k -g 30 -an -f rtsp rtsp://localhost:8554/cam_12_h264",
  "runOnDemandRestart": true,
  "runOnDemandStartTimeout": "20s",
  "runOnDemandCloseAfter": "60s",
  "record": true,
  "recordPath": "/recordings/%path/%Y-%m-%d_%H-%M-%S-%f",
  "recordFormat": "fmp4",
  "recordSegmentDuration": "1m",
  "recordDeleteAfter": "12h"
}
```

With `MEDIAMTX_TRANSCODE=nvenc` the ffmpeg command becomes `ffmpeg -nostdin -loglevel error -rtsp_transport tcp -hwaccel cuda -i rtsp://localhost:8554/cam_12 -c:v h264_nvenc -preset p1 -b:v 1500k -g 30 -an -f rtsp rtsp://localhost:8554/cam_12_h264` (the MediaMTX container must then be the NVIDIA-enabled build; deploy's `gpu` profile sets the `mediamtx` image to one with NVENC ffmpeg or mounts the host ffmpeg — deploy decides and documents it).

Responses: `200` created; `400` with `"path already exists"` → the API issues `PATCH /v3/config/paths/patch/cam_<id>` with the same body (or `DELETE /v3/config/paths/delete/cam_<id>` then add, when `rtsp_url` changed); any other failure → the camera is saved, `relay_paths_failed += 1`, warning `"MediaMTX: <status> <body>"` in the import response, and the health poller retries creation on its next tick. Retire → `DELETE /v3/config/paths/delete/cam_<id>` (+ `_h264`); `404` is ignored.

MediaMTX `runOnDemand`/`runOnInit` commands run inside the MediaMTX container, so they use `rtsp://localhost:8554` (`MEDIAMTX_RTSP_LOCAL_URL`); paths handed to the ANPR worker use `rtsp://mediamtx:8554` (`MEDIAMTX_RTSP_URL`).

### 8.4 Reading state (health poller and `/streams`)

`GET /v3/paths/list?page=0&itemsPerPage=1000` →

```json
{"pageCount": 1, "itemCount": 60, "items": [{"name": "cam_12", "confName": "cam_12", "source": {"type": "rtspSource", "id": ""}, "ready": true, "readyTime": "2026-09-04T10:00:05Z", "tracks": ["H264"], "bytesReceived": 123456789, "bytesSent": 0, "readers": [{"type": "webRTCSession", "id": "…"}]}]}
```

`GET /v3/paths/get/cam_12` → one item (404 if the path is not instantiated). `GET /v3/config/paths/list` → configured paths (`{"items": [{"name": "cam_12", "source": "…", …}]}`), used to detect lost configuration after a MediaMTX restart (runtime-added paths are **not persisted** by MediaMTX; the API re-adds every non-retired camera's path on startup and whenever the poller finds it missing).

### 8.5 Browser URLs (through Caddy)

- WHEP: `POST /mtx/<play_path>/whep` with `Content-Type: application/sdp` (offer) → `201` with `Location: /mtx/<play_path>/whep/<session>` and the answer SDP; ICE trickle `PATCH` to the `Location`; teardown `DELETE`. The `StreamPlayer` (frontend) implements: create `RTCPeerConnection({iceServers: []})`, add `recvonly` video (and audio) transceivers, `setLocalDescription(offer)`, wait for ICE gathering (max 1 s) then POST; if `iceConnectionState` is not `connected`/`completed` within **5 s** → close and switch to HLS.
- HLS: `/mtx/<play_path>/index.m3u8` via hls.js (`lowLatencyMode: true`, `liveSyncDurationCount: 3`), native `<video>` on Safari.
- Playback: `GET /playback/list?path=cam_12&start=<RFC3339>&end=<RFC3339>` → `[{"start": "2026-09-04T09:00:00Z", "duration": 60.0, "url": "…"}]`; `GET /playback/get?path=cam_12&start=<RFC3339>&duration=30&format=mp4` → `video/mp4` playable in a native `<video>` element. The frontend uses the `url` values the **API** returns (§5.16), never MediaMTX's own `url` field (it carries the internal host).

### 8.6 Recording

Enabled per path as above (`record: true`) for `anpr_enabled` cameras (they are continuously read by the worker, so the on-demand source stays active and segments are produced) and for own feeds. Segments: fMP4, 1 min, deleted after 12 h. Disk budget: ≈ 0.9 GB/h per 1080p camera at 2 Mbps → 12 cameras × 12 h ≈ 130 GB; the laptop test uses 8 × 720p synthetic at ≈ 1 Mbps ≈ 45 GB max. `RETENTION` for recordings is MediaMTX's `recordDeleteAfter`, not the API's retention job.

---

## 9. WebSocket contract

Endpoints (no `/api` prefix; Caddy proxies `/ws/*` to the API):

| Endpoint | Who | Envelope types sent by server |
|---|---|---|
| `/ws/alerts?token=<jwt>` | any authenticated role (dept_admin scoped) | `hello`, `alert`, `alert_update`, `stats`, `pong` |
| `/ws/reads/{camera_id}?token=<jwt>` | any authenticated role with access to the camera (404 → close code 4404) | `hello`, `read`, `sighting`, `object_counts`, `snapshot`, `event`, `pong` |
| `/ws/health?token=<jwt>` | any authenticated role (scoped) | `hello`, `health`, `stats`, `anpr_status`, `pong` |

Auth: `?token=` (preferred; the SPA has the JWT in memory) or the `sg_session` cookie. Invalid/missing → the server accepts then closes with code `4401` and reason `unauthorized`; expired mid-session → close `4401` when the next message would be sent (the client reconnects after re-login). Client → server messages: `{"type":"ping"}` every 25 s (server answers `pong`); anything else is ignored. Server closes idle sockets after 90 s without a ping. The client reconnects with backoff 1→2→4→8→15 s (jittered) and re-fetches the list (`GET /alerts`) on reconnect to fill any gap.

Envelope (every message):

```json
{"type": "alert", "ts": "2026-09-04T10:15:29.400Z", "data": {…}}
```

`ts` is the server send time. `data` shapes:

| type | data |
|---|---|
| `hello` | `{"user": "jury_admin", "role": "admin", "server_time": "…", "version": "1.0.0-phase1", "scope": {"department_id": null, "district": null}}` |
| `alert` | the full `Alert` object (§5.12), plus `"sound": true` (false for `camera_offline`), `"notify_title": "CRITICAL · Stolen vehicle GJ 01 AB 1234"`, `"notify_body": "Civil Hospital Gandhinagar OPD Gate · Gandhinagar · 15:45:29 IST"` (server-rendered so every client shows the same text) |
| `alert_update` | `{"id": 77, "status": "acknowledged", "priority": "critical", "confidence_level": "exact", "read_count": 4, "last_read_at": "…", "acknowledged_by_username": "jury_operator", "acknowledged_at": "…", "closed_by_username": null, "closed_at": null, "outcome": null, "note": "Unit dispatched", "escalated": false, "updated_at": "…"}` — only the changed lifecycle fields; clients merge by `id` |
| `stats` (alerts socket) | `{"alerts_new": 3, "alerts_acknowledged": 1, "critical_open": 1, "reads_last_min": 41, "cameras_online": 8}` |
| `read` | the detection item of §5.9 (`id, camera{…}, sighting_id, captured_at, plate_raw, plate_norm, plate_display, is_valid_format, confidence, bbox, crop_url, crop_sha256, mode, watchlist_hit, alert_id`) |
| `sighting` | the `Sighting` object (§5.9) — sent when a sighting opens, when its best read changes and when it closes |
| `object_counts` | `{"camera_id": 12, "minute": "2026-09-04T10:15:00.000Z", "counts": {"car": 2, "person": 1, "motorcycle": 0, "bus": 0, "truck": 0, "bicycle": 0}, "final": false}` — `final=false` is the running minute (from `live_counts`), `final=true` the stored one |
| `snapshot` | `{"camera_id": 12, "url": "/media/snapshots/cam_12.jpg?t=1725444929", "updated_at": "…"}` (`?t=` cache-buster = epoch seconds) |
| `event` | the event item of §5.13 (auto events for that camera: `loop_reset`, `intrusion`, `watchlist_hit`, `camera_offline/online`) |
| `health` | `{"camera_id": 12, "name": "…", "status": "offline", "previous_status": "online", "last_seen_at": "…", "checked_at": "…", "is_ready": false, "has_video": false, "bytes_delta": 0, "readers": 0, "source_flag": "mediamtx", "maintenance_status": "ok", "district": "Gandhinagar", "department_code": "HEALTH"}` — sent on every status change only |
| `stats` (health socket) | `{"cameras": {"total": 51, "online": 8, "degraded": 1, "offline": 41, "unknown": 1}, "uptime_24h_pct": 17.3, "anpr_live_cameras": 8, "reads_last_min": 41, "disk_free_bytes": 98765432100}` every `WS_STATS_INTERVAL_S` |
| `anpr_status` | `{"worker_id": "live-a1b2c3", "mode": "live", "gpu": false, "cameras": [{"id": 12, "state": "running", "fps_actual": 4.8}], "last_heartbeat_at": "…", "stale": false}` on every heartbeat |
| `pong` | `{}` |

Fan-out is in-process (single uvicorn worker). Scoping: a `dept_admin` connection receives only messages whose camera is in their scope; `alerts` `stats` are computed for the scope. Slow clients: the server keeps a per-connection queue of 200 messages and drops the oldest with a warning.

---

## 10. File storage (`/data`) and `/media`

### 10.1 Layout (`DATA_DIR=/data`, named volume `sentinel_data`)

```
/data/
  crops/<camera_id>/<YYYY-MM-DD>/<uuid4 hex>.jpg          # one per accepted read (plate_reads.crop_path)
  frames/<camera_id>/<YYYY-MM-DD>/<camera_id>-<plate_norm>-<first_seen_epoch_ms>.jpg   # best full frame per sighting (sightings.frame_path); intrusion frames as <camera_id>-intrusion-<epoch_ms>.jpg (events.frame_path)
  snapshots/cam_<camera_id>.jpg                            # latest 480 px snapshot, overwritten atomically every second
  clips/<camera_id>/<clip_id>.mp4                          # evidence clips (clips.path)
  reports/<YYYY-MM-DD>/<type>_<YYYYMMDD_HHMMSS>IST_<username>.<csv|pdf>   # generated reports (report_files.path)
  exports/<YYYY-MM-DD>/cameras_export_<YYYYMMDD_HHMMSS>IST_<username>.csv
  exports/<YYYY-MM-DD>/import_errors_<job_id>.csv
  watchlist/<watchlist_id>.jpg                             # person photos (P2)
  tmp/                                                     # in-progress writes; cleaned at startup
```

Dates in directory names are **UTC** dates of the write (keeps the retention job trivial); the IST timestamp appears only in report/export file names for the jury. Paths stored in the DB are relative to `/data` and use forward slashes.

Write protocol (API): write to `tmp/<uuid>` → `fsync` → compute SHA-256 while writing → `os.replace` to the final path → insert the DB row with `*_sha256`. The hash is over the exact bytes on disk. `GET /evidence/verify` recomputes it.

`/media` (bind mount, read-only inside containers) holds inputs, not outputs:

```
media/
  synthetic/cam_1.mp4 … cam_8.mp4      # generated by scripts/make_synthetic_videos.py
  synthetic/plates.json                 # ground truth (§12.4)
  own/own_gate.mp4                      # team's phone video (not committed; .gitignore)
  fallback/<id>.mp4                     # optional recorded sandbox clips (not committed)
  README.md                             # says how to regenerate
```

`media/synthetic/*.mp4`, `media/own/*`, `media/fallback/*` are git-ignored; `media/synthetic/plates.json` **is** committed once generated so the watchlist seed and tests can reference it (regeneration with the same `SYNTH_SEED` reproduces it byte-for-byte except the `generated_at` field).

### 10.2 Serving: `GET /media/<relative path>`

Handled by the API (Caddy proxies `/media/*`). Auth: Bearer header, `sg_session` cookie or `?token=`. Rules:

- Path is normalised and must stay under `DATA_DIR` (`..`, absolute paths, symlinks outside → `404`).
- Allowed prefixes: `crops/`, `frames/`, `snapshots/`, `clips/`, `reports/`, `exports/`, `watchlist/`. Anything else → `404`.
- Scoping: for `dept_admin`, files under `crops/<camera_id>/`, `frames/<camera_id>/`, `snapshots/cam_<camera_id>.jpg`, `clips/<camera_id>/` are served only if the camera is in scope; `reports/` and `exports/` only if `report_files.created_by` is the user (admin: all). Viewer/operator: all camera files; reports only their own.
- Headers: `Content-Type` by extension, `Cache-Control: private, max-age=31536000, immutable` for crops/frames/clips (content-addressed, never rewritten), `Cache-Control: no-store` for snapshots, `X-Sentinel-Sha256` when the DB knows the hash, `Content-Disposition: attachment` for `reports/` and `exports/` (inline otherwise). Range requests supported (clips in `<video>`).
- A download of a file under `reports/` or `exports/` writes audit `report.download` (once per file per user per 10 min to avoid noise).

### 10.3 Retention job (daily at `RETENTION_JOB_HOUR_UTC`)

1. `plate_reads` older than `retention.days_reads` → delete rows and their crop files; alerts keep `plate_norm`/`snapshot_path` (the snapshot copy is under `crops/` too, so alerts older than the read retention lose their image — accepted; alerts themselves are never deleted).
2. `sightings.frame_path` older than `retention.days_frames` → delete the frame file, set `frame_path=null, frame_sha256=null` (sighting row stays).
3. `camera_health_log` older than 7 days; `object_counts` older than 90 days; `clips`, `report_files`, `exports` older than `retention.days_clips`.
4. Empty date directories removed. Counts logged; audit row `retention.purge` with `after = {reads, crops, frames, health_log, object_counts, clips, reports, bytes_freed}`.

Purpose-limitation notice (S6) shown in every export dialog: *"Exports contain personal data (vehicle registrations, images). Use is limited to the investigation or administrative purpose stated in your department's SOP. Every export is logged with your username and hash."*

---

## 11. Frontend

### 11.1 Stack and structure

React 18, Vite 5, TypeScript (strict), Ant Design 5 (`ConfigProvider` theme below), Tailwind (layout utilities only), TanStack Query 5, react-router 6, react-leaflet 4 + leaflet.markercluster, hls.js, `date-fns` + `date-fns-tz`, `@fontsource/inter`, `recharts` for the dashboard charts. Build: `npm ci && npm run build` (also `npm run typecheck` = `tsc --noEmit`, `npm run lint`). No runtime env: the SPA always talks to the same origin (`/api`, `/ws`, …).

```
frontend/src/
  main.tsx, App.tsx, router.tsx
  api/ (client.ts: fetch wrapper adding Bearer + 401→login; one file per resource returning typed functions; types.ts generated by hand from this contract)
  auth/ (AuthProvider, useAuth, RequirePermission)
  ws/ (useAlertsSocket, useReadsSocket, useHealthSocket with the envelope types of §9)
  components/ (AppLayout, StreamPlayer, MapView, StatusTag, PriorityTag, PlateText, IstTime, EmptyState, PageSkeleton, ExportDialog, ConfirmDialog, CameraDrawer, AlertPanel, NotificationBell)
  pages/ (one folder per route below)
  theme/ (tokens.ts, colours.ts)
  utils/ (time.ts, plate.ts, format.ts, geo.ts)
```

### 11.2 Routes

| Path | Page | Visible to | Notes |
|---|---|---|---|
| `/login` | Login | public | Username/password; product name + Gujarat Police context line; no self-registration |
| `/` | redirect → `/dashboard` | | |
| `/dashboard` | Dashboard | all | Tiles from `/dashboard/stats`, live via `/ws/health` stats; charts from `/dashboard/charts`; "Recent alerts" strip |
| `/cameras` | Registry | all (actions by permission) | Table, filters, search, Export button (`cameras.export`), Add camera (`cameras.write`), Import (`cameras.write`); row click → drawer (metadata tabs: Details, Health, Maintenance, Snapshot, Streams) with Edit/Retire |
| `/cameras/import` | Import | cameras.write | Tabs: **Sandbox catalogue** (host shown from settings, "Import from catalogue" button, result summary with onboarding time), **CSV** (template download, upload, dry-run toggle, result table, error CSV download), **API** (bulk endpoint docs snippet with curl, link to `/api/docs`) |
| `/cameras/:id` | Camera page | all | Left: `StreamPlayer` + metadata card; right: tabs **Live reads** (WS `read` list, last 50, crops), **Objects** (live class counts + last-hour sparkline), **Health** (uptime, log), **Recordings** (segment timeline, play), **Events** (list + "Tag event" for events.write), **Zones** (P2, zones.write) |
| `/map` | GIS map | all | Layer control: departments (one layer per department code), camera type, status, maintenance, district boundaries, POIs, coverage circles, zero-coverage cells (from gap analysis, off by default), coverage cones (P2). Clustering ≥ 50 markers; popup with name, dept, status, "View live", "Details" |
| `/wall` | Video wall | all | Grid 4 / 9 / 16; 4 and 9 = live WHEP tiles; 16 = 4 live + 12 snapshot tiles (`snapshot_url` refreshed every 1 s via WS `snapshot` or polling `?t=`); camera picker per slot; layout saved with `PUT /me/wall-layout`; "Two systems" badge shows sandbox vs own feeds |
| `/detections` | Detections | all | Table with crop thumbnails, filters (plate, camera, department, time, min conf, valid only), row → drawer with full frame, hash, "Verify hash", "Play recording", "Tag event", "Label (QA)" |
| `/vehicles` | Vehicle search | all | Plate input (auto-normalised preview), window picker (default 24 h), results: exact list + fuzzy candidates with crops and confirm/reject toggles (`route.confirm`), "Build route" button → `/vehicles/:plate/route` |
| `/vehicles/:plate/route` | Route | all | Map with numbered markers + polyline, timeline table (IST), crops strip, flags panel, "Play recording" per sighting, "Export PDF" (`reports.export`), "Lookup owner (VAHAN mock)" (`external.lookup`) |
| `/watchlist` | Watchlist | all (write by permission) | Table, add/edit modal, CSV import (template), deactivate, hit counts |
| `/alerts` | Alerts | all | Panel sorted by priority then time; filters status/type/priority; escalation badge; Ack/Close with note (`alerts.ack`); detail drawer with crop, camera, map mini, "Play recording", "Create clip"; `?id=` opens one |
| `/events` | Events | all | List with filters; "Add event" (`events.write`) |
| `/reports` | Reports | all (export by permission) | Cards: Output report (filters → CSV/PDF), Route report (plate + window → PDF), Gap analysis (CSV/PDF), Analytics quality (JSON view + PDF), History (files with hashes + Verify) |
| `/health` | Health | all | Status tiles, down > 5 min list, AMC expiring list, maintenance list, disk tile, ANPR workers, MediaMTX status; per-camera uptime table |
| `/gap-analysis` | Gap analysis | all | Parameter form, summary tiles, tabs per section, map of zero-coverage cells + uncovered POIs, ageing table, recommendations, Export |
| `/audit` | Audit log | admin | Filterable table with before/after JSON viewer; CSV export |
| `/settings` | Settings | admin | Tabs: **Catalogue** (host/auth/field map JSON editor + "Test connection"), **Retention & privacy**, **Alerts & routes** (thresholds), **Notifications** (Telegram), **Webhooks**, **API keys**, **Users** |
| `/settings/users`, `/settings/api-keys`, `/settings/webhooks`, `/settings/catalogue` | deep links to tabs | admin | |
| `/about` | About | all | Product, version, team, licences summary (from `docs/LICENCES.md` content copied into the page), link to `/api/docs`, browser notification test button |
| `*` | 404 | | EmptyState with "Back to dashboard" |

Navigation (sidebar order): Dashboard · Cameras · Map · Video wall · Detections · Vehicle search · Watchlist · Alerts · Events · Reports · Health · Gap analysis · Audit log (admin) · Settings (admin) · About. Items the role cannot use are hidden, not disabled. Header: product name, environment badge (`MOCK SANDBOX` when `MOCK_SANDBOX=1`, from `GET /settings/public` key `mock_sandbox`), alert bell with unread count, sound toggle, user menu (change password, logout), IST clock.

### 11.3 Design system

**Brand:** "Sentinel Gujarat". Logo: shield glyph (inline SVG in `components/Logo.tsx`) + wordmark; favicon = shield. Tagline on login: *Unified CCTV registry, viewing and analytics for Gujarat Police*.

**Ant Design 5 tokens (`ConfigProvider theme.token`):**

| Token | Value |
|---|---|
| `colorPrimary` | `#1E4DB7` |
| `colorSuccess` | `#16A34A` |
| `colorWarning` | `#D97706` |
| `colorError` | `#DC2626` |
| `colorInfo` | `#0EA5E9` |
| `borderRadius` | `8` |
| `fontFamily` | `"Inter", -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif` (via `@fontsource/inter`, weights 400/500/600/700) |
| `fontSize` | `14` |
| `colorBgLayout` | `#F5F7FA` |
| `colorBgContainer` | `#FFFFFF` |
| `colorText` | `#111827` |
| `colorTextSecondary` | `#4B5563` |
| `colorBorder` | `#E5E7EB` |

Component overrides: `Layout.siderBg = #0B1F3A`, `Menu.darkItemBg = #0B1F3A`, `Menu.darkItemSelectedBg = #1E4DB7`, `Menu.darkItemColor = rgba(255,255,255,0.78)`, `Table.headerBg = #F3F4F6`, `Card` with 1 px border `#E5E7EB` and no shadow, `Button` primary height 36.

**Layout:** dark navy sidebar `#0B1F3A` (240 px, collapsible to 64), light content area `#F5F7FA`, white cards, 16 px page padding, 12 px card gap, page title `h3` 20 px/600 with a one-line description under it. Tables: `size="middle"`, sticky header, page size 25, column widths fixed for status/priority/time columns. Max content width: none (dashboards are wide). Mobile (< 768 px): sidebar becomes a drawer; tables scroll horizontally; wall forces 4-grid.

**Status colours (single source: `theme/colours.ts`):**

| Domain | Value | Colour | Tag text |
|---|---|---|---|
| camera.status | online | `#16A34A` | Online |
| | degraded | `#D97706` | Degraded |
| | offline | `#DC2626` | Offline |
| | unknown | `#9CA3AF` | Unknown |
| | retired | `#6B7280` (strikethrough name) | Retired |
| alert.priority | critical | `#DC2626` | Critical |
| | high | `#EA580C` | High |
| | medium | `#D97706` | Medium |
| | low | `#0EA5E9` | Low |
| alert.status | new | `#DC2626` dot pulsing | New |
| | acknowledged | `#D97706` | Acknowledged |
| | closed | `#6B7280` | Closed |
| maintenance_status | ok | none | — |
| | under_maintenance | `#7C3AED` (wrench icon) | Under maintenance |
| | faulty | `#DC2626` (warning icon) | Faulty |
| | decommissioned | `#6B7280` | Decommissioned |
| confidence_level | exact | `#16A34A` | Exact match |
| | possible | `#D97706` | Possible match |
| amc_status | expired | `#DC2626` | AMC expired |
| | expiring | `#D97706` | AMC expiring |
| department layers | POLICE `#1E4DB7`, HEALTH `#16A34A`, GSRTC `#D97706`, PANCHAYAT `#7C3AED`, MUNICIPAL `#0EA5E9`, RTO `#DB2777`, FCS `#65A30D`, others cycle `#0891B2 #9333EA #CA8A04 #4F46E5 #059669 #B91C1C`, UNASSIGNED `#9CA3AF` | | |

Map markers: circle marker 10 px filled with the **status** colour, 2 px white stroke; ring colour = department colour when the department layer is active; maintenance ≠ ok → dashed ring in the maintenance colour; `anpr_enabled` → small "A" glyph; selected camera → 14 px with primary halo; alert flash → 3 pulses of the priority colour (CSS animation). Coverage circles: primary at 15 % opacity. Zero-coverage cells: `#DC2626` at 25 %. District outlines: `#0B1F3A` 1 px, fill 4 %.

**Time formatting (`utils/time.ts`):** `fmtIst(iso) → "04 Sep 2026, 15:45:30 IST"` using `formatInTimeZone(date, 'Asia/Kolkata', 'dd MMM yyyy, HH:mm:ss')` + `" IST"`; `fmtIstShort → "04 Sep, 15:45:30"` in dense tables (full value in tooltip); relative (`"2 min ago"`) allowed only in the alert panel and dashboard with the full IST value in the tooltip. Never render a raw ISO string. Date pickers operate in IST and convert to UTC ISO for the API (`zonedTimeToUtc`).

**Plates:** `PlateText` renders `format_plate()` output in a monospace tag (`font-family: "JetBrains Mono", ui-monospace, monospace` fallback stack; no extra font package), with the raw OCR string in a tooltip when it differs.

**Empty states:** every list/table/chart uses `EmptyState` (Ant `Empty` with a one-line explanation and, when the role has the permission, one primary action): e.g. Cameras → "No cameras yet. Import from the sandbox catalogue or add one manually." with buttons; Alerts → "No open alerts. New watchlist hits appear here in real time."; Detections → "No plate reads in this window. ANPR workers post reads within seconds of a plate being visible."; Route → "No sightings for this plate in the selected window; widen the window or check fuzzy candidates." Never show a bare "No data".

**Loading states:** page-level `PageSkeleton` (title + 3 card skeletons) on first load; tables use `loading` prop; players show a spinner over the last snapshot with the current mode label ("Connecting WebRTC…", "Falling back to HLS…", "Snapshot mode — stream unavailable"); buttons show `loading` while a mutation is in flight; never a blank screen and never a full-page spinner after first paint. Errors: inline `Alert` (type error) with the `detail` from the API and a Retry button; `422` field errors map onto form fields.

**Notifications (alerts):**

- Toast: Ant `notification.open` top-right, icon and border in the priority colour, title = `notify_title`, body = `notify_body` + crop thumbnail (48 px), actions "Open" and "Acknowledge" (if permitted). Duration 8 s; `critical` persists until dismissed. Max 3 stacked; older ones collapse into the bell count.
- Sound: `public/sounds/alert.mp3` (≤ 1 s, CC0, listed in `LICENCES.md`) played once per `alert` message with `sound=true`; a second sound `alert-critical.mp3` for critical. Toggle in the header persisted in `localStorage` key `sg.sound` (default on). The first play after login is unlocked by the login click (browsers require a user gesture).
- Browser `Notification` API: after login, if `Notification.permission === 'default'` show a one-time in-app banner "Enable desktop notifications for alerts" → `Notification.requestPermission()`. On every `alert` (and `health` offline for admins) when the tab is hidden or unfocused: `new Notification(notify_title, {body: notify_body, icon: crop_url or logo, tag: 'alert-'+id, renotify: false})`; `onclick` → `window.focus()` + navigate to `/alerts?id=<id>` (or `/cameras/<id>` for health). Preference key `localStorage['sg.desktop_notify']`.
- Bell badge = count of `status=new` alerts in scope (from `stats`).
- Map flash and a red dot on the sidebar "Alerts" item when a new alert arrives while not on `/alerts`.

**Accessibility and consistency:** all colours also carry text/icons (never colour alone); focus rings kept; tables have row keys; every destructive action goes through `ConfirmDialog`; every export goes through `ExportDialog` (shows the purpose-limitation notice, filters summary, format choice, and after success the file name + SHA-256 with a copy button).

**StreamPlayer contract:** props `{cameraId, playPath?, autoplay=true, muted=true, mode?: 'auto'|'whep'|'hls'|'snapshot', onModeChange?}`. Sequence: `GET /streams/{id}` → WHEP (5 s ICE timeout) → hls.js (10 s manifest timeout) → snapshot mode (refresh every 1 s). Manual mode toggle in the tile corner (WebRTC / HLS / Snapshot) and a "codec · mode · latency" caption. Tears down peer connections on unmount. On WS `health` offline for the camera the player shows a banner but keeps retrying every 15 s.

---

## 12. Seed data and the synthetic video generator

All seed files live in `backend/seeds/` and are loaded by `python -m app.seed` (also on API start when `SEED_ON_START=1`). Loading is idempotent (upsert by natural key), UTF-8, comma-separated, header row required. Empty cell = null.

### 12.1 `cameras_sample.csv` (also the CSV template header)

Header (exact order; the template download uses the same header and the importer accepts any column order and ignores unknown columns with a warning):

```
external_id,name,department_code,type,ownership,lat,lon,address,district,police_station,ward,rtsp_url,codec,resolution,fps,storage_location,retention_days,install_date,vendor,model,heading_deg,fov_deg,connectivity_type,bandwidth_kbps,vms_platform,nvr_id,maintenance_status,amc_vendor,amc_expiry,anpr_enabled,record_enabled
```

Exact content of `cameras_sample.csv` (10 data rows; **row 4 and row 9 are deliberately invalid**; row 7 carries an unknown department to exercise the warning path). This file is **not** loaded by the seed; it is the jury's CSV-import demo file and is used by `tests/test_csv_import.py`:

```
external_id,name,department_code,type,ownership,lat,lon,address,district,police_station,ward,rtsp_url,codec,resolution,fps,storage_location,retention_days,install_date,vendor,model,heading_deg,fov_deg,connectivity_type,bandwidth_kbps,vms_platform,nvr_id,maintenance_status,amc_vendor,amc_expiry,anpr_enabled,record_enabled
CSV-001,Bhilad Checkpost North Lane,POLICE,analog,govt_dept,20.2650,72.9120,"NH-48, Bhilad",Valsad,Bhilad,,,H264,704x576,12,DVR at checkpost,15,2017-05-10,CP Plus,CP-USC-TA24L2,20,70,4g,2000,none,DVR-VLS-07,ok,Secure Vision AMC,2026-09-25,false,false
CSV-002,Dahod PDS Godown Gate,FCS,bullet,govt_dept,22.8400,74.2600,"FCS godown, Station Road",Dahod,Dahod Town,,,H264,1280x720,15,NVR at godown,30,2021-11-02,Hikvision,DS-2CD2043G2,90,90,lan,4096,Hikvision NVR,NVR-DHD-02,ok,,,false,false
CSV-003,RTO Ahmedabad Testing Track,RTO,ptz,govt_dept,23.0810,72.6580,"RTO Subhash Bridge",Ahmedabad,Sabarmati,,,H265,1920x1080,25,VMS on-prem,30,2023-01-15,Dahua,SD49225,,,fibre,8192,Milestone XProtect,,ok,Dahua AMC,2027-01-14,false,false
CSV-004,Jamnagar Rozi Port Road,POLICE,ip,govt_dept,95.0,70.0400,"Rozi port road",Jamnagar,Bedi,,,H264,1280x720,15,NVR at PS,15,2020-08-20,CP Plus,CP-UNC-TA21L3,,,4g,2000,none,NVR-JAM-11,ok,,,false,false
CSV-005,Prabhas Patan Market Square,MUNICIPAL,dome,public_facing,20.8890,70.4050,"Prabhas Patan market",Gir Somnath,Somnath,Ward 3,,H264,1280x720,15,NVR at nagarpalika,7,2022-02-14,Hikvision,DS-2CD2143G2,,,4g,2000,none,NVR-GSM-01,ok,,,false,false
CSV-006,Okha Checkpost,POLICE,analog,govt_dept,22.4680,69.0700,"Okha road checkpost",Devbhumi Dwarka,Okha,,,H264,704x576,12,DVR at checkpost,15,2016-03-01,Zicom,ZC-AN-720,45,60,4g,1500,none,DVR-DWK-03,faulty,Coastal AMC Services,2025-12-31,false,false
CSV-007,Valsad Green Park Society Gate,HOUSING_SOCIETY,ip,private,20.6050,72.9280,"Green Park Society, Halar Road",Valsad,Valsad City,,,H264,1920x1080,25,Society NVR,7,2024-06-01,TP-Link,VIGI C340,180,90,wifi,4096,none,,ok,,,false,false
CSV-008,Zalod Taluka Panchayat Office,PANCHAYAT,bullet,govt_dept,23.1020,74.1700,"Taluka Panchayat office",Dahod,Zalod,,,H264,1280x720,15,NVR at office,15,2019-12-05,CP Plus,CP-UNC-TA21L3,,,4g,2000,none,NVR-ZLD-01,under_maintenance,,,false,false
CSV-002,,FCS,bullet,govt_dept,22.8410,74.2610,"FCS godown, Station Road",Dahod,Dahod Town,,,H264,1280x720,15,NVR at godown,30,2021-11-02,Hikvision,DS-2CD2043G2,90,90,lan,4096,Hikvision NVR,NVR-DHD-02,ok,,,false,false
CSV-010,Vapi RTO Checkpost Lane 2,RTO,anpr,govt_dept,20.3800,72.9200,"NH-48 Vapi RTO checkpost",Valsad,Vapi Town,,,H264,1920x1080,25,NVR at checkpost,30,2023-09-30,Dahua,ITC215-PW6M-IRLZF,10,40,lan,8192,none,NVR-VPI-04,ok,Dahua AMC,2026-09-30,false,false
```

Expected import result: `rows_total=10, added=8, updated=0`, `errors` = `[{row:4, field:"lat", …}, {row:9, field:"external_id", message:"duplicate external_id in file (first seen at row 2)"}, {row:9, field:"name", message:"field required"}]` (row 9 produces two entries), `warnings` = `[{row:7, field:"department_code", message:"unknown department 'HOUSING_SOCIETY' mapped to UNASSIGNED"}]`. Re-importing the same file gives `added=0, updated=8` with the same errors. Cameras without `rtsp_url` never get a relay path and stay `status='unknown'` (they appear in the gap report's metadata gaps as "missing stream URL").

### 12.2 `watchlist_seed.csv` (loaded by the seed) and the watchlist CSV template

Header: `plate,entity_type,name,reason,priority,source,notes,expires_at,is_active`

`plate` is normalised on load; `expires_at` ISO-8601 or empty; `is_active` defaults true. The first six rows are the synthetic anchor plates (§12.4) — do not change them without regenerating `plates.json` and the demo script:

```
plate,entity_type,name,reason,priority,source,notes,expires_at,is_active
GJ01AB1234,vehicle,White Maruti Swift – FIR 123/2026 Sector 7 PS,stolen,critical,own,Reported stolen 2026-08-30 from Sector 7 Gandhinagar,,true
GJ18CD5678,vehicle,Grey Hyundai Creta – wanted in Kalupur case,wanted,critical,egujcop,eGujCop reference EGC-2026-004411,,true
GJ05RS9012,vehicle,Blacklisted goods carrier (overloading),blacklisted,high,manual,RTO blacklist – repeat offender,,true
MH02BZ7788,vehicle,Silver Honda City – missing person's vehicle,missing,medium,own,Missing person report MPR-2026-0912,,true
GJ06KL4455,vehicle,Suspect vehicle – chain snatching pattern,suspect,low,manual,Seen near Infocity thrice,2026-12-31T18:30:00Z,true
GJ27XY3456,vehicle,Demo plate (added live during the jury demo),suspect,low,manual,Not active by default – the demo adds it,,false
GJ01CJ7788,vehicle,Black Toyota Fortuner – stolen,stolen,critical,own,,,true
GJ03BM2210,vehicle,Red Bajaj Pulsar – stolen two-wheeler,stolen,high,own,,,true
GJ05JE9834,vehicle,Wanted – Surat cheating case,wanted,critical,egujcop,,,true
GJ12AK1001,vehicle,Blacklisted taxi – permit cancelled,blacklisted,medium,vahan,,,true
GJ33AT5566,vehicle,Suspect – border movement Dahod,suspect,low,manual,,,true
GJ38CH0007,vehicle,Suspect – Valsad narcotics watch,suspect,medium,manual,,,true
DL3CAB9911,vehicle,Wanted – interstate,wanted,high,egujcop,,,true
RJ14CV3030,vehicle,Stolen in Rajasthan – NCRB advisory,stolen,high,egujcop,,,true
MP09HB6161,vehicle,Blacklisted – toll evasion,blacklisted,low,manual,,,true
GJ10AD7070,vehicle,Missing – elderly driver,missing,medium,own,,,true
GJ15CQ2424,vehicle,Suspect – Rajkot burglary pattern,suspect,low,manual,,2026-10-31T18:30:00Z,true
GJ21AR8181,vehicle,Arrested person's vehicle (impound pending),arrested,high,egujcop,,,true
KA01MJ4545,vehicle,Wanted – Bengaluru case (interstate lookout),wanted,high,egujcop,,,true
GJ09BW0110,vehicle,Expired entry (kept for history),blacklisted,low,manual,,2026-08-01T00:00:00Z,true
,person,Ramesh K. (wanted – Ahmedabad PS),wanted,critical,egujcop,FRS roadmap – no plate,,true
,person,Unidentified body case UDB-2026-17,unidentified_body,medium,manual,AFIS/NAFIS readiness placeholder,,true
,person,Missing minor – Surat,missing,high,own,,,true
```

### 12.3 Other seed files

**`departments.csv`** — header `code,name,aliases` (`aliases` is `|`-separated, lowercase). 27 rows: `UNASSIGNED,Unassigned,` first, then the 26 departments:

```
code,name,aliases
UNASSIGNED,Unassigned,unassigned|unknown|other
POLICE,Gujarat Police,police|gujarat police|home|home department
HEALTH,Health & Family Welfare,health|health & family welfare|health and family welfare|hospital|civil hospital
GSRTC,Gujarat State Road Transport Corporation,gsrtc|transport corporation|st depot|bus stand
PANCHAYAT,Panchayat & Rural Development,panchayat|gram panchayat|rural development|taluka panchayat
MUNICIPAL,Urban Development & Municipal Corporations,municipal corporation|municipal|amc|smc|vmc|rmc|urban development|smart city|nagarpalika
RTO,Transport (RTO),rto|transport|motor vehicles
FCS,Food & Civil Supplies,food & civil supplies|food and civil supplies|fcs|pds
EDU,Education,education|school|college
FOREST,Forests & Environment,forest|forests|environment|wildlife
RNB,Roads & Buildings,roads & buildings|r&b|pwd
GIDC,Industries (GIDC),gidc|industries|industrial estate
GMB,Ports (Gujarat Maritime Board),gmb|ports|maritime
WATER,Water Resources & Narmada,water|narmada|irrigation|water supply
ENERGY,Energy & Petrochemicals,energy|getco|guvnl|electricity
TOURISM,Tourism,tourism|tourist
REVENUE,Revenue,revenue|collectorate|mamlatdar
PRISONS,Prisons,prison|jail|prisons
FIRE,Fire & Emergency Services,fire|fire brigade|emergency services
GMRC,Metro Rail (GMRC),metro|gmrc
SPORTS,Sports Youth & Cultural Activities,sports|stadium|cultural
AGRI,Agriculture & Farmers Welfare,agriculture|apmc|mandi
WCD,Women & Child Development,women & child|wcd|anganwadi
SJE,Social Justice & Empowerment,social justice|sje|hostel
LABOUR,Labour & Employment,labour|employment|iti
DST,Science & Technology,science & technology|dst|data centre
FINANCE,Finance (Treasury),finance|treasury
```

**`users.csv`** — header `username,full_name,role,department_code,district,password_env`; four rows for §2.7 (`password_env` names the env variable; the seed reads the value at run time and re-hashes only when the value changed, tracked by storing `sha256(password)` in `settings` key `seed.pwhash.<username>`).

**`pois.csv`** — header `name,type,district,lat,lon`. ≥ 45 rows across the ten mock districts, ≥ 8 in Gandhinagar, of which at least 3 are farther than 300 m from every mock camera (so `uncovered_pois` is non-empty in the demo). Required rows (exact):

```
name,type,district,lat,lon
Sachivalaya Main Gate,govt_office,Gandhinagar,23.2240,72.6475
Sector 21 Government School,school,Gandhinagar,23.2301,72.6410
Pethapur Checkpost,checkpost,Gandhinagar,23.2740,72.6580
Civil Hospital Gandhinagar,hospital,Gandhinagar,23.2280,72.6490
Sector 11 Bus Stand,bus_stand,Gandhinagar,23.2302,72.6298
Koba Circle Highway Point,highway,Gandhinagar,23.1655,72.6548
Gandhinagar Railway Station,railway_station,Gandhinagar,23.2140,72.6300
Sargasan Cross Roads,highway,Gandhinagar,23.2010,72.6280
Kalupur Railway Station,railway_station,Ahmedabad,23.0270,72.6010
Civil Hospital Asarwa,hospital,Ahmedabad,23.0530,72.6065
Geeta Mandir Bus Station,bus_stand,Ahmedabad,23.0092,72.5962
Bhilad Border Checkpost,border,Valsad,20.2617,72.9150
Somnath Temple,temple,Gir Somnath,20.8880,70.4010
Dwarkadhish Temple,temple,Devbhumi Dwarka,22.2376,68.9670
Dahod Bus Stand,bus_stand,Dahod,22.8347,74.2555
```

**`gujarat_districts.geojson`** — `FeatureCollection`; each feature has `properties.district` (title case, one of the 33: Ahmedabad, Amreli, Anand, Aravalli, Banaskantha, Bharuch, Bhavnagar, Botad, Chhota Udaipur, Dahod, Dang, Devbhumi Dwarka, Gandhinagar, Gir Somnath, Jamnagar, Junagadh, Kheda, Kutch, Mahisagar, Mehsana, Morbi, Narmada, Navsari, Panchmahal, Patan, Porbandar, Rajkot, Sabarkantha, Surat, Surendranagar, Tapi, Vadodara, Valsad) and `Polygon`/`MultiPolygon` geometry in WGS-84. Source: datameet `india-districts` (CC-BY 4.0) or GADM 4.1 level-2 (free for non-commercial use; record whichever is used in `LICENCES.md`). If the full file cannot be obtained offline, the backend builder ships `gujarat_districts_fallback.json` with hand-drawn 8–12-vertex polygons for the ten mock districts and the seed loads the fallback only when the full file is absent (logging a warning). Names in this file are the canonical `cameras.district` spellings; the importer title-cases input and maps common variants (`Devbhoomi Dwarka → Devbhumi Dwarka`, `Somnath → Gir Somnath`, `Ahmadabad → Ahmedabad`, `Panch Mahals → Panchmahal`, `Kachchh → Kutch`, `Dangs / The Dangs → Dang`, `Chhotaudepur → Chhota Udaipur`).

**`mock_catalogue.json`** — the §6.2 table, served verbatim by `/mock-sandbox/api/ingest`.

**Own camera (demo, not seeded; added manually in Video 1):** `POST /cameras {"external_id": "OWN-GATE-01", "name": "Dynatech Office Gate (private society camera)", "department_code": "POLICE", "source": "own", "ownership": "private", "type": "ip", "lat": 23.0330, "lon": 72.5150, "district": "Ahmedabad", "police_station": "Satellite", "rtsp_url": "rtsp://mediamtx:8554/own_gate", "codec": "H264", "connectivity_type": "wifi", "anpr_enabled": true, "record_enabled": true}`.

### 12.4 `scripts/make_synthetic_videos.py`

Runs inside the `synth` container (Python 3.11, OpenCV headless, numpy, Pillow, ffmpeg). No arguments needed; env `SYNTH_CAMERAS`, `SYNTH_SECONDS`, `SYNTH_SEED`, `SYNTH_OUT` (defaults 8, 90, 42, `/media/synthetic`). Also accepts `--cameras`, `--seconds`, `--seed`, `--out` flags. Deterministic for a given seed. Runtime ≤ 3 min for 8 × 90 s on a laptop CPU.

Output:

- `cam_<n>.mp4` for n = 1..N: **1280×720, 10 fps, 90 s**, H.264 (`libx264 -preset veryfast -crf 23 -g 20 -pix_fmt yuv420p -movflags +faststart`, no audio) for all cameras except **n = 8**, which is encoded H.265 (`libx265 -preset fast -crf 26 -g 20 -tag:v hvc1`); if `libx265` is unavailable the script falls back to `libx264` and records `"codec": "H264"` for that camera in `plates.json` (the mock catalogue still says `H265`; the relay/transcode path is exercised either way).
- `plates.json` — ground truth:

```json
{
  "generated_at": "2026-09-04T10:00:00Z",
  "seed": 42,
  "width": 1280, "height": 720, "fps": 10, "loop_seconds": 90,
  "cameras": [{"id": 1, "file": "cam_1.mp4", "codec": "H264", "label": "Sachivalaya Gate 1"}],
  "plates": [{"plate": "GJ01AB1234", "display": "GJ 01 AB 1234", "two_line": false, "anchor": true, "cameras": [1, 3, 6, 2]}],
  "appearances": [{"camera_id": 1, "plate": "GJ01AB1234", "start_s": 5.0, "end_s": 9.0, "two_line": false, "direction": "ltr"}]
}
```

Content rules:

- **Anchor plates and schedule** (fixed regardless of seed; each appearance lasts 4 s starting at `start_s`):

| plate | style | appearances `(camera, start_s)` | why |
|---|---|---|---|
| `GJ01AB1234` | one line | (1, 5) (3, 25) (6, 50) (2, 72) | watchlist stolen/critical → alert + 4-camera route |
| `GJ18CD5678` | one line | (4, 10) (7, 35) (8, 60) | watchlist wanted/critical, includes the H.265 camera |
| `GJ05RS9012` | one line | (2, 15) (5, 40) (1, 65) | watchlist blacklisted/high |
| `GJ27XY3456` | one line | (6, 8) (3, 30) (7, 55) (4, 80) | **not** active in the watchlist — the demo adds it live and the alert must follow within one loop |
| `MH02BZ7788` | **two lines** (`MH 02` / `BZ 7788`) | (8, 20) (6, 45) | missing/medium; two-line OCR path |
| `22BH4321AA` | two lines (`22 BH` / `4321 AA`) | (5, 12) (2, 48) (8, 78) | BH-series normalisation |

- **Filler plates:** 18 random valid Gujarat plates (`GJ` + district `01..38` + 1–2 series letters + 4 digits; no `I`, `O`, `Q` in series letters; all distinct from the anchors and from every watchlist seed row) placed into free 6-second slots so that each camera has 8–12 appearances in 90 s and no two appearances overlap on the same camera. ~30 % of fillers are two-line. Seeded RNG (`random.Random(seed)`).
- **Frame content:** per-camera background (distinct gradient colour + a grey "road" band with dashed lane line + camera label top-left `CAM <n> · <label>` + a UTC timestamp `HH:MM:SS.f` top-right, updated per frame — makes snapshots visibly live). A dark "vehicle" rectangle (≈ 520×260 px) drives across the lower third from left to right (`direction: ltr`) or right to left, at a speed that keeps it on screen for the 4 s; the plate (white `#F5F5F5` rectangle with 3 px black border, ≈ 330×80 px one-line or 260×120 px two-line, black bold text drawn with DejaVu Sans Bold via Pillow at ≥ 52 px height, thin `IND` strip on the left) is attached to the vehicle front. Mild per-frame noise (`σ=3`) and a slight scale change (±5 %) so voting has something to do. No text other than the plate may look like a plate (labels use lowercase and a different colour).
- Timing origin: appearance `start_s` is relative to the file start; the loop restarts at 90 s (MediaMTX `-stream_loop -1`), so wall-clock offsets between cameras are whatever the loop phase is — the `plates.json` seconds are for tests, not for wall-clock assertions.
- Logs a summary table (camera → appearances) and exits non-zero if ffmpeg fails.

Verification the anpr builder must do on the laptop with `CPU=1`: run the worker against `stream/1` and confirm reads of `GJ01AB1234` at ≈ 5 s and `GJ05RS9012` at ≈ 65 s of each loop with `confidence ≥ 0.8` using `ANPR_DETECTOR=contour`, and that `ANPR_DETECTOR=auto` yields the same reads.

`scripts/survey_sandbox.py` (anpr builder; runs in the anpr image): `python scripts/survey_sandbox.py --api http://api:8000 --key <internal key> --out /data/survey` → one JPEG per camera (first decodable frame, 10 s timeout) plus `survey.csv` (`camera_id, external_id, name, ok, width, height, codec, seconds_to_first_frame, error`). Used on Day 1 hour one to choose the live cameras; also usable against the mock.

---

## 13. Test accounts, demo journey, acceptance checks

### 13.1 Accounts

See §2.7. On the hosted demo the four passwords are changed in `deploy/.env` before `deploy.sh` and written into the portal submission form and `README.md` says "credentials supplied in the submission form".

### 13.2 Demo journey (plan 3.6) mapped to this contract

1. `/login` as `jury_admin`.
2. `/cameras/import` → Sandbox tab shows `catalogue.base_url`; click **Import from catalogue** → summary "50 fetched · 50 added · 8 ANPR-enabled · onboarded in N s · first stream ready in M ms"; `/cameras` shows 50 rows; open one drawer; click **Export** → CSV download with SHA-256 shown. Show the **API** tab → `/api/docs`.
3. `/map`: toggle department and status layers, districts, coverage circles; click a Gandhinagar camera → **View live**.
4. `/wall`: 9-grid with cameras 1–8 (+ own gate) playing via WebRTC; switch one tile to HLS manually.
5. `/cameras/<id of mock 1>`: live reads listing plates with confidence and IST time; object counts ticking.
6. `/watchlist`: add `GJ 27 XY 3456` (reason suspect, priority high) → within ≤ 90 s (next loop pass) toast + sound + panel + map flash; browser notification if the tab is in the background; `/alerts` shows it; acknowledge with a note.
7. `/vehicles`: search `GJ 27 XY 3456` → exact hits on 4 cameras; confirm any fuzzy candidates → **Build route** → timeline + polyline across ≥ 3 cameras (speed flags shown as amber badges) → **Export PDF** opens.
8. `/reports`: output report for the last 2 h → CSV + PDF; show IST timestamps, camera IDs, SHA-256 column and the object-count summary.
9. `/alerts` → **Play recording** on the alert → 30 s clip from 10 s before the read; **Create clip** → hash shown; `/reports` History → **Verify**.
10. `/health` · `/gap-analysis` · `/dashboard` · `/audit` (shows login, import, export, search, route, report entries).
11. Video 1 variant: `/cameras` → **Add camera** with the own-gate RTSP URL (§12.3) → appears on map and wall within a minute → same alert/route/report flow on the own feed.

### 13.3 Acceptance checks (the integrator runs these, in order, on the laptop with `COMPOSE_PROFILES=cpu`)

`H=http://localhost`; `TOKEN` obtained in check 3; `jq` available (or PowerShell `ConvertFrom-Json`). Each check states the pass condition.

| # | Check | Command / action | Pass when |
|---|---|---|---|
| 1 | Synthetic media | `docker compose -f deploy/docker-compose.yml --project-directory . --profile tools run --rm synth` | `media/synthetic/cam_1.mp4 … cam_8.mp4` and `plates.json` exist; `plates.json` lists the six anchor plates with the §12.4 schedule |
| 2 | Stack up | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env --project-directory . up -d --build` | Within 10 min (first build) `docker compose ps` shows `postgres`, `mediamtx`, `api`, `web` healthy/running and `anpr-live`, `anpr-preindex` running; `curl -s $H/healthz` → `{"status":"ok",…,"db":"ok","mediamtx":"ok"}` |
| 3 | Login + RBAC basics | `curl -s -X POST $H/api/auth/login -H 'Content-Type: application/json' -d '{"username":"jury_admin","password":"Sentinel@Admin2026"}'` | `200` with `access_token`, `user.role == "admin"`, `Set-Cookie: sg_session`; wrong password → `401 {"code":"unauthorized"}`; 11th attempt in a minute → `429` |
| 4 | Mock catalogue | `curl -s $H/api/mock-sandbox/api/ingest \| jq length` | `50`; first item matches the §6.1 example |
| 5 | Sandbox import | `curl -s -X POST $H/api/cameras/import/sandbox -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"measure_first_stream":true}'` | `fetched=50, added=50, errors=[]`, `anpr_enabled=8`, `relay_paths_created=61` (50 `cam_*` + 11 `cam_*_h264` for the H.265 cameras 8, 10, 13, 16, 20, 24, 27, 32, 36, 42, 46), `first_stream_ready_ms` a number < 15000; second run → `added=0, updated=0, unchanged=50` |
| 6 | Registry + geo | `curl -s "$H/api/cameras?page_size=5" -H …` and `curl -s $H/api/geo/cameras -H …` | `total=50`; GeoJSON has 50 features; 8 with `properties.live == true`; departments mapped (`POLICE`, `HEALTH`, `GSRTC`, `PANCHAYAT`, `MUNICIPAL`; zero `UNASSIGNED`) |
| 7 | MediaMTX paths | `docker compose … exec api curl -s http://mediamtx:9997/v3/config/paths/list \| jq '.items \| length'` | ≥ 70 (8 `stream/*`, `own_gate`, 50 `cam_*`, 11 `cam_*_h264`) |
| 8 | Streams API | `curl -s $H/api/streams/<id of mock 8> -H …` | `codec=="H265"`, `play_path` ends with `_h264`, `whep_url=="/mtx/cam_<id>_h264/whep"`; for mock 1 `play_path=="cam_<id>"`; an audit row `stream.view` exists |
| 9 | Wall in browser | Open `$H/wall` (Chrome) as `jury_admin`, 4-grid with mock 1–4 | All four tiles play via WebRTC within 5 s (caption shows `WebRTC`); switching a tile to HLS plays within 10 s; 16-grid shows 12 snapshot tiles refreshing every second; mock 8 (H.265) plays through the `_h264` path |
| 10 | Health poller | Wait 3 min after import | `GET /api/health/summary` → `cameras.online ≥ 8` (the live mock cameras, read by the ANPR worker), `offline == 42` after ≤ 4 polls; `/ws/health` delivers `stats` every 10 s |
| 11 | Offline transition | `docker compose … exec api curl -s -X DELETE http://mediamtx:9997/v3/config/paths/delete/cam_<id of mock 3>` (simulates a lost camera) | Within 4 polls (≤ 4.5 min): camera status `offline`, a `camera_offline` alert with priority `low` in `GET /api/alerts`, a `health` WS message; the poller re-creates the path automatically (it is still configured on the camera), the camera returns `online` and the alert is auto-closed with note "Camera back online" |
| 12 | ANPR reads | Wait ≤ 3 min after import | `GET /api/detections?valid_only=true` `total > 0`; `GET /api/vehicles/search?q=GJ 01 AB 1234` → `exact` non-empty; after 10 min `cameras_seen ≥ 3` for `GJ01AB1234` (the CPU worker is capped at 3 cameras — set `ANPR_MAX_CAMERAS=8` in `.env` on a laptop with ≥ 8 cores to see all four); `GET /api/events?type=loop_reset` has rows after one loop (≈ 90 s) |
| 13 | Seeded watchlist alert | Nothing to do — `GJ01AB1234` is seeded as stolen/critical | Within one loop: `GET /api/alerts?status=new` contains an alert with `type=watchlist_hit`, `priority=critical`, `confidence_level=exact`, `latency_ms < 5000`, `crop_url` that returns a JPEG (`curl -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" $H<crop_url>` → 200; without a token → 401) |
| 14 | Live watchlist add → alert ≤ 5 s of next read | `POST /api/watchlist {"entity_type":"vehicle","plate":"GJ 27 XY 3456","reason":"suspect","priority":"high"}` while a `/ws/alerts` client is connected (`websocat "ws://localhost/ws/alerts?token=$TOKEN"`) | An `alert` envelope for `GJ27XY3456` arrives within 90 s (next appearance) and `latency_ms ≤ 5000`; a second appearance on the same camera within 60 s produces `alert_update` with `read_count=2`, not a new alert |
| 15 | Ack / close / RBAC | `POST /api/alerts/{id}/ack {"note":"Unit dispatched"}` as `jury_operator`; then as `jury_viewer` | Operator → `200`, `status=acknowledged`, WS `alert_update`; viewer → `403 forbidden`; `POST /api/watchlist` as viewer → `403`; audit has `alert.ack` with `actor=jury_operator` |
| 16 | Route | `GET "/api/vehicles/GJ01AB1234/route"` | `sightings.length ≥ 3` (≥ 4 with 8 workers) in `first_seen` order, `polyline.length == sightings.length`, `legs` present, every leg flagged `implausible_speed` (expected on the 90 s loop), `total_distance_km > 0` |
| 17 | Confirm + route PDF | `POST /api/vehicles/GJ01AB1234/confirm {"decisions":[{"sighting_id":<a fuzzy id or any exact id>,"decision":"confirmed"}]}`; `curl -o route.pdf …/route.pdf` | `{"saved":1}`; PDF opens, shows IST times, numbered map, crops, SHA-256 footer; `report_files` row exists; `X-Sentinel-Sha256` header equals `sha256sum route.pdf` |
| 18 | Output report | `curl -o det.csv "$H/api/reports/detections?from=<now-2h>&to=<now>&format=csv" -H …`; same with `format=pdf` | CSV data rows (excluding the `#` trailer) == `GET /api/detections?...&valid_only=false` `total` for the same filters; first column is IST `dd MMM yyyy, HH:mm:ss IST`; PDF opens with the quality section and object-count totals; audit `report.detections` × 2 |
| 19 | CSV import | `curl -F file=@backend/seeds/cameras_sample.csv $H/api/cameras/import/csv -H …` | `added=8`, errors reference rows 4 and 9 only (three entries), warning for row 7; `error_report_url` downloads a CSV with 3 lines + header; second run `updated=8` |
| 20 | Bulk API | `curl -X POST $H/api/v1/cameras/bulk -H "X-API-Key: sk_bulk000…" -H 'Content-Type: application/json' -d '{"cameras":[{"external_id":"GSRTC-101","name":"Mehsana Depot Gate","department_code":"GSRTC","lat":23.588,"lon":72.369,"district":"Mehsana"}]}'` | `added=1`; with the internal key → `403`; without key → `401`; `/api/docs` opens and lists the endpoint |
| 21 | Department scoping | Login as `dept_admin_police`; `GET /api/cameras` | Only `department_code=="POLICE"` rows (mock 1, 6, 8, 9, 14, 16, 17, 19, 25, 29, 31, 36, 37, 39, 42, 43, 47, 49 + CSV police rows); `GET /api/cameras/<id of mock 3>` → `404`; `/ws/alerts` shows only Police-camera alerts |
| 22 | Gap analysis | `GET /api/gap-analysis` and `…/export?format=csv`, `format=pdf` | `uncovered_pois ≥ 3` in Gandhinagar, `zero_coverage.features` non-empty, `ageing` includes `CSV-006` (analog, 2016, AMC expired, faulty), `department_gaps` non-empty, recommendations per district; both exports download with hashes |
| 23 | Recordings + clip + verify | After ≥ 2 min: `GET /api/recordings/<id of mock 1>`; `POST /api/clips {"camera_id":…, "start_at":<read.captured_at − 10 s>, "duration_s":30}`; `GET /api/evidence/verify?path=<clip.path>` | `segments ≥ 1`; clip `201` with `sha256` and `size_bytes > 0`; `<video>` at `/media/<clip.path>` plays in the browser; verify → `match=true`; corrupt the file inside the volume (`docker compose exec api sh -c 'echo x >> /data/<path>'`) → `match=false` |
| 24 | Settings + catalogue test | `PUT /api/settings {"values":{"catalogue.base_url":"http://api:8000/nowhere"}}`; `POST /api/settings/catalogue/test`; `POST /api/cameras/import/sandbox` | Test → `{"ok":false,"error":…}`; import → `502 upstream_error` naming the URL; restoring the URL makes the import work again; `GET /api/settings` masks `catalogue.auth_password` as `********` |
| 25 | Webhook | `POST /api/webhooks {"name":"test","url":"http://api:8000/api/mock-sandbox/webhook-sink","event_types":["alert.created"]}` (the mock exposes a sink that records the last 20 deliveries at `GET /api/mock-sandbox/webhook-sink`, admin only) | Next alert appears in the sink with a valid `X-Sentinel-Signature`; `last_status=200` |
| 26 | Retention (dry run) | `docker compose … exec api python -m app.jobs.retention --dry-run` | Prints counts per category and writes no audit row; `--run` deletes nothing on fresh data and writes `retention.purge` |
| 27 | Audit immutability | `docker compose … exec postgres psql -U sentinel -c "DELETE FROM audit_log WHERE id=1"` | Fails with `audit_log is append-only`; `GET /api/audit?action=auth.login` lists logins with IP and user agent; as `jury_operator` → `403` |
| 28 | Browser UX | Manual, Chrome + Edge, jury_viewer and jury_operator | Viewer sees no Add/Edit/Import/Ack/Export buttons; all timestamps read `dd MMM yyyy, HH:mm:ss IST`; empty states have a sentence; alert toast + sound + desktop notification (tab in background) fire on check 14; the `MOCK SANDBOX` badge shows; `/about` lists the version `1.0.0-phase1` and links to `/api/docs` |
| 29 | Tests | `docker compose … run --rm api pytest -q` and `cd frontend && npm ci && npm run typecheck && npm run build` | pytest passes: `test_normalise.py` (the 20 vectors), `test_route.py` (ordering, same-camera merge, speed flag), `test_rbac.py` (matrix + scoping), `test_csv_import.py` (sample file result), `test_matcher.py` (exact/possible/suppression/priority); frontend typecheck and build clean, `dist/` < 6 MB |
| 30 | GPU profile validates | `COMPOSE_PROFILES=gpu docker compose -f deploy/docker-compose.yml --project-directory . config` | Renders without error, `anpr-live-gpu` has the NVIDIA device reservation, `MEDIAMTX_TRANSCODE=nvenc`; not runnable on this laptop — deploy documents the VM procedure in `README.md` |

Timing figures to capture during check 5, 12, 13, 14 (they go into the scale plan): onboarding time, first-stream time, reads/s sustained by the API, read→alert latency (`latency_ms`), cameras per worker at the achieved fps (heartbeat `fps_actual`).

---

## Amendments

Builders append dated entries here when they must deviate from or extend this contract (one line each: date, builder, section, change, reason). Nothing else in this file is edited after the freeze.

| Date | Builder | Section | Change | Reason |
|---|---|---|---|---|
| | | | | |
| 2026-09-04 | deploy | §8.2 | `hlsAllowOrigin: '*'` / `webrtcAllowOrigin: '*'` are `hlsAllowOrigins: ['*']` / `webrtcAllowOrigins: ['*']` (lists) in the pinned MediaMTX v1.20.1 (digest `sha256:16c56911…a620c`); `apiAllowOrigins`/`playbackAllowOrigins` likewise. All other §8.2/§8.3 key names verified unchanged. | Key renamed upstream |
| 2026-09-04 | deploy | §8.2 | `pathDefaults.sourceOnDemand: yes` removed: v1.20.1 refuses to start ("'sourceOnDemand' is useless when source is 'publisher'"). The §8.3 JSON still sets `sourceOnDemand: true` per `cam_<id>` path, so behaviour is unchanged. `moq: no` added (new MoQ listener in 1.20). | Fatal config error |
| 2026-09-04 | deploy | §8.2 | `runOnInit` loops are wrapped in `sh -c 'until [ -f <file> ]; do sleep 10; done; exec ffmpeg …'` with literal file names (MediaMTX expands `$VAR` in commands itself, so shell variables cannot be used) and carry `-rtsp_transport tcp` on the publish side because `rtspTransports: [tcp]` disables UDP. `own_gate` falls back to `cam_1.mp4` with an `if/else`. | Publisher must use TCP; avoid crash-loop before `synth` runs |
| 2026-09-04 | deploy | §8.3 | Backend should add `-rtsp_transport tcp` before `-f rtsp` on the **output** of the `_h264` runOnDemand command (input already has it): `… -an -rtsp_transport tcp -f rtsp rtsp://localhost:8554/cam_<id>_h264`. Verified working with libx264 on v1.20.1. | Server offers TCP only |
| 2026-09-04 | deploy | §1.1 | `web` healthcheck is `wget -qO- http://127.0.0.1:8080/healthz` — a container-internal Caddy site on :8080 that proxies to `api:8000/healthz`; on the VM port 80 only redirects to HTTPS so the original `http://127.0.0.1/healthz` would fail. | Auto-HTTPS redirect |
| 2026-09-04 | deploy | §1.1 | MediaMTX diagnostic ports 8554/8888/8889/9996/9997 are published bound to `${MTX_BIND:-127.0.0.1}` (loopback only) in addition to 8189; `deploy/docker-compose.debug.yml` publishes them on all interfaces plus 5432. | Local verification / VLC without bypassing Caddy auth from outside |
| 2026-09-04 | deploy | §1.2 | Caddy rewrites the `Location` response header of MediaMTX (`header_down Location ^/(.*)$ /mtx/$1`) for both the WHEP POST answer (`/<path>/whep/<session>`) and the HLS `index.m3u8` 302 (`/<path>/index.m3u8?cookieCheck=1`, new in 1.20) so browsers stay under `/mtx/`. Verified end-to-end. | MediaMTX returns root-relative Locations |
| 2026-09-04 | deploy | §1.3 | Extra env: `MTX_BIND`, `PREINDEX_MAX_CAMERAS` (pre-index decoder cap, 3 cpu / 50 gpu), `CADDY_ACME_EMAIL_LINE` (compose-derived `email <CADDY_EMAIL>` or empty; Caddy rejects an empty `email` argument), resource caps `*_CPUS`/`*_MEMORY`. `WORKER_ID` env is passed to every ANPR service (`live-cpu`, `preindex-cpu`, `live-gpu`, `preindex-gpu`) for the heartbeat id. | Deploy needs |
| 2026-09-04 | deploy | §13.3 check 30 | The `gpu` profile alone renders `MEDIAMTX_TRANSCODE=cpu` (compose cannot vary a shared service by profile); use `-f deploy/docker-compose.gpu.yml` as well (forces `nvenc` and the NVENC MediaMTX image) or set `MEDIAMTX_TRANSCODE=nvenc` in `.env` (`deploy.sh --gpu` does both). | Compose limitation |
| 2026-09-04 | deploy | §1.1 | GPU MediaMTX = `deploy/mediamtx-nvidia.Dockerfile`: MediaMTX 1.20.1 binary on `jrottenberg/ffmpeg:6.1-nvidia2204` (NVENC), selected by `docker-compose.gpu.yml`; container gets `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility`. Not buildable/testable on the laptop. | Decision left to deploy in §8.3 |
| 2026-09-04 | deploy | §1.1 | `anpr-preindex` (CPU) carries profiles `["cpu", "preindex"]`: it still starts with the default `cpu` profile (check 2 unchanged) and can be started alone with `--profile preindex up -d anpr-preindex` for an overnight pre-index run on the laptop. | Task requirement; harmless alias |
| 2026-09-04 | deploy | §8.3 | Re-verified on v1.20.1: the `_h264` runOnDemand command **without** an output `-rtsp_transport tcp` also publishes correctly (ffmpeg gets `461 Unsupported Transport` for UDP and falls back to TCP in the same SETUP exchange). The earlier §8.3 amendment is a recommendation (saves one round-trip and a log line), not a blocker; the backend may keep the contract command verbatim. | Verified with ffprobe on `cam_999_h264` |
| 2026-09-05 | backend | §1.3 / §2.6 | The seeded defaults `INTERNAL_API_KEY` / `BULK_API_KEY` are 42 characters after `sk_`; the backend accepts `^sk_[0-9a-z]{40,48}$` (generated keys are exactly 40). | Defaults in §1.3 were written with 42 |
| 2026-09-05 | backend | §5.22 / §6 | The mock catalogue is served at `/api/mock-sandbox/api/ingest` (through Caddy) **and** at the API root `/mock-sandbox/api/ingest`, so the default `SANDBOX_BASE_URL=http://api:8000/mock-sandbox` resolves inside the compose network. | Internal base URL |
| 2026-09-05 | backend | §5.2 / §12.1 | CSV re-import reports every matched row as `updated` (unchanged rows included; `unchanged` is still returned) so a second run of `cameras_sample.csv` gives `updated=8`; the sandbox import keeps the added/updated/unchanged split of check 5. | §12.1 wording |
| 2026-09-05 | backend | §5.2 | New `GET /api/departments` → `{"items": [{id, code, name}]}` (permission `cameras.read`) so filter dropdowns and the camera/user forms list all 27 departments, not only those with cameras. | Frontend request |
| 2026-09-05 | backend | §5.20 / §5.12 / §5.4 | `GET /settings/public` also returns `version` and `product_name`; `Alert` items carry `plate_display` and `zone_id`; `Camera` items carry `play_path`. | Additive fields |
| 2026-09-05 | integrator | §5.2 | `first_stream_ready_ms` is measured from the moment the API requests the HLS manifest (which starts the on-demand pull) until `/v3/paths/get` reports `ready`; the request follows MediaMTX 1.20's `302 ?cookieCheck=1` redirect (without it the pull never started and the figure was always null). `duration_ms` includes that wait. | Measured null on 1.20.1 |
| 2026-09-05 | integrator | §5.16 | Clip / recording-play coverage checks accept a `start_at` up to 1 s before a segment's start: MediaMTX lists segment starts with microseconds, the API renders (and clients echo) milliseconds, so a clip requested at the exact segment start was rejected with 409. | Edge case found in check 23 |
| 2026-09-05 | integrator | §7.7 / §13.3 check 12 | On the local mock the loop is seamless (`ffmpeg -stream_loop -1 -c copy` keeps PTS monotonic), so no `loop_reset` events occur by themselves; the discontinuity path is verified by kicking the worker's RTSP reader session (`POST /v3/rtspsessions/kick/<id>`): decoder restart with backoff, sightings closed, one `loop_reset` event. Real sandbox loops that restart the stream produce the event naturally. | Verified on the laptop |
| 2026-09-05 | integrator | §1.3 | Laptop (12 cores, CPU=1): `ANPR_MAX_CAMERAS=8` with `ANPR_DETECTOR=contour` and `OBJECT_DETECT=0` runs all eight synthetic cameras at 5 fps decode (~2.7 cores, 2.6 GB); cameras that are `anpr_enabled` but beyond the live cap are **not** picked up by the pre-index worker (they are `both`), so the cap must cover every live camera. YOLOX finds no COCO objects in the synthetic drawings, so `object_counts` stay empty on the mock (counts need real footage). | Measured during integration |
| 2026-09-05 | integrator | §7.7 | `anpr/decode.py`: a clean ffmpeg EOF stops the decoder only for **file** sources; for RTSP sources it is treated as a disconnect (relay restart, publisher gone, sandbox loop restart) and the decoder reconnects with the 2→30 s backoff and emits the discontinuity (`loop_reset`). Before this fix a MediaMTX restart left every camera in state `stopped` until the worker was restarted. | Found when the relay was restarted during integration |
| 2026-09-05 | integrator | §12.4 | Synthetic loops are encoded without B-frames (`libx264 … -bf 0`, `libx265 … bframes=0`): MediaMTX cannot deliver B-frame H.264 over WebRTC and hls.js fails on the mpegts variant, so browser tiles stayed black; the H.265 camera worked because its `_h264` transcode uses `-tune zerolatency`. Regeneration takes 159 s on the laptop. | Wall verification in Chrome |
| 2026-09-05 | integrator | §5.6 | Health poller: the first observation after an API start (or a counter reset) uses `previous = 0`, so `bytes_delta = bytesReceived` and online cameras no longer flip to `degraded` for one tick after each restart. | Seen after every API restart |
| 2026-09-05 | integrator | §1.1 | `scripts/Dockerfile` has no `ENTRYPOINT`; compose passes `python scripts/make_synthetic_videos.py`. With the previous `ENTRYPOINT ["python"]` the compose `synth` service ran `python python …` and exited without output (README step 1 was broken). | Fresh-clone check |
| 2026-09-05 | integrator | §5.16 / §8.5 | Playback URLs carry millisecond RFC 3339 starts (`…T20:11:13.916Z`); a whole-second start preceded the segment by up to 999 ms and MediaMTX answered "no recording segments found" for clips/plays at a segment boundary. `POST /clips` and `/recordings/{id}/play` also clamp a boundary `start_at` to the covering segment's start. | Found in check 23 |
| 2026-09-05 | integrator | §10.2 | `GET /media/snapshots/cam_<id>.jpg` is served from memory (whole file, `Content-Length` from the bytes sent) instead of stat-then-stream, because the worker replaces the file every second and a rename between `stat` and the read produced `ERR_CONTENT_LENGTH_MISMATCH` on the 16-grid wall. Range requests remain for crops/frames/clips. | Wall verification |
| 2026-09-05 | integrator | §2.2 / §11 | SPA session restore (`AuthBootstrap`) discards the stored token only on a definitive `401`/`403` from `GET /auth/me`; a network error, an aborted request or a `5xx` keeps the token and retries with 1→8 s backoff (spinner shows "API not reachable, retrying"). Previously any failure logged the user out, which happened during a transient API stall. | Found during the browser pass |
| 2026-09-05 | integrator | §11.2 | Video wall tile footer: the camera `Select` shrinks (`min-width: 0`) so status/codec tags and the clear button never overflow the tile in the 3- and 4-column grids; the codec tag is omitted on the 16-grid. Detections confidence column no longer wraps `100 %`. | Screenshot review |
| 2026-09-05 | anpr | §7.7 | OCR drops PaddleOCR boxes taller than wide or narrower than 8 % of the crop (the vertical `IND` strip) and re-recognises a line whose detector boxes overlap; plate boxes touching the left/right frame edge are skipped. §12.4: measured generator runtime 224 s on the laptop (target 180 s). | Accuracy on synthetic plates |
| 2026-09-05 | frontend | §11.1 / §11.3 | `dayjs` (+utc/timezone) replaces `date-fns`/`date-fns-tz`; `zustand` holds auth/UI state; no Tailwind (plain CSS in `styles/global.css`); alert tones are `public/sounds/alert.wav` / `alert-critical.wav` generated by `frontend/e2e/make_sounds.mjs` (no third-party audio). `LICENCES.md` follows the real `package.json`. | Actual dependencies |
