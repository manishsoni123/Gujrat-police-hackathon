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
  CATALOGUE_NOT_STREAMING,
  CONFIDENCE_LEVEL,
  EVENT_TYPE,
  LOCATION_CONFIDENCE,
  MAINTENANCE_STATUS,
  WATCHLIST_REASON,
  departmentColour,
  displayStatus,
  type AlertPriority,
  type AlertStatus,
  type AmcStatus,
  type CameraStatus,
  type ConfidenceLevel,
  type LocationConfidence,
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

/**
 * Camera status pill. Pass the catalogue `live` flag where it is known: a camera the catalogue marks
 * live=false is shown as a grey "Not streaming (catalogue)" instead of a red "Offline".
 */
export function StatusTag({ status, live, size, short }: { status: CameraStatus | string; live?: boolean | null; size?: 'small' | 'default'; short?: boolean }) {
  const st = displayStatus(status, live);
  if (st === 'not_streaming') {
    const label = live === false && !short ? CATALOGUE_NOT_STREAMING.label : CATALOGUE_NOT_STREAMING.shortLabel;
    return <ColourTag colour={CATALOGUE_NOT_STREAMING.colour} label={label} dot size={size} title={CATALOGUE_NOT_STREAMING.hint} />;
  }
  const meta = CAMERA_STATUS[st] ?? { colour: '#9CA3AF', label: humanise(status) };
  return <ColourTag colour={meta.colour} label={meta.label} dot pulse={st === 'online'} size={size} />;
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

/** Codec enum → display label ("H264" → "H.264"), shared by CodecTag and the stream metadata rows. */
export function codecLabel(codec: string | null | undefined): string {
  if (!codec) return '—';
  if (codec === 'H265') return 'H.265';
  if (codec === 'H264') return 'H.264';
  return codec;
}

export function CodecTag({ codec, size = 'small' }: { codec: string | null | undefined; size?: 'small' | 'default' }) {
  if (!codec) return null;
  const colour = codec === 'H265' ? '#7C3AED' : codec === 'H264' ? '#1E4DB7' : '#6B7280';
  return <ColourTag colour={colour} label={codecLabel(codec)} size={size} title={codec === 'H265' ? 'Transcoded to H.264 by the relay for browser playback' : undefined} />;
}

/** Small red "Watchlist hit" marker used beside plates in read tables and drawers. */
export function WatchlistHitTag({ size = 'small' }: { size?: 'small' | 'default' }) {
  return <ColourTag colour="#DC2626" label="Watchlist hit" size={size} />;
}

export function RoleTag({ role, size = 'small' }: { role: string; size?: 'small' | 'default' }) {
  const colours: Record<string, string> = { admin: '#DC2626', dept_admin: '#7C3AED', operator: '#1E4DB7', viewer: '#6B7280' };
  const labels: Record<string, string> = { admin: 'Admin', dept_admin: 'Dept admin', operator: 'Operator', viewer: 'Viewer' };
  return <ColourTag colour={colours[role] ?? '#6B7280'} label={labels[role] ?? humanise(role)} size={size} />;
}

/** Coordinate confidence of an organiser-sandbox camera (exact / approximate / guess, all team-inferred); "n/a" when not applicable. */
export function LocationConfidenceTag({ confidence, size = 'small' }: { confidence: LocationConfidence | string | null | undefined; size?: 'small' | 'default' }) {
  const meta = confidence ? LOCATION_CONFIDENCE[confidence as LocationConfidence] : undefined;
  if (!meta) return <span style={{ color: '#9CA3AF' }} title="No confidence recorded (coordinates supplied directly)">n/a</span>;
  return <ColourTag colour={meta.colour} label={`${meta.label} (team-inferred)`} size={size} title={meta.hint} />;
}

export function BoolTag({ value, yes = 'Yes', no = 'No', size = 'small' }: { value: boolean | null | undefined; yes?: string; no?: string; size?: 'small' | 'default' }) {
  if (value === null || value === undefined) return <span style={{ color: '#9CA3AF' }}>—</span>;
  return <ColourTag colour={value ? '#16A34A' : '#9CA3AF'} label={value ? yes : no} size={size} />;
}
