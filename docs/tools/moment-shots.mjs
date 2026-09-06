#!/usr/bin/env node
// Sentinel Gujarat - the eleven "moment" screenshots named in docs/PRESENTATION-OUTLINE.md and
// docs/screenshots/README.md, taken from a running stack so that the presentation, the HLD and the
// README all use the same images (submission-checklist §3.5 consistency item).
//
//   node docs/tools/moment-shots.mjs [only=05-route,10-alert-toast]      (from the repository root)
//
// Env: SG_BASE (default http://localhost), SG_OUT (default docs/screenshots), SG_USER / SG_PASSWORD
//      (default jury_admin / Sentinel@Admin2026), SG_CHANNEL (chrome | msedge; a channel with H.264
//      and a PDF viewer is needed - the bundled Chromium has neither).
//
// Output: docs/screenshots/<nn>-<name>.png at 1920x1080 plus moment-shots.json (what each shot
// contains: playing tiles, toast delay, PDF page used). Exit code 1 when any shot failed.
//
// The two PDF shots (06 route-report footer with the evidence hash, 11 output-report cover) are
// rendered by downloading the PDF from the API and opening it in the browser's PDF viewer; the
// files are kept under docs/screenshots/.build/ (git-ignored).
//
// Shot 07 (import summary) presses "Import from catalogue" on the running stack: the import is an
// idempotent upsert, so on a database that already holds the catalogue it reads "50 fetched · 0
// added · 0 updated · 50 unchanged" - the "50 added" variant only exists on a fresh database, which is why the
// README asks for that shot to be taken at the first import on the hosted VM.
//
// Playwright is taken from frontend/node_modules (the frontend's dev dependency) or docs/tools/node_modules.

import { mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '..', '..');
const require = createRequire(import.meta.url);

const BASE = process.env.SG_BASE ?? 'http://localhost';
const OUT = resolve(process.env.SG_OUT ?? join(repoRoot, 'docs', 'screenshots'));
const BUILD = join(OUT, '.build');
const USER = process.env.SG_USER ?? 'jury_admin';
const PASSWORD = process.env.SG_PASSWORD ?? 'Sentinel@Admin2026';
const CHANNEL = process.env.SG_CHANNEL ?? 'chrome';
const ONLY = process.argv[2]?.startsWith('only=') ? new Set(process.argv[2].slice(5).split(',')) : null;

mkdirSync(BUILD, { recursive: true });
const settle = (ms) => new Promise((r) => setTimeout(r, ms));
const info = {};
const failures = [];

function loadPlaywright() {
  for (const candidate of [join(repoRoot, 'frontend', 'node_modules', 'playwright'), 'playwright']) {
    try {
      return require(candidate);
    } catch {
      /* try the next location */
    }
  }
  throw new Error('playwright not found: run `npm ci` in frontend/ (or `npm i playwright` in docs/tools)');
}

// The login route is rate-limited per IP (10/min, CONTRACT §2.1); other checks running from the same
// host can exhaust the window, so a 429 is retried after a pause instead of failing the whole run.
async function apiLogin(username, password) {
  for (let attempt = 1; ; attempt += 1) {
    const r = await fetch(`${BASE}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) });
    if (r.ok) return (await r.json()).access_token;
    if (r.status === 429 && attempt < 8) {
      console.log(`login rate-limited (429), retrying in 20 s (${attempt}/8)`);
      await settle(20000);
      continue;
    }
    throw new Error(`login as ${username} failed: HTTP ${r.status}`);
  }
}
async function api(method, path, token, json) {
  const r = await fetch(`${BASE}${path}`, { method, headers: { Authorization: `Bearer ${token}`, ...(json ? { 'Content-Type': 'application/json' } : {}) }, body: json ? JSON.stringify(json) : undefined });
  return { status: r.status, data: await r.json().catch(() => null) };
}
async function download(path, token, file) {
  const r = await fetch(`${BASE}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!r.ok) throw new Error(`GET ${path} -> HTTP ${r.status}`);
  const buf = Buffer.from(await r.arrayBuffer());
  writeFileSync(file, buf);
  return { bytes: buf.length, sha256: r.headers.get('x-sentinel-sha256'), pages: countPdfPages(buf) };
}
// Page count of a PDF: "/Type /Page" objects (not "/Pages"). Good enough for reportlab output.
function countPdfPages(buf) {
  const m = buf.toString('latin1').match(/\/Type\s*\/Page(?![s\w])/g);
  return m ? m.length : 1;
}

async function login(page, shot) {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('input[autocomplete="username"]', { timeout: 30000 });
  await settle(1500);
  if (shot) await page.screenshot({ path: join(OUT, '01-login.png') });
  await page.fill('input[autocomplete="username"]', USER);
  await page.fill('input[autocomplete="current-password"]', PASSWORD);
  for (let attempt = 1; ; attempt += 1) {
    await page.click('button[type="submit"]');
    try {
      await page.waitForURL(/\/dashboard/, { timeout: 30000 });
      break;
    } catch (e) {
      if (attempt >= 4) throw e;
      console.log(`UI login did not reach /dashboard (rate limit or slow API), retrying in 20 s (${attempt}/4)`);
      await settle(20000);
    }
  }
  await settle(1500);
}

async function videoState(page) {
  return page.evaluate(() => {
    const vids = [...document.querySelectorAll('video')].map((v) => ({ t: Number(v.currentTime.toFixed(1)), w: v.videoWidth }));
    const caps = [...document.querySelectorAll('.sg-player-caption')].map((c) => c.textContent?.replace(/\s+/g, ' ').trim());
    return { playing: vids.filter((v) => v.t > 0.5 && v.w > 2).length, tiles: vids.length, caps };
  });
}

async function main() {
  const { chromium } = loadPlaywright();
  const adminTok = await apiLogin(USER, PASSWORD);
  const cams = (await api('GET', '/api/cameras?source=sandbox&page_size=100&sort=external_id&order=asc', adminTok)).data?.items ?? [];
  const idOf = (ext) => cams.find((c) => c.external_id === String(ext))?.id;
  const platesFile = join(repoRoot, 'media', 'synthetic', 'plates.json');
  const plates = existsSync(platesFile) ? JSON.parse(await (await import('node:fs/promises')).readFile(platesFile, 'utf8')) : null;
  const running = new Set([1, 2, 3, 4, 5, 6, 7, 8]);
  const filler = plates?.plates.find((p) => !p.anchor && p.cameras.filter((c) => running.has(c)).length >= 2) ?? plates?.plates.find((p) => !p.anchor) ?? null;

  const browser = await chromium.launch({ channel: CHANNEL, args: ['--autoplay-policy=no-user-gesture-required'] });
  const userAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36';
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1, locale: 'en-IN', timezoneId: 'Asia/Kolkata', permissions: ['notifications'], userAgent });
  // OSM answers 403 to automated browsers; fetch the few tiles the documents need with an identifying UA.
  await context.route(/tile\.openstreetmap\.org/, async (route) => {
    try {
      const r = await fetch(route.request().url(), { headers: { 'User-Agent': 'SentinelGujarat-docs-screenshots/1.0 (+https://github.com/dynatech-consultancy/sentinel-gujarat)' } });
      await route.fulfill({ status: r.status, headers: { 'content-type': r.headers.get('content-type') ?? 'image/png' }, body: Buffer.from(await r.arrayBuffer()) });
    } catch {
      await route.abort();
    }
  });
  const page = await context.newPage();
  await login(page, !ONLY || ONLY.has('01-login'));

  const shot = async (key, path, opts = {}) => {
    if (ONLY && !ONLY.has(key)) return;
    try {
      if (path) await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded', timeout: 90000 });
      // Pages render antd skeletons while their queries run (the route page can take a while on a
      // busy database); never capture a skeleton.
      await page.waitForFunction(() => document.querySelectorAll('.ant-skeleton, .ant-spin-spinning').length === 0, null, { timeout: 120000 }).catch(() => {});
      await settle(opts.wait ?? 3000);
      if (opts.before) await opts.before(page);
      await page.screenshot({ path: join(OUT, `${key}.png`), fullPage: Boolean(opts.fullPage) });
      console.log('✓', key);
    } catch (e) {
      failures.push(key);
      console.error('✗', key, e.message.split('\n')[0]);
    }
  };
  const setLayer = async (p, label, checked) => {
    const box = p.getByRole('group', { name: 'Map layers' }).getByRole('checkbox', { name: label });
    if ((await box.count()) && (await box.isChecked()) !== checked) await box.click();
  };

  await shot('03-dashboard', '/dashboard', { wait: 5000 });
  await shot('02-map-districts', '/map', {
    wait: 5000,
    before: async (p) => {
      await setLayer(p, /^District boundaries/i, true);
      await setLayer(p, /^Department ring/i, true);
      await settle(2500);
    },
  });
  await shot('08-map-layers', '/map', {
    wait: 5000,
    before: async (p) => {
      await setLayer(p, /^District boundaries/i, true);
      await setLayer(p, /^Department ring/i, true);
      await setLayer(p, /^Coverage circles/i, true);
      await setLayer(p, /^Points of interest/i, true);
      await p.waitForFunction(() => document.querySelectorAll('.sg-map-panel .ant-spin').length === 0, null, { timeout: 60000 }).catch(() => {});
      await settle(3000);
    },
  });
  await shot('04-camera-live-reads', idOf(1) ? `/cameras/${idOf(1)}` : '/cameras', {
    wait: 3000,
    before: async (p) => {
      // Wait for the WebRTC tile to play and the crop thumbnails to arrive (both come from /mtx and /media
      // through the session cookie and can lag behind the page on a busy host).
      let st = null;
      for (let i = 0; i < 45; i++) {
        st = await p.evaluate(() => {
          const v = document.querySelector('video');
          const imgs = [...document.querySelectorAll('.sg-table img, .ant-table img')];
          return { playing: Boolean(v && v.currentTime > 0.5 && v.videoWidth > 2), crops: imgs.length, loaded: imgs.filter((i) => i.complete && i.naturalWidth > 0).length, caption: document.querySelector('.sg-player-caption')?.textContent?.trim() ?? null };
        });
        if (st.playing && st.crops > 0 && st.loaded === st.crops) break;
        await settle(1000);
      }
      info['04-camera-live-reads'] = st;
    },
  });
  await shot('05-route', '/vehicles/GJ27XY3456/route', { wait: 7000 });
  await shot('07-import-summary', '/cameras/import', {
    wait: 3000,
    before: async (p) => {
      await p.getByRole('button', { name: 'Import from catalogue' }).click();
      await p.getByText(/fetched/i).first().waitFor({ timeout: 120000 });
      await settle(1200);
      info['07-import-summary'] = { summary: await p.getByText(/fetched/i).first().textContent() };
    },
  });
  await shot('09-wall-9', '/wall', {
    wait: 3000,
    fullPage: true, // the 3 x 3 grid is taller than 1080 px
    before: async (p) => {
      await p.locator('.ant-segmented-item-label', { hasText: /^9$/ }).click();
      await settle(600);
      const auto = p.getByRole('button', { name: 'Auto-fill' });
      if (await auto.count()) await auto.click();
      let st = null;
      for (let i = 0; i < 45; i++) {
        st = await videoState(p);
        if (st.playing >= 8) break;
        await settle(1000);
      }
      info['09-wall-9'] = st;
    },
  });
  await shot('12-systems-unaffected', '/health', { wait: 4000 });

  // 10 - alert toast: put a filler plate (present on the running loops, not on the seeded watchlist) on the
  // watchlist, wait for the toast on the dashboard, then remove the entry again so the seed stays as shipped.
  if ((!ONLY || ONLY.has('10-alert-toast')) && filler) {
    try {
      const existing = (await api('GET', `/api/watchlist?q=${filler.plate}&is_active=all`, adminTok)).data?.items ?? [];
      for (const w of existing) await api('DELETE', `/api/watchlist/${w.id}`, adminTok);
      await page.goto(`${BASE}/alerts`, { waitUntil: 'domcontentloaded' });
      await settle(3000);
      const t0 = Date.now();
      const add = await api('POST', '/api/watchlist', adminTok, { entity_type: 'vehicle', plate: filler.display, name: 'Moment shot (filler plate from plates.json)', reason: 'stolen', priority: 'critical', source: 'manual' });
      let seen = false;
      for (let i = 0; i < 260; i++) {
        if ((await page.locator('.ant-notification-notice', { hasText: filler.display }).count()) > 0) { seen = true; break; }
        await settle(500);
      }
      await settle(400);
      await page.screenshot({ path: join(OUT, '10-alert-toast.png') });
      info['10-alert-toast'] = { seen, after_s: Math.round((Date.now() - t0) / 1000), plate: filler.display };
      console.log(seen ? '✓' : '✗', '10-alert-toast', info['10-alert-toast']);
      if (!seen) failures.push('10-alert-toast');
      if (add.status === 201 && add.data?.id) await api('DELETE', `/api/watchlist/${add.data.id}`, adminTok);
    } catch (e) { failures.push('10-alert-toast'); console.error('✗ 10-alert-toast', e.message); }
  } else if (!ONLY || ONLY.has('10-alert-toast')) {
    failures.push('10-alert-toast');
    console.error('✗ 10-alert-toast: media/synthetic/plates.json not found (run the synth container first)');
  }

  // 06 / 11 - PDF pages through the browser's PDF viewer (Chrome / Edge channel).
  // `view` is the Chrome PDF-viewer fragment after the page: 'zoom=125' shows the top of the page,
  // 'zoom=200,0,650' zooms 2x and scrolls (viewer pixels) to the lower part so the footer (evidence hash, watermark) is legible.
  const pdfShot = async (key, apiPath, file, pageNo, view = 'zoom=125') => {
    if (ONLY && !ONLY.has(key)) return;
    try {
      const meta = await download(apiPath, adminTok, file);
      const target = pageNo === 'last' ? meta.pages : pageNo;
      const p = await context.newPage();
      await p.goto(`${pathToFileURL(file).href}#page=${target}&toolbar=0&${view}`, { waitUntil: 'load' });
      await settle(4000);
      await p.screenshot({ path: join(OUT, `${key}.png`) });
      await p.close();
      info[key] = { file, page: target, pages: meta.pages, bytes: meta.bytes, sha256: meta.sha256 };
      console.log('✓', key, `page ${target}/${meta.pages}`);
    } catch (e) {
      failures.push(key);
      console.error('✗', key, e.message.split('\n')[0]);
    }
  };
  await pdfShot('06-report-hash', '/api/vehicles/GJ27XY3456/route.pdf', join(BUILD, 'route_GJ27XY3456.pdf'), 1, 'zoom=200,0,650');
  const to = new Date();
  const from = new Date(to.getTime() - 2 * 3600 * 1000);
  await pdfShot('11-output-report', `/api/reports/detections?format=pdf&from=${from.toISOString()}&to=${to.toISOString()}`, join(BUILD, 'detections_last2h.pdf'), 1);

  await browser.close();
  // An `only=` run updates the entries it re-took and keeps the rest of the previous record.
  const jsonPath = join(OUT, 'moment-shots.json');
  let previous = {};
  if (ONLY && existsSync(jsonPath)) {
    try { previous = JSON.parse(await (await import('node:fs/promises')).readFile(jsonPath, 'utf8')).info ?? {}; } catch { previous = {}; }
  }
  writeFileSync(jsonPath, JSON.stringify({ base: BASE, taken_at: new Date().toISOString(), info: { ...previous, ...info }, failures }, null, 2));
  console.log(`done → ${OUT}${failures.length ? ` (failed: ${failures.join(', ')})` : ''}`);
  process.exit(failures.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
