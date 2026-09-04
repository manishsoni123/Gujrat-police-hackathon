import { Tooltip } from 'antd';
import { fmtIst, fmtIstShort, fmtRelative } from '@/utils/time';

interface IstTimeProps {
  value: string | null | undefined;
  /** short: "04 Sep, 15:45:30" with full value in tooltip; relative: "2 min ago"; full: complete IST string. */
  mode?: 'short' | 'relative' | 'full';
  muted?: boolean;
}

/** Never renders a raw ISO string: IST always, with the full value in a tooltip. */
export function IstTime({ value, mode = 'short', muted }: IstTimeProps) {
  if (!value) return <span style={{ color: '#9CA3AF' }}>—</span>;
  const full = fmtIst(value);
  const style = { whiteSpace: 'nowrap' as const, color: muted ? '#6B7280' : undefined, fontVariantNumeric: 'tabular-nums' as const };
  if (mode === 'full') return <span style={style}>{full}</span>;
  const text = mode === 'relative' ? fmtRelative(value) : fmtIstShort(value);
  return (
    <Tooltip title={full}>
      <span style={style}>{text}</span>
    </Tooltip>
  );
}
