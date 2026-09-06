/**
 * Real-sandbox UI captures against the running stack (http://localhost by default):
 *   Settings → Catalogue (source = organiser Sentinel sandbox, Test cam01), Import page (Import from
 *   Sentinel sandbox with the per-camera probe table), Map (confidence-styled markers + legend),
 *   camera drawer (Location confidence row) and About (real camera source). Also asserts that no page
 *   text contains a raw `user:password@` URL and that the MOCK SANDBOX badge is gone.
 *
 *   node e2e/sandbox_screenshots.mjs            → docs/screenshots/sandbox/ui-*.png (1440×900)
 *
 * Env: SG_BASE (default http://localhost), SG_OUT (default ../docs/screenshots/sandbox),
 *      SG_USER / SG_PASS (default jury_admin / Sentinel@Admin2026),
 *      SG_IMPORT=0 to skip running the import (screenshots the card without a result),
 *      SG_SWITCH=0 to leave `catalogue.source` untouched,
 *      SG_STEPS=settings,import,map,drawer,about (default all).
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
const RUN_IMPORT = process.env.SG_IMPORT !== '0';
const SWITCH = process.env.SG_SWITCH !== '0';
const STEPS = new Set((process.env.SG_STEPS ?? 'settings,import,map,drawer,about').split(','));
mkdirSync(OUT, { recursive: true });

const settle = (ms) => new Promise((r) => setTimeout(r, ms));
const notes = [];
let current = 'start';
const note = (kind, text) => notes.push({ page: current, kind, text: String(text).slice(0, 400) });

function wire(page) {
  page.on('pageerror', (e) => note('pageerror', e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' || m.type() === 'warning') note(m.type(), m.text());
  });
  page.on('response', (rs) => {
    if (rs.status() >= 400 && !/\/api\/auth\/login|\/mtx\/|\/whep|\/hls\//.test(rs.url())) note('http', `${rs.status()} ${rs.request().method()} ${rs.url()}`);
  });
}

/** Any `scheme://user:password@` left in the rendered text is a credential leak (masked form is `user:***@`). */
async function assertNoRawCredentials(page, label) {
  const text = await page.evaluate(() => document.body.innerText + ' ' + [...document.querySelectorAll('input')].map((i) => i.value).join(' '));
  const leaks = [...text.matchAll(/[a-z][a-z0-9+.-]*:\/\/[^\s/?#@]+:([^\s/?#@]+)@/gi)].map((m) => m[1]).filter((pw) => pw !== '***');
  if (leaks.length) note('LEAK', `${label}: ${leaks.length} raw credential(s) rendered`);
  return leaks.length;
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
  console.log('saved', path);
}

async function main() {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  wire(page);
  await login(page);

  // 1. Settings → Catalogue: pick the organiser sandbox, save, run the one-camera test
  if (STEPS.has('settings')) {
  current = 'settings-catalogue';
  await page.goto(`${BASE}/settings/catalogue`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('text=Catalogue source', { timeout: 30000 });
  await settle(1200);
  if (SWITCH) {
    const select = page.locator('.ant-form-item:has-text("Where cameras come from") .ant-select').first();
    await select.click();
    await page.locator('.ant-select-item-option:has-text("Organiser Sentinel sandbox")').first().click();
    await settle(400);
    await page.locator('button:has-text("Save")').first().click();
    await page.waitForSelector('text=Settings saved', { timeout: 15000 }).catch(() => note('warn', 'no "Settings saved" toast'));
    await settle(800);
  }
  await page.locator('button:has-text("Test cam01")').first().click();
  await page.waitForSelector('.ant-alert:has-text("cam01")', { timeout: 90000 }).catch(() => note('warn', 'catalogue test gave no cam01 alert within 90 s'));
  await settle(800);
  await assertNoRawCredentials(page, 'settings');
  await shot(page, 'ui-settings-catalogue', { fullPage: true });
  }

  // 2. Import page: the Sentinel card; run the import (probe every camera) and capture the result table
  if (STEPS.has('import')) {
  current = 'import';
  await page.goto(`${BASE}/cameras/import`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('text=Import from Sentinel sandbox', { timeout: 30000 });
  await settle(1000);
  await shot(page, 'ui-import-before');
  if (RUN_IMPORT) {
    await page.locator('button:has-text("Import from Sentinel sandbox")').first().click();
    await page.waitForSelector('text=cameras in the catalogue', { timeout: 240000 }).catch(() => note('warn', 'import gave no result within 240 s'));
    await settle(1200);
  }
  await assertNoRawCredentials(page, 'import');
  await shot(page, 'ui-import', { fullPage: true });
  }

  // 3. Map: markers styled by location confidence + legend rows
  if (STEPS.has('map')) {
  current = 'map';
  await page.goto(`${BASE}/map`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.leaflet-container', { timeout: 30000 });
  await settle(5000);
  const markerStats = await page.evaluate(() => ({
    markers: document.querySelectorAll('.sg-marker').length,
    approx: document.querySelectorAll('.sg-marker-approx').length,
    guess: document.querySelectorAll('.sg-marker-guess').length,
    clusters: document.querySelectorAll('.sg-cluster').length,
    legend: document.querySelector('.sg-legend')?.innerText.replace(/\s+/g, ' ').trim() ?? '',
  }));
  note('info', `map ${JSON.stringify(markerStats)}`);
  await shot(page, 'ui-map');
  // clustering off, then zoom into the densest area (Ahmedabad) so the confidence rings are readable
  await page.locator('.ant-switch[aria-label="Cluster markers"]').click();
  await settle(1500);
  const rings = await page.evaluate(() => ({ markers: document.querySelectorAll('.sg-marker').length, approx: document.querySelectorAll('.sg-marker-approx').length, guess: document.querySelectorAll('.sg-marker-guess').length }));
  note('info', `map unclustered ${JSON.stringify(rings)}`);
  await shot(page, 'ui-map-unclustered');
  const box = await page.locator('.leaflet-container').boundingBox();
  if (box) {
    // Ahmedabad ≈ 23.03 N 72.58 E; the Gujarat view is centred so it sits right of centre, slightly above
    const target = await page.evaluate(() => {
      const m = [...document.querySelectorAll('.leaflet-marker-icon')].find((el) => /Chiman bhai Bridge|Janpath|Paldi/.test(el.getAttribute('title') ?? ''));
      if (!m) return null;
      const r = m.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    });
    const at = target ?? { x: box.x + box.width * 0.58, y: box.y + box.height * 0.38 };
    await page.mouse.move(at.x, at.y);
    for (let i = 0; i < 6; i += 1) {
      await page.mouse.wheel(0, -120);
      await settle(350);
    }
    await settle(2500);
    await shot(page, 'ui-map-ahmedabad');
    const approx = page.locator('.sg-marker-approx, .sg-marker-guess').first();
    if (await approx.count()) {
      await approx.click({ force: true }).catch(() => {});
      await settle(1500);
      await shot(page, 'ui-map-popup');
    }
  }
  }

  // 4. Camera drawer of cam01 (id from the registry search)
  if (STEPS.has('drawer')) {
  current = 'camera-drawer';
  const camsRes = await fetch(`${BASE}/api/cameras?q=cam01&page_size=5`, { headers: { Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem('sg.token') ?? '')}` } }).catch(() => null);
  let camId = null;
  if (camsRes && camsRes.ok) {
    const j = await camsRes.json();
    camId = (j.items ?? []).find((c) => c.external_id === 'cam01')?.id ?? null;
  }
  await page.goto(`${BASE}/cameras${camId ? `?open=${camId}` : ''}`, { waitUntil: 'domcontentloaded' });
  await settle(3500);
  await assertNoRawCredentials(page, 'cameras');
  await shot(page, 'ui-camera-drawer');
  if (camId) {
    current = 'camera-detail';
    await page.goto(`${BASE}/cameras/${camId}`, { waitUntil: 'domcontentloaded' });
    await settle(6000);
    await assertNoRawCredentials(page, 'camera-detail');
    await shot(page, 'ui-camera-detail');
  }
  }

  // 5. About + header badge
  if (STEPS.has('about')) {
  current = 'about';
  await page.goto(`${BASE}/about`, { waitUntil: 'domcontentloaded' });
  await settle(2000);
  const badge = await page.locator('header:has-text("MOCK SANDBOX")').count();
  note('info', `MOCK SANDBOX badge present: ${badge > 0}`);
  await shot(page, 'ui-about');
  }

  writeFileSync(join(OUT, 'ui-notes.json'), JSON.stringify(notes, null, 2));
  console.log(JSON.stringify(notes, null, 2));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
