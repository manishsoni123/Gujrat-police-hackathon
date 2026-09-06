/**
 * Deterministic in-memory dataset for the mock API (VITE_MOCK=1). Shapes follow
 * docs/CONTRACT.md §5 exactly so pages render identically against the real API.
 * Times are generated relative to "now" so 24 h windows are always populated.
 */
import type {
  Alert,
  AlertDetail,
  ApiKey,
  AuditRow,
  Camera,
  CameraDetail,
  CameraSummary,
  Codec,
  CameraType,
  DashboardCharts,
  DashboardStats,
  Department,
  Detection,
  DetectionDetail,
  EventItem,
  EventType,
  GapAnalysis,
  GeoCameraProps,
  GeoCoverageProps,
  GeoDistrictProps,
  GeoFeatureCollection,
  GeoPoiProps,
  HealthSummary,
  QualityReport,
  ReportFile,
  SettingItem,
  Sighting,
  UserRow,
  WatchlistEntry,
  Webhook,
} from '@/api/types';
import type { AlertPriority, CameraStatus, MaintenanceStatus, WatchlistReason } from '@/theme/colours';
import { cameraFrame, plateCrop, snapshot } from './images';
import { formatPlate } from '@/utils/plate';
import { haversineKm } from '@/utils/geo';

/* ------------------------------------------------------------------ rng */

export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rnd = mulberry32(42);
const pick = <T,>(arr: T[]): T => arr[Math.floor(rnd() * arr.length)];
const between = (a: number, b: number) => a + rnd() * (b - a);
const NOW = Date.now();
const iso = (ms: number) => new Date(ms).toISOString();
const minutesAgo = (m: number) => iso(NOW - m * 60_000);
const hoursAgo = (h: number) => iso(NOW - h * 3_600_000);
const sha = (seed: string) => {
  let h1 = 0x811c9dc5;
  let out = '';
  for (let i = 0; i < 64; i += 1) {
    h1 = Math.imul(h1 ^ seed.charCodeAt(i % seed.length) ^ i, 16777619) >>> 0;
    out += (h1 & 15).toString(16);
  }
  return out;
};
const istTime = (ms: number) => new Date(ms + 5.5 * 3_600_000).toISOString().slice(11, 19);

/* ------------------------------------------------------------------ departments */

export const departments: Department[] = [
  { id: 1, code: 'UNASSIGNED', name: 'Unassigned' },
  { id: 2, code: 'POLICE', name: 'Gujarat Police' },
  { id: 3, code: 'HEALTH', name: 'Health & Family Welfare' },
  { id: 4, code: 'GSRTC', name: 'Gujarat State Road Transport Corporation' },
  { id: 5, code: 'PANCHAYAT', name: 'Panchayat & Rural Development' },
  { id: 6, code: 'MUNICIPAL', name: 'Urban Development & Municipal Corporations' },
  { id: 7, code: 'RTO', name: 'Transport (RTO)' },
  { id: 8, code: 'FCS', name: 'Food & Civil Supplies' },
  { id: 9, code: 'EDU', name: 'Education' },
  { id: 10, code: 'FOREST', name: 'Forests & Environment' },
  { id: 11, code: 'RNB', name: 'Roads & Buildings' },
  { id: 12, code: 'GIDC', name: 'Industries (GIDC)' },
  { id: 13, code: 'GMB', name: 'Ports (Gujarat Maritime Board)' },
];
const deptByCode = new Map(departments.map((d) => [d.code, d]));
const DEPT_ALIAS: Record<string, string> = { Police: 'POLICE', Health: 'HEALTH', GSRTC: 'GSRTC', Panchayat: 'PANCHAYAT', 'Municipal Corporation': 'MUNICIPAL' };

/* ------------------------------------------------------------------ cameras (CONTRACT §6.2) */

type Row = [number, string, string, string, number, number, Codec, string, number, boolean, CameraType];
const CATALOGUE: Row[] = [
  [1, 'Sachivalaya Gate 1', 'Police', 'Gandhinagar', 23.2236, 72.648, 'H264', '1280x720', 10, true, 'bullet'],
  [2, 'Sector 11 GSRTC Bus Stand', 'GSRTC', 'Gandhinagar', 23.23, 72.63, 'H264', '1280x720', 10, true, 'dome'],
  [3, 'Civil Hospital Gandhinagar OPD Gate', 'Health', 'Gandhinagar', 23.2275, 72.6485, 'H264', '1280x720', 10, true, 'bullet'],
  [4, 'Infocity Circle', 'Municipal Corporation', 'Gandhinagar', 23.189, 72.635, 'H264', '1280x720', 10, true, 'ptz'],
  [5, 'Pethapur Gram Panchayat Chowk', 'Panchayat', 'Gandhinagar', 23.267, 72.652, 'H264', '1280x720', 10, true, 'bullet'],
  [6, 'CH-0 Circle', 'Police', 'Gandhinagar', 23.217, 72.636, 'H264', '1280x720', 10, true, 'anpr'],
  [7, 'Mahatma Mandir Approach', 'Municipal Corporation', 'Gandhinagar', 23.2247, 72.654, 'H264', '1280x720', 10, true, 'dome'],
  [8, 'Koba Circle Highway', 'Police', 'Gandhinagar', 23.165, 72.655, 'H265', '1280x720', 10, true, 'anpr'],
  [9, 'Kalupur Railway Station East', 'Police', 'Ahmedabad', 23.0269, 72.6013, 'H264', '1920x1080', 25, false, 'bullet'],
  [10, 'Civil Hospital Asarwa Trauma Gate', 'Health', 'Ahmedabad', 23.0532, 72.6067, 'H265', '1920x1080', 25, false, 'dome'],
  [11, 'Gita Mandir Central Bus Station', 'GSRTC', 'Ahmedabad', 23.009, 72.596, 'H264', '1920x1080', 25, false, 'ptz'],
  [12, 'Iskcon Cross Roads SG Highway', 'Municipal Corporation', 'Ahmedabad', 23.028, 72.507, 'H264', '1920x1080', 25, false, 'anpr'],
  [13, 'Sabarmati Riverfront Ellis Bridge', 'Municipal Corporation', 'Ahmedabad', 23.0225, 72.5745, 'H265', '1920x1080', 25, false, 'ptz'],
  [14, 'Narol Circle', 'Police', 'Ahmedabad', 22.966, 72.616, 'H264', '1280x720', 25, false, 'anpr'],
  [15, 'Vastral Gram Panchayat Road', 'Panchayat', 'Ahmedabad', 23.006, 72.663, 'H264', '1280x720', 15, false, 'bullet'],
  [16, 'Pakwan Cross Roads SG Highway', 'Police', 'Ahmedabad', 23.047, 72.515, 'H265', '1920x1080', 25, false, 'anpr'],
  [17, 'Airport Road Hansol', 'Police', 'Ahmedabad', 23.074, 72.63, 'H264', '1920x1080', 25, false, 'bullet'],
  [18, 'Lal Darwaja AMTS Terminus', 'Municipal Corporation', 'Ahmedabad', 23.025, 72.582, 'H264', '1280x720', 15, false, 'dome'],
  [19, 'Vadodara Railway Station Circle', 'Police', 'Vadodara', 22.3103, 73.1815, 'H264', '1920x1080', 25, false, 'anpr'],
  [20, 'SSG Hospital Emergency Gate', 'Health', 'Vadodara', 22.3086, 73.1898, 'H265', '1920x1080', 25, false, 'dome'],
  [21, 'Vadodara Central Bus Stand', 'GSRTC', 'Vadodara', 22.3125, 73.1795, 'H264', '1920x1080', 25, false, 'ptz'],
  [22, 'Alkapuri Circle', 'Municipal Corporation', 'Vadodara', 22.3105, 73.169, 'H264', '1280x720', 25, false, 'bullet'],
  [23, 'Makarpura GIDC Gate', 'Panchayat', 'Vadodara', 22.254, 73.188, 'H264', '1280x720', 15, false, 'bullet'],
  [24, 'Akota Stadium Road', 'Municipal Corporation', 'Vadodara', 22.296, 73.165, 'H265', '1920x1080', 25, false, 'dome'],
  [25, 'Surat Railway Station Approach', 'Police', 'Surat', 21.2049, 72.8411, 'H264', '1920x1080', 25, false, 'anpr'],
  [26, 'New Civil Hospital Majura Gate', 'Health', 'Surat', 21.182, 72.8207, 'H264', '1920x1080', 25, false, 'dome'],
  [27, 'Surat Central Bus Station', 'GSRTC', 'Surat', 21.202, 72.838, 'H265', '1920x1080', 25, false, 'ptz'],
  [28, 'Athwa Gate Circle', 'Municipal Corporation', 'Surat', 21.179, 72.8112, 'H264', '1920x1080', 25, false, 'anpr'],
  [29, 'Udhna Darwaja', 'Police', 'Surat', 21.183, 72.838, 'H264', '1280x720', 25, false, 'bullet'],
  [30, 'Hazira Road Ichchhapore', 'Panchayat', 'Surat', 21.16, 72.71, 'H264', '1280x720', 15, false, 'bullet'],
  [31, 'Rajkot Junction Forecourt', 'Police', 'Rajkot', 22.3095, 70.7949, 'H264', '1920x1080', 25, false, 'anpr'],
  [32, 'PDU Civil Hospital Rajkot', 'Health', 'Rajkot', 22.2988, 70.7929, 'H265', '1920x1080', 25, false, 'dome'],
  [33, 'Rajkot ST Bus Stand', 'GSRTC', 'Rajkot', 22.294, 70.798, 'H264', '1920x1080', 25, false, 'ptz'],
  [34, 'Trikon Baug', 'Municipal Corporation', 'Rajkot', 22.2975, 70.799, 'H264', '1280x720', 25, false, 'bullet'],
  [35, 'Kalawad Road Panchayat Nagar', 'Panchayat', 'Rajkot', 22.29, 70.76, 'H264', '1280x720', 15, false, 'bullet'],
  [36, 'Gondal Road Chowkdi', 'Police', 'Rajkot', 22.27, 70.8, 'H265', '1920x1080', 25, false, 'anpr'],
  [37, 'Vapi GIDC Checkpost', 'Police', 'Valsad', 20.3893, 72.9106, 'H264', '1280x720', 15, false, 'anpr'],
  [38, 'Valsad Civil Hospital', 'Health', 'Valsad', 20.6097, 72.9342, 'H264', '1280x720', 15, false, 'dome'],
  [39, 'Bhilad Border Checkpost', 'Police', 'Valsad', 20.2617, 72.9147, 'H264', '704x576', 12, false, 'analog'],
  [40, 'Dahod Bus Stand', 'GSRTC', 'Dahod', 22.8347, 74.2555, 'H264', '1280x720', 15, false, 'bullet'],
  [41, 'Zalod Gram Panchayat', 'Panchayat', 'Dahod', 23.1005, 74.1685, 'H264', '704x576', 12, false, 'analog'],
  [42, 'Limkheda NH-47 Checkpost', 'Police', 'Dahod', 22.8176, 73.9773, 'H265', '1280x720', 15, false, 'anpr'],
  [43, 'Somnath Temple Road', 'Police', 'Gir Somnath', 20.888, 70.4013, 'H264', '1920x1080', 25, false, 'ptz'],
  [44, 'Veraval GSRTC Depot', 'GSRTC', 'Gir Somnath', 20.9077, 70.3665, 'H264', '1280x720', 15, false, 'bullet'],
  [45, 'Veraval Civil Hospital', 'Health', 'Gir Somnath', 20.91, 70.37, 'H264', '704x576', 12, false, 'analog'],
  [46, 'GG Hospital Jamnagar', 'Health', 'Jamnagar', 22.4707, 70.0577, 'H265', '1920x1080', 25, false, 'dome'],
  [47, 'Bedi Port Road Checkpost', 'Police', 'Jamnagar', 22.5, 70.05, 'H264', '1280x720', 15, false, 'anpr'],
  [48, 'Lalpur Gram Panchayat', 'Panchayat', 'Jamnagar', 22.19, 70.03, 'H264', '704x576', 12, false, 'analog'],
  [49, 'Dwarka Temple Gate', 'Police', 'Devbhumi Dwarka', 22.2376, 68.9674, 'H264', '1920x1080', 25, false, 'ptz'],
  [50, 'Khambhaliya GSRTC Depot', 'GSRTC', 'Devbhumi Dwarka', 22.2095, 69.6519, 'H264', '1280x720', 15, false, 'bullet'],
];

const PS: Record<string, string[]> = {
  Gandhinagar: ['Sector 7', 'Sector 21', 'Pethapur', 'Infocity', 'Adalaj'],
  Ahmedabad: ['Kalupur', 'Shahibaug', 'Satellite', 'Narol', 'Vastral'],
  Vadodara: ['Raopura', 'Sayajigunj', 'Makarpura', 'Gotri'],
  Surat: ['Athwa', 'Umra', 'Udhna', 'Ichchhapore'],
  Rajkot: ['A Division', 'Pradyuman Nagar', 'Gondal Road'],
  Valsad: ['Vapi Town', 'Valsad City', 'Bhilad'],
  Dahod: ['Dahod Town', 'Zalod', 'Limkheda'],
  'Gir Somnath': ['Somnath', 'Veraval'],
  Jamnagar: ['Bedi', 'City A', 'Lalpur'],
  'Devbhumi Dwarka': ['Dwarka', 'Khambhaliya'],
};
const VENDORS = [['Hikvision', 'DS-2CD2043G2'], ['Dahua', 'IPC-HFW2431S'], ['CP Plus', 'CP-UNC-TA21L3'], ['Axis', 'P1455-LE'], ['Bosch', 'DINION 5100i']];

function makeCamera(r: Row): Camera {
  const [id, name, deptDisplay, district, lat, lon, codec, resolution, fps, live, type] = r;
  const dept = deptByCode.get(DEPT_ALIAS[deptDisplay] ?? 'UNASSIGNED') as Department;
  // Catalogue live=false cameras never deliver a stream: `not_streaming`, never `offline` (CONTRACT amendment 2026-09-05 §5.6).
  const status: CameraStatus = live ? (id === 5 ? 'degraded' : 'online') : 'not_streaming';
  const [vendor, model] = VENDORS[id % VENDORS.length];
  const installYear = 2016 + (id % 9);
  const amcExpiryDays = id % 5 === 0 ? -40 : id % 3 === 0 ? 16 : 300;
  const amcExpiry = new Date(NOW + amcExpiryDays * 86_400_000).toISOString().slice(0, 10);
  const maintenance: MaintenanceStatus = id === 41 ? 'faulty' : id === 23 ? 'under_maintenance' : 'ok';
  const anpr = live;
  const uptime = live ? (id === 5 ? 71.4 : 96 + (id % 4)) : id === 30 ? 12.5 : 0;
  return {
    id,
    external_id: String(id),
    name,
    department_id: dept.id,
    department_code: dept.code,
    department_name: dept.name,
    district,
    police_station: PS[district][id % PS[district].length],
    lat,
    lon,
    status,
    type,
    source: 'sandbox',
    ownership: id % 7 === 0 ? 'public_facing' : 'govt_dept',
    address: `${name}, ${district}`,
    ward: district === 'Ahmedabad' || district === 'Surat' ? `Ward ${(id % 12) + 1}` : null,
    rtsp_url: `rtsp://mediamtx:8554/stream/${id}`,
    whep_url: `http://mediamtx:8889/stream/${id}/whep`,
    hls_url: `http://mediamtx:8888/stream/${id}/index.m3u8`,
    relay_path: `cam_${id}`,
    codec,
    resolution,
    fps,
    live,
    storage_location: id % 2 ? 'NVR at police station' : 'Departmental VMS',
    retention_days: id % 4 === 0 ? null : [7, 15, 30][id % 3],
    install_date: `${installYear}-${String((id % 12) + 1).padStart(2, '0')}-15`,
    vendor,
    model,
    heading_deg: (id * 37) % 360,
    fov_deg: 70 + (id % 3) * 10,
    connectivity_type: type === 'analog' ? '4g' : id % 3 === 0 ? 'fibre' : 'lan',
    bandwidth_kbps: type === 'analog' ? 2000 : 4096,
    vms_platform: id % 3 === 0 ? 'Milestone XProtect' : 'none',
    nvr_id: `NVR-${district.slice(0, 3).toUpperCase()}-${String(id).padStart(2, '0')}`,
    onvif_host: null,
    anpr_enabled: anpr,
    record_enabled: anpr,
    location_confidence: null,
    metadata: null,
    last_seen_at: live ? minutesAgo(id === 5 ? 3 : 1) : id === 30 ? minutesAgo(35) : hoursAgo(26 + id),
    last_status_change_at: live ? hoursAgo(5) : id === 30 ? minutesAgo(35) : hoursAgo(26 + id),
    maintenance_status: maintenance,
    last_maintenance_at: maintenance === 'ok' ? null : hoursAgo(24 * 9),
    maintenance_note: maintenance === 'faulty' ? 'IR cut filter stuck; replacement ordered' : maintenance === 'under_maintenance' ? 'Cable re-routing at GIDC gate' : null,
    amc_vendor: id % 5 === 0 ? 'Secure Vision AMC' : id % 3 === 0 ? 'Dahua AMC' : null,
    amc_expiry: id % 5 === 0 || id % 3 === 0 ? amcExpiry : null,
    created_by: null,
    created_via: 'sandbox',
    created_at: hoursAgo(30),
    updated_at: hoursAgo(1),
    retired_at: null,
    uptime_24h_pct: uptime,
    age_years: Number(((NOW - Date.parse(`${installYear}-01-15`)) / (365.25 * 86_400_000)).toFixed(1)),
    amc_status: id % 5 === 0 ? 'expired' : id % 3 === 0 ? 'expiring' : 'none',
    created_by_username: null,
  };
}

export const cameras: Camera[] = CATALOGUE.map(makeCamera);
cameras.push({
  ...makeCamera([51, 'Dynatech Office Gate (private society camera)', 'Police', 'Ahmedabad', 23.033, 72.515, 'H264', '1920x1080', 25, true, 'ip']),
  external_id: 'OWN-GATE-01',
  source: 'own',
  ownership: 'private',
  address: 'Dynatech Consultancy, Satellite, Ahmedabad',
  police_station: 'Satellite',
  rtsp_url: 'rtsp://mediamtx:8554/own_gate',
  whep_url: null,
  hls_url: null,
  live: null,
  connectivity_type: 'wifi',
  vms_platform: 'none',
  created_via: 'manual',
  created_by: 1,
  created_by_username: 'jury_admin',
  install_date: '2024-06-01',
  age_years: 2.3,
  amc_status: 'none',
  amc_vendor: null,
  amc_expiry: null,
  uptime_24h_pct: 99.2,
});
cameras.push({
  ...makeCamera([52, 'Okha Checkpost', 'Police', 'Devbhumi Dwarka', 22.468, 69.07, 'H264', '704x576', 12, false, 'analog']),
  external_id: 'CSV-006',
  source: 'csv',
  police_station: 'Okha',
  rtsp_url: null,
  whep_url: null,
  hls_url: null,
  relay_path: null,
  live: null,
  status: 'unknown',
  maintenance_status: 'faulty',
  maintenance_note: 'Housing damaged in cyclone; awaiting replacement',
  last_maintenance_at: hoursAgo(24 * 30),
  amc_vendor: 'Coastal AMC Services',
  amc_expiry: '2025-12-31',
  amc_status: 'expired',
  install_date: '2016-03-01',
  age_years: 10.5,
  created_via: 'csv',
  created_by: 1,
  created_by_username: 'jury_admin',
  uptime_24h_pct: null,
  last_seen_at: null,
  retention_days: 15,
  vendor: 'Zicom',
  model: 'ZC-AN-720',
});

export const cameraById = new Map(cameras.map((c) => [c.id, c]));
export function summary(c: Camera): CameraSummary {
  return { id: c.id, external_id: c.external_id, name: c.name, department_id: c.department_id, department_code: c.department_code, department_name: c.department_name, district: c.district, police_station: c.police_station, lat: c.lat, lon: c.lon, status: c.status, type: c.type };
}
const liveCams = cameras.filter((c) => c.anpr_enabled);
const snapshotTs = istTime(NOW);
export function snapshotUrl(c: Camera): string | null {
  if (!c.anpr_enabled) return null;
  return snapshot(c.id, c.name, c.id % 2 ? 'GJ 01 AB 1234' : null, snapshotTs);
}

export function cameraDetail(c: Camera): CameraDetail {
  return {
    ...c,
    recent_alerts: alerts.filter((a) => a.camera.id === c.id && a.status !== 'closed').length,
    reads_24h: detections.filter((d) => d.camera.id === c.id).length,
    sightings_24h: sightings.filter((s) => s.camera.id === c.id).length,
    snapshot_url: snapshotUrl(c),
    zones: c.id === 51 ? [{ id: 1, camera_id: 51, name: 'Gate apron', polygon: [[0.1, 0.5], [0.9, 0.5], [0.9, 0.95], [0.1, 0.95]], active_from: '22:00', active_to: '06:00', classes: ['person'], dwell_s: 2, priority: 'medium', is_active: true }] : [],
  };
}

/* ------------------------------------------------------------------ plates, sightings, reads */

interface PlateDef {
  plate: string;
  twoLine: boolean;
  anchor: boolean;
}
const ANCHORS: PlateDef[] = [
  { plate: 'GJ01AB1234', twoLine: false, anchor: true },
  { plate: 'GJ18CD5678', twoLine: false, anchor: true },
  { plate: 'GJ05RS9012', twoLine: false, anchor: true },
  { plate: 'GJ27XY3456', twoLine: false, anchor: true },
  { plate: 'MH02BZ7788', twoLine: true, anchor: true },
  { plate: '22BH4321AA', twoLine: true, anchor: true },
];
const SERIES = 'ABCDEFGHJKLMNPRSTUVWXYZ';
const fillers: PlateDef[] = Array.from({ length: 18 }, () => {
  const d = String(1 + Math.floor(rnd() * 38)).padStart(2, '0');
  const s = pick(SERIES.split('')) + (rnd() > 0.5 ? pick(SERIES.split('')) : '');
  const n = String(1000 + Math.floor(rnd() * 9000));
  return { plate: `GJ${d}${s}${n}`, twoLine: rnd() < 0.3, anchor: false };
});
const PLATES = [...ANCHORS, ...fillers];
const plateDef = new Map(PLATES.map((p) => [p.plate, p]));

export const sightings: Sighting[] = [];
export const detections: Detection[] = [];
let readId = 9000;
let sightingId = 500;

/** Anchor route schedule (camera, seconds into the 90 s loop) from CONTRACT §12.4. */
const SCHEDULE: Record<string, [number, number][]> = {
  GJ01AB1234: [[1, 5], [3, 25], [6, 50], [2, 72]],
  GJ18CD5678: [[4, 10], [7, 35], [8, 60]],
  GJ05RS9012: [[2, 15], [5, 40], [1, 65]],
  GJ27XY3456: [[6, 8], [3, 30], [7, 55], [4, 80]],
  MH02BZ7788: [[8, 20], [6, 45]],
  '22BH4321AA': [[5, 12], [2, 48], [8, 78]],
};

function addSighting(cam: Camera, plate: string, firstMs: number, conf: number, mode: 'live' | 'preindex' = 'live'): Sighting {
  const def = plateDef.get(plate);
  const display = formatPlate(plate);
  const readCount = 1 + Math.floor(rnd() * 3);
  const lastMs = firstMs + 1500 + readCount * 1700;
  sightingId += 1;
  const sid = sightingId;
  const reads: Detection[] = [];
  for (let i = 0; i < readCount; i += 1) {
    readId += 1;
    const captured = firstMs + i * 1700;
    const c = Math.min(0.99, conf - i * 0.03 + rnd() * 0.04);
    const rawNoise = i === readCount - 1 && rnd() < 0.25 ? display.replace('B', '8').replace('0', 'O') : display;
    reads.push({
      id: readId,
      camera: summary(cam),
      sighting_id: sid,
      captured_at: iso(captured),
      stream_pts: Number(((captured / 1000) % 90).toFixed(1)),
      frame_index: Math.floor((captured / 1000) % 90) * 10,
      plate_raw: def?.twoLine ? rawNoise.replace(/^(\S+ \S+) /, '$1  ') : rawNoise,
      plate_norm: plate,
      plate_display: display,
      is_valid_format: true,
      confidence: Number(c.toFixed(2)),
      bbox: [400 + Math.floor(rnd() * 100), 380 + Math.floor(rnd() * 40), 236, 64],
      crop_url: plateCrop(display, def?.twoLine ?? false, i === readCount - 1 ? 0.6 : 0),
      crop_sha256: sha(`${plate}-${sid}-${i}`),
      mode,
      watchlist_hit: false,
      alert_id: null,
      qa_label: null,
    });
  }
  const best = reads.reduce((a, b) => (b.confidence > a.confidence ? b : a));
  const s: Sighting = {
    id: sid,
    camera: summary(cam),
    plate_norm: plate,
    plate_display: display,
    is_valid_format: true,
    first_seen: iso(firstMs),
    last_seen: iso(lastMs),
    read_count: readCount,
    best_conf: best.confidence,
    best_read_id: best.id,
    best_crop_url: best.crop_url,
    frame_url: cameraFrame(cam.id, cam.name, display, istTime(firstMs)),
    frame_sha256: sha(`frame-${sid}`),
    closed: lastMs < NOW - 15_000,
    mode,
    recording_available: cam.anpr_enabled,
  };
  sightings.push(s);
  detections.push(...reads);
  return s;
}

// Anchors: several loop passes over the last 3 hours (loop = 90 s; sampled every ~19 min to keep volumes sane)
for (const [plate, sched] of Object.entries(SCHEDULE)) {
  for (let pass = 0; pass < 9; pass += 1) {
    const loopStart = NOW - (12 + pass * 19) * 60_000;
    sched.forEach(([camId, sec]) => {
      const cam = cameraById.get(camId) as Camera;
      addSighting(cam, plate, loopStart + sec * 1000, 0.86 + rnd() * 0.12);
    });
  }
}
// Fillers spread over 24 h on live cameras + own gate
for (let i = 0; i < 260; i += 1) {
  const cam = pick(liveCams);
  const def = pick(fillers);
  addSighting(cam, def.plate, NOW - between(2, 24 * 60) * 60_000, 0.55 + rnd() * 0.42, rnd() < 0.3 ? 'preindex' : 'live');
}
// A few invalid-format raw reads
for (let i = 0; i < 6; i += 1) {
  const cam = pick(liveCams);
  const raw = pick(['GJ01ABCD1234', 'S5S5S5S5', 'GJ1AB', 'IND GJ01 A', 'KA0', 'MH12DE14O3X']);
  const norm = raw.toUpperCase().replace(/[^A-Z0-9]/g, '').replace(/^IND/, '');
  readId += 1;
  detections.push({
    id: readId,
    camera: summary(cam),
    sighting_id: null,
    captured_at: minutesAgo(between(5, 900)),
    stream_pts: 12.3,
    frame_index: 123,
    plate_raw: raw,
    plate_norm: norm,
    plate_display: norm,
    is_valid_format: false,
    confidence: Number((0.3 + rnd() * 0.3).toFixed(2)),
    bbox: [410, 388, 200, 60],
    crop_url: plateCrop(raw, false, 1.4),
    crop_sha256: sha(`inv-${i}`),
    mode: 'live',
    watchlist_hit: false,
    alert_id: null,
    qa_label: null,
  });
}
sightings.sort((a, b) => Date.parse(b.first_seen) - Date.parse(a.first_seen));
detections.sort((a, b) => Date.parse(b.captured_at) - Date.parse(a.captured_at));
export const detectionById = new Map(detections.map((d) => [d.id, d]));
export const sightingById = new Map(sightings.map((s) => [s.id, s]));

export function detectionDetail(d: Detection): DetectionDetail {
  const s = d.sighting_id ? sightingById.get(d.sighting_id) ?? null : null;
  return { ...d, sighting: s, frame_url: s?.frame_url ?? null, frame_sha256: s?.frame_sha256 ?? null, events: events.filter((e) => e.read_id === d.id || (d.sighting_id && e.sighting_id === d.sighting_id)) };
}

/* ------------------------------------------------------------------ watchlist (CONTRACT §12.2) */

type WlRow = [string, 'vehicle' | 'person', string, WatchlistReason, AlertPriority, WatchlistEntry['source'], string, string | null, boolean];
const WL: WlRow[] = [
  ['GJ01AB1234', 'vehicle', 'White Maruti Swift – FIR 123/2026 Sector 7 PS', 'stolen', 'critical', 'own', 'Reported stolen 2026-08-30 from Sector 7 Gandhinagar', null, true],
  ['GJ18CD5678', 'vehicle', 'Grey Hyundai Creta – wanted in Kalupur case', 'wanted', 'critical', 'egujcop', 'eGujCop reference EGC-2026-004411', null, true],
  ['GJ05RS9012', 'vehicle', 'Blacklisted goods carrier (overloading)', 'blacklisted', 'high', 'manual', 'RTO blacklist – repeat offender', null, true],
  ['MH02BZ7788', 'vehicle', "Silver Honda City – missing person's vehicle", 'missing', 'medium', 'own', 'Missing person report MPR-2026-0912', null, true],
  ['GJ06KL4455', 'vehicle', 'Suspect vehicle – chain snatching pattern', 'suspect', 'low', 'manual', 'Seen near Infocity thrice', '2026-12-31T18:30:00Z', true],
  ['GJ27XY3456', 'vehicle', 'Demo plate (added live during the jury demo)', 'suspect', 'low', 'manual', 'Not active by default – the demo adds it', null, false],
  ['GJ01CJ7788', 'vehicle', 'Black Toyota Fortuner – stolen', 'stolen', 'critical', 'own', '', null, true],
  ['GJ03BM2210', 'vehicle', 'Red Bajaj Pulsar – stolen two-wheeler', 'stolen', 'high', 'own', '', null, true],
  ['GJ05JE9834', 'vehicle', 'Wanted – Surat cheating case', 'wanted', 'critical', 'egujcop', '', null, true],
  ['GJ12AK1001', 'vehicle', 'Blacklisted taxi – permit cancelled', 'blacklisted', 'medium', 'vahan', '', null, true],
  ['GJ33AT5566', 'vehicle', 'Suspect – border movement Dahod', 'suspect', 'low', 'manual', '', null, true],
  ['GJ38CH0007', 'vehicle', 'Suspect – Valsad narcotics watch', 'suspect', 'medium', 'manual', '', null, true],
  ['DL3CAB9911', 'vehicle', 'Wanted – interstate', 'wanted', 'high', 'egujcop', '', null, true],
  ['RJ14CV3030', 'vehicle', 'Stolen in Rajasthan – NCRB advisory', 'stolen', 'high', 'egujcop', '', null, true],
  ['MP09HB6161', 'vehicle', 'Blacklisted – toll evasion', 'blacklisted', 'low', 'manual', '', null, true],
  ['GJ10AD7070', 'vehicle', 'Missing – elderly driver', 'missing', 'medium', 'own', '', null, true],
  ['GJ15CQ2424', 'vehicle', 'Suspect – Rajkot burglary pattern', 'suspect', 'low', 'manual', '', '2026-10-31T18:30:00Z', true],
  ['GJ21AR8181', 'vehicle', "Arrested person's vehicle (impound pending)", 'arrested', 'high', 'egujcop', '', null, true],
  ['KA01MJ4545', 'vehicle', 'Wanted – Bengaluru case (interstate lookout)', 'wanted', 'high', 'egujcop', '', null, true],
  ['GJ09BW0110', 'vehicle', 'Expired entry (kept for history)', 'blacklisted', 'low', 'manual', '', '2026-08-01T00:00:00Z', true],
  ['', 'person', 'Ramesh K. (wanted – Ahmedabad PS)', 'wanted', 'critical', 'egujcop', 'FRS roadmap – no plate', null, true],
  ['', 'person', 'Unidentified body case UDB-2026-17', 'unidentified_body', 'medium', 'manual', 'AFIS/NAFIS readiness placeholder', null, true],
  ['', 'person', 'Missing minor – Surat', 'missing', 'high', 'own', '', null, true],
];
export const watchlist: WatchlistEntry[] = WL.map((r, i) => {
  const [plate, entity, name, reason, priority, source, notes, expires, active] = r;
  const hits = plate && SCHEDULE[plate] && active ? SCHEDULE[plate].length * 9 : 0;
  const effective = active && (!expires || Date.parse(expires) > NOW);
  return {
    id: i + 1,
    entity_type: entity,
    plate_norm: plate || null,
    plate_display: plate ? formatPlate(plate) : null,
    name,
    reason,
    priority,
    source,
    notes: notes || null,
    photo_path: null,
    added_by: 1,
    added_by_username: i < 6 ? 'jury_admin' : 'jury_operator',
    is_active: active,
    expires_at: expires,
    hit_count: hits,
    last_hit_at: hits ? minutesAgo(12) : null,
    is_effective: effective,
    alerts_24h: hits ? Math.min(hits, 6) : 0,
    created_at: hoursAgo(30 + i),
    updated_at: hoursAgo(2),
  };
});

/* ------------------------------------------------------------------ alerts + events */

export const alerts: Alert[] = [];
export const events: EventItem[] = [];
let alertId = 60;
let eventId = 300;
const REASON_FLOOR: Record<WatchlistReason, AlertPriority> = { stolen: 'critical', wanted: 'critical', blacklisted: 'high', arrested: 'high', missing: 'medium', unidentified_body: 'medium', suspect: 'low', other: 'low' };
const RANK: AlertPriority[] = ['critical', 'high', 'medium', 'low'];
const maxPriority = (a: AlertPriority, b: AlertPriority): AlertPriority => (RANK.indexOf(a) < RANK.indexOf(b) ? a : b);

function addEvent(cam: Camera, type: EventType, occurredAt: string, note: string | null, extra: Partial<EventItem> = {}): EventItem {
  eventId += 1;
  const labels: Record<string, string> = { accident: 'Accident', suspicious: 'Suspicious activity', checkpoint: 'Checkpoint', other: 'Other', watchlist_hit: 'Watchlist hit', loop_reset: 'Loop reset', intrusion: 'Intrusion', camera_offline: 'Camera offline', camera_online: 'Camera online' };
  const auto = !['accident', 'suspicious', 'checkpoint', 'other'].includes(type);
  const e: EventItem = {
    id: eventId,
    camera_id: cam.id,
    camera: summary(cam),
    occurred_at: occurredAt,
    type,
    type_label: labels[type] ?? type,
    note,
    sighting_id: null,
    read_id: null,
    alert_id: null,
    frame_url: null,
    frame_sha256: null,
    is_auto: auto,
    created_by: auto ? null : 2,
    created_by_username: auto ? null : 'jury_operator',
    created_at: occurredAt,
    ...extra,
  };
  events.push(e);
  return e;
}

const watchedSightings = sightings.filter((s) => watchlist.some((w) => w.is_effective && w.plate_norm === s.plate_norm));
watchedSightings.slice(0, 14).forEach((s, i) => {
  const w = watchlist.find((x) => x.plate_norm === s.plate_norm) as WatchlistEntry;
  const read = detections.find((d) => d.id === s.best_read_id) as Detection;
  const possible = i % 5 === 4;
  let priority = maxPriority(w.priority, REASON_FLOOR[w.reason]);
  if (possible) priority = RANK[Math.min(3, RANK.indexOf(priority) + 1)];
  const status: Alert['status'] = i < 4 ? 'new' : i < 8 ? 'acknowledged' : 'closed';
  alertId += 1;
  const created = Date.parse(read.captured_at) + 900 + Math.floor(rnd() * 1500);
  const cam = cameraById.get(s.camera.id) as Camera;
  const a: Alert = {
    id: alertId,
    type: 'watchlist_hit',
    status,
    priority,
    confidence_level: possible ? 'possible' : 'exact',
    escalated: status === 'new' && NOW - created > 5 * 60_000,
    created_at: iso(created),
    updated_at: iso(created + (status === 'new' ? 0 : 240_000)),
    latency_ms: created - Date.parse(read.captured_at),
    camera: summary(cam),
    watchlist: { id: w.id, entity_type: w.entity_type, plate_norm: w.plate_norm, plate_display: w.plate_display, name: w.name, reason: w.reason, priority: w.priority, source: w.source },
    read: { id: read.id, plate_raw: read.plate_raw, plate_norm: read.plate_norm, confidence: read.confidence, captured_at: read.captured_at, crop_url: read.crop_url, crop_sha256: read.crop_sha256 },
    sighting_id: s.id,
    plate_norm: s.plate_norm,
    snapshot_url: read.crop_url,
    snapshot_sha256: read.crop_sha256,
    read_count: s.read_count,
    last_read_at: s.last_seen,
    acknowledged_by: status === 'new' ? null : 2,
    acknowledged_by_username: status === 'new' ? null : 'jury_operator',
    acknowledged_at: status === 'new' ? null : iso(created + 120_000),
    closed_by: status === 'closed' ? 2 : null,
    closed_by_username: status === 'closed' ? 'jury_operator' : null,
    closed_at: status === 'closed' ? iso(created + 240_000) : null,
    outcome: status === 'closed' ? (i % 2 ? 'resolved' : 'false_positive') : null,
    note: status === 'new' ? null : status === 'acknowledged' ? 'Unit dispatched from Sector 7 PS' : 'Vehicle intercepted at Koba checkpost',
    recording_available: cam.anpr_enabled,
  };
  alerts.push(a);
  read.watchlist_hit = true;
  read.alert_id = a.id;
  addEvent(cam, 'watchlist_hit', a.created_at, `${w.reason} · ${w.plate_display} · ${a.confidence_level}`, { sighting_id: s.id, read_id: read.id, alert_id: a.id });
});
// camera offline alerts
[cameraById.get(30) as Camera, cameraById.get(41) as Camera].forEach((cam, i) => {
  alertId += 1;
  const created = NOW - (35 + i * 400) * 60_000;
  alerts.push({
    id: alertId,
    type: 'camera_offline',
    status: i === 0 ? 'new' : 'acknowledged',
    priority: 'low',
    confidence_level: null,
    escalated: i === 0,
    created_at: iso(created),
    updated_at: iso(created),
    latency_ms: null,
    camera: summary(cam),
    watchlist: null,
    read: null,
    sighting_id: null,
    plate_norm: null,
    snapshot_url: null,
    snapshot_sha256: null,
    read_count: 1,
    last_read_at: null,
    acknowledged_by: i === 0 ? null : 1,
    acknowledged_by_username: i === 0 ? null : 'jury_admin',
    acknowledged_at: i === 0 ? null : iso(created + 600_000),
    closed_by: null,
    closed_by_username: null,
    closed_at: null,
    outcome: null,
    note: i === 0 ? null : 'Field engineer informed',
    recording_available: false,
  });
  addEvent(cam, 'camera_offline', iso(created), '3 consecutive not-ready checks', { alert_id: alertId });
});
// loop resets and manual events
liveCams.forEach((cam, i) => {
  for (let k = 0; k < 3; k += 1) addEvent(cam, 'loop_reset', minutesAgo(1.5 + k * 1.5 + i * 0.2), `pts ${(89.9).toFixed(1)} -> 0.2 (discontinuity)`);
});
addEvent(cameraById.get(6) as Camera, 'suspicious', minutesAgo(48), 'Vehicle circling CH-0 circle twice, occupants observing the gate', { sighting_id: sightings[3]?.id ?? null });
addEvent(cameraById.get(8) as Camera, 'checkpoint', minutesAgo(130), 'Evening checkpoint at Koba circle; 40 vehicles checked');
addEvent(cameraById.get(4) as Camera, 'accident', hoursAgo(6), 'Two-wheeler collision near Infocity; ambulance dispatched');
addEvent(cameraById.get(51) as Camera, 'intrusion', minutesAgo(200), "person in zone 'Gate apron' for 2.4 s", { frame_url: cameraFrame(51, 'Dynatech Office Gate', null, istTime(NOW - 200 * 60_000)), frame_sha256: sha('intrusion-1') });
addEvent(cameraById.get(30) as Camera, 'camera_online', hoursAgo(9), 'Back online after 4 h');
events.sort((a, b) => Date.parse(b.occurred_at) - Date.parse(a.occurred_at));

export function alertDetail(a: Alert): AlertDetail {
  const reads = a.sighting_id ? detections.filter((d) => d.sighting_id === a.sighting_id).map((d) => ({ id: d.id, plate_raw: d.plate_raw, plate_norm: d.plate_norm, confidence: d.confidence, captured_at: d.captured_at, crop_url: d.crop_url, crop_sha256: d.crop_sha256 })) : [];
  return { ...a, reads, events: events.filter((e) => e.alert_id === a.id) };
}

/* ------------------------------------------------------------------ users, keys, webhooks, settings */

export const users: UserRow[] = [
  { id: 1, username: 'jury_admin', full_name: 'Jury Administrator', role: 'admin', department_id: null, department_name: null, district: null, is_active: true, last_login_at: minutesAgo(3), created_at: hoursAgo(72), updated_at: hoursAgo(72) },
  { id: 2, username: 'jury_operator', full_name: 'Jury Operator', role: 'operator', department_id: null, department_name: null, district: null, is_active: true, last_login_at: minutesAgo(41), created_at: hoursAgo(72), updated_at: hoursAgo(72) },
  { id: 3, username: 'jury_viewer', full_name: 'Jury Viewer', role: 'viewer', department_id: null, department_name: null, district: null, is_active: true, last_login_at: hoursAgo(5), created_at: hoursAgo(72), updated_at: hoursAgo(72) },
  { id: 4, username: 'dept_admin_police', full_name: 'Police Department Admin', role: 'dept_admin', department_id: 2, department_name: 'Gujarat Police', district: null, is_active: true, last_login_at: hoursAgo(20), created_at: hoursAgo(72), updated_at: hoursAgo(72) },
  { id: 5, username: 'scrb_analyst', full_name: 'SCRB Analyst', role: 'operator', department_id: 2, department_name: 'Gujarat Police', district: 'Gandhinagar', is_active: false, last_login_at: hoursAgo(300), created_at: hoursAgo(400), updated_at: hoursAgo(100) },
];

export const apiKeys: ApiKey[] = [
  { id: 1, name: 'seed-bulk', key_prefix: 'bulk0000', scope: 'bulk', is_active: true, created_by_username: 'jury_admin', last_used_at: hoursAgo(2), created_at: hoursAgo(72) },
  { id: 2, name: 'seed-internal', key_prefix: 'internal', scope: 'internal', is_active: true, created_by_username: 'jury_admin', last_used_at: minutesAgo(0.2), created_at: hoursAgo(72) },
  { id: 3, name: 'gsrtc-integration', key_prefix: 'a7f3k9m2', scope: 'bulk', is_active: false, created_by_username: 'jury_admin', last_used_at: null, created_at: hoursAgo(40) },
];

export const webhooks: Webhook[] = [
  { id: 1, name: 'Mock sink (alerts)', url: 'http://api:8000/api/mock-sandbox/webhook-sink', secret: '********', event_types: ['alert.created', 'alert.updated'], is_active: true, last_status: 204, last_delivered_at: minutesAgo(12), last_error: null, created_at: hoursAgo(30) },
  { id: 2, name: 'Control room SIEM', url: 'https://siem.example.gov.in/hooks/sentinel', secret: null, event_types: ['camera.offline', 'camera.online'], is_active: false, last_status: 502, last_delivered_at: hoursAgo(9), last_error: 'connect timeout after 5 s', created_at: hoursAgo(20) },
];

const FIELD_MAP = {
  external_id: ['id', 'camera_id', 'cameraId', 'stream_id', 'uid'],
  name: ['name', 'camera_name', 'title', 'label'],
  department_code: ['department', 'dept', 'department_name', 'owner_department', 'agency'],
  district: ['district', 'location.district', 'city'],
  lat: ['location.lat', 'lat', 'latitude', 'gps.lat', 'geo.lat', 'coordinates.lat'],
  lon: ['location.lon', 'location.lng', 'lon', 'lng', 'longitude', 'gps.lon', 'gps.lng', 'geo.lon', 'coordinates.lon'],
  address: ['location.address', 'address', 'location.name', 'place'],
  type: ['type', 'camera_type', 'kind'],
  codec: ['codec', 'video_codec', 'stream_properties.codec', 'properties.codec'],
  resolution: ['resolution', 'stream_properties.resolution', 'properties.resolution'],
  fps: ['fps', 'frame_rate', 'stream_properties.fps', 'properties.fps'],
  live: ['live', 'is_live', 'online', 'status.live', 'active'],
  rtsp_url: ['rtsp_url', 'rtsp', 'urls.rtsp', 'streams.rtsp', 'rtspUrl'],
  whep_url: ['whep_url', 'whep', 'urls.whep', 'streams.webrtc', 'webrtc_url'],
  hls_url: ['hls_url', 'hls', 'urls.hls', 'streams.hls', 'hlsUrl'],
  police_station: ['police_station', 'ps'],
  install_date: ['install_date', 'installed_on'],
};
const ALIASES = { police: 'POLICE', 'gujarat police': 'POLICE', home: 'POLICE', health: 'HEALTH', 'health & family welfare': 'HEALTH', hospital: 'HEALTH', gsrtc: 'GSRTC', 'transport corporation': 'GSRTC', panchayat: 'PANCHAYAT', 'gram panchayat': 'PANCHAYAT', 'municipal corporation': 'MUNICIPAL', municipal: 'MUNICIPAL', amc: 'MUNICIPAL', rto: 'RTO', transport: 'RTO', 'food & civil supplies': 'FCS', fcs: 'FCS' };

const setting = (key: string, value: SettingItem['value'], is_secret = false): SettingItem => ({ key, value, is_secret, updated_by_username: 'jury_admin', updated_at: hoursAgo(30) });
export const settings: SettingItem[] = [
  setting('catalogue.base_url', 'http://api:8000/mock-sandbox'),
  setting('catalogue.auth_type', 'none'),
  setting('catalogue.auth_username', ''),
  setting('catalogue.auth_password', '', true),
  setting('catalogue.auth_header', '', true),
  setting('catalogue.timeout_s', 30),
  setting('catalogue.field_map', FIELD_MAP),
  setting('catalogue.department_aliases', ALIASES),
  setting('catalogue.source', 'mock'),
  setting('catalogue.portal_url', 'https://cctv.corp8.cloud'),
  setting('catalogue.portal_email', ''),
  setting('catalogue.portal_password', '', true),
  setting('catalogue.enrichment_path', '/app/media/cameras_enrichment.csv'),
  setting('catalogue.cameras_json_path', '/app/media/cameras.json'),
  setting('sandbox.stream_host', '103.250.160.189'),
  setting('sandbox.rtsp_port', 8554),
  setting('sandbox.whep_port', 8889),
  setting('sandbox.hls_base', 'https://cctv.corp8.cloud'),
  setting('sandbox.stream_email', ''),
  setting('sandbox.stream_password', '', true),
  setting('sandbox.probe_timeout_s', 12),
  setting('sandbox.probe_parallel', 6),
  setting('retention.days_frames', 7),
  setting('retention.days_reads', 30),
  setting('retention.days_clips', 90),
  setting('gap.coverage_radius_m', 150),
  setting('gap.poi_radius_m', 300),
  setting('gap.grid_m', 500),
  setting('gap.ageing_years', 5),
  setting('alerts.suppression_s', 60),
  setting('alerts.fuzzy_min_conf', 0.8),
  setting('alerts.escalate_minutes', 5),
  setting('route.speed_flag_kmh', 150),
  setting('route.default_window_h', 24),
  setting('notify.telegram_bot_token', '', true),
  setting('notify.telegram_chat_id', ''),
  setting('notify.telegram_min_priority', 'high'),
  setting('ui.product_name', 'Sentinel Gujarat'),
  setting('ui.map_center', [23.2156, 72.6369]),
  setting('ui.map_zoom', 8),
];

/* ------------------------------------------------------------------ audit */

const ACTIONS = ['auth.login', 'camera.import_sandbox', 'camera.update', 'camera.export', 'stream.view', 'vehicle.search', 'vehicle.route', 'watchlist.create', 'alert.ack', 'alert.close', 'report.detections', 'report.route', 'settings.update', 'event.create', 'auth.login_failed', 'clip.create', 'evidence.verify', 'retention.purge'];
export const audit: AuditRow[] = Array.from({ length: 140 }, (_, i) => {
  const action = i === 0 ? 'camera.import_sandbox' : ACTIONS[i % ACTIONS.length];
  const user = action === 'retention.purge' ? null : users[i % 4];
  const cam = cameras[i % 50];
  const before = action === 'camera.update' ? { name: cam.name, maintenance_status: 'ok', anpr_enabled: false } : action === 'settings.update' ? { 'catalogue.base_url': 'http://api:8000/mock-sandbox' } : action === 'alert.ack' ? { status: 'new' } : null;
  const after = action === 'camera.update' ? { name: cam.name, maintenance_status: 'under_maintenance', anpr_enabled: true } : action === 'settings.update' ? { 'catalogue.base_url': 'http://10.0.0.5' } : action === 'alert.ack' ? { status: 'acknowledged', note: 'Unit dispatched' } : action === 'camera.import_sandbox' ? { fetched: 50, added: 50, updated: 0, errors: 0, duration_ms: 3412, first_stream_ready_ms: 2210 } : action === 'vehicle.search' ? { q: 'GJ 01 AB 1234', normalised: 'GJ01AB1234', exact: 4, fuzzy: 2 } : action === 'retention.purge' ? { reads: 0, crops: 0, frames: 0, bytes_freed: 0 } : null;
  return {
    id: 2000 - i,
    ts: minutesAgo(i * 11 + rnd() * 6),
    user_id: user?.id ?? null,
    username: user?.username ?? null,
    actor: user?.username ?? 'system',
    role: user?.role ?? 'system',
    action,
    entity: action.startsWith('camera') ? 'cameras' : action.startsWith('alert') ? 'alerts' : action.startsWith('watchlist') ? 'watchlist' : action.startsWith('settings') ? 'settings' : action.startsWith('report') ? 'report_files' : null,
    entity_id: action.startsWith('camera') ? String(cam.id) : action.startsWith('alert') ? String(61 + (i % 10)) : null,
    before,
    after,
    ip: user ? `10.20.${i % 8}.${40 + (i % 60)}` : null,
    user_agent: user ? 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0' : null,
    request_id: `9f1c${String(i).padStart(4, '0')}-1a2b-4c3d-9e8f-0a1b2c3d4e5f`,
  };
});

/* ------------------------------------------------------------------ reports */

export const reportFiles: ReportFile[] = [
  { id: 1, type: 'detections_csv', path: 'reports/2026-09-04/detections_20260904_1530IST_jury_admin.csv', url: '/media/reports/2026-09-04/detections_20260904_1530IST_jury_admin.csv', sha256: sha('r1'), size_bytes: 1_248_331, params: { from: hoursAgo(2), to: hoursAgo(0) }, row_count: 4120, created_by_username: 'jury_admin', created_at: minutesAgo(40) },
  { id: 2, type: 'detections_pdf', path: 'reports/2026-09-04/detections_20260904_1531IST_jury_admin.pdf', url: '/media/reports/2026-09-04/detections_20260904_1531IST_jury_admin.pdf', sha256: sha('r2'), size_bytes: 3_902_118, params: { from: hoursAgo(2), to: hoursAgo(0) }, row_count: 4120, created_by_username: 'jury_admin', created_at: minutesAgo(39) },
  { id: 3, type: 'route_pdf', path: 'reports/2026-09-04/route_GJ01AB1234_20260904_1502IST_jury_operator.pdf', url: '/media/reports/2026-09-04/route_GJ01AB1234_20260904_1502IST_jury_operator.pdf', sha256: sha('r3'), size_bytes: 812_004, params: { plate: 'GJ01AB1234', include: 'confirmed' }, row_count: 4, created_by_username: 'jury_operator', created_at: minutesAgo(68) },
  { id: 4, type: 'gap_pdf', path: 'reports/2026-09-04/gap_20260904_1410IST_jury_admin.pdf', url: '/media/reports/2026-09-04/gap_20260904_1410IST_jury_admin.pdf', sha256: sha('r4'), size_bytes: 1_104_220, params: { coverage_radius_m: 150 }, row_count: null, created_by_username: 'jury_admin', created_at: minutesAgo(120) },
  { id: 5, type: 'cameras_csv', path: 'exports/2026-09-04/cameras_export_20260904_1352IST_jury_admin.csv', url: '/media/exports/2026-09-04/cameras_export_20260904_1352IST_jury_admin.csv', sha256: sha('r5'), size_bytes: 24_118, params: {}, row_count: 52, created_by_username: 'jury_admin', created_at: minutesAgo(138) },
  { id: 6, type: 'quality_pdf', path: 'reports/2026-09-03/quality_20260903_2210IST_jury_admin.pdf', url: '/media/reports/2026-09-03/quality_20260903_2210IST_jury_admin.pdf', sha256: sha('r6'), size_bytes: 402_330, params: { camera_id: 1 }, row_count: 30, created_by_username: 'jury_admin', created_at: hoursAgo(17) },
];

export function qualityReport(cameraId: number | null): QualityReport {
  const cams = cameraId ? liveCams.filter((c) => c.id === cameraId) : liveCams;
  const reads = detections.filter((d) => !cameraId || d.camera.id === cameraId);
  const valid = reads.filter((r) => r.is_valid_format);
  return {
    window: { from: hoursAgo(24), to: iso(NOW), camera_id: cameraId },
    reads_total: reads.length,
    reads_valid_format: valid.length,
    valid_format_pct: Number(((valid.length / Math.max(1, reads.length)) * 100).toFixed(1)),
    sightings_total: sightings.filter((s) => !cameraId || s.camera.id === cameraId).length,
    unique_plates: new Set(valid.map((r) => r.plate_norm)).size,
    mean_confidence: Number((reads.reduce((s, r) => s + r.confidence, 0) / Math.max(1, reads.length)).toFixed(2)),
    reads_per_camera: cams.map((c) => {
      const rs = reads.filter((r) => r.camera.id === c.id);
      return { camera_id: c.id, camera_name: c.name, reads: rs.length, valid_pct: Number(((rs.filter((r) => r.is_valid_format).length / Math.max(1, rs.length)) * 100).toFixed(1)), mean_conf: Number((rs.reduce((s, r) => s + r.confidence, 0) / Math.max(1, rs.length)).toFixed(2)) };
    }),
    labelled: 90,
    exact_match_pct: 84.4,
    char_accuracy_pct: 96.7,
    per_camera_accuracy: cams.slice(0, 3).map((c, i) => ({ camera_id: c.id, camera_name: c.name, labelled: 30, exact_pct: [86.7, 83.3, 83.3][i], char_accuracy_pct: [97.1, 96.4, 96.6][i] })),
    confusions: [{ expected: 'B', got: '8', count: 5 }, { expected: 'O', got: '0', count: 4 }, { expected: 'S', got: '5', count: 2 }, { expected: 'Z', got: '2', count: 1 }],
    sample: valid.slice(0, 8).map((r, i) => ({ read_id: r.id, plate_norm: r.plate_norm, true_plate: i === 3 ? r.plate_norm.replace('B', '8') : r.plate_norm, is_match: i !== 3, crop_url: r.crop_url })),
  };
}

/* ------------------------------------------------------------------ geo */

const DISTRICT_CENTRES: Record<string, [number, number, number]> = {
  Gandhinagar: [23.24, 72.65, 0.22],
  Ahmedabad: [22.98, 72.55, 0.42],
  Vadodara: [22.3, 73.2, 0.4],
  Surat: [21.2, 72.85, 0.4],
  Rajkot: [22.3, 70.8, 0.5],
  Valsad: [20.5, 72.95, 0.3],
  Dahod: [22.95, 74.1, 0.35],
  'Gir Somnath': [20.95, 70.55, 0.35],
  Jamnagar: [22.4, 70.1, 0.45],
  'Devbhumi Dwarka': [22.3, 69.3, 0.45],
};
function polygon(lat: number, lon: number, r: number, seed: number): number[][] {
  const g = mulberry32(seed);
  const pts: number[][] = [];
  const n = 10;
  for (let i = 0; i < n; i += 1) {
    const ang = (i / n) * Math.PI * 2;
    const rr = r * (0.75 + g() * 0.4);
    pts.push([Number((lon + Math.cos(ang) * rr * 1.1).toFixed(4)), Number((lat + Math.sin(ang) * rr).toFixed(4))]);
  }
  pts.push(pts[0]);
  return pts;
}
export function geoDistricts(): GeoFeatureCollection<GeoDistrictProps> {
  return {
    type: 'FeatureCollection',
    features: Object.entries(DISTRICT_CENTRES).map(([name, [lat, lon, r]], i) => ({
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: [polygon(lat, lon, r, 100 + i)] },
      properties: { id: i + 1, name, code: name.slice(0, 2).toUpperCase(), camera_count: cameras.filter((c) => c.district === name).length, online_count: cameras.filter((c) => c.district === name && c.status === 'online').length },
    })),
  };
}
export function geoCameras(filter: { department_id?: number; district?: string; status?: string; type?: string }): GeoFeatureCollection<GeoCameraProps> {
  return {
    type: 'FeatureCollection',
    features: cameras
      .filter((c) => c.lat !== null && c.status !== 'retired')
      .filter((c) => (!filter.department_id || c.department_id === filter.department_id) && (!filter.district || c.district === filter.district) && (!filter.status || filter.status.split(',').includes(c.status)) && (!filter.type || c.type === filter.type))
      .map((c) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [c.lon, c.lat] },
        properties: { id: c.id, external_id: c.external_id, name: c.name, department_code: c.department_code, department_name: c.department_name, district: c.district, police_station: c.police_station, type: c.type, ownership: c.ownership, status: c.status, maintenance_status: c.maintenance_status, anpr_enabled: c.anpr_enabled, live: c.live, codec: c.codec, heading_deg: c.heading_deg, fov_deg: c.fov_deg, last_seen_at: c.last_seen_at },
      })),
  };
}
type PoiRow = [string, string, string, number, number];
const POIS: PoiRow[] = [
  ['Sachivalaya Main Gate', 'govt_office', 'Gandhinagar', 23.224, 72.6475],
  ['Sector 21 Government School', 'school', 'Gandhinagar', 23.2301, 72.641],
  ['Pethapur Checkpost', 'checkpost', 'Gandhinagar', 23.274, 72.658],
  ['Civil Hospital Gandhinagar', 'hospital', 'Gandhinagar', 23.228, 72.649],
  ['Sector 11 Bus Stand', 'bus_stand', 'Gandhinagar', 23.2302, 72.6298],
  ['Koba Circle Highway Point', 'highway', 'Gandhinagar', 23.1655, 72.6548],
  ['Gandhinagar Railway Station', 'railway_station', 'Gandhinagar', 23.214, 72.63],
  ['Sargasan Cross Roads', 'highway', 'Gandhinagar', 23.201, 72.628],
  ['Kalupur Railway Station', 'railway_station', 'Ahmedabad', 23.027, 72.601],
  ['Civil Hospital Asarwa', 'hospital', 'Ahmedabad', 23.053, 72.6065],
  ['Geeta Mandir Bus Station', 'bus_stand', 'Ahmedabad', 23.0092, 72.5962],
  ['Bhilad Border Checkpost', 'border', 'Valsad', 20.2617, 72.915],
  ['Somnath Temple', 'temple', 'Gir Somnath', 20.888, 70.401],
  ['Dwarkadhish Temple', 'temple', 'Devbhumi Dwarka', 22.2376, 68.967],
  ['Dahod Bus Stand', 'bus_stand', 'Dahod', 22.8347, 74.2555],
  ['Rajkot Race Course Market', 'market', 'Rajkot', 22.3, 70.79],
  ['Surat Textile Market', 'market', 'Surat', 21.19, 72.83],
  ['Vadodara Airport Road', 'highway', 'Vadodara', 22.33, 73.22],
];
function nearestCameraM(lat: number, lon: number): { id: number | null; m: number | null } {
  let best: { id: number | null; m: number | null } = { id: null, m: null };
  cameras.forEach((c) => {
    if (c.lat === null || c.lon === null) return;
    const m = haversineKm(lat, lon, c.lat, c.lon) * 1000;
    if (best.m === null || m < best.m) best = { id: c.id, m };
  });
  return best;
}
export function geoPois(district?: string): GeoFeatureCollection<GeoPoiProps> {
  return {
    type: 'FeatureCollection',
    features: POIS.filter((p) => !district || p[2] === district).map((p, i) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [p[4], p[3]] }, properties: { id: i + 1, name: p[0], type: p[1], district: p[2], nearest_camera_m: nearestCameraM(p[3], p[4]).m } })),
  };
}
export function geoCoverage(radius = 150): GeoFeatureCollection<GeoCoverageProps> {
  const byDistrict = new Map<string, Camera[]>();
  cameras.filter((c) => c.lat !== null && c.status !== 'offline' && c.status !== 'retired').forEach((c) => byDistrict.set(c.district ?? '?', [...(byDistrict.get(c.district ?? '?') ?? []), c]));
  const dLat = radius / 111_000;
  return {
    type: 'FeatureCollection',
    features: Array.from(byDistrict.entries()).map(([district, cams]) => ({
      type: 'Feature',
      geometry: {
        type: 'MultiPolygon',
        coordinates: cams.map((c) => {
          const dLon = dLat / Math.cos(((c.lat as number) * Math.PI) / 180);
          const ring: number[][] = [];
          for (let i = 0; i <= 16; i += 1) {
            const a = (i / 16) * Math.PI * 2;
            ring.push([Number(((c.lon as number) + Math.cos(a) * dLon).toFixed(6)), Number(((c.lat as number) + Math.sin(a) * dLat).toFixed(6))]);
          }
          return [ring];
        }),
      },
      properties: { district, radius_m: radius, camera_count: cams.length },
    })),
  };
}

/* ------------------------------------------------------------------ gap analysis */

export function gapAnalysis(params: { coverage_radius_m?: number; poi_radius_m?: number; grid_m?: number; ageing_years?: number; district?: string }): GapAnalysis {
  const p = { coverage_radius_m: params.coverage_radius_m ?? 150, poi_radius_m: params.poi_radius_m ?? 300, grid_m: params.grid_m ?? 500, ageing_years: params.ageing_years ?? 5 };
  const cams = cameras.filter((c) => c.status !== 'retired' && (!params.district || c.district === params.district));
  const districts = Array.from(new Set(cams.map((c) => c.district).filter(Boolean))) as string[];
  const uncovered = POIS.map((poi, i) => ({ poi, i, near: nearestCameraM(poi[3], poi[4]) })).filter((x) => (x.near.m ?? Infinity) > p.poi_radius_m && (!params.district || x.poi[2] === params.district));
  const ageing = cams.filter((c) => (c.age_years ?? 0) > p.ageing_years || c.amc_status === 'expired' || c.type === 'analog' || c.maintenance_status === 'faulty');
  const cells: GapAnalysis['zero_coverage']['features'] = [];
  const g = mulberry32(7);
  const step = p.grid_m / 111_000;
  districts.forEach((d) => {
    const [lat, lon] = DISTRICT_CENTRES[d] ?? [22.5, 72, 0.3];
    for (let r = 0; r < 6; r += 1)
      for (let c = 0; c < 6; c += 1) {
        if (g() < 0.5) continue;
        const la = lat - 0.03 + r * step;
        const lo = lon - 0.03 + c * step * 1.08;
        cells.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [[[lo, la], [lo + step * 1.08, la], [lo + step * 1.08, la + step], [lo, la + step], [lo, la]]] }, properties: { district: d, cell: `r${r}c${c}` } });
      }
  });
  const byDistrict = districts.map((d) => {
    const dc = cams.filter((c) => c.district === d);
    const online = dc.filter((c) => c.status === 'online').length;
    return { district: d, total: dc.length, online, online_pct: Number(((online / Math.max(1, dc.length)) * 100).toFixed(1)), zero_coverage_cells: cells.filter((c) => c.properties.district === d).length * 12, uncovered_pois: uncovered.filter((u) => u.poi[2] === d).length, ageing: ageing.filter((c) => c.district === d).length };
  });
  const areas = new Map<string, Camera[]>();
  cams.forEach((c) => areas.set(`${c.district}|${c.police_station}`, [...(areas.get(`${c.district}|${c.police_station}`) ?? []), c]));
  const deptGaps: GapAnalysis['department_gaps'] = [];
  ['POLICE', 'HEALTH', 'GSRTC', 'PANCHAYAT', 'MUNICIPAL'].forEach((code) =>
    districts.forEach((d) => {
      if (!cams.some((c) => c.district === d && c.department_code === code)) deptGaps.push({ department_code: code, department_name: deptByCode.get(code)?.name ?? code, district: d });
    }),
  );
  return {
    generated_at: iso(NOW),
    cached: false,
    params: p,
    summary: { cameras_total: cams.length, with_location: cams.filter((c) => c.lat !== null).length, online_pct: Number(((cams.filter((c) => c.status === 'online').length / Math.max(1, cams.length)) * 100).toFixed(1)), districts_with_cameras: districts.length, districts_total: 33, zero_coverage_cells: cells.length * 12, uncovered_pois: uncovered.length, ageing_cameras: ageing.length, metadata_gaps: cams.filter((c) => !c.rtsp_url || c.retention_days === null || !c.install_date).length, offline_hotspots: 2 },
    by_area: Array.from(areas.entries()).map(([k, list]) => {
      const [district, police_station] = k.split('|');
      const count = (s: CameraStatus) => list.filter((c) => c.status === s).length;
      const byDept: Record<string, number> = {};
      list.forEach((c) => {
        byDept[c.department_code] = (byDept[c.department_code] ?? 0) + 1;
      });
      return { district, police_station, ward: null, total: list.length, online: count('online'), degraded: count('degraded'), offline: count('offline'), unknown: count('unknown'), online_pct: Number(((count('online') / list.length) * 100).toFixed(1)), by_department: byDept };
    }),
    by_district: byDistrict,
    department_gaps: deptGaps.slice(0, 18),
    uncovered_pois: uncovered.map((u) => ({ id: u.i + 1, name: u.poi[0], type: u.poi[1], district: u.poi[2], lat: u.poi[3], lon: u.poi[4], nearest_camera_id: u.near.id, nearest_camera_m: u.near.m === null ? null : Number(u.near.m.toFixed(1)) })),
    zero_coverage: { type: 'FeatureCollection', features: cells },
    zero_coverage_truncated: false,
    offline_hotspots: [
      { district: 'Surat', police_station: 'Athwa', count: 2, camera_ids: [28, 29] },
      { district: 'Ahmedabad', police_station: 'Kalupur', count: 3, camera_ids: [9, 11, 18] },
    ],
    metadata_gaps: cams.filter((c) => !c.rtsp_url || c.retention_days === null || !c.install_date).map((c) => ({ camera_id: c.id, name: c.name, missing: [!c.rtsp_url ? 'rtsp_url' : null, c.retention_days === null ? 'retention_days' : null, !c.install_date ? 'install_date' : null].filter(Boolean) as string[] })),
    ageing: ageing
      .map((c) => {
        const reasons = [(c.age_years ?? 0) > p.ageing_years ? `older than ${p.ageing_years} years` : null, c.type === 'analog' ? 'analog' : null, c.amc_status === 'expired' ? 'AMC expired' : null, c.amc_status === 'expiring' ? 'AMC expiring' : null, c.status === 'offline' ? 'offline 100% of 24 h' : null, c.maintenance_status === 'faulty' ? 'faulty' : null].filter(Boolean) as string[];
        const near = c.lat !== null && POIS.some((poi) => haversineKm(poi[3], poi[4], c.lat as number, c.lon as number) < 0.3);
        const score = Math.min(100, 10 * Math.max(0, (c.age_years ?? 0) - p.ageing_years) + (c.type === 'analog' ? 30 : 0) + (c.amc_status === 'expired' ? 25 : 0) + (c.amc_status === 'expiring' ? 15 : 0) + 0.2 * (c.status === 'offline' ? 100 : 0) + (near ? 10 : 0));
        return { camera_id: c.id, name: c.name, department_code: c.department_code, district: c.district, type: c.type, install_date: c.install_date, age_years: c.age_years, amc_expiry: c.amc_expiry, amc_status: c.amc_status, maintenance_status: c.maintenance_status, offline_pct_24h: c.status === 'offline' ? 100 : 100 - (c.uptime_24h_pct ?? 0), near_poi: near, priority_score: Number(score.toFixed(1)), reasons };
      })
      .sort((a, b) => b.priority_score - a.priority_score),
    recommendations: byDistrict.map((d) => ({ district: d.district, text: `${d.uncovered_pois ? `Add ${d.uncovered_pois} camera${d.uncovered_pois > 1 ? 's' : ''} at uncovered POIs; ` : ''}${d.ageing ? `replace ${d.ageing} ageing/analog camera${d.ageing > 1 ? 's' : ''}; ` : ''}${d.online_pct < 50 ? `restore ${d.total - d.online} offline cameras (${d.online_pct} % online).` : 'coverage healthy.'}` })),
  };
}

/* ------------------------------------------------------------------ health + dashboard */

export function healthSummary(): HealthSummary {
  const count = (s: CameraStatus) => cameras.filter((c) => c.status === s).length;
  return {
    checked_at: minutesAgo(0.4),
    cameras: { total: cameras.length, online: count('online'), degraded: count('degraded'), offline: count('offline'), not_streaming: count('not_streaming'), unknown: count('unknown'), retired: count('retired') },
    uptime_24h_pct: 97.4,
    not_streaming: cameras.filter((c) => c.status === 'not_streaming').map((c) => ({ id: c.id, name: c.name, district: c.district, since: c.last_status_change_at })),
    anpr_live_cameras: liveCams.length,
    down_over_5min: cameras.filter((c) => c.status === 'offline' && c.live !== false).map((c) => ({ id: c.id, name: c.name, district: c.district, department_name: c.department_name, offline_since: c.last_status_change_at as string, minutes: Math.round((NOW - Date.parse(c.last_status_change_at as string)) / 60_000) })),
    amc_expiring_30d: cameras.filter((c) => c.amc_status === 'expiring').map((c) => ({ id: c.id, name: c.name, amc_vendor: c.amc_vendor, amc_expiry: c.amc_expiry as string, days_left: Math.round((Date.parse(c.amc_expiry as string) - NOW) / 86_400_000) })),
    maintenance: cameras.filter((c) => c.maintenance_status !== 'ok').map((c) => ({ id: c.id, name: c.name, maintenance_status: c.maintenance_status, since: c.last_maintenance_at })),
    disk: { data_used_bytes: 12_345_678_901, data_free_bytes: 398_765_432_100, recordings_used_bytes: 45_678_901_234 },
    mediamtx: { ok: true, paths: 70, ready: 10 },
    anpr_workers: [
      { id: 'live-a1b2c3', mode: 'live', gpu: false, version: '1.0.0-phase1', detector: 'contour', cameras: 8, fps_total: 38.4, last_heartbeat_at: minutesAgo(0.1), stale: false },
      { id: 'preindex-d4e5f6', mode: 'preindex', gpu: false, version: '1.0.0-phase1', detector: 'auto', cameras: 42, fps_total: 41.0, last_heartbeat_at: minutesAgo(0.2), stale: false },
    ],
  };
}

export function dashboardStats(): DashboardStats {
  const h = healthSummary();
  const objs = { person: 1210, car: 3412, motorcycle: 2104, bus: 91, truck: 214, bicycle: 63 };
  return {
    generated_at: iso(NOW),
    cameras: { ...h.cameras, anpr_live: liveCams.length, recording: liveCams.length },
    reads: { last_1h: detections.filter((d) => Date.parse(d.captured_at) > NOW - 3_600_000).length, last_24h: detections.length, total: 15321 + detections.length, last_read_at: detections[0]?.captured_at ?? null },
    sightings: { last_24h: sightings.length, total: 2001 + sightings.length, valid_format_pct_24h: 88.4 },
    alerts: { new: alerts.filter((a) => a.status === 'new').length, acknowledged: alerts.filter((a) => a.status === 'acknowledged').length, last_24h: alerts.length, critical_open: alerts.filter((a) => a.status !== 'closed' && a.priority === 'critical').length, avg_latency_ms_24h: 1450 },
    watchlist: { active: watchlist.filter((w) => w.is_effective).length, vehicles: watchlist.filter((w) => w.is_effective && w.entity_type === 'vehicle').length, persons: watchlist.filter((w) => w.is_effective && w.entity_type === 'person').length },
    object_counts_24h: objs,
    events_24h: events.length,
    disk: h.disk,
    anpr_workers: h.anpr_workers,
  };
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const istLabel = (ms: number, bucket: 'hour' | 'day') => {
  const d = new Date(ms + 5.5 * 3_600_000);
  const day = `${String(d.getUTCDate()).padStart(2, '0')} ${MONTHS[d.getUTCMonth()]}`;
  return bucket === 'day' ? day : `${day} ${String(d.getUTCHours()).padStart(2, '0')}:00`;
};

export function dashboardCharts(bucket: 'hour' | 'day', fromMs: number, cameraId?: number): DashboardCharts {
  const size = bucket === 'hour' ? 3_600_000 : 86_400_000;
  const start = Math.floor(fromMs / size) * size;
  const buckets: number[] = [];
  for (let t = start; t <= NOW; t += size) buckets.push(t);
  const cams = liveCams.filter((c) => !cameraId || c.id === cameraId);
  const g = mulberry32(11);
  const vehicles_per_hour = buckets.flatMap((b) => cams.map((c) => ({ camera_id: c.id, camera_name: c.name, bucket_start: iso(b), label_ist: istLabel(b, bucket), sightings: Math.round((bucket === 'hour' ? 20 : 400) * (0.4 + g() * 0.9) * (c.id === 6 || c.id === 8 ? 1.5 : 1)) })));
  const daily = Array.from(new Set(buckets.map((b) => Math.floor((b + 5.5 * 3_600_000) / 86_400_000))));
  const alerts_per_camera_day = daily.flatMap((d) => cams.slice(0, 5).map((c) => ({ camera_id: c.id, camera_name: c.name, bucket_start: iso(d * 86_400_000 - 5.5 * 3_600_000), label_ist: istLabel(d * 86_400_000 - 5.5 * 3_600_000, 'day'), alerts: Math.round(g() * 4) })));
  const classes = ['car', 'motorcycle', 'person', 'truck', 'bus', 'bicycle'];
  const base: Record<string, number> = { car: 140, motorcycle: 90, person: 50, truck: 9, bus: 4, bicycle: 3 };
  const object_counts = buckets.flatMap((b) => classes.map((cls) => ({ camera_id: 0, camera_name: 'all', bucket_start: iso(b), label_ist: istLabel(b, bucket), class: cls, count: Math.round(base[cls] * (0.5 + g() * 0.9) * (bucket === 'day' ? 24 : 1)) })));
  const counts = new Map<string, { n: number; cams: Set<number>; last: string }>();
  sightings.forEach((s) => {
    const e = counts.get(s.plate_norm) ?? { n: 0, cams: new Set<number>(), last: s.first_seen };
    e.n += 1;
    e.cams.add(s.camera.id);
    if (s.first_seen > e.last) e.last = s.first_seen;
    counts.set(s.plate_norm, e);
  });
  const top_plates = Array.from(counts.entries())
    .sort((a, b) => b[1].n - a[1].n)
    .slice(0, 20)
    .map(([plate, e]) => ({ plate_norm: plate, plate_display: formatPlate(plate), sightings: e.n, cameras: e.cams.size, last_seen: e.last }));
  const bins = ['0.9-1.0', '0.8-0.9', '0.7-0.8', '0.6-0.7', '0.5-0.6', '<0.5'];
  const reads_by_confidence = bins.map((bin, i) => ({ bin, reads: detections.filter((d) => (i === 5 ? d.confidence < 0.5 : d.confidence >= 0.9 - i * 0.1 && d.confidence < 1 - i * 0.1 + (i === 0 ? 0.01 : 0))).length }));
  return { window: { from: iso(fromMs), to: iso(NOW), bucket }, vehicles_per_hour, top_plates, alerts_per_camera_day, object_counts, reads_by_confidence };
}

export function objectCountsSeries(cameraId: number) {
  const g = mulberry32(cameraId);
  const items: { camera_id: number; bucket_start: string; label_ist: string; class: string; count: number }[] = [];
  const totals: Record<string, number> = {};
  const base: Record<string, number> = { car: 6, motorcycle: 4, person: 2, truck: 0.5 };
  for (let m = 60; m >= 1; m -= 1) {
    const b = Math.floor((NOW - m * 60_000) / 60_000) * 60_000;
    Object.keys(base).forEach((cls) => {
      const n = Math.round(base[cls] * (0.3 + g()));
      items.push({ camera_id: cameraId, bucket_start: iso(b), label_ist: istTime(b).slice(0, 5), class: cls, count: n });
      totals[cls] = (totals[cls] ?? 0) + n;
    });
  }
  return { items, totals };
}

export const NOW_MS = NOW;
export { iso, minutesAgo, hoursAgo, sha, istTime };
