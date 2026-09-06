/**
 * Final acceptance screenshots of the jury demo journey (MVP-PLAN §3.6 / CONTRACT §13.2) against the REAL
 * stack (Caddy on http://localhost) as jury_admin at 1440x900, plus the landing page of each jury role.
 *
 *   cd frontend && node e2e/final_journey.mjs [only=02-import-summary,09-wall-9]
 *
 * Output: docs/screenshots/final/<nn>-<step>.png, console-errors.json (per step: page errors, console
 * errors/warnings, failed requests, HTTP >= 400 responses - the target is zero) and journey.json (what
 * each shot contained: playing tiles, toast delay, hashes, PDF page counts).
 *
 * Env: SG_BASE (default http://localhost), SG_OUT (default ../docs/screenshots/final), SG_CHANNEL
 *      (chrome | msedge; a channel with H.264 and a PDF viewer is needed - the bundled Chromium has neither).
 *
 * The script drives the real UI (login form, Import from catalogue, Add to watchlist, Acknowledge with a
 * note, Export dialogs, Play recording / Export clip, Verify) and uses the API only to prepare state
 * (close the previous alerts of the demo plate so the live add raises a fresh toast) and to download the
 * two PDFs it renders through Chrome's PDF viewer.
 */
import { chromium } from 'playwright';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.SG_BASE ?? 'http://localhost';
const OUT = process.env.SG_OUT ?? join(here, '..', '..', 'docs', 'screenshots', 'final');
const BUILD = join(OUT, '.build');
const CHANNEL = process.env.SG_CHANNEL ?? 'chrome';
const ONLY = process.argv[2]?.startsWith('only=') ? new Set(process.argv[2].slice(5).split(',')) : null;
const ADMIN = ['jury_admin', 'Sentinel@Admin2026'];
const PLATE = { display: 'GJ 27 XY 3456', norm: 'GJ27XY3456' };
mkdirSync(BUILD, { recursive: true });

const settle = (ms) => new Promise((r) => setTimeout(r, ms));
const consoleLog = {};
const info = {};
const failures = [];
let current = 'init';
const note = (kind, text) => {
  if (/favicon|tile\.openstreetmap|ERR_INTERNET_DISCONNECTED|ERR_NAME_NOT_RESOLVED|Download the React DevTools/.test(text)) return;
  (consoleLog[current] ??= []).push(`[${kind}] ${text.slice(0, 300)}`);
};

async function apiLogin(username, password) {
  for (let attempt = 1; ; attempt += 1) {
    const r = await fetch(`${BASE}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) });
    if (r.ok) return (await r.json()).access_token;
    if (r.status === 429 && attempt < 8) { console.log(`login rate-limited, retrying in 20 s (${attempt}/8)`); await settle(20000); continue; }
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
  const m = buf.toString('latin1').match(/\/Type\s*\/Page(?![s\w])/g);
  return { bytes: buf.length, sha256: r.headers.get('x-sentinel-sha256'), pages: m ? m.length : 1 };
}

function wire(page) {
  page.on('pageerror', (e) => note('pageerror', e.message));
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') note(m.type(), m.text()); });
  page.on('requestfailed', (rq) => { const f = rq.failure()?.errorText ?? ''; if (!/ERR_ABORTED/.test(f)) note('requestfailed', `${rq.method()} ${rq.url()} ${f}`); });
  page.on('response', (rs) => {
    if (rs.status() < 400) return;
    // Stream probes (HLS manifest of a path whose on-demand source is still starting, WHEP negotiation) answer 4xx by
    // design and are recorded separately so the URL is known; everything else counts as an issue.
    if (/\/api\/auth\/login|\/mtx\/|\/whep/.test(rs.url())) { (info.stream_probe_4xx ??= []).push(`${current} ${rs.status()} ${rs.request().method()} ${rs.url().replace(BASE, '')}`); return; }
    note('http', `${rs.status()} ${rs.request().method()} ${rs.url()}`);
  });
}

async function uiLogin(page, user, pass, shotFile) {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('input[autocomplete="username"]', { timeout: 30000 });
  await settle(1500);
  if (shotFile) await page.screenshot({ path: shotFile });
  await page.fill('input[autocomplete="username"]', user);
  await page.fill('input[autocomplete="current-password"]', pass);
  for (let attempt = 1; ; attempt += 1) {
    await page.click('button[type="submit"]');
    try { await page.waitForURL(/\/dashboard/, { timeout: 30000 }); break; } catch (e) { if (attempt >= 4) throw e; await settle(20000); }
  }
  await settle(1500);
}
const noSkeleton = (p) => p.waitForFunction(() => document.querySelectorAll('.ant-skeleton, .ant-spin-spinning').length === 0, null, { timeout: 120000 }).catch(() => {});
async function dismissToasts(p) {
  const closers = p.locator('.ant-notification-notice-close');
  for (let i = (await closers.count()) - 1; i >= 0; i--) await closers.nth(i).click({ timeout: 1500 }).catch(() => {});
  // antd message pills auto-close after 3 s; wait them out rather than clicking.
  await p.waitForFunction(() => document.querySelectorAll('.ant-message-notice').length === 0, null, { timeout: 5000 }).catch(() => {});
  // A full-page capture taken while the page is scrolled paints the sticky header mid-page; start from the top.
  await p.evaluate(() => window.scrollTo(0, 0)).catch(() => {});
  await settle(300);
}
async function videoState(page) {
  return page.evaluate(() => {
    const vids = [...document.querySelectorAll('video')].map((v) => ({ t: Number(v.currentTime.toFixed(1)), w: v.videoWidth, h: v.videoHeight }));
    const caps = [...document.querySelectorAll('.sg-player-caption')].map((c) => c.textContent?.replace(/\s+/g, ' ').trim());
    return { playing: vids.filter((v) => v.t > 0.5 && v.w > 2).length, tiles: vids.length, caps, badge: document.body.innerText.includes('2 systems') };
  });
}
async function selectAntOption(page, labelText, optionText) {
  // antd Select inside a Form.Item labelled `labelText`: open it and pick the option by its visible text.
  const item = page.locator('.ant-form-item', { has: page.locator('label', { hasText: labelText }) }).first();
  await item.locator('.ant-select-selector').click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option', { hasText: new RegExp(`^${optionText}$`) }).first().click();
  await settle(300);
}

async function main() {
  const adminTok = await apiLogin(...ADMIN);
  const cams = (await api('GET', '/api/cameras?page_size=200', adminTok)).data?.items ?? [];
  const sandbox = cams.filter((c) => c.source === 'sandbox');
  const idOf = (ext) => sandbox.find((c) => c.external_id === String(ext))?.id;
  const browser = await chromium.launch({ channel: CHANNEL, args: ['--autoplay-policy=no-user-gesture-required'] });
  const userAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36';
  const tileRoute = async (route) => {
    try {
      const r = await fetch(route.request().url(), { headers: { 'User-Agent': 'SentinelGujarat-docs-screenshots/1.0 (+https://github.com/dynatech-consultancy/sentinel-gujarat)' } });
      await route.fulfill({ status: r.status, headers: { 'content-type': r.headers.get('content-type') ?? 'image/png' }, body: Buffer.from(await r.arrayBuffer()) });
    } catch { await route.abort(); }
  };
  const newContext = async () => {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, locale: 'en-IN', timezoneId: 'Asia/Kolkata', permissions: ['notifications'], userAgent });
    await ctx.route(/tile\.openstreetmap\.org/, tileRoute);
    return ctx;
  };
  const context = await newContext();
  const page = await context.newPage();
  wire(page);
  current = '01-login';
  await uiLogin(page, ...ADMIN, !ONLY || ONLY.has('01-login') ? join(OUT, '01-login.png') : null);
  if (!ONLY || ONLY.has('01-login')) console.log('✓ 01-login');

  const shot = async (key, path, opts = {}) => {
    if (ONLY && !ONLY.has(key)) return;
    current = key;
    try {
      if (path) await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded', timeout: 90000 });
      await noSkeleton(page);
      await settle(opts.wait ?? 2500);
      if (opts.before) await opts.before(page);
      // The stack is live: a watchlist hit can pop a toast at any moment and antd "message" pills linger for 3 s
      // after an action. Neither belongs on a documentation capture (13-alert-toast is the one that shows the toast).
      if (!opts.keepToasts) await dismissToasts(page);
      await page.screenshot({ path: join(OUT, `${key}.png`), fullPage: Boolean(opts.fullPage) });
      console.log('✓', key, info[key] ? JSON.stringify(info[key]).slice(0, 160) : '');
    } catch (e) {
      failures.push(key);
      console.error('✗', key, e.message.split('\n')[0]);
    }
  };
  const clickFirstRow = async (p) => { await p.locator('.sg-table tbody tr.ant-table-row').first().click(); await settle(2500); };
  const setLayer = async (p, label, checked) => {
    const box = p.getByRole('group', { name: 'Map layers' }).getByRole('checkbox', { name: label });
    if ((await box.count()) && (await box.isChecked()) !== checked) await box.click();
  };
  const exportViaDialog = async (p, key, format) => {
    // ExportDialog: pick the format radio, press "Export CSV|PDF", wait for the evidence hash.
    // antd icon buttons carry the icon's aria-label in their accessible name ("download Export CSV"), so match the
    // end of the name, never the start.
    const dlg = p.locator('.ant-modal:visible').last();
    if (format) await dlg.locator('.ant-radio-button-wrapper', { hasText: format.toUpperCase() }).click();
    await dlg.getByRole('button', { name: new RegExp(`Export ${format ? format.toUpperCase() : ''}$`) }).click();
    await dlg.getByText('SHA-256 (evidence hash)').waitFor({ timeout: 60000 });
    await settle(600);
    const hash = await dlg.locator('code').first().textContent();
    const saved = await dlg.getByText(/^Saved /).first().textContent();
    info[key] = { saved, sha256: hash };
  };

  // 2. Registry onboarding ------------------------------------------------------------------------------------
  await shot('02-import-summary', '/cameras/import', {
    wait: 2500,
    before: async (p) => {
      await p.getByRole('button', { name: 'Import from catalogue' }).click();
      await p.getByText(/fetched/i).first().waitFor({ timeout: 120000 });
      await settle(1200);
      info['02-import-summary'] = { summary: (await p.getByText(/fetched/i).first().textContent())?.replace(/\s+/g, ' ') };
    },
  });
  await shot('03-api-docs', '/api/docs', { wait: 4000, before: async (p) => { await p.waitForSelector('.swagger-ui .opblock', { timeout: 30000 }); info['03-api-docs'] = { operations: await p.locator('.swagger-ui .opblock').count() }; } });
  await shot('04-cameras-registry', '/cameras', { wait: 2500, before: async (p) => { await p.waitForSelector('.sg-table tbody tr.ant-table-row', { timeout: 30000 }); info['04-cameras-registry'] = { rows_on_page: await p.locator('.sg-table tbody tr.ant-table-row').count(), total_text: await p.locator('.ant-pagination-total-text').first().textContent().catch(() => null) }; } });
  await shot('05-camera-drawer', '/cameras', { wait: 2500, before: clickFirstRow });
  await shot('06-cameras-export-hash', '/cameras', {
    wait: 2000,
    before: async (p) => {
      await p.getByRole('button', { name: 'Export CSV' }).click();
      await settle(600);
      await exportViaDialog(p, '06-cameras-export-hash', 'csv');
    },
  });

  // 3. GIS map ------------------------------------------------------------------------------------------------
  await shot('07-map-layers', '/map', {
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
  await shot('08-map-view-live', '/map', {
    wait: 5000,
    before: async (p) => {
      const cluster = p.getByRole('switch', { name: 'Cluster markers' });
      if ((await cluster.count()) && (await cluster.isChecked())) { await cluster.click(); await settle(1500); }
      const marker = p.locator('.leaflet-marker-icon[title="Sachivalaya Gate 1"]').first();
      if (await marker.count()) await marker.click({ force: true });
      else await p.locator('.leaflet-marker-icon').first().click({ force: true });
      await p.locator('.leaflet-popup').first().waitFor({ timeout: 15000 });
      await settle(1500);
      info['08-map-view-live'] = { popup: (await p.locator('.leaflet-popup').first().innerText()).replace(/\s+/g, ' ').slice(0, 160) };
    },
  });

  // 4. Video wall ---------------------------------------------------------------------------------------------
  await shot('09-wall-9', '/wall', {
    wait: 3000,
    fullPage: true,
    before: async (p) => {
      await p.locator('.ant-segmented-item-label', { hasText: /^9$/ }).click();
      await settle(600);
      const auto = p.getByRole('button', { name: 'Auto-fill' });
      if (await auto.count()) await auto.click();
      let st = null;
      // All nine tiles (the H.265 camera needs its libx264 transcode to spin up); accept 8 after 75 s.
      for (let i = 0; i < 75; i++) { st = await videoState(p); if (st.playing >= 9 || (i >= 60 && st.playing >= 8)) break; await settle(1000); }
      const save = p.getByRole('button', { name: /Save layout/ });
      if (await save.count()) { await save.click().catch(() => {}); await settle(800); }
      info['09-wall-9'] = st;
    },
  });
  await shot('10-wall-hls-tile', null, {
    wait: 300,
    fullPage: true,
    before: async (p) => {
      await p.locator('.sg-player').first().getByRole('button', { name: 'Playback mode' }).click();
      await p.locator('.ant-dropdown:visible .ant-dropdown-menu-item', { hasText: /^HLS$/ }).click();
      let cap = null;
      for (let i = 0; i < 25; i++) { cap = (await videoState(p)).caps[0] ?? ''; if (/HLS/.test(cap) && /LIVE/.test(cap)) break; await settle(1000); }
      info['10-wall-hls-tile'] = { first_tile_caption: cap };
    },
  });

  // 5. Camera page with live reads ----------------------------------------------------------------------------
  await shot('11-camera-live-reads', idOf(1) ? `/cameras/${idOf(1)}` : '/cameras', {
    wait: 3000,
    before: async (p) => {
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
      info['11-camera-live-reads'] = st;
    },
  });

  // 6. Watchlist add -> alert ---------------------------------------------------------------------------------
  const doWatchlist = !ONLY || ONLY.has('12-watchlist-add') || ONLY.has('13-alert-toast');
  if (doWatchlist) {
    // Prepare: the demo plate must not be on the active watchlist and must have no open alert (an open alert
    // absorbs the next reads as alert_update instead of raising a fresh alert + toast).
    const existing = (await api('GET', `/api/watchlist?q=${PLATE.norm}&is_active=all`, adminTok)).data?.items ?? [];
    for (const w of existing) await api('DELETE', `/api/watchlist/${w.id}`, adminTok);
    const open = (await api('GET', `/api/alerts?status=new,acknowledged&plate=${PLATE.norm}&page_size=200`, adminTok)).data?.items ?? [];
    for (const a of open) await api('POST', `/api/alerts/${a.id}/close`, adminTok, { outcome: 'other', note: 'closed before the final demo run' });
    info.prep = { watchlist_entries_removed: existing.length, alerts_closed: open.length };
  }
  let tAdd = 0;
  await shot('12-watchlist-add', '/watchlist', {
    wait: 2500,
    before: async (p) => {
      await p.getByRole('button', { name: 'Add to watchlist' }).first().click();
      await settle(700);
      await p.getByPlaceholder('GJ 27 XY 3456').fill(PLATE.display);
      await p.getByPlaceholder('White Maruti Swift – FIR 123/2026').fill('Silver hatchback seen leaving the scene – FIR 118/2026');
      await selectAntOption(p, 'Reason', 'Suspect');
      await selectAntOption(p, 'Priority', 'High');
      await settle(700);
    },
  });
  if (doWatchlist) {
    current = '12-watchlist-add';
    tAdd = Date.now();
    await page.getByRole('button', { name: 'Add entry' }).click();
    await page.locator('.ant-modal:visible').first().waitFor({ state: 'hidden', timeout: 20000 }).catch(() => {});
    await settle(800);
  }
  if (!ONLY || ONLY.has('13-alert-toast')) {
    current = '13-alert-toast';
    try {
      await page.goto(`${BASE}/alerts`, { waitUntil: 'domcontentloaded' });
      await noSkeleton(page);
      let seen = false;
      for (let i = 0; i < 300; i++) {
        if ((await page.locator('.ant-notification-notice', { hasText: PLATE.display }).count()) > 0) { seen = true; break; }
        await settle(500);
      }
      await settle(400);
      await page.screenshot({ path: join(OUT, '13-alert-toast.png') });
      info['13-alert-toast'] = { seen, after_s: Math.round((Date.now() - tAdd) / 1000) };
      console.log(seen ? '✓' : '✗', '13-alert-toast', JSON.stringify(info['13-alert-toast']));
      if (!seen) failures.push('13-alert-toast');
    } catch (e) { failures.push('13-alert-toast'); console.error('✗ 13-alert-toast', e.message); }
  }
  await shot('14-alerts-list', '/alerts', { wait: 4000 });
  await shot('15-alert-acknowledge', '/alerts', {
    wait: 3000,
    before: async (p) => {
      await p.locator('.sg-table tbody tr.ant-table-row', { hasText: PLATE.display }).first().click();
      await settle(2500);
      await p.locator('.ant-drawer:visible').getByRole('button', { name: 'Acknowledge' }).click();
      await settle(600);
      await p.getByPlaceholder('Unit dispatched from Sector 7 PS').fill('Unit dispatched from Sector 7 PS');
      await settle(400);
    },
  });
  if (!ONLY || ONLY.has('15-alert-acknowledge')) {
    current = '15-alert-acknowledge';
    await page.locator('.ant-modal:visible').getByRole('button', { name: 'Acknowledge' }).click();
    await page.getByText('Alert acknowledged').first().waitFor({ timeout: 15000 }).catch(() => {});
    await settle(800);
  }

  // 7. Vehicle search -> route -> PDF ------------------------------------------------------------------------
  await shot('16-vehicle-search', `/vehicles?q=${PLATE.norm}`, {
    wait: 4000,
    before: async (p) => {
      const confirm = p.locator('.ant-segmented-item', { hasText: /^Confirm$/ }).first();
      if (await confirm.count()) { await confirm.click(); await settle(1500); }
    },
  });
  await shot('17-route', `/vehicles/${PLATE.norm}/route`, { wait: 7000, fullPage: true, before: async (p) => { info['17-route'] = { stops: await p.locator('tbody tr.ant-table-row').count(), markers: await p.locator('.leaflet-marker-icon').count(), speed_flags: await p.locator('.ant-tag', { hasText: /speed|implausible/i }).count() }; } });
  await shot('18-route-export-hash', null, {
    wait: 500,
    before: async (p) => {
      await p.getByRole('button', { name: 'Export PDF' }).click();
      await settle(600);
      await exportViaDialog(p, '18-route-export-hash', 'pdf');
    },
  });
  const pdfShot = async (key, apiPath, file, pageNo, view = 'zoom=110') => {
    if (ONLY && !ONLY.has(key)) return;
    current = key;
    try {
      const meta = await download(apiPath, adminTok, file);
      const target = pageNo === 'last' ? meta.pages : pageNo;
      const p = await context.newPage();
      await p.goto(`${pathToFileURL(file).href}#page=${target}&toolbar=0&${view}`, { waitUntil: 'load' });
      await settle(4000);
      await p.screenshot({ path: join(OUT, `${key}.png`) });
      await p.close();
      info[key] = { file: file.split(/[\\/]/).pop(), page: target, pages: meta.pages, bytes: meta.bytes, sha256: meta.sha256 };
      console.log('✓', key, `page ${target}/${meta.pages}`);
    } catch (e) { failures.push(key); console.error('✗', key, e.message.split('\n')[0]); }
  };
  await pdfShot('19-route-pdf', `/api/vehicles/${PLATE.norm}/route.pdf`, join(BUILD, `route_${PLATE.norm}.pdf`), 1);

  // 8. Output report ------------------------------------------------------------------------------------------
  await shot('20-reports-output-csv-hash', '/reports', {
    wait: 3000,
    before: async (p) => {
      await p.getByRole('button', { name: 'Generate CSV / PDF' }).first().click();
      await settle(600);
      await exportViaDialog(p, '20-reports-output-csv-hash', 'csv');
    },
  });
  if (!ONLY || ONLY.has('20-reports-output-csv-hash')) { await page.locator('.ant-modal:visible').getByRole('button', { name: 'Done' }).click().catch(() => {}); await settle(500); }
  const to = new Date();
  const from = new Date(to.getTime() - 2 * 3600 * 1000);
  await pdfShot('21-output-report-pdf', `/api/reports/detections?format=pdf&from=${from.toISOString()}&to=${to.toISOString()}`, join(BUILD, 'detections_last2h.pdf'), 1);

  // 9. Recording playback from the alert, evidence clip, verify -------------------------------------------------
  await shot('22-alert-play-recording', '/alerts', {
    wait: 3000,
    before: async (p) => {
      await p.locator('.sg-table tbody tr.ant-table-row', { hasText: PLATE.display }).first().click();
      await settle(2500);
      const play = p.locator('.ant-drawer:visible').getByRole('button', { name: 'Play recording' });
      await play.waitFor({ timeout: 15000 });
      if (await play.isDisabled()) throw new Error('Play recording is disabled (no recording covers the read yet)');
      await play.click();
      let st = null;
      for (let i = 0; i < 30; i++) {
        st = await p.evaluate(() => { const v = document.querySelector('.ant-modal video'); return v ? { rs: v.readyState, t: Number(v.currentTime.toFixed(1)), w: v.videoWidth } : null; });
        if (st && st.rs >= 2 && st.t > 0.3) break;
        await settle(1000);
      }
      info['22-alert-play-recording'] = st;
    },
  });
  await shot('23-export-clip-hash', null, {
    wait: 300,
    before: async (p) => {
      await p.locator('.ant-modal:visible').getByRole('button', { name: /Export 30 s clip/ }).click();
      await p.locator('.ant-modal:visible').getByText(/^Clip #\d+ stored/).waitFor({ timeout: 60000 });
      await settle(800);
      info['23-export-clip-hash'] = { text: await p.locator('.ant-modal:visible .ant-alert-message').first().textContent() };
    },
  });
  await shot('24-reports-history-verify', '/reports', {
    wait: 3000,
    fullPage: true,
    before: async (p) => {
      await p.getByRole('button', { name: /Verify$/ }).first().click();
      await p.getByRole('button', { name: /Verified$/ }).first().waitFor({ timeout: 30000 });
      await settle(600);
      info['24-reports-history-verify'] = { verified_buttons: await p.getByRole('button', { name: /Verified$/ }).count() };
    },
  });

  // 10. Health · Gap analysis · Dashboard · Audit --------------------------------------------------------------
  await shot('25-health', '/health', { wait: 4000 });
  await shot('26-gap-analysis', '/gap-analysis', { wait: 3000, fullPage: true, before: async (p) => { await p.waitForFunction(() => document.querySelectorAll('.ant-skeleton').length === 0, null, { timeout: 90000 }); await settle(4000); } });
  await shot('27-dashboard', '/dashboard', { wait: 5000 });
  await shot('28-audit', '/audit', { wait: 3000, before: async (p) => { info['28-audit'] = { actions_visible: [...new Set((await p.locator('.sg-table tbody tr.ant-table-row').allInnerTexts()).map((t) => t.match(/[a-z_]+\.[a-z_]+/)?.[0]).filter(Boolean))] }; } });
  await page.close();
  await context.close();

  // 11. Landing page of each jury role (fresh session each) ----------------------------------------------------
  for (const [key, user, pass] of [['29-landing-jury_admin', 'jury_admin', 'Sentinel@Admin2026'], ['30-landing-jury_operator', 'jury_operator', 'Sentinel@Ops2026'], ['31-landing-jury_viewer', 'jury_viewer', 'Sentinel@View2026']]) {
    if (ONLY && !ONLY.has(key)) continue;
    current = key;
    const ctx = await newContext();
    const p = await ctx.newPage();
    wire(p);
    try {
      await uiLogin(p, user, pass, null);
      await noSkeleton(p);
      await settle(4000);
      await p.screenshot({ path: join(OUT, `${key}.png`) });
      info[key] = { url: p.url(), header: (await p.locator('header, .ant-layout-header').first().innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 120) };
      console.log('✓', key, JSON.stringify(info[key]).slice(0, 160));
    } catch (e) { failures.push(key); console.error('✗', key, e.message.split('\n')[0]); }
    await ctx.close();
  }
  await browser.close();

  // An `only=` pass replaces the entries it re-took and keeps the rest of the previous record.
  let prevInfo = {}, prevLog = {}, prevFailures = [];
  if (ONLY) {
    try { const j = JSON.parse(readFileSync(join(OUT, 'journey.json'), 'utf8')); prevInfo = j.info ?? {}; prevFailures = (j.failures ?? []).filter((f) => !ONLY.has(f)); } catch { /* first run */ }
    try { prevLog = JSON.parse(readFileSync(join(OUT, 'console-errors.json'), 'utf8')); for (const k of ONLY) delete prevLog[k]; } catch { /* first run */ }
  }
  const mergedLog = { ...prevLog, ...consoleLog };
  writeFileSync(join(OUT, 'console-errors.json'), JSON.stringify(mergedLog, null, 2));
  writeFileSync(join(OUT, 'journey.json'), JSON.stringify({ base: BASE, taken_at: new Date().toISOString(), info: { ...prevInfo, ...info, stream_probe_4xx: [...(prevInfo.stream_probe_4xx ?? []).filter((l) => !ONLY || ![...ONLY].some((k) => l.startsWith(k))), ...(info.stream_probe_4xx ?? [])] }, failures: [...prevFailures, ...failures] }, null, 2));
  const pagesWithErrors = Object.entries(consoleLog).filter(([, v]) => v.length);
  console.log(`console/network issues on ${pagesWithErrors.length} steps:`);
  for (const [k, v] of pagesWithErrors) console.log(`  ${k}: ${v.length}\n    ${[...new Set(v)].slice(0, 6).join('\n    ')}`);
  console.log(`done → ${OUT}${failures.length ? ` (failed: ${failures.join(', ')})` : ''}`);
  process.exit(failures.length ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
