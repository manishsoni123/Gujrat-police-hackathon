/** Full-height GIS map with layer control, clustering, legend, camera popup with snapshot + View live. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Checkbox, Divider, Select, Space, Spin, Switch, Typography } from 'antd';
import L from 'leaflet';
import { GeoJSON, useMap } from '@/components/leaflet';
import { PageHeader } from '@/components/PageHeader';
import { BaseMap, CONFIDENCE_LEGEND, CameraMarkers, FitBounds, PanToFlash, escapeHtml, locationHint, useAlertFlash, type MapCamera } from '@/components/MapView';
import { gapApi, geoApi } from '@/api';
import { CAMERA_STATUS, CATALOGUE_NOT_STREAMING, MAINTENANCE_STATUS, departmentColour, displayStatus, type CameraStatus } from '@/theme/colours';
import { ErrorState } from '@/components/States';
import type { GeoFeatureCollection } from '@/api/types';
import { cameraTypeLabel, fmtMetres } from '@/utils/format';

const TYPES = ['analog', 'ip', 'ptz', 'dome', 'bullet', 'anpr', 'other'];
const STATUSES: CameraStatus[] = ['online', 'degraded', 'offline', 'not_streaming', 'unknown'];

function PopupBinder({ selected }: { selected: number | null }) {
  const map = useMap();
  useEffect(() => {
    if (selected === null) return;
    map.closePopup();
  }, [selected, map]);
  return null;
}

export function MapPage() {
  const navigate = useNavigate();
  const [deptFilter, setDeptFilter] = useState<string[]>([]);
  const [typeFilter, setTypeFilter] = useState<string[]>([]);
  const [statusFilter, setStatusFilter] = useState<CameraStatus[]>([...STATUSES]);
  const [deptRing, setDeptRing] = useState(true);
  const [showDistricts, setShowDistricts] = useState(true);
  const [showPois, setShowPois] = useState(false);
  const [showCoverage, setShowCoverage] = useState(false);
  const [showGaps, setShowGaps] = useState(false);
  const [cluster, setCluster] = useState(true);
  const [selected] = useState<number | null>(null);
  const flash = useAlertFlash();

  const geo = useQuery({ queryKey: ['geo', 'cameras'], queryFn: () => geoApi.cameras(), staleTime: 60_000 });
  const districts = useQuery({ queryKey: ['geo', 'districts'], queryFn: geoApi.districts, staleTime: 600_000, enabled: showDistricts });
  const pois = useQuery({ queryKey: ['geo', 'pois'], queryFn: () => geoApi.pois(), staleTime: 600_000, enabled: showPois });
  const coverage = useQuery({ queryKey: ['geo', 'coverage'], queryFn: () => geoApi.coverage(), staleTime: 300_000, enabled: showCoverage });
  const gaps = useQuery({ queryKey: ['gap', 'map'], queryFn: () => gapApi.get(), staleTime: 300_000, enabled: showGaps });

  const allCams = useMemo<MapCamera[]>(
    () =>
      (geo.data?.features ?? [])
        .filter((f) => Array.isArray(f.geometry.coordinates))
        .map((f) => {
          const [lon, lat] = f.geometry.coordinates as [number, number];
          const p = f.properties;
          return { id: p.id, name: p.name, lat, lon, status: p.status, live: p.live, location_confidence: p.location_confidence ?? null, department_code: p.department_code, department_name: p.department_name, maintenance_status: p.maintenance_status, anpr_enabled: p.anpr_enabled, district: p.district, type: p.type, police_station: p.police_station, codec: p.codec };
        }),
    [geo.data],
  );
  const departmentCodes = useMemo(() => Array.from(new Set(allCams.map((c) => c.department_code))).sort(), [allCams]);
  /** code → display name from the geo properties (falls back to the code for departments without a name). */
  const departmentName = useMemo(() => {
    const m = new Map<string, string>();
    allCams.forEach((c) => {
      if (c.department_name && !m.has(c.department_code)) m.set(c.department_code, c.department_name);
    });
    return (code: string) => m.get(code) ?? code;
  }, [allCams]);
  const cams = useMemo(
    () =>
      allCams.filter(
        (c) => (deptFilter.length === 0 || deptFilter.includes(c.department_code)) && (typeFilter.length === 0 || typeFilter.includes(c.type ?? '')) && statusFilter.includes(displayStatus(c.status, c.live)),
      ),
    [allCams, deptFilter, typeFilter, statusFilter],
  );
  const points = useMemo(() => allCams.map((c) => [c.lat, c.lon] as [number, number]), [allCams]);
  const styleOpts = useMemo(() => ({ departmentRing: deptRing, selectedId: selected, flashId: flash.flashId, flashColour: flash.flashColour }), [deptRing, selected, flash.flashId, flash.flashColour]);

  const popupHtml = useCallback(
    (c: MapCamera) => {
      const status = CAMERA_STATUS[displayStatus(c.status, c.live)];
      return `
      <div style="font-family:inherit">
        <div style="font-weight:600;font-size:14px;margin-bottom:4px">${escapeHtml(c.name)}</div>
        <div style="font-size:12px;color:#4B5563">${escapeHtml(c.department_name ?? c.department_code)} · ${escapeHtml(c.district ?? '—')}${c.police_station ? ' · ' + escapeHtml(c.police_station) : ''}</div>
        ${locationHint(c) ? `<div style="font-size:11px;color:#92400E;margin-top:2px" title="Coordinates were inferred by the team from the camera name; confirm with the organiser before operational use">${escapeHtml(locationHint(c))}</div>` : ''}
        <div style="font-size:12px;margin:6px 0;display:flex;gap:8px;align-items:center">
          <span style="display:inline-flex;align-items:center;gap:4px;color:${status.colour};font-weight:500"><span style="width:8px;height:8px;border-radius:50%;background:${status.colour};display:inline-block"></span>${status.label}</span>
          <span style="color:#6B7280">${escapeHtml(cameraTypeLabel(c.type))} · ${escapeHtml(c.codec ?? '')}${c.anpr_enabled ? ' · ANPR' : ''}</span>
        </div>
        <img src="/media/snapshots/cam_${c.id}.jpg?t=${Math.floor(Date.now() / 1000)}" alt="" onerror="this.style.display='none'" style="width:100%;height:120px;object-fit:cover;border-radius:6px;background:#0b0f19;margin:4px 0" />
        <div style="display:flex;gap:8px;margin-top:6px">
          <a href="/cameras/${c.id}" data-nav="/cameras/${c.id}" style="flex:1;text-align:center;background:#1E4DB7;color:#fff;padding:5px 8px;border-radius:6px;font-weight:500;text-decoration:none">View live</a>
          <a href="/cameras?open=${c.id}" data-nav="/cameras?open=${c.id}" style="flex:1;text-align:center;border:1px solid #E5E7EB;padding:5px 8px;border-radius:6px;color:#111827;text-decoration:none">Details</a>
        </div>
      </div>`;
    },
    [],
  );

  // Intercept popup links so navigation stays client-side.
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const a = (e.target as HTMLElement).closest('a[data-nav]') as HTMLAnchorElement | null;
      if (a) {
        e.preventDefault();
        navigate(a.dataset.nav ?? '/');
      }
    };
    document.addEventListener('click', handler);
    return () => document.removeEventListener('click', handler);
  }, [navigate]);

  const districtStyle = useCallback(() => ({ color: '#0B1F3A', weight: 1, fillColor: '#0B1F3A', fillOpacity: 0.04 }), []);
  const coverageStyle = useCallback(() => ({ color: '#1E4DB7', weight: 1, fillColor: '#1E4DB7', fillOpacity: 0.15 }), []);
  const gapStyle = useCallback(() => ({ color: '#DC2626', weight: 0.5, fillColor: '#DC2626', fillOpacity: 0.25 }), []);
  const poiToLayer = useCallback(
    (feature: GeoFeatureCollection['features'][number], latlng: L.LatLng) =>
      L.circleMarker(latlng, { radius: 5, color: '#fff', weight: 1, fillColor: '#DB2777', fillOpacity: 0.95 }).bindTooltip(
        `${escapeHtml(String(feature.properties.name))} (${escapeHtml(String(feature.properties.type))}) · nearest camera ${fmtMetres(feature.properties.nearest_camera_m as number | null)}`,
      ),
    [],
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 56px - 48px)' }}>
      <PageHeader
        title="Map"
        description={`${cams.length} of ${allCams.length} cameras shown · click a marker for details and live view`}
        extra={
          <Space wrap>
            <Select mode="multiple" allowClear placeholder="Department" style={{ minWidth: 200 }} value={deptFilter} onChange={setDeptFilter} maxTagCount="responsive" options={departmentCodes.map((d) => ({ value: d, label: departmentName(d) }))} aria-label="Filter by department" />
            <Select mode="multiple" allowClear placeholder="Camera type" style={{ minWidth: 170 }} value={typeFilter} onChange={setTypeFilter} maxTagCount="responsive" options={TYPES.map((t) => ({ value: t, label: cameraTypeLabel(t) }))} aria-label="Filter by camera type" />
          </Space>
        }
      />
      {geo.isError ? <ErrorState error={geo.error} onRetry={() => void geo.refetch()} /> : null}
      <div style={{ flex: 1, minHeight: 420, position: 'relative' }}>
        <BaseMap height="100%">
          <PopupBinder selected={selected} />
          <PanToFlash camera={flash.camera} />
          {points.length ? <FitBounds points={points} /> : null}
          {showDistricts && districts.data ? (
            <GeoJSON key="districts" data={districts.data as unknown as GeoJSON.GeoJsonObject} style={districtStyle} onEachFeature={(f, layer) => layer.bindTooltip(`${f.properties?.name}: ${f.properties?.camera_count} cameras, ${f.properties?.online_count} online`, { sticky: true })} />
          ) : null}
          {showCoverage && coverage.data ? <GeoJSON key="coverage" data={coverage.data as unknown as GeoJSON.GeoJsonObject} style={coverageStyle} /> : null}
          {showGaps && gaps.data ? <GeoJSON key="gaps" data={gaps.data.zero_coverage as unknown as GeoJSON.GeoJsonObject} style={gapStyle} /> : null}
          {showPois && pois.data ? <GeoJSON key="pois" data={pois.data as unknown as GeoJSON.GeoJsonObject} pointToLayer={poiToLayer as unknown as (f: GeoJSON.Feature, latlng: L.LatLng) => L.Layer} /> : null}
          <CameraMarkers cameras={cams} cluster={cluster && cams.length >= 50} styleOpts={styleOpts} popupHtml={popupHtml} />
        </BaseMap>

        <div className="sg-map-panel" role="group" aria-label="Map layers">
          <Typography.Text strong>Layers</Typography.Text>
          <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
            <Checkbox checked={showDistricts} onChange={(e) => setShowDistricts(e.target.checked)}>
              District boundaries {districts.isFetching ? <Spin size="small" /> : null}
            </Checkbox>
            <Checkbox checked={showCoverage} onChange={(e) => setShowCoverage(e.target.checked)}>
              Coverage circles {coverage.isFetching ? <Spin size="small" /> : null}
            </Checkbox>
            <Checkbox checked={showGaps} onChange={(e) => setShowGaps(e.target.checked)}>
              Zero-coverage grid {gaps.isFetching ? <Spin size="small" /> : null}
            </Checkbox>
            <Checkbox checked={showPois} onChange={(e) => setShowPois(e.target.checked)}>
              Points of interest {pois.isFetching ? <Spin size="small" /> : null}
            </Checkbox>
            <Checkbox checked={deptRing} onChange={(e) => setDeptRing(e.target.checked)}>
              Department ring colour
            </Checkbox>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>Cluster markers</span>
              <Switch size="small" checked={cluster} onChange={setCluster} aria-label="Cluster markers" />
            </div>
          </div>
          <Divider style={{ margin: '10px 0' }} />
          <Typography.Text strong>Status</Typography.Text>
          <div style={{ display: 'grid', gap: 4, marginTop: 6 }}>
            {STATUSES.map((st) => (
              <Checkbox key={st} checked={statusFilter.includes(st)} onChange={(e) => setStatusFilter((prev) => (e.target.checked ? [...prev, st] : prev.filter((x) => x !== st)))}>
                <span className="sg-legend-swatch" style={{ background: CAMERA_STATUS[st].colour, display: 'inline-block', marginRight: 6 }} />
                <span title={st === 'not_streaming' ? CATALOGUE_NOT_STREAMING.hint : undefined}>{st === 'not_streaming' ? CATALOGUE_NOT_STREAMING.label : CAMERA_STATUS[st].label}</span> <span style={{ color: '#9CA3AF' }}>({allCams.filter((c) => displayStatus(c.status, c.live) === st).length})</span>
              </Checkbox>
            ))}
          </div>
        </div>

        <div className="sg-legend" aria-label="Legend">
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Legend</div>
          {deptRing
            ? departmentCodes.slice(0, 8).map((d) => (
                <div key={d} className="sg-legend-row">
                  <span className="sg-legend-swatch" style={{ background: '#fff', boxShadow: `0 0 0 2px ${departmentColour(d)}` }} />
                  <span title={d}>{departmentName(d)}</span>
                </div>
              ))
            : null}
          <div className="sg-legend-row">
            <span className="sg-legend-swatch" style={{ background: '#fff', boxShadow: `0 0 0 2px ${MAINTENANCE_STATUS.under_maintenance.colour}`, borderStyle: 'dashed' }} /> Maintenance (dashed)
          </div>
          <div className="sg-legend-row">
            <span style={{ fontSize: 9, fontWeight: 700, background: '#0B1F3A', color: '#fff', borderRadius: 3, padding: '0 3px' }}>A</span> ANPR enabled
          </div>
          {allCams.some((c) => c.location_confidence === 'approx' || c.location_confidence === 'guess')
            ? CONFIDENCE_LEGEND.map((row) => (
                <div key={row.key} className="sg-legend-row" title={row.hint}>
                  <span className="sg-legend-swatch" style={row.key === 'approx' ? { background: '#9CA3AF', boxShadow: '0 0 0 2px rgba(17,24,39,0.55)', borderStyle: 'dashed', borderColor: '#fff' } : { background: '#fff', boxShadow: '0 0 0 2px #9CA3AF' }} />
                  {row.label}
                </div>
              ))
            : null}
          <div className="sg-legend-row" title="Three pulses in the alert priority colour when a new alert arrives">
            <span className="sg-legend-swatch" style={{ background: '#DC2626', boxShadow: '0 0 0 3px rgba(220,38,38,0.35)' }} /> New alert (pulses)
          </div>
          {showPois ? (
            <div className="sg-legend-row">
              <span className="sg-legend-swatch" style={{ background: '#DB2777' }} /> Point of interest
            </div>
          ) : null}
          {showGaps ? (
            <div className="sg-legend-row">
              <span style={{ width: 10, height: 10, background: 'rgba(220,38,38,0.35)', border: '1px solid #DC2626' }} /> Zero coverage cell
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
