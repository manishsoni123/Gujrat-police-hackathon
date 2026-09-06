// Sentinel Gujarat - scripted acceptance checks against a running local stack (CONTRACT §13.3), Node 24, no deps.
//   node scripts/integration_checks.mjs a   registry, streams (HLS/WHEP via Caddy), CSV/bulk import, RBAC, gap analysis, health poller
//   node scripts/integration_checks.mjs b   ANPR reads, seeded + live watchlist alerts (WebSocket), route + PDF, reports, clips/evidence,
//                                            reader-kick discontinuity, settings/catalogue test, webhook sink, login rate limit
// Env: H (default http://localhost), MTX (default http://127.0.0.1:9997), SP (output dir for measurements.json + files; default scripts/out).
// Run phase a on a fresh database (it imports the catalogue); phase b needs the ANPR workers running for >= 2 min.
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
const here = dirname(fileURLToPath(import.meta.url));
import { writeFileSync, readFileSync, existsSync, mkdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { execSync } from 'node:child_process';

const H = process.env.H ?? 'http://localhost';
const MTX = process.env.MTX ?? 'http://127.0.0.1:9997';
const SP = process.env.SP ?? new URL('.', import.meta.url).pathname.replace(/^\/([A-Za-z]):/, '$1:');
const OUT = `${SP}/out`;
mkdirSync(OUT, { recursive: true });
const IK = 'sk_internal0000000000000000000000000000000000';
const BK = 'sk_bulk00000000000000000000000000000000000000';
const phase = process.argv[2] ?? 'a';
const results = [];
const meas = existsSync(`${SP}/measurements.json`) ? JSON.parse(readFileSync(`${SP}/measurements.json`, 'utf8')) : {};
const saveMeas = () => writeFileSync(`${SP}/measurements.json`, JSON.stringify(meas, null, 2));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => new Date();
const iso = (d) => d.toISOString();
function check(name, ok, info = '') {
  results.push([name, Boolean(ok), info]);
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${info ? `  -> ${String(info).slice(0, 300)}` : ''}`);
}
async function req(method, path, { token, key, json, body, headers = {}, raw = false, redirect = 'follow' } = {}) {
  const h = { ...headers };
  if (token) h.Authorization = `Bearer ${token}`;
  if (key) h['X-API-Key'] = key;
  if (json !== undefined) { h['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
  const res = await fetch(`${H}${path}`, { method, headers: h, body, redirect });
  const buf = Buffer.from(await res.arrayBuffer());
  let data = null;
  const ct = res.headers.get('content-type') ?? '';
  if (!raw && ct.includes('application/json')) { try { data = JSON.parse(buf.toString('utf8')); } catch { data = null; } }
  return { status: res.status, headers: res.headers, buf, data, text: raw ? null : buf.toString('utf8') };
}
async function login(username, password) {
  const r = await req('POST', '/api/auth/login', { json: { username, password } });
  if (r.status !== 200) throw new Error(`login ${username} failed: ${r.status} ${r.text}`);
  return { token: r.data.access_token, user: r.data.user, cookie: r.headers.get('set-cookie') ?? '' };
}
const sha = (b) => createHash('sha256').update(b).digest('hex');
async function pollUntil(label, fn, { every = 15000, timeout = 240000 } = {}) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn();
    if (v) return { value: v, elapsed_s: Math.round((Date.now() - t0) / 1000) };
    if (Date.now() - t0 > timeout) return { value: null, elapsed_s: Math.round((Date.now() - t0) / 1000) };
    process.stdout.write(`  … waiting for ${label} (${Math.round((Date.now() - t0) / 1000)} s)\n`);
    await sleep(every);
  }
}
async function camerasByExt(token) {
  const r = await req('GET', '/api/cameras?source=sandbox&page_size=100&sort=external_id&order=asc', { token });
  const m = {};
  for (const c of r.data.items) m[c.external_id] = c;
  return m;
}

async function phaseA() {
  const admin = await login('jury_admin', 'Sentinel@Admin2026');
  check('login admin 200 + cookie', admin.user.role === 'admin' && /sg_session=/.test(admin.cookie) && /HttpOnly/i.test(admin.cookie), admin.cookie.slice(0, 90));
  const A = admin.token;
  const bad = await req('POST', '/api/auth/login', { json: { username: 'jury_admin', password: 'nope' } });
  check('login wrong password 401 unauthorized', bad.status === 401 && bad.data?.code === 'unauthorized', JSON.stringify(bad.data));
  const op = await login('jury_operator', 'Sentinel@Ops2026');
  const vw = await login('jury_viewer', 'Sentinel@View2026');
  const da = await login('dept_admin_police', 'Sentinel@Police2026');
  const me = await req('GET', '/api/auth/me', { token: A });
  check('auth/me permissions', me.status === 200 && Array.isArray(me.data.permissions) && me.data.permissions.includes('admin.settings'), me.data.permissions?.length);

  const ing = await req('GET', '/api/mock-sandbox/api/ingest');
  check('mock catalogue 50 items', ing.status === 200 && Array.isArray(ing.data) && ing.data.length === 50, `len=${ing.data?.length}`);
  const f = ing.data?.[0] ?? {};
  check('mock first item shape', f.id === 1 && f.name === 'Sachivalaya Gate 1' && f.department === 'Police' && f.location?.lat === 23.2236 && f.rtsp_url === 'rtsp://mediamtx:8554/stream/1' && f.live === true, JSON.stringify(f).slice(0, 200));

  let t0 = Date.now();
  const imp = await req('POST', '/api/cameras/import/sandbox', { token: A, json: { measure_first_stream: true } });
  const importWall = Date.now() - t0;
  const j = imp.data ?? {};
  check('sandbox import', imp.status === 200 && j.fetched === 50 && j.added === 50 && (j.errors ?? []).length === 0 && j.anpr_enabled === 8 && j.relay_paths_created === 61 && typeof j.first_stream_ready_ms === 'number' && j.first_stream_ready_ms < 15000, JSON.stringify(j).slice(0, 400));
  meas.import_50 = { duration_ms: j.duration_ms, first_stream_ready_ms: j.first_stream_ready_ms, wall_ms: importWall, relay_paths_created: j.relay_paths_created, warnings: (j.warnings ?? []).length };
  const imp2 = await req('POST', '/api/cameras/import/sandbox', { token: A, json: {} });
  check('sandbox import second run unchanged 50', imp2.status === 200 && imp2.data.added === 0 && imp2.data.updated === 0 && imp2.data.unchanged === 50, JSON.stringify(imp2.data).slice(0, 200));
  const daimp = await req('POST', '/api/cameras/import/sandbox', { token: da.token, json: {} });
  check('dept_admin sandbox import 403', daimp.status === 403);

  const cams = await camerasByExt(A);
  const idOf = (ext) => cams[String(ext)]?.id;
  const list = await req('GET', '/api/cameras?source=sandbox&page_size=5', { token: A });
  check('cameras total 50', list.status === 200 && list.data.total === 50, list.data.total);
  const geo = await req('GET', '/api/geo/cameras', { token: A });
  const feats = geo.data?.features ?? [];
  const live = feats.filter((x) => x.properties.live === true).length;
  const depts = new Set(feats.map((x) => x.properties.department_code));
  check('geo cameras 50 features, 8 live, departments mapped', geo.status === 200 && feats.length === 50 && live === 8 && ['POLICE', 'HEALTH', 'GSRTC', 'PANCHAYAT', 'MUNICIPAL'].every((d) => depts.has(d)) && !depts.has('UNASSIGNED'), `features=${feats.length} live=${live} depts=${[...depts].join(',')}`);
  const dl = await req('GET', '/api/departments', { token: vw.token });
  check('GET /api/departments 27', dl.status === 200 && dl.data.items?.length === 27, dl.data?.items?.length);
  const dist = await req('GET', '/api/geo/districts', { token: A });
  check('geo districts 33', dist.data?.features?.length === 33, dist.data?.features?.length);

  const cfg = await fetch(`${MTX}/v3/config/paths/list?itemsPerPage=1000`).then((r) => r.json());
  const names = new Set((cfg.items ?? []).map((x) => x.name));
  const missing = Object.values(cams).map((c) => `cam_${c.id}`).filter((n) => !names.has(n));
  const h264 = Object.values(cams).filter((c) => c.codec === 'H265').map((c) => `cam_${c.id}_h264`).filter((n) => !names.has(n));
  check('mediamtx config paths >= 70 incl. cam_<id> (+_h264)', names.size >= 70 && missing.length === 0 && h264.length === 0, `paths=${names.size} missing=${missing.join(',')} missing_h264=${h264.join(',')}`);

  const s8 = await req('GET', `/api/streams/${idOf(8)}`, { token: A });
  check('streams mock 8 H265 -> _h264', s8.status === 200 && s8.data.codec === 'H265' && s8.data.play_path === `cam_${idOf(8)}_h264` && s8.data.whep_url === `/mtx/cam_${idOf(8)}_h264/whep`, JSON.stringify(s8.data).slice(0, 200));
  const s1 = await req('GET', `/api/streams/${idOf(1)}`, { token: A });
  check('streams mock 1 cam_<id>', s1.status === 200 && s1.data.play_path === `cam_${idOf(1)}` && s1.data.hls_url === `/mtx/cam_${idOf(1)}/index.m3u8`, JSON.stringify(s1.data).slice(0, 200));

  // HLS + WHEP through the web proxy (Caddy forward_auth)
  const cookie = `sg_session=${A}`;
  t0 = Date.now();
  const hls = await req('GET', `/mtx/cam_${idOf(1)}/index.m3u8`, { headers: { Cookie: cookie } });
  check('HLS cam_1 via /mtx (cookie) 200 #EXTM3U', hls.status === 200 && hls.text.startsWith('#EXTM3U'), `${hls.status} ${Date.now() - t0} ms ${hls.text.slice(0, 60).replace(/\n/g, ' ')}`);
  meas.hls_first_playlist_ms = Date.now() - t0;
  const hlsTok = await req('GET', `/mtx/stream/1/index.m3u8?token=${A}`);
  check('HLS stream/1 via /mtx (?token) 200', hlsTok.status === 200 && hlsTok.text.startsWith('#EXTM3U'), hlsTok.status);
  const hlsNo = await req('GET', `/mtx/cam_${idOf(1)}/index.m3u8`);
  check('HLS without auth 401', hlsNo.status === 401, hlsNo.status);
  const h8 = await req('GET', `/mtx/cam_${idOf(8)}_h264/index.m3u8`, { headers: { Cookie: cookie } });
  check('HLS H.265 camera through _h264 transcode path', h8.status === 200 && h8.text.startsWith('#EXTM3U'), `${h8.status} ${h8.text.slice(0, 40).replace(/\n/g, ' ')}`);
  const opt = await req('OPTIONS', `/mtx/cam_${idOf(1)}/whep`, { headers: { Cookie: cookie } });
  check('WHEP OPTIONS 204', opt.status === 204, opt.status);
  const post = await req('POST', `/mtx/cam_${idOf(1)}/whep`, { headers: { Cookie: cookie, 'Content-Type': 'application/sdp' }, body: 'v=0\r\n' });
  check('WHEP POST bogus SDP reaches MediaMTX (4xx)', post.status >= 400 && post.status < 500 && post.status !== 401, `${post.status} ${post.text?.slice(0, 80)}`);
  const postNo = await req('POST', `/mtx/cam_${idOf(1)}/whep`, { headers: { 'Content-Type': 'application/sdp' }, body: 'v=0\r\n' });
  check('WHEP POST without auth 401', postNo.status === 401, postNo.status);
  const master = await req('GET', `/mtx/cam_${idOf(1)}/index.m3u8`, { headers: { Cookie: cookie } });
  const mediaPl = (master.text.split('\n').find((l) => l && !l.startsWith('#')) ?? '').trim();
  const media = mediaPl ? await req('GET', `/mtx/cam_${idOf(1)}/${mediaPl}`, { headers: { Cookie: cookie } }) : { status: 0, text: '' };
  const segName = (media.text.split('\n').find((l) => l && !l.startsWith('#')) ?? '').trim();
  if (segName) {
    const sres = await req('GET', `/mtx/cam_${idOf(1)}/${segName}`, { headers: { Cookie: cookie }, raw: true });
    check('HLS media playlist + segment via /mtx 200', media.status === 200 && media.text.includes('#EXTINF') && sres.status === 200 && sres.buf.length > 1000, `${mediaPl.slice(0, 30)} -> ${segName.slice(0, 40)} ${sres.status} ${sres.buf.length} B`);
  } else check('HLS media playlist + segment via /mtx 200', false, `master=${master.status} media=${media.status} ${media.text.slice(0, 80)}`);

  // CSV import
  const csv = readFileSync(join(here, '..', 'backend', 'seeds', 'cameras_sample.csv'));
  const fd = new FormData();
  fd.append('file', new Blob([csv], { type: 'text/csv' }), 'cameras_sample.csv');
  const ci = await req('POST', '/api/cameras/import/csv', { token: A, body: fd });
  const cj = ci.data ?? {};
  const errRows = (cj.errors ?? []).map((e) => e.row);
  check('csv import added 8, errors rows 4+9 (3 entries), warning row 7', ci.status === 200 && cj.rows_total === 10 && cj.added === 8 && errRows.length === 3 && new Set(errRows).size === 2 && errRows.includes(4) && errRows.includes(9) && (cj.warnings ?? []).some((w) => w.row === 7 && w.field === 'department_code'), JSON.stringify(cj).slice(0, 400));
  const er = await req('GET', cj.error_report_url ?? '/nope', { token: A });
  check('csv error report downloadable (header + 3 lines)', er.status === 200 && er.text.trim().split('\n').length === 4 && er.text.startsWith('row,external_id,field,message'), `${er.status} lines=${er.text?.trim().split('\n').length}`);
  const erNo = await req('GET', cj.error_report_url ?? '/nope');
  check('csv error report without token 401', erNo.status === 401, erNo.status);
  const fd2 = new FormData();
  fd2.append('file', new Blob([csv], { type: 'text/csv' }), 'cameras_sample.csv');
  const ci2 = await req('POST', '/api/cameras/import/csv', { token: A, body: fd2 });
  check('csv re-import updated 8 same errors', ci2.status === 200 && ci2.data.added === 0 && ci2.data.updated === 8 && ci2.data.errors.length === 3, JSON.stringify(ci2.data).slice(0, 200));

  // Bulk API
  const bulkBody = { cameras: [{ external_id: 'GSRTC-101', name: 'Mehsana Depot Gate', department_code: 'GSRTC', lat: 23.588, lon: 72.369, district: 'Mehsana' }] };
  const b1 = await req('POST', '/api/v1/cameras/bulk', { key: BK, json: bulkBody });
  check('bulk API added 1', b1.status === 200 && (b1.data.added === 1 || b1.data.updated === 1), JSON.stringify(b1.data).slice(0, 200));
  check('bulk API internal key 403', (await req('POST', '/api/v1/cameras/bulk', { key: IK, json: bulkBody })).status === 403);
  check('bulk API no key 401', (await req('POST', '/api/v1/cameras/bulk', { json: bulkBody })).status === 401);
  const docs = await req('GET', '/api/docs');
  const oapi = await req('GET', '/api/openapi.json');
  check('/api/docs + openapi lists bulk endpoint', docs.status === 200 && oapi.status === 200 && Boolean(oapi.data?.paths?.['/api/v1/cameras/bulk']), Object.keys(oapi.data?.paths ?? {}).length + ' paths');

  // Own camera (second "system" on the wall)
  const own = await req('POST', '/api/cameras', { token: A, json: { external_id: 'OWN-GATE-01', name: 'Dynatech Office Gate (private society camera)', department_code: 'POLICE', source: 'own', ownership: 'private', type: 'ip', lat: 23.033, lon: 72.515, district: 'Ahmedabad', police_station: 'Satellite', rtsp_url: 'rtsp://mediamtx:8554/own_gate', codec: 'H264', connectivity_type: 'wifi', anpr_enabled: false, record_enabled: true } });
  check('own camera create 201', own.status === 201 || own.status === 409, `${own.status} ${JSON.stringify(own.data).slice(0, 120)}`);

  // RBAC
  const dl2 = await req('GET', '/api/cameras?page_size=200', { token: da.token });
  const nonPolice = (dl2.data?.items ?? []).filter((c) => c.department_code !== 'POLICE');
  check('dept_admin sees only POLICE cameras', dl2.status === 200 && dl2.data.total > 0 && nonPolice.length === 0, `total=${dl2.data?.total} nonPolice=${nonPolice.length}`);
  check('dept_admin GET other-dept camera 404', (await req('GET', `/api/cameras/${idOf(3)}`, { token: da.token })).status === 404);
  check('viewer POST /cameras 403', (await req('POST', '/api/cameras', { token: vw.token, json: { external_id: 'X', name: 'x' } })).status === 403);
  check('viewer POST /watchlist 403', (await req('POST', '/api/watchlist', { token: vw.token, json: { entity_type: 'vehicle', plate: 'GJ01ZZ0001', reason: 'suspect' } })).status === 403);
  check('operator POST /cameras 403', (await req('POST', '/api/cameras', { token: op.token, json: { external_id: 'X', name: 'x' } })).status === 403);
  check('viewer export 403', (await req('GET', '/api/cameras/export?format=csv', { token: vw.token })).status === 403);

  // Gap analysis + exports
  t0 = Date.now();
  const gap = await req('GET', '/api/gap-analysis', { token: A });
  const g = gap.data ?? {};
  const gandhi = (g.uncovered_pois ?? []).filter((p) => p.district === 'Gandhinagar').length;
  check('gap analysis numbers + GeoJSON', gap.status === 200 && g.summary?.zero_coverage_cells > 0 && (g.zero_coverage?.features?.length ?? 0) > 0 && gandhi >= 3 && (g.ageing ?? []).some((a) => a.name === 'Okha Checkpost') && (g.department_gaps ?? []).length > 0 && (g.recommendations ?? []).length > 0, `${Date.now() - t0} ms summary=${JSON.stringify(g.summary)} gandhinagar_uncovered=${gandhi}`);
  meas.gap_analysis_ms = Date.now() - t0;
  const gc = await req('GET', '/api/gap-analysis/export?format=csv', { token: A });
  check('gap csv export + hash', gc.status === 200 && gc.text.startsWith('section,') && gc.headers.get('x-sentinel-sha256') === sha(gc.buf), gc.headers.get('content-disposition'));
  writeFileSync(`${OUT}/gap-analysis.csv`, gc.buf);
  const gp = await req('GET', '/api/gap-analysis/export?format=pdf', { token: A, raw: true });
  check('gap pdf export', gp.status === 200 && gp.buf.subarray(0, 4).toString() === '%PDF' && gp.headers.get('x-sentinel-sha256') === sha(gp.buf), `${gp.buf.length} B`);
  writeFileSync(`${OUT}/gap-analysis.pdf`, gp.buf);
  const ce = await req('GET', '/api/cameras/export?format=csv', { token: A });
  const celines = ce.text.trim().split('\n');
  check('cameras export csv + watermark + hash', ce.status === 200 && celines[0].startsWith('external_id,name,department_code') && celines[celines.length - 1].startsWith('# Exported by jury_admin') && ce.headers.get('x-sentinel-sha256') === sha(ce.buf) && /cameras_export_\d{8}_\d{4}IST\.csv/.test(ce.headers.get('content-disposition') ?? ''), `rows=${celines.length - 2} ${ce.headers.get('content-disposition')}`);
  writeFileSync(`${OUT}/cameras_export.csv`, ce.buf);
  const dae = await req('GET', '/api/cameras/export?format=csv', { token: da.token });
  check('dept_admin export scoped', dae.status === 200 && !dae.text.includes(',HEALTH,'), dae.text.trim().split('\n').length);

  // Health poller. A catalogue camera that never delivered a stream (live=false) becomes `not_streaming`
  // after HEALTH_OFFLINE_AFTER checks - never `offline`, and never with a camera_offline alert (CONTRACT
  // amendment 2026-09-05, §4.1/§5.6). `offline` + alert is reserved for a camera that was online before.
  const hp = await pollUntil('health poller (online>=9, not_streaming>=42)', async () => {
    const r = await req('GET', '/api/health/summary', { token: A });
    const c = r.data?.cameras ?? {};
    process.stdout.write(`  health: ${JSON.stringify(c)} mediamtx=${JSON.stringify(r.data?.mediamtx)}\n`);
    return c.online >= 9 && c.not_streaming >= 42 ? r.data : null;
  }, { every: 20000, timeout: 300000 });
  check('health poller: 8 live + own online, 42 catalogue not_streaming within 5 min', Boolean(hp.value), `${hp.elapsed_s} s ${JSON.stringify(hp.value?.cameras)}`);
  meas.health_settle_s = hp.elapsed_s;
  const down5 = hp.value?.down_over_5min;
  const down5n = Array.isArray(down5) ? down5.length : Number(down5 ?? 0);
  check('never-streamed cameras are not_streaming, none offline, none down_over_5min', hp.value?.cameras?.offline === 0 && down5n === 0 && (hp.value?.not_streaming ?? []).length >= 42, `offline=${hp.value?.cameras?.offline} down_over_5min=${down5n} not_streaming[]=${hp.value?.not_streaming?.length}`);
  const allCams = (await req('GET', '/api/cameras?page_size=200', { token: A })).data?.items ?? [];
  const neverSeen = new Set(allCams.filter((c) => !c.last_seen_at).map((c) => c.id));
  const coAlerts = await req('GET', '/api/alerts?type=camera_offline&status=new,acknowledged,closed&page_size=200', { token: A });
  const bogus = (coAlerts.data?.items ?? []).filter((x) => neverSeen.has(x.camera?.id));
  check('no camera_offline alert (open or closed) for a never-online camera', coAlerts.status === 200 && bogus.length === 0, `never_seen=${neverSeen.size} camera_offline_alerts=${coAlerts.data?.total} on_never_seen=${bogus.length}`);
  const openAll = await req('GET', '/api/alerts?status=new,acknowledged&page_size=200', { token: A });
  check('no alert flood after import (open alerts < 20 before any ANPR read)', openAll.status === 200 && openAll.data.total < 20, `open=${openAll.data?.total}`);
  const cam3h = await req('GET', `/api/cameras/${idOf(3)}/health?hours=1`, { token: A });
  check('camera health log has checks (mock 3 is live -> online)', cam3h.status === 200 && cam3h.data.checks > 0 && cam3h.data.log.length > 0 && cam3h.data.status === 'online', `checks=${cam3h.data?.checks} status=${cam3h.data?.status}`);
  const cam9h = await req('GET', `/api/cameras/${idOf(9)}/health?hours=1`, { token: A });
  check('catalogue camera 9 (live=false) is not_streaming after its failed checks', cam9h.status === 200 && cam9h.data.checks >= 3 && cam9h.data.status === 'not_streaming', `checks=${cam9h.data?.checks} status=${cam9h.data?.status}`);

  // Internal worker API is unreachable through Caddy (CONTRACT §1.2), even with the internal key.
  const int1 = await req('GET', '/api/internal/anpr-config?mode=live', { key: IK });
  const int2 = await req('POST', '/api/internal/heartbeat', { key: IK, json: { worker_id: 'x', mode: 'live' } });
  check('/api/internal/* is 404 through Caddy even with the internal key', int1.status === 404 && int2.status === 404, `${int1.status},${int2.status}`);
  // dept_admin may only reach HLS of cameras in its scope (forward_auth -> /auth/verify -> 404 otherwise).
  const daOwnDept = await req('GET', `/mtx/cam_${idOf(1)}/index.m3u8`, { headers: { Cookie: `sg_session=${da.token}` } });
  const daOther = await req('GET', `/mtx/cam_${idOf(3)}/index.m3u8`, { headers: { Cookie: `sg_session=${da.token}` } });
  check('dept_admin HLS: own-department camera 200, other department 404', daOwnDept.status === 200 && daOwnDept.text.startsWith('#EXTM3U') && daOther.status === 404, `police cam_${idOf(1)}=${daOwnDept.status} health cam_${idOf(3)}=${daOther.status}`);

  // Audit
  const au = await req('GET', '/api/audit?page_size=500', { token: A });
  const acts = new Set((au.data?.items ?? []).map((i) => i.action));
  const need = ['auth.login', 'auth.login_failed', 'camera.import_sandbox', 'camera.import_csv', 'camera.import_bulk', 'camera.export', 'report.gap', 'stream.view', 'camera.create'];
  check('audit actions present', need.every((a) => acts.has(a)), `missing=${need.filter((a) => !acts.has(a)).join(',')} total=${au.data?.total}`);
  const first = au.data?.items?.find((i) => i.action === 'auth.login');
  check('audit login row has ip + user agent', Boolean(first?.ip) && Boolean(first?.user_agent), JSON.stringify({ ip: first?.ip, ua: first?.user_agent }).slice(0, 120));
  check('operator audit 403', (await req('GET', '/api/audit', { token: op.token })).status === 403);
  meas.camera_ids = Object.fromEntries(Object.entries(cams).map(([k, v]) => [k, v.id]));
  saveMeas();
}

async function phaseB() {
  const admin = await login('jury_admin', 'Sentinel@Admin2026');
  const A = admin.token;
  const op = await login('jury_operator', 'Sentinel@Ops2026');
  const vw = await login('jury_viewer', 'Sentinel@View2026');
  const cams = await camerasByExt(A);
  const idOf = (ext) => cams[String(ext)]?.id;

  const w = await pollUntil('ANPR reads', async () => {
    const hz = await req('GET', '/healthz');
    const d = await req('GET', '/api/detections?valid_only=true', { token: A });
    const s = await req('GET', '/api/sightings', { token: A });
    process.stdout.write(`  workers=${hz.data?.anpr_workers} reads=${d.data?.total} sightings=${s.data?.total}\n`);
    return d.data?.total > 0 && s.data?.total > 0 ? { reads: d.data.total, sightings: s.data.total } : null;
  }, { every: 15000, timeout: 360000 });
  check('ANPR reads + sightings appear', Boolean(w.value), `${w.elapsed_s} s ${JSON.stringify(w.value)}`);
  const d1 = (await req('GET', '/api/detections?valid_only=true', { token: A })).data;
  const item = d1.items?.[0];
  check('detection item shape (plate_display, crop_url, IST-renderable ts)', Boolean(item?.plate_display) && /^\/media\/crops\//.test(item?.crop_url ?? '') && /Z$/.test(item?.captured_at ?? ''), JSON.stringify(item).slice(0, 250));
  const crop = await req('GET', item.crop_url, { token: A, raw: true });
  check('crop jpeg 200 with sha header', crop.status === 200 && crop.buf[0] === 0xff && crop.buf[1] === 0xd8 && crop.headers.get('x-sentinel-sha256') === item.crop_sha256, `${crop.buf.length} B`);
  check('crop without token 401', (await req('GET', item.crop_url)).status === 401);
  await sleep(20000);
  const d2 = (await req('GET', '/api/detections?valid_only=true', { token: A })).data;
  check('reads grow', d2.total > d1.total, `${d1.total} -> ${d2.total}`);

  // seeded watchlist alert
  const al = await pollUntil('seeded alert GJ01AB1234', async () => {
    const r = await req('GET', '/api/alerts?status=new,acknowledged&type=watchlist_hit&page_size=100', { token: A });
    return (r.data?.items ?? []).find((a) => a.plate_norm === 'GJ01AB1234') ?? null;
  }, { every: 10000, timeout: 200000 });
  const a = al.value;
  check('seeded watchlist alert critical/exact with latency', Boolean(a) && a.priority === 'critical' && a.confidence_level === 'exact' && typeof a.latency_ms === 'number' && a.latency_ms < 5000 && Boolean(a.snapshot_url), a ? `latency=${a.latency_ms} cam=${a.camera?.name} title? ${a.notify_title ?? ''}` : 'none');

  // live watchlist add -> WS alert
  const wsUrl = `${H.replace('http', 'ws')}/ws/alerts?token=${A}`;
  const got = { alert: [], alert_update: [], other: [] };
  const ws = new WebSocket(wsUrl);
  let hello = null;
  await new Promise((resolve, reject) => {
    ws.onopen = () => {};
    ws.onerror = (e) => reject(new Error('ws error'));
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === 'hello') { hello = m; resolve(); }
      else if (m.type === 'alert') got.alert.push(m);
      else if (m.type === 'alert_update') got.alert_update.push(m);
      else got.other.push(m.type);
    };
    setTimeout(() => reject(new Error('ws hello timeout')), 10000);
  });
  check('ws hello envelope', hello?.data?.user === 'jury_admin' && hello?.data?.version === '1.0.0-phase1', JSON.stringify(hello?.data).slice(0, 150));
  const pinger = setInterval(() => { try { ws.send(JSON.stringify({ type: 'ping' })); } catch {} }, 25000);
  ws.send(JSON.stringify({ type: 'ping' }));
  await sleep(1500);
  check('ws pong', got.other.includes('pong'), got.other.join(','));
  const existing = await req('GET', '/api/watchlist?q=GJ27XY3456&is_active=all', { token: A });
  for (const wl of existing.data?.items ?? []) if (!wl.is_active) await req('PUT', `/api/watchlist/${wl.id}`, { token: A, json: { is_active: true, priority: 'high' } });
  // A re-run finds open alerts for the plate from the previous run; suppression would attach the next read to
  // them (alert_update) instead of raising a new alert, so close them first (the fresh-DB run has none).
  const openPrev = (await req('GET', '/api/alerts?status=new,acknowledged&plate=GJ27XY3456&page_size=200', { token: A })).data?.items ?? [];
  for (const a0 of openPrev) await req('POST', `/api/alerts/${a0.id}/close`, { token: op.token, json: { note: 'closed before re-test', outcome: 'other' } });
  if (openPrev.length) await sleep(2000);
  const tAdd = Date.now();
  const add = existing.data?.items?.length ? { status: 200, data: existing.data.items[0] } : await req('POST', '/api/watchlist', { token: op.token, json: { entity_type: 'vehicle', plate: 'GJ 27 XY 3456', reason: 'suspect', priority: 'high' } });
  check('watchlist add GJ 27 XY 3456 (operator)', (add.status === 201 || add.status === 200) && add.data.plate_norm === 'GJ27XY3456', `${add.status} ${JSON.stringify(add.data).slice(0, 120)}`);
  const wsAlert = await pollUntil('WS alert for GJ27XY3456', async () => got.alert.find((m) => m.data.plate_norm === 'GJ27XY3456') ?? null, { every: 5000, timeout: 200000 });
  const wa = wsAlert.value;
  check('WS alert arrives within one loop (<= 120 s) with latency <= 5 s', Boolean(wa) && (Date.now() - tAdd) <= 200000 && wa.data.latency_ms <= 5000 && wa.data.sound === true && /HIGH/.test(wa.data.notify_title ?? ''), wa ? `after ${Math.round((Date.now() - tAdd) / 1000)} s latency_ms=${wa.data.latency_ms} title="${wa.data.notify_title}" body="${wa.data.notify_body}"` : 'none');
  meas.watchlist_add_to_alert_s = wa ? Math.round((Date.now() - tAdd) / 1000) : null;
  meas.ws_alert_latency_ms = wa?.data?.latency_ms ?? null;
  const upd = await pollUntil('alert_update read_count>=2 (suppression on next loop)', async () => got.alert_update.find((m) => m.data.read_count >= 2) ?? null, { every: 10000, timeout: 150000 });
  check('alert_update via suppression (read_count >= 2)', Boolean(upd.value), upd.value ? JSON.stringify(upd.value.data).slice(0, 160) : `updates=${got.alert_update.length}`);
  clearInterval(pinger);
  ws.close();

  // ack / close / RBAC
  const fresh = (await req('GET', '/api/alerts?status=new&type=watchlist_hit&page_size=50', { token: A })).data.items;
  const target = fresh.find((x) => x.plate_norm === 'GJ27XY3456') ?? fresh[0];
  check('viewer ack 403', (await req('POST', `/api/alerts/${target.id}/ack`, { token: vw.token, json: { note: 'x' } })).status === 403);
  const ack = await req('POST', `/api/alerts/${target.id}/ack`, { token: op.token, json: { note: 'Unit dispatched' } });
  check('operator ack 200', ack.status === 200 && ack.data.status === 'acknowledged' && ack.data.acknowledged_by_username === 'jury_operator', JSON.stringify(ack.data).slice(0, 120));
  check('ack twice 409', (await req('POST', `/api/alerts/${target.id}/ack`, { token: op.token, json: {} })).status === 409);

  // vehicle search + route
  const vs = await pollUntil('GJ01AB1234 seen on >= 3 cameras', async () => {
    const r = await req('GET', '/api/vehicles/search?q=GJ 01 AB 1234', { token: A });
    process.stdout.write(`  search exact=${r.data?.exact?.length} fuzzy=${r.data?.fuzzy?.length} cameras_seen=${r.data?.cameras_seen}\n`);
    return r.data?.cameras_seen >= 3 ? r.data : null;
  }, { every: 15000, timeout: 300000 });
  const v = vs.value;
  check('vehicle search exact hits on >= 3 cameras', Boolean(v) && v.query.normalised === 'GJ01AB1234' && v.exact.length >= 3, v ? `exact=${v.exact.length} fuzzy=${v.fuzzy.length} seen=${v.cameras_seen}` : 'timeout');
  const rt = await req('GET', '/api/vehicles/GJ01AB1234/route', { token: A });
  const r = rt.data ?? {};
  const ordered = (r.sightings ?? []).every((s, i, arr) => i === 0 || arr[i - 1].first_seen <= s.first_seen);
  check('route ordered sightings + polyline + legs flagged', rt.status === 200 && (r.sightings ?? []).length >= 3 && ordered && r.polyline.length === r.sightings.length && (r.legs ?? []).length >= 2 && r.legs.filter((l) => l.distance_km > 0.5).every((l) => l.flags.includes('implausible_speed')) && r.total_distance_km > 0, `stops=${r.sightings?.length} cams=${r.cameras_count} km=${r.total_distance_km} loops=${r.loop_resets_in_window} seq=${r.sightings?.map((s) => s.camera?.external_id).join('>')}`);
  // Two systems on one route: GJ01AB1234 is one of the two plates the own-gate loop shares with sandbox cameras 1-3
  // (CONTRACT §12.4 amendment); the pre-index worker reads the own gate (Ahmedabad, ~25 km from Gandhinagar).
  const seqExt = (r.sightings ?? []).map((s) => s.camera?.external_id);
  const ownStops = seqExt.filter((e) => e === 'OWN-GATE-01').length;
  const sandboxStops = seqExt.filter((e) => /^\d+$/.test(String(e))).length;
  check('route spans two systems (own gate + sandbox cameras) with a sane total distance', ownStops >= 1 && sandboxStops >= 2 && r.total_distance_km > 1 && r.total_distance_km < 500, `own=${ownStops} sandbox=${sandboxStops} km=${r.total_distance_km} seq=${seqExt.join('>')}`);
  meas.route_two_systems = { own_stops: ownStops, sandbox_stops: sandboxStops, total_distance_km: r.total_distance_km, cameras_count: r.cameras_count };
  const conf = await req('POST', '/api/vehicles/GJ01AB1234/confirm', { token: op.token, json: { decisions: [{ sighting_id: r.sightings[0].sighting_id, decision: 'confirmed' }] } });
  check('route confirm saved', conf.status === 200 && conf.data.saved === 1, JSON.stringify(conf.data));
  const pdf = await req('GET', '/api/vehicles/GJ01AB1234/route.pdf', { token: A, raw: true });
  check('route.pdf magic + sha header + filename', pdf.status === 200 && pdf.buf.subarray(0, 4).toString() === '%PDF' && pdf.headers.get('x-sentinel-sha256') === sha(pdf.buf) && /route_GJ01AB1234_\d{8}_\d{4}IST\.pdf/.test(pdf.headers.get('content-disposition') ?? ''), `${pdf.buf.length} B ${pdf.headers.get('content-disposition')}`);
  writeFileSync(`${OUT}/route_GJ01AB1234.pdf`, pdf.buf);
  check('viewer route.pdf 403', (await req('GET', '/api/vehicles/GJ01AB1234/route.pdf', { token: vw.token })).status === 403);

  // reports
  const from = iso(new Date(Date.now() - 2 * 3600 * 1000)), to = iso(now());
  const det = await req('GET', `/api/detections?from=${from}&to=${to}&page_size=1`, { token: A });
  const csvr = await req('GET', `/api/reports/detections?from=${from}&to=${to}&format=csv`, { token: A });
  const lines = csvr.text.trim().split('\n');
  check('detections csv rows == /detections total, IST first column, trailer, hash', csvr.status === 200 && lines[0].startsWith('captured_at_ist,captured_at_utc,camera_id') && lines[lines.length - 1].startsWith('# Sentinel Gujarat 1.0.0-phase1 | rows=') && Math.abs((lines.length - 2) - det.data.total) <= 3 && /^"?\d{2} \w{3} \d{4}, \d{2}:\d{2}:\d{2} IST"?,/.test(lines[1]) && csvr.headers.get('x-sentinel-sha256') === sha(csvr.buf), `rows=${lines.length - 2} total=${det.data.total} first=${lines[1]?.slice(0, 60)}`);
  writeFileSync(`${OUT}/detections.csv`, csvr.buf);
  const pdfr = await req('GET', `/api/reports/detections?from=${from}&to=${to}&format=pdf`, { token: A, raw: true });
  check('detections pdf', pdfr.status === 200 && pdfr.buf.subarray(0, 4).toString() === '%PDF' && pdfr.headers.get('x-sentinel-sha256') === sha(pdfr.buf), `${pdfr.buf.length} B`);
  writeFileSync(`${OUT}/detections.pdf`, pdfr.buf);
  const q = await req('GET', `/api/reports/quality?from=${from}&to=${to}`, { token: A });
  check('quality json', q.status === 200 && q.data.reads_total > 0 && q.data.reads_per_camera.length > 0, `reads=${q.data?.reads_total} valid=${q.data?.valid_format_pct}% conf=${q.data?.mean_confidence}`);
  meas.quality = { reads_total: q.data?.reads_total, valid_format_pct: q.data?.valid_format_pct, mean_confidence: q.data?.mean_confidence, sightings_total: q.data?.sightings_total, unique_plates: q.data?.unique_plates };
  const qpdf = await req('GET', `/api/reports/quality?from=${from}&to=${to}&format=pdf`, { token: A, raw: true });
  check('quality pdf', qpdf.status === 200 && qpdf.buf.subarray(0, 4).toString() === '%PDF', `${qpdf.buf.length} B`);
  writeFileSync(`${OUT}/quality.pdf`, qpdf.buf);
  const hist = await req('GET', '/api/reports/history', { token: A });
  check('reports history with hashes', hist.status === 200 && hist.data.total >= 3 && hist.data.items[0].sha256, hist.data?.total);
  // The mock loop is seamless (ffmpeg -stream_loop keeps PTS monotonic), so force a discontinuity: kick the
  // worker's RTSP reader session on cam_3 -> decoder restart with backoff -> sightings closed + loop_reset event.
  const evBefore = (await req('GET', '/api/events?type=loop_reset', { token: A })).data?.total ?? 0;
  const sess = await fetch(`${MTX}/v3/rtspsessions/list?itemsPerPage=1000`).then((x) => x.json());
  const reader = (sess.items ?? []).find((s) => s.path === `cam_${idOf(3)}` && s.state === 'read');
  const kick = reader ? await fetch(`${MTX}/v3/rtspsessions/kick/${reader.id}`, { method: 'POST' }) : null;
  const tKick = Date.now();
  const ev = await pollUntil('loop_reset event after reader kick', async () => {
    const e = await req('GET', '/api/events?type=loop_reset', { token: A });
    return (e.data?.total ?? 0) > evBefore ? e.data : null;
  }, { every: 5000, timeout: 90000 });
  // The event arrives within seconds; the `decoder_restarts` counter travels with the next heartbeat (HEARTBEAT_S,
  // 15 s), so poll the health summary for it instead of reading it once.
  const restartedPoll = await pollUntil('decoder_restarts >= 1 in the next heartbeat', async () => {
    const hb = (await req('GET', '/api/health/summary', { token: A })).data?.anpr_workers?.find((x) => x.mode === 'live');
    const cs = (hb?.camera_states ?? []).find((c) => c.id === idOf(3));
    return (cs?.decoder_restarts ?? 0) >= 1 ? cs : null;
  }, { every: 5000, timeout: 45000 });
  const restarted = restartedPoll.value;
  check('discontinuity: kicked reader -> decoder restart + loop_reset event', Boolean(kick) && kick.status === 200 && Boolean(ev.value) && (ev.value.items[0].camera?.id === idOf(3)) && (restarted?.decoder_restarts ?? 0) >= 1, `kick=${kick?.status} after ${Math.round((Date.now() - tKick) / 1000)} s events=${ev.value?.total} note="${ev.value?.items?.[0]?.note}" restarts=${restarted?.decoder_restarts} state=${restarted?.state}`);
  meas.reconnect_after_kick_s = ev.value ? Math.round((Date.now() - tKick) / 1000) : null;

  // recordings + clip + verify
  const rec = await req('GET', `/api/recordings/${idOf(1)}`, { token: A });
  check('recordings segments for camera 1', rec.status === 200 && rec.data.segments.length >= 1 && rec.data.record_enabled, `segments=${rec.data?.segments?.length} path=${rec.data?.playback_path}`);
  if (rec.data?.segments?.length) {
    const segs = rec.data.segments;
    const seg0 = segs.length >= 2 ? segs[segs.length - 2] : segs[0]; // a completed segment (the last one is still being written)
    const clipStart = iso(new Date(new Date(seg0.start).getTime() + 5000));
    const clipEdge = await req('POST', '/api/clips', { token: op.token, json: { camera_id: idOf(1), start_at: seg0.start, duration_s: 10 } });
    check('clip at exact segment start accepted (ms/us tolerance)', clipEdge.status === 201, `${clipEdge.status} ${JSON.stringify(clipEdge.data).slice(0, 120)}`);
    const clip = await req('POST', '/api/clips', { token: op.token, json: { camera_id: idOf(1), start_at: clipStart, duration_s: 20 } });
    check('clip 201 with sha256 + size', clip.status === 201 && clip.data.sha256 && clip.data.size_bytes > 0, JSON.stringify(clip.data).slice(0, 200));
    if (clip.status === 201) {
      const ver = await req('GET', `/api/evidence/verify?path=${encodeURIComponent(clip.data.path)}`, { token: A });
      check('evidence verify clip match', ver.status === 200 && ver.data.match === true && ver.data.entity === 'clip', JSON.stringify(ver.data).slice(0, 160));
      const range = await req('GET', clip.data.url, { token: A, headers: { Range: 'bytes=0-99' }, raw: true });
      check('clip range request 206', range.status === 206 && range.buf.length === 100 && Boolean(range.headers.get('content-range')), range.status);
      const play = await req('GET', `/api/recordings/${idOf(1)}/play?at=${encodeURIComponent(item.captured_at)}`, { token: A });
      check('recordings play resolves url', play.status === 200 && play.data.available === true && /^\/playback\/get\?/.test(play.data.url ?? ''), JSON.stringify(play.data).slice(0, 160));
      if (play.data?.url) {
        const pb = await req('GET', play.data.url, { headers: { Cookie: `sg_session=${A}` }, raw: true });
        check('playback mp4 through /playback (cookie)', pb.status === 200 && pb.buf.length > 1000 && /video\/mp4/.test(pb.headers.get('content-type') ?? ''), `${pb.status} ${pb.buf.length} B ${pb.headers.get('content-type')}`);
      }
    }
  }
  const verEv = await req('GET', `/api/evidence/verify?path=${encodeURIComponent(item.crop_url.replace('/media/', ''))}`, { token: A });
  check('evidence verify crop match', verEv.status === 200 && verEv.data.match === true && verEv.data.entity === 'plate_read', JSON.stringify(verEv.data).slice(0, 160));
  // corrupt the file inside the volume -> verify must report match=false (check 23)
  const cropRel = item.crop_url.replace('/media/', '');
  try {
    execSync(`docker exec sentinel-api sh -c "echo x >> /data/${cropRel}"`, { stdio: 'ignore' });
    const verBad = await req('GET', `/api/evidence/verify?path=${encodeURIComponent(cropRel)}`, { token: A });
    check('evidence verify corrupted file -> match=false', verBad.status === 200 && verBad.data.match === false && verBad.data.stored_sha256 !== verBad.data.computed_sha256, JSON.stringify(verBad.data).slice(0, 160));
  } catch (e) {
    check('evidence verify corrupted file -> match=false', false, String(e));
  }
  check('evidence traversal 422', (await req('GET', '/api/evidence/verify?path=../etc/passwd', { token: A })).status === 422);

  // dashboard / object counts / health workers
  const ds = await req('GET', '/api/dashboard/stats', { token: A });
  check('dashboard stats', ds.status === 200 && ds.data.reads.total > 0 && ds.data.alerts.last_24h > 0 && ds.data.anpr_workers.length >= 1, `reads=${JSON.stringify(ds.data?.reads)} alerts=${JSON.stringify(ds.data?.alerts)} workers=${ds.data?.anpr_workers?.map((x) => `${x.id}:${x.cameras}cams:${x.fps_total}fps`).join(',')}`);
  meas.dashboard = { reads: ds.data?.reads, alerts: ds.data?.alerts, object_counts_24h: ds.data?.object_counts_24h, anpr_workers: ds.data?.anpr_workers };
  const ch = await req('GET', '/api/dashboard/charts', { token: A });
  check('dashboard charts IST labels', ch.status === 200 && ch.data.vehicles_per_hour.length > 0 && ch.data.top_plates.length > 0 && /\d{2} \w{3} \d{2}:\d{2}/.test(ch.data.vehicles_per_hour[0].label_ist), `top=${ch.data?.top_plates?.slice(0, 3).map((p) => p.plate_display).join('|')} label=${ch.data?.vehicles_per_hour?.[0]?.label_ist}`);
  const oc = await req('GET', '/api/object-counts', { token: A });
  const hs = await req('GET', '/api/health/summary', { token: A });
  const wk = hs.data?.anpr_workers?.find((x) => x.mode === 'live');
  // Object detection is off in the laptop demo profile (OBJECT_DETECT=0): the worker item then reports no object
  // weights (`extra.object_weights == null`) and the synthetic drawings hold no COCO objects anyway (CONTRACT §1.3 amendment).
  const objOff = wk?.object_detect === false || (wk?.extra != null && wk.extra.object_weights == null);
  if (objOff) check('object counts endpoint (object detection off on this worker: synthetic drawings hold no COCO objects)', oc.status === 200 && oc.data && 'totals' in oc.data, `object_weights=${wk?.extra?.object_weights} totals=${JSON.stringify(oc.data?.totals)}`);
  else check('object counts', oc.status === 200 && Object.keys(oc.data.totals ?? {}).length > 0, JSON.stringify(oc.data?.totals));
  check('health summary anpr worker', Boolean(wk) && wk.stale === false && wk.cameras >= 4, JSON.stringify(wk));
  const allAlerts = (await req('GET', '/api/alerts?status=new,acknowledged,closed&type=watchlist_hit&page_size=200', { token: A })).data.items;
  const lat = allAlerts.map((x) => x.latency_ms).filter((x) => typeof x === 'number').sort((x, y) => x - y);
  const p = (q) => lat[Math.min(lat.length - 1, Math.floor(q * lat.length))];
  meas.alert_latency = { n: lat.length, p50_ms: p(0.5), p95_ms: p(0.95), max_ms: lat[lat.length - 1] };
  check('alert latency p95 < 5000 ms', lat.length > 0 && p(0.95) < 5000, JSON.stringify(meas.alert_latency));

  // audit
  const au = await req('GET', '/api/audit?page_size=500', { token: A });
  const acts = new Set((au.data?.items ?? []).map((i) => i.action));
  const need = ['auth.login', 'camera.import_sandbox', 'vehicle.search', 'vehicle.route', 'vehicle.confirm', 'report.route', 'report.detections', 'report.quality', 'camera.export', 'alert.ack', 'watchlist.create', 'clip.create', 'evidence.verify'];
  check('audit covers login/import/search/route/export/ack', need.every((x) => acts.has(x)), `missing=${need.filter((x) => !acts.has(x)).join(',')}`);

  // check 24: settings + catalogue test
  const putBad = await req('PUT', '/api/settings', { token: A, json: { values: { 'catalogue.base_url': 'http://api:8000/nowhere', 'catalogue.auth_password': '********' } } });
  const test = await req('POST', '/api/settings/catalogue/test', { token: A, json: {} });
  const impBad = await req('POST', '/api/cameras/import/sandbox', { token: A, json: {} });
  const putGood = await req('PUT', '/api/settings', { token: A, json: { values: { 'catalogue.base_url': 'http://api:8000/mock-sandbox' } } });
  const test2 = await req('POST', '/api/settings/catalogue/test', { token: A, json: {} });
  const impGood = await req('POST', '/api/cameras/import/sandbox', { token: A, json: {} });
  const sget = await req('GET', '/api/settings', { token: A });
  const pw = (sget.data?.items ?? []).find((i) => i.key === 'catalogue.auth_password');
  check('settings: bad catalogue URL -> test ok:false, import 502 upstream_error; restore works; password masked', putBad.status === 200 && test.data?.ok === false && impBad.status === 502 && impBad.data?.code === 'upstream_error' && /nowhere/.test(impBad.data?.detail ?? '') && putGood.status === 200 && test2.data?.ok === true && test2.data?.count === 50 && impGood.status === 200 && impGood.data?.unchanged === 50 && (pw?.value === '********' || pw?.value === ''), `test=${JSON.stringify(test.data).slice(0, 80)} import=${impBad.status} ${impBad.data?.detail?.slice(0, 60)} restored=${impGood.data?.unchanged} pw=${JSON.stringify(pw?.value)}`);
  check('settings unknown key 422', (await req('PUT', '/api/settings', { token: A, json: { values: { 'nope.key': 1 } } })).status === 422);
  check('viewer GET /settings 403', (await req('GET', '/api/settings', { token: vw.token })).status === 403);

  // check 25: webhook into the mock sink, signed
  // The open sink lives at the API root only (compose network); /api/mock-sandbox/webhook-sink needs the internal key (amendment §5.22).
  const wh = await req('POST', '/api/webhooks', { token: A, json: { name: 'integration-sink', url: 'http://api:8000/mock-sandbox/webhook-sink', secret: 's3cret-integration', event_types: ['alert.created'] } });
  check('webhook create (secret masked)', wh.status === 201 && wh.data.secret === '********', `${wh.status} ${JSON.stringify(wh.data).slice(0, 120)}`);
  const whTest = await req('POST', `/api/webhooks/${wh.data?.id}/test`, { token: A, json: {} });
  check('webhook test delivery', whTest.status === 200 && (whTest.data.status === 204 || whTest.data.status === 200), JSON.stringify(whTest.data));
  const sinkHit = await pollUntil('alert.created delivered to the mock sink', async () => {
    const sink = await req('GET', '/api/mock-sandbox/webhook-sink', { token: A });
    const hit = (sink.data?.deliveries ?? []).find((d) => d.headers?.['x-sentinel-event'] === 'alert.created' && /^sha256=[0-9a-f]{64}$/.test(d.headers?.['x-sentinel-signature'] ?? ''));
    return hit ?? null;
  }, { every: 5000, timeout: 120000 });
  const whList = await req('GET', '/api/webhooks', { token: A });
  const whRow = (whList.data?.items ?? []).find((w) => w.id === wh.data?.id);
  check('webhook alert.created in sink with valid signature + last_status 2xx', Boolean(sinkHit.value) && sinkHit.value.body?.event === 'alert.created' && Boolean(sinkHit.value.body?.data?.plate_norm) && whRow && whRow.last_status >= 200 && whRow.last_status < 300, sinkHit.value ? `after ${sinkHit.elapsed_s} s plate=${sinkHit.value.body?.data?.plate_norm} last_status=${whRow?.last_status}` : 'no delivery');
  if (wh.data?.id) check('webhook delete', (await req('DELETE', `/api/webhooks/${wh.data.id}`, { token: A })).status === 204);

  // dashboard has no alert flood: open alerts stay bounded (suppression + re-alert window) and no camera_offline alert
  // exists for a camera that never streamed.
  const openB = await req('GET', '/api/alerts?status=new,acknowledged&page_size=200', { token: A });
  const openOffline = (openB.data?.items ?? []).filter((x) => x.type === 'camera_offline').length;
  const camsB = (await req('GET', '/api/cameras?page_size=200', { token: A })).data?.items ?? [];
  const neverSeenB = new Set(camsB.filter((c) => !c.last_seen_at).map((c) => c.id));
  const coB = (await req('GET', '/api/alerts?type=camera_offline&status=new,acknowledged,closed&page_size=200', { token: A })).data?.items ?? [];
  check('no alert flood: open alerts bounded, no open camera_offline, none ever for never-online cameras', openB.status === 200 && openB.data.total <= 60 && openOffline === 0 && coB.filter((x) => neverSeenB.has(x.camera?.id)).length === 0, `open=${openB.data?.total} open_camera_offline=${openOffline} camera_offline_total=${coB.length} never_seen=${neverSeenB.size} not_streaming=${camsB.filter((c) => c.status === 'not_streaming').length}`);
  meas.open_alerts_after_run = openB.data?.total ?? null;

  // rate limit last. A throw-away username: five failures lock a username for 15 min (amendment §2.1), and the
  // per-IP window (10/min) is exhausted for the next minute - the jury accounts must stay usable afterwards.
  const codes = [];
  for (let i = 0; i < 11; i++) codes.push((await req('POST', '/api/auth/login', { json: { username: 'ratelimit_probe', password: 'wrong' } })).status);
  check('login rate limit: 401 first, 429 by the 11th attempt (username lock after 5 failures, IP window 10/min)', codes[0] === 401 && codes[5] === 429 && codes[codes.length - 1] === 429, codes.join(','));
  saveMeas();
}

try {
  if (phase === 'a') await phaseA(); else await phaseB();
} catch (e) {
  check(`phase ${phase} crashed`, false, e.stack ?? String(e));
}
const failed = results.filter((r) => !r[1]);
console.log(`\n${results.length - failed.length} passed, ${failed.length} failed${failed.length ? ': ' + failed.map((f) => f[0]).join(' | ') : ''}`);
process.exit(failed.length ? 1 : 0);
