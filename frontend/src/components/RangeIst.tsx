/** IST date-time range picker that yields UTC ISO strings for the API (CONTRACT §11.3). */
import { DatePicker } from 'antd';
import { dayjs, istToUtcIso, toIst, type Dayjs } from '@/utils/time';

export interface IsoRange {
  from?: string;
  to?: string;
}

interface RangeIstProps {
  value: IsoRange;
  onChange: (r: IsoRange) => void;
  showTime?: boolean;
  allowClear?: boolean;
  style?: React.CSSProperties;
}

const PRESETS = [
  { label: 'Last hour', hours: 1 },
  { label: 'Last 6 h', hours: 6 },
  { label: 'Last 24 h', hours: 24 },
  { label: 'Last 7 days', hours: 24 * 7 },
];

export function RangeIst({ value, onChange, showTime = true, allowClear = true, style }: RangeIstProps) {
  const from = toIst(value.from);
  const to = toIst(value.to);
  return (
    <DatePicker.RangePicker
      showTime={showTime ? { format: 'HH:mm' } : false}
      format={showTime ? 'DD MMM YYYY, HH:mm' : 'DD MMM YYYY'}
      value={from && to ? [from, to] : from ? [from, null] : null}
      allowClear={allowClear}
      style={{ width: 340, ...style }}
      presets={PRESETS.map((p) => ({ label: p.label, value: [dayjs().tz('Asia/Kolkata').subtract(p.hours, 'hour'), dayjs().tz('Asia/Kolkata')] as [Dayjs, Dayjs] }))}
      onChange={(v) => onChange({ from: istToUtcIso(v?.[0] ?? null), to: istToUtcIso(v?.[1] ?? null) })}
      aria-label="Time range (IST)"
    />
  );
}

/** Strip the /media/ prefix so a crop/frame/report URL becomes the DATA_DIR-relative path for /evidence/verify. */
export function mediaRelativePath(url: string | null | undefined): string | null {
  if (!url) return null;
  const clean = url.split('?')[0];
  if (clean.startsWith('/media/')) return clean.slice('/media/'.length);
  if (clean.startsWith('data:')) return null;
  return clean.replace(/^\//, '');
}
