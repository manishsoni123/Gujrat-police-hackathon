/**
 * Screenshot every page of the SPA against the REAL stack (Caddy on http://localhost) at 1440x900,
 * plus the wall (4/9/16 grids with WebRTC/HLS state), the live alert toast, a 1024 px tablet pass and
 * the jury_viewer role. Uses the Google Chrome channel so H.264 WebRTC/HLS decodes in headless mode.
 *
 *   node e2e/live_screenshots.mjs [only=key1,key2]   -> docs/screenshots/live/<key>.png + console-errors.json
 *
 * Env: SG_BASE (default http://localhost), SG_OUT (default ../docs/screenshots/live), SP (where the
 *      console-errors.json / wall-info.json go; default SG_OUT), SG_CHANNEL (chrome | msedge | chromium).
 */
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));

const BASE = process.env.SG_BASE ?? 'http://localhost';
const OUT = process.env.SG_OUT ?? join(here, '..', '..', 'docs', 'screenshots', 'live');
const SP = process.env.SP ?? OUT;
const ONLY = process.argv[2] ? new Set(process.argv[2].split(',')) : null;
mkdirSync(OUT, { recursive: true });
const settle = (ms) => new Promise((r) => setTimeout(r, ms));
const consoleLog = {};
let current = 'init';
const note = (kind, text) => {
  if (/favicon|tile\.openstreetmap|ERR_INTERNET_DISCONNECTED|ERR_NAME_NOT_RESOLVED|Download the React DevTools/.test(text)) return;
  (consoleLog[current] ??= []).push(`[${kind}] ${text.slice(0, 300)}`);
};

async function apiLogin(username, password) {
  const r = await fetch(`${BASE}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) });
  return (await r.json()).access_token;
}
async function api(method, path, token, json) {
  const r = await fetch(`${BASE}${path}`, { method, headers: { Authorization: `Bearer ${token}`, ...(json ? { 'Content-Type': 'application/json' } : {}) }, body: json ? JSON.stringify(json) : undefined });
  return { status: r.status, data: await r.json().catch(() => null) };
}

async function wire(page) {
  page.on('pageerror', (e) => note('pageerror', e.message));
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') note(m.type(), m.text()); });
  page.on('requestfailed', (rq) => { const f = rq.failure()?.errorText ?? ''; if (!/ERR_ABORTED/.test(f)) note('requestfailed', `${rq.method()} ${rq.url()} ${f}`); });
  page.on('response', (rs) => { if (rs.status() >= 400 && !/\/api\/auth\/login|\/mtx\/|\/whep/.test(rs.url())) note('http', `${rs.status()} ${rs.request().method()} ${rs.url()}`); });
}

async function login(page, user, pass, shot) {
  current = 'login';
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('input[autocomplete="username"]', { timeout: 30000 });
  await settle(1200);
  if (shot) await page.screenshot({ path: `${OUT}/login.png` });
  await page.fill('input[autocomplete="username"]', user);
  await page.fill('input[autocomplete="current-password"]', pass);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/dashboard/, { timeout: 30000 });
  await settle(1500);
}

async function videoState(page) {
  return page.evaluate(() => {
    const vids = [...document.querySelectorAll('video')].map((v) => ({ t: Number(v.currentTime.toFixed(1)), rs: v.readyState, w: v.videoWidth, h: v.videoHeight, visible: v.offsetParent !== null || getComputedStyle(v).display !== 'none' }));
    const caps = [...document.querySelectorAll('.sg-player-caption')].map((c) => c.textContent?.replace(/\s+/g, ' ').trim());
    const live = document.querySelectorAll('.sg-live-badge').length;
    return { vids, caps, live };
  });
}

async function main() {
  const plates = JSON.parse(readFileSync(join(here, '..', '..', 'media', 'synthetic', 'plates.json'), 'utf8'));
  const running = new Set([1, 2, 3, 4, 5, 6, 7, 8]);
  const filler = plates.plates.find((p) => !p.anchor && p.cameras.filter((c) => running.has(c)).length >= 2) ?? plates.plates.find((p) => !p.anchor);
  const adminTok = await apiLogin('jury_admin', 'Sentinel@Admin2026');
  const cams = (await api('GET', '/api/cameras?source=sandbox&page_size=100&sort=external_id&order=asc', adminTok)).data.items;
  const idOf = (ext) => cams.find((c) => c.external_id === String(ext))?.id;
  const browser = await chromium.launch({ channel: process.env.SG_CHANNEL ?? 'chrome', args: ['--autoplay-policy=no-user-gesture-required'] });
  // OSM tile servers answer 403 to the "HeadlessChrome" UA; a normal Chrome UA gets the base map for the docs shots.
  const userAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36';
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, locale: 'en-IN', timezoneId: 'Asia/Kolkata', permissions: ['notifications'], userAgent });
  // OSM refuses tile requests from automated browsers (403). For the document screenshots the tiles are fetched by
  // Node with an identifying User-Agent (a few dozen tiles per run) and handed to the page; real users load them directly.
  const tileRoute = async (route) => {
    try {
      const r = await fetch(route.request().url(), { headers: { 'User-Agent': 'SentinelGujarat-docs-screenshots/1.0 (+https://github.com/dynatech-consultancy/sentinel-gujarat)' } });
      await route.fulfill({ status: r.status, headers: { 'content-type': r.headers.get('content-type') ?? 'image/png' }, body: Buffer.from(await r.arrayBuffer()) });
    } catch {
      await route.abort();
    }
  };
  await context.route(/tile.openstreetmap.org/, tileRoute);
  const page = await context.newPage();
  await wire(page);
  await login(page, 'jury_admin', 'Sentinel@Admin2026', !ONLY || ONLY.has('login'));
  const wallInfo = {};
  const failures = [];
  const shot = async (key, path, opts = {}) => {
    if (ONLY && !ONLY.has(key)) return;
    current = key;
    try {
      if (path) await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded' });
      await settle(opts.wait ?? 2500);
      if (opts.before) await opts.before(page);
      await page.screenshot({ path: `${OUT}/${key}.png`, fullPage: Boolean(opts.fullPage) });
      console.log('✓', key);
    } catch (e) {
      failures.push(key);
      console.error('✗', key, e.message.split('\n')[0]);
    }
  };
  const clickFirstRow = async (p) => { await p.locator('.sg-table tbody tr.ant-table-row').first().click(); await settle(2500); };

  await shot('dashboard', '/dashboard', { wait: 4000 });
  await shot('cameras', '/cameras', { wait: 2500 });
  await shot('camera-drawer', '/cameras', { wait: 2500, before: clickFirstRow });
  await shot('camera-detail', `/cameras/${idOf(1)}`, { wait: 9000 });
  await shot('camera-detail-h265', `/cameras/${idOf(8)}`, { wait: 9000 });
  await shot('import', '/cameras/import', { wait: 2500 });
  await shot('map', '/map', { wait: 6000 });
  await shot('wall', '/wall', {
    wait: 3000,
    before: async (p) => {
      let st = null;
      for (let i = 0; i < 30; i++) {
        st = await videoState(p);
        const playing = st.vids.filter((v) => v.t > 0.5 && v.w > 2).length;
        if (playing >= 4) break;
        await settle(1000);
      }
      wallInfo.grid4 = st;
    },
  });
  await shot('wall-9', null, {
    wait: 500,
    before: async (p) => {
      await p.locator('.ant-segmented-item-label', { hasText: /^9$/ }).click();
      await settle(500);
      await p.getByRole('button', { name: 'Auto-fill' }).click();
      let st = null;
      for (let i = 0; i < 40; i++) {
        st = await videoState(p);
        if (st.vids.filter((v) => v.t > 0.5 && v.w > 2).length >= 8) break;
        await settle(1000);
      }
      wallInfo.grid9 = st;
    },
  });
  await shot('wall-16', null, {
    wait: 500,
    before: async (p) => {
      await p.locator('.ant-segmented-item-label', { hasText: /^16$/ }).click();
      await settle(8000);
      wallInfo.grid16 = await videoState(p);
    },
  });
  await shot('detections', '/detections', { wait: 3000 });
  await shot('detection-drawer', '/detections', { wait: 3000, before: clickFirstRow });
  // GJ 27 XY 3456 crosses four Gandhinagar cameras (6 -> 3 -> 7 -> 4) and never camera 1, whose loop the own-gate
  // fallback also plays, so its route is the clean multi-camera example for the documents.
  await shot('vehicles', '/vehicles?q=GJ27XY3456', { wait: 4000 });
  await shot('route', '/vehicles/GJ27XY3456/route', { wait: 6000, fullPage: true });
  await shot('route-gj01ab1234', '/vehicles/GJ01AB1234/route', { wait: 6000 });
  await shot('watchlist', '/watchlist', { wait: 2500 });
  await shot('watchlist-add', '/watchlist', {
    wait: 2500,
    before: async (p) => {
      await p.getByRole('button', { name: 'Add to watchlist' }).first().click();
      await settle(700);
      await p.getByPlaceholder('GJ 27 XY 3456').fill('GJ 27 XY 3456');
      await settle(700);
    },
  });
  await shot('alerts', '/alerts', { wait: 3000 });
  await shot('alert-drawer', '/alerts', { wait: 3000, before: clickFirstRow });
  await shot('events', '/events', { wait: 2500 });
  await shot('reports', '/reports', { wait: 3000, fullPage: true });
  await shot('health', '/health', { wait: 3000 });
  await shot('gap-analysis', '/gap-analysis', { wait: 3000, fullPage: true, before: async (p) => { await p.waitForFunction(() => document.querySelectorAll('.ant-skeleton').length === 0, null, { timeout: 60000 }); await settle(4000); } });
  await shot('audit', '/audit', { wait: 2500 });
  await shot('audit-diff', '/audit?action=camera.', { wait: 2500, before: async (p) => { await p.locator('.sg-table .ant-table-row-expand-icon').first().click(); await settle(900); } });
  await shot('settings', '/settings', { wait: 2500, fullPage: true });
  await shot('settings-catalogue', '/settings/catalogue', { wait: 2500, fullPage: true });
  await shot('settings-retention', '/settings/retention', { wait: 2500 });
  await shot('settings-alerts', '/settings/alerts', { wait: 2500 });
  await shot('settings-notifications', '/settings/notifications', { wait: 2500 });
  await shot('settings-webhooks', '/settings/webhooks', { wait: 2500 });
  await shot('settings-api-keys', '/settings/api-keys', { wait: 2500 });
  await shot('users', '/users', { wait: 2500 });
  await shot('about', '/about', { wait: 2500, fullPage: true });
  await shot('not-found', '/no-such-page', { wait: 2000 });
  await shot('global-search', '/dashboard', { wait: 3000, before: async (p) => { await p.keyboard.press('Control+K'); await settle(500); await p.keyboard.type('GJ 01 AB'); await settle(1200); } });

  // Alert toast: add a filler plate (not on the watchlist) that appears on the running cameras, then wait on the dashboard.
  if (!ONLY || ONLY.has('alert-toast')) {
    current = 'alert-toast';
    try {
      const existing = (await api('GET', `/api/watchlist?q=${filler.plate}&is_active=all`, adminTok)).data.items;
      for (const w of existing) await api('DELETE', `/api/watchlist/${w.id}`, adminTok);
      await page.goto(`${BASE}/dashboard`, { waitUntil: 'domcontentloaded' });
      await settle(3000);
      const t0 = Date.now();
      const add = await api('POST', '/api/watchlist', adminTok, { entity_type: 'vehicle', plate: filler.display, name: 'Toast demo (filler plate from plates.json)', reason: 'stolen', priority: 'critical', source: 'manual' });
      console.log('watchlist add for toast:', add.status, filler.display, 'cameras', filler.cameras.join(','));
      let seen = false;
      for (let i = 0; i < 260; i++) {
        const n = await page.locator('.ant-notification-notice', { hasText: filler.display }).count();
        if (n > 0) { seen = true; break; }
        await settle(500);
      }
      await settle(400);
      await page.screenshot({ path: `${OUT}/alert-toast.png` });
      wallInfo.toast = { seen, after_s: Math.round((Date.now() - t0) / 1000), plate: filler.display };
      console.log(seen ? '✓' : '✗', 'alert-toast', wallInfo.toast);
      if (!seen) failures.push('alert-toast');
      await settle(1500);
      await page.screenshot({ path: `${OUT}/alert-toast-bell.png` });
      // Leave the demo watchlist as seeded: remove the plate again (its alerts keep the plate text).
      if (add.status === 201 && add.data?.id) await api('DELETE', `/api/watchlist/${add.data.id}`, adminTok);
    } catch (e) { failures.push('alert-toast'); console.error('✗ alert-toast', e.message); }
  }
  await page.close();

  // Tablet width (1024) and role views.
  if (!ONLY || ONLY.has('tablet')) {
    const ctx2 = await browser.newContext({ viewport: { width: 1024, height: 768 }, locale: 'en-IN', timezoneId: 'Asia/Kolkata', userAgent });
    await ctx2.route(/tile.openstreetmap.org/, tileRoute);
    const p2 = await ctx2.newPage();
    await wire(p2);
    await login(p2, 'jury_admin', 'Sentinel@Admin2026', false);
    for (const [key, path] of [['dashboard-1024', '/dashboard'], ['cameras-1024', '/cameras'], ['alerts-1024', '/alerts'], ['wall-1024', '/wall']]) {
      current = key;
      try { await p2.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded' }); await settle(key === 'wall-1024' ? 9000 : 3500); await p2.screenshot({ path: `${OUT}/${key}.png` }); console.log('✓', key); } catch (e) { failures.push(key); console.error('✗', key, e.message); }
    }
    await ctx2.close();
  }
  if (!ONLY || ONLY.has('viewer')) {
    const ctx3 = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'en-IN', timezoneId: 'Asia/Kolkata', userAgent });
    const p3 = await ctx3.newPage();
    await wire(p3);
    await login(p3, 'jury_viewer', 'Sentinel@View2026', false);
    for (const [key, path] of [['viewer-cameras', '/cameras'], ['viewer-alerts', '/alerts'], ['viewer-watchlist', '/watchlist'], ['viewer-reports', '/reports'], ['viewer-forbidden-audit', '/audit']]) {
      current = key;
      try { await p3.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded' }); await settle(3000); await p3.screenshot({ path: `${OUT}/${key}.png` }); console.log('✓', key); } catch (e) { failures.push(key); console.error('✗', key, e.message); }
    }
    await ctx3.close();
  }
  await browser.close();
  writeFileSync(`${SP}/console-errors.json`, JSON.stringify(consoleLog, null, 2));
  writeFileSync(`${SP}/wall-info.json`, JSON.stringify(wallInfo, null, 2));
  console.log('wall:', JSON.stringify(wallInfo));
  const pagesWithErrors = Object.entries(consoleLog).filter(([, v]) => v.length);
  console.log(`console/network issues on ${pagesWithErrors.length} pages:`);
  for (const [k, v] of pagesWithErrors) console.log(`  ${k}: ${v.length}\n    ${[...new Set(v)].slice(0, 6).join('\n    ')}`);
  console.log(`done → ${OUT}${failures.length ? ` (failed: ${failures.join(', ')})` : ''}`);
  process.exit(failures.length ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
