# Sentinel Gujarat – backend (FastAPI)

Single-process API: REST + WebSocket fan-out + APScheduler (health poller, WS stats, nightly retention
purge, matcher refresh) + the in-process watchlist matcher. Contract: `docs/CONTRACT.md`.

## Layout

```
app/main.py            app factory, lifespan (extensions + create_all + audit trigger, seed, scheduler), routers
app/core/              config (pydantic-settings), security (JWT/bcrypt/API keys), rbac, audit middleware, tz, hashing
app/db/                models.py (every table of CONTRACT §4), session.py
app/schemas/           Pydantic request models
app/api/               one router per resource (auth, cameras, imports, …, internal, mock_sandbox, ws, media, healthz)
app/services/          importers, MediaMTX client, health poller, gap analysis, matcher, sightings ingestion,
                       plate search/route, reports (pandas CSV, reportlab PDF), retention, settings, notifications
app/adapters/          CameraSourceAdapter (rtsp, sandbox catalogue) and ExternalLookupAdapter (VAHAN/SARTHI mocks)
app/seed.py            python -m app.seed – idempotent seed (departments, users, API keys, POIs, districts, watchlist)
app/jobs/retention.py  python -m app.jobs.retention --dry-run | --run
seeds/                 departments.csv, users.csv, pois.csv, gujarat_districts.geojson, watchlist_seed.csv,
                       cameras_sample.csv (jury CSV demo, not seeded), mock_catalogue.json
tests/                 pytest, PostgreSQL-free (DB-marked tests are skipped unless DATABASE_URL is set)
```

## Build and test (no local Python needed)

```
docker build -f backend/Dockerfile -t sentinel-api .          # from the repository root
docker run --rm sentinel-api pytest -q
docker run --rm sentinel-api python -c "import app.main"
```

`backend/Dockerfile.dockerignore` is the BuildKit per-Dockerfile ignore file (context = repository root).

## Notes

* All timestamps are stored in UTC; CSV/PDF exports and notification text render IST (`Asia/Kolkata`).
* Every file written under `DATA_DIR` goes through `core/hashing.write_bytes_hashed` (tmp → fsync → sha256 → rename);
  reports and exports are recorded in `report_files` and served through `/media/*` with `X-Sentinel-Sha256`.
* `audit_log` is append-only through a `BEFORE UPDATE OR DELETE` trigger (CONTRACT decision 23). The application
  connects as the schema owner, so a separate restricted DB role is documented as a hardening step for the VM
  (`CREATE ROLE sentinel_app … ; REVOKE UPDATE, DELETE ON audit_log FROM sentinel_app`) rather than created here –
  Postgres would then report `permission denied` before the trigger's `audit_log is append-only` message that
  acceptance check 27 expects.
* API keys: generated keys are `sk_` + 40 chars; the seeded defaults from CONTRACT §1.3 are 42 chars long, so the
  validator accepts 40–48 characters.
* Camera status `not_streaming` (CONTRACT Amendments 2026-09-05): a camera that never delivered a stream (catalogue
  `live=false`, or a source that never answered) is `not_streaming`, never `offline`, and raises no alert/event; only an
  online/degraded → offline transition alerts. The pure state machine is `services/health_poller.decide_status`
  (`tests/test_health_state.py`). `uptime_24h_pct` counts monitored cameras only (`source_flag != catalogue`).
* Public-deployment hardening (`COOKIE_SECURE=1` / https `PUBLIC_BASE_URL`): default `JWT_SECRET`/`POSTGRES_PASSWORD` are
  fatal; default API keys / jury passwords log a `SECURITY WARNING`, set `default_secrets_in_use` on `/healthz` and keep the
  seeded bulk key inactive. Outbound URLs (catalogue, webhooks) go through `core/urlguard.py` (`ALLOW_PRIVATE_URLS=1`
  or `MOCK_SANDBOX=1` permits compose/private hosts). `?token=` is honoured on `/ws/*` and `/media/*` only.
* Tokens: PyJWT HS256 with `exp`/`iat`/`sub` required; `users.token_not_before` revokes tokens after a password
  reset/change, deactivation or role change. Login: per-IP window (all attempts) + per-username lock (5 failures / 15 min).
* Settings not in the CONTRACT freeze: `alerts.re_alert_minutes` (env `ALERT_RE_ALERT_MINUTES`, default 60, 0 = never
  re-alert while an alert is open).
* CSV exports/templates/error reports neutralise spreadsheet formulas (`csv_importer.neutralise_cell`).
