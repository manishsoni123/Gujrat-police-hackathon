/**
 * Shared coloured tags so status / priority / reason colours are identical on
 * every page (single source: theme/colours.ts).
 */
import { Tag, Tooltip } from 'antd';
import { ToolOutlined, WarningOutlined, StopOutlined, CheckCircleOutlined } from '@ant-design/icons';
import {
  ALERT_PRIORITY,
  ALERT_STATUS,
  AMC_STATUS,
  CAMERA_STATUS,
  CONFIDENCE_LEVEL,
  EVENT_TYPE,
  MAINTENANCE_STATUS,
  WATCHLIST_REASON,
  departmentColour,
  type AlertPriority,
  type AlertStatus,
  type AmcStatus,
  type CameraStatus,
  type ConfidenceLevel,
  type MaintenanceStatus,
  type WatchlistReason,
} from '@/theme/colours';
import { humanise } from '@/utils/format';

interface BaseTagProps {
  colour: string;
  label: string;
  icon?: React.ReactNode;
  dot?: boolean;
  pulse?: boolean;
  size?: 'small' | 'default';
  title?: string;
}

/** Soft coloured pill: tinted background + coloured text, always with text. */
export function ColourTag({ colour, label, icon, dot, pulse, size = 'default', title }: BaseTagProps) {
  const tag = (
    <Tag
      style={{
        color: colour,
        background: `${colour}14`,
        borderColor: `${colour}40`,
        fontWeight: 500,
        margin: 0,
        fontSize: size === 'small' ? 11 : 12,
        lineHeight: size === 'small' ? '18px' : '22px',
        paddingInline: size === 'small' ? 6 : 8,
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        whiteSpace: 'nowrap',
      }}
    >
      {dot ? <span className={pulse ? 'sg-dot sg-dot-pulse' : 'sg-dot'} style={{ background: colour }} aria-hidden /> : null}
      {icon}
      {label}
    </Tag>
  );
  return title ? <Tooltip title={title}>{tag}</Tooltip> : tag;
}

export function StatusTag({ status, size }: { status: CameraStatus | string; size?: 'small' | 'default' }) {
  const meta = CAMERA_STATUS[status as CameraStatus] ?? { colour: '#9CA3AF', label: humanise(status) };
  return <ColourTag colour={meta.colour} label={meta.label} dot pulse={status === 'online'} size={size} />;
}

export function PriorityTag({ priority, size }: { priority: AlertPriority | string; size?: 'small' | 'default' }) {
  const meta = ALERT_PRIORITY[priority as AlertPriority] ?? { colour: '#9CA3AF', label: humanise(priority) };
  return <ColourTag colour={meta.colour} label={meta.label} size={size} />;
}

export function AlertStatusTag({ status, size }: { status: AlertStatus | string; size?: 'small' | 'default' }) {
  const meta = ALERT_STATUS[status as AlertStatus] ?? { colour: '#9CA3AF', label: humanise(status) };
  return <ColourTag colour={meta.colour} label={meta.label} dot={status === 'new'} pulse={status === 'new'} size={size} />;
}

export function ReasonTag({ reason, size }: { reason: WatchlistReason | string; size?: 'small' | 'default' }) {
  const meta = WATCHLIST_REASON[reason as WatchlistReason] ?? { colour: '#9CA3AF', label: humanise(reason) };
  return <ColourTag colour={meta.colour} label={meta.label} size={size} />;
}

export function ConfidenceLevelTag({ level, size }: { level: ConfidenceLevel | null | undefined; size?: 'small' | 'default' }) {
  if (!level) return null;
  const meta = CONFIDENCE_LEVEL[level];
  return <ColourTag colour={meta.colour} label={meta.label} size={size} />;
}

export function MaintenanceTag({ status, size }: { status: MaintenanceStatus | string; size?: 'small' | 'default' }) {
  if (status === 'ok') return <ColourTag colour={MAINTENANCE_STATUS.ok.colour} label="OK" icon={<CheckCircleOutlined />} size={size} />;
  const meta = MAINTENANCE_STATUS[status as MaintenanceStatus] ?? { colour: '#9CA3AF', label: humanise(status) };
  const icon = status === 'under_maintenance' ? <ToolOutlined /> : status === 'faulty' ? <WarningOutlined /> : <StopOutlined />;
  return <ColourTag colour={meta.colour} label={meta.label} icon={icon} size={size} />;
}

export function AmcTag({ status, size }: { status: AmcStatus | string | null | undefined; size?: 'small' | 'default' }) {
  if (!status || status === 'none') return <span style={{ color: '#9CA3AF' }}>—</span>;
  const meta = AMC_STATUS[status as AmcStatus] ?? { colour: '#9CA3AF', label: humanise(status) };
  return <ColourTag colour={meta.colour} label={meta.label} size={size} />;
}

export function EventTypeTag({ type, size }: { type: string; size?: 'small' | 'default' }) {
  const meta = EVENT_TYPE[type] ?? { colour: '#9CA3AF', label: humanise(type) };
  return <ColourTag colour={meta.colour} label={meta.label} size={size} />;
}

export function DepartmentTag({ code, name, size }: { code: string | null | undefined; name?: string | null; size?: 'small' | 'default' }) {
  const c = departmentColour(code);
  return <ColourTag colour={c} label={code ?? 'UNASSIGNED'} title={name ?? undefined} size={size} />;
}

export function CodecTag({ codec, size = 'small' }: { codec: string | null | undefined; size?: 'small' | 'default' }) {
  if (!codec) return null;
  const colour = codec === 'H265' ? '#7C3AED' : codec === 'H264' ? '#1E4DB7' : '#6B7280';
  return <ColourTag colour={colour} label={codec === 'H265' ? 'H.265' : codec === 'H264' ? 'H.264' : codec} size={size} title={codec === 'H265' ? 'Transcoded to H.264 by the relay for browser playback' : undefined} />;
}

export function RoleTag({ role, size = 'small' }: { role: string; size?: 'small' | 'default' }) {
  const colours: Record<string, string> = { admin: '#DC2626', dept_admin: '#7C3AED', operator: '#1E4DB7', viewer: '#6B7280' };
  const labels: Record<string, string> = { admin: 'Admin', dept_admin: 'Dept admin', operator: 'Operator', viewer: 'Viewer' };
  return <ColourTag colour={colours[role] ?? '#6B7280'} label={labels[role] ?? humanise(role)} size={size} />;
}

export function BoolTag({ value, yes = 'Yes', no = 'No', size = 'small' }: { value: boolean | null | undefined; yes?: string; no?: string; size?: 'small' | 'default' }) {
  if (value === null || value === undefined) return <span style={{ color: '#9CA3AF' }}>—</span>;
  return <ColourTag colour={value ? '#16A34A' : '#9CA3AF'} label={value ? yes : no} size={size} />;
}
