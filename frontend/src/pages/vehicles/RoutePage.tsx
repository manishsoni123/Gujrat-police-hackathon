/** Route (/vehicles/:plate/route): map with numbered markers + polyline, timeline table, crops strip, plausibility flags, PDF export, print. */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Segmented, Space, Table, Tag, Tooltip, Typography } from 'antd';
import { rowProps } from '@/hooks/useListQuery';
import { MATCH_LABEL, CONFIRMATION_LABEL } from '@/utils/labels';
import { FilePdfOutlined, PlayCircleOutlined, PrinterOutlined, SearchOutlined, WarningOutlined } from '@ant-design/icons';
import { Marker, Polyline, Popup } from '@/components/leaflet';
import { PageHeader } from '@/components/PageHeader';
import { KpiTile } from '@/components/KpiTile';
import { vehiclesApi } from '@/api';
import type { RouteSighting, VehicleRoute } from '@/api/types';
import { BaseMap, FitBounds, numberedIcon } from '@/components/MapView';
import { PlateText } from '@/components/PlateText';
import { IstTime } from '@/components/IstTime';
import { CropThumb, ConfidenceBar } from '@/components/Misc';
import { DepartmentTag } from '@/components/Tags';
import { EmptyState, ErrorState, PageSkeleton } from '@/components/States';
import { ExportDialog } from '@/components/Dialogs';
import { PlayRecordingModal } from '@/components/Recordings';
import { RangeIst, type IsoRange } from '@/components/RangeIst';
import { usePermission } from '@/store/auth';
import { fmtKm, fmtSpeed } from '@/utils/format';
import { fmtIst, fmtMinutes, hoursAgoIso, nowIso } from '@/utils/time';
import { formatPlate } from '@/utils/plate';

const FLAG_META: Record<string, { colour: string; label: string }> = {
  implausible_speed: { colour: '#D97706', label: 'Implausible speed' },
  long_gap: { colour: '#6B7280', label: 'Long gap' },
  overlap: { colour: '#7C3AED', label: 'Overlap' },
};

/** Default route window: the last 2 h (a looping demo feed produces ~4 stops per 90 s loop). */
const DEFAULT_WINDOW_H = 2;
const WINDOW_PRESETS = [2, 6, 24];
const CROPS_INITIAL = 12;
const TIMELINE_PAGE = 25;

interface CameraStops {
  key: number;
  camera: RouteSighting['camera'];
  stops: RouteSighting[];
  label: number;
  fuzzy: boolean;
}

/** One marker per camera (stops at the same camera would otherwise stack on the same point). */
function groupByCamera(sightings: RouteSighting[]): CameraStops[] {
  const map = new Map<number, CameraStops>();
  sightings.forEach((s) => {
    if (s.camera.lat === null || s.camera.lon === null) return;
    const g = map.get(s.camera.id);
    if (g) {
      g.stops.push(s);
      g.fuzzy = g.fuzzy && s.match === 'fuzzy';
    } else map.set(s.camera.id, { key: s.camera.id, camera: s.camera, stops: [s], label: s.seq, fuzzy: s.match === 'fuzzy' });
  });
  return Array.from(map.values());
}

function FlagTag({ type }: { type: string }) {
  const m = FLAG_META[type] ?? { colour: '#9CA3AF', label: type };
  return (
    <Tag style={{ margin: 0, color: m.colour, borderColor: `${m.colour}55`, background: `${m.colour}14`, fontWeight: 500 }} icon={<WarningOutlined />}>
      {m.label}
    </Tag>
  );
}

export function RoutePage() {
  const { plate = '' } = useParams();
  const navigate = useNavigate();
  const [sp, setSp] = useSearchParams();
  const canExport = usePermission('reports.export');
  const [include, setInclude] = useState<'confirmed' | 'all'>((sp.get('include') as 'confirmed' | 'all') ?? 'confirmed');
  const [range, setRange] = useState<IsoRange>({ from: sp.get('from') ?? hoursAgoIso(DEFAULT_WINDOW_H), to: sp.get('to') ?? nowIso() });
  const [exportOpen, setExportOpen] = useState(false);
  const [play, setPlay] = useState<RouteSighting | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [showAllCrops, setShowAllCrops] = useState(false);
  const [page, setPage] = useState(1);
  const setWindow = (hours: number) => setRange({ from: hoursAgoIso(hours), to: nowIso() });
  const activePreset = useMemo(() => {
    if (!range.from || !range.to) return null;
    const hours = (Date.parse(range.to) - Date.parse(range.from)) / 3_600_000;
    return WINDOW_PRESETS.find((h) => Math.abs(h - hours) < 0.05) ?? null;
  }, [range]);

  useEffect(() => {
    const next = new URLSearchParams();
    if (range.from) next.set('from', range.from);
    if (range.to) next.set('to', range.to);
    next.set('include', include);
    setSp(next, { replace: true });
  }, [range, include, setSp]);

  const route = useQuery({ queryKey: ['vehicles', 'route', plate, range, include], queryFn: () => vehiclesApi.route(plate, { from: range.from, to: range.to, include }), enabled: Boolean(plate) });
  const r: VehicleRoute | undefined = route.data;
  const points = useMemo(() => r?.polyline ?? [], [r]);
  const legByTo = useMemo(() => new Map((r?.legs ?? []).map((l) => [l.to_seq, l])), [r]);
  const cameraStops = useMemo(() => groupByCamera(r?.sightings ?? []), [r]);
  // Jump the timeline to the page holding the stop chosen on the map.
  useEffect(() => {
    if (selected === null || !r) return;
    const idx = r.sightings.findIndex((s) => s.seq === selected);
    if (idx >= 0) setPage(Math.floor(idx / TIMELINE_PAGE) + 1);
  }, [selected, r]);

  if (route.isLoading) return <PageSkeleton cards={3} />;
  if (route.isError) return <ErrorState error={route.error} onRetry={() => void route.refetch()} />;
  if (!r) return null;

  const columns = [
    { title: '#', dataIndex: 'seq', width: 50, render: (v: number, s: RouteSighting) => <span className={`sg-route-marker ${s.match === 'fuzzy' ? 'sg-route-marker-fuzzy' : ''}`} style={{ width: 24, height: 24, fontSize: 11 }}>{v}</span> },
    { title: 'Crop', dataIndex: 'crop_url', width: 110, render: (u: string | null) => <CropThumb src={u} alt={`Crop at stop`} width={96} height={34} /> },
    { title: 'Camera', dataIndex: ['camera', 'name'], render: (v: string, s: RouteSighting) => <span><Button type="link" style={{ padding: 0, fontWeight: 600 }} onClick={() => navigate(`/cameras/${s.camera.id}`)}>{v}</Button><div style={{ fontSize: 11, color: '#6B7280' }}><DepartmentTag code={s.camera.department_code} size="small" /> {s.camera.district ?? '—'} · {s.camera.police_station ?? '—'}</div></span> },
    { title: 'First seen (IST)', dataIndex: 'first_seen', width: 150, render: (v: string) => <IstTime value={v} /> },
    { title: 'Last seen (IST)', dataIndex: 'last_seen', width: 150, render: (v: string) => <IstTime value={v} /> },
    { title: 'Dwell', width: 80, render: (_v: unknown, s: RouteSighting) => fmtMinutes((Date.parse(s.last_seen) - Date.parse(s.first_seen)) / 60_000) },
    { title: 'Reads', dataIndex: 'read_count', width: 70, align: 'right' as const },
    { title: 'Conf.', dataIndex: 'best_conf', width: 120, render: (v: number) => <ConfidenceBar value={v} width={50} /> },
    {
      title: 'From previous',
      width: 230,
      render: (_v: unknown, s: RouteSighting) => {
        const leg = legByTo.get(s.seq);
        if (!leg) return <span style={{ color: '#9CA3AF' }}>start</span>;
        return (
          <div style={{ fontSize: 12 }}>
            <div>{fmtKm(leg.distance_km)} in {fmtMinutes(leg.minutes)} · {fmtSpeed(leg.speed_kmh)}</div>
            <Space size={4} wrap style={{ marginTop: 2 }}>{leg.flags.map((f) => <FlagTag key={f} type={f} />)}</Space>
          </div>
        );
      },
    },
    { title: 'Match', dataIndex: 'match', width: 150, render: (v: string, s: RouteSighting) => <Space size={4}><Tag color={v === 'exact' ? 'blue' : 'orange'} style={{ margin: 0 }}>{MATCH_LABEL[v] ?? v}</Tag>{s.confirmation ? <Tag color={s.confirmation === 'confirmed' ? 'green' : 'default'} style={{ margin: 0 }}>{CONFIRMATION_LABEL[s.confirmation] ?? s.confirmation}</Tag> : null}</Space> },
    { title: '', width: 60, className: 'sg-no-print', render: (_v: unknown, s: RouteSighting) => <Tooltip title={s.recording_available ? 'Play recording from 10 s before' : 'No recording on this camera'}><Button size="small" icon={<PlayCircleOutlined />} disabled={!s.recording_available} onClick={(e) => { e.stopPropagation(); setPlay(s); }} aria-label="Play recording" /></Tooltip> },
  ];

  return (
    <div className="sg-route-page">
      <PageHeader
        breadcrumb={[{ label: 'Vehicle search', to: `/vehicles?q=${r.plate}` }, { label: formatPlate(r.plate) }]}
        title={`Route of ${r.plate_display}`}
        docTitle={`Route ${r.plate_display}`}
        description={
          <span>
            {r.sightings.length} stop{r.sightings.length === 1 ? '' : 's'} across {r.cameras_count} camera{r.cameras_count === 1 ? '' : 's'} between <IstTime value={r.window.from} /> and <IstTime value={r.window.to} /> · {r.loop_resets_in_window} loop reset{r.loop_resets_in_window === 1 ? '' : 's'} in window. Same-camera sightings within 60 s are merged into one stop.
          </span>
        }
        extra={
          <Space wrap className="sg-no-print">
            <Space.Compact>
              {WINDOW_PRESETS.map((h) => (
                <Button key={h} type={activePreset === h ? 'primary' : 'default'} onClick={() => setWindow(h)} aria-pressed={activePreset === h}>
                  Last {h} h
                </Button>
              ))}
            </Space.Compact>
            <RangeIst value={range} onChange={setRange} />
            <Segmented value={include} options={[{ value: 'confirmed', label: 'Exact + confirmed' }, { value: 'all', label: 'All candidates' }]} onChange={(v) => setInclude(v as 'confirmed' | 'all')} />
            <Button icon={<SearchOutlined />} onClick={() => navigate(`/vehicles?q=${r.plate}`)}>
              Back to search
            </Button>
            <Button icon={<PrinterOutlined />} onClick={() => window.print()}>
              Print
            </Button>
            {canExport ? (
              <Button type="primary" icon={<FilePdfOutlined />} onClick={() => setExportOpen(true)}>
                Export PDF
              </Button>
            ) : null}
          </Space>
        }
      />
      {!r.sightings.length ? (
        <Card>
          <EmptyState title="No sightings for this plate in the selected window" description="Widen the window, or go back to the search and confirm fuzzy candidates so they are included in the route." actions={<Button type="primary" onClick={() => navigate(`/vehicles?q=${r.plate}`)}>Back to search</Button>} />
        </Card>
      ) : (
        <div>
          <div className="sg-grid sg-grid-4" style={{ marginBottom: 12 }}>
            <KpiTile label="Stops" value={r.sightings.length} colour="#1E4DB7" footer={`${r.cameras_count} distinct camera${r.cameras_count === 1 ? '' : 's'} · one map marker per camera`} />
            <KpiTile label="Distance" value={fmtKm(r.total_distance_km)} colour="#0EA5E9" footer="straight-line legs" />
            <KpiTile label="Duration" value={fmtMinutes(r.total_duration_min)} colour="#16A34A" footer="first to last sighting" />
            <KpiTile label="Plausibility flags" value={r.flags.length} colour={r.flags.length ? '#D97706' : '#16A34A'} footer={r.flags.length ? 'review legs marked amber' : 'all legs plausible'} />
          </div>
          <div className="sg-grid" style={{ gridTemplateColumns: 'minmax(0, 1.3fr) minmax(0, 1fr)', marginBottom: 12 }}>
            <Card size="small" title="Movement map" styles={{ body: { padding: 8 } }}>
              <BaseMap height={420}>
                {points.length ? <FitBounds points={points} fitKey={r.plate + r.sightings.length} /> : null}
                {points.length > 1 ? <Polyline positions={points} pathOptions={{ color: '#1E4DB7', weight: 3, opacity: 0.85, dashArray: '2 6' }} /> : null}
                {cameraStops.map((g) => (
                  <Marker key={g.key} position={[g.camera.lat as number, g.camera.lon as number]} icon={numberedIcon(g.label, g.fuzzy, g.stops.length)} eventHandlers={{ click: () => setSelected(g.stops[0].seq) }}>
                    <Popup>
                      <div style={{ fontSize: 12, maxHeight: 220, overflow: 'auto' }}>
                        <div style={{ fontWeight: 600 }}>{g.camera.name}</div>
                        <div style={{ color: '#6B7280', marginBottom: 4 }}>{g.stops.length} stop{g.stops.length === 1 ? '' : 's'} at this camera</div>
                        {g.stops.slice(0, 8).map((s) => (
                          <div key={s.seq} style={{ display: 'flex', gap: 6, alignItems: 'center', padding: '1px 0', cursor: 'pointer' }} onClick={() => setSelected(s.seq)}>
                            <span className={`sg-route-marker ${s.match === 'fuzzy' ? 'sg-route-marker-fuzzy' : ''}`} style={{ width: 20, height: 20, fontSize: 10 }}>{s.seq}</span>
                            <span>{fmtIst(s.first_seen)}</span>
                            <span style={{ color: '#6B7280' }}>{s.read_count} reads · {Math.round(s.best_conf * 100)} %</span>
                          </div>
                        ))}
                        {g.stops.length > 8 ? <div style={{ color: '#6B7280' }}>… and {g.stops.length - 8} more in the timeline</div> : null}
                      </div>
                    </Popup>
                  </Marker>
                ))}
              </BaseMap>
              <div style={{ fontSize: 12, color: '#6B7280', padding: '6px 4px 0' }}>One marker per camera (numbered by its first stop; the small badge counts repeat stops). Blue = exact reads, amber = confirmed near matches. Straight segments, no road routing.</div>
            </Card>
            <Card size="small" title={`Plausibility (${r.flags.length})`} styles={{ body: { padding: r.flags.length ? 12 : 0, maxHeight: 470, overflow: 'auto' } }}>
              {r.flags.length ? (
                <div style={{ display: 'grid', gap: 8 }}>
                  <Alert type="warning" showIcon message="Flags are advisory" description="On looping test feeds every leg is expected to trip the speed threshold because the same 90 s clip plays on cameras kilometres apart. On real feeds a flag means the OCR may have matched two different vehicles." style={{ fontSize: 12 }} />
                  {r.flags.map((f, i) => (
                    <div key={i} style={{ border: '1px solid #E5E7EB', borderRadius: 6, padding: '8px 10px', fontSize: 12 }}>
                      <Space size={6}>
                        <FlagTag type={f.type} />
                        <strong>{f.from_seq} → {f.to_seq}</strong>
                      </Space>
                      <div style={{ color: '#4B5563', marginTop: 4 }}>{f.message}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState compact title="No plausibility flags" description={`Every leg is under the ${'150'} km/h threshold and no long gaps or overlaps were found.`} />
              )}
            </Card>
          </div>
          <Card size="small" title="Timeline" styles={{ body: { padding: 0 } }} style={{ marginBottom: 12 }}>
            <Table<RouteSighting>
              size="small"
              rowKey="seq"
              pagination={r.sightings.length > TIMELINE_PAGE ? { current: page, pageSize: TIMELINE_PAGE, size: 'small', showSizeChanger: false, onChange: setPage, showTotal: (t, range) => `stops ${range[0]}–${range[1]} of ${t}` } : false}
              dataSource={r.sightings}
              columns={columns}
              scroll={{ x: 1100 }}
              rowClassName={(s) => (s.seq === selected ? 'sg-row-flash' : '')}
              onRow={(s) => rowProps(() => setSelected(s.seq))}
            />
          </Card>
          <Card size="small" title={`Evidence crops (${r.sightings.length})`} extra={r.sightings.length > CROPS_INITIAL ? <Button type="link" size="small" className="sg-no-print" style={{ padding: 0 }} onClick={() => setShowAllCrops((v) => !v)}>{showAllCrops ? `Show first ${CROPS_INITIAL}` : `Show all ${r.sightings.length}`}</Button> : null}>
            <div className="sg-crop-strip">
              {(showAllCrops ? r.sightings : r.sightings.slice(0, CROPS_INITIAL)).map((s) => (
                <div key={s.seq} className="sg-crop-strip-item">
                  <CropThumb src={s.crop_url} alt={`Crop ${s.seq} at ${s.camera.name}`} width={160} height={54} />
                  <div style={{ marginTop: 4 }}>
                    <strong>{s.seq}.</strong> {s.camera.name}
                  </div>
                  <div style={{ color: '#6B7280' }}><IstTime value={s.first_seen} /></div>
                </div>
              ))}
            </div>
          </Card>
          <Typography.Text type="secondary" style={{ display: 'block', marginTop: 10, fontSize: 12 }}>
            Queried plate <PlateText plate={r.plate} size="small" /> · window {fmtIst(r.window.from)} → {fmtIst(r.window.to)} · include: {r.window.include}. The PDF carries the same table, map, crops and a SHA-256 evidence footer.
          </Typography.Text>
        </div>
      )}
      <ExportDialog
        open={exportOpen}
        title={`Export route report · ${r.plate_display}`}
        formats={['pdf']}
        filters={[
          { label: 'Plate', value: r.plate_display },
          { label: 'Window', value: `${fmtIst(r.window.from)} → ${fmtIst(r.window.to)}` },
          { label: 'Include', value: include === 'all' ? 'all candidates' : 'exact + confirmed' },
          { label: 'Stops', value: r.sightings.length },
        ]}
        onExport={() => vehiclesApi.routePdf(r.plate, { from: range.from, to: range.to, include })}
        onClose={() => setExportOpen(false)}
      />
      {play ? <PlayRecordingModal open onClose={() => setPlay(null)} cameraId={play.camera.id} cameraName={play.camera.name} at={play.first_seen} sightingId={play.sighting_id} /> : null}
    </div>
  );
}
