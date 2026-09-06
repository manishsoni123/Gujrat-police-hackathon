#!/usr/bin/env node
// Sentinel Gujarat - Solution Presentation builder.
//
//   node docs/tools/build-presentation.mjs                 # -> docs/export/Sentinel-Gujarat-Presentation.pptx + .pdf
//   node docs/tools/build-presentation.mjs --no-pdf        # PPTX only (no Chromium needed for the deck, still needed for image prep)
//   node docs/tools/build-presentation.mjs --out <dir>
//
// Environment (all optional; unfilled values are printed as [FILL: ...] placeholders and listed at the end):
//   SG_HOSTED_URL   hosted demo URL            SG_REPO_URL   repository URL (default: README)
//   SG_TEAM_EMAIL   team contact e-mail        SG_TEAM       JSON array of {role, name} for the team slide
//   SG_SHOTS        auto (default: moment shot when present, else live capture) | moment (require them) | live (ignore them)
//
// The deck is defined once as a slide model (title, elements in inches on a 16:9 canvas, speaker notes) and rendered
// twice: with pptxgenjs (MIT) into the PPTX and as an HTML twin that Chromium prints to PDF and screenshots per
// slide into docs/export/.build/slides/ for layout checks. Speaker notes come from docs/PRESENTATION-OUTLINE.md
// (column "Speaker notes" of the slide table, matched by outline slide number). Screenshots are taken from
// docs/screenshots/live/ and the architecture / workflow / scale diagrams from docs/diagrams/*.svg, converted to
// PNG by Chromium for PowerPoint. Measured figures are the canonical README "Measured on a 12-core laptop" set.
// Exit code 1 on any missing input, unrendered image or Chromium failure.

import { readFile, writeFile, mkdir, stat, copyFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve, basename } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { loadPlaywright, launchChromium, countPdfPages } from './export-pdf.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const docsDir = resolve(here, '..');
const require = createRequire(import.meta.url);

const fail = (m) => { process.stderr.write(`build-presentation: ${m}\n`); process.exit(1); };
const log = (m) => process.stdout.write(`build-presentation: ${m}\n`);

// ---------------------------------------------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------------------------------------------

const PRODUCT = 'Sentinel Gujarat';
const ORG = 'Dynatech Consultancy';
const VERSION = '1.0.0-phase1';
const TAG = 'v1.0-phase1';
const MODEL_STATEMENT = 'Model 1 + Model 2 (hybrid roadmap to 3/4)';
const FOOTER = `${ORG} · Gujarat Police Innovation Challenge 2026 · ${MODEL_STATEMENT}`;
const HOSTED_URL = process.env.SG_HOSTED_URL || '[FILL: https://<demo-domain>]';
const REPO_URL = process.env.SG_REPO_URL || 'https://github.com/dynatech-consultancy/sentinel-gujarat';
const TEAM_EMAIL = process.env.SG_TEAM_EMAIL || '[FILL: team e-mail]';
const SHOTS = process.env.SG_SHOTS || 'auto';
if (!['auto', 'moment', 'live'].includes(SHOTS)) fail(`SG_SHOTS must be auto, moment or live (got ${SHOTS})`);
const TEAM = process.env.SG_TEAM ? JSON.parse(process.env.SG_TEAM) : [
  { role: 'B · Backend (FastAPI, PostgreSQL/PostGIS, matcher, reports)', name: '[FILL: name]' },
  { role: 'M · ANPR / ML (decoder, detector, PaddleOCR, voting, counts)', name: '[FILL: name]' },
  { role: 'F · Frontend (React, Ant Design, map, wall, alerts)', name: '[FILL: name]' },
  { role: 'D · Deployment (Docker, MediaMTX, Caddy, VM, soak)', name: '[FILL: name]' },
  { role: 'P · PM / documentation / videos / submission', name: '[FILL: name]' },
];

const C = {
  navy: '0B1F3A', blue: '1E4DB7', white: 'FFFFFF', soft: 'E8EEFA', ink: '1A202C', muted: '4A5568',
  line: 'C9D3E6', amber: 'D97706', amberSoft: 'FDF1DC', green: '15803D', greenSoft: 'E3F3E8', red: 'B91C1C', grey: 'F4F6FA',
};
const FONT = 'Arial';
const W = 13.333;
const H = 7.5;

// ---------------------------------------------------------------------------------------------------------------
// Inputs
// ---------------------------------------------------------------------------------------------------------------

// Each key lists candidates relative to docs/screenshots/, first existing wins: the real-sandbox captures of
// 5 Sept 2026 (frontend/e2e/sandbox_proof.mjs / sandbox_screenshots.mjs, 1440x900, the 30 organiser cameras)
// where the slide shows something the real feed has proven (map, wall, health), then the hand-taken "moment"
// shots of PRESENTATION-OUTLINE.md (docs/tools/moment-shots.mjs, 1920x1080), else the live-stack captures
// (frontend/e2e/live_screenshots.mjs, 1440x900) which always exist after a stack run. The alert / route / report
// / camera-page shots stay on the mock loops until a real sandbox read exists. sandbox/proof-import.png is a
// full-page capture (1440x3132) and is listed last on purpose: crop it to 16:9 before promoting it.
const SCREENSHOTS = {
  login: ['01-login.png', 'live/login.png'],
  dashboard: ['03-dashboard.png', 'sandbox/proof-dashboard.png', 'live/dashboard.png'],
  map: ['sandbox/proof-map.png', '08-map-layers.png', '02-map-districts.png', 'live/map.png'],
  wall9: ['sandbox/proof-wall-9.png', '09-wall-9.png', 'live/wall-9.png'],
  cameraDetail: ['04-camera-live-reads.png', 'sandbox/proof-camera-cam01.png', 'live/camera-detail.png'],
  alertToast: ['10-alert-toast.png', 'live/alert-toast.png'],
  route: ['05-route.png', 'live/route-gj01ab1234.png'],
  reports: ['11-output-report.png', 'live/reports.png'],
  import: ['07-import-summary.png', 'live/import.png', 'sandbox/proof-import.png'],
  health: ['sandbox/proof-health.png', '12-systems-unaffected.png', 'live/health.png'],
};
const DIAGRAMS = { architecture: '01-architecture.svg', onboarding: '02-onboarding.svg', anpr: '03-anpr-alert-sequence.svg', scale: '07-scale-topology.svg' };

function parseArgs(argv) {
  const o = { out: join(docsDir, 'export'), pdf: true };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--out') o.out = resolve(argv[++i] ?? '');
    else if (argv[i] === '--no-pdf') o.pdf = false;
    else if (argv[i] === '--help' || argv[i] === '-h') { process.stdout.write('usage: build-presentation.mjs [--out <dir>] [--no-pdf]\n'); process.exit(0); }
    else fail(`unknown argument ${argv[i]}`);
  }
  return o;
}

async function readOutlineNotes() {
  const md = await readFile(join(docsDir, 'PRESENTATION-OUTLINE.md'), 'utf8');
  const notes = {};
  const titles = {};
  for (const line of md.split(/\r?\n/)) {
    const m = line.match(/^\|\s*(\d+)\s*\|\s*\*\*(.+?)\*\*[^|]*\|(.*)\|\s*$/);
    if (!m) continue;
    const cells = m[3].split(' | ').map((c) => c.trim());
    const last = cells[cells.length - 1] || '';
    let note = last.replace(/^"|"$/g, '').replace(/`/g, '').trim();
    if (!note) note = cells[0] || '';
    notes[Number(m[1])] = note;
    titles[Number(m[1])] = m[2];
  }
  if (Object.keys(notes).length < 15) fail(`only ${Object.keys(notes).length} slide rows found in PRESENTATION-OUTLINE.md`);
  return { notes, titles };
}

// ---------------------------------------------------------------------------------------------------------------
// Image preparation (Chromium): SVG diagrams -> PNG, tall screenshots cropped to the first 1440x900 viewport
// ---------------------------------------------------------------------------------------------------------------

async function prepareImages(browser, buildDir) {
  const imgDir = join(buildDir, 'img');
  await mkdir(imgDir, { recursive: true });
  const out = {};
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const used = [];
  for (const [key, candidates] of Object.entries(SCREENSHOTS)) {
    const pool = SHOTS === 'live' ? candidates.filter((c) => c.startsWith('live/')) : SHOTS === 'moment' ? candidates.filter((c) => !c.startsWith('live/')) : candidates;
    const rel = pool.find((c) => existsSync(join(docsDir, 'screenshots', c)));
    if (!rel) fail(`missing screenshot for "${key}" (SG_SHOTS=${SHOTS}; tried ${pool.join(', ')} under docs/screenshots/; run node docs/tools/moment-shots.mjs or cd frontend && node e2e/live_screenshots.mjs)`);
    const src = join(docsDir, 'screenshots', rel);
    used.push(`${key}=${rel}`);
    const buf = await readFile(src);
    const w = buf.readUInt32BE(16);
    const h = buf.readUInt32BE(20);
    const dest = join(imgDir, `${key}.png`);
    const cropH = Math.round(w * 0.625);
    if (h <= Math.round(w * 0.75)) {
      await copyFile(src, dest);
      out[key] = { path: dest, w, h };
    } else {
      // full-page capture: keep the first viewport (top of the page) so the tile has the 16:10 shape of the others
      await page.setViewportSize({ width: w, height: cropH });
      await page.setContent(`<html><body style="margin:0"><img src="data:image/png;base64,${buf.toString('base64')}" style="display:block;width:${w}px;height:${h}px"></body></html>`);
      await page.screenshot({ path: dest, clip: { x: 0, y: 0, width: w, height: cropH } });
      out[key] = { path: dest, w, h: cropH };
    }
  }
  log(`screenshots: ${used.join(' · ')}`);
  for (const [key, file] of Object.entries(DIAGRAMS)) {
    const src = join(docsDir, 'diagrams', file);
    if (!existsSync(src)) fail(`missing diagram ${src} (render with mermaid-cli, see PRESENTATION-OUTLINE.md)`);
    let svg = await readFile(src, 'utf8');
    const vb = svg.match(/viewBox="([\d.\s-]+)"/)?.[1].trim().split(/\s+/).map(Number) ?? [0, 0, 1600, 900];
    const aspect = vb[2] / vb[3];
    const width = 2400;
    const height = Math.round(width / aspect);
    svg = svg.replace(/<svg([^>]*?)\swidth="[^"]*"/, '<svg$1').replace(/<svg([^>]*?)\sheight="[^"]*"/, '<svg$1')
      .replace(/style="max-width:[^"]*"/, `style="width:${width}px;height:${height}px;background:white"`);
    await page.setViewportSize({ width, height });
    await page.setContent(`<html><body style="margin:0;background:white">${svg}</body></html>`);
    await page.evaluate(() => document.fonts.ready);
    const dest = join(imgDir, `${key}.png`);
    await page.screenshot({ path: dest, clip: { x: 0, y: 0, width, height } });
    out[key] = { path: dest, w: width, h: height };
  }
  await page.close();
  return out;
}

// ---------------------------------------------------------------------------------------------------------------
// Slide model
// ---------------------------------------------------------------------------------------------------------------

const MEASURED = [
  ['Fresh clone → running stack', '`docker compose up -d --build` 27 s with warm images; `/healthz` ok 3 s later (seed of 27 departments, 4 users, 52 POIs, 33 districts, 23 watchlist rows included)'],
  ['Organiser sandbox import, 30 real cameras (fetch → upsert → 47 relay paths → ffprobe of every stream, 6 parallel → first stream)', '82–99 s including the probes (45 s cap; the sandbox answers in 4–38 s); first on-demand stream 2–5 s; re-run 0 added / 30 updated / 0 duplicates'],
  ['Mock catalogue import of 50 cameras (fetch → upsert → 61 relay paths → first stream)', '2.6 s on the fresh stack (`duration_ms` 2566, first on-demand stream 2 066 ms); second run, 50 unchanged: 14–44 ms'],
  ['Read → alert latency (`alerts.latency_ms`; 3 s vote window + 1 s batch)', 'steady state p50 3.3 s · p95 4.0 s · max 4.1 s (8 cameras, n = 13); fresh run p50 3.2 s · p95 4.4 s (n = 14)'],
  ['Watchlist add → first WebSocket alert', '10–15 s (next appearance of the plate); webhook delivered to the sink 15 s after the add'],
  ['ANPR worker, 8 cameras, CPU only (`CPU=1`)', 'decode 5.0 fps per camera (40 fps total); 19–21 frames/s inferred; PaddleOCR 270–470 ms per crop; 70–85 % of plate appearances yield a voted read'],
  ['API cost per worker request', '`/internal/detections` 15–40 ms · `/internal/snapshots` 12–20 ms · heartbeat < 70 ms at ≈ 16 internal requests/s'],
  ['Vehicle search / route (36 sightings, 5 cameras)', 'search 60–120 ms · route 100–200 ms · route PDF 0.6 s (72 KB)'],
  ['Output report, 2 h window (68 reads)', 'CSV 19 KB / 0.2 s · PDF 465 KB / 1.4 s'],
  ['Gap analysis (60 cameras, 33 districts, 500 m grid, 237 k empty cells)', '4.0–8.8 s uncached, < 100 ms cached (5 min TTL)'],
  ['Health poller settle / relay outage recovery', '120 s to settle (two 60 s ticks); after a 3 min relay outage every camera auto-closed "Camera back online" on the first tick'],
  ['Stack footprint (8 live cameras, 9-tile wall)', 'RAM ≈ 3.3–4.3 GB total (anpr-live 2.0–2.3 GB, mediamtx 170–330 MB, api 150 MB); CPU anpr-live 190–270 %, mediamtx 15–80 %, api 7–14 %'],
  ['DB size / evidence files', '2.5 MB per 1,000 sightings incl. reads; crop 7.1 KB + best frame 30.7 KB per sighting; recordings ≈ 120 MB per camera-hour (720p loops)'],
];

// Element helpers (inches). `id` is used for overflow reporting in the HTML twin.
const text = (x, y, w, h, t, o = {}) => ({ type: 'text', x, y, w, h, text: t, ...o });
const bullets = (x, y, w, h, items, o = {}) => ({ type: 'bullets', x, y, w, h, items, ...o });
const image = (x, y, w, h, src, o = {}) => ({ type: 'image', x, y, w, h, src, ...o });
const table = (x, y, w, rows, colW, o = {}) => ({ type: 'table', x, y, w, rows, colW, ...o });
const box = (x, y, w, h, title, items, o = {}) => ({ type: 'box', x, y, w, h, title, items, ...o });
const tile = (x, y, w, h, value, label, o = {}) => ({ type: 'tile', x, y, w, h, value, label, ...o });
const TILE = { valueSize: (e) => (e.big ? 26 : 20), labelSize: (e) => (e.big ? 12 : 9.5), valueH: (e) => (e.big ? 0.75 : 0.55) };
const flow = (x, y, w, h, steps, o = {}) => ({ type: 'flow', x, y, w, h, steps, ...o });
const arrow = (x, y, w, h, o = {}) => ({ type: 'arrow', x, y, w, h, ...o });

function buildSlides(outline) {
  const n = (k) => outline.notes[k] || '';
  const slides = [];

  // 1 Title -----------------------------------------------------------------------------------------------------
  slides.push({
    id: 'title', kind: 'title', outline: 1, notes: n(1),
    elements: [
      text(0.7, 0.9, 6.9, 0.5, 'GUJARAT POLICE INNOVATION CHALLENGE 2026 · CCTV INTEGRATION HACKATHON (SENTINEL) · PHASE 1', { size: 10, color: 'A9C2F2', bold: true }),
      text(0.7, 1.45, 6.9, 1.1, 'Sentinel Gujarat', { size: 46, bold: true, color: C.white }),
      text(0.7, 2.6, 6.9, 1.3, 'Unified CCTV registry, GIS, live viewing, ANPR, watchlist alerts and vehicle route reconstruction for Gujarat Police', { size: 19, color: C.white }),
      text(0.7, 4.05, 6.9, 1.6, [
        'Dynatech Consultancy · Category 2 (Company / Systems Integrator)',
        `${MODEL_STATEMENT}`,
        `Version ${VERSION} · git tag ${TAG} · September 2026`,
        `Hosted demo: ${HOSTED_URL}`,
        `Repository: ${REPO_URL}`,
      ].join('\n'), { size: 12.5, color: 'DCE6FA', line: 1.35 }),
      text(0.7, 5.95, 6.9, 0.65, 'A working platform: everything in this deck is running software on the organiser’s sandbox feeds plus our own camera.', { size: 11, color: 'A9C2F2', italic: true }),
      image(7.95, 1.25, 4.85, 5.0, 'login', { frame: true }),
    ],
  });

  // 2 Agenda ----------------------------------------------------------------------------------------------------
  const agenda = [
    'The problem in Gujarat’s terms', 'Chosen models and justification', 'Overview, objectives, innovations', 'Architecture',
    'End-to-end workflow', 'AI analytics approach', 'Watchlist correlation & alerting — the four boxes', 'Technology stack — all open source',
    'Existing departmental systems remain unaffected', 'Scalability, interoperability, security, deployment', 'Bonus items delivered',
    'Operational benefits for policing', 'Measured figures', 'Screenshots — the demo journey', 'Team, links, Phase 2 readiness',
  ];
  slides.push({
    id: 'agenda', title: 'Agenda', kicker: 'Fifteen minutes, one working platform', outline: null,
    notes: 'The order follows the portal’s presentation requirements (guide §7.2): chosen models with justification, overview and innovations, architecture and workflow, AI analytics, the four-box watchlist workflow, technologies, scalability / interoperability / security / deployment, bonus items, operational benefits, screenshots and team.',
    elements: [
      bullets(0.8, 1.45, 5.9, 5.3, agenda.slice(0, 8).map((t, i) => `${i + 1}.  ${t}`), { size: 16, noBullet: true, line: 1.5 }),
      bullets(6.9, 1.45, 5.9, 5.3, agenda.slice(8).map((t, i) => `${i + 9}.  ${t}`), { size: 16, noBullet: true, line: 1.5 }),
    ],
  });

  // 3 Problem ---------------------------------------------------------------------------------------------------
  slides.push({
    id: 'problem', title: 'The problem in Gujarat’s terms', kicker: 'One view over the cameras that already exist — without touching what departments run', outline: 2, notes: n(2),
    elements: [
      tile(0.6, 1.4, 1.75, 1.15, '26', 'departments with their own CCTV estates'),
      tile(2.5, 1.4, 1.75, 1.15, '~80,000', 'cameras to reach statewide'),
      tile(4.4, 1.4, 1.75, 1.15, '7–15+ d', 'retention, many vendors, DVR / NVR / VMS'),
      tile(6.3, 1.4, 1.75, 1.15, '~1,000 km', 'incl. border districts on 4G'),
      bullets(0.6, 2.85, 7.5, 4.1, [
        'Analog and IP cameras, DVRs, NVRs and VMS platforms from many vendors; retention 7–15+ days',
        'Remote and border districts (Valsad, Dahod, Gir Somnath, Jamnagar, Devbhumi Dwarka) often on 4G',
        { t: 'Gujarat Police (SCRB) needs one interoperable platform that:', sub: [
          'knows where every camera is, who owns it, how it is connected and whether it works — registry + GIS + health',
          'shows any camera live without disturbing the owning department — unified viewing',
          'reads plates, correlates with watchlists and alerts in real time — ANPR + correlation + alerts',
          'answers “where did GJ 01 AB 1234 go?” across cameras — route reconstruction (the Phase 2 live test)',
          'has a costed path to ~80,000 cameras and to VAHAN / SARTHI / eGujCop / AFIS / NAFIS',
        ] },
      ], { size: 12.5 }),
      image(8.4, 1.4, 4.4, 2.85, 'map', { frame: true, caption: 'GIS map with department, status and district layers' }),
      image(8.4, 4.3, 4.4, 2.7, 'dashboard', { frame: true, caption: 'Dashboard: reads, alerts, object counts, camera health' }),
    ],
  });

  // 4 Chosen models --------------------------------------------------------------------------------------------
  const status = (t, kind) => ({ text: t, fill: kind === 'done' ? C.greenSoft : C.amberSoft, color: kind === 'done' ? C.green : C.amber, bold: true });
  slides.push({
    id: 'models', title: 'Chosen models and justification', kicker: MODEL_STATEMENT, outline: 3, notes: n(3),
    elements: [
      table(0.6, 1.4, 12.1, [
        ['Model', 'Status in this submission', 'Why'],
        ['Model 1 · Centralised CCTV Registry & GIS Mapping', status('Delivered (mandatory)', 'done'), 'Bulk (catalogue and CSV), manual and API onboarding; GIS map with department / type / status / maintenance / coverage / district layers; health and maintenance monitoring; gap-analysis and ageing report; role-scoped search, filtering, export; append-only audit trail'],
        ['Model 2 · Unified Viewing & Metadata Analytics', status('Delivered', 'done'), 'Direct RTSP to each source through an internal relay (no federation middleware — FAQ Q17); video wall 4/9/16; ANPR metadata; event tagging; searchable vehicle-movement records; alerts on watchlisted vehicles; departmental systems untouched'],
        ['Model 3 · VMS Federation & Middleware', status('Roadmap, seeded in code', 'seed'), 'CameraSourceAdapter interface (RTSP and sandbox-catalogue implementations today; ONVIF designed as P2) and the outbound webhook are the seed of the adapter framework and event bus; Kafka federation described in the Plan for Scale'],
        ['Model 4 · Central VMS & AI Platform', status('Roadmap, partially delivered', 'seed'), 'Short-retention event recording, playback and evidence clips are delivered; hot / warm / cold central recording, Kubernetes and DR are designed and costed in the Plan for Scale'],
        ['Model 5 · Hybrid', status('This submission', 'done'), 'Described identically in every document as “Model 1 + Model 2 (hybrid roadmap to 3/4)”'],
      ], [3.0, 2.3, 6.8], { size: 12.5 }),
      text(0.6, 5.85, 12.1, 0.95, 'Every mandatory item of the challenge — registry, GIS, onboarding of sandbox feeds, ANPR, watchlist, real-time alert, movement history, output report — lives in Models 1 and 2 and can be shown working on the organiser’s raw RTSP feeds. Models 3 and 4 are shown as code and design, not claimed.', { size: 11.5, color: C.muted, fill: C.grey }),
    ],
  });

  // 5 Overview / objectives / innovations ---------------------------------------------------------------------
  slides.push({
    id: 'overview', title: 'Overview, objectives, innovations', kicker: 'Register every camera on a map · watch it live · read plates · alert on watchlist hits · trace the route', outline: 4, notes: n(4),
    elements: [
      text(0.6, 1.35, 6.1, 0.4, 'Objectives — what “done” means, with the evidence in the demo', { size: 13, bold: true, color: C.blue }),
      bullets(0.6, 1.75, 6.1, 5.2, [
        'O1 · Onboard the 30 organiser sandbox cameras in one click and measure the time — import summary with `duration_ms` (30/30 in 82–99 s incl. a probe of each)',
        'O2 · Feeds from at least two systems in one viewer — wall with sandbox + own private camera, “Two systems” badge',
        'O3 · Plates with confidence and timestamps, crops with SHA-256 — detections page, live-reads overlay',
        'O4 · Alert within seconds of a watchlisted read — toast + sound + desktop notification; `latency_ms` on every alert',
        'O5 · Route across cameras with operator confirmation — numbered markers, polyline, timeline, PDF',
        'O6 · Evidence-grade output report — CSV + PDF, IST timestamps, camera IDs, hashes, counts, quality section',
        'O7 · Role and department scoping, audited — four roles; `dept_admin_police` sees only Police cameras',
        'O8 · Fresh VM in under 15 minutes; CPU-only laptop profile',
      ], { size: 12.5 }),
      text(7.0, 1.35, 5.8, 0.4, 'Innovations — why they matter for policing', { size: 13, bold: true, color: C.blue }),
      bullets(7.0, 1.75, 5.8, 5.2, [
        { t: 'One connection per camera, relay-centric', sub: ['Departmental NVRs see a single extra viewer; 4G cameras are never overloaded'] },
        { t: 'Settings-driven catalogue onboarding with a field map', sub: ['A new environment is a settings change, not code; onboarding time is measured and shown'] },
        { t: 'Position-aware plate normalisation + char-wise voting', sub: ['Shared by worker and API, 20 test vectors; search and alerts agree'] },
        { t: 'Pre-index + live dual mode', sub: ['All cameras indexed at keyframe rate, road-facing cameras at 5 fps'] },
        { t: 'Operator-in-the-loop routes, evidence hashes on every artefact', sub: ['Human-confirmed, audited, SHA-256 verified chain of custody'] },
        { t: 'Recording next to metadata · privacy controls · open source end-to-end', sub: ['Play the moment from any alert; retention, watermark, scoping; no licence fees'] },
      ], { size: 12.5 }),
    ],
  });

  // 6 Architecture ---------------------------------------------------------------------------------------------
  slides.push({
    id: 'architecture', title: 'Architecture (HLD §3.1)', kicker: 'Sources → MediaMTX relay → browsers (WHEP / HLS) and ANPR workers → FastAPI + PostgreSQL / PostGIS → React UI', outline: 5, notes: n(5),
    elements: [
      image(0.35, 1.25, 10.1, 5.7, 'architecture'),
      box(10.6, 1.35, 2.35, 1.75, 'One connection per camera', ['The relay pulls each source once over TCP; browsers, workers, health and recording all read the relay'], { compact: true }),
      box(10.6, 3.3, 2.35, 1.75, 'Worker → metadata + crops', ['Posts JSON + crop.jpg; needs only outbound HTTPS to the API — runs on the VM, a district appliance or a laptop'], { compact: true }),
      box(10.6, 5.25, 2.35, 1.75, 'PostgreSQL, the only datastore', ['Same containers on one VM today and per district at scale; UTC stored, IST rendered'], { compact: true }),
    ],
  });

  // 7 End-to-end workflow --------------------------------------------------------------------------------------
  slides.push({
    id: 'workflow', title: 'End-to-end workflow', kicker: 'Onboarding (catalogue / CSV / API / form → validate → upsert → relay path → health → ANPR) and ANPR → sighting → alert', outline: 6, notes: n(6),
    elements: [
      image(0.4, 1.25, 3.2, 5.15, 'onboarding', { caption: 'Onboarding — four paths into one registry (HLD §6.1)' }),
      image(3.7, 1.25, 9.25, 5.15, 'anpr', { caption: 'ANPR → sighting → watchlist alert sequence (HLD §8.1)' }),
      bullets(0.6, 6.4, 12.2, 0.65, [
        'From catalogue to first stream in seconds — the import summary prints the measured time (30 real sandbox cameras in 82–99 s with every stream probed, first stream 2–5 s; 50 mock cameras in 2.6 s)',
        'Every read is voted for 3 s, normalised identically in the worker and the API, and matched in-process; route search reuses the same sightings',
      ], { size: 11 }),
    ],
  });

  // 8 AI analytics ---------------------------------------------------------------------------------------------
  slides.push({
    id: 'analytics', title: 'AI analytics approach', kicker: 'Delivered: ANPR and vehicle / person counting · Roadmap: intrusion zones (P2), FRS, tracking', outline: 7, notes: n(7),
    elements: [
      flow(0.6, 1.35, 12.2, 0.95, ['Decode — ffmpeg, PTS-driven, TCP, 2→30 s backoff', 'Plate detector — YOLO-v9-t ONNX (contour fallback)', 'PaddleOCR — two-line aware, CLAHE', 'Normalise — position-aware, 20 test vectors', 'Vote — char-wise, 3 s window', 'Sightings → matcher → alerts']),
      bullets(0.6, 2.55, 7.4, 4.4, [
        'Live mode on the 8 road-facing sandbox cameras picked by a readability survey (5 fps per T4-class GPU, 1 fps with full-resolution tiles on the CPU-only laptop); pre-index mode at keyframe rate on all 30, so the whole loop is indexed before the test',
        'Indian formats handled deterministically: standard and BH series, two-line plates, OCR confusions (0/O, 1/I, 5/S, 8/B) mapped by character position; at most two substitutions',
        'Char-wise voting over a 3 s window per camera; the best crop and full frame are stored with SHA-256 at write time',
        'Vehicle / person counts with YOLOX-s (COCO, ONNX) every N-th frame; dashboard tiles and the output report carry the counts',
        'Quality section: spot-check accuracy from operator-labelled crops, printed in the report — measured, not claimed',
        'All inference through onnxruntime / PaddlePaddle — CPU today, CUDA on the GPU profile; no cloud AI API anywhere',
      ], { size: 11.5 }),
      image(8.25, 2.55, 4.55, 4.4, 'cameraDetail', { frame: true, caption: 'Camera page: WebRTC player, live reads with crops and confidence, IST times' }),
    ],
  });

  // 9 Four boxes -----------------------------------------------------------------------------------------------
  slides.push({
    id: 'fourbox', title: 'Watchlist correlation & alerting — the complete integration workflow', kicker: 'Database structure · matching logic · alerting mechanism · user interface (HLD §7)', outline: 8, notes: n(8),
    elements: [
      box(0.6, 1.35, 6.0, 2.75, '1 · Database structure', [
        'watchlist — entity_type, plate_norm (unique among active vehicles), reason, priority, source (own / egujcop / vahan / manual / import), expires_at, hit_count',
        'plate_reads — every voted read: camera, captured_at (UTC), plate_raw / plate_norm, confidence, bbox, crop + SHA-256',
        'sightings — continuous presence at one camera: first / last seen, read_count, best crop, frame + SHA-256',
        'alerts — type, status (new / acknowledged / closed), priority, confidence_level, snapshot, latency_ms, outcome',
        'events, route_confirmations, audit_log (append-only) · indexes on (plate_norm, time) + trigram GIN',
      ]),
      box(6.75, 1.35, 6.0, 2.75, '2 · Matching logic', [
        'Normalise: uppercase, strip IND prefix, position-aware confusion maps, standard + BH patterns',
        'Exact: plate_norm in an in-memory map of effective watchlist rows (rebuilt on change and every 60 s)',
        'Possible: read confidence ≥ 0.8 and Levenshtein distance 1 → confidence_level = possible',
        'Suppression: same (watchlist, camera) within 60 s or still open within 10 min → read_count += 1, alert_update',
        'Priority = max(entry priority, reason floor) — stolen / wanted always critical; possible steps down one level',
      ]),
      box(0.6, 4.25, 6.0, 2.75, '3 · Alerting mechanism', [
        'INSERT alerts with latency_ms = created_at − captured_at; event watchlist_hit; watchlist.hit_count++',
        'WebSocket /ws/alerts envelope {type, ts, data + sound + notify_title / body}, scoped to the user’s department',
        'Browser desktop notification when the tab is hidden; sound per priority',
        'Outbound webhook with X-Sentinel-Signature (HMAC SHA-256), retries 2 / 4 / 8 s, delivery log; Telegram (P2)',
        'Escalation badge when unacknowledged > 5 min; camera-offline and intrusion alerts share the lifecycle',
      ]),
      box(6.75, 4.25, 6.0, 2.75, '4 · User interface', [
        'Toast (priority-coloured, critical persists) + sound + bell badge + map marker flash + sidebar dot',
        'Alert panel sorted by priority then time; Ack / Close with note and outcome (resolved, false_positive, duplicate, other)',
        'Detail drawer: crop, full frame, camera card, mini-map, “Play recording” (from 10 s before), “Create clip” with hash',
        'One click to the vehicle’s route: numbered markers, polyline, timeline, confirm / reject candidates, PDF',
        'Everything scoped by role and department; every action in the audit log',
      ]),
    ],
  });

  // 10 Tech stack ----------------------------------------------------------------------------------------------
  const stack = (title, items) => ({ title, items });
  const stackBoxes = [
    stack('Frontend', ['React 18 — MIT', 'Ant Design 5 — MIT', 'Vite 5 · TypeScript — MIT / Apache-2.0', 'TanStack Query · zustand — MIT', 'Leaflet — BSD-2 · hls.js — Apache-2.0', 'recharts · dayjs — MIT · Inter — OFL 1.1']),
    stack('Backend', ['FastAPI · uvicorn — MIT / BSD-3', 'SQLAlchemy — MIT · Pydantic — MIT', 'APScheduler — MIT', 'ReportLab (PDF reports) — BSD-3', 'python-jose · passlib · bcrypt — MIT / BSD-3 / Apache-2.0']),
    stack('Data', ['PostgreSQL 16 — PostgreSQL Licence', 'PostGIS — GPL-2.0 (server, unmodified)', 'pg_trgm · fuzzystrmatch — PostgreSQL', 'GeoJSON · CSV · OpenAPI 3 · ISO-8601 · WGS-84']),
    stack('Streaming', ['MediaMTX — MIT', 'ffmpeg — LGPL / GPL build', 'RTSP / RTP over TCP · WebRTC (WHEP) · HLS · fMP4', 'Caddy 2 (TLS, forward-auth) — Apache-2.0']),
    stack('ANPR / analytics', ['PaddleOCR · PaddlePaddle — Apache-2.0', 'open-image-models YOLO-v9-t plate detector — MIT', 'YOLOX-s (COCO) — Apache-2.0', 'onnxruntime — MIT · OpenCV — Apache-2.0', 'NumPy — BSD-3']),
    stack('Deployment', ['Docker Engine · Compose v2 — Apache-2.0', 'NVIDIA Container Toolkit — Apache-2.0', 'Ubuntu 22.04 / Debian slim / Alpine images', 'Let’s Encrypt certificates', 'pytest · Playwright (tools only)']),
  ];
  stackBoxes.forEach((b, i) => {
    const col = i % 3;
    const row = Math.floor(i / 3);
    slides.__stack ??= [];
    slides.__stack.push(box(0.6 + col * 4.1, 1.35 + row * 2.45, 3.95, 2.3, b.title, b.items, { small: true }));
  });
  slides.push({
    id: 'stack', title: 'Technology stack — all open source', kicker: 'No proprietary VMS, SDK or cloud AI anywhere; every licence recorded in docs/LICENCES.md and on the /about page', outline: 9, notes: n(9),
    elements: [
      ...slides.__stack,
      text(0.6, 6.3, 12.2, 0.65, 'Disclosed: the Ultralytics YOLOv8 fallback plate detector is AGPL-3.0 and is not installed in the default images (only used if the primary detector is unavailable); react-leaflet is under the Hippocratic License 2.1 (source-available) and is replaceable by direct Leaflet hooks — recorded in LICENCES.md note A. NVIDIA driver / CUDA runtimes are platform dependencies, not part of the solution; the CPU profile runs without them.', { size: 10, color: C.muted, fill: C.grey }),
    ],
  });
  delete slides.__stack;

  // 11 Systems unaffected --------------------------------------------------------------------------------------
  slides.push({
    id: 'unaffected', title: 'Existing departmental systems remain unaffected', kicker: 'Model 2 architecture note (HLD §17): what the platform does to a departmental system is nothing but read one stream', outline: 10, notes: n(10),
    elements: [
      text(0.6, 1.5, 3.2, 1.7, 'Departmental NVR / VMS / IP camera\n\nUntouched: no software installed, no configuration, recording schedule, retention or user account changed', { size: 11.5, fill: C.soft, color: C.navy, bold: false, align: 'center', valign: 'middle', border: C.blue }),
      arrow(3.9, 2.0, 1.5, 0.5, { label: 'one read-only RTSP\nconnection\n(TCP, on demand)' }),
      text(5.5, 1.5, 3.0, 1.7, 'MediaMTX internal relay\n\nInside our deployment boundary — not a federation middleware at the department (FAQ Q17)', { size: 11.5, fill: C.navy, color: C.white, align: 'center', valign: 'middle' }),
      arrow(8.6, 2.1, 0.9, 0.5, {}),
      text(9.6, 1.35, 3.2, 0.5, 'Operator browsers — WebRTC / HLS', { size: 11, fill: C.grey, color: C.ink, align: 'center', valign: 'middle', border: C.line }),
      text(9.6, 1.9, 3.2, 0.5, 'ANPR workers — live + pre-index', { size: 11, fill: C.grey, color: C.ink, align: 'center', valign: 'middle', border: C.line }),
      text(9.6, 2.45, 3.2, 0.5, 'Health probes', { size: 11, fill: C.grey, color: C.ink, align: 'center', valign: 'middle', border: C.line }),
      text(9.6, 3.0, 3.2, 0.5, 'Event recording · clips · playback', { size: 11, fill: C.grey, color: C.ink, align: 'center', valign: 'middle', border: C.line }),
      bullets(0.6, 3.55, 12.2, 3.45, [
        'Direct, read-only integration with a stream credential the department issues — we write nothing to cameras, DVRs, NVRs or VMS',
        'Exactly one connection per camera: MediaMTX pulls once, on demand, and closes after 60 s without readers; every consumer reads the relay, never the source',
        'No data leaves the department except the stream; camera metadata is entered by the department and correctable by its own dept_admin',
        'Bandwidth is bounded and predictable: one stream at native bitrate (or sub-stream on constrained links); keyframe-only pulls where analysis is at keyframe rate',
        'Failure isolation: platform down → departments continue unchanged; source down → camera marked offline, low-priority alert, exponential backoff 2–30 s',
        'Reversible: retire the camera on our side, delete the relay path, the department revokes the credential — nothing to undo on their side',
      ], { size: 12.5, line: 1.25 }),
    ],
  });

  // 12 Scale ----------------------------------------------------------------------------------------------------
  slides.push({
    id: 'scale', title: 'Scalability, interoperability, security, deployment', kicker: 'The same containers run per district; decode is the bottleneck, so we size by NVDEC — the arithmetic is in the Plan for Scale', outline: 11, notes: n(11),
    elements: [
      image(0.5, 1.3, 6.6, 4.1, 'scale', { caption: 'Edge / district / central topology for ~80,000 cameras (SCALE-PLAN §2)' }),
      bullets(7.3, 1.3, 5.55, 4.15, [
        'Edge / district ANPR appliances (2 × L4 ≈ 64 live / 144 keyframe cameras) → Kafka → Kubernetes core → Citus / Timescale + object storage; DR region',
        '≈ 2,800 T4-equivalents for a mixed 80 k profile (16 k road-facing at 5 fps + 64 k at keyframe rate) ≈ 700 two-GPU appliances',
        'Video stays in the district (160–320 Gbps aggregate); metadata + best crop to the centre ≈ 0.25–0.7 Gbps; low-bandwidth mode: keyframe-only, sub-streams, store-and-forward',
        'Storage tiers by retention 7 / 15 / 30 days; ~115 M sightings/day, day-partitioned; crops ~150 TB in object storage',
        'Interoperability: OpenAPI, bulk API, CSV templates, HMAC webhooks, catalogue field map, adapters; Kafka topics and NIC gateway adapters at scale',
        'Security: RBAC scoping in SQL, append-only audit trigger, SHA-256 evidence, TLS, forward-auth on media, API-key scopes; SSO + MFA, mTLS, HSM, WAF, SIEM at scale',
        'Deployment: Docker Compose today (`deploy.sh`, < 15 min on a fresh VM); Kubernetes, HA/DR, 99.9 % core availability target',
      ], { size: 10.5 }),
      table(0.6, 5.6, 12.2, [
        ['Topology (SCALE-PLAN §8.4)', 'Capex (₹ Cr)', 'Opex (₹ Cr / yr)', '5-year TCO (₹ Cr)', 'Per camera, 5 yr'],
        ['A · Metadata-only edge ANPR (Model 2 at scale) — recommended first', '≈ 195', '≈ 46', '≈ 425', '≈ ₹53,000'],
        ['B · Central recording at the state data centre (Model 4)', '≈ 315', '≈ 78', '≈ 705', '≈ ₹88,000'],
        ['B′ · Regional recording, central index', '≈ 243', '≈ 53', '≈ 508', '≈ ₹63,500'],
      ], [5.4, 1.6, 1.7, 1.8, 1.7], { size: 10 }),
    ],
  });

  // 13 Bonus items ---------------------------------------------------------------------------------------------
  slides.push({
    id: 'bonus', title: 'Bonus items delivered', kicker: 'None replaced a mandatory item; all of them are in the demo', outline: 12, notes: n(12),
    elements: [
      bullets(0.6, 1.4, 7.4, 5.55, [
        { t: 'Cross-camera vehicle tracking', sub: ['Route reconstruction with operator confirmation, plausibility (speed) flags, timeline and PDF'] },
        { t: 'Recorded evidence and clips', sub: ['“Play recording” from any alert or sighting; 30 s evidence clip; SHA-256 on every crop, frame, clip and report; GET /evidence/verify'] },
        { t: 'Privacy controls', sub: ['Retention job, purpose-limitation notice, export watermark, data scoping in SQL'] },
        { t: 'Private-society camera onboarding', sub: ['Own feed registered as ownership = private through the same form'] },
        { t: 'Integration-ready APIs', sub: ['OpenAPI, bulk onboarding API, HMAC webhooks, CSV templates, VAHAN / SARTHI mock adapters, eGujCop watchlist import mapping'] },
        { t: 'Vehicle / person counting · camera health, maintenance and AMC tracking · desktop notifications', sub: [] },
      ], { size: 12.5 }),
      image(8.25, 1.4, 4.55, 5.55, 'route', { frame: true, caption: 'Route of a sandbox-seen plate: markers, polyline, timeline and PDF' }),
    ],
  });

  // 14 Operational benefits ------------------------------------------------------------------------------------
  slides.push({
    id: 'benefits', title: 'Operational benefits for policing', kicker: 'For a control room: alert → acknowledge → route → PDF in under a minute; for a planner: gaps and ageing per department on one screen', outline: 13, notes: n(13),
    elements: [
      tile(0.6, 1.4, 3.95, 1.7, '3 days → 15 min', 'time to trace a vehicle across departments (≈ 6,000 traces/yr ≈ 70 FTE ≈ ₹14 Cr/yr)', { big: true }),
      tile(4.7, 1.4, 3.95, 1.7, 'p50 3.3 s', 'read → alert on screen, with crop and location, measured on 8 cameras', { big: true }),
      tile(8.8, 1.4, 3.95, 1.7, '26 → 1', 'one map, one health view and one gap report for every department', { big: true }),
      tile(0.6, 3.3, 3.95, 1.7, '₹24 k vs ₹1 L+', 'per camera: reuse the installed estate instead of new ANPR cameras (≈ ₹600 Cr avoided)', { big: true }),
      tile(4.7, 3.3, 3.95, 1.7, '₹0 licences', 'no per-camera VMS / analytics fee — ₹40–120 Cr/yr avoided at 80,000 cameras', { big: true }),
      tile(8.8, 3.3, 3.95, 1.7, 'SHA-256', 'evidence-grade exports: hashed, watermarked, audited; verify endpoint for the court', { big: true }),
      text(0.6, 5.3, 12.15, 1.6, 'Against Topology A’s ≈ ₹46 Cr/yr opex and ≈ ₹39 Cr/yr amortised capex, the quantified benefits (≈ ₹65 Cr/yr direct — operator hours, recovered vehicles, better camera budgets — plus avoided licence and capex) give payback inside the first two years of statewide operation, before counting deterrence and investigation quality (SCALE-PLAN §9).', { size: 12, color: C.ink, fill: C.soft, valign: 'middle' }),
    ],
  });

  // 15 Measured figures ----------------------------------------------------------------------------------------
  slides.push({
    id: 'measured', title: 'Measured figures — 12-core laptop, CPU only', kicker: 'Intel i5-1345U, 16 GB, Docker Desktop, no GPU · 5 Sept 2026 · first row: the 30 real organiser cameras; the rest: mock sandbox (50 catalogue cameras, 8 synthetic streams + own gate) · same numbers in README, acceptance log, HLD §14, Plan for Scale §1.1', outline: null,
    notes: 'These are the canonical measured figures — identical in the README, the acceptance log, the HLD §14 and the Plan for Scale §1.1. They were taken on a CPU-only laptop, so they are the floor: GPU cameras-per-T4 figures are filled from the VM soak. The point for the jury is that every planning value in the Plan for Scale is anchored in a number we measured ourselves.',
    elements: [
      table(0.6, 1.5, 12.15, [['Figure', 'Measured'], ...MEASURED], [4.4, 7.75], { size: 10.5 }),
    ],
  });

  // 16 Screenshots ---------------------------------------------------------------------------------------------
  const shots = [
    ['import', 'Sandbox catalogue import with onboarding time'], ['map', 'GIS map — the 30 organiser cameras with district and confidence layers'], ['wall9', '9-tile wall — live organiser sandbox cameras (WebRTC, H.265 and B-frame sources re-encoded)'],
    ['alertToast', 'Watchlist alert toast + panel'], ['route', 'Route with timeline and PDF'], ['reports', 'Output report (CSV / PDF with hashes)'],
  ];
  slides.push({
    id: 'screens', title: 'Screenshots — the demo journey', kicker: 'Captured from the running stack at the same version tag as the videos; jury credentials are in the submission form', outline: 14, notes: n(14),
    elements: shots.map(([key, cap], i) => image(0.6 + (i % 3) * 4.1, 1.35 + Math.floor(i / 3) * 2.85, 3.95, 2.45, key, { frame: true, caption: cap })),
  });

  // 17 Team ----------------------------------------------------------------------------------------------------
  slides.push({
    id: 'team', title: 'Team, links, Phase 2 readiness', kicker: 'Dynatech Consultancy · Category 2', outline: 15, notes: n(15),
    elements: [
      table(0.6, 1.4, 6.3, [['Role', 'Team member'], ...TEAM.map((m) => [m.role, m.name])], [4.2, 2.1], { size: 10.5 }),
      bullets(0.6, 4.75, 6.3, 2.2, [
        `Hosted demo: ${HOSTED_URL}`,
        `Repository: ${REPO_URL} (tag ${TAG})`,
        'Videos and Drive folder: links in the submission form',
        `Contact: ${TEAM_EMAIL}`,
      ], { size: 11.5 }),
      box(7.2, 1.4, 5.6, 3.1, 'Phase 2 readiness (HLD §18, PHASE2-RUNBOOK.md)', [
        'Bring the stack offline on a GPU laptop + the VM; onboard the venue’s cameras from Settings in < 5 min',
        'Live ANPR on all cameras split across VM and laptop; pre-index keeps running so the given plate is already in the database',
        'Route of the given plate live, with crops and confirmation, PDF in < 60 s',
        'Same acceptance checks (CONTRACT §13.3, 30 checks) re-run on site',
      ]),
      text(7.2, 4.75, 5.6, 2.2, 'Thank you.\n\nEverything shown is running software — same URL, same version tag, same screenshots in the HLD, the Plan for Scale and the README.', { size: 15, color: C.white, fill: C.navy, valign: 'middle', align: 'center' }),
    ],
  });

  return slides;
}

// ---------------------------------------------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------------------------------------------

// Text runs: `code` spans and **bold** in slide copy are rendered in the same face in both outputs (kept simple).
const plain = (s) => String(s).replace(/`/g, '');
const fit = (img, w, h) => {
  const r = Math.min(w / img.w, h / img.h);
  const fw = img.w * r;
  const fh = img.h * r;
  return { w: fw, h: fh, dx: (w - fw) / 2, dy: (h - fh) / 2 };
};

// ---------------------------------------------------------------------------------------------------------------
// PPTX renderer
// ---------------------------------------------------------------------------------------------------------------

async function renderPptx(slides, images, outPath) {
  const PptxGenJS = require('pptxgenjs');
  const pptx = new PptxGenJS();
  pptx.defineLayout({ name: 'SG169', width: W, height: H });
  pptx.layout = 'SG169';
  pptx.author = ORG;
  pptx.company = ORG;
  pptx.title = `${PRODUCT} — Solution Presentation (${VERSION})`;
  pptx.subject = 'Gujarat Police Innovation Challenge 2026 · CCTV Integration Hackathon · Phase 1';
  const imgData = {};
  for (const [k, v] of Object.entries(images)) imgData[k] = `image/png;base64,${(await readFile(v.path)).toString('base64')}`;

  const rect = pptx.ShapeType.rect;
  slides.forEach((s, idx) => {
    const slide = pptx.addSlide();
    const number = idx + 1;
    if (s.kind === 'title') {
      slide.background = { color: C.navy };
      slide.addShape(rect, { x: 0, y: H - 0.12, w: W, h: 0.12, fill: { color: C.blue }, line: { color: C.blue } });
    } else {
      slide.background = { color: C.white };
      slide.addShape(rect, { x: 0, y: 0, w: W, h: 1.1, fill: { color: C.navy }, line: { color: C.navy } });
      slide.addShape(rect, { x: 0, y: 1.1, w: W, h: 0.06, fill: { color: C.blue }, line: { color: C.blue } });
      slide.addText(s.title, { x: 0.55, y: 0.12, w: 12.2, h: 0.6, fontFace: FONT, fontSize: 24, bold: true, color: C.white, valign: 'middle', margin: 2 });
      if (s.kicker) slide.addText(s.kicker, { x: 0.55, y: 0.66, w: 12.2, h: 0.4, fontFace: FONT, fontSize: 11, color: 'C7D6F5', valign: 'middle', margin: 2, fit: 'shrink' });
      slide.addText(FOOTER, { x: 0.55, y: 7.1, w: 10.5, h: 0.3, fontFace: FONT, fontSize: 8.5, color: C.muted, margin: 0, valign: 'middle' });
      slide.addText(`${number} / ${slides.length}`, { x: 11.3, y: 7.1, w: 1.5, h: 0.3, fontFace: FONT, fontSize: 8.5, color: C.muted, align: 'right', margin: 0, valign: 'middle' });
    }
    if (s.notes) slide.addNotes(s.notes);

    for (const e of s.elements) {
      if (e.type === 'text') {
        const opts = {
          x: e.x, y: e.y, w: e.w, h: e.h, fontFace: FONT, fontSize: e.size ?? 12, bold: !!e.bold, italic: !!e.italic,
          color: e.color ?? C.ink, align: e.align ?? 'left', valign: e.valign ?? 'top', margin: 6, lineSpacingMultiple: e.line ?? 1.15,
        };
        if (e.fill) opts.fill = { color: e.fill };
        if (e.border) opts.line = { color: e.border, width: 1 };
        slide.addText(plain(e.text), opts);
      } else if (e.type === 'bullets') {
        const runs = [];
        for (const it of e.items) {
          const t = typeof it === 'string' ? it : it.t;
          runs.push({ text: plain(t), options: { bullet: e.noBullet ? false : { indent: 14 }, breakLine: true, bold: typeof it !== 'string' && it.sub?.length > 0, paraSpaceAfter: 4 } });
          if (typeof it !== 'string') for (const sub of it.sub ?? []) runs.push({ text: plain(sub), options: { bullet: { indent: 14 }, indentLevel: 1, breakLine: true, color: C.muted, paraSpaceAfter: 3 } });
        }
        slide.addText(runs, { x: e.x, y: e.y, w: e.w, h: e.h, fontFace: FONT, fontSize: e.size ?? 12, color: C.ink, valign: 'top', margin: 4, lineSpacingMultiple: e.line ?? 1.12, fit: 'shrink' });
      } else if (e.type === 'image') {
        const img = images[e.src];
        if (!img) fail(`unknown image key ${e.src}`);
        const f = fit(img, e.w, e.h);
        const capH = e.caption ? 0.3 : 0;
        const f2 = e.caption ? fit(img, e.w, e.h - capH) : f;
        if (e.frame) slide.addShape(rect, { x: e.x + f2.dx - 0.03, y: e.y + f2.dy - 0.03, w: f2.w + 0.06, h: f2.h + 0.06, fill: { color: C.white }, line: { color: C.line, width: 1 }, shadow: { type: 'outer', blur: 4, offset: 2, angle: 45, color: '000000', opacity: 0.25 } });
        slide.addImage({ data: imgData[e.src], x: e.x + f2.dx, y: e.y + f2.dy, w: f2.w, h: f2.h });
        if (e.caption) slide.addText(e.caption, { x: e.x, y: e.y + e.h - capH, w: e.w, h: capH, fontFace: FONT, fontSize: 9, color: C.muted, align: 'center', valign: 'middle', margin: 0, italic: true });
      } else if (e.type === 'table') {
        const rows = e.rows.map((r, ri) => r.map((cell) => {
          const c = typeof cell === 'string' ? { text: cell } : cell;
          const o = { fontFace: FONT, fontSize: e.size ?? 11, color: ri === 0 ? C.white : (c.color ?? C.ink), bold: ri === 0 || !!c.bold, fill: { color: ri === 0 ? C.navy : (c.fill ?? (ri % 2 === 0 ? 'F7F9FD' : C.white)) }, valign: 'top', margin: 4 };
          return { text: plain(c.text), options: o };
        }));
        slide.addTable(rows, { x: e.x, y: e.y, w: e.w, colW: e.colW, border: { type: 'solid', pt: 0.5, color: C.line }, autoPage: false });
      } else if (e.type === 'box') {
        const titleH = e.compact ? 0.3 : 0.4;
        slide.addShape(rect, { x: e.x, y: e.y, w: e.w, h: e.h, fill: { color: C.white }, line: { color: C.line, width: 1 } });
        slide.addShape(rect, { x: e.x, y: e.y, w: e.w, h: titleH, fill: { color: C.blue }, line: { color: C.blue } });
        slide.addText(e.title, { x: e.x, y: e.y, w: e.w, h: titleH, fontFace: FONT, fontSize: e.compact ? 10.5 : 12.5, bold: true, color: C.white, valign: 'middle', margin: 6 });
        const size = e.compact ? 9.5 : 11;
        const runs = e.items.map((t) => ({ text: plain(t), options: { bullet: e.compact ? false : { indent: 10 }, breakLine: true, paraSpaceAfter: e.small ? 2 : 3 } }));
        slide.addText(runs, { x: e.x, y: e.y + titleH, w: e.w, h: e.h - titleH, fontFace: FONT, fontSize: size, color: C.ink, valign: 'top', margin: 5, lineSpacingMultiple: 1.1, fit: 'shrink' });
      } else if (e.type === 'tile') {
        slide.addShape(rect, { x: e.x, y: e.y, w: e.w, h: e.h, fill: { color: C.soft }, line: { color: C.soft } });
        slide.addShape(rect, { x: e.x, y: e.y, w: 0.08, h: e.h, fill: { color: C.blue }, line: { color: C.blue } });
        const vh = TILE.valueH(e);
        slide.addText(e.value, { x: e.x + 0.12, y: e.y + 0.08, w: e.w - 0.2, h: vh, fontFace: FONT, fontSize: TILE.valueSize(e), bold: true, color: C.navy, valign: 'middle', margin: 2, fit: 'shrink' });
        slide.addText(e.label, { x: e.x + 0.12, y: e.y + vh + 0.06, w: e.w - 0.2, h: e.h - vh - 0.12, fontFace: FONT, fontSize: TILE.labelSize(e), color: C.muted, valign: 'top', margin: 2, fit: 'shrink' });
      } else if (e.type === 'flow') {
        const gap = 0.12;
        const sw = (e.w - gap * (e.steps.length - 1)) / e.steps.length;
        e.steps.forEach((st, i) => {
          slide.addText(st, { x: e.x + i * (sw + gap), y: e.y, w: sw, h: e.h, shape: pptx.ShapeType.chevron, fill: { color: i === e.steps.length - 1 ? C.blue : C.navy }, line: { color: C.white, width: 0.5 }, fontFace: FONT, fontSize: 9.5, color: C.white, align: 'center', valign: 'middle', margin: 8, fit: 'shrink' });
        });
      } else if (e.type === 'arrow') {
        slide.addShape(pptx.ShapeType.rightArrow, { x: e.x, y: e.y, w: e.w, h: e.h, fill: { color: C.blue }, line: { color: C.blue } });
        if (e.label) slide.addText(e.label, { x: e.x - 0.3, y: e.y + e.h + 0.02, w: e.w + 0.6, h: 0.55, fontFace: FONT, fontSize: 8.5, color: C.muted, align: 'center', valign: 'top', margin: 0 });
      }
    }
  });
  await pptx.writeFile({ fileName: outPath });
}

// ---------------------------------------------------------------------------------------------------------------
// HTML twin (PDF export + per-slide screenshots)
// ---------------------------------------------------------------------------------------------------------------

const escapeHtml = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const inl = (s) => escapeHtml(plain(s)).replace(/\n/g, '<br>');
const PX = 96;
const px = (inches) => `${(inches * PX).toFixed(1)}px`;
const pt = (points) => `${(points * PX / 72).toFixed(1)}px`;
const pos = (e) => `left:${px(e.x)};top:${px(e.y)};width:${px(e.w)};height:${px(e.h)};`;

function twinCss() {
  return `
@page { size: ${W}in ${H}in; margin: 0; }
html, body { margin: 0; padding: 0; background: #666; }
body { font-family: ${FONT}, Helvetica, sans-serif; color: #${C.ink}; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.slide { position: relative; width: ${px(W)}; height: ${px(H)}; overflow: hidden; background: #fff; page-break-after: always; margin: 0 auto 12px auto; }
.slide.title { background: #${C.navy}; }
.slide.title .bar { position: absolute; left: 0; bottom: 0; width: 100%; height: ${px(0.12)}; background: #${C.blue}; }
.band { position: absolute; left: 0; top: 0; width: 100%; height: ${px(1.1)}; background: #${C.navy}; border-bottom: ${px(0.06)} solid #${C.blue}; }
.band h1 { position: absolute; left: ${px(0.55)}; top: ${px(0.12)}; width: ${px(12.2)}; height: ${px(0.6)}; margin: 0; font-size: ${pt(24)}; line-height: ${px(0.6)}; color: #fff; font-weight: 700; white-space: nowrap; overflow: hidden; }
.band .kicker { position: absolute; left: ${px(0.55)}; top: ${px(0.66)}; width: ${px(12.2)}; height: ${px(0.4)}; font-size: ${pt(11)}; line-height: ${px(0.4)}; color: #C7D6F5; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.footer { position: absolute; left: ${px(0.55)}; top: ${px(7.1)}; width: ${px(10.5)}; height: ${px(0.3)}; font-size: ${pt(8.5)}; line-height: ${px(0.3)}; color: #${C.muted}; }
.num { position: absolute; left: ${px(11.3)}; top: ${px(7.1)}; width: ${px(1.5)}; height: ${px(0.3)}; font-size: ${pt(8.5)}; line-height: ${px(0.3)}; color: #${C.muted}; text-align: right; }
.el { position: absolute; box-sizing: border-box; overflow: hidden; }
.text { padding: ${pt(6)}; white-space: pre-wrap; line-height: 1.15; }
.text.mid { display: flex; align-items: center; }
.text.mid > span { width: 100%; }
.bul { padding: ${pt(4)}; }
.bul ul { margin: 0; padding-left: ${pt(14)}; }
.bul ul.nobullet { list-style: none; padding-left: 0; }
.bul li { margin: 0 0 ${pt(4)} 0; line-height: 1.12; }
.bul li.head { font-weight: 700; }
.bul li ul { margin-top: ${pt(2)}; }
.bul li ul li { font-weight: 400; color: #${C.muted}; margin-bottom: ${pt(3)}; }
.img { display: flex; flex-direction: column; align-items: center; justify-content: center; }
.img .ph { display: flex; align-items: center; justify-content: center; flex: 1 1 auto; width: 100%; min-height: 0; }
.img img { max-width: 100%; max-height: 100%; object-fit: contain; }
.img.frame img { border: 1px solid #${C.line}; box-shadow: 2px 2px 6px rgba(0,0,0,.25); background: #fff; }
.img .cap { flex: 0 0 ${px(0.3)}; line-height: ${px(0.3)}; font-size: ${pt(9)}; color: #${C.muted}; font-style: italic; text-align: center; width: 100%; }
table.t { border-collapse: collapse; table-layout: fixed; }
table.t th, table.t td { border: 0.5pt solid #${C.line}; padding: ${pt(4)}; vertical-align: top; text-align: left; line-height: 1.15; overflow-wrap: break-word; }
table.t th { background: #${C.navy}; color: #fff; font-weight: 700; }
table.t tr:nth-child(odd) td { background: #F7F9FD; }
.box { border: 1px solid #${C.line}; background: #fff; }
.box .bt { background: #${C.blue}; color: #fff; font-weight: 700; padding: 0 ${pt(6)}; display: flex; align-items: center; }
.box ul { margin: 0; padding: ${pt(5)} ${pt(5)} ${pt(5)} ${pt(15)}; line-height: 1.1; }
.box ul.plain { list-style: none; padding-left: ${pt(5)}; }
.box li { margin-bottom: ${pt(3)}; }
.tile { background: #${C.soft}; border-left: ${px(0.08)} solid #${C.blue}; padding: ${px(0.08)} ${px(0.1)} ${px(0.06)} ${px(0.12)}; }
.tile .v { font-weight: 700; color: #${C.navy}; line-height: 1.1; white-space: nowrap; }
.tile .l { color: #${C.muted}; line-height: 1.15; margin-top: ${pt(4)}; }
.flow { display: flex; gap: ${px(0.12)}; }
.flow .step { flex: 1 1 0; background: #${C.navy}; color: #fff; font-size: ${pt(9.5)}; display: flex; align-items: center; justify-content: center; text-align: center; padding: 0 ${pt(10)}; line-height: 1.15; clip-path: polygon(0 0, calc(100% - 14px) 0, 100% 50%, calc(100% - 14px) 100%, 0 100%, 14px 50%); }
.flow .step:last-child { background: #${C.blue}; }
.arrow { background: #${C.blue}; clip-path: polygon(0 30%, 70% 30%, 70% 0, 100% 50%, 70% 100%, 70% 70%, 0 70%); }
.arrowlabel { position: absolute; font-size: ${pt(8.5)}; color: #${C.muted}; text-align: center; line-height: 1.15; }
.ovf { outline: 3px solid #E11D48 !important; }
`;
}

function twinElement(e, images, imgData) {
  if (e.type === 'text') {
    const mid = e.valign === 'middle';
    const st = `${pos(e)}font-size:${pt(e.size ?? 12)};color:#${e.color ?? C.ink};text-align:${e.align ?? 'left'};${e.fill ? `background:#${e.fill};` : ''}${e.border ? `border:1px solid #${e.border};` : ''}${e.bold ? 'font-weight:700;' : ''}${e.italic ? 'font-style:italic;' : ''}${e.line ? `line-height:${e.line};` : ''}`;
    return `<div class="el text${mid ? ' mid' : ''}" data-id="${e.id ?? ''}" style="${st}"><span>${inl(e.text)}</span></div>`;
  }
  if (e.type === 'bullets') {
    const li = e.items.map((it) => {
      if (typeof it === 'string') return `<li>${inl(it)}</li>`;
      const sub = it.sub?.length ? `<ul>${it.sub.map((s) => `<li>${inl(s)}</li>`).join('')}</ul>` : '';
      return `<li class="${it.sub?.length ? 'head' : ''}">${inl(it.t)}${sub}</li>`;
    }).join('');
    return `<div class="el bul" style="${pos(e)}font-size:${pt(e.size ?? 12)};${e.line ? `line-height:${e.line};` : ''}"><ul class="${e.noBullet ? 'nobullet' : ''}">${li}</ul></div>`;
  }
  if (e.type === 'image') {
    return `<div class="el img${e.frame ? ' frame' : ''}" style="${pos(e)}"><div class="ph"><img src="${imgData[e.src]}"></div>${e.caption ? `<div class="cap">${inl(e.caption)}</div>` : ''}</div>`;
  }
  if (e.type === 'table') {
    const cols = e.colW.map((w) => `<col style="width:${px(w)}">`).join('');
    const rows = e.rows.map((r, ri) => `<tr>${r.map((cell) => {
      const c = typeof cell === 'string' ? { text: cell } : cell;
      const tag = ri === 0 ? 'th' : 'td';
      const st = ri === 0 ? '' : `${c.fill ? `background:#${c.fill} !important;` : ''}${c.color ? `color:#${c.color};` : ''}${c.bold ? 'font-weight:700;' : ''}`;
      return `<${tag} style="${st}">${inl(c.text)}</${tag}>`;
    }).join('')}</tr>`).join('');
    return `<div class="el" style="${pos({ ...e, h: H - e.y - 0.45 })}font-size:${pt(e.size ?? 11)};"><table class="t" style="width:${px(e.w)}"><colgroup>${cols}</colgroup>${rows}</table></div>`;
  }
  if (e.type === 'box') {
    const titleH = e.compact ? 0.3 : 0.4;
    const size = e.compact ? 9.5 : 11;
    return `<div class="el box" style="${pos(e)}"><div class="bt" style="height:${px(titleH)};font-size:${pt(e.compact ? 10.5 : 12.5)}">${inl(e.title)}</div><ul class="${e.compact ? 'plain' : ''}" style="font-size:${pt(size)}">${e.items.map((t) => `<li>${inl(t)}</li>`).join('')}</ul></div>`;
  }
  if (e.type === 'tile') {
    return `<div class="el tile" style="${pos(e)}"><div class="v" style="font-size:${pt(TILE.valueSize(e))}">${inl(e.value)}</div><div class="l" style="font-size:${pt(TILE.labelSize(e))}">${inl(e.label)}</div></div>`;
  }
  if (e.type === 'flow') {
    return `<div class="el flow" style="${pos(e)}">${e.steps.map((s) => `<div class="step">${inl(s)}</div>`).join('')}</div>`;
  }
  if (e.type === 'arrow') {
    const label = e.label ? `<div class="arrowlabel" style="left:${px(e.x - 0.3)};top:${px(e.y + e.h + 0.02)};width:${px(e.w + 0.6)};height:${px(0.55)}">${inl(e.label)}</div>` : '';
    return `<div class="el arrow" style="${pos(e)}"></div>${label}`;
  }
  return '';
}

async function renderTwin(slides, images, buildDir, outPdf, browser) {
  const imgData = {};
  for (const [k, v] of Object.entries(images)) imgData[k] = `data:image/png;base64,${(await readFile(v.path)).toString('base64')}`;
  const body = slides.map((s, idx) => {
    const chrome = s.kind === 'title'
      ? '<div class="bar"></div>'
      : `<div class="band"><h1>${inl(s.title)}</h1>${s.kicker ? `<div class="kicker">${inl(s.kicker)}</div>` : ''}</div><div class="footer">${inl(FOOTER)}</div><div class="num">${idx + 1} / ${slides.length}</div>`;
    return `<section class="slide${s.kind === 'title' ? ' title' : ''}" id="s${idx + 1}">${chrome}${s.elements.map((e) => twinElement(e, images, imgData)).join('')}</section>`;
  }).join('\n');
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${PRODUCT} — Solution Presentation</title><style>${twinCss()}</style></head><body>${body}</body></html>`;
  const htmlPath = join(buildDir, 'presentation.html');
  await writeFile(htmlPath, html, 'utf8');

  const page = await browser.newPage({ viewport: { width: Math.round(W * PX) + 40, height: Math.round(H * PX) + 40 }, deviceScaleFactor: 1 });
  await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready);
  // Overflow report: any absolutely positioned element whose content is taller than its box.
  const overflow = await page.evaluate(() => {
    const out = [];
    document.querySelectorAll('.slide').forEach((s, si) => {
      s.querySelectorAll('.el').forEach((el) => {
        const inner = el.classList.contains('img') ? null : el;
        if (!inner) return;
        const table = el.querySelector('table');
        const contentH = table ? table.getBoundingClientRect().height : el.scrollHeight;
        const boxH = table ? (parseFloat(el.style.height)) : el.clientHeight;
        if (contentH > boxH + 1) { el.classList.add('ovf'); out.push(`slide ${si + 1}: ${el.className.replace('el ', '')} at ${el.style.left}/${el.style.top} needs ${Math.round(contentH)}px, has ${Math.round(boxH)}px`); }
      });
    });
    return out;
  });
  const slidesDir = join(buildDir, 'slides');
  await mkdir(slidesDir, { recursive: true });
  const shots = [];
  for (let i = 0; i < slides.length; i += 1) {
    const el = page.locator(`#s${i + 1}`);
    const p = join(slidesDir, `slide-${String(i + 1).padStart(2, '0')}.png`);
    await el.screenshot({ path: p });
    shots.push(p);
  }
  await page.evaluate(() => document.querySelectorAll('.ovf').forEach((el) => el.classList.remove('ovf')));
  let pdf = null;
  if (outPdf) {
    await page.addStyleTag({ content: 'html, body { background: #fff; } .slide { margin: 0; }' });
    pdf = await page.pdf({ path: outPdf, width: `${W}in`, height: `${H}in`, printBackground: true, margin: { top: 0, right: 0, bottom: 0, left: 0 }, preferCSSPageSize: true });
  }
  await page.close();
  return { overflow, shots, pdf, htmlPath };
}

// ---------------------------------------------------------------------------------------------------------------
// Structural check of the written PPTX (slide count, notes, media) without PowerPoint
// ---------------------------------------------------------------------------------------------------------------

async function inspectPptx(path) {
  const buf = await readFile(path);
  // Count central-directory entries by name prefix (zip is store/deflate; names are plain in the directory).
  const names = [];
  let i = buf.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]));
  if (i < 0) fail('pptx is not a zip');
  const cdOffset = buf.readUInt32LE(i + 16);
  let p = cdOffset;
  while (p + 46 <= buf.length && buf.readUInt32LE(p) === 0x02014b50) {
    const nameLen = buf.readUInt16LE(p + 28);
    const extraLen = buf.readUInt16LE(p + 30);
    const commentLen = buf.readUInt16LE(p + 32);
    names.push(buf.toString('utf8', p + 46, p + 46 + nameLen));
    p += 46 + nameLen + extraLen + commentLen;
  }
  return {
    slides: names.filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n)).length,
    notes: names.filter((n) => /^ppt\/notesSlides\/notesSlide\d+\.xml$/.test(n)).length,
    media: names.filter((n) => /^ppt\/media\//.test(n)).length,
  };
}

// ---------------------------------------------------------------------------------------------------------------

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const outline = await readOutlineNotes();
  const slides = buildSlides(outline);
  const buildDir = join(opts.out, '.build');
  await mkdir(buildDir, { recursive: true });
  const pw = await loadPlaywright();
  const { browser, label } = await launchChromium(pw.chromium);
  log(`browser: ${label}; ${slides.length} slides; notes for outline rows ${Object.keys(outline.notes).join(',')}`);
  let result;
  try {
    const images = await prepareImages(browser, buildDir);
    log(`images prepared: ${Object.keys(images).length} (${Object.keys(DIAGRAMS).length} diagrams rasterised from SVG)`);
    const pptxPath = join(opts.out, 'Sentinel-Gujarat-Presentation.pptx');
    await renderPptx(slides, images, pptxPath);
    const info = await inspectPptx(pptxPath);
    const size = (await stat(pptxPath)).size;
    log(`${pptxPath} (${(size / 1024 / 1024).toFixed(1)} MB; ${info.slides} slides, ${info.notes} notes pages, ${info.media} media files)`);
    if (info.slides !== slides.length) fail(`pptx has ${info.slides} slides, expected ${slides.length}`);
    const pdfPath = opts.pdf ? join(opts.out, 'Sentinel-Gujarat-Presentation.pdf') : null;
    result = await renderTwin(slides, images, buildDir, pdfPath, browser);
    if (pdfPath) {
      const pages = countPdfPages(result.pdf);
      log(`${pdfPath} (${pages} pages, ${((await stat(pdfPath)).size / 1024 / 1024).toFixed(1)} MB) - rendered from the HTML twin ${basename(result.htmlPath)}`);
      if (pages !== slides.length) fail(`pdf has ${pages} pages, expected ${slides.length}`);
    }
    log(`slide previews: ${result.shots.length} PNG(s) in ${join(buildDir, 'slides')}`);
  } finally {
    await browser.close();
  }
  if (result.overflow.length) {
    log(`layout warnings (content taller than its box in the HTML twin; check the same slide in PowerPoint):`);
    for (const o of result.overflow) log(`  ${o}`);
  } else {
    log('layout: no overflow detected in the HTML twin');
  }
  const placeholders = [HOSTED_URL, TEAM_EMAIL, ...TEAM.map((m) => m.name)].filter((v) => /\[FILL/.test(v));
  if (placeholders.length) log(`placeholders still to fill (SG_HOSTED_URL, SG_TEAM_EMAIL, SG_TEAM): ${[...new Set(placeholders)].join(', ')}`);
}

main().catch((err) => fail(err?.stack || String(err)));
