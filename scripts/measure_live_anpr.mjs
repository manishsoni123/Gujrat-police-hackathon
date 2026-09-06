#!/usr/bin/env node
// Measure the live ANPR worker over a wall-clock window on the running compose stack (accuracy pass,
// docs/anpr-accuracy.md section 4). Run from the repository root with Node >= 20:
//
//   node scripts/measure_live_anpr.mjs <fromISO> <toISO> [outDir=media/anpr_measure] [nCrops=20]
//        [--footage "14-06-2026 02:11 (cam01 clock at 21:55 IST)"] [--stats <docker-stats sample file>]
//
// Sources (nothing is inferred):
//   * GET /api/detections?mode=live and GET /api/sightings (jury login from SENTINEL_USER / SENTINEL_PASSWORD,
//     default jury_admin / the documented demo password) - reads posted by the LIVE worker for the eight live
//     organiser cameras (ids in LIVE); the pre-index worker's synthetic own-gate loop (camera 60) is excluded;
//   * docker logs sentinel-anpr-live - vehicles / static-text tracks, per-camera counters (delta of the first
//     and last "stats:" line inside the window), OCR and detect cost inside the window (delta of the cumulative
//     figures, so a stall before the window does not skew the mean), main-loop stalls (gaps > 45 s between
//     stats lines), decoder restarts / errors, API timeouts;
//   * media/anpr_evidence/<camera>/<date>/vehicles.jsonl - detected-vehicle evidence whose best shot lies in
//     the window;
//   * an optional docker-stats sample file (blocks of "== HH:MM:SS" + "<container> <cpu%> <mem> / <cap>" +
//     MemAvailable/SwapFree lines) - min/max resources inside the window.
// It also downloads up to nCrops random VALID crops and up to nCrops INVALID crops (crop_url) into outDir for
// the by-eye spot check, and writes outDir/summary.json. Nothing is written to the API.
import fs from 'node:fs';
import path from 'node:path';
import { execSync } from 'node:child_process';

const args = process.argv.slice(2);
const opts = {};
const positional = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--footage') opts.footage = args[++i];
  else if (args[i] === '--stats') opts.stats = args[++i];
  else positional.push(args[i]);
}
const [from, to, outDir = 'media/anpr_measure', nCropsArg = '20'] = positional;
if (!from || !to) {
  console.error('usage: node scripts/measure_live_anpr.mjs <fromISO> <toISO> [outDir] [nCrops] [--footage note] [--stats file]');
  process.exit(2);
}
const nCrops = Number(nCropsArg);
const API = process.env.SENTINEL_API || 'http://localhost';
const USER = process.env.SENTINEL_USER || 'jury_admin';
const PASSWORD = process.env.SENTINEL_PASSWORD || 'Sentinel@Admin2026';
const LIVE = new Set((process.env.LIVE_CAMERAS || '61,62,64,65,67,72,74,76').split(',').map(Number));
const fromMs = Date.parse(from), toMs = Date.parse(to);

const login = await fetch(`${API}/api/auth/login`, {
  method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: USER, password: PASSWORD }),
});
if (!login.ok) { console.error('login failed', login.status); process.exit(1); }
const H = { authorization: `Bearer ${(await login.json()).access_token}` };

async function list(endpoint, extra = {}) {
  const items = [];
  for (let page = 1; page < 100; page++) {
    const q = new URLSearchParams({ from, to, page: String(page), page_size: '500', ...extra });
    const r = await fetch(`${API}/api/${endpoint}?${q}`, { headers: H });
    if (!r.ok) { console.error(endpoint, r.status, (await r.text()).slice(0, 200)); break; }
    const j = await r.json();
    items.push(...j.items);
    if (items.length >= j.total || !j.items.length) break;
  }
  return items;
}
const mean = a => a.length ? +(a.reduce((x, y) => x + y, 0) / a.length).toFixed(3) : null;

// ---- API reads -------------------------------------------------------------------------------------
const allLiveMode = await list('detections', { mode: 'live' });
const reads = allLiveMode.filter(r => LIVE.has(r.camera?.id));
const sightings = (await list('sightings')).filter(s => LIVE.has(s.camera?.id) && (s.mode ?? 'live') === 'live');
const valid = reads.filter(r => r.is_valid_format);
const byCam = {};
for (const r of reads) {
  const k = `${r.camera?.id} ${r.camera?.external_id || ''}`;
  byCam[k] ??= { reads: 0, valid: 0, plates: new Set(), conf: [], invalid: [] };
  byCam[k].reads++; byCam[k].conf.push(r.confidence);
  if (r.is_valid_format) { byCam[k].valid++; byCam[k].plates.add(r.plate_norm); }
  else if (byCam[k].invalid.length < 8) byCam[k].invalid.push(`${r.plate_raw}@${r.confidence}`);
}
const summary = {
  window: { from, to, minutes: +((toMs - fromMs) / 60000).toFixed(1) }, footage_clock: opts.footage || null,
  api: {
    reads_live_cameras: reads.length, reads_all_live_mode: allLiveMode.length,
    valid: valid.length, valid_pct: reads.length ? +(100 * valid.length / reads.length).toFixed(1) : null,
    unique_valid_plates: new Set(valid.map(r => r.plate_norm)).size, unique_all: new Set(reads.map(r => r.plate_norm)).size,
    mean_conf_all: mean(reads.map(r => r.confidence)), mean_conf_valid: mean(valid.map(r => r.confidence)),
    sightings: sightings.length, sightings_valid: sightings.filter(s => s.is_valid_format).length,
    per_camera: Object.fromEntries(Object.entries(byCam).map(([k, v]) => [k, {
      reads: v.reads, valid: v.valid, valid_plates: [...v.plates].sort(), mean_conf: mean(v.conf), invalid_samples: v.invalid }])),
    valid_plates: [...new Set(valid.map(r => r.plate_norm))].sort(),
  },
};

// ---- worker log ---------------------------------------------------------------------------------------
let lines = [];
try {
  const raw = execSync(`docker logs --since ${from} --until ${to} sentinel-anpr-live 2>&1`, { maxBuffer: 1 << 28 }).toString();
  lines = raw.split('\n').map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
} catch (e) { console.error('docker logs unavailable:', String(e.message).slice(0, 160)); }
const msgs = lines.map(l => l.msg);
const count = re => msgs.filter(m => re.test(m)).length;
const statsRe = /frames=(\d+) \(([\d.]+)\/s\) detect=([\d.]+) ms\/frame ocr=(\S+) ([\d.]+) ms\/call \((\d+)\)/;
const stats = lines.filter(l => l.msg.startsWith('stats:')).map(l => {
  const m = l.msg.match(statsRe);
  const cams = l.msg.match(/cameras=(\{.*\})$/)?.[1];
  let cameras = null; try { cameras = cams ? JSON.parse(cams) : null; } catch {}
  return m && { ts: l.ts, frames: +m[1], detect_ms: +m[3], ocr: m[4], ocr_ms: +m[5], ocr_calls: +m[6],
    detect_cum: +m[3] * +m[1], ocr_cum: +m[5] * +m[6], cameras };
}).filter(Boolean);
const first = stats[0], last = stats.at(-1);
const gaps = [];
for (let i = 1; i < stats.length; i++) {
  const g = (Date.parse(stats[i].ts) - Date.parse(stats[i - 1].ts)) / 1000;
  if (g > 45) gaps.push({ at: stats[i].ts, seconds: +g.toFixed(0) });
}
const vehicles = msgs.filter(m => /: vehicle t\d+/.test(m));
const textTracks = msgs.filter(m => /: static text t\d+/.test(m));
const vehByCam = {};
for (const m of vehicles) {
  const cam = m.match(/camera (\d+):/)[1]; const w = +m.match(/w=(\d+)px/)[1]; const plate = m.match(/plate=(\S+)/)?.[1];
  const frames = +(m.match(/frames=(\d+)/)?.[1] || 1);
  vehByCam[cam] ??= { vehicles: 0, ge60: 0, with_plate: 0, multi_frame: 0, widths: [] };
  const v = vehByCam[cam];
  v.vehicles++; if (w >= 60) v.ge60++; if (plate && plate !== '-') v.with_plate++; if (frames > 1) v.multi_frame++; v.widths.push(w);
}
for (const v of Object.values(vehByCam)) {
  v.widths.sort((a, b) => a - b); v.median_w = v.widths[v.widths.length >> 1]; v.max_w = v.widths.at(-1); delete v.widths;
}
const delta = (a, b, key) => (b?.[key] ?? 0) - (a?.[key] ?? 0);
const perCamera = {};
if (first?.cameras && last?.cameras) {
  for (const [cid, endc] of Object.entries(last.cameras)) {
    const startc = first.cameras[cid] || {};
    perCamera[cid] = {
      state_end: endc.state, fps_end: endc.fps, processed: delta(startc, endc, 'processed'), reads: delta(startc, endc, 'reads'),
      restarts: delta(startc, endc, 'restarts'),
      vehicles: delta(startc.vehicles, endc.vehicles, 'detected'), vehicles_ocr: delta(startc.vehicles, endc.vehicles, 'ocr'),
      vehicles_read: delta(startc.vehicles, endc.vehicles, 'read'), text_tracks: delta(startc.vehicles, endc.vehicles, 'text'),
      ocr_calls: delta(startc.vehicles, endc.vehicles, 'ocr_calls'), evidence_files: delta(startc.vehicles, endc.vehicles, 'evidence_files'),
      suppressed: Object.fromEntries(Object.keys(endc.suppressed || {}).filter(k => k !== 'osd_regions_now')
        .map(k => [k, delta(startc.suppressed, endc.suppressed, k)])),
      osd_regions_now: endc.suppressed?.osd_regions_now,
    };
  }
}
summary.worker = {
  log_lines: lines.length, stats_lines: stats.length, first_stats: first?.ts, last_stats: last?.ts,
  frames_in_window: delta(first, last, 'frames'),
  frames_per_s: first && last ? +(delta(first, last, 'frames') / ((Date.parse(last.ts) - Date.parse(first.ts)) / 1000)).toFixed(2) : null,
  detect_ms_per_frame: first && last && delta(first, last, 'frames') ? +(delta(first, last, 'detect_cum') / delta(first, last, 'frames')).toFixed(1) : null,
  ocr_backend: last?.ocr, ocr_calls: delta(first, last, 'ocr_calls'),
  ocr_ms_per_call: first && last && delta(first, last, 'ocr_calls') ? +(delta(first, last, 'ocr_cum') / delta(first, last, 'ocr_calls')).toFixed(1) : null,
  main_loop_gaps_over_45s: gaps,
  vehicles_logged: vehicles.length, vehicles_ge60: Object.values(vehByCam).reduce((a, v) => a + v.ge60, 0),
  vehicles_by_camera: vehByCam, static_text_tracks: textTracks.length,
  reads_logged: count(/: read /), decoder_kills: count(/killing decoder/), decoder_errors: count(/decoder error/),
  reconnects: count(/reconnect in/), api_timeouts: count(/Timeout|timed out|Connection aborted/i),
  exceptions: count(/frame processing failed|Traceback/), discontinuities: count(/discontinuity/),
  per_camera: perCamera,
  cameras_running_at_end: last?.cameras ? Object.values(last.cameras).filter(c => c.state === 'running').length : null,
};

// ---- evidence store -------------------------------------------------------------------------------
const evidence = { files_in_window: 0, ge60: 0, with_valid_plate: 0, by_camera: {} };
const evRoot = path.resolve('media/anpr_evidence');
if (fs.existsSync(evRoot)) {
  for (const cam of fs.readdirSync(evRoot)) {
    const camDir = path.join(evRoot, cam);
    if (!fs.statSync(camDir).isDirectory()) continue;
    for (const day of fs.readdirSync(camDir)) {
      const f = path.join(camDir, day, 'vehicles.jsonl');
      if (!fs.existsSync(f)) continue;
      for (const line of fs.readFileSync(f, 'utf8').split('\n')) {
        if (!line.trim()) continue;
        let rec; try { rec = JSON.parse(line); } catch { continue; }
        const t = Date.parse(rec.best_at);
        if (!(t >= fromMs && t < toMs)) continue;
        evidence.files_in_window++;
        evidence.by_camera[cam] = (evidence.by_camera[cam] || 0) + 1;
        if (rec.width_px >= 60) evidence.ge60++;
        if (rec.is_valid_format) evidence.with_valid_plate++;
      }
    }
  }
}
summary.evidence = evidence;

// ---- resources ------------------------------------------------------------------------------------
if (opts.stats && fs.existsSync(opts.stats)) {
  const res = {};
  const add = (k, v) => { (res[k] ??= []).push(v); };
  let inWin = false;
  const day = from.slice(0, 10);
  for (const line of fs.readFileSync(opts.stats, 'utf8').split('\n')) {
    const h = line.match(/^== (\d\d:\d\d:\d\d)/);
    if (h) { const t = Date.parse(`${day}T${h[1]}Z`); inWin = t >= fromMs && t < toMs; continue; }
    if (!inWin) continue;
    const c = line.match(/^(sentinel-\S+) ([\d.]+)% ([\d.]+)(MiB|GiB) \//);
    if (c) { add(`${c[1]} cpu%`, +c[2]); add(`${c[1]} MiB`, +(c[4] === 'GiB' ? c[3] * 1024 : c[3]).toFixed(0)); continue; }
    const m = line.match(/^(MemAvailable|SwapFree):\s+(\d+) kB/);
    if (m) add(`vm ${m[1]} MiB`, Math.round(m[2] / 1024));
    const p = line.match(/^(pswpin|pswpout) (\d+)/);
    if (p) add(`vm ${p[1]}`, +p[2]);
  }
  summary.resources = Object.fromEntries(Object.entries(res).map(([k, v]) => [k, { min: Math.min(...v), max: Math.max(...v), samples: v.length }]));
}

// ---- crops for the spot check -----------------------------------------------------------------------
fs.mkdirSync(outDir, { recursive: true });
async function fetchCrops(items, tag) {
  const picked = [];
  for (const r of [...items].sort(() => Math.random() - 0.5).slice(0, nCrops)) {
    const res = await fetch(`${API}${r.crop_url}`, { headers: H });
    if (!res.ok) { picked.push({ id: r.id, err: res.status }); continue; }
    const name = `${tag}_${r.camera?.external_id}_${r.id}_${r.plate_norm}_${Math.round(r.confidence * 100)}.jpg`;
    fs.writeFileSync(path.join(outDir, name), Buffer.from(await res.arrayBuffer()));
    picked.push({ id: r.id, camera: r.camera?.external_id, plate_norm: r.plate_norm, plate_raw: r.plate_raw, confidence: r.confidence,
      bbox: r.bbox, captured_at: r.captured_at, votes: r.votes ?? r.vote_count ?? null, file: name });
  }
  return picked;
}
summary.crops = { valid: await fetchCrops(valid, 'valid'), invalid: await fetchCrops(reads.filter(r => !r.is_valid_format), 'invalid') };
fs.writeFileSync(path.join(outDir, 'summary.json'), JSON.stringify(summary, null, 2));
console.log(JSON.stringify(summary, null, 1));
