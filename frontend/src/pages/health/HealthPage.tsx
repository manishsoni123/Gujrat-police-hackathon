/** Health (/health): status tiles, relay/disk/ANPR worker cards, down > 5 min, AMC expiring, maintenance, per-camera uptime. */
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, Progress, Space, Table, Tooltip, Typography } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, ClusterOutlined, DatabaseOutlined, ExclamationCircleOutlined, PauseCircleOutlined, QuestionCircleOutlined, ReloadOutlined, ThunderboltOutlined, VideoCameraOutlined } from '@ant-design/icons';
import { PageHeader } from '@/components/PageHeader';
import { KpiTile } from '@/components/KpiTile';
import { camerasApi, healthApi } from '@/api';
import type { AnprWorker, Camera } from '@/api/types';
import { rowProps } from '@/hooks/useListQuery';
import { notStreaming, splitNotStreaming } from '@/utils/cameraState';
import { CATALOGUE_NOT_STREAMING } from '@/theme/colours';
import { useUiStore } from '@/store/ui';
import { EmptyState, ErrorState, PageSkeleton } from '@/components/States';
import { ColourTag, StatusTag, MaintenanceTag, DepartmentTag } from '@/components/Tags';
import { IstTime } from '@/components/IstTime';
import { fmtBytes, fmtPct } from '@/utils/format';
import { fmtIstDate, fmtMinutes } from '@/utils/time';

/** Plate-detector badge from the worker heartbeat (CONTRACT §7.7): amber when the contour fallback is active. */
function DetectorTag({ detector }: { detector: string | null | undefined }) {
  if (!detector) return null;
  if (detector === 'contour') {
    return <ColourTag colour="#D97706" label="Contour fallback" size="small" title="Plate detector: bright-quadrilateral contour finder. Reads the synthetic plates on this CPU-only laptop but is a weak fallback on real footage; the ONNX detector (YOLO-v9-t) is the production path." />;
  }
  if (detector === 'onnx') return <ColourTag colour="#16A34A" label="ONNX detector" size="small" title="Plate detector: YOLO-v9-t ONNX model (open-image-models)" />;
  if (detector === 'auto') return <ColourTag colour="#1E4DB7" label="ONNX + contour fallback" size="small" title="Plate detector: YOLO-v9-t ONNX first; the contour finder only on frames where ONNX returns no box" />;
  return <ColourTag colour="#6B7280" label={detector} size="small" title="Plate detector reported by the worker" />;
}

function workerLabel(w: AnprWorker): string {
  const kind = w.mode === 'live' ? 'Live ANPR worker' : 'Pre-index worker';
  return `${kind} (${w.gpu ? 'GPU' : 'CPU'})`;
}

export function HealthPage() {
  const navigate = useNavigate();
  const live = useUiStore((s) => s.healthStats);
  const summary = useQuery({ queryKey: ['health', 'summary'], queryFn: healthApi.summary, refetchInterval: 30_000 });
  const cams = useQuery({ queryKey: ['cameras', 'uptime'], queryFn: () => camerasApi.list({ page_size: 200, sort: 'last_seen_at', order: 'desc' }), refetchInterval: 60_000 });
  const [uptimeFilter, setUptimeFilter] = useState<'all' | 'below90'>('all');

  const camRows = useMemo(() => cams.data?.items ?? [], [cams.data]);
  /** Ids of cameras the catalogue marks live=false: never pulled, so they are "not streaming", not down. */
  const notStreamingIds = useMemo(() => new Set(notStreaming(camRows).map((c) => c.id)), [camRows]);
  const uptimeRows = useMemo(() => {
    const rows = camRows.filter((c) => c.status !== 'retired');
    const filtered = uptimeFilter === 'below90' ? rows.filter((c) => !notStreamingIds.has(c.id) && (c.uptime_24h_pct ?? 0) < 90) : rows;
    // Streaming cameras first (worst uptime on top); not-streaming catalogue cameras at the end.
    return filtered.sort((a, b) => Number(notStreamingIds.has(a.id)) - Number(notStreamingIds.has(b.id)) || (a.uptime_24h_pct ?? -1) - (b.uptime_24h_pct ?? -1));
  }, [camRows, uptimeFilter, notStreamingIds]);

  if (summary.isLoading && !summary.data) return <PageSkeleton cards={4} />;
  if (summary.isError && !summary.data) return <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />;
  const h = summary.data;
  if (!h) return null;
  const cameras = splitNotStreaming(live?.cameras ?? h.cameras, camRows) ?? { ...h.cameras, not_streaming: 0 };
  const uptime = live?.uptime_24h_pct ?? h.uptime_24h_pct;
  const diskTotal = h.disk.data_used_bytes + h.disk.data_free_bytes;
  const down = h.down_over_5min.filter((d) => !notStreamingIds.has(d.id));
  const downExcluded = h.down_over_5min.length - down.length;
  const notStreamingRows = h.not_streaming ?? notStreaming(camRows).map((c) => ({ id: c.id, name: c.name, district: c.district, since: c.last_status_change_at }));

  return (
    <div>
      <PageHeader
        title="Health"
        description={
          <span>
            Poller checks every camera each minute through the relay (ready flag, video track, byte delta) and probes idle paths. Last check <IstTime value={h.checked_at} mode="relative" />.
          </span>
        }
        extra={
          <Button icon={<ReloadOutlined />} loading={summary.isFetching} onClick={() => void summary.refetch()}>
            Refresh
          </Button>
        }
      />
      <div className="sg-grid sg-grid-7" style={{ marginBottom: 12 }}>
        <KpiTile label="Cameras" value={cameras.total} icon={<VideoCameraOutlined />} colour="#1E4DB7" footer={`${h.anpr_live_cameras} with live ANPR`} onClick={() => navigate('/cameras')} />
        <KpiTile label="Online" value={cameras.online} icon={<CheckCircleOutlined />} colour="#16A34A" onClick={() => navigate('/cameras?status=online')} />
        <KpiTile label="Degraded" value={cameras.degraded} icon={<ExclamationCircleOutlined />} colour="#D97706" onClick={() => navigate('/cameras?status=degraded')} />
        <KpiTile label="Offline" value={cameras.offline} icon={<CloseCircleOutlined />} colour="#DC2626" hint="Should stream but failed three health checks in a row" onClick={() => navigate('/cameras?status=offline')} />
        <KpiTile label="Not streaming" value={cameras.not_streaming} icon={<PauseCircleOutlined />} colour={CATALOGUE_NOT_STREAMING.colour} footer="catalogue live = false" hint={CATALOGUE_NOT_STREAMING.hint} onClick={() => navigate('/cameras?status=not_streaming')} />
        <KpiTile label="Unknown" value={cameras.unknown} icon={<QuestionCircleOutlined />} colour="#9CA3AF" hint="Never checked - typically cameras without a stream URL" onClick={() => navigate('/cameras?status=unknown')} />
        <KpiTile label="Uptime 24 h" value={uptime === null ? '—' : fmtPct(uptime)} icon={<ThunderboltOutlined />} colour="#0EA5E9" footer="ready checks / all checks" hint="Includes the not-streaming catalogue cameras, which are checked but never ready; on the mock sandbox the figure is low by design." />
      </div>

      <div className="sg-grid sg-grid-3" style={{ marginBottom: 12 }}>
        <Card size="small" title={<Space><ClusterOutlined /> Relay (MediaMTX)</Space>}>
          <div style={{ display: 'flex', gap: 24 }}>
            <div>
              <div style={{ fontSize: 12, color: '#6B7280' }}>State</div>
              <div style={{ fontWeight: 600, color: h.mediamtx.ok ? '#16A34A' : '#DC2626' }}>{h.mediamtx.ok ? 'reachable' : 'unreachable'}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: '#6B7280' }}>Configured paths</div>
              <div style={{ fontWeight: 600 }}>{h.mediamtx.paths}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: '#6B7280' }}>Ready now</div>
              <div style={{ fontWeight: 600 }}>{h.mediamtx.ready}</div>
            </div>
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
            On-demand paths report not-ready while nobody reads them; the poller probes those actively, so idle cameras still count as online.
          </Typography.Text>
        </Card>
        <Card size="small" title={<Space><DatabaseOutlined /> Storage</Space>}>
          <Progress percent={Math.round((h.disk.data_used_bytes / Math.max(1, diskTotal)) * 100)} size="small" strokeColor="#1E4DB7" />
          <div style={{ fontSize: 12, color: '#4B5563', marginTop: 4 }}>
            Data volume {fmtBytes(h.disk.data_used_bytes)} used · {fmtBytes(h.disk.data_free_bytes)} free
          </div>
          <div style={{ fontSize: 12, color: '#4B5563', marginTop: 2 }}>Recordings {fmtBytes(h.disk.recordings_used_bytes)} (12 h rolling, deleted by the relay)</div>
        </Card>
        <Card size="small" title="ANPR workers">
          {h.anpr_workers.length ? (
            <div style={{ display: 'grid', gap: 6 }}>
              {h.anpr_workers.map((w) => (
                <div key={w.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, fontSize: 12, border: '1px solid #E5E7EB', borderRadius: 6, padding: '6px 10px', flexWrap: 'wrap' }}>
                  <span>
                    <Tooltip title={`Worker id ${w.id}${w.version ? ` · version ${w.version}` : ''}`}>
                      <strong>{workerLabel(w)}</strong>
                    </Tooltip>{' '}
                    · {w.cameras} camera{w.cameras === 1 ? '' : 's'} · {w.fps_total.toFixed(1)} fps
                  </span>
                  <Space size={6} wrap>
                    <DetectorTag detector={w.detector} />
                    <ColourTag colour={w.stale ? '#DC2626' : '#16A34A'} label={w.stale ? 'Not responding' : 'Running'} size="small" dot pulse={!w.stale} title={w.stale ? 'No heartbeat for more than 45 s' : 'Heartbeat received within the last 45 s'} />
                    <IstTime value={w.last_heartbeat_at} mode="relative" muted />
                  </Space>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState compact title="No worker heartbeats" description="Workers post a heartbeat every 15 s once they start." />
          )}
        </Card>
      </div>

      <div className="sg-grid sg-grid-4" style={{ marginBottom: 12 }}>
        <Card size="small" title={`Down for more than 5 minutes (${down.length})`} extra={downExcluded ? <Tooltip title={CATALOGUE_NOT_STREAMING.hint}><span style={{ fontSize: 12, color: CATALOGUE_NOT_STREAMING.colour }}>{downExcluded} not streaming (catalogue) not listed</span></Tooltip> : null} styles={{ body: { padding: 0 } }}>
          {down.length ? (
            <Table
              size="small"
              rowKey="id"
              pagination={down.length > 8 ? { pageSize: 8, size: 'small', showSizeChanger: false } : false}
              dataSource={down}
              onRow={(r) => rowProps(() => navigate(`/cameras/${r.id}`))}
              columns={[
                { title: 'Camera', dataIndex: 'name', ellipsis: true, render: (v: string, r) => <span><strong>{v}</strong><div style={{ fontSize: 11, color: '#6B7280' }}>{r.department_name} · {r.district ?? '—'}</div></span> },
                { title: 'Since', dataIndex: 'offline_since', width: 120, render: (v: string) => <IstTime value={v} /> },
                { title: 'Down', dataIndex: 'minutes', width: 90, align: 'right', render: (v: number) => <span style={{ color: '#DC2626', fontWeight: 500 }}>{fmtMinutes(v)}</span> },
              ]}
            />
          ) : (
            <EmptyState compact title="No camera has been down for more than 5 minutes" description="Offline transitions raise a low-priority alert in the alert channel." />
          )}
        </Card>
        <Card size="small" title={`AMC expiring within 30 days (${h.amc_expiring_30d.length})`} styles={{ body: { padding: 0 } }}>
          {h.amc_expiring_30d.length ? (
            <Table
              size="small"
              rowKey="id"
              pagination={h.amc_expiring_30d.length > 8 ? { pageSize: 8, size: 'small', showSizeChanger: false } : false}
              dataSource={h.amc_expiring_30d}
              onRow={(r) => rowProps(() => navigate(`/cameras?open=${r.id}`))}
              columns={[
                { title: 'Camera', dataIndex: 'name', ellipsis: true, render: (v: string, r) => <span><strong>{v}</strong><div style={{ fontSize: 11, color: '#6B7280' }}>{r.amc_vendor ?? 'vendor not recorded'}</div></span> },
                { title: 'Expiry', dataIndex: 'amc_expiry', width: 110, render: (v: string) => fmtIstDate(v) },
                { title: 'Days', dataIndex: 'days_left', width: 70, align: 'right', render: (v: number) => <span style={{ color: v <= 7 ? '#DC2626' : '#D97706', fontWeight: 500 }}>{v}</span> },
              ]}
            />
          ) : (
            <EmptyState compact title="No AMC contracts expire in the next 30 days" description="AMC expiry is recorded per camera under Maintenance." />
          )}
        </Card>
        <Card size="small" title={`Under maintenance / faulty (${h.maintenance.length})`} styles={{ body: { padding: 0 } }}>
          {h.maintenance.length ? (
            <Table
              size="small"
              rowKey="id"
              pagination={h.maintenance.length > 8 ? { pageSize: 8, size: 'small', showSizeChanger: false } : false}
              dataSource={h.maintenance}
              onRow={(r) => rowProps(() => navigate(`/cameras?open=${r.id}`))}
              columns={[
                { title: 'Camera', dataIndex: 'name', ellipsis: true, render: (v: string) => <strong>{v}</strong> },
                { title: 'Status', dataIndex: 'maintenance_status', width: 150, render: (v: string) => <MaintenanceTag status={v} size="small" /> },
                { title: 'Since', dataIndex: 'since', width: 120, render: (v: string | null) => <IstTime value={v} /> },
              ]}
            />
          ) : (
            <EmptyState compact title="No cameras flagged for maintenance" description="Maintenance status is set from the camera drawer." />
          )}
        </Card>
        <Card size="small" title={<Tooltip title={CATALOGUE_NOT_STREAMING.hint}><span style={{ color: CATALOGUE_NOT_STREAMING.colour }}>Not streaming (catalogue) ({notStreamingRows.length})</span></Tooltip>} styles={{ body: { padding: 0 } }}>
          {notStreamingRows.length ? (
            <Table
              size="small"
              rowKey="id"
              pagination={notStreamingRows.length > 8 ? { pageSize: 8, size: 'small', showSizeChanger: false } : false}
              dataSource={notStreamingRows}
              onRow={(r) => rowProps(() => navigate(`/cameras?open=${r.id}`))}
              columns={[
                { title: 'Camera', dataIndex: 'name', ellipsis: true, render: (v: string, r) => <span><strong>{v}</strong><div style={{ fontSize: 11, color: '#6B7280' }}>{r.district ?? '—'}</div></span> },
                { title: 'Since', dataIndex: 'since', width: 120, render: (v: string | null) => <IstTime value={v} /> },
                { title: '', width: 110, render: () => <StatusTag status="not_streaming" size="small" short /> },
              ]}
            />
          ) : (
            <EmptyState compact title="Every registered camera has delivered a stream" description="Cameras the catalogue marks live=false would be listed here, in grey, without raising offline alerts." />
          )}
        </Card>
      </div>

      <Card
        size="small"
        title="Per-camera uptime (24 h)"
        styles={{ body: { padding: 0 } }}
        extra={
          <Space>
            <Button size="small" type={uptimeFilter === 'all' ? 'primary' : 'default'} onClick={() => setUptimeFilter('all')}>
              All
            </Button>
            <Button size="small" type={uptimeFilter === 'below90' ? 'primary' : 'default'} onClick={() => setUptimeFilter('below90')}>
              Below 90 %
            </Button>
          </Space>
        }
      >
        <Table<Camera>
          className="sg-table"
          size="small"
          rowKey="id"
          loading={cams.isLoading}
          dataSource={uptimeRows}
          pagination={{ pageSize: 15, size: 'small', showSizeChanger: false, showTotal: (t) => `${t} cameras` }}
          onRow={(r) => rowProps(() => navigate(`/cameras/${r.id}`))}
          columns={[
            { title: 'Camera', dataIndex: 'name', ellipsis: true, render: (v: string) => <strong>{v}</strong> },
            { title: 'Department', dataIndex: 'department_code', width: 120, render: (v: string, r) => <DepartmentTag code={v} name={r.department_name} size="small" /> },
            { title: 'District', dataIndex: 'district', width: 140, render: (v: string | null) => v ?? '—' },
            { title: 'Status', dataIndex: 'status', width: 170, render: (v: string, r) => <StatusTag status={v} live={r.live} size="small" /> },
            {
              title: 'Uptime 24 h',
              dataIndex: 'uptime_24h_pct',
              width: 220,
              render: (v: number | null, r) =>
                notStreamingIds.has(r.id) ? (
                  <Tooltip title={CATALOGUE_NOT_STREAMING.hint}><span style={{ color: CATALOGUE_NOT_STREAMING.colour }}>not streaming (catalogue)</span></Tooltip>
                ) : v === null ? (
                  <span style={{ color: '#9CA3AF' }}>never checked</span>
                ) : (
                  <Progress percent={Math.round(v)} size="small" strokeColor={v >= 90 ? '#16A34A' : v >= 50 ? '#D97706' : '#DC2626'} format={(p) => `${p} %`} />
                ),
            },
            { title: 'Last seen', dataIndex: 'last_seen_at', width: 150, render: (v: string | null) => <IstTime value={v} /> },
            { title: 'Status since', dataIndex: 'last_status_change_at', width: 150, render: (v: string | null) => <IstTime value={v} /> },
          ]}
        />
      </Card>
    </div>
  );
}
