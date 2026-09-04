/**
 * Screenshot every page of the SPA against the in-memory mock (VITE_MOCK=1).
 *
 *   npm run dev:mock            (in another terminal, or set SG_BASE)
 *   node e2e/screenshots.mjs    → docs/screenshots/mock/<page>.png at 1440×900
 *
 * Env: SG_BASE (default http://localhost:5173), SG_OUT (default ../docs/screenshots/mock),
 *      SG_USER / SG_PASS (default jury_admin / Sentinel@Admin2026), SG_ONLY=comma list of page keys.
 */
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.SG_BASE ?? 'http://localhost:5173';
const OUT = resolve(process.env.SG_OUT ?? join(here, '..', '..', 'docs', 'screenshots', 'mock'));
const USER = process.env.SG_USER ?? 'jury_admin';
const PASS = process.env.SG_PASS ?? 'Sentinel@Admin2026';
const ONLY = process.env.SG_ONLY ? new Set(process.env.SG_ONLY.split(',')) : null;
mkdirSync(OUT, { recursive: true });

const settle = (ms) => new Promise((r) => setTimeout(r, ms));

/** page key → { path, before(page)?, wait?: ms, fullPage? } */
const PAGES = [
  { key: 'dashboard', path: '/dashboard', wait: 3500 },
  {
    key: 'alert-toast',
    path: '/dashboard',
    wait: 2500,
    before: async (page) => {
      await page.evaluate(() => window.__sgMock?.emitAlert('GJ27XY3456'));
      await settle(1200);
    },
  },
  { key: 'map', path: '/map', wait: 4000 },
  { key: 'wall', path: '/wall', wait: 4000 },
  { key: 'cameras', path: '/cameras', wait: 2000 },
  { key: 'camera-drawer', path: '/cameras?open=3', wait: 3000 },
  { key: 'camera-detail', path: '/cameras/1', wait: 4500 },
  { key: 'import', path: '/cameras/import', wait: 2000 },
  { key: 'health', path: '/health', wait: 2500 },
  { key: 'gap-analysis', path: '/gap-analysis', wait: 4500, fullPage: true },
  { key: 'detections', path: '/detections', wait: 2500 },
  {
    key: 'detection-drawer',
    path: '/detections',
    wait: 2500,
    before: async (page) => {
      await page.locator('.sg-table tbody tr.ant-table-row').first().click();
      await settle(1800);
    },
  },
  { key: 'vehicles', path: '/vehicles?q=GJ01AB1234', wait: 3500 },
  { key: 'route', path: '/vehicles/GJ01AB1234/route', wait: 4500, fullPage: true },
  { key: 'watchlist', path: '/watchlist', wait: 2000 },
  {
    key: 'watchlist-add',
    path: '/watchlist',
    wait: 2000,
    before: async (page) => {
      await page.getByRole('button', { name: 'Add to watchlist' }).first().click();
      await settle(600);
      await page.getByPlaceholder('GJ 27 XY 3456').fill('GJ 27 XY 3456');
      await settle(600);
    },
  },
  { key: 'alerts', path: '/alerts', wait: 2500 },
  {
    key: 'alert-drawer',
    path: '/alerts',
    wait: 2500,
    before: async (page) => {
      await page.locator('.sg-table tbody tr.ant-table-row').first().click();
      await settle(2500);
    },
  },
  { key: 'events', path: '/events', wait: 2000 },
  { key: 'reports', path: '/reports', wait: 2500, fullPage: true },
  { key: 'audit', path: '/audit', wait: 2000 },
  {
    key: 'audit-diff',
    path: '/audit?action=camera.',
    wait: 2000,
    before: async (page) => {
      const btn = page.locator('.sg-table .ant-table-row-expand-icon').first();
      await btn.click();
      await settle(800);
    },
  },
  { key: 'settings', path: '/settings', wait: 2000, fullPage: true },
  { key: 'settings-webhooks', path: '/settings/webhooks', wait: 2000 },
  { key: 'settings-api-keys', path: '/settings/api-keys', wait: 2000 },
  { key: 'users', path: '/users', wait: 2000 },
  { key: 'about', path: '/about', wait: 2000 },
  { key: 'global-search', path: '/dashboard', wait: 2500, before: async (page) => { await page.keyboard.press('Control+K'); await settle(400); await page.keyboard.type('GJ 01 AB'); await settle(700); } },
];

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
  await page.evaluate(() => {
    try {
      localStorage.setItem('sg.desktop_banner_dismissed', '1');
    } catch {
      /* ignore */
    }
  });
  await page.waitForSelector('input[autocomplete="username"]', { timeout: 30_000 });
  await settle(800);
  if (!ONLY || ONLY.has('login')) await page.screenshot({ path: join(OUT, 'login.png') });
  await page.fill('input[autocomplete="username"]', USER);
  await page.fill('input[autocomplete="current-password"]', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/dashboard/, { timeout: 30_000 });
}

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, locale: 'en-IN', timezoneId: 'Asia/Kolkata' });
  const page = await context.newPage();
  page.on('pageerror', (e) => console.error('[pageerror]', e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/favicon|tile\.openstreetmap|net::ERR/.test(m.text())) console.error('[console]', m.text());
  });
  await login(page);
  console.log('logged in as', USER);
  const failures = [];
  for (const p of PAGES) {
    if (ONLY && !ONLY.has(p.key)) continue;
    try {
      await page.goto(`${BASE}${p.path}`, { waitUntil: 'domcontentloaded' });
      await settle(p.wait ?? 2000);
      if (p.before) await p.before(page);
      await page.screenshot({ path: join(OUT, `${p.key}.png`), fullPage: Boolean(p.fullPage) });
      console.log('✓', p.key);
    } catch (e) {
      failures.push(p.key);
      console.error('✗', p.key, e.message);
    }
  }
  await browser.close();
  console.log(`done → ${OUT}${failures.length ? ` (failed: ${failures.join(', ')})` : ''}`);
  process.exit(failures.length ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
