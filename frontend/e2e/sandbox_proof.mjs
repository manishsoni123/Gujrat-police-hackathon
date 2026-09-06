/**
 * Real-sandbox onboarding proof (30 organiser cameras) against the running stack, in real Chrome
 * (the Playwright headless shell has no H.264 decoder, so WHEP answers "codecs not supported by client" there):
 *   import result, cameras table, map (district boundaries + confidence rings), 9-tile wall with real
 *   cameras incl. one H.265 (waits for video.currentTime > 0 per tile and measures tile luminance so a
 *   black tile is reported), camera detail for cam01 / cam17 with live view, health, dashboard.
 *
 *   node e2e/sandbox_proof.mjs            -> docs/screenshots/sandbox/proof-*.png (1440x900) + proof-notes.json
 *
 * Env: SG_BASE (http://localhost), SG_OUT, SG_USER/SG_PASS (jury_admin), SG_CHANNEL (chrome),
 *      SG_STEPS=import,cameras,map,wall,detail,health,dashboard (default all), SG_IMPORT=0 skips running the import,
 *      SG_WALL_WAIT_S (default 60) upper bound for the tiles, SG_WALL_IDS comma list of camera ids for the 9 tiles.
 */
import { chromium } from 'playwright';
import { execSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.SG_BASE ?? 'http://localhost';
const OUT = resolve(process.env.SG_OUT ?? join(here, '..', '..', 'docs', 'screenshots', 'sandbox'));
const USER = process.env.SG_USER ?? 'jury_admin';
const PASS = process.env.SG_PASS ?? 'Sentinel@Admin2026';
const STEPS = new Set((process.env.SG_STEPS ?? 'import,cameras,map,wall,detail,health,dashboard').split(','));
const RUN_IMPORT = process.env.SG_IMPORT !== '0';
const WALL_WAIT_S = Number(process.env.SG_WALL_WAIT_S ?? 60);
mkdirSync(OUT, { recursive: true });

const settle = (ms) => new Promise((r) => setTimeout(r, ms));
const notes = { steps: {}, events: [] };
let current = 'start';
const note = (kind, text) => notes.events.push({ page: current, kind, text: String(text).slice(0, 400) });
const ist = () => new Date().toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false });

function wire(page) {
  page.on('pageerror', (e) => note('pageerror', e.message));
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const t = m.text();
    if (/favicon|tile\.openstreetmap|Download the React DevTools/.test(t)) return;
    note('console', t);
  });
  page.on('response', (rs) => {
    if (rs.status() >= 400 && !/\/api\/auth\/login|tile\.openstreetmap/.test(rs.url())) note('http', `${rs.status()} ${rs.request().method()} ${rs.url()}`);
  });
}

/** Any `scheme://user:password@` left in the rendered text is a credential leak (masked form is `user:***@`). */
async function assertNoRawCredentials(page, label) {
  const text = await page.evaluate(() => document.body.innerText + ' ' + [...document.querySelectorAll('input')].map((i) => i.value).join(' '));
  const leaks = [...text.matchAll(/[a-z][a-z0-9+.-]*:\/\/[^\s/?#@]+:([^\s/?#@]+)@/gi)].map((m) => m[1]).filter((pw) => pw !== '***');
  if (leaks.length) note('LEAK', `${label}: ${leaks.length} raw credential(s) rendered`);
  return leaks.length;
}

async function apiLogin() {
  const r = await fetch(`${BASE}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: USER, password: PASS }) });
  return (await r.json()).access_token;
}
async function api(tok, method, path, body) {
  const r = await fetch(`${BASE}/api${path}`, { method, headers: { Authorization: `Bearer ${tok}`, 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, data: await r.json().catch(() => null) };
}

async function login(page) {
  current = 'login';
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('input[autocomplete="username"]', { timeout: 30000 });
  await page.fill('input[autocomplete="username"]', USER);
  await page.fill('input[autocomplete="current-password"]', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/dashboard/, { timeout: 30000 });
  await settle(1500);
}

async function shot(page, name, opts = {}) {
  const path = join(OUT, `${name}.png`);
  await page.screenshot({ path, fullPage: Boolean(opts.fullPage) });
  console.log(ist(), 'saved', path);
}

/** Per player: currentTime, readyState, size, caption text and mean luminance of a 64x36 sample (0 = black). */
async function videoState(page) {
  return page.evaluate(() => {
    const players = [...document.querySelectorAll('.sg-player')];
    return players.map((p) => {
      const v = p.querySelector('video');
      const title = p.querySelector('.sg-player-title')?.textContent?.trim() ?? '';
      const caption = p.querySelector('.sg-player-caption')?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
      const overlay = p.querySelector('.sg-player-overlay')?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
      let luma = null;
      if (v && v.videoWidth > 0) {
        try {
          const c = document.createElement('canvas');
          c.width = 64;
          c.height = 36;
          const ctx = c.getContext('2d');
          ctx.drawImage(v, 0, 0, 64, 36);
          const d = ctx.getImageData(0, 0, 64, 36).data;
          let s = 0;
          for (let i = 0; i < d.length; i += 4) s += 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
          luma = Math.round(s / (d.length / 4));
        } catch (e) {
          luma = `err:${e.message}`;
        }
      }
      return { title, t: v ? Number(v.currentTime.toFixed(1)) : null, rs: v ? v.readyState : null, w: v?.videoWidth ?? 0, h: v?.videoHeight ?? 0, live: Boolean(p.querySelector('.sg-live-badge')), caption, overlay, luma };
    });
  });
}

function dockerStats() {
  try {
    const raw = execSync('docker stats --no-stream --format "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.NetIO}}"', { encoding: 'utf8' });
    return raw.trim().split('\n').filter((l) => l.startsWith('sentinel-')).map((l) => {
      const [name, cpu, mem, net] = l.split('|');
      return { name, cpu, mem, net };
    });
  } catch (e) {
    return `docker stats failed: ${e.message}`;
  }
}
async function mtxPaths(tok, ids) {
  const out = {};
  for (const id of ids) {
    const s = await api(tok, 'GET', `/streams/${id}`);
    out[id] = s.data ? { play_path: s.data.play_path, ready: s.data.ready, readers: s.data.readers, codec: s.data.codec } : s.status;
  }
  return out;
}

async function main() {
  const tok = await apiLogin();
  const cams = (await api(tok, 'GET', '/cameras?source=sandbox&page_size=100&sort=external_id&order=asc')).data.items;
  const idOf = (ext) => cams.find((c) => c.external_id === ext)?.id;
  const browser = await chromium.launch({ channel: process.env.SG_CHANNEL ?? 'chrome', args: ['--autoplay-policy=no-user-gesture-required'] });
  const userAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36';
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, locale: 'en-IN', timezoneId: 'Asia/Kolkata', userAgent });
  // OSM refuses tile requests from automated browsers (403): fetch the few dozen tiles from Node with an identifying UA.
  await ctx.route(/tile.openstreetmap.org/, async (route) => {
    try {
      const r = await fetch(route.request().url(), { headers: { 'User-Agent': 'SentinelGujarat-docs-screenshots/1.0 (+https://github.com/dynatech-consultancy/sentinel-gujarat)' } });
      await route.fulfill({ status: r.status, headers: { 'content-type': r.headers.get('content-type') ?? 'image/png' }, body: Buffer.from(await r.arrayBuffer()) });
    } catch {
      await route.abort();
    }
  });
  const page = await ctx.newPage();
  wire(page);
  await login(page);

  if (STEPS.has('import')) {
    current = 'import';
    const t0 = Date.now();
    await page.goto(`${BASE}/cameras/import`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('text=Import from Sentinel sandbox', { timeout: 30000 });
    await settle(1000);
    if (RUN_IMPORT) {
      await page.locator('button:has-text("Import from Sentinel sandbox")').first().click();
      const ok = await page.waitForSelector('text=cameras in the catalogue', { timeout: 240000 }).then(() => true).catch(() => false);
      notes.steps.import = { ran: true, result_shown: ok, seconds: Math.round((Date.now() - t0) / 1000) };
      await settle(1500);
    }
    await assertNoRawCredentials(page, 'import');
    await shot(page, 'proof-import', { fullPage: true });
  }

  if (STEPS.has('cameras')) {
    current = 'cameras';
    await page.goto(`${BASE}/cameras`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.sg-table tbody tr.ant-table-row', { timeout: 30000 });
    await settle(2500);
    const rows = await page.locator('.sg-table tbody tr.ant-table-row').count();
    notes.steps.cameras = { rows_on_page: rows, total_text: await page.locator('.ant-pagination-total-text').first().textContent().catch(() => null) };
    await assertNoRawCredentials(page, 'cameras');
    await shot(page, 'proof-cameras');
  }

  if (STEPS.has('map')) {
    current = 'map';
    await page.goto(`${BASE}/map`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.leaflet-container', { timeout: 30000 });
    await settle(7000);
    const st = await page.evaluate(() => ({
      markers: document.querySelectorAll('.sg-marker').length,
      approx: document.querySelectorAll('.sg-marker-approx').length,
      guess: document.querySelectorAll('.sg-marker-guess').length,
      clusters: document.querySelectorAll('.sg-cluster').length,
      district_polygons: document.querySelectorAll('.leaflet-overlay-pane path, .leaflet-pane svg path').length,
      legend: document.querySelector('.sg-legend')?.innerText.replace(/\s+/g, ' ').trim() ?? '',
    }));
    notes.steps.map = st;
    await shot(page, 'proof-map');
    // zoom into Ahmedabad (9 cameras, mixed confidence) so the rings are readable
    const target = await page.evaluate(() => {
      const m = [...document.querySelectorAll('.leaflet-marker-icon')].find((el) => /Chiman bhai Bridge|Janpath/.test(el.getAttribute('title') ?? ''));
      if (!m) return null;
      const r = m.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    });
    if (target) {
      await page.mouse.move(target.x, target.y);
      for (let i = 0; i < 5; i += 1) {
        await page.mouse.wheel(0, -120);
        await settle(350);
      }
      await settle(3000);
      await shot(page, 'proof-map-ahmedabad');
    }
  }

  if (STEPS.has('wall')) {
    current = 'wall';
    const ids = (process.env.SG_WALL_IDS ?? '').split(',').filter(Boolean).map(Number);
    const wallIds = ids.length === 9 ? ids : [idOf('cam01'), idOf('cam02'), idOf('cam03'), idOf('cam04'), idOf('cam17'), idOf('cam05'), idOf('cam07'), idOf('cam08'), idOf('cam09')];
    await api(tok, 'PUT', '/me/wall-layout', { grid: 9, tiles: wallIds.map((camera_id, slot) => ({ slot, camera_id })) });
    notes.steps.wall = { camera_ids: wallIds, before: await mtxPaths(tok, wallIds), stats_before: dockerStats() };
    const t0 = Date.now();
    await page.goto(`${BASE}/wall`, { waitUntil: 'domcontentloaded' });
    const firstPlay = {};
    let st = [];
    for (let i = 0; i < WALL_WAIT_S; i += 1) {
      await settle(1000);
      st = await videoState(page);
      st.forEach((v, k) => {
        if (v.t > 0 && v.w > 2 && firstPlay[k] === undefined) firstPlay[k] = Math.round((Date.now() - t0) / 1000);
      });
      if (st.length >= 9 && st.every((v) => v.t > 0 && v.w > 2)) break;
    }
    const elapsed = Math.round((Date.now() - t0) / 1000);
    await settle(3000);
    st = await videoState(page);
    notes.steps.wall.first_play_s = firstPlay;
    notes.steps.wall.elapsed_s = elapsed;
    notes.steps.wall.tiles = st;
    notes.steps.wall.playing = st.filter((v) => v.t > 0 && v.w > 2).length;
    notes.steps.wall.black = st.filter((v) => typeof v.luma === 'number' && v.luma < 8).map((v) => v.title);
    notes.steps.wall.within_20s = Object.values(firstPlay).filter((s) => s <= 20).length;
    await shot(page, 'proof-wall-9');
    await settle(15000);
    const st2 = await videoState(page);
    notes.steps.wall.after_15s_more = st2.map((v, k) => ({ title: v.title, advanced_s: st[k] ? Number((v.t - st[k].t).toFixed(1)) : null, luma: v.luma, live: v.live, caption: v.caption }));
    notes.steps.wall.stats_during = dockerStats();
    notes.steps.wall.paths_during = await mtxPaths(tok, wallIds);
    await shot(page, 'proof-wall-9-after');
    console.log(ist(), 'wall', JSON.stringify({ playing: notes.steps.wall.playing, elapsed, firstPlay, black: notes.steps.wall.black }));
  }

  if (STEPS.has('detail')) {
    for (const ext of (process.env.SG_DETAIL ?? 'cam01,cam17').split(',')) {
      current = `detail-${ext}`;
      const id = idOf(ext);
      const t0 = Date.now();
      await page.goto(`${BASE}/cameras/${id}`, { waitUntil: 'domcontentloaded' });
      let st = [];
      let first = null;
      for (let i = 0; i < WALL_WAIT_S; i += 1) {
        await settle(1000);
        st = await videoState(page);
        if (st[0] && st[0].t > 0 && st[0].w > 2) {
          first = Math.round((Date.now() - t0) / 1000);
          break;
        }
      }
      await settle(3000);
      st = await videoState(page);
      notes.steps[`detail_${ext}`] = { id, first_play_s: first, player: st[0] ?? null };
      await assertNoRawCredentials(page, `detail-${ext}`);
      await shot(page, `proof-camera-${ext}`);
      console.log(ist(), ext, JSON.stringify(notes.steps[`detail_${ext}`]));
    }
  }

  if (STEPS.has('health')) {
    current = 'health';
    await page.goto(`${BASE}/health`, { waitUntil: 'domcontentloaded' });
    await settle(4000);
    notes.steps.health = { text: (await page.locator('main').innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 600) };
    await shot(page, 'proof-health', { fullPage: true });
  }
  if (STEPS.has('dashboard')) {
    current = 'dashboard';
    await page.goto(`${BASE}/dashboard`, { waitUntil: 'domcontentloaded' });
    await settle(5000);
    await shot(page, 'proof-dashboard');
  }

  writeFileSync(join(OUT, 'proof-notes.json'), JSON.stringify(notes, null, 2));
  console.log(JSON.stringify(notes.events, null, 1));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
