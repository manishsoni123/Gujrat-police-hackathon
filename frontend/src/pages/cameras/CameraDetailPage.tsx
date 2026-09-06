/**
 * Camera page (/cameras/:id): StreamPlayer + metadata on the left; tabs on the
 * right — Live reads (WS), Objects (live counts + last-hour chart), Health,
 * Recordings, Events (+ Tag event), Zones.
 */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Badge, Button, Card, Descriptions, Space, Table, Tabs, Tag, Tooltip, Typography } from 'antd';
import { EditOutlined, TagOutlined, UnorderedListOutlined, WifiOutlined } from '@ant-design/icons';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts';
import { PageHeader } from '@/components/PageHeader';
import { StreamPlayer, type ActiveMode } from '@/components/StreamPlayer';
import { camerasApi, detectionsApi, eventsApi, objectsApi } from '@/api';
import type { Detection, EventItem } from '@/api/types';
import { useReadsSocket } from '@/ws/useReadsSocket';
import { useHealthSocket } from '@/ws/useHealthSocket';
import { StatusTag, MaintenanceTag, DepartmentTag, CodecTag, EventTypeTag, BoolTag, AmcTag, LocationConfidenceTag, WatchlistHitTag, codecLabel } from '@/components/Tags';
import { rowProps } from '@/hooks/useListQuery';
import { IstTime } from '@/components/IstTime';
import { PlateText } from '@/components/PlateText';
import { ConfidenceBar, CropThumb } from '@/components/Misc';
import { EmptyState, ErrorState, PageSkeleton } from '@/components/States';
import { HealthLogTable } from './HealthLogTable';
import { RecordingsTimeline } from '@/components/Recordings';
import { EventForm } from '@/components/EventForm';
import { CameraForm } from './CameraForm';
import { usePermission } from '@/store/auth';
import { cameraTypeLabel, fmtHeadingFov, fmtLatLon, fmtPct, humanise } from '@/utils/format';
import { fmtIstDate } from '@/utils/time';
import { OBJECT_CLASS_COLOURS } from '@/theme/colours';

const OBJECT_CLASSES = ['car', 'motorcycle', 'person', 'truck', 'bus', 'bicycle'];

function LiveReadsList({ reads, connected }: { reads: Detection[]; connected: boolean }) {
  const navigate = useNavigate();
  if (!reads.length) {
    return (
      <EmptyState
        compact
        title="No plate reads yet"
        description={connected ? 'Connected to the live read channel. Reads appear here within seconds of a plate being visible.' : 'Waiting for the live read channel to connect…'}
      />
    );
  }
  return (
    <Table<Detection>
      size="small"
      rowKey="id"
      pagination={false}
      dataSource={reads}
      scroll={{ y: 520 }}
      rowClassName={(r) => (r.watchlist_hit ? 'sg-row-new' : '')}
      onRow={(r) => rowProps(() => navigate(`/detections?id=${r.id}`))}
      columns={[
        { title: 'Crop', dataIndex: 'crop_url', width: 108, render: (u: string | null, r) => <CropThumb src={u} alt={`Plate crop ${r.plate_display}`} sha256={r.crop_sha256} width={90} height={36} /> },
        {
          title: 'Plate',
          dataIndex: 'plate_norm',
          render: (p: string, r) => (
            <div>
              <PlateText plate={p} raw={r.plate_raw} invalid={!r.is_valid_format} />
              {r.watchlist_hit ? <div style={{ marginTop: 3 }}><WatchlistHitTag /></div> : null}
            </div>
          ),
        },
        { title: 'Conf.', dataIndex: 'confidence', width: 130, render: (v: number) => <ConfidenceBar value={v} width={60} /> },
        { title: 'Time (IST)', dataIndex: 'captured_at', width: 130, render: (v: string) => <IstTime value={v} /> },
      ]}
    />
  );
}

function ObjectsTab({ cameraId, live }: { cameraId: number; live: Record<string, number> | null }) {
  const q = useQuery({ queryKey: ['object-counts', cameraId], queryFn: () => objectsApi.counts({ camera_id: cameraId, bucket: 'minute' }), refetchInterval: 60_000 });
  const data = useMemo(() => {
    const items = q.data?.items ?? [];
    const labels = Array.from(new Set(items.map((i) => i.label_ist)));
    return labels.map((label) => {
      const row: Record<string, string | number> = { label };
      items.filter((i) => i.label_ist === label).forEach((i) => {
        row[i.class] = i.count;
      });
      return row;
    });
  }, [q.data]);
  const classes = useMemo(() => OBJECT_CLASSES.filter((c) => (q.data?.items ?? []).some((i) => i.class === c)), [q.data]);
  return (
    <div>
      <Typography.Text strong style={{ fontSize: 13 }}>
        Current minute (live)
      </Typography.Text>
      <div className="sg-grid" style={{ gridTemplateColumns: 'repeat(6, minmax(0, 1fr))', margin: '8px 0 16px' }}>
        {OBJECT_CLASSES.map((cls) => (
          <Card key={cls} size="small" styles={{ body: { padding: '8px 10px', textAlign: 'center' } }} style={{ borderTop: `3px solid ${OBJECT_CLASS_COLOURS[cls] ?? '#9CA3AF'}` }}>
            <div style={{ fontSize: 22, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{live ? live[cls] ?? 0 : '—'}</div>
            <div style={{ fontSize: 11, color: '#6B7280', textTransform: 'capitalize' }}>{cls}</div>
          </Card>
        ))}
      </div>
      <Typography.Text strong style={{ fontSize: 13 }}>
        Last hour per minute · totals: {q.data ? Object.entries(q.data.totals).map(([k, v]) => `${k} ${v}`).join(' · ') : '…'}
      </Typography.Text>
      <div style={{ height: 220, marginTop: 8 }}>
        {data.length ? (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#EEF0F3" />
              <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={24} />
              <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
              <ChartTooltip contentStyle={{ fontSize: 12 }} />
              {classes.map((c) => (
                <Bar key={c} dataKey={c} stackId="o" fill={OBJECT_CLASS_COLOURS[c] ?? '#9CA3AF'} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <EmptyState compact title="No object counts in the last hour" description="The YOLOX detector counts persons and vehicles per minute on live ANPR cameras." />
        )}
      </div>
    </div>
  );
}

export function CameraDetailPage() {
  const { id } = useParams();
  const cameraId = Number(id);
  const navigate = useNavigate();
  const canWrite = usePermission('cameras.write');
  const canEvent = usePermission('events.write');
  const cam = useQuery({ queryKey: ['cameras', 'detail', cameraId], queryFn: () => camerasApi.get(cameraId), enabled: Number.isFinite(cameraId) });
  const seedReads = useQuery({ queryKey: ['detections', 'camera', cameraId, 'seed'], queryFn: () => detectionsApi.list({ camera_id: cameraId, page_size: 30 }), enabled: Number.isFinite(cameraId) });
  const events = useQuery({ queryKey: ['events', 'camera', cameraId], queryFn: () => eventsApi.list({ camera_id: cameraId, page_size: 50 }), enabled: Number.isFinite(cameraId) });
  const ws = useReadsSocket(Number.isFinite(cameraId) ? cameraId : null);
  const [offline, setOffline] = useState(false);
  const [mode, setMode] = useState<ActiveMode | null>(null);
  const [eventOpen, setEventOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [tab, setTab] = useState('reads');

  useHealthSocket({ onHealth: (h) => h.camera_id === cameraId && setOffline(h.status === 'offline') });
  useEffect(() => {
    if (cam.data) setOffline(cam.data.status === 'offline');
  }, [cam.data]);

  const reads = useMemo(() => {
    const seed = seedReads.data?.items ?? [];
    const ids = new Set(ws.reads.map((r) => r.id));
    return [...ws.reads, ...seed.filter((r) => !ids.has(r.id))].slice(0, 50);
  }, [ws.reads, seedReads.data]);
  const liveEvents = useMemo<EventItem[]>(() => {
    const base = events.data?.items ?? [];
    const ids = new Set(ws.events.map((e) => e.id));
    return [...ws.events, ...base.filter((e) => !ids.has(e.id))];
  }, [ws.events, events.data]);

  if (cam.isLoading) return <PageSkeleton cards={2} />;
  if (cam.isError) return <ErrorState error={cam.error} onRetry={() => void cam.refetch()} />;
  const c = cam.data;
  if (!c) return null;

  return (
    <div>
      <PageHeader
        breadcrumb={[{ label: 'Cameras', to: '/cameras' }, { label: c.name }]}
        title={c.name}
        docTitle={`${c.name} · Camera`}
        description={
          <Space size={6} wrap>
            <StatusTag status={c.status} live={c.live} size="small" />
            <MaintenanceTag status={c.maintenance_status} size="small" />
            <DepartmentTag code={c.department_code} name={c.department_name} size="small" />
            <CodecTag codec={c.codec} />
            {c.anpr_enabled ? <Tag color="blue" style={{ margin: 0 }}>ANPR live</Tag> : null}
            {c.record_enabled ? <Tag style={{ margin: 0 }}>REC 12 h</Tag> : null}
            <span style={{ color: '#6B7280' }}>
              #{c.external_id} · {c.district ?? '—'} {c.police_station ? `· ${c.police_station}` : ''}
            </span>
          </Space>
        }
        extra={
          <Space wrap>
            <Tooltip title={ws.connected ? 'Live read channel connected' : 'Live read channel disconnected'}>
              <Badge status={ws.connected ? 'processing' : 'default'} text={<span style={{ fontSize: 12, color: '#6B7280' }}><WifiOutlined /> {ws.connected ? 'reads live' : 'reads offline'}</span>} />
            </Tooltip>
            <Button icon={<UnorderedListOutlined />} onClick={() => navigate(`/cameras?open=${c.id}`)}>
              Registry entry
            </Button>
            {canEvent ? (
              <Button icon={<TagOutlined />} onClick={() => setEventOpen(true)}>
                Tag event
              </Button>
            ) : null}
            {canWrite ? (
              <Button type="primary" icon={<EditOutlined />} onClick={() => setEditOpen(true)}>
                Edit
              </Button>
            ) : null}
          </Space>
        }
      />
      <div className="sg-grid sg-camera-layout">
        <div style={{ display: 'grid', gap: 12, alignContent: 'start' }}>
          <StreamPlayer cameraId={c.id} title={c.name} offline={offline} snapshotUrl={ws.snapshot?.url ?? null} onModeChange={setMode} />
          <Card size="small" title="Camera metadata" extra={<span style={{ fontSize: 12, color: '#6B7280' }}>{mode ? `playing via ${mode === 'whep' ? 'WebRTC' : mode === 'hls' ? 'HLS' : 'snapshots'}` : 'connecting…'}</span>}>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="Relay path">
                <code>{c.relay_path ?? '—'}</code>
              </Descriptions.Item>
              <Descriptions.Item label="Stream">{codecLabel(c.codec)} · {c.resolution ?? '—'} · {c.fps ?? '—'} fps</Descriptions.Item>
              <Descriptions.Item label="Type / ownership">{cameraTypeLabel(c.type)} · {humanise(c.ownership)}</Descriptions.Item>
              <Descriptions.Item label="Source">{humanise(c.source)}</Descriptions.Item>
              <Descriptions.Item label="Address" span={2}>{c.address ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="Coordinates">{fmtLatLon(c.lat, c.lon)}</Descriptions.Item>
              <Descriptions.Item label="Heading / FoV">{fmtHeadingFov(c.heading_deg, c.fov_deg)}</Descriptions.Item>
              {c.location_confidence || c.metadata?.enrichment ? (
                <Descriptions.Item label="Location confidence" span={2}>
                  <LocationConfidenceTag confidence={c.location_confidence} />
                  {c.metadata?.enrichment?.source ? <span style={{ marginLeft: 8, fontSize: 12, color: '#6B7280' }}>source: {c.metadata.enrichment.source}</span> : null}
                  {c.metadata?.enrichment?.notes ? <div style={{ marginTop: 4, fontSize: 12, color: '#4B5563' }}>{c.metadata.enrichment.notes}</div> : null}
                </Descriptions.Item>
              ) : null}
              <Descriptions.Item label="Connectivity">{c.connectivity_type ? c.connectivity_type.toUpperCase() : '—'}{c.bandwidth_kbps ? ` · ${c.bandwidth_kbps} kbps` : ''}</Descriptions.Item>
              <Descriptions.Item label="Storage">{c.storage_location ?? '—'}{c.retention_days != null ? ` · ${c.retention_days} d` : ''}</Descriptions.Item>
              <Descriptions.Item label="Vendor / model">{c.vendor ?? '—'}{c.model ? ` · ${c.model}` : ''}</Descriptions.Item>
              <Descriptions.Item label="Installed">{fmtIstDate(c.install_date)}{c.age_years != null ? ` (${c.age_years.toFixed(1)} y)` : ''}</Descriptions.Item>
              <Descriptions.Item label="AMC"><AmcTag status={c.amc_status} size="small" /></Descriptions.Item>
              <Descriptions.Item label="Catalogue live"><BoolTag value={c.live} yes="Yes" no="No (not streaming)" /></Descriptions.Item>
              <Descriptions.Item label="Uptime 24 h">{fmtPct(c.uptime_24h_pct)}</Descriptions.Item>
              <Descriptions.Item label="Last seen"><IstTime value={c.last_seen_at} /></Descriptions.Item>
              <Descriptions.Item label="Reads / sightings 24 h">{c.reads_24h} / {c.sightings_24h}</Descriptions.Item>
              <Descriptions.Item label="Open alerts">{c.recent_alerts}</Descriptions.Item>
            </Descriptions>
          </Card>
        </div>
        <Card size="small" styles={{ body: { paddingTop: 4 } }}>
          <Tabs
            activeKey={tab}
            onChange={setTab}
            items={[
              { key: 'reads', label: <span>Live reads <Badge count={reads.length} size="small" color="#1E4DB7" overflowCount={50} style={{ marginLeft: 4 }} /></span>, children: <LiveReadsList reads={reads} connected={ws.connected} /> },
              { key: 'objects', label: 'Objects', children: <ObjectsTab cameraId={c.id} live={ws.counts?.counts ?? null} /> },
              { key: 'health', label: 'Health', children: <HealthLogTable cameraId={c.id} /> },
              { key: 'recordings', label: 'Recordings', children: <RecordingsTimeline cameraId={c.id} cameraName={c.name} /> },
              {
                key: 'events',
                label: 'Events',
                children: liveEvents.length ? (
                  <Table<EventItem>
                    size="small"
                    rowKey="id"
                    pagination={{ pageSize: 10, size: 'small' }}
                    dataSource={liveEvents}
                    columns={[
                      { title: 'Type', dataIndex: 'type', width: 150, render: (t: string) => <EventTypeTag type={t} size="small" /> },
                      { title: 'Occurred (IST)', dataIndex: 'occurred_at', width: 140, render: (v: string) => <IstTime value={v} /> },
                      { title: 'Note', dataIndex: 'note', ellipsis: true, render: (v: string | null) => v ?? <span style={{ color: '#9CA3AF' }}>—</span> },
                      { title: 'By', dataIndex: 'created_by_username', width: 110, render: (u: string | null, r) => (r.is_auto ? <Tag style={{ margin: 0 }}>auto</Tag> : u) },
                    ]}
                  />
                ) : (
                  <EmptyState compact title="No events on this camera" description="Manual tags and automatic events (watchlist hit, loop reset, offline) are listed here." actions={canEvent ? <Button onClick={() => setEventOpen(true)}>Tag event</Button> : undefined} />
                ),
              },
              {
                key: 'zones',
                label: 'Zones',
                children: c.zones.length ? (
                  <Table
                    size="small"
                    rowKey="id"
                    pagination={false}
                    dataSource={c.zones}
                    columns={[
                      { title: 'Name', dataIndex: 'name' },
                      { title: 'Active hours (IST)', render: (_v, z) => (z.active_from ? `${z.active_from} → ${z.active_to}` : 'always') },
                      { title: 'Classes', dataIndex: 'classes', render: (v: string[]) => (v.length ? v.join(', ') : 'all') },
                      { title: 'Dwell', dataIndex: 'dwell_s', width: 80, render: (v: number) => `${v} s` },
                      { title: 'Priority', dataIndex: 'priority', width: 90, render: (v: string) => humanise(v) },
                      { title: 'Active', dataIndex: 'is_active', width: 80, render: (v: boolean) => <BoolTag value={v} /> },
                    ]}
                  />
                ) : (
                  <EmptyState compact title="No intrusion zones" description="Zones are polygons on the frame; a person or vehicle inside one for longer than the dwell time raises an intrusion alert (P2 feature, worker-ready)." />
                ),
              },
            ]}
          />
        </Card>
      </div>
      <EventForm open={eventOpen} onClose={() => setEventOpen(false)} cameraId={c.id} />
      <CameraForm open={editOpen} onClose={() => setEditOpen(false)} camera={c} />
    </div>
  );
}
