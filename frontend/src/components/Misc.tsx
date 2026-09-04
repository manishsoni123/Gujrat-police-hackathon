/** Small shared widgets: confidence bar, crop thumbnail + lightbox, hash display, copy button, filter toolbar. */
import { useState, type CSSProperties, type ReactNode } from 'react';
import { Button, Image, Tooltip, Typography, message } from 'antd';
import { CopyOutlined, PictureOutlined } from '@ant-design/icons';
import { shortHash } from '@/utils/format';

export function ConfidenceBar({ value, width = 90 }: { value: number | null | undefined; width?: number }) {
  if (value === null || value === undefined) return <span style={{ color: '#9CA3AF' }}>—</span>;
  const pct = Math.round(value * 100);
  const colour = pct >= 85 ? '#16A34A' : pct >= 60 ? '#D97706' : '#DC2626';
  return (
    <Tooltip title={`Confidence ${pct} %`}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, whiteSpace: 'nowrap' }}>
        <div style={{ width, height: 6, background: '#E5E7EB', borderRadius: 3, overflow: 'hidden', flex: 'none' }} aria-hidden>
          <div style={{ width: `${pct}%`, height: '100%', background: colour }} />
        </div>
        <span style={{ fontVariantNumeric: 'tabular-nums', fontSize: 12, color: '#374151', minWidth: 38, whiteSpace: 'nowrap' }}>{pct} %</span>
      </div>
    </Tooltip>
  );
}

interface CropThumbProps {
  src: string | null | undefined;
  alt: string;
  width?: number;
  height?: number;
  sha256?: string | null;
  preview?: boolean;
  style?: CSSProperties;
}

/** Plate crop thumbnail with a lightbox preview and alt text. */
export function CropThumb({ src, alt, width = 96, height = 40, sha256, preview = true, style }: CropThumbProps) {
  if (!src) {
    return (
      <div
        style={{ width, height, background: '#F3F4F6', border: '1px dashed #E5E7EB', borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#9CA3AF', ...style }}
        aria-label={`${alt} (no image)`}
      >
        <PictureOutlined />
      </div>
    );
  }
  return (
    <Image
      src={src}
      alt={alt}
      width={width}
      height={height}
      preview={preview ? { mask: 'View', toolbarRender: () => (sha256 ? <div className="sg-lightbox-hash">SHA-256 {sha256}</div> : null) } : false}
      style={{ objectFit: 'contain', borderRadius: 4, border: '1px solid #E5E7EB', background: '#111827', ...style }}
    />
  );
}

export function HashText({ hash, full = false }: { hash: string | null | undefined; full?: boolean }) {
  if (!hash) return <span style={{ color: '#9CA3AF' }}>—</span>;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(hash);
      message.success('Hash copied');
    } catch {
      message.warning('Clipboard unavailable');
    }
  };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <Tooltip title={hash}>
        <code style={{ fontSize: 12, wordBreak: full ? 'break-all' : undefined }}>{full ? hash : shortHash(hash)}</code>
      </Tooltip>
      <Button type="text" size="small" icon={<CopyOutlined />} onClick={copy} aria-label="Copy hash" />
    </span>
  );
}

export function Toolbar({ children, right, style }: { children?: ReactNode; right?: ReactNode; style?: CSSProperties }) {
  return (
    <div className="sg-toolbar" style={style}>
      <div className="sg-toolbar-left">{children}</div>
      {right ? <div className="sg-toolbar-right">{right}</div> : null}
    </div>
  );
}

export function SectionTitle({ children, extra }: { children: ReactNode; extra?: ReactNode }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
      <Typography.Text strong style={{ fontSize: 14 }}>
        {children}
      </Typography.Text>
      {extra}
    </div>
  );
}

export function Muted({ children }: { children: ReactNode }) {
  return <span style={{ color: '#6B7280' }}>{children}</span>;
}

/** Controlled show-more list wrapper. */
export function ShowMore<T>({ items, initial = 5, render }: { items: T[]; initial?: number; render: (item: T, i: number) => ReactNode }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? items : items.slice(0, initial);
  return (
    <div>
      {shown.map(render)}
      {items.length > initial ? (
        <Button type="link" size="small" onClick={() => setExpanded((v) => !v)} style={{ paddingInline: 0 }}>
          {expanded ? 'Show less' : `Show ${items.length - initial} more`}
        </Button>
      ) : null}
    </div>
  );
}
