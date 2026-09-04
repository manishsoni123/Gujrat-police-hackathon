/** Shared number / size / distance / text formatting helpers. */

export function fmtNumber(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(n);
}

export function fmtPct(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return `${n.toFixed(digits)} %`;
}

export function fmtConfidence(c: number | null | undefined): string {
  if (c === null || c === undefined) return '—';
  return `${Math.round(c * 100)} %`;
}

export function fmtBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function fmtKm(km: number | null | undefined): string {
  if (km === null || km === undefined || Number.isNaN(km)) return '—';
  if (km < 1) return `${Math.round(km * 1000)} m`;
  return `${km.toFixed(2)} km`;
}

export function fmtMetres(m: number | null | undefined): string {
  if (m === null || m === undefined || Number.isNaN(m)) return '—';
  if (m >= 1000) return `${(m / 1000).toFixed(2)} km`;
  return `${Math.round(m)} m`;
}

export function fmtSpeed(kmh: number | null | undefined): string {
  if (kmh === null || kmh === undefined || Number.isNaN(kmh)) return '—';
  return `${Math.round(kmh)} km/h`;
}

export function fmtLatLon(lat: number | null | undefined, lon: number | null | undefined): string {
  if (lat === null || lat === undefined || lon === null || lon === undefined) return '—';
  return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

export function fmtLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

/** "under_maintenance" → "Under maintenance". */
export function humanise(value: string | null | undefined): string {
  if (!value) return '—';
  const s = value.replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function shortHash(hash: string | null | undefined, n = 16): string {
  if (!hash) return '—';
  return hash.length > n ? `${hash.slice(0, n)}…` : hash;
}

export function truncate(text: string | null | undefined, n = 60): string {
  if (!text) return '';
  return text.length > n ? `${text.slice(0, n - 1)}…` : text;
}

/** Append a cache-busting query parameter unless the URL is a data: URI. */
export function withCacheBuster(url: string | null | undefined, t: number = Date.now()): string | null {
  if (!url) return null;
  if (url.startsWith('data:') || url.startsWith('blob:')) return url;
  return `${url}${url.includes('?') ? '&' : '?'}t=${Math.floor(t / 1000)}`;
}

export function capitalise(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Camera type enum → readable label (single source for tables, forms, map popups). */
export const CAMERA_TYPE_LABEL: Record<string, string> = {
  analog: 'Analog',
  ip: 'IP camera',
  ptz: 'PTZ',
  dome: 'Dome',
  bullet: 'Bullet',
  anpr: 'ANPR',
  other: 'Other',
};

export function cameraTypeLabel(type: string | null | undefined): string {
  if (!type) return '—';
  return CAMERA_TYPE_LABEL[type] ?? humanise(type);
}

/** "45° / 70°", or a single dash when neither heading nor field of view is known. */
export function fmtHeadingFov(heading: number | null | undefined, fov: number | null | undefined): string {
  if (heading == null && fov == null) return '—';
  return `${heading ?? '—'}° / ${fov ?? '—'}°`;
}

/** "Gandhinagar · Sector 21 PS" — omits the police station segment when it is unknown. */
export function fmtPlace(district: string | null | undefined, policeStation?: string | null): string {
  const d = district ?? '—';
  return policeStation ? `${d} · ${policeStation}` : d;
}
