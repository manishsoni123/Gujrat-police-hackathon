/**
 * Shared Leaflet building blocks: base map, camera markers (status colour,
 * department ring, maintenance dash, ANPR glyph), clustering, fit-to-bounds.
 */
import { useEffect, useMemo, type ReactNode } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import 'leaflet.markercluster';
import 'leaflet.markercluster/dist/MarkerCluster.css';
import 'leaflet.markercluster/dist/MarkerCluster.Default.css';
import { MapContainer, TileLayer, useMap } from './leaflet';
import { CAMERA_STATUS, MAINTENANCE_STATUS, departmentColour, type CameraStatus, type MaintenanceStatus } from '@/theme/colours';
import { GUJARAT_CENTER, GUJARAT_ZOOM, OSM_ATTRIBUTION, OSM_TILES, boundsOf } from '@/utils/geo';

export interface MapCamera {
  id: number;
  name: string;
  lat: number;
  lon: number;
  status: CameraStatus;
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
  flashId?: number | null;
  flashColour?: string;
}

export function cameraIcon(c: MapCamera, opts: MarkerStyleOptions = {}): L.DivIcon {
  const status = CAMERA_STATUS[c.status]?.colour ?? '#9CA3AF';
  const selected = opts.selectedId === c.id;
  const size = selected ? 14 : 10;
  const classes = ['sg-marker'];
  const styles = [`background:${status}`, `width:${size}px`, `height:${size}px`];
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

/** Marker with the camera status attached so cluster icons can summarise what they contain. */
type StatusMarker = L.Marker & { sgStatus?: CameraStatus };

const WORST_FIRST: CameraStatus[] = ['offline', 'degraded', 'unknown', 'retired', 'online'];

/**
 * Cluster icon: neutral primary fill with the count, a ring in the worst status colour inside the
 * cluster and a title with the full breakdown ("12 cameras · 3 online · 9 offline"), replacing the
 * library's green/yellow/orange size-based colours which read as health on a status-coloured map.
 */
export function clusterIcon(cluster: L.MarkerCluster): L.DivIcon {
  const markers = cluster.getAllChildMarkers() as StatusMarker[];
  const counts: Partial<Record<CameraStatus, number>> = {};
  markers.forEach((m) => {
    const st = m.sgStatus ?? 'unknown';
    counts[st] = (counts[st] ?? 0) + 1;
  });
  const worst = WORST_FIRST.find((st) => (counts[st] ?? 0) > 0) ?? 'unknown';
  const n = markers.length;
  const size = n < 10 ? 34 : n < 100 ? 40 : 46;
  const breakdown = WORST_FIRST.filter((st) => counts[st])
    .map((st) => `${counts[st]} ${CAMERA_STATUS[st].label.toLowerCase()}`)
    .join(' · ');
  const title = escapeHtml(`${n} cameras · ${breakdown}`);
  return L.divIcon({
    className: '',
    html: `<div class="sg-cluster" style="--ring:${CAMERA_STATUS[worst].colour}" title="${title}" role="img" aria-label="${title}">${n}</div>`,
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
      const m: StatusMarker = L.marker([c.lat, c.lon], { icon: cameraIcon(c, styleOpts), title: c.name, keyboard: true, alt: c.name });
      m.sgStatus = c.status;
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

/** Simple numbered marker for route pages. */
export function numberedIcon(n: number, fuzzy = false): L.DivIcon {
  return L.divIcon({
    className: '',
    html: `<div class="sg-route-marker ${fuzzy ? 'sg-route-marker-fuzzy' : ''}">${n}</div>`,
    iconSize: [26, 26],
    iconAnchor: [13, 13],
    popupAnchor: [0, -13],
  });
}

export function escapeHtml(s: string | null | undefined): string {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] ?? c);
}
