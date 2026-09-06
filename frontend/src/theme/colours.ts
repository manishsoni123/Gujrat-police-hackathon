/**
 * Single source of truth for every status / priority / domain colour
 * (CONTRACT §11.3). Components import from here; never hard-code a colour
 * in a page.
 */

export const BRAND = {
  navy: '#0B1F3A',
  primary: '#1E4DB7',
  success: '#16A34A',
  warning: '#D97706',
  error: '#DC2626',
  info: '#0EA5E9',
  bgLayout: '#F5F7FA',
  bgContainer: '#FFFFFF',
  text: '#111827',
  textSecondary: '#4B5563',
  border: '#E5E7EB',
  headerBg: '#F3F4F6',
  muted: '#9CA3AF',
} as const;

export type CameraStatus = 'unknown' | 'online' | 'degraded' | 'offline' | 'not_streaming' | 'retired';
export type AlertPriority = 'critical' | 'high' | 'medium' | 'low';
export type AlertStatus = 'new' | 'acknowledged' | 'closed';
export type MaintenanceStatus = 'ok' | 'under_maintenance' | 'faulty' | 'decommissioned';
export type ConfidenceLevel = 'exact' | 'possible';
export type AmcStatus = 'expired' | 'expiring' | 'ok' | 'none';
export type WatchlistReason =
  | 'stolen'
  | 'wanted'
  | 'blacklisted'
  | 'missing'
  | 'suspect'
  | 'arrested'
  | 'unidentified_body'
  | 'other';

export const CAMERA_STATUS: Record<CameraStatus, { colour: string; label: string }> = {
  online: { colour: '#16A34A', label: 'Online' },
  degraded: { colour: '#D97706', label: 'Degraded' },
  offline: { colour: '#DC2626', label: 'Offline' },
  // Never delivered a stream (catalogue live=false, or a source that never answered) — not an outage (CONTRACT amendment 2026-09-05 §5.6).
  not_streaming: { colour: '#6B7280', label: 'Not streaming' },
  unknown: { colour: '#9CA3AF', label: 'Unknown' },
  retired: { colour: '#6B7280', label: 'Retired' },
};

/**
 * "Not streaming" presentation: grey, never red. The API status `not_streaming` covers every camera
 * that has never delivered a stream; when the catalogue `live` flag is `false` the label says so
 * ("Not streaming (catalogue)") because that is the usual reason on the sandbox.
 */
export const CATALOGUE_NOT_STREAMING = {
  colour: CAMERA_STATUS.not_streaming.colour,
  label: 'Not streaming (catalogue)',
  shortLabel: 'Not streaming',
  hint: 'This camera has never delivered a stream — the catalogue marks it live=false (or the source never answered). The relay does not pull it, so it is neither online nor offline and raises no camera-offline alert.',
} as const;

/** Status to render for a camera: `not_streaming` when the catalogue says live=false (retired wins), else the API status. */
export function displayStatus(status: CameraStatus | string, live: boolean | null | undefined): CameraStatus {
  if (live === false && status !== 'retired') return 'not_streaming';
  return (status as CameraStatus) in CAMERA_STATUS ? (status as CameraStatus) : 'unknown';
}

/** Label for a status + live flag pair ("Not streaming (catalogue)" when live=false). */
export function statusLabel(status: CameraStatus | string, live?: boolean | null): string {
  const st = displayStatus(status, live);
  if (st === 'not_streaming' && live === false) return CATALOGUE_NOT_STREAMING.label;
  return CAMERA_STATUS[st].label;
}

export const ALERT_PRIORITY: Record<AlertPriority, { colour: string; label: string; rank: number }> = {
  critical: { colour: '#DC2626', label: 'Critical', rank: 0 },
  high: { colour: '#EA580C', label: 'High', rank: 1 },
  medium: { colour: '#D97706', label: 'Medium', rank: 2 },
  low: { colour: '#0EA5E9', label: 'Low', rank: 3 },
};

export const ALERT_STATUS: Record<AlertStatus, { colour: string; label: string }> = {
  new: { colour: '#DC2626', label: 'New' },
  acknowledged: { colour: '#D97706', label: 'Acknowledged' },
  closed: { colour: '#6B7280', label: 'Closed' },
};

export const MAINTENANCE_STATUS: Record<MaintenanceStatus, { colour: string; label: string }> = {
  ok: { colour: '#16A34A', label: 'OK' },
  under_maintenance: { colour: '#7C3AED', label: 'Under maintenance' },
  faulty: { colour: '#DC2626', label: 'Faulty' },
  decommissioned: { colour: '#6B7280', label: 'Decommissioned' },
};

export const CONFIDENCE_LEVEL: Record<ConfidenceLevel, { colour: string; label: string }> = {
  exact: { colour: '#16A34A', label: 'Exact match' },
  possible: { colour: '#D97706', label: 'Possible match' },
};

export const AMC_STATUS: Record<AmcStatus, { colour: string; label: string }> = {
  expired: { colour: '#DC2626', label: 'AMC expired' },
  expiring: { colour: '#D97706', label: 'AMC expiring' },
  ok: { colour: '#16A34A', label: 'AMC valid' },
  none: { colour: '#9CA3AF', label: 'No AMC' },
};

export const WATCHLIST_REASON: Record<WatchlistReason, { colour: string; label: string }> = {
  stolen: { colour: '#DC2626', label: 'Stolen' },
  wanted: { colour: '#B91C1C', label: 'Wanted' },
  blacklisted: { colour: '#EA580C', label: 'Blacklisted' },
  missing: { colour: '#D97706', label: 'Missing' },
  suspect: { colour: '#0EA5E9', label: 'Suspect' },
  arrested: { colour: '#7C3AED', label: 'Arrested' },
  unidentified_body: { colour: '#6B7280', label: 'Unidentified body' },
  other: { colour: '#9CA3AF', label: 'Other' },
};

export type LocationConfidence = 'exact' | 'approx' | 'guess';

/**
 * Confidence of a camera's coordinates. Organiser-sandbox rows are team-inferred from the camera name alone
 * (media/cameras_enrichment.README.md): exact = landmark found (~100 m), approx = area/junction (~1 km),
 * guess = district-HQ fallback or best candidate. Map markers: exact solid, approx dashed ring, guess hollow.
 */
export const LOCATION_CONFIDENCE: Record<LocationConfidence, { colour: string; label: string; hint: string; marker: string }> = {
  exact: { colour: '#16A34A', label: 'Exact', hint: 'Landmark found in OpenStreetMap; the pole is expected within ~100 m', marker: 'solid' },
  approx: { colour: '#D97706', label: 'Approximate', hint: 'Approximate location (team-inferred): area or junction centre, within ~1 km', marker: 'dashed ring' },
  guess: { colour: '#6B7280', label: 'Guess', hint: 'Location is a guess (team-inferred): district-headquarters fallback or best candidate; may be tens of km off', marker: 'hollow ring' },
};

export const EVENT_TYPE: Record<string, { colour: string; label: string }> = {
  accident: { colour: '#DC2626', label: 'Accident' },
  suspicious: { colour: '#D97706', label: 'Suspicious' },
  checkpoint: { colour: '#1E4DB7', label: 'Checkpoint' },
  other: { colour: '#6B7280', label: 'Other' },
  watchlist_hit: { colour: '#DC2626', label: 'Watchlist hit' },
  loop_reset: { colour: '#9CA3AF', label: 'Loop reset' },
  intrusion: { colour: '#7C3AED', label: 'Intrusion' },
  camera_offline: { colour: '#DC2626', label: 'Camera offline' },
  camera_online: { colour: '#16A34A', label: 'Camera online' },
};

export const OBJECT_CLASS_COLOURS: Record<string, string> = {
  person: '#7C3AED',
  bicycle: '#0891B2',
  car: '#1E4DB7',
  motorcycle: '#D97706',
  bus: '#16A34A',
  truck: '#EA580C',
};

const DEPARTMENT_FIXED: Record<string, string> = {
  POLICE: '#1E4DB7',
  HEALTH: '#16A34A',
  GSRTC: '#D97706',
  PANCHAYAT: '#7C3AED',
  MUNICIPAL: '#0EA5E9',
  RTO: '#DB2777',
  FCS: '#65A30D',
  UNASSIGNED: '#9CA3AF',
};
const DEPARTMENT_CYCLE = ['#0891B2', '#9333EA', '#CA8A04', '#4F46E5', '#059669', '#B91C1C'];

/** Deterministic department colour: fixed palette for the seven main departments, cycling for the rest. */
export function departmentColour(code: string | null | undefined): string {
  if (!code) return DEPARTMENT_FIXED.UNASSIGNED;
  const fixed = DEPARTMENT_FIXED[code];
  if (fixed) return fixed;
  let h = 0;
  for (let i = 0; i < code.length; i += 1) h = (h * 31 + code.charCodeAt(i)) >>> 0;
  return DEPARTMENT_CYCLE[h % DEPARTMENT_CYCLE.length];
}

export const CHART_SERIES = ['#1E4DB7', '#0EA5E9', '#16A34A', '#D97706', '#7C3AED', '#DB2777', '#0891B2', '#CA8A04'];
