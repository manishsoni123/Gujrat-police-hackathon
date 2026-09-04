import type { ReactNode } from 'react';
import { Card, Skeleton, Tooltip } from 'antd';
import { fmtNumber } from '@/utils/format';

interface KpiTileProps {
  label: string;
  value: number | string | null | undefined;
  suffix?: string;
  hint?: string;
  icon?: ReactNode;
  colour?: string;
  loading?: boolean;
  footer?: ReactNode;
  onClick?: () => void;
  digits?: number;
}

/** Dashboard statistic tile: label, large value, optional icon/colour accent and footer. */
export function KpiTile({ label, value, suffix, hint, icon, colour = '#1E4DB7', loading, footer, onClick, digits = 0 }: KpiTileProps) {
  const rendered = typeof value === 'number' ? fmtNumber(value, digits) : value ?? '—';
  const body = (
    <Card
      size="small"
      hoverable={Boolean(onClick)}
      onClick={onClick}
      styles={{ body: { padding: '14px 16px' } }}
      style={{ height: '100%', borderTop: `3px solid ${colour}`, cursor: onClick ? 'pointer' : undefined }}
    >
      {loading ? (
        <Skeleton active paragraph={{ rows: 1 }} title={{ width: '50%' }} />
      ) : (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 12, color: '#6B7280', fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {label}
            </div>
            <div style={{ fontSize: 26, fontWeight: 600, lineHeight: '34px', color: '#111827', fontVariantNumeric: 'tabular-nums' }}>
              {rendered}
              {suffix ? <span style={{ fontSize: 13, color: '#6B7280', marginLeft: 4, fontWeight: 500 }}>{suffix}</span> : null}
            </div>
            {footer ? <div style={{ fontSize: 12, color: '#6B7280', marginTop: 2 }}>{footer}</div> : null}
          </div>
          {icon ? (
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 8,
                background: `${colour}14`,
                color: colour,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 18,
                flexShrink: 0,
              }}
            >
              {icon}
            </div>
          ) : null}
        </div>
      )}
    </Card>
  );
  return hint ? <Tooltip title={hint}>{body}</Tooltip> : body;
}
