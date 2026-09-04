import dayjs, { type Dayjs } from 'dayjs';
import utc from 'dayjs/plugin/utc';
import timezone from 'dayjs/plugin/timezone';
import relativeTime from 'dayjs/plugin/relativeTime';
import duration from 'dayjs/plugin/duration';

dayjs.extend(utc);
dayjs.extend(timezone);
dayjs.extend(relativeTime);
dayjs.extend(duration);

export const IST = 'Asia/Kolkata';

/** Parse any ISO input as a Dayjs in IST. Naive strings are treated as UTC (contract global rule). */
export function toIst(iso: string | Date | Dayjs | null | undefined): Dayjs | null {
  if (iso === null || iso === undefined || iso === '') return null;
  const d = dayjs.utc(iso);
  if (!d.isValid()) return null;
  return d.tz(IST);
}

/** "04 Sep 2026, 15:45:30 IST" */
export function fmtIst(iso: string | Date | null | undefined): string {
  const d = toIst(iso);
  return d ? `${d.format('DD MMM YYYY, HH:mm:ss')} IST` : '—';
}

/** "04 Sep, 15:45:30" for dense tables (full value goes in a tooltip). */
export function fmtIstShort(iso: string | Date | null | undefined): string {
  const d = toIst(iso);
  return d ? d.format('DD MMM, HH:mm:ss') : '—';
}

/** "15:45:29" */
export function fmtIstTime(iso: string | Date | null | undefined): string {
  const d = toIst(iso);
  return d ? d.format('HH:mm:ss') : '—';
}

/** "04 Sep 2026" for date-only values (YYYY-MM-DD) or timestamps. */
export function fmtIstDate(value: string | null | undefined): string {
  if (!value) return '—';
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return dayjs(value).format('DD MMM YYYY');
  const d = toIst(value);
  return d ? d.format('DD MMM YYYY') : '—';
}

/** "just now" (< 45 s), then dayjs' "2 minutes ago" (only for alert panel / dashboard, with fmtIst in the tooltip). */
export function fmtRelative(iso: string | Date | null | undefined): string {
  const d = toIst(iso);
  if (!d) return '—';
  if (Math.abs(dayjs().diff(d, 'second')) < 45) return 'just now';
  return d.fromNow();
}

/** Current time as IST Dayjs. */
export function nowIst(): Dayjs {
  return dayjs().tz(IST);
}

/** Convert an IST-picked Dayjs (from a DatePicker) to a UTC ISO string with ms + Z. */
export function istToUtcIso(d: Dayjs | null | undefined): string | undefined {
  if (!d) return undefined;
  return d.tz(IST, true).utc().toISOString();
}

/** UTC ISO string for "now minus n hours". */
export function hoursAgoIso(hours: number): string {
  return dayjs().subtract(hours, 'hour').toISOString();
}

export function nowIso(): string {
  return dayjs().toISOString();
}

/** File-name friendly IST stamp: 20260904_1545 */
export function istStamp(): string {
  return nowIst().format('YYYYMMDD_HHmm');
}

/** Human duration from seconds: "2 h 05 min", "35 s", "1.2 min". */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—';
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const m = seconds / 60;
  if (m < 60) return `${m.toFixed(m < 10 ? 1 : 0)} min`;
  const h = Math.floor(m / 60);
  const rem = Math.round(m - h * 60);
  return `${h} h ${rem.toString().padStart(2, '0')} min`;
}

export function fmtMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return '—';
  return fmtDuration(minutes * 60);
}

export { dayjs };
export type { Dayjs };
