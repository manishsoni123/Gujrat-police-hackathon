# Sentinel Gujarat — Registry API documentation (Model 1 deliverable)

| | |
|---|---|
| **Product** | Sentinel Gujarat `1.0.0-phase1` (git tag `v1.0-phase1`) · Dynatech Consultancy · Model 1 + Model 2 (hybrid roadmap to 3/4) |
| **Live reference** | Swagger UI at `https://<host>/api/docs`, ReDoc at `/api/redoc`, machine-readable schema at `/api/openapi.json` (OpenAPI 3.1) |
| **This file** | Human-readable rendering of the registry-facing part of the API, derived from `docs/CONTRACT.md` §2, §3, §5 and §6. Before the PDF export it is **regenerated from the running API** with `node docs/tools/render-registry-api.mjs https://<host>/api/openapi.json > docs/REGISTRY-API.generated.md` and the two are diffed; anything the generated file adds is merged here so the document never lags the code |
| **Audience** | Departmental IT teams integrating their camera inventories and NVR/VMS exports; SCRB administrators; the hackathon jury ("registry API documentation" expected deliverable) |

Conventions (contract global rules): every path below is relative to `/api`; JSON keys are `snake_case`; IDs are integers; timestamps in JSON are ISO-8601 UTC with a trailing `Z` (`2026-09-04T10:15:30.123Z`) and are rendered as IST (`dd MMM yyyy, HH:mm:ss IST`) in every UI, CSV and PDF; coordinates are WGS-84 decimal degrees, `lat` then `lon`; date-only fields are `YYYY-MM-DD`.

---

## 1. Authentication

| Mechanism | Used by | How |
|---|---|---|
| **JWT bearer** (HS256, 8 h) | Interactive users and scripts acting as a user | `POST /auth/login` → `access_token`; send `Authorization: Bearer <token>` |
| **Session cookie** `sg_session` | Browser media requests (`<img>`, `<video>`, HLS, WHEP) | Set automatically by `POST /auth/login` (HttpOnly, SameSite=Lax, Secure on HTTPS); cleared by `POST /auth/logout` |
| **API key** `X-API-Key: sk_<40 lowercase alphanumerics>` | Machine-to-machine | Scope `bulk` → only `POST /v1/cameras/bulk`; scope `internal` → only `/internal/*` (ANPR workers). Keys are created once by an admin (`POST /api-keys`), stored hashed, shown once |

### 1.1 `POST /auth/login`

Request `{"username": "jury_admin", "password": "…"}` → `200`:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs…",
  "token_type": "bearer",
  "expires_at": "2026-09-04T18:15:30.000Z",
  "user": {"id": 1, "username": "jury_admin", "full_name": "Jury Administrator", "role": "admin", "department_id": null, "department_name": null, "district": null}
}
```

`401 {"detail":"Invalid username or password","code":"unauthorized"}` on failure; `429 rate_limited` after 10 attempts per minute per IP. Usernames are case-insensitive.

### 1.2 Other auth endpoints

| Method & path | Purpose |
|---|---|
| `POST /auth/logout` | Clears the cookie; audited |
| `GET /auth/me` | The `user` object plus `"permissions": [...]` (see §1.3) — the UI hides actions from this list |
| `POST /auth/change-password` `{current_password, new_password}` | `204`; policy ≥ 10 chars with a letter and a digit |
| `GET /auth/verify` | `204` when a valid token/cookie is present, else `401`; used by the reverse proxy to guard `/mtx/*` and `/playback/*` |

### 1.3 Roles, permissions and data scoping

Roles: `admin`, `dept_admin`, `operator`, `viewer`. A `dept_admin` sees and edits only cameras of their own department (and district, when set); the restriction is applied **in SQL** to every camera-derived resource, and a scoped miss returns `404` so existence is not leaked. The watchlist is statewide.

| Permission | Registry endpoints | admin | dept_admin | operator | viewer |
|---|---|---|---|---|---|
| `cameras.read` | `GET /cameras*`, `GET /geo/*`, `GET /streams/*`, `GET /health/*`, `GET /gap-analysis` | ✓ | ✓ (scoped) | ✓ | ✓ |
| `cameras.write` | `POST/PUT/DELETE /cameras*`, `POST /cameras/import/*`, `PUT /cameras/{id}/maintenance` | ✓ | ✓ (scoped; cannot move a camera to another department) | – | – |
| `cameras.export` | `GET /cameras/export`, `GET /gap-analysis/export` | ✓ | ✓ (scoped) | ✓ | – |
| `admin.settings` | `GET/PUT /settings`, `POST /settings/catalogue/test`, `/webhooks*` | ✓ | – | – | – |
| `admin.apikeys` | `/api-keys*` | ✓ | – | – | – |
| `admin.users` | `/users*` | ✓ | – | – | – |
| `admin.audit` | `GET /audit*` | ✓ | – | – | – |
| API key scope `bulk` | `POST /v1/cameras/bulk` | — | — | — | — |

`POST /cameras/import/sandbox` additionally requires the `admin` role (a catalogue spans departments).

---

## 2. Shared shapes

### 2.1 Error body

Every non-2xx JSON response has the same shape; `errors[]` is present only for validation/import errors (`row` = 1-based data row for CSV, `index` = 0-based item for JSON bulk):

```json
{"detail": "Human-readable message", "code": "validation_error", "errors": [{"row": 4, "field": "lat", "message": "must be between -90 and 90"}]}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `bad_request` | Malformed multipart, unsupported file type, bad `format=` |
| 401 | `unauthorized` | Missing/expired token or API key |
| 403 | `forbidden` | Role/scope lacks the permission (`{"detail":"Insufficient role"}`) |
| 404 | `not_found` | Missing or out-of-scope entity |
| 409 | `conflict` | Duplicate `external_id` / username; illegal state transition |
| 413 | `too_large` | CSV > 200 MB |
| 422 | `validation_error` | Body/query validation (`field` is the dotted location) |
| 429 | `rate_limited` | Login |
| 502 | `upstream_error` | Catalogue host or MediaMTX unreachable / non-2xx (`detail` names the URL and status) |
| 503 | `unavailable` | Database down |

### 2.2 Pagination and sorting

List endpoints accept `page` (1-based, default 1), `page_size` (default 25, max 200), `sort` (endpoint whitelist; unknown → 422) and `order` (`asc`|`desc`). Response: `{"items": [...], "total": 1234, "page": 1, "page_size": 25}`. Text filter `q` is a case-insensitive contains-match over the endpoint's listed columns.

### 2.3 Enumerations used by the registry

| Field | Values |
|---|---|
| `source` | `sandbox`, `csv`, `api`, `manual`, `own` |
| `type` | `analog`, `ip`, `ptz`, `dome`, `bullet`, `anpr`, `other` |
| `ownership` | `govt_dept`, `private`, `public_facing` |
| `connectivity_type` | `lan`, `fibre`, `4g`, `5g`, `leased_line`, `wifi`, `other` |
| `codec` | `H264`, `H265`, `MJPEG`, `UNKNOWN` (input accepts `h264/avc/h.264`, `h265/hevc/h.265`, `mjpeg`) |
| `status` (read-only) | `unknown`, `online`, `degraded`, `offline`, `retired` |
| `maintenance_status` | `ok`, `under_maintenance`, `faulty`, `decommissioned` |
| `amc_status` (derived) | `expired`, `expiring` (≤ 30 days), `ok`, `none` |

### 2.4 `CameraImportRow` — the one input shape for CSV, bulk API, catalogue and the manual form

Every field is optional except `external_id` and `name`; unknown keys are ignored with a warning.

| Field | Type | Validation | Default |
|---|---|---|---|
| `external_id` | string ≤ 64 | required, trimmed; unique per `source` | |
| `name` | string ≤ 160 | required | |
| `department_code` | string | must match `departments.code` (case-insensitive; the list is served by `GET /departments`, §3.10) or a display-name alias (e.g. `Municipal Corporation` → `MUNICIPAL`); otherwise `UNASSIGNED` + **warning** (never a row error) | `UNASSIGNED` |
| `type`, `ownership`, `connectivity_type`, `maintenance_status` | enum | see §2.3 | `ip`, `govt_dept`, null, `ok` |
| `lat`, `lon` | number | both or neither; −90..90 / −180..180; warning when outside the Gujarat bounding box 20.1–24.8 N, 68.1–74.5 E | null |
| `address`, `district`, `police_station`, `ward` | string | district is title-cased and common variants are mapped (`Devbhoomi Dwarka` → `Devbhumi Dwarka`, `Kachchh` → `Kutch`, …) | null |
| `rtsp_url` | string ≤ 512 | must start with `rtsp://` or `rtsps://`; credentials allowed in the URL (masked in responses for non-admins) | null |
| `whep_url`, `hls_url` | string | `http(s)://`; stored for reference only — playback always goes through the platform relay | null |
| `codec` | string | normalised to §2.3 | `UNKNOWN` |
| `resolution` | string | `^\d{2,5}x\d{2,5}$` (also accepts `1920×1080`, `{"width":1920,"height":1080}`, `[1920,1080]` from catalogues) | null |
| `fps` | int | 1..60 | null |
| `live` | bool | catalogue live flag (`true/false`, `"live"/"offline"`, `1/0`) | null |
| `storage_location`, `vendor`, `model`, `vms_platform`, `nvr_id`, `amc_vendor` | string | | null |
| `retention_days` | int | 0..3650 | null |
| `install_date`, `amc_expiry` | date | `YYYY-MM-DD` | null |
| `heading_deg` | int | 0..359 | null |
| `fov_deg` | int | 1..360 | null |
| `bandwidth_kbps` | int | ≥ 0 | null |
| `anpr_enabled`, `record_enabled` | bool | `true/false/1/0/yes/no` | see import rules |

**Import rules (all four paths):** upsert on `(source, external_id)`; on update only the supplied fields change (CSV: a blank cell means "unchanged" on update and `null` on insert); a duplicate `external_id` inside one file/batch is an error on the second occurrence; after commit the API creates the relay path for every added/updated camera (`relay_paths_created` / `relay_paths_failed`) — a relay failure is a **warning**, the camera is still saved with `status='unknown'` and the health poller retries. New catalogue cameras get `anpr_enabled = live AND (enabled_count < ANPR_AUTO_ENABLE_MAX)` and `record_enabled = anpr_enabled`.

### 2.5 `Camera` (response object)

Every stored column (see the CSV template columns plus `id`, `source`, `relay_path`, `live`, `status`, `health_fail_count` omitted, `last_seen_at`, `last_status_change_at`, `created_by`, `created_via`, `created_at`, `updated_at`, `retired_at`) plus derived `department_code`, `department_name`, `uptime_24h_pct`, `age_years`, `amc_status`, `created_by_username`. Example (abridged):

```json
{
  "id": 12, "source": "sandbox", "external_id": "3", "name": "Civil Hospital Gandhinagar OPD Gate",
  "department_id": 2, "department_code": "HEALTH", "department_name": "Health & Family Welfare",
  "type": "bullet", "ownership": "govt_dept", "lat": 23.2275, "lon": 72.6485, "district": "Gandhinagar", "police_station": "Sector 7",
  "rtsp_url": "rtsp://mediamtx:8554/stream/3", "relay_path": "cam_12", "codec": "H264", "resolution": "1280x720", "fps": 10, "live": true,
  "anpr_enabled": true, "record_enabled": true, "status": "online", "last_seen_at": "2026-09-04T10:14:00.000Z",
  "maintenance_status": "ok", "amc_expiry": null, "amc_status": "none", "uptime_24h_pct": 99.2, "age_years": null,
  "created_via": "sandbox", "created_at": "2026-09-04T10:00:03.000Z", "updated_at": "2026-09-04T10:00:03.000Z", "retired_at": null
}
```

---

## 3. Camera registry endpoints

### 3.1 `GET /cameras` — list (permission `cameras.read`)

Filters: `q` (name, external_id, address, police_station), `department_id`, `district`, `police_station`, `type`, `status` (comma list), `source`, `ownership`, `maintenance_status`, `anpr_enabled`, `include_retired` (default false). Sort whitelist: `name` (default, asc), `external_id`, `status`, `district`, `department_name`, `last_seen_at`, `updated_at`, `created_at`, `install_date`, `amc_expiry`. Items are `Camera`.

```
GET /api/cameras?district=Gandhinagar&status=online,degraded&sort=last_seen_at&order=desc&page_size=50
```

### 3.2 `POST /cameras` — manual add (permission `cameras.write`)

Body: `CameraImportRow` + optional `source` (`manual` default; `own` allowed for the team's own feeds). `201 Camera`. Creates the relay path. Example — onboarding a private society camera:

```bash
curl -X POST https://<host>/api/cameras -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{
  "external_id": "OWN-GATE-01", "name": "Dynatech Office Gate (private society camera)", "department_code": "POLICE",
  "source": "own", "ownership": "private", "type": "ip", "lat": 23.0330, "lon": 72.5150, "district": "Ahmedabad",
  "police_station": "Satellite", "rtsp_url": "rtsp://mediamtx:8554/own_gate", "codec": "H264",
  "connectivity_type": "wifi", "anpr_enabled": true, "record_enabled": true}'
```

### 3.3 `GET /cameras/{id}` — detail

`Camera` + `recent_alerts`, `reads_24h`, `sightings_24h`, `snapshot_url`, `zones[]`.

### 3.4 `PUT /cameras/{id}` — partial update

Only supplied fields change. Changing `rtsp_url`, `codec`, `record_enabled` or `anpr_enabled` re-creates the relay path. Audited with a before/after diff.

### 3.5 `DELETE /cameras/{id}` — retire

Soft retire: `status='retired'`, `retired_at=now()`, relay path deleted, ANPR stops on the worker's next configuration reload. `204`.

### 3.6 `PUT /cameras/{id}/maintenance`

Body `{maintenance_status, last_maintenance_at?, maintenance_note?, amc_vendor?, amc_expiry?}` → `Camera`. Drives the "AMC expiring in 30 days" and maintenance lists on the health page and the ageing section of the gap report.

### 3.7 `GET /cameras/{id}/health?hours=24`

```json
{"camera_id": 12, "status": "online", "uptime_pct": 99.2, "last_seen_at": "…", "last_status_change_at": "…", "checks": 1440,
 "log": [{"checked_at": "…", "is_ready": true, "has_video": true, "bytes_delta": 1048576, "readers": 2, "source_flag": "mediamtx", "status_after": "online"}],
 "transitions": [{"at": "…", "from": "offline", "to": "online"}]}
```

### 3.8 `GET /cameras/export?format=csv` — registry export (permission `cameras.export`)

Same filters as the list. Streams a CSV with the template columns (§4.1) plus `id, source, status, last_seen_at_ist, relay_path, created_at_ist`; the last line is a watermark `# Exported by <username> at <IST> from Sentinel Gujarat 1.0.0-phase1; sha256 of rows above: <hex>`. Headers: `Content-Disposition: attachment; filename="cameras_export_<YYYYMMDD_HHMM>IST.csv"`, `X-Sentinel-Sha256`. Recorded in the `report_files` ledger and the audit log. This export is the submission's **sample onboarded camera-metadata dataset**.

### 3.9 `GET /cameras/import/template`

Returns `cameras_template.csv` (header of §4.1 + two example rows).

### 3.10 `GET /departments` — the valid `department_code` values (permission `cameras.read`)

Lists every department in the registry in code order, including departments that have no cameras yet, so that a departmental system, a CSV author or the manual form can pick a valid `department_code` (§2.4) before onboarding. No parameters; not paginated (27 seeded rows, one per Gujarat government department plus `UNASSIGNED`); scoped roles receive the full list because it contains no camera data.

```json
{ "items": [ { "id": 22, "code": "AGRI", "name": "Agriculture & Farmers Welfare" }, "…", { "id": 2, "code": "POLICE", "name": "Gujarat Police" }, "…", { "id": 1, "code": "UNASSIGNED", "name": "Unassigned" } ] }
```

`department_code` is matched case-insensitively against `code`, and the importers also accept the display `name` or a seeded alias (for example `Municipal Corporation` → `MUNICIPAL`); anything else lands in `UNASSIGNED` with a row warning, never a row error. Any value not listed here should be treated as a data-quality issue on the sending side.

```bash
curl -s https://<host>/api/departments -H "Authorization: Bearer $TOKEN" | jq -r '.items[] | "\(.code)\t\(.name)"'
```

---

## 4. Bulk onboarding

### 4.1 CSV template (exact header, any column order accepted, unknown columns ignored with a warning)

```
external_id,name,department_code,type,ownership,lat,lon,address,district,police_station,ward,rtsp_url,codec,resolution,fps,storage_location,retention_days,install_date,vendor,model,heading_deg,fov_deg,connectivity_type,bandwidth_kbps,vms_platform,nvr_id,maintenance_status,amc_vendor,amc_expiry,anpr_enabled,record_enabled
```

Valid `department_code` values for the template come from `GET /departments` (§3.10); enumerations for the other columns are in §2.3.

### 4.2 `POST /cameras/import/csv` (permission `cameras.write`)

`multipart/form-data`: `file` (UTF-8 CSV with or without BOM, comma delimiter, ≤ 200 MB, ≤ 20,000 rows), `dry_run` (`true/false`, default false). A `dept_admin` gets a row error `"outside your department scope"` for rows of other departments. Response `200` even when some rows failed (`422` only when the header is unusable):

```json
{
  "job_id": "2026-09-04T10-05-11_7f3a", "dry_run": false, "rows_total": 10, "added": 7, "updated": 1,
  "errors": [
    {"row": 4, "external_id": "CSV-004", "field": "lat", "message": "must be between -90 and 90 (got 95.0)"},
    {"row": 9, "external_id": "CSV-002", "field": "external_id", "message": "duplicate external_id in file (first seen at row 2)"}
  ],
  "warnings": [{"row": 7, "field": "department_code", "message": "unknown department 'HOUSING_SOCIETY' mapped to UNASSIGNED"}],
  "error_report_url": "/media/exports/2026-09-04/import_errors_2026-09-04T10-05-11_7f3a.csv",
  "relay_paths_created": 8, "relay_paths_failed": 0, "duration_ms": 240
}
```

The error report CSV (`row,external_id,field,message,original_line`) is downloadable with the same credentials.

```bash
curl -X POST https://<host>/api/cameras/import/csv -H "Authorization: Bearer $TOKEN" -F file=@cameras.csv -F dry_run=true
```

### 4.3 `POST /v1/cameras/bulk` — API push from a departmental system (API key, scope `bulk`)

Body `{"cameras": [CameraImportRow, …]}` (a bare JSON array is also accepted), ≤ 1,000 rows per call; `source='api'`; idempotent upsert on `external_id`, so a department can re-push its whole inventory on a schedule.

```bash
curl -X POST https://<host>/api/v1/cameras/bulk -H "X-API-Key: sk_…" -H "Content-Type: application/json" -d '{"cameras": [
  {"external_id": "GSRTC-101", "name": "Mehsana Depot Gate", "department_code": "GSRTC", "lat": 23.588, "lon": 72.369,
   "district": "Mehsana", "type": "bullet", "rtsp_url": "rtsp://user:pass@10.20.30.40:554/Streaming/Channels/101", "codec": "H264"}]}'
```

```json
{"added": 2, "updated": 1, "errors": [{"index": 3, "external_id": "X9", "field": "name", "message": "field required"}], "warnings": [],
 "results": [{"index": 0, "external_id": "GSRTC-101", "id": 61, "action": "added"}, {"index": 1, "external_id": "GSRTC-102", "id": 62, "action": "added"},
             {"index": 2, "external_id": "3", "id": 12, "action": "updated"}, {"index": 3, "external_id": "X9", "id": null, "action": "error"}],
 "duration_ms": 88}
```

Wrong key scope → `403`; missing key → `401`.

### 4.4 `POST /cameras/import/sandbox` — catalogue import (admin)

Body optional `{"measure_first_stream": true, "dry_run": false}`. Reads the `catalogue.*` settings (§6), fetches `{base_url}/api/ingest`, unwraps wrapper objects, maps fields through `catalogue.field_map`, maps department names through `catalogue.department_aliases`, upserts with `source='sandbox'`, creates relay paths, and — when asked — measures the time to the first ready stream (the onboarding-efficiency evidence).

```json
{
  "source_url": "http://<host>/api/ingest", "started_at": "…", "finished_at": "…", "duration_ms": 3412,
  "fetched": 50, "added": 50, "updated": 0, "unchanged": 0, "errors": [],
  "warnings": [{"row": 17, "external_id": "17", "field": "department", "message": "unknown department 'Roads' mapped to UNASSIGNED"}],
  "relay_paths_created": 61, "relay_paths_failed": 0, "anpr_enabled": 8, "first_stream_ready_ms": 2210, "dry_run": false
}
```

Second run against an unchanged catalogue → `added=0, updated=0, unchanged=50`. Catalogue unreachable → `502 upstream_error` naming the URL.

---

## 5. Geo, health and gap analysis

| Method & path | Permission | Response |
|---|---|---|
| `GET /geo/cameras?department_id&district&status&type` | cameras.read | GeoJSON `FeatureCollection` of non-retired cameras with coordinates; properties `id, external_id, name, department_code, department_name, district, police_station, type, ownership, status, maintenance_status, anpr_enabled, live, codec, heading_deg, fov_deg, last_seen_at` |
| `GET /geo/districts` | cameras.read | GeoJSON of the 33 districts with `camera_count`, `online_count` |
| `GET /geo/pois?district&type` | cameras.read | GeoJSON points with `nearest_camera_m` |
| `GET /geo/coverage?radius=150&district=&include_offline=` | cameras.read | One `MultiPolygon` per district = union of camera buffers; cached 5 min |
| `GET /health/summary` | cameras.read | Camera counts by status, 24 h uptime %, `down_over_5min[]`, `amc_expiring_30d[]`, `maintenance[]`, disk, relay and ANPR-worker status |
| `GET /gap-analysis?coverage_radius_m&poi_radius_m&grid_m&ageing_years&district&refresh` | cameras.read | Summary, coverage by area and district, department × district gaps, uncovered POIs, zero-coverage grid (GeoJSON), offline hotspots, metadata gaps, **ageing infrastructure** with a replacement-priority score, templated recommendations |
| `GET /gap-analysis/export?format=csv\|pdf` | cameras.export | Sectioned CSV or PDF with a static map; hashed and ledgered like every export |

Health state machine (poller every 60 s): `online` = relay path ready with a video track and bytes flowing (or an active `ffprobe` success for idle on-demand paths); `degraded` = ready without video/bytes; `offline` after 3 consecutive not-ready checks (raises a low-priority `camera_offline` alert, auto-closed when the camera returns). Cameras the catalogue marks `live=false` are not probed.

---

## 6. Settings that drive onboarding (admin)

| Method & path | Notes |
|---|---|
| `GET /settings` | `{"items": [{"key", "value", "is_secret", "updated_by_username", "updated_at"}]}`; secrets masked as `********` |
| `PUT /settings` `{"values": {...}}` | Unknown key → 422; a secret submitted as `********` is unchanged; audited with secrets redacted |
| `POST /settings/catalogue/test` | Fetches the catalogue with the saved (or supplied) settings → `{"ok": true, "status": 200, "count": 50, "sample": {…first raw item…}, "mapped_sample": {…CameraImportRow…}, "unmapped_fields": ["camera_type"], "duration_ms": 120}` or `{"ok": false, "error": "…"}` |
| `GET /settings/public` | Non-secret keys (`ui.*`, `route.*`, `gap.*`, `alerts.escalate_minutes`, `mock_sandbox`) for the UI |

Catalogue keys: `catalogue.base_url`, `catalogue.auth_type` (`none|basic|bearer|header`), `catalogue.auth_username`, `catalogue.auth_password` (secret), `catalogue.auth_header` (secret, `Name: value`), `catalogue.timeout_s`, `catalogue.field_map` (target → ordered candidate source paths, dotted, e.g. `"lat": ["location.lat", "lat", "latitude", "gps.lat"]`), `catalogue.department_aliases` (lowercase display name → department code). Wrapper objects are unwrapped automatically (`cameras`, `data`, `items`, `results`, `streams`). A new environment whose catalogue differs in shape is therefore onboarded by editing the field map in the UI and running **Test connection** until `unmapped_fields` is empty — no code change.

---

## 7. Machine access: API keys, webhooks, audit export (admin)

| Method & path | Notes |
|---|---|
| `GET /api-keys` | `{items: [{id, name, key_prefix, scope, is_active, created_by_username, last_used_at, created_at}]}` |
| `POST /api-keys` `{name, scope}` | `201 {id, name, scope, key: "sk_…", key_prefix, created_at}` — the key is shown **once** |
| `DELETE /api-keys/{id}` | Deactivates |
| `GET/POST /webhooks`, `PUT/DELETE /webhooks/{id}`, `POST /webhooks/{id}/test` | Outbound notifications: `POST <url>` with `{"event", "ts", "delivery_id", "data"}`, headers `X-Sentinel-Event`, `X-Sentinel-Delivery`, `X-Sentinel-Signature: sha256=<HMAC of body>`; events `alert.created`, `alert.updated`, `camera.offline`, `camera.online`, `event.created`; retries after 2/4/8 s |
| `GET /audit?user&action&entity&entity_id&from&to&q` and `GET /audit/export?format=csv` | Append-only trail of every mutation and every sensitive read (`camera.import_sandbox`, `camera.import_csv`, `camera.import_bulk`, `camera.export`, `stream.view`, …) with actor, role, before/after, IP, user agent |

---

## 8. Regenerating this document from the running API

```bash
# Markdown from the live schema (Node ≥ 18, no dependencies)
node docs/tools/render-registry-api.mjs https://<host>/api/openapi.json > docs/REGISTRY-API.generated.md
# or from a saved file
curl -s https://<host>/api/openapi.json -o openapi.json && node docs/tools/render-registry-api.mjs openapi.json > docs/REGISTRY-API.generated.md
```

The generated file lists every operation with its parameters, request body schema and response codes exactly as FastAPI publishes them; the curated text above is kept for the narrative (import rules, scoping, examples). For the Drive deliverable `Sentinel-Gujarat_REGISTRY-API.pdf`, export this file followed by the generated appendix.

*Sentinel Gujarat · Dynatech Consultancy · Registry API v1.0.0-phase1 · live reference at `/api/docs`.*
