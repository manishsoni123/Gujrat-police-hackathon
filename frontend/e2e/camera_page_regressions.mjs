/**
 * Camera page regressions (review findings 15-18, 2026-10-13), checked in a real browser against the
 * in-memory mock:
 *
 *   npm run dev:mock                        (in another terminal, or set SG_BASE)
 *   node e2e/camera_page_regressions.mjs    → one PASS/FAIL line per check, exit code 1 on any failure
 *
 * Env: SG_BASE (default http://localhost:5173), SG_CHANNEL (default msedge; empty = bundled Chromium),
 *      SG_USER / SG_PASS (default jury_admin / Sentinel@Admin2026), SG_ONLY = comma list of check keys.
 *
 * The mock has no media relay, so an init script gives the page either a fake WebRTC peer (a canvas
 * track) or a fake native-HLS <video>, records the mock `/ws/*` sockets (to inject `/ws/reads/{id}`
 * envelopes) and can fail `GET /api/cameras/{id}` on demand. Checks:
 *   f15-whep    detection canvas is mounted while the player plays over WebRTC (control)
 *   f15-hls     no detection canvas over HLS playback; the toggle tooltip says boxes need WebRTC
 *   f18-cadence at ~0.7 fps overlay frames the boxes stay fully opaque between updates and still
 *               disappear once frames stop
 *   f18-remount the cadence seen while the canvas was unmounted (boxes toggled off; also the state
 *               after a WebRTC reconnect) applies to the first frame drawn after it mounts again
 *   f16-hook    useReadsSocket starts empty when its camera id changes
 *   f16-filter  /ws/reads envelopes that belong to another camera are ignored
 *   f16-nav     in-app navigation camera 1 → camera 9 shows none of camera 1's live reads / snapshot
 *   f17-404     a 404 on the 30 s camera re-read ends the page (ErrorState, player gone and its WebRTC
 *               session DELETEd, poll stopped); Retry once access is back restores it
 *   f17-500     a 500 keeps the last loaded camera and the playing session, and the poll continues
 *   f17-latch   once a 404 has ended the page, later transient failures of the re-read (a Caddy 502 on
 *               Retry, a network error on the refetch a /ws/health event triggers) keep it ended: no
 *               player, no /ws/reads socket, no 30 s poll; only a successful re-read restores it
 */
import { chromium } from 'playwright';

const BASE = process.env.SG_BASE ?? 'http://localhost:5173';
const CHANNEL = process.env.SG_CHANNEL ?? 'msedge';
const USER = process.env.SG_USER ?? 'jury_admin';
const PASS = process.env.SG_PASS ?? 'Sentinel@Admin2026';
const ONLY = process.env.SG_ONLY ? new Set(process.env.SG_ONLY.split(',')) : null;
const ANPR_CAM = 1; // mock: ANPR, online
const PLAIN_CAM = 9; // mock: no ANPR, not streaming, no reads
const PLAIN_CAM_NAME = 'Kalupur Railway Station East';
const POLL_MS = 30_000;

const settle = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const record = (key, ok, detail) => {
  results.push({ key, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${key}  ${detail}`);
};

/** Runs in the page before the app: fetch/WebSocket recording plus the fake media path. */
function initHarness(opts) {
  window.__sg = { sockets: [], calls: [], whepDeletes: [], fail: null };
  /** Highest alpha of any detection-canvas pixel (0 = nothing drawn, -1 = no canvas). */
  window.__sg.alpha = () => {
    const c = document.querySelector('canvas.sg-player-detections');
    if (!c || !c.width) return -1;
    const px = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let m = 0;
    for (let i = 3; i < px.length; i += 4) if (px[i] > m) m = px[i];
    return m;
  };
  const nativeFetch = window.fetch.bind(window);
  let inner = nativeFetch;
  const wrapped = async (input, init) => {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(raw, window.location.href);
    const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const p = url.pathname;
    if (method === 'GET' && /^\/api\/cameras\/\d+$/.test(p)) {
      window.__sg.calls.push({ path: p, at: Date.now() });
      const f = window.__sg.fail;
      if (f && f.path === p) {
        if (f.network) throw new TypeError('Failed to fetch'); // API down: the client reports status 0
        if (f.emptyBody) return new Response('', { status: f.status }); // proxy error page (Caddy 502)
        return new Response(JSON.stringify({ detail: f.detail, code: f.code }), { status: f.status, headers: { 'Content-Type': 'application/json' } });
      }
    }
    if (opts.whep && p.startsWith('/mtx/')) {
      if (method === 'POST' && p.endsWith('/whep')) return new Response('v=0\r\n', { status: 201, headers: { 'Content-Type': 'application/sdp', Location: `${p}/session-1` } });
      if (method === 'DELETE') {
        window.__sg.whepDeletes.push({ path: p, at: Date.now() });
        return new Response(null, { status: 200 });
      }
    }
    // The mock's own pass-through calls the fetch it captured (this wrapper): hand non-mock paths to the browser.
    if (!/^\/(api|mtx|media|playback|healthz)(\/|$)/.test(p)) return nativeFetch(input, init);
    return inner(input, init);
  };
  Object.defineProperty(window, 'fetch', { configurable: true, get: () => wrapped, set: (v) => { inner = v; } });

  let BaseSocket = window.WebSocket;
  function RecordingSocket(url, protocols) {
    const s = new BaseSocket(url, protocols);
    window.__sg.sockets.push(s);
    return s;
  }
  Object.assign(RecordingSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  Object.defineProperty(window, 'WebSocket', { configurable: true, get: () => RecordingSocket, set: (v) => { BaseSocket = v; } });

  if (opts.whep) {
    class FakePeer {
      constructor() {
        this.iceConnectionState = 'new';
        this.iceGatheringState = 'complete';
        this.localDescription = null;
        this.oniceconnectionstatechange = null;
        this.onicegatheringstatechange = null;
        this.ontrack = null;
        this.closed = false;
      }
      addTransceiver() {
        return {};
      }
      async createOffer() {
        return { type: 'offer', sdp: 'v=0\r\n' };
      }
      async setLocalDescription(d) {
        this.localDescription = d;
      }
      async setRemoteDescription() {
        setTimeout(() => {
          if (this.closed) return;
          const c = document.createElement('canvas');
          c.width = 1280;
          c.height = 720;
          const g = c.getContext('2d');
          const paint = () => {
            g.fillStyle = '#263445';
            g.fillRect(0, 0, 1280, 720);
          };
          paint();
          this.painter = setInterval(paint, 200);
          this.ontrack?.({ track: c.captureStream(5).getVideoTracks()[0] });
          this.iceConnectionState = 'connected';
          this.oniceconnectionstatechange?.();
        }, 50);
      }
      async getStats() {
        return new Map();
      }
      close() {
        this.closed = true;
        clearInterval(this.painter);
      }
    }
    window.RTCPeerConnection = FakePeer;
  }
  if (opts.hls) {
    // No WebRTC and no MSE: the player takes its native-HLS path, which a stubbed <video> accepts.
    delete window.RTCPeerConnection;
    delete window.MediaSource;
    delete window.ManagedMediaSource;
    delete window.WebKitMediaSource;
    const canPlay = HTMLMediaElement.prototype.canPlayType;
    HTMLMediaElement.prototype.canPlayType = function (t) {
      return String(t).includes('mpegurl') ? 'maybe' : canPlay.call(this, t);
    };
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  }
}

async function openPage(browser, opts = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, locale: 'en-IN', timezoneId: 'Asia/Kolkata' });
  await context.addInitScript(initHarness, opts);
  const page = await context.newPage();
  page.on('pageerror', (e) => console.error('[pageerror]', e.message));
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
  await page.evaluate(() => {
    try {
      localStorage.setItem('sg.desktop_banner_dismissed', '1');
    } catch {
      /* ignore */
    }
  });
  await page.waitForSelector('input[autocomplete="username"]', { timeout: 60_000 });
  await page.fill('input[autocomplete="username"]', USER);
  await page.fill('input[autocomplete="current-password"]', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/dashboard/, { timeout: 60_000 });
  return { context, page };
}

/** Wait for the camera page of `id` (header) and its open `/ws/reads/{id}` socket. */
async function openCamera(page, id, name) {
  await page.goto(`${BASE}/cameras/${id}`, { waitUntil: 'domcontentloaded' });
  if (name) await page.getByRole('heading', { name }).first().waitFor({ timeout: 30_000 });
  await page.waitForFunction((cid) => window.__sg.sockets.some((s) => s.url.endsWith(`/ws/reads/${cid}`) && s.readyState === 1), id, { timeout: 30_000 });
}

/** Emit an envelope on the newest open `/ws/reads/{socketCam}` mock socket. */
const emit = (page, socketCam, type, data) =>
  page.evaluate(
    ([cid, t, d]) => {
      const s = window.__sg.sockets.filter((x) => x.url.endsWith(`/ws/reads/${cid}`) && x.readyState === 1).at(-1);
      if (!s) throw new Error(`no open /ws/reads/${cid} socket`);
      s.emit(t, d);
    },
    [socketCam, type, data],
  );

const pageText = (page) => page.evaluate(() => document.body.innerText.replace(/\s+/g, ''));
const caption = (page) => page.evaluate(() => document.querySelector('.sg-player-caption')?.textContent ?? '');
const canvasCount = (page) => page.evaluate(() => document.querySelectorAll('canvas.sg-player-detections').length);

/** A read of `cameraId` shaped like the mock's seed reads (camera 1 has some), relabelled with `plate`. */
async function fakeRead(page, cameraId, plate, id) {
  return page.evaluate(
    async ([template, cam, pl, rid]) => {
      const seed = await fetch(`/api/detections?camera_id=${template}&page_size=1`).then((r) => r.json());
      const camera = await fetch(`/api/cameras/${cam}`).then((r) => r.json());
      const d = seed.items[0];
      return { ...d, id: rid, plate_norm: pl, plate_raw: pl, plate_display: pl, captured_at: new Date().toISOString(), camera: { ...d.camera, id: camera.id, name: camera.name } };
    },
    [ANPR_CAM, cameraId, plate, id],
  );
}

const OVERLAY_FRAME = (cid) => ({
  camera_id: cid,
  captured_at: new Date().toISOString(),
  frame_w: 1280,
  frame_h: 720,
  plates: [{ box: [0.3, 0.4, 0.25, 0.12], conf: 0.92, label: 'GJ01AB1234', track: 7 }],
  objects: [{ box: [0.2, 0.3, 0.45, 0.5], conf: 0.88, cls: 'car' }],
  objects_at: new Date().toISOString(),
});

async function checkWhepAndCadence(browser) {
  const { context, page } = await openPage(browser, { whep: true });
  try {
    await openCamera(page, ANPR_CAM, 'Sachivalaya Gate 1');
    await page.waitForSelector('.sg-live-badge', { timeout: 30_000 });
    await page.waitForFunction(() => (document.querySelector('.sg-player-caption')?.textContent ?? '').includes('WebRTC'), null, { timeout: 30_000 });
    const n = await canvasCount(page);
    if (!ONLY || ONLY.has('f15-whep')) record('f15-whep', n === 1, `playing via WebRTC, detection canvases=${n} (want 1)`);

    if (!ONLY || ONLY.has('f18-cadence')) {
      // Overlay frames every 1430 ms (≈ 0.7 fps, the documented 8-camera CPU worker rate), sampled in-page.
      const frame = OVERLAY_FRAME(ANPR_CAM);
      const sample = await page.evaluate(async ([cid, f]) => {
        const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
        const sock = window.__sg.sockets.filter((x) => x.url.endsWith(`/ws/reads/${cid}`) && x.readyState === 1).at(-1);
        const alpha = window.__sg.alpha;
        for (let k = 0; k < 4; k += 1) {
          sock.emit('overlay', f);
          await sleep(1430);
        }
        sock.emit('overlay', f);
        await sleep(1300);
        const at1300 = alpha(); // just before the next update would land
        await sleep(400);
        const at1700 = alpha(); // one slow frame (0.59 fps) late
        await sleep(3300);
        const at5000 = alpha(); // frames stopped: gone
        return { at1300, at1700, at5000 };
      }, [ANPR_CAM, frame]);
      const ok = sample.at1300 >= 250 && sample.at1700 >= 250 && sample.at5000 === 0;
      record('f18-cadence', ok, `max canvas alpha at 1.3 s=${sample.at1300}, 1.7 s=${sample.at1700} (want ≥250), 5 s after the last frame=${sample.at5000} (want 0)`);
    }
  } finally {
    await context.close();
  }
}

async function checkCadenceAcrossRemount(browser) {
  const { context, page } = await openPage(browser, { whep: true });
  try {
    await openCamera(page, ANPR_CAM, 'Sachivalaya Gate 1');
    await page.waitForFunction(() => (document.querySelector('.sg-player-caption')?.textContent ?? '').includes('WebRTC'), null, { timeout: 30_000 });
    await page.waitForSelector('canvas.sg-player-detections', { timeout: 10_000 });
    const sample = await page.evaluate(async ([cid, f]) => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const sock = window.__sg.sockets.filter((x) => x.url.endsWith(`/ws/reads/${cid}`) && x.readyState === 1).at(-1);
      const toggle = () => document.querySelector('button[aria-label="Detection boxes"]').click();
      toggle(); // boxes off: the canvas unmounts while frames keep arriving at ≈ 0.7 fps
      await sleep(300);
      const unmounted = !document.querySelector('canvas.sg-player-detections');
      for (let k = 0; k < 4; k += 1) {
        sock.emit('overlay', f);
        await sleep(k < 3 ? 1430 : 300);
      }
      toggle(); // boxes on again, between two frames
      await sleep(1130);
      const remounted = Boolean(document.querySelector('canvas.sg-player-detections'));
      sock.emit('overlay', f); // first frame the new canvas draws
      await sleep(1300);
      const at1300 = window.__sg.alpha();
      await sleep(400);
      const at1700 = window.__sg.alpha();
      return { unmounted, remounted, at1300, at1700 };
    }, [ANPR_CAM, OVERLAY_FRAME(ANPR_CAM)]);
    const ok = sample.unmounted && sample.remounted && sample.at1300 >= 250 && sample.at1700 >= 250;
    record('f18-remount', ok, `canvas unmounted while off=${sample.unmounted}, mounted again=${sample.remounted}; first frame after the remount: max canvas alpha at 1.3 s=${sample.at1300}, 1.7 s=${sample.at1700} (want ≥250)`);
  } finally {
    await context.close();
  }
}

async function checkHls(browser) {
  const { context, page } = await openPage(browser, { hls: true });
  try {
    await openCamera(page, ANPR_CAM, 'Sachivalaya Gate 1');
    await page.waitForSelector('.sg-live-badge', { timeout: 30_000 });
    await page.waitForFunction(() => (document.querySelector('.sg-player-caption')?.textContent ?? '').includes('HLS'), null, { timeout: 30_000 });
    await emit(page, ANPR_CAM, 'overlay', OVERLAY_FRAME(ANPR_CAM));
    await settle(300);
    const n = await canvasCount(page);
    await page.hover('button[aria-label="Detection boxes"]');
    const tip = await page
      .locator('.ant-tooltip:not(.ant-tooltip-hidden)')
      .last()
      .textContent({ timeout: 5000 })
      .catch(() => '');
    record('f15-hls', n === 0 && /WebRTC/.test(tip ?? ''), `playing via HLS (${(await caption(page)).trim()}), detection canvases=${n} (want 0), toggle tooltip="${tip}" (want a WebRTC note)`);
  } finally {
    await context.close();
  }
}

async function checkHookReset(browser) {
  const { context, page } = await openPage(browser);
  try {
    const res = await page.evaluate(async ([a, b]) => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const main = await fetch('/src/main.tsx').then((r) => r.text());
      const dep = (name) => {
        const m = new RegExp(`from\\s+"([^"]*/deps/${name}\\.js[^"]*)"`).exec(main);
        if (!m) throw new Error(`no ${name} import in /src/main.tsx`);
        return m[1];
      };
      const reactMod = await import(dep('react'));
      const React = reactMod.default ?? reactMod;
      const rdc = await import(dep('react-dom_client'));
      const createRoot = rdc.createRoot ?? rdc.default.createRoot;
      const { useReadsSocket } = await import('/src/ws/useReadsSocket.ts');
      const log = [];
      function Probe({ id }) {
        const s = useReadsSocket(id);
        log.push({ id, reads: s.reads.map((r) => r.camera.id), snapshot: s.snapshot?.camera_id ?? null });
        return null;
      }
      const root = createRoot(document.createElement('div'));
      root.render(React.createElement(Probe, { id: a }));
      for (let i = 0; i < 100; i += 1) {
        const last = log.at(-1);
        if (last && last.reads.length && last.snapshot === a) break;
        await sleep(100);
      }
      const before = log.at(-1);
      const mark = log.length;
      root.render(React.createElement(Probe, { id: b }));
      await sleep(400); // camera b's own snapshot needs ≥ 2 s; it has no reads
      const after = log.slice(mark).filter((e) => e.id === b);
      root.unmount();
      return { before, after };
    }, [ANPR_CAM, PLAIN_CAM]);
    const leaked = res.after.filter((e) => e.reads.length || e.snapshot !== null);
    const ok = Boolean(res.before?.reads.length) && res.before.snapshot === ANPR_CAM && res.after.length > 0 && leaked.length === 0;
    record('f16-hook', ok, `camera ${ANPR_CAM}: ${res.before?.reads.length ?? 0} reads, snapshot of ${res.before?.snapshot}; after switching to ${PLAIN_CAM}: ${res.after.length} renders, ${leaked.length} carrying camera ${ANPR_CAM} data (want 0) ${leaked.length ? JSON.stringify(leaked[0]) : ''}`);
  } finally {
    await context.close();
  }
}

async function checkFilter(browser) {
  const { context, page } = await openPage(browser);
  try {
    await openCamera(page, PLAIN_CAM, PLAIN_CAM_NAME);
    // Wait for the player to reach snapshot mode (no relay in the mock), where a pushed snapshot would show.
    await page.waitForFunction(() => (document.querySelector('.sg-player-caption')?.textContent ?? '').includes('Snapshot'), null, { timeout: 60_000 });
    const foreign = await fakeRead(page, ANPR_CAM, 'GJ99ZZ0002', 990_002);
    const own = await fakeRead(page, PLAIN_CAM, 'GJ99ZZ0003', 990_003);
    // The mock pushes its own camera-9 snapshot every 2 s; mute it so it cannot overwrite a leaked one.
    await page.evaluate((cid) => {
      const s = window.__sg.sockets.filter((x) => x.url.endsWith(`/ws/reads/${cid}`) && x.readyState === 1).at(-1);
      const orig = s.emit.bind(s);
      s.emit = (t, d) => (t === 'snapshot' && !String(d?.url ?? '').includes('#') ? undefined : orig(t, d));
    }, PLAIN_CAM);
    await emit(page, PLAIN_CAM, 'read', foreign);
    await emit(page, PLAIN_CAM, 'snapshot', { camera_id: ANPR_CAM, url: 'data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22/%3E#foreign-snapshot', updated_at: new Date().toISOString() });
    await emit(page, PLAIN_CAM, 'read', own);
    await settle(500);
    const text = await pageText(page);
    const foreignSnap = await page.evaluate(() => document.querySelectorAll('img[src*="foreign-snapshot"]').length);
    const ok = !text.includes('GJ99ZZ0002') && text.includes('GJ99ZZ0003') && foreignSnap === 0;
    record('f16-filter', ok, `on camera ${PLAIN_CAM}: camera ${ANPR_CAM}'s read shown=${text.includes('GJ99ZZ0002')} (want false), own read shown=${text.includes('GJ99ZZ0003')} (want true), camera ${ANPR_CAM}'s snapshot shown=${foreignSnap > 0} (want false)`);
  } finally {
    await context.close();
  }
}

async function checkNavigation(browser) {
  const { context, page } = await openPage(browser);
  try {
    await openCamera(page, ANPR_CAM, 'Sachivalaya Gate 1');
    const marker = await fakeRead(page, ANPR_CAM, 'GJ99ZZ0001', 990_001);
    await emit(page, ANPR_CAM, 'read', marker);
    await emit(page, ANPR_CAM, 'snapshot', { camera_id: ANPR_CAM, url: 'data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22/%3E#cam1-snapshot', updated_at: new Date().toISOString() });
    await settle(300);
    const shownOnA = (await pageText(page)).includes('GJ99ZZ0001');
    // In-app navigation (what Ctrl+K search and back/forward do): the route element is reused. Done as a
    // history traversal the app's own router handles; `import('/src/router.tsx')` would hand back a second
    // router instance once Vite has hot-updated any page module (the app then imports it as `?t=…`).
    await page.evaluate((to) => {
      const idx = (window.history.state?.idx ?? 0) + 1;
      window.history.pushState({ usr: null, key: 'e2e-nav', idx }, '', to);
      window.dispatchEvent(new PopStateEvent('popstate', { state: window.history.state }));
    }, `/cameras/${PLAIN_CAM}`);
    await page.getByRole('heading', { name: PLAIN_CAM_NAME }).first().waitFor({ timeout: 30_000 });
    await settle(600); // well inside camera 9's first snapshot push (≥ 2 s after its socket opens)
    const text = await pageText(page);
    const staleSnap = await page.evaluate(() => document.querySelectorAll('img[src*="cam1-snapshot"]').length);
    const ok = shownOnA && !text.includes('GJ99ZZ0001') && staleSnap === 0 && text.includes('Noplatereadsyet');
    record('f16-nav', ok, `read shown on camera ${ANPR_CAM}=${shownOnA}; after navigating to camera ${PLAIN_CAM}: camera ${ANPR_CAM}'s read shown=${text.includes('GJ99ZZ0001')} (want false), its snapshot shown=${staleSnap > 0} (want false), empty reads list=${text.includes('Noplatereadsyet')}`);
  } finally {
    await context.close();
  }
}

/** Fail the camera re-read with `status` after the page loaded and watch the next 30 s polls. */
async function checkRefetchFailure(browser, key, status) {
  const { context, page } = await openPage(browser, { whep: true });
  try {
    await openCamera(page, ANPR_CAM, 'Sachivalaya Gate 1');
    await page.waitForSelector('.sg-player', { timeout: 30_000 });
    await page.waitForSelector('.sg-live-badge', { timeout: 30_000 }); // a WebRTC session is open
    const path = `/api/cameras/${ANPR_CAM}`;
    await page.evaluate(([p, st]) => {
      window.__sg.fail = { path: p, status: st, code: st === 404 ? 'not_found' : 'internal_error', detail: st === 404 ? 'Camera not found' : 'Internal server error' };
      window.__sg.failFrom = Date.now();
    }, [path, status]);
    // First failing poll (≤ 30 s), then the page's verdict: ErrorState or the refresh warning, which a 5xx
    // reaches only after the client's two retries (1 s + 2 s).
    await page.waitForFunction((p) => window.__sg.calls.some((c) => c.path === p && c.at >= window.__sg.failFrom), path, { timeout: POLL_MS + 10_000 });
    await page
      .waitForFunction(() => Boolean(document.querySelector('.ant-alert-error')) || document.body.innerText.includes('Could not refresh this camera'), null, { timeout: 15_000 })
      .catch(() => undefined);
    await settle(1000);
    const state = async () =>
      page.evaluate(() => ({
        errorState: Boolean(document.querySelector('.ant-alert-error')),
        refreshBanner: document.body.innerText.includes('Could not refresh this camera'),
        player: document.querySelectorAll('.sg-player').length,
        header: Boolean([...document.querySelectorAll('h1, h2, h3, h4')].some((h) => h.textContent?.includes('Sachivalaya Gate 1'))),
      }));
    const first = await state();
    const firstFailCalls = await page.evaluate((p) => window.__sg.calls.filter((c) => c.path === p && c.at >= window.__sg.failFrom).length, path);
    const whepClosed = await page.evaluate(() => window.__sg.whepDeletes.some((d) => d.at >= window.__sg.failFrom));
    await settle(POLL_MS + 5000);
    const second = await state();
    const laterCalls = (await page.evaluate((p) => window.__sg.calls.filter((c) => c.path === p && c.at >= window.__sg.failFrom).length, path)) - firstFailCalls;
    if (status === 404) {
      // Access comes back: Retry restores the page and its player.
      await page.evaluate(() => {
        window.__sg.fail = null;
      });
      let restored = false;
      if (first.errorState) {
        await page.locator('.ant-alert-error').getByRole('button', { name: 'Retry' }).click();
        restored = await page
          .waitForSelector('.sg-live-badge', { timeout: 30_000 })
          .then(() => true)
          .catch(() => false);
      }
      const ok = first.errorState && first.player === 0 && !first.refreshBanner && whepClosed && second.errorState && second.player === 0 && laterCalls === 0 && restored;
      record(key, ok, `after the 404: ErrorState=${first.errorState} (want true), players=${first.player} (want 0), WHEP session DELETEd=${whepClosed} (want true), refresh banner=${first.refreshBanner} (want false); camera re-reads in the following ${(POLL_MS + 5000) / 1000} s=${laterCalls} (want 0); Retry after access returns restores the live player=${restored} (want true)`);
    } else {
      const ok = !first.errorState && first.refreshBanner && first.player === 1 && first.header && !whepClosed && second.player === 1 && laterCalls >= 1;
      record(key, ok, `after the ${status}: refresh banner=${first.refreshBanner} (want true), ErrorState=${first.errorState} (want false), players=${first.player} (want 1), WHEP session DELETEd=${whepClosed} (want false), last camera kept=${first.header}; re-reads in the following ${(POLL_MS + 5000) / 1000} s=${laterCalls} (want ≥ 1)`);
    }
  } finally {
    await context.close();
  }
}

/**
 * A 404 ends the page; transient failures after it must not bring it back. Each refetch is triggered the
 * way it happens in production: a `/ws/health` event (useHealthSocket invalidates ['cameras'] on every
 * camera's health change) or the ErrorState's Retry button.
 */
async function checkAccessLatch(browser) {
  const { context, page } = await openPage(browser, { whep: true });
  try {
    await openCamera(page, ANPR_CAM, 'Sachivalaya Gate 1');
    await page.waitForSelector('.sg-live-badge', { timeout: 30_000 }); // a WebRTC session is open
    const path = `/api/cameras/${ANPR_CAM}`;
    const failWith = (f) =>
      page.evaluate(([p, x]) => {
        window.__sg.fail = x ? { path: p, ...x } : null;
      }, [path, f]);
    const now = () => page.evaluate(() => Date.now());
    const callsSince = (t) => page.evaluate(([p, since]) => window.__sg.calls.filter((c) => c.path === p && c.at >= since).length, [path, t]);
    const waitCall = (t) => page.waitForFunction(([p, since]) => window.__sg.calls.some((c) => c.path === p && c.at >= since), [path, t], { timeout: 15_000 });
    // A health change of another camera: the page's own `offline` flag stays as it is.
    const healthEvent = () =>
      page.evaluate(() => {
        const s = window.__sg.sockets.filter((x) => x.url.endsWith('/ws/health') && x.readyState === 1).at(-1);
        if (!s) throw new Error('no open /ws/health socket');
        const at = new Date().toISOString();
        s.emit('health', { camera_id: 2, name: 'other', status: 'online', previous_status: 'degraded', last_seen_at: at, checked_at: at, is_ready: true, has_video: true, bytes_delta: 1000, readers: 1, source_flag: 'ok', maintenance_status: 'ok' });
      });
    const state = () =>
      page.evaluate((cid) => ({
        errorState: Boolean(document.querySelector('.ant-alert-error')),
        refreshBanner: document.body.innerText.includes('Could not refresh this camera'),
        players: document.querySelectorAll('.sg-player').length,
        readsSockets: window.__sg.sockets.filter((s) => s.url.endsWith(`/ws/reads/${cid}`) && s.readyState === 1).length,
      }), ANPR_CAM);
    const ended = (s) => s.errorState && !s.refreshBanner && s.players === 0 && s.readsSockets === 0;
    const show = (s) => `ErrorState=${s.errorState} banner=${s.refreshBanner} players=${s.players} /ws/reads=${s.readsSockets}`;
    // Wait out the client's two retries of a 5xx / network error (1 s + 2 s) before judging the page.
    const RETRIES_MS = 4500;

    // 1. Access lost: a health-triggered re-read answers 404.
    await failWith({ status: 404, code: 'not_found', detail: 'Camera not found' });
    let t = await now();
    await healthEvent();
    await waitCall(t);
    await page.waitForSelector('.ant-alert-error', { timeout: 15_000 }).catch(() => undefined);
    await settle(1000);
    const afterLoss = await state();

    // 2. The API restarts: Retry hits Caddy's 502.
    await failWith({ status: 502, emptyBody: true });
    t = await now();
    await page.getByRole('button', { name: 'Retry' }).first().click({ timeout: 5000 }).catch(() => undefined);
    await waitCall(t).catch(() => undefined);
    await settle(RETRIES_MS);
    const after502 = await state();

    // 3. Still down: the next health event's refetch gets no answer at all (status 0).
    await failWith({ network: true });
    t = await now();
    await healthEvent();
    await waitCall(t).catch(() => undefined);
    await settle(RETRIES_MS);
    const afterNetwork = await state();

    // 4. No 30 s poll restarts while the camera is gone.
    t = await now();
    await settle(POLL_MS + 5000);
    const polls = await callsSince(t);
    const afterWait = await state();

    // 5. Access comes back: Retry restores the page, its live player and the read channel.
    await failWith(null);
    t = await now();
    await page.getByRole('button', { name: 'Retry' }).first().click({ timeout: 5000 }).catch(() => undefined);
    await waitCall(t).catch(() => undefined);
    await page
      .waitForFunction(() => !document.querySelector('.ant-alert-error') && !document.body.innerText.includes('Could not refresh this camera'), null, { timeout: 15_000 })
      .catch(() => undefined);
    const restored = await page
      .waitForSelector('.sg-live-badge', { timeout: 30_000 })
      .then(() => true)
      .catch(() => false);
    await page
      .waitForFunction((cid) => window.__sg.sockets.some((s) => s.url.endsWith(`/ws/reads/${cid}`) && s.readyState === 1), ANPR_CAM, { timeout: 10_000 })
      .catch(() => undefined);
    const afterRestore = await state();

    const ok = ended(afterLoss) && ended(after502) && ended(afterNetwork) && ended(afterWait) && polls === 0 && restored && afterRestore.players === 1 && afterRestore.readsSockets === 1 && !afterRestore.errorState && !afterRestore.refreshBanner;
    record(
      'f17-latch',
      ok,
      `after the 404: ${show(afterLoss)}; after Retry → 502: ${show(after502)}; after health refetch → network error: ${show(afterNetwork)} (each want ErrorState=true banner=false players=0 /ws/reads=0); camera re-reads in the next ${(POLL_MS + 5000) / 1000} s=${polls} (want 0), then ${show(afterWait)}; Retry after access returns: live player=${restored}, ${show(afterRestore)} (want ErrorState=false banner=false players=1 /ws/reads=1)`,
    );
  } finally {
    await context.close();
  }
}

async function main() {
  const browser = await chromium.launch({ ...(CHANNEL ? { channel: CHANNEL } : {}), args: ['--no-proxy-server', '--autoplay-policy=no-user-gesture-required'] });
  const want = (k) => !ONLY || ONLY.has(k);
  const guard = async (key, fn) => {
    try {
      await fn();
    } catch (e) {
      record(key, false, `error: ${e instanceof Error ? e.message.split('\n')[0] : String(e)}`);
    }
  };
  const jobs = [];
  if (want('f17-404')) jobs.push(guard('f17-404', () => checkRefetchFailure(browser, 'f17-404', 404)));
  if (want('f17-500')) jobs.push(guard('f17-500', () => checkRefetchFailure(browser, 'f17-500', 500)));
  if (want('f17-latch')) jobs.push(guard('f17-latch', () => checkAccessLatch(browser)));
  jobs.push(
    (async () => {
      if (want('f15-whep') || want('f18-cadence')) await guard('f15-whep/f18-cadence', () => checkWhepAndCadence(browser));
      if (want('f18-remount')) await guard('f18-remount', () => checkCadenceAcrossRemount(browser));
      if (want('f15-hls')) await guard('f15-hls', () => checkHls(browser));
      if (want('f16-hook')) await guard('f16-hook', () => checkHookReset(browser));
      if (want('f16-filter')) await guard('f16-filter', () => checkFilter(browser));
      if (want('f16-nav')) await guard('f16-nav', () => checkNavigation(browser));
    })(),
  );
  await Promise.all(jobs);
  await browser.close();
  const failed = results.filter((r) => !r.ok).length;
  console.log(`${results.length - failed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

void main();
