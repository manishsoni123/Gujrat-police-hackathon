#!/usr/bin/env node
// Sentinel Gujarat - government-feed evidence report (detected vehicles + plates read) for a wall-clock window.
//
// The portal asks the output report to show "detected vehicles OR number plates with corresponding timestamps".
// The API's own output report (GET /reports/detections, CSV + PDF) lists every OCR read (valid and invalid
// format) with crop hashes. This companion report adds the detected vehicles the worker could not read at
// all (plate boxes below the OCR width floor, blown-out night plates) from the worker's evidence store
// (media/anpr_evidence/<camera>/<date>/vehicles.jsonl + best-shot crops, CONTRACT.md section 7 amendment
// "Detected-vehicle evidence") and prints both side by side, per organiser camera, with IST timestamps,
// camera ids and SHA-256 of every crop it embeds. Nothing is inferred: every row comes from the API or the
// evidence store, and every crop is the file the worker wrote.
//
// Usage (repository root, Node >= 20, running stack):
//   node docs/tools/build-evidence-report.mjs --from 2026-09-05T15:00:00Z --to 2026-09-05T17:00:00Z
//        [--out docs/export] [--api http://localhost] [--note "feed availability note"]
//   env SG_USER / SG_PASS override the jury_admin login.
// Writes <out>/evidence_<from>_<to>_IST.pdf, .csv (vehicle evidence rows, one per detected vehicle) and .json
// (the counts) and prints the counts. Exit 1 on any error.

import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadPlaywright, launchChromium, countPdfPages } from './export-pdf.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '..', '..');

const argv = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = argv.indexOf(name);
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : dflt;
};
const API = opt('--api', process.env.SENTINEL_API || 'http://localhost');
const OUT = resolve(repoRoot, opt('--out', 'docs/export'));
const NOTE = opt('--note', '');
const USER = process.env.SG_USER || 'jury_admin';
const PASS = process.env.SG_PASS || 'Sentinel@Admin2026';
const from = opt('--from', null);
const to = opt('--to', null);
if (!from || !to) {
  console.error('usage: build-evidence-report.mjs --from <ISO> --to <ISO> [--out dir] [--api url] [--note text]');
  process.exit(2);
}
const tFrom = new Date(from);
const tTo = new Date(to);
if (Number.isNaN(tFrom) || Number.isNaN(tTo) || tTo <= tFrom) {
  console.error('bad window');
  process.exit(2);
}

const IST = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const fmtIst = (d) => `${IST.format(new Date(d)).replace(',', '')} IST`;
const stamp = (d) => {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(new Date(d)).map((x) => [x.type, x.value]));
  return `${p.year}${p.month}${p.day}_${p.hour}${p.minute}`;
};
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const sha256 = (buf) => createHash('sha256').update(buf).digest('hex');
const displayPlate = (p) => {
  const m = /^([A-Z]{2})(\d{1,2})([A-Z]{1,3})(\d{4})$/.exec(p || '');
  return m ? `${m[1]} ${m[2]} ${m[3]} ${m[4]}` : p || '';
};

async function api(path, token) {
  const r = await fetch(`${API}/api${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!r.ok) throw new Error(`${path}: ${r.status} ${(await r.text()).slice(0, 200)}`);
  return r;
}

async function main() {
  const login = await fetch(`${API}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: USER, password: PASS }) });
  if (!login.ok) throw new Error(`login ${login.status}`);
  const token = (await login.json()).access_token;

  // Organiser cameras (source=sandbox) - the government feed; the own gate and mock rows are excluded.
  const cams = new Map();
  for (let page = 1; ; page++) {
    const j = await (await api(`/cameras?source=sandbox&page=${page}&page_size=200`, token)).json();
    for (const c of j.items) cams.set(c.id, c);
    if (j.items.length < 200) break;
  }
  const inWindow = (d) => {
    const t = new Date(d);
    return t >= tFrom && t <= tTo;
  };

  // Reads (valid and invalid format) from the API, any mode, organiser cameras only.
  const reads = [];
  for (let page = 1; ; page++) {
    const j = await (await api(`/detections?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&page=${page}&page_size=500&sort=captured_at&order=asc`, token)).json();
    for (const it of j.items) if (cams.has(it.camera.id)) reads.push(it);
    if (j.items.length < 500) break;
  }
  for (const r of reads) {
    const rs = await fetch(`${API}${r.crop_url}`, { headers: { Authorization: `Bearer ${token}` } });
    r.cropBuf = rs.ok ? Buffer.from(await rs.arrayBuffer()) : null;
  }

  // Watchlist alerts raised on organiser cameras inside the window.
  const alerts = [];
  for (let page = 1; ; page++) {
    const j = await (await api(`/alerts?type=watchlist_hit&from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&page=${page}&page_size=200`, token)).json();
    for (const a of j.items) if (cams.has(a.camera?.id)) alerts.push(a);
    if (j.items.length < 200) break;
  }

  // Detected-vehicle evidence from the worker's store (best shot inside the window; static-text tracks excluded).
  const evidenceDir = join(repoRoot, 'media', 'anpr_evidence');
  const vehicles = [];
  let unclassified = 0;
  let textTracks = 0;
  for (const cid of cams.keys()) {
    const camDir = join(evidenceDir, String(cid));
    if (!existsSync(camDir)) continue;
    for (const day of await readdir(camDir)) {
      const f = join(camDir, day, 'vehicles.jsonl');
      if (!existsSync(f)) continue;
      for (const line of (await readFile(f, 'utf8')).split('\n')) {
        if (!line.trim()) continue;
        const rec = JSON.parse(line);
        if (!inWindow(rec.best_at)) continue;
        if (rec.kind === 'text') { textTracks++; continue; }
        if (!rec.kind) unclassified++;
        const cropPath = join(evidenceDir, rec.file);
        rec.cropBuf = existsSync(cropPath) ? await readFile(cropPath) : null;
        rec.sha256 = rec.cropBuf ? sha256(rec.cropBuf) : '';
        vehicles.push(rec);
      }
    }
  }
  vehicles.sort((a, b) => a.best_at.localeCompare(b.best_at));

  // Per-camera summary.
  const per = new Map();
  const bucket = (cid) => {
    if (!per.has(cid)) per.set(cid, { vehicles: 0, w60: 0, ocr: 0, readsValid: 0, readsInvalid: 0, alerts: 0 });
    return per.get(cid);
  };
  for (const v of vehicles) { const b = bucket(v.camera_id); b.vehicles++; if (v.width_px >= 60) b.w60++; if (v.ocr_runs > 0) b.ocr++; }
  for (const r of reads) { const b = bucket(r.camera.id); if (r.is_valid_format) b.readsValid++; else b.readsInvalid++; }
  for (const a of alerts) bucket(a.camera.id).alerts++;
  const validReads = reads.filter((r) => r.is_valid_format);
  const uniquePlates = new Set(validReads.map((r) => r.plate_norm));

  const counts = {
    window: { from, to, from_ist: fmtIst(from), to_ist: fmtIst(to) },
    organiser_cameras: cams.size,
    cameras_with_evidence: per.size,
    detected_vehicles: vehicles.length,
    detected_vehicles_ge_60px: vehicles.filter((v) => v.width_px >= 60).length,
    detected_vehicles_ocr_attempted: vehicles.filter((v) => v.ocr_runs > 0).length,
    static_text_tracks_excluded: textTracks,
    unclassified_pre_kind_records: unclassified,
    reads_total: reads.length,
    reads_valid_format: validReads.length,
    reads_invalid_format: reads.length - validReads.length,
    unique_valid_plates: uniquePlates.size,
    watchlist_alerts: alerts.length,
    per_camera: Object.fromEntries([...per.entries()].map(([cid, b]) => [cams.get(cid).external_id, b])),
  };

  const camLabel = (cid) => { const c = cams.get(cid); return c ? `${c.id} · ${c.external_id} · ${c.name}` : String(cid); };
  const thumb = (buf, h) => (buf ? `<img src="data:image/jpeg;base64,${buf.toString('base64')}" style="height:${h}px;max-width:220px;object-fit:contain;image-rendering:auto">` : '<span class="muted">crop missing</span>');

  const perRows = [...per.entries()].sort((a, b) => a[0] - b[0]).map(([cid, b]) => {
    const c = cams.get(cid);
    return `<tr><td>${c.id}</td><td>${esc(c.external_id)}</td><td>${esc(c.name)}</td><td>${esc(c.district || '')}</td><td class="n">${b.vehicles}</td><td class="n">${b.w60}</td><td class="n">${b.ocr}</td><td class="n">${b.readsValid}</td><td class="n">${b.readsInvalid}</td><td class="n">${b.alerts}</td></tr>`;
  }).join('');

  const readRows = reads.map((r, i) => `<tr><td class="n">${i + 1}</td><td>${fmtIst(r.captured_at)}</td><td>${esc(camLabel(r.camera.id))}</td><td class="plate ${r.is_valid_format ? 'ok' : 'bad'}">${esc(r.is_valid_format ? displayPlate(r.plate_norm) : r.plate_norm)}</td><td>${r.is_valid_format ? 'valid format' : 'invalid format (unreadable)'}</td><td class="n">${Number(r.confidence).toFixed(2)}</td><td>${r.watchlist_hit ? 'watchlist hit' : ''}</td><td>${thumb(r.cropBuf, 22)}</td><td class="mono">${esc((r.crop_sha256 || '').slice(0, 16))}…</td></tr>`).join('');

  const vehRows = vehicles.map((v, i) => `<tr><td class="n">${i + 1}</td><td>${fmtIst(v.best_at)}</td><td>${esc(camLabel(v.camera_id))}</td><td class="n">${v.width_px}×${v.height_px}</td><td class="n">${v.frames}</td><td class="n">${Number(v.det_conf).toFixed(2)}</td><td>${v.is_valid_format ? `<span class="plate ok">${esc(displayPlate(v.plate_norm))}</span>` : v.plate_norm ? `<span class="plate bad">${esc(v.plate_norm)}</span> (invalid)` : v.ocr_runs > 0 ? 'OCR: no string' : `not attempted (&lt; ${v.ocr_min_w} px)`}${v.kind ? '' : ' <span class="muted">unclassified</span>'}</td><td>${thumb(v.cropBuf, 26)}</td><td class="mono">${esc(v.sha256.slice(0, 16))}…</td></tr>`).join('');

  const alertRows = alerts.map((a) => `<tr><td>${fmtIst(a.created_at)}</td><td>${esc(camLabel(a.camera.id))}</td><td class="plate ok">${esc(displayPlate(a.plate_norm))}</td><td>${esc(a.priority)} · ${esc(a.confidence_level || '')}</td><td class="n">${a.latency_ms ?? ''}</td><td>${esc(a.watchlist?.reason || '')}</td></tr>`).join('');

  const generated = fmtIst(new Date());
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>Government-feed evidence report</title>
<style>
  @page { size: A4; margin: 0; }
  body { font: 10.5px/1.35 "Segoe UI", Arial, sans-serif; color: #1a1a1a; margin: 0; }
  h1 { font-size: 20px; margin: 0 0 4px; } h2 { font-size: 14px; margin: 18px 0 6px; border-bottom: 1px solid #999; padding-bottom: 2px; }
  p { margin: 4px 0; } .muted { color: #666; } .mono { font-family: Consolas, monospace; font-size: 9px; }
  table { border-collapse: collapse; width: 100%; margin: 4px 0 8px; page-break-inside: auto; }
  th, td { border: 1px solid #bbb; padding: 2px 4px; vertical-align: middle; text-align: left; }
  th { background: #e8eef5; font-weight: 600; } td.n { text-align: right; font-variant-numeric: tabular-nums; }
  tr { page-break-inside: avoid; }
  .plate { font-family: Consolas, monospace; font-weight: 700; } .plate.ok { color: #0b5d1e; } .plate.bad { color: #8a1c1c; }
  .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin: 8px 0; }
  .kpi { border: 1px solid #bbb; padding: 6px 8px; } .kpi b { display: block; font-size: 18px; }
</style></head><body>
<h1>Sentinel Gujarat — government-feed evidence report</h1>
<p><b>Window:</b> ${esc(fmtIst(from))} → ${esc(fmtIst(to))} (UTC ${esc(from)} → ${esc(to)}) · <b>Source:</b> organiser sandbox cameras (${cams.size} imported, relay paths <code>cam_&lt;id&gt;</code>) · <b>Generated:</b> ${esc(generated)} by ${esc(USER)}</p>
<p class="muted">Every row below is a record the system produced during the window: plate reads come from <code>GET /api/detections</code> (the same rows as the API output report <code>GET /api/reports/detections</code>), detected vehicles from the ANPR worker's evidence store (<code>media/anpr_evidence/&lt;camera&gt;/&lt;date&gt;/vehicles.jsonl</code>, one best-shot crop per ended plate track; tracks whose OCR only ever returned letters — burnt-in captions, signboards — are excluded as static text). Timestamps are IST (UTC stored). SHA-256 prefixes identify the crop files.</p>
${NOTE ? `<p><b>Feed availability:</b> ${esc(NOTE)}</p>` : ''}
${unclassified ? `<p class="muted"><b>${unclassified} of ${vehicles.length} vehicle records are marked <i>unclassified</i></b>: they were written by the worker image that ran before 16:15 UTC on 5 Sept, which did not yet separate static-text tracks (signboards, burnt-in captions) from vehicles; a signboard boxed by the detector may therefore be listed among them. Records written after that carry <code>kind = vehicle | plate</code> and every static-text track is left out.</p>` : ''}
<div class="kpis">
  <div class="kpi"><b>${counts.detected_vehicles}</b>detected vehicles (plate boxes tracked)<br><span class="muted">${counts.detected_vehicles_ge_60px} at ≥ 60 px · OCR attempted on ${counts.detected_vehicles_ocr_attempted}</span></div>
  <div class="kpi"><b>${counts.reads_valid_format}</b>plates read (valid format)<br><span class="muted">${counts.unique_valid_plates} distinct plates</span></div>
  <div class="kpi"><b>${counts.reads_invalid_format}</b>OCR strings in invalid format<br><span class="muted">plate seen, characters unreadable</span></div>
  <div class="kpi"><b>${counts.watchlist_alerts}</b>watchlist alerts<br><span class="muted">${counts.static_text_tracks_excluded} static-text tracks excluded</span></div>
</div>
<h2>1. Per camera</h2>
<table><thead><tr><th>Id</th><th>External id</th><th>Camera</th><th>District</th><th>Vehicles detected</th><th>≥ 60 px</th><th>OCR attempted</th><th>Plates read</th><th>Invalid strings</th><th>Alerts</th></tr></thead><tbody>${perRows || '<tr><td colspan="10" class="muted">no evidence in the window</td></tr>'}</tbody></table>
<h2>2. Plates read (${reads.length} OCR reads, ${validReads.length} valid format)</h2>
<table><thead><tr><th>#</th><th>Captured (IST)</th><th>Camera</th><th>Plate / OCR string</th><th>Format</th><th>Conf</th><th>Match</th><th>Crop</th><th>SHA-256</th></tr></thead><tbody>${readRows || '<tr><td colspan="9" class="muted">no reads in the window</td></tr>'}</tbody></table>
<h2>3. Watchlist alerts on organiser cameras (${alerts.length})</h2>
<table><thead><tr><th>Raised (IST)</th><th>Camera</th><th>Plate</th><th>Priority · match</th><th>Latency ms</th><th>Reason</th></tr></thead><tbody>${alertRows || '<tr><td colspan="6" class="muted">no watchlist alert on an organiser camera in the window</td></tr>'}</tbody></table>
<h2>4. Detected vehicles with timestamps (${vehicles.length}, best shot per track)</h2>
<table><thead><tr><th>#</th><th>Best shot (IST)</th><th>Camera</th><th>Plate box px</th><th>Frames</th><th>Det conf</th><th>OCR result</th><th>Crop</th><th>SHA-256</th></tr></thead><tbody>${vehRows || '<tr><td colspan="9" class="muted">no detected vehicle in the window</td></tr>'}</tbody></table>
</body></html>`;

  await mkdir(OUT, { recursive: true });
  const base = `evidence_${stamp(from)}_${stamp(to)}_IST`;
  const pdfPath = join(OUT, `${base}.pdf`);
  const { chromium } = await loadPlaywright();
  const { browser, label } = await launchChromium(chromium);
  try {
    const page = await browser.newPage();
    await page.setContent(html, { waitUntil: 'load' });
    await page.pdf({
      path: pdfPath, format: 'A4', printBackground: true, displayHeaderFooter: true,
      headerTemplate: '<div style="font-size:8px;color:#555;width:100%;padding:0 12mm;display:flex;justify-content:space-between"><span>Sentinel Gujarat · Dynatech Consultancy</span><span>Government-feed evidence report</span></div>',
      footerTemplate: '<div style="font-size:8px;color:#555;width:100%;padding:0 12mm;display:flex;justify-content:space-between"><span>Hackathon evidence — organiser sandbox feed, IST timestamps</span><span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>',
      margin: { top: '14mm', right: '10mm', bottom: '14mm', left: '10mm' },
    });
  } finally {
    await browser.close();
  }
  const pdfBuf = await readFile(pdfPath);
  counts.pdf = { path: pdfPath, bytes: pdfBuf.length, pages: countPdfPages(pdfBuf), sha256: sha256(pdfBuf), browser: label };

  // CSV of the vehicle evidence (the reads have their own CSV from the API).
  const cols = ['best_at_ist', 'best_at_utc', 'camera_id', 'camera_external_id', 'camera_name', 'district', 'first_seen_utc', 'last_seen_utc', 'plate_box_w_px', 'plate_box_h_px', 'frames', 'det_conf', 'ocr_runs', 'plate_norm', 'is_valid_format', 'kind', 'crop_file', 'crop_sha256'];
  const q = (v) => { const s = String(v ?? ''); return /^[=+\-@\t\r]/.test(s) ? `"'${s.replace(/"/g, '""')}"` : /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const csvRows = vehicles.map((v) => [fmtIst(v.best_at), v.best_at, v.camera_id, v.camera_external_id, cams.get(v.camera_id)?.name, cams.get(v.camera_id)?.district, v.first_seen, v.last_seen, v.width_px, v.height_px, v.frames, v.det_conf, v.ocr_runs, v.plate_norm, v.is_valid_format, v.kind || 'unclassified', v.file, v.sha256].map(q).join(','));
  const csvBody = [cols.join(','), ...csvRows].join('\n') + '\n';
  const csv = csvBody + `# Sentinel Gujarat 1.0.0-phase1 | vehicle evidence rows=${vehicles.length} | window ${from}..${to} | generated ${fmtIst(new Date())} by ${USER} | sha256(rows)=${sha256(Buffer.from(csvBody))}\n`;
  const csvPath = join(OUT, `${base}.csv`);
  await writeFile(csvPath, csv, 'utf8');
  counts.csv = { path: csvPath, rows: vehicles.length, sha256: sha256(Buffer.from(csv)) };
  await writeFile(join(OUT, `${base}.json`), JSON.stringify(counts, null, 2) + '\n', 'utf8');
  console.log(JSON.stringify(counts, null, 2));
}

main().catch((e) => { console.error(`build-evidence-report: ${e.stack || e}`); process.exit(1); });
