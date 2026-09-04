/**
 * Generated SVG images (data URIs) for the mock API: plate crops, full frames,
 * camera snapshots. No binary assets are shipped for the mock.
 */

function svgUri(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg.replace(/\s+/g, ' ').trim())}`;
}

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** A white Indian number plate with the IND strip, ~320×90. */
export function plateCrop(display: string, twoLine = false, noise = 0): string {
  const w = twoLine ? 260 : 330;
  const h = twoLine ? 120 : 90;
  const parts = display.split(' ');
  const line1 = twoLine ? parts.slice(0, 2).join(' ') : display;
  const line2 = twoLine ? parts.slice(2).join(' ') : '';
  const blur = noise > 0 ? `filter="url(#b)"` : '';
  return svgUri(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
    <defs><filter id="b"><feGaussianBlur stdDeviation="${noise}"/></filter></defs>
    <rect width="${w}" height="${h}" fill="#1f2937"/>
    <rect x="4" y="4" width="${w - 8}" height="${h - 8}" rx="6" fill="#F5F5F5" stroke="#111" stroke-width="3"/>
    <rect x="8" y="8" width="26" height="${h - 16}" rx="3" fill="#1E4DB7"/>
    <text x="21" y="${h / 2 + 4}" font-family="DejaVu Sans, Arial" font-size="11" font-weight="700" fill="#fff" text-anchor="middle" transform="rotate(-90 21 ${h / 2})">IND</text>
    ${
      twoLine
        ? `<text x="${(w + 30) / 2}" y="52" font-family="DejaVu Sans, Arial" font-size="40" font-weight="700" fill="#111" text-anchor="middle" ${blur}>${esc(line1)}</text>
           <text x="${(w + 30) / 2}" y="100" font-family="DejaVu Sans, Arial" font-size="40" font-weight="700" fill="#111" text-anchor="middle" ${blur}>${esc(line2)}</text>`
        : `<text x="${(w + 30) / 2}" y="${h / 2 + 16}" font-family="DejaVu Sans, Arial" font-size="44" font-weight="700" fill="#111" text-anchor="middle" letter-spacing="2" ${blur}>${esc(line1)}</text>`
    }
  </svg>`);
}

const CAMERA_HUES = [210, 25, 140, 275, 45, 190, 330, 95, 10, 240];

/** A synthetic 960×540 frame: gradient sky, road band, vehicle with plate, camera label and timestamp. */
export function cameraFrame(cameraId: number, label: string, plate: string | null, ts: string, w = 960, h = 540): string {
  const hue = CAMERA_HUES[cameraId % CAMERA_HUES.length];
  const vx = 120 + ((cameraId * 97) % 520);
  return svgUri(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 960 540">
    <defs>
      <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="hsl(${hue},45%,28%)"/><stop offset="1" stop-color="hsl(${hue},35%,52%)"/></linearGradient>
    </defs>
    <rect width="960" height="540" fill="url(#sky)"/>
    <rect y="330" width="960" height="210" fill="#3f3f46"/>
    <rect y="326" width="960" height="4" fill="#71717a"/>
    <g stroke="#e5e7eb" stroke-width="5" stroke-dasharray="40 30"><line x1="0" y1="440" x2="960" y2="440"/></g>
    <rect x="0" y="520" width="960" height="20" fill="#27272a"/>
    ${plate ? `<g><rect x="${vx}" y="340" width="520" height="150" rx="18" fill="#111827"/><rect x="${vx + 30}" y="352" width="460" height="52" rx="8" fill="#1f2937"/>
    <rect x="${vx + 200}" y="428" width="230" height="52" rx="4" fill="#F5F5F5" stroke="#000" stroke-width="3"/>
    <rect x="${vx + 204}" y="432" width="16" height="44" fill="#1E4DB7"/>
    <text x="${vx + 322}" y="466" font-family="DejaVu Sans, Arial" font-size="32" font-weight="700" fill="#111" text-anchor="middle">${esc(plate)}</text></g>` : ''}
    <rect x="14" y="14" width="${Math.min(600, 40 + label.length * 11)}" height="30" rx="4" fill="rgba(0,0,0,0.55)"/>
    <text x="24" y="35" font-family="DejaVu Sans Mono, Consolas, monospace" font-size="16" fill="#e5e7eb">cam ${cameraId} · ${esc(label.toLowerCase())}</text>
    <rect x="770" y="14" width="176" height="30" rx="4" fill="rgba(0,0,0,0.55)"/>
    <text x="858" y="35" font-family="DejaVu Sans Mono, Consolas, monospace" font-size="16" fill="#e5e7eb" text-anchor="middle">${esc(ts)}</text>
  </svg>`);
}

export function snapshot(cameraId: number, label: string, plate: string | null, ts: string): string {
  return cameraFrame(cameraId, label, plate, ts, 480, 270);
}
