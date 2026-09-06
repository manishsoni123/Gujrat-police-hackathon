# Sentinel Gujarat — team setup guide

This is the short path from a fresh clone to a running stack on your own machine.
The jury-facing document is [`README.md`](README.md) (architecture, measured figures, hosted deployment);
this file only covers **getting it running and working on it**.

Everything runs in Docker. You do not need Python or Node on your machine unless you
develop the frontend outside the container.

---

## 1. What you are cloning

| Service (compose name) | What it is | Port on your machine |
|---|---|---|
| `web` | Caddy 2 serving the React SPA and proxying everything else | http://localhost (80), 443 |
| `api` | FastAPI backend: REST, WebSockets, health poller, alerts, reports | internal only (`/api/*` via Caddy) |
| `postgres` | PostGIS 16 | internal only |
| `mediamtx` | RTSP/WebRTC/HLS relay: every camera stream goes through it | 8189 (WebRTC), 8554/8888/8889 on 127.0.0.1 |
| `anpr-live`, `anpr-preindex` | Plate detection + OCR workers (CPU profile) | none |
| `synth` | one-shot generator of the 8 synthetic demo camera videos | none |

Source layout: `backend/` (API), `frontend/` (SPA), `anpr/` (workers), `deploy/` (compose, Caddyfile,
MediaMTX config, `.env.example`, VM scripts), `scripts/` (tools and checks), `docs/` (HLD, contract,
runbooks), `media/` (catalogue copy, enrichment CSV, generated videos).

## 2. Prerequisites

- **Git.** Keep the default install options. `.gitattributes` forces LF line endings for scripts
  and YAML, so the repo works on Windows without changing `core.autocrlf`.
- **Docker Desktop** (Windows/macOS) with the **WSL2 backend**, or Docker Engine + Compose v2 on Linux.
  In *Settings → Resources* give it at least **8 GB memory and 6 CPUs** (12 CPUs is what the reference
  laptop uses) and keep **40 GB of free disk** (images are about 5 GB, plus build cache and recordings).
- **Internet during the first build.** The build downloads base images, about 55 MB of ONNX model
  weights (hash-verified, see `anpr/weights/HASHES.txt`) and the PaddleOCR models.
- Ports **80, 443 and 8189** must be free on your machine. Stop IIS, Skype, or another compose stack if
  `up` fails with "port is already allocated".
- Optional: **Node 24** for frontend hot reload, **make** (`choco install make` on Windows) for the short
  commands in the `Makefile`. Every `make` target is just a wrapper around the compose commands below.

## 3. Clone

```bash
git clone https://github.com/manishsoni123/Gujrat-police-hackathon.git sentinel-gujarat
cd sentinel-gujarat
```

All commands below run from this directory, in PowerShell or Git Bash.

## 4. Configure

```bash
cp deploy/.env.example deploy/.env        # PowerShell: Copy-Item deploy/.env.example deploy/.env
```

`deploy/.env` works unchanged: CPU profile, plain HTTP on port 80, built-in mock catalogue with 50
cameras and 8 synthetic live streams, documented default passwords. **Never commit this file** (it is
git-ignored; it holds every secret).

Two optional edits before the first start:

- **On an 8-core or better laptop**, set the validated demo profile so all eight synthetic cameras get
  ANPR: `ANPR_MAX_CAMERAS=8`, `ANPR_DETECTOR=contour`, `OBJECT_DETECT=0`, `ANPR_LIVE_MEMORY=4g`.
- **To use the real organiser sandbox** (30 cameras on the organiser relay) you need the team's stream
  credentials. Ask Manish for `SANDBOX_STREAM_EMAIL` and `SANDBOX_STREAM_PASSWORD` and set
  `CATALOGUE_SOURCE=sentinel_portal`. The catalogue copy (`media/cameras.json`) and the team's location
  enrichment (`media/cameras_enrichment.csv`) are already in the repo. Then follow README §4.1. The
  real-feed ANPR tuning is documented next to `ANPR_MAX_CAMERAS` in `deploy/.env.example`.

## 5. First start

```bash
# 1. generate the synthetic demo videos (about 2-3 minutes, once)
docker compose -f deploy/docker-compose.yml --env-file deploy/.env --project-directory . --profile tools run --rm synth

# 2. build every image and start the stack; waits until every healthcheck passes (first build 5-10 minutes)
docker compose -f deploy/docker-compose.yml --env-file deploy/.env --project-directory . up -d --build --wait --wait-timeout 600

# 3. check
docker compose -f deploy/docker-compose.yml --env-file deploy/.env --project-directory . ps
curl http://localhost/healthz
```

The API creates the schema and seeds departments, users, API keys, POIs, districts and the watchlist on
its first start, so there is no separate migration or seed step.

With `make`: `make env synthetic up` does the same three steps; `make help` lists every target.

## 6. Log in

Open **http://localhost**.

| Username | Role | Password (from `deploy/.env`) |
|---|---|---|
| `jury_admin` | admin | `JURY_ADMIN_PASSWORD` |
| `jury_operator` | operator | `JURY_OPERATOR_PASSWORD` |
| `jury_viewer` | viewer | `JURY_VIEWER_PASSWORD` |
| `dept_admin_police` | department admin (Police only) | `DEPT_ADMIN_PASSWORD` |

The `MOCK SANDBOX` badge in the header is expected on a laptop; it means the catalogue is the built-in
mock. API docs are at http://localhost/api/docs.

Quick demo journey: **Cameras → Import → Import from catalogue** (50 mock cameras, 8 live) → **Map** →
**Video wall** → open a camera page for live reads → **Watchlist → add `GJ 27 XY 3456`** (alert within
one 90-second loop) → **Vehicles → search → route → PDF** → **Reports**.

## 7. Everyday commands

The long compose prefix is the same every time; define it once per shell.

```bash
# Git Bash
DC="docker compose -f deploy/docker-compose.yml --env-file deploy/.env --project-directory ."
# PowerShell
function dc { docker compose -f deploy/docker-compose.yml --env-file deploy/.env --project-directory . @args }
```

| Task | Command | `make` |
|---|---|---|
| Status | `$DC ps` | `make ps` |
| Follow logs | `$DC logs -f --tail 200 api` (any service name, or none for all) | `make logs S=api` |
| Restart one service | `$DC restart api` | |
| Rebuild after a code change | `$DC up -d --build api` (or `web`, `anpr-live anpr-preindex`) | `make up` |
| Stop (keeps data) | `$DC down` | `make down` |
| Wipe the database and re-seed | `bash deploy/reset-db.sh --yes` | `make reset-db` |
| Delete everything incl. volumes | `$DC down --volumes` | `make clean` |
| Backend tests | `$DC run --rm --no-deps api pytest -q` | `make test-backend` |
| Frontend typecheck + build | `cd frontend && npm ci && npm run typecheck && npm run build` | `make test-frontend` |
| Worker tests | `$DC run --rm --no-deps anpr-live python -m pytest -q anpr/tests` | |
| Shell in the API | `$DC exec api bash` | `make shell-api` |
| psql | `$DC exec postgres sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB'` | `make psql` |
| Scripted acceptance checks (105) | `node scripts/integration_checks.mjs` against a running stack | |

## 8. Developing

**Frontend with hot reload.** The Vite dev server proxies `/api`, `/ws`, `/mtx`, `/media` and
`/playback` to the running stack, so log in and everything works at http://localhost:5173.

```bash
cd frontend
npm ci
VITE_API_TARGET=http://localhost npm run dev      # PowerShell: $env:VITE_API_TARGET='http://localhost'; npm run dev
```

**Backend.** Edit `backend/app/…`, then `$DC up -d --build api`. Tests need no database
(DB-marked tests skip unless `DATABASE_URL` is set). Conventions are in `backend/README.md`.

**ANPR worker.** Edit `anpr/…`, then `$DC up -d --build anpr-live anpr-preindex`. Tuning knobs and
their meaning are in `anpr/README.md` and `deploy/.env.example`.

**Schema changes.** There is no migration tool: tables come from `create_all` plus a few targeted
start-up migrations. After pulling a change that alters a table, run `bash deploy/reset-db.sh --yes`.

**Screenshots and end-to-end.** `cd frontend && node e2e/final_journey.mjs` walks the whole jury
journey against the running stack and writes screenshots to `docs/screenshots/final/`.

## 9. What is deliberately not in git

| Path | Why | How to get it |
|---|---|---|
| `deploy/.env` | secrets | copy from `.env.example`; ask Manish for sandbox credentials |
| `media/synthetic/*.mp4` | 29 MB generated videos | step 1 of section 5 |
| `anpr/weights/*.onnx` | 55 MB model weights | downloaded and hash-checked during the image build |
| `media/anpr_survey/`, `anpr_crops/`, `anpr_evidence/`, `anpr_measure/`, `loopcheck/` | crops and frames from the real feed | shared out of band when needed |
| `docs/export/` | generated PDFs and the presentation | `make deliverables` (Node 24) |
| `docs/screenshots/**/*.png` | generated | the e2e scripts above |

## 10. Troubleshooting

- **`up` hangs or the API is unhealthy.** `$DC logs --tail 100 api`. On the very first start the API
  waits for PostGIS and seeds, which can take a minute.
- **"password authentication failed" after changing `POSTGRES_PASSWORD`.** The postgres image applies
  the password only when it creates the volume. Run `bash deploy/rotate-db-password.sh --generate`,
  or wipe with `bash deploy/reset-db.sh --yes`.
- **Weights download fails during the build.** Retry (`$DC build anpr-live`). Offline: get the `.onnx`
  files from a teammate into `anpr/weights/` and build with `--build-arg DOWNLOAD_WEIGHTS=0`.
- **`bash deploy/reset-db.sh` fails with a `\r: command not found` error.** Your checkout has CRLF line
  endings. Run `git config core.autocrlf false`, then `git rm --cached -r . && git reset --hard`.
- **Docker Desktop runs out of memory.** Raise it in *Settings → Resources*, or lower the per-service
  caps (`*_MEMORY`, `*_CPUS`) in `deploy/.env` and set `ANPR_MAX_CAMERAS=3`.
- **Video tiles stay black.** WebRTC needs port 8189 on `localhost`; if a VPN or firewall blocks it the
  tile falls back to HLS after a few seconds. `rtsp://127.0.0.1:8554/stream/1` in VLC (TCP) proves the
  relay itself.
- **Port 80 is taken.** Find the owner with `netstat -ano | findstr :80` (Windows) and stop it; the
  stack publishes 80 and 443 on purpose so the SPA, API, WebSockets and media share one origin.

## 11. Working in git

- Branch from `main`, open a pull request, keep commits small.
- Never commit `deploy/.env`, credentials, or screenshots that show them. `git status` should never
  list `deploy/.env`; if it does, stop and check `.gitignore`.
- Scripts and YAML must stay LF (`.gitattributes` handles it; do not override it in your editor).
- Large binaries (videos, weights, crops, PDFs) stay out of the repo; see section 9.
