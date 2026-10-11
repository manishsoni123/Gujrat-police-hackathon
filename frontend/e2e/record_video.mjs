/**
 * Records the two mandatory demonstration videos (guide §7.3) as a REAL screen recording of the
 * running platform - Playwright drives the actual UI in a real Chrome window and captures the frames
 * the browser paints, including the WebRTC/HLS tiles and the live ANPR reads. Nothing is composited,
 * re-timed or animated; the only overlay is a lower-third caption that replaces the voice-over.
 *
 *   cd frontend && node e2e/record_video.mjs video1      # own feed          (target <= 3:00)
 *   cd frontend && node e2e/record_video.mjs video2      # government feed   (target <= 5:00)
 *
 * Env:
 *   SG_BASE      base URL, default http://localhost (use the HOSTED url for the real submission)
 *   SG_OUT       output directory, default ../docs/export/video
 *   SG_CHANNEL   chrome | msedge (default chrome - the bundled Chromium has no H.264 and cannot
 *                play the WebRTC tiles, so a real channel is mandatory for an honest recording)
 *   SG_USER / SG_PASSWORD   login, default jury_admin from deploy/.env
 *   SG_ALERT_WAIT_S         how long to wait for a live watchlist alert (default 150)
 *   SG_NO_CAPTION=1         record without the caption overlay
 *
 * Output: <SG_OUT>/Sentinel-Gujarat_Video<N>_*.webm (+ .mp4 when ffmpeg is reachable) and
 * run-video<N>.json - an honest record of what each step actually showed (tiles playing, reads on
 * screen, whether the alert fired live, hashes, row counts). Read that file before you upload:
 * any step that did not happen is listed under "degraded".
 */
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync, renameSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const MODE = (process.argv[2] ?? '').toLowerCase();
if (!['video1', 'video2'].includes(MODE)) {
  console.error('usage: node e2e/record_video.mjs video1|video2');
  process.exit(2);
}
const N = MODE === 'video1' ? 1 : 2;
const BASE = process.env.SG_BASE ?? 'http://localhost';
const OUT = process.env.SG_OUT ?? join(here, '..', '..', 'docs', 'export', 'video');
const CHANNEL = process.env.SG_CHANNEL ?? 'chrome';
const USER = process.env.SG_USER ?? 'jury_admin';
const PASSWORD = process.env.SG_PASSWORD ?? 'Sentinel@Admin2026';
// Video 1 has a hard 3:00 limit (guide §7.3), so the wait for the vehicle's next pass is capped much
// tighter there than in Video 2. Both are real waits on real reads - never a speed-up or a cut.
const ALERT_WAIT_S = Number(process.env.SG_ALERT_WAIT_S ?? (MODE === 'video1' ? 45 : 70));
const CAPTIONS = process.env.SG_NO_CAPTION !== '1';
const VIDEO_DIR = join(OUT, `.raw-video${N}`);
mkdirSync(VIDEO_DIR, { recursive: true });

const settle = (ms) => new Promise((r) => setTimeout(r, ms));
const run = { mode: MODE, base: BASE, started_at: new Date().toISOString(), steps: [], degraded: [] };
const degraded = (step, why) => {
  run.degraded.push(`${step}: ${why}`);
  console.error('  ! ', step, '-', why);
};

/* ---------------------------------------------------------------- API helpers (state only) ---- */

async function apiLogin() {
  for (let attempt = 1; ; attempt += 1) {
    const r = await fetch(`${BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: USER, password: PASSWORD }),
    });
    if (r.ok) return (await r.json()).access_token;
    if (r.status === 429 && attempt < 8) { await settle(20000); continue; }
    throw new Error(`login as ${USER} failed: HTTP ${r.status} - is the stack up at ${BASE}?`);
  }
}
async function api(method, path, token, json) {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(json ? { 'Content-Type': 'application/json' } : {}) },
    body: json ? JSON.stringify(json) : undefined,
  });
  return { status: r.status, data: await r.json().catch(() => null) };
}

/**
 * The plate to put on the watchlist on camera. It must be one this video's own feed is really
 * producing, so the alert that follows is a genuine match on a genuine read.
 *
 * `/api/detections` does NOT honour a `source` query parameter (only the report endpoints do), so the
 * government/own split is made here from each read's own camera id. Getting this wrong would put a
 * synthetic own-gate plate on screen while the caption says "government feed".
 *
 * Plates already read more than once are preferred: a recurring vehicle is the one most likely to be
 * read again inside the recording, which is what makes the alert fire live rather than never.
 */
async function pickLivePlate(token, wantSource) {
  const cams = (await api('GET', '/api/cameras?page_size=200', token)).data?.items ?? [];
  const sourceOf = new Map(cams.map((c) => [c.id, c.source]));
  const reads = (await api('GET', '/api/detections?page_size=200', token)).data?.items ?? [];
  const mine = reads.filter((r) => r.is_valid_format && sourceOf.get(r.camera?.id) === wantSource);
  const wl = (await api('GET', '/api/watchlist?page_size=200', token)).data?.items ?? [];
  const active = new Set(wl.filter((w) => w.is_active ?? w.active).map((w) => w.plate_norm));
  // Both feeds are loops (the own clip ~90 s, the organiser recordings far longer), so a plate's next
  // pass is predictable from the gaps between its own past reads. Choose the candidate whose next pass
  // is due soonest but not immediately - that is what keeps the honest "wait for the alert" beat short
  // enough for Video 1's three-minute limit without ever staging the alert.
  const now = Date.now();
  const byPlate = new Map();
  for (const r of mine) {
    const ts = byPlate.get(r.plate_norm) ?? [];
    ts.push(new Date(r.captured_at).getTime());
    byPlate.set(r.plate_norm, ts);
  }
  const cands = [];
  for (const [plate, tsRaw] of byPlate) {
    if (active.has(plate) || tsRaw.length < 2) continue;
    const ts = [...tsRaw].sort((a, b) => b - a);
    const gaps = [];
    for (let i = 0; i < ts.length - 1; i += 1) gaps.push((ts[i] - ts[i + 1]) / 1000);
    gaps.sort((a, b) => a - b);
    const period = gaps[Math.floor(gaps.length / 2)];          // median gap = the loop period
    const since = (now - ts[0]) / 1000;
    let due = period - (since % period);                        // seconds until the next expected pass
    if (due < 20) due += period;                                // too close: the form is still being filled
    cands.push({ plate, reads: ts.length, period: Math.round(period), due_in_s: Math.round(due) });
  }
  cands.sort((a, b) => a.due_in_s - b.due_in_s);
  const best = cands[0] ?? null;
  // Never fall back to a plate that is already on the active watchlist: re-adding it is rejected as a
  // duplicate, so no new alert would fire and the video would show a stale one. Better to report none.
  const fallback = [...byPlate.keys()].find((p) => !active.has(p)) ?? null;
  return {
    plate: best?.plate ?? fallback,
    source: wantSource,
    valid_reads_from_this_source: mine.length,
    cameras: [...new Set(mine.map((r) => r.camera?.name))].slice(0, 6),
    timing: best,
    candidates: cands.slice(0, 5),
  };
}

/* ------------------------------------------------------------------- caption overlay ---------- */

const CAPTION_SCRIPT = `
(() => {
  if (window.__sgCaptionReady) return;
  window.__sgCaptionReady = true;
  const mount = () => {
    if (document.getElementById('sg-caption-root')) return;
    const root = document.createElement('div');
    root.id = 'sg-caption-root';
    root.innerHTML = \`
      <style>
        #sg-caption-root { position: fixed; inset: auto 0 0 0; z-index: 2147483647; pointer-events: none;
          font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }
        #sg-cap { margin: 0 0 28px 32px; display: inline-block; max-width: 62vw; padding: 14px 20px;
          background: rgba(9,17,31,.90); border-left: 4px solid #1668dc; border-radius: 6px;
          color: #fff; box-shadow: 0 8px 28px rgba(0,0,0,.45); opacity: 0; transition: opacity .35s; }
        #sg-cap.on { opacity: 1; }
        #sg-cap b { display: block; font-size: 19px; font-weight: 650; letter-spacing: .2px; }
        #sg-cap span { display: block; margin-top: 5px; font-size: 15px; line-height: 1.45; color: #cfd8e6; }
        #sg-badge { position: fixed; right: 26px; bottom: 26px; padding: 7px 13px; border-radius: 5px;
          background: rgba(9,17,31,.86); color: #9fb4d0; font-size: 12.5px; letter-spacing: .3px; }
        #sg-badge i { font-style: normal; color: #ff4d4f; }
      </style>
      <div id="sg-cap"></div>
      <div id="sg-badge"><i>&#9679;</i> Sentinel Gujarat 1.0.0-phase1 &middot; live platform recording</div>\`;
    document.body.appendChild(root);
  };
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);
  window.__sgCaption = (title, text) => {
    mount();
    const el = document.getElementById('sg-cap');
    if (!el) return;
    if (!title) { el.classList.remove('on'); return; }
    el.innerHTML = '<b></b><span></span>';
    el.querySelector('b').textContent = title;
    el.querySelector('span').textContent = text || '';
    el.classList.add('on');
  };
})();`;

/* ------------------------------------------------------------------------- the recording ------ */

async function main() {
  const token = await apiLogin();
  const health = (await api('GET', '/api/healthz', token)).data ?? {};
  run.platform = { version: health.version, db: health.db, mediamtx: health.mediamtx, anpr_workers: health.anpr_workers };
  console.log(`platform ${health.version ?? '?'} · db ${health.db ?? '?'} · mediamtx ${health.mediamtx ?? '?'} · anpr workers ${health.anpr_workers ?? 0}`);
  if (!health.anpr_workers) degraded('preflight', 'no ANPR worker is heartbeating - live reads will not appear on screen');

  const cams = (await api('GET', '/api/cameras?page_size=200', token)).data?.items ?? [];
  const sandbox = cams.filter((c) => c.source === 'sandbox');
  const own = cams.find((c) => c.source === 'own') ?? cams.find((c) => /OWN-GATE/i.test(c.external_id ?? ''));
  run.registry = { total: cams.length, sandbox: sandbox.length, own: own?.name ?? null };
  console.log(`registry: ${cams.length} cameras (${sandbox.length} sandbox, own gate ${own ? 'present' : 'MISSING'})`);

  // For the live-reads beat, open the camera that is actually producing plates right now - not simply
  // the first ANPR-enabled one, which on a wide-angle preset can sit empty for the whole recording.
  const recent = (await api('GET', '/api/detections?page_size=200', token)).data?.items ?? [];
  const wantSource = MODE === 'video1' ? 'own' : 'sandbox';
  const sourceOf = new Map(cams.map((c) => [c.id, c.source]));
  const yieldByCam = new Map();
  for (const r of recent) {
    if (!r.is_valid_format || sourceOf.get(r.camera?.id) !== wantSource) continue;
    yieldByCam.set(r.camera.id, (yieldByCam.get(r.camera.id) ?? 0) + 1);
  }
  const pool = MODE === 'video1' ? (own ? [own] : []) : sandbox;
  const anprCams = [...pool].sort((a, b) => (yieldByCam.get(b.id) ?? 0) - (yieldByCam.get(a.id) ?? 0));
  run.read_yield = Object.fromEntries([...yieldByCam].map(([id, n]) => [cams.find((c) => c.id === id)?.name ?? id, n]));
  if (!anprCams.length) degraded('preflight', `no ${wantSource} camera found`);
  else if (!yieldByCam.get(anprCams[0].id)) degraded('preflight', `no camera on the ${wantSource} feed has produced a valid plate recently - the live-reads panel may be empty on screen`);
  console.log(`live-reads camera: ${anprCams[0]?.name ?? '-'} (${yieldByCam.get(anprCams[0]?.id) ?? 0} recent valid reads)`);

  const picked = await pickLivePlate(token, MODE === 'video1' ? 'own' : 'sandbox');
  run.plate_pick = picked;
  if (!picked.plate) degraded('preflight', `no valid-format plate has been read on the ${picked.source} feed yet - the alert step cannot fire live`);
  console.log(`plate for the live watchlist add: ${picked.plate ?? 'NONE'} (${picked.valid_reads_from_this_source} valid ${picked.source} reads, cameras: ${picked.cameras.join(', ') || '-'})`);
  if (picked.timing) console.log(`  loop period ~${picked.timing.period}s, next pass expected in ~${picked.timing.due_in_s}s`);
  // Wait for the predicted pass plus a full extra period of slack, so a missed pass still lands.
  const alertWait = picked.timing
    ? Math.min(MODE === 'video1' ? 95 : 240, picked.timing.due_in_s + picked.timing.period + 20)
    : ALERT_WAIT_S;

  const browser = await chromium.launch({
    channel: CHANNEL,
    args: ['--autoplay-policy=no-user-gesture-required', '--disable-features=IsolateOrigins,site-per-process'],
  });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    locale: 'en-IN',
    timezoneId: 'Asia/Kolkata',
    permissions: ['notifications'],
    recordVideo: { dir: VIDEO_DIR, size: { width: 1920, height: 1080 } },
  });
  if (CAPTIONS) await context.addInitScript(CAPTION_SCRIPT);
  // OSM tiles rate-limit an automated client; serve them with a polite UA so the map is never blank.
  await context.route(/tile\.openstreetmap\.org/, async (route) => {
    try {
      const r = await fetch(route.request().url(), { headers: { 'User-Agent': 'SentinelGujarat-demo-video/1.0' } });
      await route.fulfill({ status: r.status, headers: { 'content-type': r.headers.get('content-type') ?? 'image/png' }, body: Buffer.from(await r.arrayBuffer()) });
    } catch { await route.abort(); }
  });

  const page = await context.newPage();
  const t0 = Date.now();
  const at = () => { const s = Math.round((Date.now() - t0) / 1000); return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`; };

  const caption = async (title, text) => {
    if (!CAPTIONS) return;
    await page.evaluate(([a, b]) => window.__sgCaption?.(a, b), [title, text]).catch(() => {});
  };
  const go = async (path) => {
    await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    // Both waits are best-effort: several pages keep a live widget spinning for ever, and every second
    // spent timing out here is dead air in a video with a three-minute limit.
    await page.waitForFunction(() => document.querySelectorAll('.ant-skeleton, .ant-spin-spinning').length === 0, null, { timeout: 6000 }).catch(() => {});
    await page.waitForSelector('.sg-table tbody tr.ant-table-row, .ant-empty, .sg-player, .leaflet-container, .ant-descriptions', { timeout: 8000 }).catch(() => {});
  };
  /** One storyboard beat: show the caption, do the work, hold the frame for `secs`. */
  const step = async (key, title, text, secs, body) => {
    const started = at();
    console.log(`[${started}] ${key} - ${title}`);
    await caption(title, text);
    let detail = null;
    try {
      if (body) detail = await body();
    } catch (e) {
      degraded(key, e.message.split('\n')[0]);
    }
    await settle(secs * 1000);
    run.steps.push({ key, at: started, title, detail: detail ?? null });
  };

  /* ------------------------------------------------------------------------ shared beats ----- */

  const login = async () => {
    await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('input[autocomplete="username"]', { timeout: 30000 });
    await caption(
      `Sentinel Gujarat - Video ${N}: ${MODE === 'video1' ? 'our own feed' : 'the government sandbox feed'}`,
      'Dynatech Consultancy · Gujarat Police Innovation Challenge 2026 · Model 1 + Model 2. Recorded live from the running platform.',
    );
    await settle(MODE === 'video1' ? 3500 : 5000);
    await page.fill('input[autocomplete="username"]', USER);
    await settle(700);
    await page.fill('input[autocomplete="current-password"]', PASSWORD);
    await settle(700);
    await page.click('button[type="submit"]');
    await page.waitForURL(/\/dashboard/, { timeout: 45000 });
    await settle(2500);
  };

  /** Camera page: live WebRTC/HLS tile on the left, live ANPR reads filling on the right. */
  const liveReads = async (cam, secs) => {
    await go(`/cameras/${cam.id}`);
    const painting = () => page.waitForFunction(
      () => [...document.querySelectorAll('video')].some((v) => v.currentTime > 0.5 && v.videoWidth > 2),
      null,
      { timeout: 20000 },
    ).then(() => true).catch(() => false);
    // WebRTC negotiation is unreliable while Playwright's screencast is capturing the tab, even though
    // the same tile plays fine in a normal window. The platform's own HLS fallback is the documented
    // answer for exactly this, so use it rather than recording a black player.
    let mode = 'webrtc';
    if (!(await painting())) {
      mode = 'hls';
      await page.locator('.sg-player').first().getByRole('button', { name: 'Playback mode' }).click().catch(() => {});
      await settle(800);
      await page.locator('.ant-dropdown:visible .ant-dropdown-menu-item', { hasText: /^HLS$/ }).click().catch(() => {});
      if (!(await painting())) degraded('live-reads', `neither WebRTC nor HLS painted a frame on ${cam.name}`);
    }
    run.player_mode = mode;
    await settle(2000);
    // The right-hand panel is a Tabs control; "Live reads" carries a Badge with the count the
    // WebSocket has delivered so far.
    await page.getByRole('tab', { name: /Live reads/ }).click().catch(() => {});
    await settle(2000);
    const state = await page.evaluate(() => {
      const v = [...document.querySelectorAll('video')].map((x) => ({ t: +x.currentTime.toFixed(1), w: x.videoWidth }));
      const badge = document.querySelector('.ant-tabs-tab-active .ant-badge-count, .ant-tabs-tab .ant-badge-count');
      return {
        playing: v.filter((x) => x.t > 0.5 && x.w > 2).length,
        tiles: v.length,
        reads_on_screen: badge ? Number(badge.textContent.replace(/\D/g, '')) || 0 : 0,
      };
    });
    await settle(secs * 1000);
    const after = await page.evaluate(() => {
      const b = document.querySelector('.ant-tabs-tab-active .ant-badge-count, .ant-tabs-tab .ant-badge-count');
      return b ? Number(b.textContent.replace(/\D/g, '')) || 0 : 0;
    });
    return { camera: cam.name, ...state, reads_after_hold: after };
  };

  /** Add `plate` to the watchlist through the real form, then wait for a real read to raise the alert. */
  const watchlistAndAlert = async (plate, reason, waitS) => {
    await go('/watchlist');
    await settle(2000);
    await page.getByRole('button', { name: 'Add to watchlist' }).first().click();
    await settle(1200);
    const modal = page.locator('.ant-modal:visible').last();
    // The first control in this form is the Entity *Select*, not an input - target the plate field by
    // its placeholder. Reason defaults to "stolen" (critical floor), which is exactly what we demo.
    await modal.getByPlaceholder('GJ 27 XY 3456').fill(plate, { timeout: 15000 });
    await settle(1400);
    // Optional field - keep its own timeout short, or a miss costs 30 s of dead air in the recording.
    await modal.locator('textarea').first().fill(`Demonstration entry - ${reason}`, { timeout: 4000 }).catch(() => {});
    await settle(1600);
    await page.getByRole('button', { name: /^(Add entry|Save|OK)$/ }).first().click();
    await modal.waitFor({ state: 'hidden', timeout: 25000 }).catch(() => {});
    await settle(2500);

    await caption('Waiting for the vehicle to be read again', `The matcher runs on every new read. ${plate} is now on the watchlist - the next time any camera reads it, the alert fires by itself.`);
    const before = (await api('GET', '/api/alerts?page_size=20', token)).data?.items ?? [];
    const beforeTop = Math.max(0, ...before.map((a) => a.id));
    const deadline = Date.now() + waitS * 1000;
    let fired = null;
    while (Date.now() < deadline) {
      const now = (await api('GET', '/api/alerts?page_size=20', token)).data?.items ?? [];
      // It must be OUR match, not merely the newest row: the platform also raises camera_offline
      // alerts, and accepting one of those would caption an unrelated event as the watchlist hit.
      const fresh = now.find((a) => a.id > beforeTop && a.type === 'watchlist_hit' && a.plate_norm === plate);
      if (fresh) { fired = fresh; break; }
      await settle(5000);
    }
    if (!fired) {
      degraded('alert', `no watchlist_hit for ${plate} within ${waitS}s - the panel shows earlier alerts, not a fresh one`);
      return { plate, live_alert: false, waited_s: waitS };
    }
    return {
      plate,
      live_alert: true,
      alert_id: fired.id,
      type: fired.type,
      latency_ms: fired.latency_ms,
      camera: fired.camera?.name ?? null,
      priority: fired.priority ?? null,
    };
  };

  const alertsPanel = async (secs) => {
    await go('/alerts');
    await settle(3500);
    const rows = await page.locator('.sg-table tbody tr.ant-table-row').count().catch(() => 0);
    if (rows) {
      await page.locator('.sg-table tbody tr.ant-table-row').first().click().catch(() => {});
      await settle(3500);
    }
    await settle(secs * 1000);
    return { alert_rows: rows };
  };

  const routeOf = async (plate, secs) => {
    await go(`/vehicles?q=${encodeURIComponent(plate)}`);
    await settle(3500);
    await go(`/vehicles/${encodeURIComponent(plate)}/route`);
    await settle(5000);
    // Count the stops from the API, not the DOM: the route page renders a timeline whose markup has
    // changed before, and a miscounted table row previously reported a healthy route as empty.
    const routeJson = (await api('GET', `/api/vehicles/${encodeURIComponent(plate)}/route`, token)).data;
    const sightings = routeJson?.sightings ?? [];
    const apiDetail = { stops: sightings.length, cameras: new Set(sightings.map((s) => s.camera?.name)).size };
    const detail = {
      stops: apiDetail.stops,
      cameras_on_route: apiDetail.cameras,
      markers: await page.locator('.leaflet-marker-icon').count().catch(() => 0),
    };
    if (detail.cameras_on_route < 2) {
      degraded('route', `${plate} was seen on ${detail.cameras_on_route} camera(s) - a single-camera trail, not a cross-camera route`);
    }
    await settle(secs * 1000);
    return detail;
  };

  const outputReport = async (secs) => {
    await go('/reports');
    await settle(3000);
    await page.getByRole('button', { name: 'Generate CSV / PDF' }).first().click();
    // The generated file lands in the report history table below, with its SHA-256 - there is no modal.
    await settle(9000);
    const rows = await page.locator('.sg-table tbody tr.ant-table-row').count().catch(() => 0);
    const hash = await page.locator('.sg-table tbody tr.ant-table-row').first().locator('code, .sg-hash').last().textContent().catch(() => null);
    if (!rows) degraded('report', 'the report history table is empty - no file was generated');
    await page.mouse.wheel(0, 500).catch(() => {});
    await settle(secs * 1000);
    return { history_rows: rows, report_hash: hash?.trim().slice(0, 24) ?? null };
  };

  /* ------------------------------------------------------------------------ storyboards ------ */

  await login();

  if (MODE === 'video1') {
    const cam = own ?? cams[0];
    await step('registry', 'Model 1 - the camera registry',
      'Every camera carries department, ownership, type, codec, connectivity and location. Our own feed - a recorded forecourt clip published over RTSP from our own relay - is onboarded here alongside the government cameras.', 7,
      async () => {
        await go('/cameras');
        await settle(2500);
        const rows = await page.locator('.sg-table tbody tr.ant-table-row').count();
        await page.locator('.sg-table tbody tr.ant-table-row').first().click().catch(() => {});
        await settle(3500);
        return { rows };
      });

    await step('live-reads', 'Live view and live ANPR on our own camera',
      'The tile plays over WebRTC with HLS fallback. On the right, plates are read live - each with the OCR confidence, the IST timestamp and the plate crop.', 4,
      () => liveReads(cam, 11));

    if (picked.plate) {
      await step('watchlist', `Putting ${picked.plate} on the watchlist as a stolen vehicle`,
        'Reason "stolen" carries a critical priority floor, so the alert is critical whatever the operator types. Nothing else is configured.', 2,
        () => watchlistAndAlert(picked.plate, 'Stolen', alertWait).then((r) => { run.alert = r; return r; }));

      await step('alert', 'The alert fired from a real read',
        'The crop, the camera, its location and the read-to-alert latency in milliseconds. Repeat reads inside a minute attach to the same alert instead of flooding the panel.', 8,
        () => alertsPanel(7));
    }

    // Guide §7.3 scopes Video 1 to onboarding -> viewing -> AI detection -> watchlist -> alert, with a
    // hard 3:00 limit; the route and the output report are Video 2's required content and are cut here
    // rather than have this video run long.
    await step('close', 'Sentinel Gujarat 1.0.0-phase1',
      'Model 1 + Model 2, with a hybrid roadmap to Models 3 and 4. Video two runs the same platform on the government feed, with the cross-camera route and the output report. Everything here is the running platform - no mock-ups.', 5,
      async () => { await go('/dashboard'); await settle(3000); return {}; });
  } else {
    await step('import', 'Model 1 - onboarding the government cameras',
      'One click against the organiser catalogue. The importer maps every field, creates a relay path per camera - a transcoded path for the H.265 ones - and prints the measured onboarding time.', 14,
      async () => { await go('/cameras/import'); await settle(5000); return {}; });

    await step('registry', `The registry after onboarding - ${sandbox.length} government cameras`,
      'Department, camera type, codec, ownership, connectivity, maintenance status and coordinates for each. This export is the sample camera-metadata dataset in our submission.', 14,
      async () => { await go('/cameras'); await settle(3500); await page.locator('.sg-table tbody tr.ant-table-row').first().click().catch(() => {}); await settle(3500); return { rows: await page.locator('.sg-table tbody tr.ant-table-row').count() }; });

    await step('map', 'GIS mapping with department, status and coverage layers',
      'District boundaries, department layers and camera health. Solid, dashed and hollow markers show how confident we are of each location - the organiser catalogue ships no coordinates, so this is stated, not hidden.', 18,
      async () => { await go('/map'); await settle(9000); return { markers: await page.locator('.leaflet-marker-icon').count() }; });

    await step('wall', 'Unified viewing - the video wall',
      'Nine live tiles: government cameras and our own private camera side by side - two different systems in one viewer, with the departmental systems untouched.', 26,
      async () => {
        await go('/wall');
        await settle(4000);
        await page.locator('.ant-segmented-item-label', { hasText: /^9$/ }).click().catch(() => {});
        await settle(2500);
        await page.getByRole('button', { name: 'Auto-fill' }).click().catch(() => {});
        // Hold until most tiles have a first frame (cold organiser pulls are 4-40 s), then keep rolling.
        await page.waitForFunction(
          () => [...document.querySelectorAll('video')].filter((v) => v.currentTime > 0.5 && v.videoWidth > 2).length >= 5,
          null,
          { timeout: 50000 },
        ).catch(() => {});
        await settle(6000);
        const s = await page.evaluate(() => {
          const v = [...document.querySelectorAll('video')].map((x) => ({ t: +x.currentTime.toFixed(1), w: x.videoWidth }));
          return { playing: v.filter((x) => x.t > 0.5 && x.w > 2).length, tiles: v.length, two_systems: document.body.innerText.includes('2 systems') };
        });
        if (!s.playing) degraded('wall', 'no tile is painting frames');
        return s;
      });

    await step('health', 'Camera health and maintenance monitoring',
      'The poller judges every camera from the relay state or a direct probe: online, degraded, offline or never-streamed. Ageing and AMC expiry feed the gap-analysis report.', 12,
      async () => { await go('/health'); await settle(5000); return {}; });

    if (anprCams.length) {
      await step('live-reads', 'ANPR on the government feed',
        'Native-resolution decode, tiled YOLO plate detection, best-shot tracking and an OCR ensemble. Each read carries the crop, the confidence and the IST timestamp.', 26,
        () => liveReads(anprCams[0], 18));
    }

    await step('detections', 'Searchable metadata - every read indexed by camera and time',
      'This is the vehicle-movement metadata the challenge asks for: plate, camera, department, coordinates and timestamp, filterable and exportable.', 14,
      async () => { await go('/detections'); await settle(5000); return { rows: await page.locator('.sg-table tbody tr.ant-table-row').count() }; });

    if (picked.plate) {
      await step('watchlist', `Watchlist correlation - adding ${picked.plate}`,
        'The watchlist holds stolen vehicles, wanted and missing persons. Matching is exact on the normalised plate, or edit-distance one when the OCR confidence is high.', 4,
        () => watchlistAndAlert(picked.plate, 'Stolen', alertWait).then((r) => { run.alert = r; return r; }));

      await step('alert', 'Automatic real-time alert',
        'The alert is raised by the matcher the moment a camera reads the plate - with the crop, the camera, its location on the map and the read-to-alert latency.', 18,
        () => alertsPanel(12));

      await step('route', `Vehicle movement history for ${picked.plate}`,
        'Search by plate returns every sighting with its crop; the route orders them in time across cameras with a timestamped, location-wise timeline. This is the Phase 2 test case.', 20,
        () => routeOf(picked.plate, 13));
    }

    await step('report', 'The output report on the government feed',
      'Every detected vehicle and plate with IST timestamp, camera ID, department, coordinates, confidence and crop hash - as CSV and PDF, with the analytics-quality section and the measured accuracy from labelled crops.', 16,
      () => outputReport(10));

    await step('gap', 'Gap analysis and the audit trail',
      'Uncovered zones, ageing infrastructure and recommendations complete Model 1, and every mutating action - import, export, search, route, acknowledgement - is written to an append-only audit log.', 14,
      async () => { await go('/gap-analysis'); await settle(7000); await go('/audit'); await settle(5000); return {}; });

    await step('close', 'Sentinel Gujarat 1.0.0-phase1',
      'Model 1 + Model 2, hybrid roadmap to Models 3 and 4. Open-source throughout. Everything in this video is the running platform on the organiser feed - no mock-ups. Thank you.', 8,
      async () => { await go('/dashboard'); await settle(4000); return {}; });
  }

  const totalS = Math.round((Date.now() - t0) / 1000);
  run.duration_s = totalS;
  run.finished_at = new Date().toISOString();
  console.log(`\nrecorded ${Math.floor(totalS / 60)}m ${totalS % 60}s`);

  const video = page.video();
  await context.close();
  await browser.close();

  const stem = N === 1
    ? 'Sentinel-Gujarat_Video1_Own-Feed_v1.0-phase1'
    : 'Sentinel-Gujarat_Video2_Government-Feed_v1.0-phase1';
  const webm = join(OUT, `${stem}.webm`);
  const raw = await video?.path();
  if (raw && existsSync(raw)) { renameSync(raw, webm); run.webm = webm; }
  else degraded('capture', 'playwright produced no video file');

  // Playwright writes VP8 webm. YouTube accepts it as-is; the Drive copy is nicer as H.264 mp4.
  if (run.webm) {
    const mp4 = join(OUT, `${stem}.mp4`);
    for (const attempt of [
      ['ffmpeg', ['-y', '-i', webm, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4]],
      ['docker', ['run', '--rm', '-v', `${OUT}:/work`, '--entrypoint', 'ffmpeg', 'sentinel-anpr:cpu', '-y', '-i', `/work/${stem}.webm`, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', `/work/${stem}.mp4`]],
    ]) {
      try {
        execFileSync(attempt[0], attempt[1], { stdio: 'pipe', timeout: 900000 });
        run.mp4 = mp4;
        console.log(`mp4: ${mp4}`);
        break;
      } catch { /* try the next transcoder */ }
    }
    if (!run.mp4) degraded('transcode', 'no ffmpeg reachable - upload the .webm (YouTube accepts it) or remux by hand');
  }

  writeFileSync(join(OUT, `run-video${N}.json`), JSON.stringify(run, null, 2));
  console.log(`\nwebm: ${run.webm ?? '-'}\nlog:  ${join(OUT, `run-video${N}.json`)}`);
  if (run.degraded.length) {
    console.log(`\n${run.degraded.length} step(s) DEGRADED - fix these before uploading:`);
    for (const d of run.degraded) console.log(`  - ${d}`);
    process.exitCode = 1;
  } else {
    console.log('\nall steps showed real data.');
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
