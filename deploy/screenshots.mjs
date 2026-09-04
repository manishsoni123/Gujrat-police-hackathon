// Sentinel Gujarat - page screenshot capture for the submission documents.
//
// Runs inside the Playwright image (see `make screenshots`):
//   docker run --rm --add-host=host.docker.internal:host-gateway \
//     -v $PWD/deploy/screenshots.mjs:/work/screenshots.mjs:ro -v $PWD/docs/screenshots:/work/out \
//     -e BASE_URL=http://host.docker.internal -w /work mcr.microsoft.com/playwright:v1.47.2-jammy node /work/screenshots.mjs
//
// Env: BASE_URL (default http://localhost), SG_USERNAME / SG_PASSWORD (default jury_admin),
//      OUT_DIR (default ./out), WIDTH/HEIGHT (default 1600x900), ROUTES (comma list override).
// Logs in through POST /api/auth/login (sets the sg_session cookie in the browser context and
// stores the token under localStorage "sg.token" as the SPA does), then visits every route and
// writes <n>_<route>.png. Exit code 1 if any page failed.

import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';

const BASE_URL = (process.env.BASE_URL || 'http://localhost').replace(/\/$/, '');
const USERNAME = process.env.SG_USERNAME || 'jury_admin';
const PASSWORD = process.env.SG_PASSWORD || 'Sentinel@Admin2026';
const OUT_DIR = process.env.OUT_DIR || './out';
const WIDTH = Number(process.env.WIDTH || 1600);
const HEIGHT = Number(process.env.HEIGHT || 900);

const DEFAULT_ROUTES = [
  '/login',
  '/dashboard',
  '/cameras',
  '/cameras/import',
  '/map',
  '/wall',
  '/detections',
  '/vehicles',
  '/watchlist',
  '/alerts',
  '/events',
  '/reports',
  '/health',
  '/gap-analysis',
  '/audit',
  '/settings',
  '/about',
];
const ROUTES = process.env.ROUTES ? process.env.ROUTES.split(',').map((r) => r.trim()) : DEFAULT_ROUTES;

function fileName(index, route) {
  const slug = route === '/' ? 'root' : route.replace(/^\//, '').replace(/[^a-z0-9]+/gi, '_');
  return `${String(index + 1).padStart(2, '0')}_${slug || 'root'}.png`;
}

async function login(context) {
  const res = await context.request.post(`${BASE_URL}/api/auth/login`, {
    data: { username: USERNAME, password: PASSWORD },
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok()) {
    throw new Error(`login failed: HTTP ${res.status()} ${await res.text()}`);
  }
  const body = await res.json();
  if (!body.access_token) {
    throw new Error('login response has no access_token');
  }
  // The SPA reads the token from localStorage; the sg_session cookie is already in the context.
  await context.addInitScript((token) => {
    try {
      window.localStorage.setItem('sg.token', token);
    } catch (_) {
      /* storage may be unavailable in some contexts */
    }
  }, body.access_token);
  return body.user;
}

async function main() {
  mkdirSync(OUT_DIR, { recursive: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT }, ignoreHTTPSErrors: true });
  let failures = 0;
  try {
    const user = await login(context);
    console.log(`logged in as ${user.username} (${user.role}) at ${BASE_URL}`);
    const page = await context.newPage();
    for (const [i, route] of ROUTES.entries()) {
      const target = `${BASE_URL}${route}`;
      const out = `${OUT_DIR}/${fileName(i, route)}`;
      try {
        if (route === '/login') {
          // Capture the login page in a clean context so the redirect-to-dashboard does not kick in.
          const clean = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT } });
          const p = await clean.newPage();
          await p.goto(target, { waitUntil: 'networkidle', timeout: 30000 });
          await p.screenshot({ path: out, fullPage: false });
          await clean.close();
        } else {
          await page.goto(target, { waitUntil: 'networkidle', timeout: 45000 });
          // Give maps, charts and video tiles a moment to render.
          await page.waitForTimeout(2500);
          await page.screenshot({ path: out, fullPage: false });
        }
        console.log(`ok   ${route} -> ${out}`);
      } catch (err) {
        failures += 1;
        console.error(`FAIL ${route}: ${err.message}`);
      }
    }
  } finally {
    await browser.close();
  }
  if (failures > 0) {
    console.error(`${failures} page(s) failed`);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
