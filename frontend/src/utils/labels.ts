/** Operator-facing labels for raw API enums that have no colour meta in theme/colours.ts. */

export const MATCH_LABEL: Record<string, string> = { exact: 'Exact', fuzzy: 'Near match' };
export const CONFIRMATION_LABEL: Record<string, string> = { confirmed: 'Confirmed', rejected: 'Rejected' };

/** "1 character off · 83 % similar" for a fuzzy candidate. */
export function fuzzyDescription(distance: number, score: number): string {
  const chars = `${distance} character${distance === 1 ? '' : 's'} off`;
  return `${chars} · ${Math.round(score * 100)} % similar`;
}

/** Camera source enum → readable origin (wall "systems" badge, import summaries). */
export const CAMERA_SOURCE_LABEL: Record<string, string> = {
  sandbox: 'organiser sandbox catalogue',
  csv: 'CSV import',
  api: 'bulk API',
  manual: 'added manually',
  own: 'our own camera',
};
export function cameraSourceLabel(source: string | null | undefined): string {
  if (!source) return '—';
  return CAMERA_SOURCE_LABEL[source] ?? source;
}
