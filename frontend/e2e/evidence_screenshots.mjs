/**
 * Government-feed evidence captures against the running stack (real organiser cameras, no mock):
 *   Detections page filtered to a real camera with its crops, Vehicle search for a plate read on the real
 *   feed, the Alerts page opened on a watchlist alert raised by a real camera (only when one exists - nothing
 *   is staged) and, when a real plate was seen on two or more cameras, its route page.
 *
 *   node e2e/evidence_screenshots.mjs            → docs/screenshots/sandbox/{detections,search,alert,route}-real.png (1440×900)
 *
 * Env: SG_BASE (default http://localhost), SG_OUT (default ../docs/screenshots/sandbox),
 *      SG_USER / SG_PASS (default jury_admin / Sentinel@Admin2026), SG_CHANNEL (default chrome; '' = bundled chromium),
 *      SG_CAMERA (default: the real camera with the most valid reads), SG_PLATE (default: its most-read plate),
 *      SG_FROM (ISO, default 3 h ago) - the window the pages are asked to show.
 * Writes evidence-notes.json next to the captures (what each page showed, console/http errors).
 */
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.SG_BASE ?? 'http://localhost';
const OUT = resolve(process.env.SG_OUT ?? join(here, '..', '..', 'docs', 'screenshots', 'sandbox'));
const USER = process.env.SG_USER ?? 'jury_admin';
const PASS = process.env.SG_PASS ?? 'Sentinel@Admin2026';
const CHANNEL = process.env.SG_CHANNEL ?? 'chrome';
const FROM = process.env.SG_FROM ?? new Date(Date.now() - 3 * 3600 * 1000).toISOString();
mkdirSync(OUT, { recursive: true });

const settle = (ms) => new Promise((r) => setTimeout(r, ms));
const notes = [];
let current = 'start';
const note = (kind, text) => notes.push({ page: current, kind, text: String(text).slice(0, 400) });

function wire(page) {
  page.on('pageerror', (e) => note('pageerror', e.message));
  page.on('console', (m) => { if (m.type() === 'error') note('console', m.text()); });
  page.on('response', (rs) => {
    if (rs.status() >= 400 && !/\/api\/auth\/login|\/mtx\/|\/whep|\/hls\//.test(rs.url())) note('http', `${rs.status()} ${rs.request().method()} ${rs.url()}`);
  });
}

async function apiJson(token, path) {
  const r = await fetch(`${BASE}/api${path}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return r.json();
}

async function login(page) {
  current = 'login';
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('input[autocomplete="username"]', { timeout: 30000 });
  await page.fill('input[autocomplete="username"]', USER);
  await page.fill('input[autocomplete="current-password"]', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/dashboard/, { timeout: 30000 });
  await settle(1000);
}

async function shot(page, name) {
  const path = join(OUT, `${name}.png`);
  await page.screenshot({ path });
  console.log('saved', path);
  return path;
}

/** Ant Design RangePicker: pick the smallest preset ("Last 6 h" / "Last 24 h") that covers the window, then close it. */
async function setRange(page, fromIso) {
  const picker = page.locator('.ant-picker-range').first();
  if (!(await picker.count())) return false;
  const hours = (Date.now() - new Date(fromIso).getTime()) / 3600e3;
  const preset = hours <= 1 ? 'Last hour' : hours <= 6 ? 'Last 6 h' : hours <= 24 ? 'Last 24 h' : 'Last 7 days';
  await picker.click();
  const item = page.locator('.ant-picker-presets li', { hasText: preset }).first();
  await item.waitFor({ timeout: 10000 });
  await item.click();
  await settle(300);
  await page.keyboard.press('Escape');
  await page.mouse.click(5, 450); // click the sidebar gutter so nothing stays focused
  await settle(1200);
  return preset;
}

async function main() {
  const loginRs = await fetch(`${BASE}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: USER, password: PASS }) });
  if (!loginRs.ok) throw new Error(`api login ${loginRs.status}`);
  const token = (await loginRs.json()).access_token;

  const cams = (await apiJson(token, '/cameras?source=sandbox&page_size=200')).items;
  const real = new Map(cams.map((c) => [c.id, c]));
  const dets = (await apiJson(token, `/detections?valid_only=true&from=${encodeURIComponent(FROM)}&page_size=500`)).items.filter((d) => real.has(d.camera.id));
  const perCam = new Map();
  const perPlate = new Map();
  for (const d of dets) {
    perCam.set(d.camera.id, (perCam.get(d.camera.id) ?? 0) + 1);
    const p = perPlate.get(d.plate_norm) ?? { n: 0, cams: new Set() };
    p.n += 1; p.cams.add(d.camera.id); perPlate.set(d.plate_norm, p);
  }
  const cameraId = Number(process.env.SG_CAMERA ?? [...perCam.entries()].sort((a, b) => b[1] - a[1])[0]?.[0]);
  const plate = process.env.SG_PLATE ?? [...perPlate.entries()].sort((a, b) => b[1].n - a[1].n)[0]?.[0];
  const routePlate = [...perPlate.entries()].find(([, p]) => p.cams.size >= 2)?.[0] ?? null;
  const alerts = (await apiJson(token, `/alerts?type=watchlist_hit&page_size=200&from=${encodeURIComponent(FROM)}`)).items.filter((a) => real.has(a.camera?.id));
  note('facts', `valid reads on real cameras since ${FROM}: ${dets.length}; camera ${cameraId} (${real.get(cameraId)?.external_id}); plate ${plate}; route candidate ${routePlate}; real-camera alerts ${alerts.length}`);
  if (!cameraId || !plate) throw new Error('no valid read on a real camera in the window - nothing to capture');

  const browser = await chromium.launch(CHANNEL ? { channel: CHANNEL } : {});
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'en-IN', timezoneId: 'Asia/Kolkata' });
  const page = await ctx.newPage();
  wire(page);
  const result = {};
  try {
    await login(page);

    current = 'detections';
    await page.goto(`${BASE}/detections?camera_id=${cameraId}`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.ant-table', { timeout: 30000 });
    await setRange(page, FROM);
    await page.waitForFunction(() => document.querySelectorAll('.ant-table-tbody img').length > 0, null, { timeout: 30000 }).catch(() => note('warn', 'no crop image rendered in the detections table'));
    await settle(1500);
    result.detections = { file: await shot(page, 'detections-real'), rows: await page.locator('.ant-table-tbody tr').count(), crops: await page.locator('.ant-table-tbody img').count(), camera: real.get(cameraId)?.external_id };

    current = 'search';
    await page.goto(`${BASE}/vehicles?q=${encodeURIComponent(plate)}`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.ant-table, .ant-empty', { timeout: 30000 });
    await setRange(page, FROM);
    await settle(1500);
    const searchText = await page.evaluate(() => document.body.innerText);
    result.search = { file: await shot(page, 'search-real'), plate, exactRowsVisible: (searchText.match(new RegExp(plate.replace(/(\w{2})(\d{1,2})(\w{1,3})(\d{4})/, '$1 $2 $3 $4'), 'g')) || []).length };

    if (alerts.length) {
      current = 'alert';
      const a = alerts[0];
      await page.goto(`${BASE}/alerts?id=${a.id}`, { waitUntil: 'domcontentloaded' });
      await page.waitForSelector('.ant-table, .ant-drawer, .ant-card', { timeout: 30000 });
      await settle(2500);
      result.alert = { file: await shot(page, 'alert-real'), id: a.id, plate: a.plate_norm, camera: a.camera?.external_id, priority: a.priority, latency_ms: a.latency_ms, created_at: a.created_at };
    } else {
      note('skipped', 'no watchlist alert on a real camera in the window - alert-real.png not captured (nothing staged)');
    }

    if (routePlate) {
      current = 'route';
      await page.goto(`${BASE}/vehicles/${routePlate}/route?from=${encodeURIComponent(FROM)}`, { waitUntil: 'domcontentloaded' });
      await page.waitForSelector('.leaflet-container', { timeout: 30000 });
      await settle(4000);
      result.route = { file: await shot(page, 'route-real'), plate: routePlate, cameras: perPlate.get(routePlate).cams.size };
    } else {
      note('skipped', 'no plate seen on two or more real cameras - route-real.png not captured');
    }
  } finally {
    await browser.close();
  }
  writeFileSync(join(OUT, 'evidence-notes.json'), JSON.stringify({ base: BASE, from: FROM, at: new Date().toISOString(), result, notes }, null, 2));
  console.log(JSON.stringify({ result, skipped: notes.filter((n) => n.kind === 'skipped').map((n) => n.text), errors: notes.filter((n) => ['pageerror', 'console', 'http'].includes(n.kind)).length }, null, 2));
}

main().catch((e) => { console.error(e); process.exit(1); });
