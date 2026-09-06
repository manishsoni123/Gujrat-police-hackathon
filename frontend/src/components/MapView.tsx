/**
 * Shared Leaflet building blocks: base map, camera markers (status colour,
 * department ring, maintenance dash, ANPR glyph), clustering, fit-to-bounds.
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import 'leaflet.markercluster';
import 'leaflet.markercluster/dist/MarkerCluster.css';
import 'leaflet.markercluster/dist/MarkerCluster.Default.css';
import { MapContainer, TileLayer, useMap } from './leaflet';
import { CAMERA_STATUS, LOCATION_CONFIDENCE, MAINTENANCE_STATUS, departmentColour, displayStatus, type CameraStatus, type LocationConfidence, type MaintenanceStatus } from '@/theme/colours';
import { GUJARAT_CENTER, GUJARAT_ZOOM, OSM_ATTRIBUTION, OSM_TILES, boundsOf } from '@/utils/geo';
import { ALERT_PRIORITY } from '@/theme/colours';
import { useUiStore } from '@/store/ui';

export interface MapCamera {
  id: number;
  name: string;
  lat: number;
  lon: number;
  status: CameraStatus;
  /** Catalogue live flag: `false` renders the grey "not streaming" state instead of the status colour. */
  live?: boolean | null;
  /** Coordinate confidence: exact = solid, approx = dashed ring, guess = hollow ring (+ tooltip). */
  location_confidence?: LocationConfidence | null;
  department_code: string;
  maintenance_status?: MaintenanceStatus;
  anpr_enabled?: boolean;
  district?: string | null;
  type?: string;
  department_name?: string;
  police_station?: string | null;
  codec?: string;
}

export interface MarkerStyleOptions {
  departmentRing?: boolean;
  selectedId?: number | null;
  /** Camera to pulse (new alert): three pulses in `flashColour`; a containing cluster pulses too. */
  flashId?: number | null;
  flashColour?: string;
}

export function cameraIcon(c: MapCamera, opts: MarkerStyleOptions = {}): L.DivIcon {
  const status = CAMERA_STATUS[displayStatus(c.status, c.live)].colour;
  const selected = opts.selectedId === c.id;
  const size = selected ? 14 : 10;
  const classes = ['sg-marker'];
  const styles = [`width:${size}px`, `height:${size}px`];
  if (c.location_confidence === 'guess') {
    // hollow ring in the status colour: the point is a district-HQ fallback / best candidate, not a surveyed pole
    classes.push('sg-marker-guess');
    styles.push('background:#fff', `border-color:${status}`);
  } else {
    styles.push(`background:${status}`);
    if (c.location_confidence === 'approx') classes.push('sg-marker-approx');
  }
  if (opts.departmentRing) {
    classes.push('sg-marker-ring');
    styles.push(`--ring:${departmentColour(c.department_code)}`);
  }
  if (c.maintenance_status && c.maintenance_status !== 'ok') {
    classes.push('sg-marker-dashed');
    styles.push(`--maint:${MAINTENANCE_STATUS[c.maintenance_status].colour}`);
  }
  if (c.anpr_enabled) classes.push('sg-marker-anpr');
  if (selected) styles.push('box-shadow:0 0 0 4px rgba(30,77,183,0.35)');
  if (opts.flashId === c.id) {
    classes.push('sg-marker-flash');
    styles.push(`--flash:${opts.flashColour ?? '#DC2626'}`);
  }
  return L.divIcon({
    className: '',
    html: `<div class="${classes.join(' ')}" style="${styles.join(';')}"></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    popupAnchor: [0, -size / 2],
  });
}

/** Tooltip suffix for an inferred location ("Approximate location (team-inferred)"); empty for exact/unknown. */
export function locationHint(c: Pick<MapCamera, 'location_confidence'>): string {
  if (c.location_confidence === 'approx') return 'Approximate location (team-inferred)';
  if (c.location_confidence === 'guess') return 'Location is a guess (team-inferred)';
  return '';
}

export function markerTitle(c: MapCamera): string {
  const hint = locationHint(c);
  return hint ? `${c.name} - ${hint}` : c.name;
}

/** Legend entries for the confidence rings (organiser-sandbox cameras carry team-inferred coordinates). */
export const CONFIDENCE_LEGEND: { key: LocationConfidence; label: string; hint: string }[] = (['approx', 'guess'] as LocationConfidence[]).map((k) => ({
  key: k,
  label: `${LOCATION_CONFIDENCE[k].label} location (${LOCATION_CONFIDENCE[k].marker})`,
  hint: LOCATION_CONFIDENCE[k].hint,
}));

interface BaseMapProps {
  center?: [number, number];
  zoom?: number;
  height?: number | string;
  children?: ReactNode;
  className?: string;
  scrollWheelZoom?: boolean;
}

export function BaseMap({ center = GUJARAT_CENTER, zoom = GUJARAT_ZOOM, height = 420, children, className, scrollWheelZoom = true }: BaseMapProps) {
  return (
    <div className={`sg-map ${className ?? ''}`} style={{ height }}>
      <MapContainer center={center} zoom={zoom} style={{ width: '100%', height: '100%' }} scrollWheelZoom={scrollWheelZoom} preferCanvas>
        <TileLayer url={OSM_TILES} attribution={OSM_ATTRIBUTION} maxZoom={19} />
        {children}
      </MapContainer>
    </div>
  );
}

/** Fits the map to the given points once (or whenever `key` changes). */
export function FitBounds({ points, fitKey }: { points: Array<[number, number]>; fitKey?: string | number }) {
  const map = useMap();
  const b = useMemo(() => boundsOf(points), [points]);
  useEffect(() => {
    if (b) map.fitBounds(b, { padding: [20, 20], maxZoom: 15 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [b, fitKey, map]);
  return null;
}

/** Marker with the display status (and flash flag) attached so cluster icons can summarise what they contain. */
type StatusMarker = L.Marker & { sgStatus?: CameraStatus; sgFlash?: string | null };

const WORST_FIRST: CameraStatus[] = ['offline', 'degraded', 'unknown', 'not_streaming', 'retired', 'online'];

/**
 * Cluster icon: neutral primary fill with the count, a ring in the worst status colour inside the
 * cluster and a title with the full breakdown ("12 cameras · 3 online · 9 offline"), replacing the
 * library's green/yellow/orange size-based colours which read as health on a status-coloured map.
 */
export function clusterIcon(cluster: L.MarkerCluster): L.DivIcon {
  const markers = cluster.getAllChildMarkers() as StatusMarker[];
  const counts: Partial<Record<CameraStatus, number>> = {};
  let flash: string | null = null;
  markers.forEach((m) => {
    const st = m.sgStatus ?? 'unknown';
    counts[st] = (counts[st] ?? 0) + 1;
    if (m.sgFlash) flash = m.sgFlash;
  });
  const worst = WORST_FIRST.find((st) => (counts[st] ?? 0) > 0) ?? 'unknown';
  const n = markers.length;
  const size = n < 10 ? 34 : n < 100 ? 40 : 46;
  const breakdown = WORST_FIRST.filter((st) => counts[st])
    .map((st) => `${counts[st]} ${CAMERA_STATUS[st].label.toLowerCase()}`)
    .join(' · ');
  const title = escapeHtml(`${n} cameras · ${breakdown}`);
  const cls = flash ? 'sg-cluster sg-marker-flash' : 'sg-cluster';
  const style = `--ring:${CAMERA_STATUS[worst].colour}${flash ? `;--flash:${flash}` : ''}`;
  return L.divIcon({
    className: '',
    html: `<div class="${cls}" style="${style}" title="${title}" role="img" aria-label="${title}">${n}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

interface ClusterLayerProps {
  cameras: MapCamera[];
  cluster?: boolean;
  styleOpts?: MarkerStyleOptions;
  onClick?: (c: MapCamera) => void;
  popupHtml?: (c: MapCamera) => string;
}

/** Camera markers with optional leaflet.markercluster grouping (≥ 50 markers by default). */
export function CameraMarkers({ cameras, cluster, styleOpts, onClick, popupHtml }: ClusterLayerProps) {
  const map = useMap();
  useEffect(() => {
    const useCluster = cluster ?? cameras.length >= 50;
    const group: L.LayerGroup = useCluster
      ? L.markerClusterGroup({ maxClusterRadius: 40, showCoverageOnHover: false, spiderfyOnMaxZoom: true, disableClusteringAtZoom: 14, iconCreateFunction: clusterIcon })
      : L.layerGroup();
    cameras.forEach((c) => {
      const m: StatusMarker = L.marker([c.lat, c.lon], { icon: cameraIcon(c, styleOpts), title: markerTitle(c), keyboard: true, alt: markerTitle(c) });
      m.sgStatus = displayStatus(c.status, c.live);
      m.sgFlash = styleOpts?.flashId === c.id ? styleOpts.flashColour ?? '#DC2626' : null;
      if (popupHtml) m.bindPopup(popupHtml(c), { className: 'sg-popup', maxWidth: 300 });
      if (onClick) m.on('click', () => onClick(c));
      group.addLayer(m);
    });
    group.addTo(map);
    return () => {
      map.removeLayer(group);
    };
  }, [map, cameras, cluster, styleOpts, onClick, popupHtml]);
  return null;
}

/**
 * Alert "map flash" (CONTRACT §11.3): the camera of the most recent live alert, with the priority colour,
 * for `ms` after it arrives. Feed the result into `styleOpts` of `CameraMarkers`.
 */
export function useAlertFlash(ms = 2600): { flashId: number | null; flashColour: string | undefined; camera: { id: number; lat: number | null; lon: number | null } | null } {
  const lastAlertAt = useUiStore((s) => s.lastAlertAt);
  const [flash, setFlash] = useState<{ id: number; colour: string; lat: number | null; lon: number | null } | null>(null);
  useEffect(() => {
    if (!lastAlertAt) return undefined;
    const latest = useUiStore.getState().liveAlerts[0];
    if (!latest) return undefined;
    setFlash({ id: latest.camera.id, colour: ALERT_PRIORITY[latest.priority]?.colour ?? '#DC2626', lat: latest.camera.lat, lon: latest.camera.lon });
    const t = setTimeout(() => setFlash(null), ms);
    return () => clearTimeout(t);
  }, [lastAlertAt, ms]);
  return { flashId: flash?.id ?? null, flashColour: flash?.colour, camera: flash ? { id: flash.id, lat: flash.lat, lon: flash.lon } : null };
}

/** Pans the map (no zoom change) to the flashing alert camera so the pulse is on screen. */
export function PanToFlash({ camera }: { camera: { id: number; lat: number | null; lon: number | null } | null }) {
  const map = useMap();
  useEffect(() => {
    if (!camera || camera.lat === null || camera.lon === null) return;
    const target = L.latLng(camera.lat, camera.lon);
    if (!map.getBounds().pad(-0.1).contains(target)) map.panTo(target, { animate: true });
  }, [camera, map]);
  return null;
}

/** Simple numbered marker for route pages. */
export function numberedIcon(n: number | string, fuzzy = false, stops = 1): L.DivIcon {
  const badge = stops > 1 ? `<span class="sg-route-marker-count" title="${stops} stops at this camera">${stops}</span>` : '';
  return L.divIcon({
    className: '',
    html: `<div class="sg-route-marker ${fuzzy ? 'sg-route-marker-fuzzy' : ''}">${n}${badge}</div>`,
    iconSize: [26, 26],
    iconAnchor: [13, 13],
    popupAnchor: [0, -13],
  });
}

export function escapeHtml(s: string | null | undefined): string {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] ?? c);
}
