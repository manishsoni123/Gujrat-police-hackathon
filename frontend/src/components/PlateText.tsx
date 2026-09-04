import { Tooltip } from 'antd';
import { formatPlate } from '@/utils/plate';
import { MONO_FAMILY } from '@/theme/tokens';

interface PlateTextProps {
  plate: string | null | undefined;
  /** Raw OCR string (shown in a tooltip when it differs). */
  raw?: string | null;
  size?: 'small' | 'default' | 'large';
  invalid?: boolean;
}

/** Monospace plate rendering "GJ 01 AB 1234" with the raw read in a tooltip. */
export function PlateText({ plate, raw, size = 'default', invalid }: PlateTextProps) {
  if (!plate) return <span style={{ color: '#9CA3AF' }}>—</span>;
  const display = formatPlate(plate);
  const fontSize = size === 'large' ? 18 : size === 'small' ? 12 : 13;
  const el = (
    <span
      style={{
        fontFamily: MONO_FAMILY,
        fontWeight: 600,
        fontSize,
        letterSpacing: 0.6,
        background: invalid ? '#FEF3C7' : '#F3F4F6',
        border: `1px solid ${invalid ? '#FCD34D' : '#E5E7EB'}`,
        borderRadius: 6,
        padding: size === 'large' ? '4px 10px' : '1px 6px',
        color: '#111827',
        whiteSpace: 'nowrap',
      }}
    >
      {display}
    </span>
  );
  const rawNorm = raw ? raw.replace(/\s+/g, '') : null;
  if (rawNorm && rawNorm !== plate) return <Tooltip title={`Raw OCR: ${raw}`}>{el}</Tooltip>;
  if (invalid) return <Tooltip title="Not a valid Indian registration format - searchable but not alertable">{el}</Tooltip>;
  return el;
}
