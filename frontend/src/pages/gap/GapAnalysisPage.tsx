/** Gap analysis (/gap-analysis): parameter form, summary tiles, section tabs, map overlays, ageing table, recommendations, CSV/PDF export. */
import { useCallback, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, Form, InputNumber, List, Select, Space, Table, Tabs, Tag, Tooltip, Typography } from 'antd';
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons';
import L from 'leaflet';
import { GeoJSON } from '@/components/leaflet';
import { PageHeader } from '@/components/PageHeader';
import { rowProps } from '@/hooks/useListQuery';
import { KpiTile } from '@/components/KpiTile';
import { gapApi, geoApi } from '@/api';
import type { GapAnalysis, GeoFeatureCollection } from '@/api/types';
import { EmptyState, ErrorState, PageSkeleton } from '@/components/States';
import { BaseMap, CameraMarkers, FitBounds, escapeHtml, type MapCamera } from '@/components/MapView';
import { ExportDialog } from '@/components/Dialogs';
import { DepartmentTag, MaintenanceTag, AmcTag } from '@/components/Tags';
import { IstTime } from '@/components/IstTime';
import { usePermission } from '@/store/auth';
import { useDistricts } from '@/hooks/useCamerasOptions';
import { cameraTypeLabel, fmtMetres, fmtPct } from '@/utils/format';
import { fmtIstDate } from '@/utils/time';

interface Params {
  coverage_radius_m: number;
  poi_radius_m: number;
  grid_m: number;
  ageing_years: number;
  district?: string;
}

export function GapAnalysisPage() {
  const navigate = useNavigate();
  const canExport = usePermission('cameras.export');
  const districts = useDistricts();
  const [params, setParams] = useState<Params>({ coverage_radius_m: 150, poi_radius_m: 300, grid_m: 500, ageing_years: 5 });
  const [refresh, setRefresh] = useState(0);
  const [exportOpen, setExportOpen] = useState(false);
  const [form] = Form.useForm<Params>();

  const gap = useQuery({ queryKey: ['gap', params, refresh], queryFn: () => gapApi.get({ ...params, refresh: refresh ? 1 : undefined }), staleTime: 300_000 });
  const geo = useQuery({ queryKey: ['geo', 'cameras', params.district], queryFn: () => geoApi.cameras(params.district ? { district: params.district } : undefined), staleTime: 60_000 });

  const cams = useMemo<MapCamera[]>(
    () =>
      (geo.data?.features ?? []).map((f) => {
        const [lon, lat] = f.geometry.coordinates as [number, number];
        return { id: f.properties.id, name: f.properties.name, lat, lon, status: f.properties.status, department_code: f.properties.department_code, maintenance_status: f.properties.maintenance_status, anpr_enabled: f.properties.anpr_enabled };
      }),
    [geo.data],
  );
  const points = useMemo(() => cams.map((c) => [c.lat, c.lon] as [number, number]), [cams]);
  const gapStyle = useCallback(() => ({ color: '#DC2626', weight: 0.5, fillColor: '#DC2626', fillOpacity: 0.25 }), []);
  const poiToLayer = useCallback(
    (feature: GeoFeatureCollection['features'][number], latlng: L.LatLng) =>
      L.circleMarker(latlng, { radius: 6, color: '#fff', weight: 1.5, fillColor: '#DB2777', fillOpacity: 0.95 }).bindTooltip(`${escapeHtml(String(feature.properties.name))} · nearest camera ${fmtMetres(feature.properties.nearest_camera_m as number | null)}`),
    [],
  );
  const uncoveredGeo = useMemo<GeoFeatureCollection | null>(
    () =>
      gap.data
        ? { type: 'FeatureCollection', features: gap.data.uncovered_pois.map((p) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [p.lon, p.lat] }, properties: { name: p.name, nearest_camera_m: p.nearest_camera_m } })) }
        : null,
    [gap.data],
  );

  if (gap.isLoading && !gap.data) return <PageSkeleton cards={4} />;
  if (gap.isError && !gap.data) return <ErrorState error={gap.error} onRetry={() => void gap.refetch()} />;
  const g = gap.data as GapAnalysis;
  const s = g.summary;

  const deptCols = Array.from(new Set(g.by_area.flatMap((a) => Object.keys(a.by_department)))).sort();

  return (
    <div>
      <PageHeader
        title="Gap analysis"
        description={
          <span>
            Coverage by area, zero-coverage grid cells, uncovered points of interest, department gaps, offline hotspots, metadata gaps and ageing infrastructure. Generated <IstTime value={g.generated_at} mode="relative" />{g.cached ? ' (cached)' : ''}.
          </span>
        }
        extra={
          <Space wrap>
            <Button icon={<ReloadOutlined />} loading={gap.isFetching} onClick={() => setRefresh((n) => n + 1)}>
              Recompute
            </Button>
            {canExport ? (
              <Button type="primary" icon={<DownloadOutlined />} onClick={() => setExportOpen(true)}>
                Export report
              </Button>
            ) : null}
          </Space>
        }
      />
      <Card size="small" style={{ marginBottom: 12 }}>
        <Form form={form} layout="inline" initialValues={params} onFinish={(v) => setParams({ ...v, district: v.district || undefined })}>
          <Form.Item name="coverage_radius_m" label="Coverage radius (m)">
            <InputNumber min={25} max={2000} step={25} style={{ width: 110 }} />
          </Form.Item>
          <Form.Item name="poi_radius_m" label="POI radius (m)">
            <InputNumber min={50} max={5000} step={50} style={{ width: 110 }} />
          </Form.Item>
          <Form.Item name="grid_m" label="Grid (m)">
            <InputNumber min={100} max={5000} step={100} style={{ width: 100 }} />
          </Form.Item>
          <Form.Item name="ageing_years" label="Ageing (years)">
            <InputNumber min={1} max={30} style={{ width: 80 }} />
          </Form.Item>
          <Form.Item name="district" label="District">
            <Select allowClear placeholder="All" style={{ width: 170 }} options={districts.map((d) => ({ value: d, label: d }))} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={gap.isFetching}>
              Apply
            </Button>
          </Form.Item>
        </Form>
      </Card>

      <div className="sg-grid sg-grid-6" style={{ marginBottom: 12 }}>
        <KpiTile label="Cameras analysed" value={s.cameras_total} colour="#1E4DB7" footer={`${s.with_location} with coordinates · ${fmtPct(s.online_pct, 0)} online`} />
        <KpiTile label="Districts covered" value={`${s.districts_with_cameras} / ${s.districts_total}`} colour="#0EA5E9" footer="of Gujarat's districts" />
        <KpiTile label="Zero-coverage cells" value={s.zero_coverage_cells} colour="#DC2626" footer={`${g.params.grid_m} m grid, ${g.params.coverage_radius_m} m radius`} />
        <KpiTile label="Uncovered POIs" value={s.uncovered_pois} colour="#DB2777" footer={`no camera within ${g.params.poi_radius_m} m`} />
        <KpiTile label="Ageing cameras" value={s.ageing_cameras} colour="#D97706" footer={`> ${g.params.ageing_years} y, analog, AMC, faulty`} />
        <KpiTile label="Metadata gaps" value={s.metadata_gaps} colour="#7C3AED" footer={`${s.offline_hotspots} offline hotspots`} />
      </div>

      <div className="sg-grid" style={{ gridTemplateColumns: 'minmax(0, 1.15fr) minmax(0, 1fr)', marginBottom: 12 }}>
        <Card size="small" title="Zero-coverage cells and uncovered POIs" styles={{ body: { padding: 8 } }}>
          <BaseMap height={440}>
            {points.length ? <FitBounds points={points} fitKey={params.district ?? 'all'} /> : null}
            <GeoJSON key={`gap-${refresh}-${JSON.stringify(params)}`} data={g.zero_coverage as unknown as GeoJSON.GeoJsonObject} style={gapStyle} />
            {uncoveredGeo ? <GeoJSON key={`poi-${g.generated_at}`} data={uncoveredGeo as unknown as GeoJSON.GeoJsonObject} pointToLayer={poiToLayer as unknown as (f: GeoJSON.Feature, latlng: L.LatLng) => L.Layer} /> : null}
            <CameraMarkers cameras={cams} cluster={cams.length >= 50} onClick={(c) => navigate(`/cameras/${c.id}`)} />
          </BaseMap>
          <div style={{ display: 'flex', gap: 16, fontSize: 12, color: '#4B5563', padding: '8px 4px 0' }}>
            <span><span style={{ display: 'inline-block', width: 12, height: 12, background: 'rgba(220,38,38,0.35)', border: '1px solid #DC2626', verticalAlign: -2, marginRight: 4 }} />zero-coverage cell</span>
            <span><span className="sg-legend-swatch" style={{ display: 'inline-block', background: '#DB2777', verticalAlign: -2, marginRight: 4 }} />uncovered POI</span>
            <span><span className="sg-legend-swatch" style={{ display: 'inline-block', background: '#16A34A', verticalAlign: -2, marginRight: 4 }} />camera (status colour)</span>
            {g.zero_coverage_truncated ? <Tag color="orange" style={{ margin: 0 }}>cells truncated at 5 000</Tag> : null}
          </div>
        </Card>
        <Card size="small" title="Recommendations" styles={{ body: { padding: 0 } }}>
          {g.recommendations.length ? (
            <List
              size="small"
              dataSource={g.recommendations}
              renderItem={(r) => (
                <List.Item style={{ padding: '10px 16px' }}>
                  <div>
                    <div style={{ fontWeight: 600 }}>{r.district}</div>
                    <div style={{ color: '#4B5563' }}>{r.text}</div>
                  </div>
                </List.Item>
              )}
              style={{ maxHeight: 480, overflow: 'auto' }}
            />
          ) : (
            <EmptyState compact title="No recommendations" description="Recommendations are generated per district from uncovered POIs and ageing cameras." />
          )}
        </Card>
      </div>

      <Card size="small" styles={{ body: { paddingTop: 4 } }}>
        <Tabs
          items={[
            {
              key: 'district',
              label: `By district (${g.by_district.length})`,
              children: (
                <Table
                  size="small"
                  rowKey="district"
                  pagination={false}
                  dataSource={g.by_district}
                  columns={[
                    { title: 'District', dataIndex: 'district', render: (v: string) => <strong>{v}</strong> },
                    { title: 'Cameras', dataIndex: 'total', width: 90, align: 'right' },
                    { title: 'Online', dataIndex: 'online', width: 90, align: 'right' },
                    { title: 'Online %', dataIndex: 'online_pct', width: 100, align: 'right', render: (v: number) => <span style={{ color: v >= 80 ? '#16A34A' : v >= 40 ? '#D97706' : '#DC2626', fontWeight: 500 }}>{fmtPct(v, 0)}</span> },
                    { title: 'Zero-coverage cells', dataIndex: 'zero_coverage_cells', width: 150, align: 'right' },
                    { title: 'Uncovered POIs', dataIndex: 'uncovered_pois', width: 130, align: 'right' },
                    { title: 'Ageing', dataIndex: 'ageing', width: 90, align: 'right' },
                  ]}
                />
              ),
            },
            {
              key: 'area',
              label: `By police station (${g.by_area.length})`,
              children: (
                <Table
                  size="small"
                  rowKey={(r) => `${r.district}-${r.police_station}-${r.ward}`}
                  pagination={{ pageSize: 15, size: 'small' }}
                  dataSource={g.by_area}
                  scroll={{ x: 900 }}
                  columns={[
                    { title: 'District', dataIndex: 'district', width: 140, fixed: 'left' },
                    { title: 'Police station', dataIndex: 'police_station', width: 160, render: (v: string | null) => v ?? '—' },
                    { title: 'Ward', dataIndex: 'ward', width: 90, render: (v: string | null) => v ?? '—' },
                    { title: 'Total', dataIndex: 'total', width: 70, align: 'right' },
                    { title: 'Online', dataIndex: 'online', width: 70, align: 'right' },
                    { title: 'Degraded', dataIndex: 'degraded', width: 90, align: 'right' },
                    { title: 'Offline', dataIndex: 'offline', width: 80, align: 'right' },
                    { title: 'Online %', dataIndex: 'online_pct', width: 90, align: 'right', render: (v: number) => fmtPct(v, 0) },
                    ...deptCols.map((code) => ({ title: <DepartmentTag code={code} size="small" />, key: code, width: 100, align: 'right' as const, render: (_v: unknown, r: GapAnalysis['by_area'][number]) => r.by_department[code] ?? <span style={{ color: '#D1D5DB' }}>0</span> })),
                  ]}
                />
              ),
            },
            {
              key: 'pois',
              label: `Uncovered POIs (${g.uncovered_pois.length})`,
              children: (
                <Table
                  size="small"
                  rowKey="id"
                  pagination={{ pageSize: 15, size: 'small' }}
                  dataSource={g.uncovered_pois}
                  columns={[
                    { title: 'Point of interest', dataIndex: 'name', render: (v: string) => <strong>{v}</strong> },
                    { title: 'Type', dataIndex: 'type', width: 130, render: (v: string) => v.replace(/_/g, ' ') },
                    { title: 'District', dataIndex: 'district', width: 140 },
                    { title: 'Nearest camera', dataIndex: 'nearest_camera_m', width: 150, align: 'right', render: (v: number | null, r) => (v === null ? '—' : <Tooltip title={`camera #${r.nearest_camera_id}`}><span style={{ color: '#DC2626', fontWeight: 500 }}>{fmtMetres(v)}</span></Tooltip>) },
                    { title: 'Coordinates', width: 170, render: (_v, r) => <code style={{ fontSize: 12 }}>{r.lat.toFixed(4)}, {r.lon.toFixed(4)}</code> },
                  ]}
                />
              ),
            },
            {
              key: 'dept',
              label: `Department gaps (${g.department_gaps.length})`,
              children: g.department_gaps.length ? (
                <Table
                  size="small"
                  rowKey={(r) => `${r.department_code}-${r.district}`}
                  pagination={{ pageSize: 15, size: 'small' }}
                  dataSource={g.department_gaps}
                  columns={[
                    { title: 'Department', dataIndex: 'department_code', width: 140, render: (v: string, r) => <DepartmentTag code={v} name={r.department_name} size="small" /> },
                    { title: 'Name', dataIndex: 'department_name' },
                    { title: 'District without cameras', dataIndex: 'district', width: 220 },
                  ]}
                />
              ) : (
                <EmptyState compact title="Every department has cameras in every district analysed" />
              ),
            },
            {
              key: 'hotspots',
              label: `Offline hotspots (${g.offline_hotspots.length})`,
              children: g.offline_hotspots.length ? (
                <Table
                  size="small"
                  rowKey={(r) => `${r.district}-${r.police_station}`}
                  pagination={false}
                  dataSource={g.offline_hotspots}
                  columns={[
                    { title: 'District', dataIndex: 'district', width: 160 },
                    { title: 'Police station', dataIndex: 'police_station', width: 200, render: (v: string | null) => v ?? '—' },
                    { title: 'Cameras offline > 24 h', dataIndex: 'count', width: 180, align: 'right' },
                    { title: 'Camera ids', dataIndex: 'camera_ids', render: (ids: number[]) => <Space size={4} wrap>{ids.map((id) => <Button key={id} size="small" type="link" style={{ padding: 0 }} onClick={() => navigate(`/cameras/${id}`)}>#{id}</Button>)}</Space> },
                  ]}
                />
              ) : (
                <EmptyState compact title="No offline hotspots" description="A hotspot is a police station with several cameras offline for more than 24 h." />
              ),
            },
            {
              key: 'meta',
              label: `Metadata gaps (${g.metadata_gaps.length})`,
              children: g.metadata_gaps.length ? (
                <Table
                  size="small"
                  rowKey="camera_id"
                  pagination={{ pageSize: 15, size: 'small' }}
                  dataSource={g.metadata_gaps}
                  onRow={(r) => rowProps(() => navigate(`/cameras?open=${r.camera_id}`))}
                  columns={[
                    { title: 'Camera', dataIndex: 'name', render: (v: string, r) => <span><strong>{v}</strong> <span style={{ color: '#9CA3AF' }}>#{r.camera_id}</span></span> },
                    { title: 'Missing fields', dataIndex: 'missing', render: (m: string[]) => <Space size={4} wrap>{m.map((f) => <Tag key={f} color="purple" style={{ margin: 0 }}>{f}</Tag>)}</Space> },
                  ]}
                />
              ) : (
                <EmptyState compact title="No metadata gaps" description="Every camera has a stream URL, retention and install date." />
              ),
            },
            {
              key: 'ageing',
              label: `Ageing infrastructure (${g.ageing.length})`,
              children: (
                <Table
                  size="small"
                  rowKey="camera_id"
                  pagination={{ pageSize: 15, size: 'small' }}
                  dataSource={g.ageing}
                  scroll={{ x: 1100 }}
                  onRow={(r) => rowProps(() => navigate(`/cameras?open=${r.camera_id}`))}
                  columns={[
                    { title: 'Priority', dataIndex: 'priority_score', width: 100, align: 'right', fixed: 'left', render: (v: number) => <span style={{ fontWeight: 600, color: v >= 60 ? '#DC2626' : v >= 30 ? '#D97706' : '#4B5563' }}>{v.toFixed(0)}</span> },
                    { title: 'Camera', dataIndex: 'name', width: 240, ellipsis: true, render: (v: string) => <strong>{v}</strong> },
                    { title: 'Department', dataIndex: 'department_code', width: 120, render: (v: string) => <DepartmentTag code={v} size="small" /> },
                    { title: 'District', dataIndex: 'district', width: 130, render: (v: string | null) => v ?? '—' },
                    { title: 'Type', dataIndex: 'type', width: 100, render: (v: string) => cameraTypeLabel(v) },
                    { title: 'Installed', dataIndex: 'install_date', width: 110, render: (v: string | null) => fmtIstDate(v) },
                    { title: 'Age', dataIndex: 'age_years', width: 70, align: 'right', render: (v: number | null) => (v === null ? '—' : `${v.toFixed(1)} y`) },
                    { title: 'AMC', dataIndex: 'amc_status', width: 120, render: (v: string) => <AmcTag status={v} size="small" /> },
                    { title: 'Maintenance', dataIndex: 'maintenance_status', width: 150, render: (v: string) => <MaintenanceTag status={v} size="small" /> },
                    { title: 'Offline 24 h', dataIndex: 'offline_pct_24h', width: 100, align: 'right', render: (v: number) => fmtPct(v, 0) },
                    { title: 'Reasons', dataIndex: 'reasons', render: (rs: string[]) => <Space size={4} wrap>{rs.map((r) => <Tag key={r} style={{ margin: 0 }}>{r}</Tag>)}</Space> },
                  ]}
                />
              ),
            },
          ]}
        />
      </Card>
      <Typography.Text type="secondary" style={{ display: 'block', marginTop: 10, fontSize: 12 }}>
        Priority score = 10 × years over threshold + 30 analog + 25 AMC expired + 15 AMC expiring + 0.2 × offline % + 10 near POI (capped at 100).
      </Typography.Text>
      <ExportDialog
        open={exportOpen}
        title="Export gap-analysis report"
        filters={[
          { label: 'Coverage radius', value: `${params.coverage_radius_m} m` },
          { label: 'POI radius', value: `${params.poi_radius_m} m` },
          { label: 'Grid', value: `${params.grid_m} m` },
          { label: 'Ageing threshold', value: `${params.ageing_years} years` },
          { label: 'District', value: params.district ?? 'all' },
        ]}
        onExport={(fmt) => gapApi.export(fmt, { ...params })}
        onClose={() => setExportOpen(false)}
      />
    </div>
  );
}
