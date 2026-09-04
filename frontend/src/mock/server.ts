/**
 * In-memory mock of the Sentinel Gujarat API for UI review without the backend
 * (`VITE_MOCK=1`). Intercepts `window.fetch` for `/api/*`, `/media/*`, `/mtx/*`,
 * `/playback/*` and replaces `window.WebSocket` for `/ws/*` with a fake that emits
 * the CONTRACT §9 envelopes. Mutations change the in-memory state so the flows
 * (ack/close, watchlist CRUD, settings, users, keys, confirmations) feel real.
 *
 * `window.__sgMock.emitAlert()` pushes a live alert through the alerts socket
 * (used by the screenshot script to capture the toast).
 */
import type { Alert, AlertWsPayload, Camera, RouteFlag, RouteLeg, Sighting, WsEnvelope } from '@/api/types';
import { normalisePlate, formatPlate } from '@/utils/plate';
import { haversineKm } from '@/utils/geo';
import * as D from './data';
import { snapshot } from './images';

type Json = unknown;
interface MockResponse {
  status?: number;
  body?: Json;
  headers?: Record<string, string>;
  text?: string;
  blob?: Blob;
}
type Handler = (m: RegExpMatchArray, url: URL, init: RequestInit) => MockResponse | Promise<MockResponse>;
interface Route {
  method: string;
  re: RegExp;
  fn: Handler;
}

const routes: Route[] = [];
const on = (method: string, pattern: string, fn: Handler) => routes.push({ method, re: new RegExp(`^${pattern}$`), fn });
const err = (status: number, code: string, detail: string, extra: Record<string, unknown> = {}): MockResponse => ({ status, body: { detail, code, ...extra } });
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

const TOKEN = 'mock.eyJzdWIiOiIxIn0.signature';
const PERMS: Record<string, string[]> = {
  admin: ['cameras.read', 'cameras.write', 'cameras.export', 'analytics.read', 'watchlist.write', 'alerts.ack', 'route.confirm', 'events.write', 'reports.export', 'external.lookup', 'zones.write', 'admin.users', 'admin.audit', 'admin.apikeys', 'admin.settings', 'settings.read_public'],
  dept_admin: ['cameras.read', 'cameras.write', 'cameras.export', 'analytics.read', 'watchlist.write', 'alerts.ack', 'route.confirm', 'events.write', 'reports.export', 'external.lookup', 'zones.write', 'settings.read_public'],
  operator: ['cameras.read', 'cameras.export', 'analytics.read', 'watchlist.write', 'alerts.ack', 'route.confirm', 'events.write', 'reports.export', 'external.lookup', 'settings.read_public'],
  viewer: ['cameras.read', 'analytics.read', 'settings.read_public'],
};
const PASSWORDS: Record<string, string> = { jury_admin: 'Sentinel@Admin2026', jury_operator: 'Sentinel@Ops2026', jury_viewer: 'Sentinel@View2026', dept_admin_police: 'Sentinel@Police2026' };
let currentUser = D.users[0];
let wallLayout = { grid: 9 as 4 | 9 | 16, tiles: [1, 2, 3, 4, 6, 7, 8, 51, 5].map((camera_id, slot) => ({ slot, camera_id })) };
const confirmations = new Map<string, 'confirmed' | 'rejected'>();
const qaLabels = new Map<number, string>();
let nextId = 10_000;

/* ------------------------------------------------------------------ helpers */

function body<T>(init: RequestInit): T {
  if (!init.body || typeof init.body !== 'string') return {} as T;
  try {
    return JSON.parse(init.body) as T;
  } catch {
    return {} as T;
  }
}
function paginate<T>(items: T[], url: URL, sortable: Record<string, (x: T) => string | number | null>, defaultSort: string, defaultOrder: 'asc' | 'desc'): MockResponse {
  const page = Number(url.searchParams.get('page') ?? 1);
  const size = Math.min(500, Number(url.searchParams.get('page_size') ?? 25));
  const sort = url.searchParams.get('sort') ?? defaultSort;
  const order = (url.searchParams.get('order') as 'asc' | 'desc' | null) ?? defaultOrder;
  const key = sortable[sort];
  if (!key) return err(422, 'validation_error', `Unknown sort column '${sort}'`, { errors: [{ field: 'sort', message: `must be one of ${Object.keys(sortable).join(', ')}` }] });
  const sorted = [...items].sort((a, b) => {
    const va = key(a);
    const vb = key(b);
    if (va === vb) return 0;
    if (va === null) return 1;
    if (vb === null) return -1;
    const c = va < vb ? -1 : 1;
    return order === 'asc' ? c : -c;
  });
  return { body: { items: sorted.slice((page - 1) * size, page * size), total: sorted.length, page, page_size: size } };
}
const inWindow = (ts: string, url: URL, defaultHours = 24) => {
  const from = url.searchParams.get('from') ? Date.parse(url.searchParams.get('from') as string) : D.NOW_MS - defaultHours * 3_600_000;
  const to = url.searchParams.get('to') ? Date.parse(url.searchParams.get('to') as string) : Number.POSITIVE_INFINITY;
  const t = Date.parse(ts);
  return t >= from && t <= to;
};
const num = (url: URL, k: string) => (url.searchParams.get(k) ? Number(url.searchParams.get(k)) : undefined);
const str = (url: URL, k: string) => url.searchParams.get(k) ?? undefined;
const csvBlob = (rows: string[][], trailer: string) => new Blob([rows.map((r) => r.map((c) => (/[",\n]/.test(c) ? `"${c.replace(/"/g, '""')}"` : c)).join(',')).join('\n') + `\n${trailer}\n`], { type: 'text/csv' });
const pdfBlob = (title: string) =>
  new Blob([`%PDF-1.4\n% Sentinel Gujarat mock ${title}\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n`], { type: 'application/pdf' });
function levenshtein(a: string, b: string): number {
  const m = a.length;
  const n = b.length;
  const dp: number[] = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= m; i += 1) {
    let prev = dp[0];
    dp[0] = i;
    for (let j = 1; j <= n; j += 1) {
      const tmp = dp[j];
      dp[j] = Math.min(dp[j] + 1, dp[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
      prev = tmp;
    }
  }
  return dp[n];
}
const istStamp = () => new Date(D.NOW_MS + 5.5 * 3_600_000).toISOString().replace(/[-:]/g, '').replace('T', '_').slice(0, 13);

/* ------------------------------------------------------------------ auth */

on('POST', '/auth/login', (_m, _u, init) => {
  const { username, password } = body<{ username: string; password: string }>(init);
  const u = D.users.find((x) => x.username === (username ?? '').toLowerCase() && x.is_active);
  if (!u || PASSWORDS[u.username] !== password) return err(401, 'unauthorized', 'Invalid username or password');
  currentUser = u;
  return { body: { access_token: TOKEN, token_type: 'bearer', expires_at: D.iso(D.NOW_MS + 8 * 3_600_000), user: { id: u.id, username: u.username, full_name: u.full_name, role: u.role, department_id: u.department_id, department_name: u.department_name, district: u.district } } };
});
on('POST', '/auth/logout', () => ({ status: 204 }));
on('GET', '/auth/me', () => ({ body: { id: currentUser.id, username: currentUser.username, full_name: currentUser.full_name, role: currentUser.role, department_id: currentUser.department_id, department_name: currentUser.department_name, district: currentUser.district, permissions: PERMS[currentUser.role] } }));
on('POST', '/auth/change-password', () => ({ status: 204 }));
on('GET', '/me/wall-layout', () => ({ body: wallLayout }));
on('PUT', '/me/wall-layout', (_m, _u, init) => {
  wallLayout = body(init);
  return { body: wallLayout };
});

/* ------------------------------------------------------------------ cameras */

const camSort: Record<string, (c: Camera) => string | number | null> = {
  name: (c) => c.name.toLowerCase(),
  external_id: (c) => c.external_id,
  status: (c) => c.status,
  district: (c) => c.district,
  department_name: (c) => c.department_name,
  last_seen_at: (c) => c.last_seen_at,
  updated_at: (c) => c.updated_at,
  created_at: (c) => c.created_at,
  install_date: (c) => c.install_date,
  amc_expiry: (c) => c.amc_expiry,
};
function scopedCameras(): Camera[] {
  return D.cameras.filter((c) => currentUser.role !== 'dept_admin' || c.department_id === currentUser.department_id);
}
function filterCameras(url: URL): Camera[] {
  const q = str(url, 'q')?.toLowerCase();
  const status = str(url, 'status')?.split(',');
  return scopedCameras().filter(
    (c) =>
      (str(url, 'include_retired') === 'true' || c.status !== 'retired') &&
      (!q || c.name.toLowerCase().includes(q) || c.external_id.toLowerCase().includes(q) || (c.address ?? '').toLowerCase().includes(q) || (c.police_station ?? '').toLowerCase().includes(q)) &&
      (!num(url, 'department_id') || c.department_id === num(url, 'department_id')) &&
      (!str(url, 'district') || c.district === str(url, 'district')) &&
      (!str(url, 'type') || c.type === str(url, 'type')) &&
      (!status || status.includes(c.status)) &&
      (!str(url, 'source') || c.source === str(url, 'source')) &&
      (!str(url, 'maintenance_status') || c.maintenance_status === str(url, 'maintenance_status')) &&
      (!str(url, 'anpr_enabled') || String(c.anpr_enabled) === str(url, 'anpr_enabled')),
  );
}
on('GET', '/cameras', (_m, url) => paginate(filterCameras(url), url, camSort, 'name', 'asc'));
on('GET', '/departments', () => ({ body: { items: D.departments } }));
on('GET', '/cameras/export', (_m, url) => {
  const cams = filterCameras(url);
  const rows = [['id', 'external_id', 'name', 'department_code', 'type', 'lat', 'lon', 'district', 'status', 'relay_path'], ...cams.map((c) => [String(c.id), c.external_id, c.name, c.department_code, c.type, String(c.lat ?? ''), String(c.lon ?? ''), c.district ?? '', c.status, c.relay_path ?? ''])];
  return { blob: csvBlob(rows, `# Exported by ${currentUser.username} from Sentinel Gujarat 1.0.0-phase1; sha256 of rows above: ${D.sha('export')}`), headers: { 'Content-Disposition': `attachment; filename="cameras_export_${istStamp()}IST.csv"`, 'X-Sentinel-Sha256': D.sha('export') } };
});
on('GET', '/cameras/import/template', () => ({
  blob: csvBlob(
    [
      ['external_id', 'name', 'department_code', 'type', 'ownership', 'lat', 'lon', 'address', 'district', 'police_station', 'ward', 'rtsp_url', 'codec', 'resolution', 'fps', 'storage_location', 'retention_days', 'install_date', 'vendor', 'model', 'heading_deg', 'fov_deg', 'connectivity_type', 'bandwidth_kbps', 'vms_platform', 'nvr_id', 'maintenance_status', 'amc_vendor', 'amc_expiry', 'anpr_enabled', 'record_enabled'],
      ['CSV-001', 'Bhilad Checkpost North Lane', 'POLICE', 'analog', 'govt_dept', '20.2650', '72.9120', 'NH-48, Bhilad', 'Valsad', 'Bhilad', '', '', 'H264', '704x576', '12', 'DVR at checkpost', '15', '2017-05-10', 'CP Plus', 'CP-USC-TA24L2', '20', '70', '4g', '2000', 'none', 'DVR-VLS-07', 'ok', 'Secure Vision AMC', '2026-09-25', 'false', 'false'],
    ],
    '',
  ),
  headers: { 'Content-Disposition': 'attachment; filename="cameras_template.csv"' },
}));
on('POST', '/cameras/import/sandbox', async (_m, _u, init) => {
  if (currentUser.role !== 'admin') return err(403, 'forbidden', 'Insufficient role');
  const b = body<{ measure_first_stream?: boolean; dry_run?: boolean }>(init);
  await wait(1800);
  return { body: { source_url: 'http://api:8000/mock-sandbox/api/ingest', started_at: D.iso(D.NOW_MS - 3412), finished_at: D.iso(D.NOW_MS), duration_ms: 3412, fetched: 50, added: 0, updated: 0, unchanged: 50, errors: [], warnings: [{ row: 17, external_id: '17', field: 'department', message: "unknown department 'Roads' mapped to UNASSIGNED" }], relay_paths_created: 61, relay_paths_failed: 0, anpr_enabled: 8, first_stream_ready_ms: b.measure_first_stream === false ? null : 2210, dry_run: Boolean(b.dry_run) } };
});
on('POST', '/cameras/import/csv', async (_m, _u, init) => {
  const fd = init.body instanceof FormData ? init.body : null;
  const dry = fd?.get('dry_run') === 'true';
  await wait(900);
  return { body: { job_id: `${D.iso(D.NOW_MS).slice(0, 19).replace(/:/g, '-')}_7f3a`, dry_run: dry, rows_total: 10, added: dry ? 0 : 8, updated: 0, errors: [{ row: 4, external_id: 'CSV-004', field: 'lat', message: 'must be between -90 and 90 (got 95.0)' }, { row: 9, external_id: 'CSV-002', field: 'external_id', message: 'duplicate external_id in file (first seen at row 2)' }, { row: 9, external_id: 'CSV-002', field: 'name', message: 'field required' }], warnings: [{ row: 7, external_id: 'CSV-007', field: 'department_code', message: "unknown department 'HOUSING_SOCIETY' mapped to UNASSIGNED" }], error_report_url: '/media/exports/2026-09-04/import_errors_2026-09-04T10-05-11_7f3a.csv', relay_paths_created: dry ? 0 : 8, relay_paths_failed: 0, duration_ms: 240 } };
});
on('GET', '/cameras/(\\d+)', (m) => {
  const c = scopedCameras().find((x) => x.id === Number(m[1]));
  return c ? { body: D.cameraDetail(c) } : err(404, 'not_found', 'Camera not found');
});
on('POST', '/cameras', (_m, _u, init) => {
  const row = body<Record<string, unknown>>(init);
  if (!row.external_id || !row.name) return err(422, 'validation_error', 'Validation failed', { errors: [{ field: !row.external_id ? 'external_id' : 'name', message: 'field required' }] });
  if (D.cameras.some((c) => c.external_id === row.external_id)) return err(409, 'conflict', `external_id '${row.external_id}' already exists`);
  nextId += 1;
  const dept = D.departments.find((d) => d.code === row.department_code) ?? D.departments[0];
  const base = D.cameras[0];
  const c: Camera = { ...base, id: nextId, external_id: String(row.external_id), name: String(row.name), department_id: dept.id, department_code: dept.code, department_name: dept.name, type: (row.type as Camera['type']) ?? 'ip', ownership: (row.ownership as Camera['ownership']) ?? 'govt_dept', lat: (row.lat as number) ?? null, lon: (row.lon as number) ?? null, address: (row.address as string) ?? null, district: (row.district as string) ?? null, police_station: (row.police_station as string) ?? null, rtsp_url: (row.rtsp_url as string) ?? null, relay_path: row.rtsp_url ? `cam_${nextId}` : null, codec: (row.codec as Camera['codec']) ?? 'UNKNOWN', source: (row.source as Camera['source']) ?? 'manual', created_via: (row.source as string) ?? 'manual', status: 'unknown', anpr_enabled: Boolean(row.anpr_enabled), record_enabled: Boolean(row.record_enabled), live: null, created_at: D.iso(D.NOW_MS), updated_at: D.iso(D.NOW_MS), created_by: currentUser.id, created_by_username: currentUser.username, uptime_24h_pct: null, last_seen_at: null, whep_url: null, hls_url: null };
  D.cameras.push(c);
  D.cameraById.set(c.id, c);
  return { status: 201, body: c };
});
on('PUT', '/cameras/(\\d+)', (m, _u, init) => {
  const c = D.cameraById.get(Number(m[1]));
  if (!c) return err(404, 'not_found', 'Camera not found');
  Object.assign(c, body(init), { updated_at: D.iso(Date.now()) });
  return { body: c };
});
on('PUT', '/cameras/(\\d+)/maintenance', (m, _u, init) => {
  const c = D.cameraById.get(Number(m[1]));
  if (!c) return err(404, 'not_found', 'Camera not found');
  Object.assign(c, body(init), { updated_at: D.iso(Date.now()) });
  return { body: c };
});
on('DELETE', '/cameras/(\\d+)', (m) => {
  const c = D.cameraById.get(Number(m[1]));
  if (!c) return err(404, 'not_found', 'Camera not found');
  c.status = 'retired';
  c.retired_at = D.iso(Date.now());
  return { status: 204 };
});
on('GET', '/cameras/(\\d+)/health', (m, url) => {
  const c = D.cameraById.get(Number(m[1]));
  if (!c) return err(404, 'not_found', 'Camera not found');
  const hours = num(url, 'hours') ?? 24;
  const g = D.mulberry32(c.id);
  const checks = Math.min(1500, hours * 60);
  const log = Array.from({ length: checks }, (_, i) => {
    const ready = c.status === 'online' ? g() > 0.03 : c.status === 'degraded' ? g() > 0.3 : c.status === 'offline' ? g() < 0.05 : false;
    return { checked_at: D.iso(D.NOW_MS - i * 60_000), is_ready: ready, has_video: ready, bytes_delta: ready ? Math.round(120_000 + g() * 80_000) : 0, readers: ready ? 1 + Math.round(g() * 2) : 0, source_flag: (ready && g() < 0.2 ? 'probe' : 'mediamtx') as 'probe' | 'mediamtx', status_after: ready ? c.status === 'degraded' && g() < 0.5 ? 'degraded' : 'online' : c.status === 'offline' ? 'offline' : 'degraded' };
  });
  const transitions = c.status === 'online' ? [{ at: D.hoursAgo(5), from: 'degraded', to: 'online' }, { at: D.hoursAgo(5.2), from: 'online', to: 'degraded' }] : c.status === 'offline' ? [{ at: c.last_status_change_at as string, from: 'online', to: 'offline' }] : [];
  return { body: { camera_id: c.id, status: c.status, uptime_pct: c.uptime_24h_pct, last_seen_at: c.last_seen_at, last_status_change_at: c.last_status_change_at, checks, log, transitions } };
});

/* ------------------------------------------------------------------ geo / streams / health */

on('GET', '/geo/cameras', (_m, url) => ({ body: D.geoCameras({ department_id: num(url, 'department_id'), district: str(url, 'district'), status: str(url, 'status'), type: str(url, 'type') }) }));
on('GET', '/geo/districts', () => ({ body: D.geoDistricts() }));
on('GET', '/geo/pois', (_m, url) => ({ body: D.geoPois(str(url, 'district')) }));
on('GET', '/geo/coverage', (_m, url) => ({ body: D.geoCoverage(num(url, 'radius') ?? 150) }));
on('GET', '/streams/(\\d+)', (m) => {
  const c = D.cameraById.get(Number(m[1]));
  if (!c) return err(404, 'not_found', 'Camera not found');
  const play = c.codec === 'H265' ? `cam_${c.id}_h264` : `cam_${c.id}`;
  return { body: { camera_id: c.id, name: c.name, codec: c.codec, relay_path: c.relay_path, play_path: play, whep_url: `/mtx/${play}/whep`, hls_url: `/mtx/${play}/index.m3u8`, snapshot_url: D.snapshotUrl(c), snapshot_updated_at: c.anpr_enabled ? D.iso(Date.now() - 800) : null, snapshot_stale: false, ready: c.status === 'online' || c.status === 'degraded', readers: c.status === 'online' ? 2 : 0, record_enabled: c.record_enabled, playback_path: play, status: c.status } };
});
on('GET', '/health/summary', () => ({ body: D.healthSummary() }));
on('GET', '/healthz', () => ({ body: { status: 'ok', version: '1.0.0-phase1', time: D.iso(Date.now()), db: 'ok', mediamtx: 'ok', anpr_workers: 2, uptime_s: 86_400 * 2 + 3123 } }));
on('GET', '/gap-analysis', (_m, url) => ({ body: D.gapAnalysis({ coverage_radius_m: num(url, 'coverage_radius_m'), poi_radius_m: num(url, 'poi_radius_m'), grid_m: num(url, 'grid_m'), ageing_years: num(url, 'ageing_years'), district: str(url, 'district') }) }));
on('GET', '/gap-analysis/export', (_m, url) => {
  const fmt = str(url, 'format') ?? 'csv';
  const h = D.sha(`gap-${fmt}`);
  return { blob: fmt === 'pdf' ? pdfBlob('gap analysis') : csvBlob([['section', 'district', 'value'], ['by_district', 'Gandhinagar', '8']], `# sha256(rows)=${h}`), headers: { 'Content-Disposition': `attachment; filename="gap_analysis_${istStamp()}IST.${fmt}"`, 'X-Sentinel-Sha256': h } };
});

/* ------------------------------------------------------------------ detections / sightings */

on('GET', '/detections', (_m, url) => {
  const plate = str(url, 'plate') ? normalisePlate(str(url, 'plate') as string).plate_norm : undefined;
  const fuzzy = str(url, 'fuzzy') === '1' || str(url, 'fuzzy') === 'true';
  const minConf = num(url, 'min_conf');
  const items = D.detections.filter(
    (d) =>
      inWindow(d.captured_at, url) &&
      (!plate || (fuzzy ? levenshtein(d.plate_norm, plate) <= 2 : d.plate_norm === plate)) &&
      (!num(url, 'camera_id') || d.camera.id === num(url, 'camera_id')) &&
      (!num(url, 'department_id') || d.camera.department_id === num(url, 'department_id')) &&
      (!str(url, 'district') || d.camera.district === str(url, 'district')) &&
      (minConf === undefined || d.confidence >= minConf) &&
      (str(url, 'valid_only') !== 'true' || d.is_valid_format) &&
      (!str(url, 'mode') || d.mode === str(url, 'mode')) &&
      (!num(url, 'sighting_id') || d.sighting_id === num(url, 'sighting_id')),
  );
  return paginate(items, url, { captured_at: (d) => d.captured_at, confidence: (d) => d.confidence, plate_norm: (d) => d.plate_norm, camera_name: (d) => d.camera.name }, 'captured_at', 'desc');
});
on('GET', '/detections/(\\d+)', (m) => {
  const d = D.detectionById.get(Number(m[1]));
  return d ? { body: D.detectionDetail(d) } : err(404, 'not_found', 'Read not found');
});
on('GET', '/sightings', (_m, url) => {
  const plate = str(url, 'plate') ? normalisePlate(str(url, 'plate') as string).plate_norm : undefined;
  const items = D.sightings.filter((s) => inWindow(s.first_seen, url) && (!plate || s.plate_norm === plate) && (!num(url, 'camera_id') || s.camera.id === num(url, 'camera_id')));
  return paginate(items, url, { first_seen: (s) => s.first_seen, best_conf: (s) => s.best_conf }, 'first_seen', 'desc');
});

/* ------------------------------------------------------------------ vehicles */

function searchHits(q: string, url: URL) {
  const win = (s: Sighting) => inWindow(s.first_seen, url, 24) && (!num(url, 'camera_id') || s.camera.id === num(url, 'camera_id')) && (!num(url, 'department_id') || s.camera.department_id === num(url, 'department_id'));
  const exact = D.sightings.filter((s) => s.plate_norm === q && win(s));
  const fuzzy = D.sightings
    .filter((s) => s.plate_norm !== q && win(s) && levenshtein(s.plate_norm, q) <= 2)
    .map((s) => {
      const distance = levenshtein(s.plate_norm, q);
      return { s, distance, score: Number(((1 - distance / 3) * 0.7 + s.best_conf * 0.3).toFixed(2)) };
    })
    .sort((a, b) => b.score - a.score);
  return { exact, fuzzy };
}
on('GET', '/vehicles/search', (_m, url) => {
  const raw = str(url, 'q') ?? '';
  const norm = normalisePlate(raw);
  const limit = Math.min(200, num(url, 'limit') ?? 50);
  const { exact, fuzzy } = searchHits(norm.plate_norm, url);
  const from = str(url, 'from') ?? D.hoursAgo(24);
  const to = str(url, 'to') ?? D.iso(Date.now());
  const conf = (s: Sighting) => confirmations.get(`${norm.plate_norm}:${s.id}`) ?? null;
  return {
    body: {
      query: { raw, normalised: norm.plate_norm, is_valid_format: norm.is_valid_format, from, to },
      exact: exact.slice(0, limit).map((s) => ({ ...s, match: 'exact', score: 1, confirmation: conf(s) })),
      fuzzy: fuzzy.slice(0, limit).map(({ s, distance, score }) => ({ ...s, match: 'fuzzy', distance, similarity: Number((0.9 - distance * 0.15).toFixed(2)), score, confirmation: conf(s), sample_reads: D.detections.filter((d) => d.sighting_id === s.id).slice(0, 3).map((d) => ({ id: d.id, plate_raw: d.plate_raw, confidence: d.confidence, crop_url: d.crop_url })) })),
      cameras_seen: new Set(exact.map((s) => s.camera.id)).size,
    },
  };
});
on('POST', '/vehicles/([^/]+)/confirm', (m, _u, init) => {
  const plate = normalisePlate(decodeURIComponent(m[1])).plate_norm;
  const { decisions } = body<{ decisions: { sighting_id: number; decision: 'confirmed' | 'rejected' }[] }>(init);
  (decisions ?? []).forEach((d) => confirmations.set(`${plate}:${d.sighting_id}`, d.decision));
  return { body: { saved: decisions?.length ?? 0 } };
});
function buildRoute(plateRaw: string, url: URL) {
  const plate = normalisePlate(decodeURIComponent(plateRaw)).plate_norm;
  const include = str(url, 'include') ?? 'confirmed';
  const { exact, fuzzy } = searchHits(plate, url);
  const conf = (s: Sighting) => confirmations.get(`${plate}:${s.id}`) ?? null;
  const chosen = [...exact.filter((s) => conf(s) !== 'rejected').map((s) => ({ s, match: 'exact' as const })), ...fuzzy.filter(({ s }) => (include === 'all' ? conf(s) !== 'rejected' : conf(s) === 'confirmed')).map(({ s }) => ({ s, match: 'fuzzy' as const }))].sort((a, b) => Date.parse(a.s.first_seen) - Date.parse(b.s.first_seen));
  // merge same-camera sightings within 60 s
  const merged: { s: Sighting; match: 'exact' | 'fuzzy'; first: number; last: number; reads: number }[] = [];
  chosen.forEach(({ s, match }) => {
    const prev = merged[merged.length - 1];
    const first = Date.parse(s.first_seen);
    const last = Date.parse(s.last_seen);
    if (prev && prev.s.camera.id === s.camera.id && first - prev.last <= 60_000) {
      prev.last = Math.max(prev.last, last);
      prev.reads += s.read_count;
    } else merged.push({ s, match, first, last, reads: s.read_count });
  });
  const speedFlag = 150;
  const legs: RouteLeg[] = [];
  const flags: RouteFlag[] = [];
  for (let i = 1; i < merged.length; i += 1) {
    const a = merged[i - 1];
    const b = merged[i];
    const km = haversineKm(a.s.camera.lat ?? 0, a.s.camera.lon ?? 0, b.s.camera.lat ?? 0, b.s.camera.lon ?? 0);
    const minutes = (b.first - a.last) / 60_000;
    const speed = minutes > 0 ? km / (minutes / 60) : null;
    const legFlags: string[] = [];
    if (minutes <= 0) legFlags.push('overlap');
    if (speed !== null && speed > speedFlag) legFlags.push('implausible_speed');
    if (minutes > 6 * 60) legFlags.push('long_gap');
    legs.push({ from_seq: i, to_seq: i + 1, distance_km: Number(km.toFixed(2)), minutes: Number(minutes.toFixed(2)), speed_kmh: speed === null ? null : Number(speed.toFixed(1)), flags: legFlags });
    legFlags.forEach((t) =>
      flags.push({ from_seq: i, to_seq: i + 1, type: t as RouteFlag['type'], distance_km: Number(km.toFixed(2)), minutes: Number(minutes.toFixed(2)), speed_kmh: speed === null ? null : Number(speed.toFixed(1)), message: t === 'implausible_speed' ? `${Math.round(speed ?? 0)} km/h between ${a.s.camera.name} and ${b.s.camera.name} (${minutes.toFixed(1)} min for ${km.toFixed(1)} km)` : t === 'overlap' ? `Overlapping sightings between ${a.s.camera.name} and ${b.s.camera.name}` : `Gap of ${(minutes / 60).toFixed(1)} h between ${a.s.camera.name} and ${b.s.camera.name}` }),
    );
  }
  const sightingsOut = merged.map((m, i) => ({ seq: i + 1, sighting_id: m.s.id, camera: m.s.camera, first_seen: D.iso(m.first), last_seen: D.iso(m.last), read_count: m.reads, best_conf: m.s.best_conf, crop_url: m.s.best_crop_url, frame_url: m.s.frame_url, match: m.match, confirmation: conf(m.s), recording_available: m.s.recording_available, in_polyline: m.s.camera.lat !== null }));
  return {
    plate,
    plate_display: formatPlate(plate),
    window: { from: str(url, 'from') ?? D.hoursAgo(24), to: str(url, 'to') ?? D.iso(Date.now()), include },
    sightings: sightingsOut,
    polyline: merged.filter((m) => m.s.camera.lat !== null).map((m) => [m.s.camera.lat as number, m.s.camera.lon as number]),
    legs,
    flags,
    total_distance_km: Number(legs.reduce((s, l) => s + l.distance_km, 0).toFixed(2)),
    total_duration_min: merged.length ? Number(((merged[merged.length - 1].last - merged[0].first) / 60_000).toFixed(1)) : 0,
    cameras_count: new Set(merged.map((m) => m.s.camera.id)).size,
    loop_resets_in_window: D.events.filter((e) => e.type === 'loop_reset' && inWindow(e.occurred_at, url, 24)).length,
  };
}
on('GET', '/vehicles/([^/]+)/route', (m, url) => ({ body: buildRoute(m[1], url) }));
on('GET', '/vehicles/([^/]+)/route.pdf', (m) => {
  const h = D.sha(`route-${m[1]}`);
  return { blob: pdfBlob('route'), headers: { 'Content-Disposition': `attachment; filename="route_${m[1]}_${istStamp()}IST.pdf"`, 'X-Sentinel-Sha256': h } };
});

/* ------------------------------------------------------------------ watchlist */

on('GET', '/watchlist', (_m, url) => {
  const q = str(url, 'q')?.toLowerCase();
  const active = str(url, 'is_active') ?? 'true';
  const items = D.watchlist.filter(
    (w) =>
      (active === 'all' || String(w.is_active) === active) &&
      (!q || (w.plate_norm ?? '').toLowerCase().includes(q.replace(/\s+/g, '')) || (w.name ?? '').toLowerCase().includes(q)) &&
      (!str(url, 'entity_type') || w.entity_type === str(url, 'entity_type')) &&
      (!str(url, 'reason') || w.reason === str(url, 'reason')) &&
      (!str(url, 'priority') || w.priority === str(url, 'priority')) &&
      (!str(url, 'source') || w.source === str(url, 'source')) &&
      (!str(url, 'expired') || String(Boolean(w.expires_at && Date.parse(w.expires_at) < Date.now())) === str(url, 'expired')),
  );
  return paginate(items, url, { created_at: (w) => w.created_at, plate_norm: (w) => w.plate_norm, priority: (w) => ['critical', 'high', 'medium', 'low'].indexOf(w.priority), hit_count: (w) => w.hit_count, last_hit_at: (w) => w.last_hit_at }, 'created_at', 'desc');
});
on('GET', '/watchlist/import/template', () => ({ blob: csvBlob([['plate', 'entity_type', 'name', 'reason', 'priority', 'source', 'notes', 'expires_at', 'is_active'], ['GJ01AB1234', 'vehicle', 'White Maruti Swift – FIR 123/2026', 'stolen', 'critical', 'own', 'Reported stolen', '', 'true']], ''), headers: { 'Content-Disposition': 'attachment; filename="watchlist_template.csv"' } }));
on('POST', '/watchlist/import/csv', async () => {
  await wait(700);
  return { body: { rows_total: 12, added: 10, updated: 1, errors: [{ row: 5, field: 'plate', message: 'not a valid Indian registration format' }], error_report_url: '/media/exports/2026-09-04/import_errors_wl.csv', duration_ms: 120 } };
});
on('POST', '/watchlist', (_m, _u, init) => {
  const b = body<{ entity_type: 'vehicle' | 'person'; plate?: string; name?: string; reason: string; priority?: string; source?: string; notes?: string; expires_at?: string | null; is_active?: boolean }>(init);
  let plate: string | null = null;
  if (b.entity_type === 'vehicle') {
    const n = normalisePlate(b.plate ?? '');
    if (!n.is_valid_format) return err(422, 'validation_error', 'Validation failed', { errors: [{ field: 'plate', message: 'not a valid Indian registration format' }] });
    plate = n.plate_norm;
    const dup = D.watchlist.find((w) => w.plate_norm === plate && w.is_active && w.entity_type === 'vehicle');
    if (dup) return err(409, 'conflict', `Plate ${formatPlate(plate)} is already on the active watchlist`, { existing_id: dup.id });
  }
  nextId += 1;
  const row = { id: nextId, entity_type: b.entity_type, plate_norm: plate, plate_display: plate ? formatPlate(plate) : null, name: b.name ?? null, reason: b.reason as never, priority: (b.priority ?? 'medium') as never, source: (b.source ?? 'manual') as never, notes: b.notes ?? null, photo_path: null, added_by: currentUser.id, added_by_username: currentUser.username, is_active: b.is_active ?? true, expires_at: b.expires_at ?? null, hit_count: 0, last_hit_at: null, is_effective: (b.is_active ?? true) && (!b.expires_at || Date.parse(b.expires_at) > Date.now()), alerts_24h: 0, created_at: D.iso(Date.now()), updated_at: D.iso(Date.now()) };
  D.watchlist.unshift(row);
  return { status: 201, body: row };
});
on('PUT', '/watchlist/(\\d+)', (m, _u, init) => {
  const w = D.watchlist.find((x) => x.id === Number(m[1]));
  if (!w) return err(404, 'not_found', 'Watchlist entry not found');
  const b = body<Record<string, unknown>>(init);
  if (typeof b.plate === 'string') {
    const n = normalisePlate(b.plate);
    if (!n.is_valid_format) return err(422, 'validation_error', 'Validation failed', { errors: [{ field: 'plate', message: 'not a valid Indian registration format' }] });
    w.plate_norm = n.plate_norm;
    w.plate_display = formatPlate(n.plate_norm);
    delete b.plate;
  }
  Object.assign(w, b, { updated_at: D.iso(Date.now()) });
  w.is_effective = w.is_active && (!w.expires_at || Date.parse(w.expires_at) > Date.now());
  return { body: w };
});
on('DELETE', '/watchlist/(\\d+)', (m) => {
  const i = D.watchlist.findIndex((x) => x.id === Number(m[1]));
  if (i < 0) return err(404, 'not_found', 'Watchlist entry not found');
  D.watchlist.splice(i, 1);
  return { status: 204 };
});

/* ------------------------------------------------------------------ alerts / events */

const PRIORITY_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };
function scopedAlerts(): Alert[] {
  return D.alerts.filter((a) => currentUser.role !== 'dept_admin' || a.camera.department_id === currentUser.department_id);
}
on('GET', '/alerts', (_m, url) => {
  const statuses = (str(url, 'status') ?? 'new,acknowledged').split(',');
  const plate = str(url, 'plate') ? normalisePlate(str(url, 'plate') as string).plate_norm : undefined;
  const items = scopedAlerts().filter(
    (a) =>
      statuses.includes(a.status) &&
      inWindow(a.created_at, url) &&
      (!str(url, 'type') || a.type === str(url, 'type')) &&
      (!str(url, 'priority') || a.priority === str(url, 'priority')) &&
      (!num(url, 'camera_id') || a.camera.id === num(url, 'camera_id')) &&
      (!num(url, 'department_id') || a.camera.department_id === num(url, 'department_id')) &&
      (!plate || a.plate_norm === plate) &&
      (!str(url, 'escalated') || String(a.escalated) === str(url, 'escalated')),
  );
  const sort = str(url, 'sort') ?? 'priority';
  if (sort === 'priority') {
    const sorted = [...items].sort((a, b) => PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority] || Date.parse(b.created_at) - Date.parse(a.created_at));
    const page = num(url, 'page') ?? 1;
    const size = num(url, 'page_size') ?? 25;
    return { body: { items: sorted.slice((page - 1) * size, page * size), total: sorted.length, page, page_size: size } };
  }
  return paginate(items, url, { created_at: (a) => a.created_at }, 'created_at', 'desc');
});
on('GET', '/alerts/(\\d+)', (m) => {
  const a = scopedAlerts().find((x) => x.id === Number(m[1]));
  return a ? { body: D.alertDetail(a) } : err(404, 'not_found', 'Alert not found');
});
on('POST', '/alerts/(\\d+)/ack', (m, _u, init) => {
  const a = D.alerts.find((x) => x.id === Number(m[1]));
  if (!a) return err(404, 'not_found', 'Alert not found');
  if (a.status !== 'new') return err(409, 'conflict', `Alert is already ${a.status}`);
  const { note } = body<{ note?: string }>(init);
  Object.assign(a, { status: 'acknowledged', acknowledged_by: currentUser.id, acknowledged_by_username: currentUser.username, acknowledged_at: D.iso(Date.now()), note: note ?? null, escalated: false, updated_at: D.iso(Date.now()) });
  sockets.forEach((s) => s.pathIs('/ws/alerts') && s.emit('alert_update', { id: a.id, status: a.status, priority: a.priority, confidence_level: a.confidence_level, read_count: a.read_count, last_read_at: a.last_read_at, acknowledged_by_username: a.acknowledged_by_username, acknowledged_at: a.acknowledged_at, closed_by_username: null, closed_at: null, outcome: null, note: a.note, escalated: false, updated_at: a.updated_at }));
  return { body: a };
});
on('POST', '/alerts/(\\d+)/close', (m, _u, init) => {
  const a = D.alerts.find((x) => x.id === Number(m[1]));
  if (!a) return err(404, 'not_found', 'Alert not found');
  if (a.status === 'closed') return err(409, 'conflict', 'Alert is already closed');
  const { note, outcome } = body<{ note?: string; outcome?: Alert['outcome'] }>(init);
  const now = D.iso(Date.now());
  Object.assign(a, { status: 'closed', acknowledged_by: a.acknowledged_by ?? currentUser.id, acknowledged_by_username: a.acknowledged_by_username ?? currentUser.username, acknowledged_at: a.acknowledged_at ?? now, closed_by: currentUser.id, closed_by_username: currentUser.username, closed_at: now, outcome: outcome ?? 'resolved', note: note ?? a.note, escalated: false, updated_at: now });
  return { body: a };
});
on('GET', '/events', (_m, url) => {
  const types = str(url, 'type')?.split(',');
  const items = D.events.filter((e) => inWindow(e.occurred_at, url) && (!num(url, 'camera_id') || e.camera_id === num(url, 'camera_id')) && (!num(url, 'department_id') || e.camera.department_id === num(url, 'department_id')) && (!types || types.includes(e.type)) && (!str(url, 'is_auto') || String(e.is_auto) === str(url, 'is_auto')) && (!num(url, 'sighting_id') || e.sighting_id === num(url, 'sighting_id')) && (!num(url, 'alert_id') || e.alert_id === num(url, 'alert_id')));
  return paginate(items, url, { occurred_at: (e) => e.occurred_at }, 'occurred_at', 'desc');
});
on('POST', '/events', (_m, _u, init) => {
  const b = body<{ camera_id: number; type: string; occurred_at?: string; note?: string; sighting_id?: number | null; read_id?: number | null }>(init);
  const cam = D.cameraById.get(b.camera_id);
  if (!cam) return err(404, 'not_found', 'Camera not found');
  if (!['accident', 'suspicious', 'checkpoint', 'other'].includes(b.type)) return err(422, 'validation_error', 'Validation failed', { errors: [{ field: 'type', message: 'must be one of accident, suspicious, checkpoint, other' }] });
  nextId += 1;
  const labels: Record<string, string> = { accident: 'Accident', suspicious: 'Suspicious activity', checkpoint: 'Checkpoint', other: 'Other' };
  const e = { id: nextId, camera_id: cam.id, camera: D.summary(cam), occurred_at: b.occurred_at ?? D.iso(Date.now()), type: b.type as never, type_label: labels[b.type], note: b.note ?? null, sighting_id: b.sighting_id ?? null, read_id: b.read_id ?? null, alert_id: null, frame_url: null, frame_sha256: null, is_auto: false, created_by: currentUser.id, created_by_username: currentUser.username, created_at: D.iso(Date.now()) };
  D.events.unshift(e);
  return { status: 201, body: e };
});

/* ------------------------------------------------------------------ dashboard / reports */

on('GET', '/dashboard/stats', () => ({ body: D.dashboardStats() }));
on('GET', '/dashboard/charts', (_m, url) => ({ body: D.dashboardCharts((str(url, 'bucket') as 'hour' | 'day') ?? 'hour', str(url, 'from') ? Date.parse(str(url, 'from') as string) : D.NOW_MS - 24 * 3_600_000, num(url, 'camera_id')) }));
on('GET', '/reports/detections', async (_m, url) => {
  if (!str(url, 'from')) return err(422, 'validation_error', "Query parameter 'from' is required", { errors: [{ field: 'from', message: 'field required' }] });
  await wait(600);
  const fmt = str(url, 'format') ?? 'csv';
  const rows = D.detections.filter((d) => inWindow(d.captured_at, url));
  const h = D.sha(`det-${fmt}-${rows.length}`);
  const file = { id: (nextId += 1), type: (fmt === 'pdf' ? 'detections_pdf' : 'detections_csv') as 'detections_pdf' | 'detections_csv', path: `reports/2026-09-04/detections_${istStamp()}IST_${currentUser.username}.${fmt}`, url: `/media/reports/2026-09-04/detections_${istStamp()}IST_${currentUser.username}.${fmt}`, sha256: h, size_bytes: fmt === 'pdf' ? 812_331 : rows.length * 240, params: { from: str(url, 'from'), to: str(url, 'to') }, row_count: rows.length, created_by_username: currentUser.username, created_at: D.iso(Date.now()) };
  D.reportFiles.unshift(file);
  return { blob: fmt === 'pdf' ? pdfBlob('detections') : csvBlob([['captured_at_ist', 'captured_at_utc', 'camera_id', 'camera_external_id', 'camera_name', 'department', 'district', 'lat', 'lon', 'plate', 'plate_raw', 'is_valid_format', 'confidence', 'sighting_id', 'read_id', 'crop_path', 'crop_sha256', 'mode'], ...rows.slice(0, 500).map((d) => [d.captured_at, d.captured_at, String(d.camera.id), d.camera.external_id, d.camera.name, d.camera.department_code, d.camera.district ?? '', String(d.camera.lat), String(d.camera.lon), d.plate_norm, d.plate_raw, String(d.is_valid_format), String(d.confidence), String(d.sighting_id ?? ''), String(d.id), 'crops/…', d.crop_sha256 ?? '', d.mode])], `# Sentinel Gujarat 1.0.0-phase1 | rows=${rows.length} | sha256(rows)=${h}`), headers: { 'Content-Disposition': `attachment; filename="detections_${istStamp()}IST.${fmt}"`, 'X-Sentinel-Sha256': h } };
});
on('GET', '/reports/quality', (_m, url) => {
  if (str(url, 'format') === 'pdf') {
    const h = D.sha('quality-pdf');
    return { blob: pdfBlob('quality'), headers: { 'Content-Disposition': `attachment; filename="quality_${istStamp()}IST.pdf"`, 'X-Sentinel-Sha256': h } };
  }
  return { body: D.qualityReport(num(url, 'camera_id') ?? null) };
});
on('GET', '/reports/history', (_m, url) => paginate(D.reportFiles, url, { created_at: (r) => r.created_at, type: (r) => r.type, size_bytes: (r) => r.size_bytes }, 'created_at', 'desc'));
on('GET', '/qa/sample', (_m, url) => {
  const n = num(url, 'n') ?? 30;
  const items = D.detections.filter((d) => d.is_valid_format && !qaLabels.has(d.id) && (!num(url, 'camera_id') || d.camera.id === num(url, 'camera_id'))).slice(0, n);
  return { body: { items: items.map((d) => ({ id: d.id, plate_norm: d.plate_norm, plate_display: d.plate_display, confidence: d.confidence, crop_url: d.crop_url, camera: d.camera, captured_at: d.captured_at })) } };
});
on('POST', '/qa/labels', (_m, _u, init) => {
  const { labels } = body<{ labels: { read_id: number; true_plate: string }[] }>(init);
  let exact = 0;
  (labels ?? []).forEach((l) => {
    const norm = normalisePlate(l.true_plate).plate_norm;
    qaLabels.set(l.read_id, norm);
    const d = D.detectionById.get(l.read_id);
    if (d) {
      d.qa_label = { true_plate: norm, is_match: norm === d.plate_norm };
      if (norm === d.plate_norm) exact += 1;
    }
  });
  return { body: { saved: labels?.length ?? 0, exact, char_accuracy_pct: 96.2 } };
});
on('GET', '/evidence/verify', (_m, url) => {
  const path = str(url, 'path') ?? '';
  if (path.includes('..') || path.startsWith('/')) return err(422, 'validation_error', 'Invalid path');
  const report = D.reportFiles.find((r) => r.path === path);
  const h = report?.sha256 ?? D.sha(path);
  return { body: { path, exists: true, entity: report ? 'report_file' : path.startsWith('crops') ? 'plate_read' : 'clip', entity_id: report?.id ?? 9876, stored_sha256: h, computed_sha256: h, match: true, size_bytes: report?.size_bytes ?? 18_234, checked_at: D.iso(Date.now()) } };
});
on('GET', '/external/vahan/([^/]+)', (m) => {
  const plate = normalisePlate(decodeURIComponent(m[1])).plate_norm;
  const g = D.mulberry32(plate.split('').reduce((s, c) => s + c.charCodeAt(0), 0));
  const found = g() > 0.2;
  const owners = ['R. Patel', 'S. Shah', 'M. Desai', 'K. Joshi', 'A. Mehta'];
  const models = ['Maruti Swift VXI', 'Hyundai Creta SX', 'Tata Nexon XZ', 'Honda City V', 'Toyota Fortuner 4x2'];
  return { body: found ? { source: 'VAHAN (mock adapter)', adapter: 'vahan_mock', plate, found: true, owner_name: owners[Math.floor(g() * 5)], vehicle_class: 'Motor Car', maker_model: models[Math.floor(g() * 5)], fuel: g() > 0.5 ? 'Petrol' : 'Diesel', colour: ['White', 'Silver', 'Grey', 'Black', 'Red'][Math.floor(g() * 5)], registration_date: '2019-06-12', rto: `${plate.slice(0, 2)}-${plate.slice(2, 4)} ${plate.startsWith('GJ') ? 'Ahmedabad' : 'RTO'}`, insurance_valid_till: '2027-03-31', fitness_valid_till: '2034-06-11', note: 'Mock data – integration-ready adapter; real VAHAN access requires NIC gateway credentials' } : { source: 'VAHAN (mock adapter)', adapter: 'vahan_mock', plate, found: false, note: 'Mock data – integration-ready adapter; real VAHAN access requires NIC gateway credentials' } };
});

/* ------------------------------------------------------------------ recordings / clips / objects */

on('GET', '/recordings/(\\d+)', (m) => {
  const c = D.cameraById.get(Number(m[1]));
  if (!c) return err(404, 'not_found', 'Camera not found');
  const path = c.codec === 'H265' ? `cam_${c.id}_h264` : `cam_${c.id}`;
  const segs = c.record_enabled ? Array.from({ length: 150 }, (_, i) => { const start = Math.floor((D.NOW_MS - (150 - i) * 60_000) / 60_000) * 60_000; return { start: D.iso(start), duration_s: 60, end: D.iso(start + 60_000), url: `/playback/get?path=${path}&start=${encodeURIComponent(D.iso(start))}&duration=60&format=mp4` }; }) : [];
  return { body: { camera_id: c.id, playback_path: path, record_enabled: c.record_enabled, retention_h: 12, segments: segs } };
});
on('GET', '/recordings/(\\d+)/play', (m, url) => {
  const c = D.cameraById.get(Number(m[1]));
  if (!c) return err(404, 'not_found', 'Camera not found');
  const at = Date.parse(str(url, 'at') ?? D.iso(D.NOW_MS)) - (num(url, 'before_s') ?? 10) * 1000;
  if (!c.record_enabled) return { body: { url: null, start: null, duration_s: 30, available: false } };
  return { body: { url: `/playback/get?path=cam_${c.id}&start=${encodeURIComponent(D.iso(at))}&duration=${num(url, 'duration_s') ?? 30}&format=mp4`, start: D.iso(at), duration_s: num(url, 'duration_s') ?? 30, available: true } };
});
const clips: unknown[] = [];
on('POST', '/clips', (_m, _u, init) => {
  const b = body<{ camera_id: number; start_at: string; duration_s?: number; alert_id?: number; sighting_id?: number }>(init);
  const c = D.cameraById.get(b.camera_id);
  if (!c) return err(404, 'not_found', 'Camera not found');
  if (!c.record_enabled) return err(409, 'conflict', 'No recording covers the requested start time');
  nextId += 1;
  const clip = { id: nextId, camera_id: c.id, camera: D.summary(c), alert_id: b.alert_id ?? null, sighting_id: b.sighting_id ?? null, start_at: b.start_at, duration_s: b.duration_s ?? 30, path: `clips/${c.id}/${nextId}.mp4`, url: `/media/clips/${c.id}/${nextId}.mp4`, sha256: D.sha(`clip-${nextId}`), size_bytes: 4_812_330, created_by: currentUser.id, created_by_username: currentUser.username, created_at: D.iso(Date.now()) };
  clips.unshift(clip);
  return { status: 201, body: clip };
});
on('GET', '/clips', (_m, url) => paginate(clips as { camera_id: number; created_at: string }[], url, { created_at: (c) => c.created_at }, 'created_at', 'desc'));
on('GET', '/object-counts', (_m, url) => ({ body: D.objectCountsSeries(num(url, 'camera_id') ?? 1) }));
on('GET', '/zones', (_m, url) => ({ body: num(url, 'camera_id') === 51 ? D.cameraDetail(D.cameraById.get(51) as Camera).zones : [] }));

/* ------------------------------------------------------------------ admin */

const mask = (s: (typeof D.settings)[number]) => ({ ...s, value: s.is_secret ? (s.value ? '********' : '') : s.value });
on('GET', '/settings', () => (currentUser.role === 'admin' ? { body: { items: D.settings.map(mask) } } : err(403, 'forbidden', 'Insufficient role')));
on('PUT', '/settings', (_m, _u, init) => {
  const { values } = body<{ values: Record<string, unknown> }>(init);
  for (const [k, v] of Object.entries(values ?? {})) {
    const s = D.settings.find((x) => x.key === k);
    if (!s) return err(422, 'validation_error', `Unknown setting '${k}'`, { errors: [{ field: k, message: 'unknown key' }] });
    if (s.is_secret && v === '********') continue;
    s.value = v as never;
    s.updated_at = D.iso(Date.now());
    s.updated_by_username = currentUser.username;
  }
  return { body: { items: D.settings.map(mask) } };
});
on('POST', '/settings/catalogue/test', async (_m, _u, init) => {
  await wait(500);
  const b = body<{ base_url?: string }>(init);
  const base = b.base_url ?? (D.settings.find((s) => s.key === 'catalogue.base_url')?.value as string);
  if (base && !base.includes('mock-sandbox')) return { body: { ok: false, error: `GET ${base}/api/ingest: connection refused` } };
  return { body: { ok: true, status: 200, count: 50, sample: { id: 1, name: 'Sachivalaya Gate 1', department: 'Police', district: 'Gandhinagar', type: 'bullet', location: { lat: 23.2236, lon: 72.648, address: 'Sachivalaya, Sector 10, Gandhinagar' }, codec: 'H264', resolution: '1280x720', fps: 10, live: true, rtsp_url: 'rtsp://mediamtx:8554/stream/1', whep_url: 'http://mediamtx:8889/stream/1/whep', hls_url: 'http://mediamtx:8888/stream/1/index.m3u8', stream_properties: { bitrate_kbps: 1200 } }, mapped_sample: { external_id: '1', name: 'Sachivalaya Gate 1', department_code: 'POLICE', district: 'Gandhinagar', lat: 23.2236, lon: 72.648, address: 'Sachivalaya, Sector 10, Gandhinagar', type: 'bullet', codec: 'H264', resolution: '1280x720', fps: 10, live: true, rtsp_url: 'rtsp://mediamtx:8554/stream/1' }, unmapped_fields: ['stream_properties.bitrate_kbps'], duration_ms: 118 } };
});
on('GET', '/settings/public', () => ({ body: { mock_sandbox: true, 'ui.product_name': 'Sentinel Gujarat', 'ui.map_center': [23.2156, 72.6369], 'ui.map_zoom': 8, 'route.speed_flag_kmh': 150, 'route.default_window_h': 24, 'gap.coverage_radius_m': 150, 'gap.poi_radius_m': 300, 'gap.grid_m': 500, 'gap.ageing_years': 5, 'alerts.escalate_minutes': 5 } }));
on('GET', '/webhooks', () => ({ body: { items: D.webhooks } }));
on('POST', '/webhooks', (_m, _u, init) => {
  const b = body<{ name: string; url: string; secret?: string; event_types: string[]; is_active?: boolean }>(init);
  nextId += 1;
  const w = { id: nextId, name: b.name, url: b.url, secret: b.secret ? '********' : null, event_types: b.event_types as never, is_active: b.is_active ?? true, last_status: null, last_delivered_at: null, last_error: null, created_at: D.iso(Date.now()) };
  D.webhooks.push(w);
  return { status: 201, body: w };
});
on('PUT', '/webhooks/(\\d+)', (m, _u, init) => {
  const w = D.webhooks.find((x) => x.id === Number(m[1]));
  if (!w) return err(404, 'not_found', 'Webhook not found');
  Object.assign(w, body(init));
  return { body: w };
});
on('DELETE', '/webhooks/(\\d+)', (m) => {
  const i = D.webhooks.findIndex((x) => x.id === Number(m[1]));
  if (i < 0) return err(404, 'not_found', 'Webhook not found');
  D.webhooks.splice(i, 1);
  return { status: 204 };
});
on('POST', '/webhooks/(\\d+)/test', async (m) => {
  await wait(400);
  const w = D.webhooks.find((x) => x.id === Number(m[1]));
  if (!w) return err(404, 'not_found', 'Webhook not found');
  const ok = w.url.includes('mock-sandbox');
  w.last_status = ok ? 204 : 502;
  w.last_delivered_at = D.iso(Date.now());
  w.last_error = ok ? null : 'connect timeout after 5 s';
  return { body: { status: w.last_status, duration_ms: ok ? 42 : 5003, error: w.last_error } };
});
on('GET', '/api-keys', () => ({ body: { items: D.apiKeys } }));
on('POST', '/api-keys', (_m, _u, init) => {
  const b = body<{ name: string; scope: 'bulk' | 'internal' }>(init);
  if (D.apiKeys.some((k) => k.name === b.name)) return err(409, 'conflict', `An API key named '${b.name}' already exists`);
  nextId += 1;
  const g = D.mulberry32(nextId);
  const raw = Array.from({ length: 40 }, () => '0123456789abcdefghijklmnopqrstuvwxyz'[Math.floor(g() * 36)]).join('');
  const key = { id: nextId, name: b.name, key_prefix: raw.slice(0, 8), scope: b.scope, is_active: true, created_by_username: currentUser.username, last_used_at: null, created_at: D.iso(Date.now()) };
  D.apiKeys.push(key);
  return { status: 201, body: { id: key.id, name: key.name, scope: key.scope, key: `sk_${raw}`, key_prefix: key.key_prefix, created_at: key.created_at } };
});
on('DELETE', '/api-keys/(\\d+)', (m) => {
  const k = D.apiKeys.find((x) => x.id === Number(m[1]));
  if (!k) return err(404, 'not_found', 'API key not found');
  k.is_active = false;
  return { status: 204 };
});
on('GET', '/users', (_m, url) => {
  const q = str(url, 'q')?.toLowerCase();
  const items = D.users.filter((u) => (!q || u.username.includes(q) || u.full_name.toLowerCase().includes(q)) && (!str(url, 'role') || u.role === str(url, 'role')) && (!str(url, 'is_active') || String(u.is_active) === str(url, 'is_active')));
  return paginate(items, url, { username: (u) => u.username, created_at: (u) => u.created_at, last_login_at: (u) => u.last_login_at, role: (u) => u.role }, 'username', 'asc');
});
on('POST', '/users', (_m, _u, init) => {
  const b = body<{ username: string; password: string; full_name: string; role: string; department_id?: number | null; district?: string | null }>(init);
  if (D.users.some((u) => u.username === b.username.toLowerCase())) return err(409, 'conflict', `Username '${b.username}' already exists`);
  if (!b.password || b.password.length < 10) return err(422, 'validation_error', 'Validation failed', { errors: [{ field: 'password', message: 'at least 10 characters with one letter and one digit' }] });
  nextId += 1;
  const dept = D.departments.find((d) => d.id === b.department_id);
  const u = { id: nextId, username: b.username.toLowerCase(), full_name: b.full_name, role: b.role as never, department_id: dept?.id ?? null, department_name: dept?.name ?? null, district: b.district ?? null, is_active: true, last_login_at: null, created_at: D.iso(Date.now()), updated_at: D.iso(Date.now()) };
  D.users.push(u);
  return { status: 201, body: u };
});
on('PUT', '/users/(\\d+)', (m, _u, init) => {
  const u = D.users.find((x) => x.id === Number(m[1]));
  if (!u) return err(404, 'not_found', 'User not found');
  const b = body<Record<string, unknown>>(init);
  if ('department_id' in b) {
    const dept = D.departments.find((d) => d.id === b.department_id);
    b.department_name = dept?.name ?? null;
  }
  Object.assign(u, b, { updated_at: D.iso(Date.now()) });
  return { body: u };
});
on('POST', '/users/(\\d+)/reset-password', () => ({ status: 204 }));
on('DELETE', '/users/(\\d+)', (m) => {
  const u = D.users.find((x) => x.id === Number(m[1]));
  if (!u) return err(404, 'not_found', 'User not found');
  if (u.id === currentUser.id) return err(409, 'conflict', 'You cannot deactivate your own account');
  u.is_active = false;
  return { status: 204 };
});
on('GET', '/audit', (_m, url) => {
  if (currentUser.role !== 'admin') return err(403, 'forbidden', 'Insufficient role');
  const user = str(url, 'user');
  const action = str(url, 'action');
  const q = str(url, 'q')?.toLowerCase();
  const items = D.audit.filter((a) => inWindow(a.ts, url, 24 * 7) && (!user || a.username === user || String(a.user_id) === user) && (!action || a.action.startsWith(action)) && (!str(url, 'entity') || a.entity === str(url, 'entity')) && (!str(url, 'entity_id') || a.entity_id === str(url, 'entity_id')) && (!q || a.action.includes(q) || a.actor.includes(q) || (a.entity_id ?? '').includes(q)));
  return paginate(items, url, { ts: (a) => a.ts }, 'ts', 'desc');
});
on('GET', '/audit/export', () => {
  const h = D.sha('audit');
  return { blob: csvBlob([['ts', 'actor', 'role', 'action', 'entity', 'entity_id', 'ip'], ...D.audit.slice(0, 200).map((a) => [a.ts, a.actor, a.role, a.action, a.entity ?? '', a.entity_id ?? '', a.ip ?? ''])], `# sha256(rows)=${h}`), headers: { 'Content-Disposition': `attachment; filename="audit_${istStamp()}IST.csv"`, 'X-Sentinel-Sha256': h } };
});
on('GET', '/mock-sandbox/api/ingest', () => ({ body: D.cameras.slice(0, 50).map((c) => ({ id: c.id, name: c.name, department: c.department_name, district: c.district, type: c.type, location: { lat: c.lat, lon: c.lon, address: c.address }, codec: c.codec, resolution: c.resolution, fps: c.fps, live: c.live, rtsp_url: c.rtsp_url, whep_url: c.whep_url, hls_url: c.hls_url })) }));

/* ------------------------------------------------------------------ fetch shim */

const originalFetch = window.fetch.bind(window);

async function handle(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const rawUrl = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
  const url = new URL(rawUrl, window.location.origin);
  const method = (init.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
  const path = url.pathname;
  if (path.startsWith('/media/')) {
    // Report/export downloads: hand back a small file; crops are data URIs already.
    const name = path.split('/').pop() ?? 'file';
    const blob = name.endsWith('.pdf') ? pdfBlob(name) : csvBlob([['row', 'external_id', 'field', 'message', 'original_line'], ['4', 'CSV-004', 'lat', 'must be between -90 and 90 (got 95.0)', 'CSV-004,Jamnagar Rozi Port Road,...'], ['9', 'CSV-002', 'external_id', 'duplicate external_id in file (first seen at row 2)', 'CSV-002,,FCS,...'], ['9', 'CSV-002', 'name', 'field required', 'CSV-002,,FCS,...']], '');
    return new Response(blob, { status: 200, headers: { 'Content-Type': blob.type, 'Content-Disposition': `attachment; filename="${name}"`, 'X-Sentinel-Sha256': D.sha(name) } });
  }
  if (path.startsWith('/mtx/') || path.startsWith('/playback/')) return new Response('', { status: 404 });
  if (!path.startsWith('/api/') && path !== '/healthz') return originalFetch(input, init);
  const apiPath = path === '/healthz' ? '/healthz' : path.slice(4);
  const route = routes.find((r) => r.method === method && r.re.test(apiPath));
  const m = route ? (apiPath.match(route.re) as RegExpMatchArray) : null;
  await wait(60 + Math.random() * 120);
  if (!route || !m) return new Response(JSON.stringify({ detail: `Mock: no handler for ${method} ${apiPath}`, code: 'not_found' }), { status: 404, headers: { 'Content-Type': 'application/json' } });
  const res = await route.fn(m, url, init);
  const status = res.status ?? (method === 'POST' && res.body ? 200 : 200);
  if (res.blob) return new Response(res.blob, { status, headers: { 'Content-Type': res.blob.type, ...(res.headers ?? {}) } });
  if (status === 204) return new Response(null, { status: 204, headers: res.headers });
  return new Response(JSON.stringify(res.body ?? null), { status, headers: { 'Content-Type': 'application/json', ...(res.headers ?? {}) } });
}

/* ------------------------------------------------------------------ WebSocket shim */

const sockets = new Set<MockSocket>();

class MockSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSING = 2;
  readonly CLOSED = 3;
  url: string;
  readyState = 0;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  private timers: ReturnType<typeof setInterval>[] = [];
  private path: string;
  constructor(url: string) {
    this.url = url;
    this.path = new URL(url).pathname;
    sockets.add(this);
    setTimeout(() => {
      if (this.readyState !== 0) return;
      this.readyState = 1;
      this.onopen?.(new Event('open'));
      this.emit('hello', { user: currentUser.username, role: currentUser.role, server_time: D.iso(Date.now()), version: '1.0.0-phase1', scope: { department_id: currentUser.department_id, district: currentUser.district } });
      this.start();
    }, 150);
  }
  pathIs(p: string): boolean {
    return this.path === p;
  }
  emit(type: string, data: unknown): void {
    if (this.readyState !== 1) return;
    const env: WsEnvelope = { type, ts: D.iso(Date.now()), data };
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(env) }));
  }
  private start(): void {
    if (this.path === '/ws/alerts') {
      const stats = () => this.emit('stats', { alerts_new: D.alerts.filter((a) => a.status === 'new').length, alerts_acknowledged: D.alerts.filter((a) => a.status === 'acknowledged').length, critical_open: D.alerts.filter((a) => a.status !== 'closed' && a.priority === 'critical').length, reads_last_min: 38 + Math.round(Math.random() * 10), cameras_online: D.cameras.filter((c) => c.status === 'online').length });
      stats();
      this.timers.push(setInterval(stats, 10_000));
    } else if (this.path === '/ws/health') {
      const stats = () => this.emit('stats', { cameras: { total: D.cameras.length, online: D.cameras.filter((c) => c.status === 'online').length, degraded: D.cameras.filter((c) => c.status === 'degraded').length, offline: D.cameras.filter((c) => c.status === 'offline').length, unknown: D.cameras.filter((c) => c.status === 'unknown').length }, uptime_24h_pct: 18.9, anpr_live_cameras: 9, reads_last_min: 38 + Math.round(Math.random() * 10), disk_free_bytes: 398_765_432_100 });
      stats();
      this.timers.push(setInterval(stats, 10_000));
    } else if (this.path.startsWith('/ws/reads/')) {
      const cameraId = Number(this.path.split('/').pop());
      const cam = D.cameraById.get(cameraId);
      if (!cam) {
        this.close(4404, 'not found');
        return;
      }
      const pool = D.detections.filter((d) => d.camera.id === cameraId && d.is_valid_format);
      let i = 0;
      const tick = () => {
        if (!pool.length) return;
        const d = pool[i % pool.length];
        i += 1;
        this.emit('read', { ...d, id: 20_000 + i, captured_at: D.iso(Date.now() - 700), alert_id: null, watchlist_hit: D.watchlist.some((w) => w.is_effective && w.plate_norm === d.plate_norm) });
      };
      this.timers.push(setInterval(tick, 3500));
      this.timers.push(setInterval(() => this.emit('object_counts', { camera_id: cameraId, minute: D.iso(Math.floor(Date.now() / 60_000) * 60_000), counts: { car: 2 + Math.round(Math.random() * 5), person: Math.round(Math.random() * 3), motorcycle: Math.round(Math.random() * 4), bus: 0, truck: Math.round(Math.random()), bicycle: 0 }, final: false }), 5000));
      this.timers.push(setInterval(() => this.emit('snapshot', { camera_id: cameraId, url: snapshot(cameraId, cam.name, Math.random() > 0.5 ? pool[i % Math.max(1, pool.length)]?.plate_display ?? null : null, new Date(Date.now() + 5.5 * 3_600_000).toISOString().slice(11, 19)), updated_at: D.iso(Date.now()) }), 2000));
    }
  }
  send(data: string): void {
    try {
      if (JSON.parse(data).type === 'ping') setTimeout(() => this.emit('pong', {}), 30);
    } catch {
      /* ignore */
    }
  }
  close(code = 1000, reason = ''): void {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.timers.forEach(clearInterval);
    sockets.delete(this);
    this.onclose?.(new CloseEvent('close', { code, reason }));
  }
  addEventListener(): void {
    /* not used by the SPA */
  }
  removeEventListener(): void {
    /* not used by the SPA */
  }
}

/** Push a synthetic live alert through every open alerts socket (demo / screenshots). */
function emitAlert(plate = 'GJ27XY3456'): AlertWsPayload {
  const cam = D.cameraById.get(6) as Camera;
  const s = D.sightings.find((x) => x.plate_norm === plate) ?? D.sightings[0];
  const read = D.detections.find((d) => d.id === s.best_read_id) ?? D.detections[0];
  nextId += 1;
  const now = Date.now();
  const w = D.watchlist.find((x) => x.plate_norm === plate);
  const a: AlertWsPayload = {
    id: nextId,
    type: 'watchlist_hit',
    status: 'new',
    priority: w?.priority ?? 'high',
    confidence_level: 'exact',
    escalated: false,
    created_at: D.iso(now),
    updated_at: D.iso(now),
    latency_ms: 1277,
    camera: D.summary(cam),
    watchlist: w ? { id: w.id, entity_type: w.entity_type, plate_norm: w.plate_norm, plate_display: w.plate_display, name: w.name, reason: w.reason, priority: w.priority, source: w.source } : { id: 0, entity_type: 'vehicle', plate_norm: plate, plate_display: formatPlate(plate), name: 'Demo plate (added live)', reason: 'suspect', priority: 'high', source: 'manual' },
    read: { id: read.id, plate_raw: read.plate_raw, plate_norm: plate, confidence: read.confidence, captured_at: D.iso(now - 1277), crop_url: read.crop_url, crop_sha256: read.crop_sha256 },
    sighting_id: s.id,
    plate_norm: plate,
    snapshot_url: read.crop_url,
    snapshot_sha256: read.crop_sha256,
    read_count: 1,
    last_read_at: D.iso(now - 1277),
    acknowledged_by: null,
    acknowledged_by_username: null,
    acknowledged_at: null,
    closed_by: null,
    closed_by_username: null,
    closed_at: null,
    outcome: null,
    note: null,
    recording_available: true,
    sound: true,
    notify_title: `${(w?.priority ?? 'high').toUpperCase()} · ${w?.reason === 'stolen' ? 'Stolen vehicle' : 'Watchlist vehicle'} ${formatPlate(plate)}`,
    notify_body: `${cam.name} · ${cam.district} · ${new Date(now + 5.5 * 3_600_000).toISOString().slice(11, 19)} IST`,
  };
  D.alerts.unshift(a);
  sockets.forEach((sk) => sk.pathIs('/ws/alerts') && sk.emit('alert', a));
  return a;
}

declare global {
  interface Window {
    __sgMock?: { emitAlert: (plate?: string) => AlertWsPayload; loginAs: (username: string) => void };
  }
}

export function installMockServer(): void {
  window.fetch = handle as typeof window.fetch;
  (window as unknown as { WebSocket: unknown }).WebSocket = MockSocket;
  window.__sgMock = {
    emitAlert,
    loginAs: (username: string) => {
      const u = D.users.find((x) => x.username === username);
      if (u) currentUser = u;
    },
  };
  console.info('[Sentinel Gujarat] mock API installed (VITE_MOCK=1) – no backend calls are made');
}
