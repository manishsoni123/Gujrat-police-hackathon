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
