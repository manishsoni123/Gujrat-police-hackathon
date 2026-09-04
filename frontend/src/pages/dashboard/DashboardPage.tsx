/** Dashboard: KPI tiles (live via /ws/health stats), charts, recent alerts strip, mini map. */
import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, List, Segmented, Space, Typography } from 'antd';
import {
  AlertOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  EyeOutlined,
  ExclamationCircleOutlined,
  FlagOutlined,
  ThunderboltOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts';
import { useState } from 'react';
import { PageHeader } from '@/components/PageHeader';
import { KpiTile } from '@/components/KpiTile';
import { ErrorState, EmptyState, PageSkeleton } from '@/components/States';
import { alertsApi, dashboardApi, geoApi, healthApi } from '@/api';
import { useUiStore } from '@/store/ui';
import { fmtBytes, fmtLatency, fmtPct, fmtPlace } from '@/utils/format';
import { hoursAgoIso } from '@/utils/time';
import { PriorityTag, AlertStatusTag, ConfidenceLevelTag } from '@/components/Tags';
import { PlateText } from '@/components/PlateText';
import { IstTime } from '@/components/IstTime';
import { BaseMap, CameraMarkers, FitBounds, type MapCamera } from '@/components/MapView';
import { CHART_SERIES, OBJECT_CLASS_COLOURS } from '@/theme/colours';
import type { DashboardCharts } from '@/api/types';

function pivotByCamera(rows: { camera_name: string; label_ist: string; sightings?: number; alerts?: number }[], valueKey: 'sightings' | 'alerts') {
  const labels = Array.from(new Set(rows.map((r) => r.label_ist)));
  const cams = Array.from(new Set(rows.map((r) => r.camera_name))).slice(0, 8);
  const data = labels.map((label) => {
    const row: Record<string, string | number> = { label };
    cams.forEach((c) => {
      row[c] = 0;
    });
    rows.filter((r) => r.label_ist === label && cams.includes(r.camera_name)).forEach((r) => {
      row[r.camera_name] = (row[r.camera_name] as number) + (r[valueKey] ?? 0);
    });
    return row;
  });
  return { data, cams };
}

function pivotObjects(rows: DashboardCharts['object_counts']) {
  const labels = Array.from(new Set(rows.map((r) => r.label_ist)));
  const classes = Array.from(new Set(rows.map((r) => r.class)));
  const data = labels.map((label) => {
    const row: Record<string, string | number> = { label };
    classes.forEach((c) => {
      row[c] = rows.filter((r) => r.label_ist === label && r.class === c).reduce((s, r) => s + r.count, 0);
    });
    return row;
  });
  return { data, classes };
}

export function DashboardPage() {
  const navigate = useNavigate();
  const [range, setRange] = useState<'24h' | '7d'>('24h');
  const live = useUiStore((s) => s.healthStats);
  const stats = useQuery({ queryKey: ['dashboard', 'stats'], queryFn: dashboardApi.stats, refetchInterval: 30_000 });
  // Seeds the uptime tile until the first `stats` frame arrives on /ws/health (every 10 s).
  const health = useQuery({ queryKey: ['health', 'summary', 'dashboard'], queryFn: healthApi.summary, staleTime: 60_000 });
  const charts = useQuery({
    queryKey: ['dashboard', 'charts', range],
    queryFn: () => dashboardApi.charts({ from: hoursAgoIso(range === '24h' ? 24 : 24 * 7), bucket: range === '24h' ? 'hour' : 'day' }),
    refetchInterval: 60_000,
  });
  const alerts = useQuery({ queryKey: ['alerts', 'list', 'dashboard'], queryFn: () => alertsApi.list({ page_size: 6, status: 'new,acknowledged' }), refetchInterval: 30_000 });
  const geo = useQuery({ queryKey: ['geo', 'cameras'], queryFn: () => geoApi.cameras(), staleTime: 60_000 });

  const cams = useMemo<MapCamera[]>(
    () =>
      (geo.data?.features ?? []).map((f) => {
        const [lon, lat] = f.geometry.coordinates as [number, number];
        return { id: f.properties.id, name: f.properties.name, lat, lon, status: f.properties.status, department_code: f.properties.department_code, maintenance_status: f.properties.maintenance_status, anpr_enabled: f.properties.anpr_enabled };
      }),
    [geo.data],
  );
  const points = useMemo(() => cams.map((c) => [c.lat, c.lon] as [number, number]), [cams]);

  const perHour = useMemo(() => pivotByCamera(charts.data?.vehicles_per_hour ?? [], 'sightings'), [charts.data]);
  const alertsPer = useMemo(() => pivotByCamera(charts.data?.alerts_per_camera_day ?? [], 'alerts'), [charts.data]);
  const objects = useMemo(() => pivotObjects(charts.data?.object_counts ?? []), [charts.data]);

  if (stats.isLoading && !stats.data) return <PageSkeleton cards={4} />;
  if (stats.isError && !stats.data) return <ErrorState error={stats.error} onRetry={() => void stats.refetch()} />;

  const s = stats.data;
  const cameras = live?.cameras ?? s?.cameras;
  const uptime = live?.uptime_24h_pct ?? health.data?.uptime_24h_pct ?? null;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        description="Live picture of the camera estate, ANPR throughput and open alerts. Tiles update in real time from the health channel."
        extra={
          <Segmented options={[{ label: 'Last 24 h', value: '24h' }, { label: 'Last 7 days', value: '7d' }]} value={range} onChange={(v) => setRange(v as '24h' | '7d')} />
        }
      />
      <div className="sg-grid sg-grid-6" style={{ marginBottom: 12 }}>
        <KpiTile label="Cameras" value={cameras?.total} icon={<VideoCameraOutlined />} colour="#1E4DB7" footer={`${s?.cameras.anpr_live ?? 0} with live ANPR`} onClick={() => navigate('/cameras')} />
        <KpiTile label="Online" value={cameras?.online} icon={<CheckCircleOutlined />} colour="#16A34A" footer={`${cameras?.degraded ?? 0} degraded`} onClick={() => navigate('/cameras?status=online')} />
        <KpiTile label="Offline" value={cameras?.offline} icon={<CloseCircleOutlined />} colour="#DC2626" footer={`${cameras?.unknown ?? 0} unknown`} onClick={() => navigate('/health')} />
        <KpiTile
          label="Uptime 24 h"
          value={uptime === null ? (s?.cameras ? '—' : null) : fmtPct(uptime)}
          icon={<ThunderboltOutlined />}
          colour="#0EA5E9"
          footer={cameras ? `${cameras.offline} of ${cameras.total} cameras offline` : 'share of ready health checks'}
          hint="Ready health checks ÷ all checks over the last 24 h across every registered camera. Cameras the catalogue marks live=false count as offline, which is why the mock sandbox shows a low figure."
          onClick={() => navigate('/health')}
        />
        <KpiTile label="Reads 24 h" value={s?.reads.last_24h} icon={<EyeOutlined />} colour="#7C3AED" footer={`${live ? `${live.reads_last_min} reads/min now · ` : ''}${s?.sightings.last_24h ?? 0} sightings · ${fmtPct(s?.sightings.valid_format_pct_24h ?? null, 0)} valid`} onClick={() => navigate('/detections')} />
        <KpiTile label="Open alerts" value={(s?.alerts.new ?? 0) + (s?.alerts.acknowledged ?? 0)} icon={<AlertOutlined />} colour={s?.alerts.critical_open ? '#DC2626' : '#D97706'} footer={`${s?.alerts.critical_open ?? 0} critical · avg latency ${fmtLatency(s?.alerts.avg_latency_ms_24h)}`} onClick={() => navigate('/alerts')} />
      </div>

      <div className="sg-grid sg-grid-3" style={{ marginBottom: 12 }}>
        <Card title="Reads per hour by camera" size="small" styles={{ body: { height: 260 } }}>
          {perHour.data.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={perHour.data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#EEF0F3" />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={24} />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <ChartTooltip contentStyle={{ fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {perHour.cams.map((c, i) => (
                  <Line key={c} type="monotone" dataKey={c} stroke={CHART_SERIES[i % CHART_SERIES.length]} dot={false} strokeWidth={2} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState compact title="No sightings in this window" description="ANPR workers post reads within seconds of a plate being visible." />
          )}
        </Card>
        <Card title="Alerts per camera" size="small" styles={{ body: { height: 260 } }}>
          {alertsPer.data.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={alertsPer.data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#EEF0F3" />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <ChartTooltip contentStyle={{ fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {alertsPer.cams.map((c, i) => (
                  <Bar key={c} dataKey={c} stackId="a" fill={CHART_SERIES[i % CHART_SERIES.length]} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState compact title="No alerts in this window" description="Watchlist hits and camera-offline alerts are counted per camera per day." />
          )}
        </Card>
        <Card title="Object counts (vehicles / persons)" size="small" styles={{ body: { height: 260 } }}>
          {objects.data.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={objects.data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#EEF0F3" />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={24} />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <ChartTooltip contentStyle={{ fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {objects.classes.map((c) => (
                  <Bar key={c} dataKey={c} stackId="o" fill={OBJECT_CLASS_COLOURS[c] ?? '#9CA3AF'} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState compact title="No object counts yet" description="The YOLOX detector counts persons and vehicles per camera per minute on live cameras." />
          )}
        </Card>
      </div>

      <div className="sg-grid" style={{ gridTemplateColumns: 'minmax(0, 1.2fr) minmax(0, 1fr) minmax(0, 0.8fr)' }}>
        <Card
          title="Recent alerts"
          size="small"
          extra={
            <Button type="link" size="small" onClick={() => navigate('/alerts')} style={{ padding: 0 }}>
              Open panel
            </Button>
          }
          styles={{ body: { padding: 0 } }}
        >
          {alerts.data && alerts.data.items.length === 0 ? (
            <EmptyState compact title="No open alerts" description="New watchlist hits appear here in real time." />
          ) : (
            <List
              loading={alerts.isLoading}
              dataSource={alerts.data?.items ?? []}
              renderItem={(a) => (
                <List.Item onClick={() => navigate(`/alerts?id=${a.id}`)} style={{ cursor: 'pointer', padding: '10px 16px' }}>
                  <div style={{ display: 'flex', gap: 12, width: '100%', alignItems: 'center' }}>
                    {a.snapshot_url ? <img src={a.snapshot_url} alt="" style={{ width: 64, height: 44, objectFit: 'contain', background: '#111827', borderRadius: 4, border: '1px solid #E5E7EB' }} /> : null}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <Space size={6} wrap>
                        <PriorityTag priority={a.priority} size="small" />
                        <AlertStatusTag status={a.status} size="small" />
                        <ConfidenceLevelTag level={a.confidence_level} size="small" />
                        {a.plate_norm ? <PlateText plate={a.plate_norm} size="small" /> : <span style={{ fontWeight: 500 }}>{a.type === 'camera_offline' ? 'Camera offline' : a.type}</span>}
                      </Space>
                      <div style={{ fontSize: 12, color: '#6B7280', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {a.watchlist?.name ? `${a.watchlist.name} · ` : ''}
                        {a.camera.name} · {fmtPlace(a.camera.district, a.camera.police_station)}
                      </div>
                    </div>
                    <IstTime value={a.created_at} mode="relative" muted />
                  </div>
                </List.Item>
              )}
            />
          )}
        </Card>
        <Card title="Top plates" size="small" styles={{ body: { padding: 0 } }}>
          {charts.data?.top_plates.length ? (
            <List
              size="small"
              dataSource={charts.data.top_plates.slice(0, 8)}
              renderItem={(p, i) => (
                <List.Item onClick={() => navigate(`/vehicles?q=${p.plate_norm}`)} style={{ cursor: 'pointer', padding: '8px 16px' }}>
                  <Space>
                    <span style={{ color: '#9CA3AF', width: 18, display: 'inline-block' }}>{i + 1}.</span>
                    <PlateText plate={p.plate_norm} size="small" />
                  </Space>
                  <span style={{ fontSize: 12, color: '#6B7280' }}>
                    {p.sightings} sightings · {p.cameras} cameras
                  </span>
                </List.Item>
              )}
            />
          ) : (
            <EmptyState compact title="No plates ranked yet" description="Plates seen most often in the window appear here." />
          )}
        </Card>
        <Card title="Estate map" size="small" styles={{ body: { padding: 8 } }} extra={<Button type="link" size="small" style={{ padding: 0 }} onClick={() => navigate('/map')}>Full map</Button>}>
          <BaseMap height={300} scrollWheelZoom={false}>
            <CameraMarkers cameras={cams} cluster onClick={(c) => navigate(`/cameras/${c.id}`)} />
            {points.length ? <FitBounds points={points} /> : null}
          </BaseMap>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#6B7280', padding: '8px 4px 0' }}>
            <span>
              <FlagOutlined /> Watchlist: {s?.watchlist.active ?? 0} active
            </span>
            <span>
              <ExclamationCircleOutlined /> Disk free {fmtBytes(s?.disk.data_free_bytes)}
            </span>
          </div>
        </Card>
      </div>
      <Typography.Text type="secondary" style={{ display: 'block', marginTop: 10, fontSize: 12 }}>
        Buckets are computed in IST. Reads = accepted (voted) plate reads; sightings = one vehicle passing one camera.
      </Typography.Text>
    </div>
  );
}
