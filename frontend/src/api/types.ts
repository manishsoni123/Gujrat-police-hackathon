/**
 * Typed shapes of the Sentinel Gujarat API, hand-generated from
 * docs/CONTRACT.md §2, §3, §5 and §9. Keep in sync with the contract.
 */
import type {
  AlertPriority,
  AlertStatus,
  AmcStatus,
  CameraStatus,
  ConfidenceLevel,
  MaintenanceStatus,
  WatchlistReason,
} from '@/theme/colours';

export type Role = 'admin' | 'dept_admin' | 'operator' | 'viewer';

export type Permission =
  | 'cameras.read'
  | 'cameras.write'
  | 'cameras.export'
  | 'analytics.read'
  | 'watchlist.write'
  | 'alerts.ack'
  | 'route.confirm'
  | 'events.write'
  | 'reports.export'
  | 'external.lookup'
  | 'zones.write'
  | 'admin.users'
  | 'admin.audit'
  | 'admin.apikeys'
  | 'admin.settings'
  | 'settings.read_public';

export interface User {
  id: number;
  username: string;
  full_name: string;
  role: Role;
  department_id: number | null;
  department_name: string | null;
  district: string | null;
}

export interface Me extends User {
  permissions: Permission[];
}

export interface LoginResponse {
  access_token: string;
  token_type: 'bearer';
  expires_at: string;
  user: User;
}

export interface ApiErrorItem {
  row?: number;
  index?: number;
  field?: string;
  message: string;
  external_id?: string;
}

export interface ApiErrorBody {
  detail: string;
  code: string;
  errors?: ApiErrorItem[];
  existing_id?: number;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ListParams {
  page?: number;
  page_size?: number;
  sort?: string;
  order?: 'asc' | 'desc';
  [key: string]: string | number | boolean | undefined;
}

/* ---------- cameras ---------- */

export type CameraSource = 'sandbox' | 'csv' | 'api' | 'manual' | 'own';
export type CameraType = 'analog' | 'ip' | 'ptz' | 'dome' | 'bullet' | 'anpr' | 'other';
export type Ownership = 'govt_dept' | 'private' | 'public_facing';
export type Connectivity = 'lan' | 'fibre' | '4g' | '5g' | 'leased_line' | 'wifi' | 'other';
export type Codec = 'H264' | 'H265' | 'MJPEG' | 'UNKNOWN';

export interface CameraSummary {
  id: number;
  external_id: string;
  name: string;
  department_id: number;
  department_code: string;
  department_name: string;
  district: string | null;
  police_station: string | null;
  lat: number | null;
  lon: number | null;
  status: CameraStatus;
  type: CameraType;
}

export interface Camera extends CameraSummary {
  source: CameraSource;
  ownership: Ownership;
  address: string | null;
  ward: string | null;
  rtsp_url: string | null;
  whep_url: string | null;
  hls_url: string | null;
  relay_path: string | null;
  codec: Codec;
  resolution: string | null;
  fps: number | null;
  live: boolean | null;
  storage_location: string | null;
  retention_days: number | null;
  install_date: string | null;
  vendor: string | null;
  model: string | null;
  heading_deg: number | null;
  fov_deg: number | null;
  connectivity_type: Connectivity | null;
  bandwidth_kbps: number | null;
  vms_platform: string | null;
  nvr_id: string | null;
  onvif_host: string | null;
  anpr_enabled: boolean;
  record_enabled: boolean;
  last_seen_at: string | null;
  last_status_change_at: string | null;
  maintenance_status: MaintenanceStatus;
  last_maintenance_at: string | null;
  maintenance_note: string | null;
  amc_vendor: string | null;
  amc_expiry: string | null;
  created_by: number | null;
  created_via: string;
  created_at: string;
  updated_at: string;
  retired_at: string | null;
  uptime_24h_pct: number | null;
  age_years: number | null;
  amc_status: AmcStatus;
  created_by_username: string | null;
}

export interface CameraDetail extends Camera {
  recent_alerts: number;
  reads_24h: number;
  sightings_24h: number;
  snapshot_url: string | null;
  zones: Zone[];
}

export interface CameraImportRow {
  external_id: string;
  name: string;
  department_code?: string;
  type?: CameraType;
  ownership?: Ownership;
  lat?: number | null;
  lon?: number | null;
  address?: string | null;
  district?: string | null;
  police_station?: string | null;
  ward?: string | null;
  rtsp_url?: string | null;
  whep_url?: string | null;
  hls_url?: string | null;
  codec?: string;
  resolution?: string | null;
  fps?: number | null;
  live?: boolean | null;
  storage_location?: string | null;
  retention_days?: number | null;
  install_date?: string | null;
  vendor?: string | null;
  model?: string | null;
  heading_deg?: number | null;
  fov_deg?: number | null;
  connectivity_type?: Connectivity | null;
  bandwidth_kbps?: number | null;
  vms_platform?: string | null;
  nvr_id?: string | null;
  maintenance_status?: MaintenanceStatus;
  amc_vendor?: string | null;
  amc_expiry?: string | null;
  anpr_enabled?: boolean;
  record_enabled?: boolean;
  source?: CameraSource;
}

export interface ImportWarning {
  row?: number;
  index?: number;
  external_id?: string;
  field?: string;
  message: string;
}

export interface SandboxImportResult {
  source_url: string;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  fetched: number;
  added: number;
  updated: number;
  unchanged: number;
  errors: ImportWarning[];
  warnings: ImportWarning[];
  relay_paths_created: number;
  relay_paths_failed: number;
  anpr_enabled: number;
  first_stream_ready_ms: number | null;
  dry_run: boolean;
}

export interface CsvImportResult {
  job_id: string;
  dry_run: boolean;
  rows_total: number;
  added: number;
  updated: number;
  errors: ImportWarning[];
  warnings: ImportWarning[];
  error_report_url: string | null;
  relay_paths_created: number;
  relay_paths_failed: number;
  duration_ms: number;
}

export interface CameraHealth {
  camera_id: number;
  status: CameraStatus;
  uptime_pct: number | null;
  last_seen_at: string | null;
  last_status_change_at: string | null;
  checks: number;
  log: HealthLogRow[];
  transitions: { at: string; from: CameraStatus; to: CameraStatus }[];
}

export interface HealthLogRow {
  checked_at: string;
  is_ready: boolean;
  has_video: boolean;
  bytes_delta: number;
  readers: number;
  source_flag: 'mediamtx' | 'probe' | 'catalogue' | 'manual';
  status_after: CameraStatus;
}

export interface MaintenanceUpdate {
  maintenance_status: MaintenanceStatus;
  last_maintenance_at?: string | null;
  maintenance_note?: string | null;
  amc_vendor?: string | null;
  amc_expiry?: string | null;
}

/* ---------- geo ---------- */

export interface GeoFeature<P = Record<string, unknown>> {
  type: 'Feature';
  geometry: { type: string; coordinates: unknown };
  properties: P;
}
export interface GeoFeatureCollection<P = Record<string, unknown>> {
  type: 'FeatureCollection';
  features: GeoFeature<P>[];
}

export interface GeoCameraProps {
  id: number;
  external_id: string;
  name: string;
  department_code: string;
  department_name: string;
  district: string | null;
  police_station: string | null;
  type: CameraType;
  ownership: Ownership;
  status: CameraStatus;
  maintenance_status: MaintenanceStatus;
  anpr_enabled: boolean;
  live: boolean | null;
  codec: Codec;
  heading_deg: number | null;
  fov_deg: number | null;
  last_seen_at: string | null;
}
export interface GeoDistrictProps {
  id: number;
  name: string;
  code: string | null;
  camera_count: number;
  online_count: number;
}
export interface GeoPoiProps {
  id: number;
  name: string;
  type: string;
  district: string;
  nearest_camera_m: number | null;
}
export interface GeoCoverageProps {
  district: string;
  radius_m: number;
  camera_count: number;
}

/* ---------- streams ---------- */

export interface StreamInfo {
  camera_id: number;
  name: string;
  codec: Codec;
  relay_path: string;
  play_path: string;
  whep_url: string;
  hls_url: string;
  snapshot_url: string | null;
  snapshot_updated_at: string | null;
  snapshot_stale: boolean;
  ready: boolean | null;
  readers: number | null;
  record_enabled: boolean;
  playback_path: string;
  status: CameraStatus;
}

/* ---------- health ---------- */

export interface AnprWorker {
  id: string;
  mode: 'live' | 'preindex';
  gpu: boolean;
  cameras: number;
  fps_total: number;
  last_heartbeat_at: string;
  stale: boolean;
}

export interface HealthSummary {
  checked_at: string;
  cameras: { total: number; online: number; degraded: number; offline: number; unknown: number; retired: number };
  uptime_24h_pct: number | null;
  anpr_live_cameras: number;
  down_over_5min: { id: number; name: string; district: string | null; department_name: string; offline_since: string; minutes: number }[];
  amc_expiring_30d: { id: number; name: string; amc_vendor: string | null; amc_expiry: string; days_left: number }[];
  maintenance: { id: number; name: string; maintenance_status: MaintenanceStatus; since: string | null }[];
  disk: { data_used_bytes: number; data_free_bytes: number; recordings_used_bytes: number };
  mediamtx: { ok: boolean; paths: number; ready: number };
  anpr_workers: AnprWorker[];
}

export interface WallLayout {
  grid: 4 | 9 | 16;
  tiles: { slot: number; camera_id: number | null }[];
}

/* ---------- gap analysis ---------- */

export interface GapAnalysis {
  generated_at: string;
  cached: boolean;
  params: { coverage_radius_m: number; poi_radius_m: number; grid_m: number; ageing_years: number };
  summary: {
    cameras_total: number;
    with_location: number;
    online_pct: number;
    districts_with_cameras: number;
    districts_total: number;
    zero_coverage_cells: number;
    uncovered_pois: number;
    ageing_cameras: number;
    metadata_gaps: number;
    offline_hotspots: number;
  };
  by_area: {
    district: string;
    police_station: string | null;
    ward: string | null;
    total: number;
    online: number;
    degraded: number;
    offline: number;
    unknown: number;
    online_pct: number;
    by_department: Record<string, number>;
  }[];
  by_district: { district: string; total: number; online: number; online_pct: number; zero_coverage_cells: number; uncovered_pois: number; ageing: number }[];
  department_gaps: { department_code: string; department_name: string; district: string }[];
  uncovered_pois: { id: number; name: string; type: string; district: string; lat: number; lon: number; nearest_camera_id: number | null; nearest_camera_m: number | null }[];
  zero_coverage: GeoFeatureCollection<{ district: string; cell: string }>;
  zero_coverage_truncated?: boolean;
  offline_hotspots: { district: string; police_station: string | null; count: number; camera_ids: number[] }[];
  metadata_gaps: { camera_id: number; name: string; missing: string[] }[];
  ageing: {
    camera_id: number;
    name: string;
    department_code: string;
    district: string | null;
    type: CameraType;
    install_date: string | null;
    age_years: number | null;
    amc_expiry: string | null;
    amc_status: AmcStatus;
    maintenance_status: MaintenanceStatus;
    offline_pct_24h: number;
    near_poi: boolean;
    priority_score: number;
    reasons: string[];
  }[];
  recommendations: { district: string; text: string }[];
}

/* ---------- detections / sightings ---------- */

export interface Detection {
  id: number;
  camera: CameraSummary;
  sighting_id: number | null;
  captured_at: string;
  stream_pts: number | null;
  frame_index: number | null;
  plate_raw: string;
  plate_norm: string;
  plate_display: string;
  is_valid_format: boolean;
  confidence: number;
  bbox: [number, number, number, number] | null;
  crop_url: string | null;
  crop_sha256: string | null;
  mode: 'live' | 'preindex';
  watchlist_hit: boolean;
  alert_id: number | null;
  qa_label: { true_plate: string; is_match: boolean } | null;
}

export interface DetectionDetail extends Detection {
  sighting: Sighting | null;
  frame_url: string | null;
  frame_sha256: string | null;
  events: EventItem[];
}

export interface Sighting {
  id: number;
  camera: CameraSummary;
  plate_norm: string;
  plate_display: string;
  is_valid_format: boolean;
  first_seen: string;
  last_seen: string;
  read_count: number;
  best_conf: number;
  best_read_id: number | null;
  best_crop_url: string | null;
  frame_url: string | null;
  frame_sha256: string | null;
  closed: boolean;
  mode: 'live' | 'preindex';
  recording_available: boolean;
}

/* ---------- vehicles ---------- */

export type Confirmation = 'confirmed' | 'rejected' | null;

export interface SearchHit extends Sighting {
  match: 'exact' | 'fuzzy';
  score: number;
  distance?: number;
  similarity?: number;
  confirmation: Confirmation;
  sample_reads?: { id: number; plate_raw: string; confidence: number; crop_url: string | null }[];
}

export interface VehicleSearchResult {
  query: { raw: string; normalised: string; is_valid_format: boolean; from: string; to: string };
  exact: SearchHit[];
  fuzzy: SearchHit[];
  cameras_seen: number;
}

export interface RouteSighting {
  seq: number;
  sighting_id: number;
  camera: CameraSummary;
  first_seen: string;
  last_seen: string;
  read_count: number;
  best_conf: number;
  crop_url: string | null;
  frame_url: string | null;
  match: 'exact' | 'fuzzy';
  confirmation: Confirmation;
  recording_available: boolean;
  in_polyline?: boolean;
}

export interface RouteLeg {
  from_seq: number;
  to_seq: number;
  distance_km: number;
  minutes: number;
  speed_kmh: number | null;
  flags: string[];
}

export interface RouteFlag {
  from_seq: number;
  to_seq: number;
  type: 'implausible_speed' | 'long_gap' | 'overlap';
  distance_km: number;
  minutes: number;
  speed_kmh: number | null;
  message: string;
}

export interface VehicleRoute {
  plate: string;
  plate_display: string;
  window: { from: string; to: string; include: 'confirmed' | 'all' };
  sightings: RouteSighting[];
  polyline: [number, number][];
  legs: RouteLeg[];
  flags: RouteFlag[];
  total_distance_km: number;
  total_duration_min: number;
  cameras_count: number;
  loop_resets_in_window: number;
}

/* ---------- watchlist ---------- */

export type WatchlistSource = 'own' | 'egujcop' | 'vahan' | 'manual' | 'import';

export interface WatchlistEntry {
  id: number;
  entity_type: 'vehicle' | 'person';
  plate_norm: string | null;
  plate_display: string | null;
  name: string | null;
  reason: WatchlistReason;
  priority: AlertPriority;
  source: WatchlistSource;
  notes: string | null;
  photo_path: string | null;
  added_by: number | null;
  added_by_username: string | null;
  is_active: boolean;
  expires_at: string | null;
  hit_count: number;
  last_hit_at: string | null;
  is_effective: boolean;
  alerts_24h: number;
  created_at: string;
  updated_at: string;
}

export interface WatchlistInput {
  entity_type: 'vehicle' | 'person';
  plate?: string;
  name?: string;
  reason: WatchlistReason;
  priority?: AlertPriority;
  source?: WatchlistSource;
  notes?: string;
  expires_at?: string | null;
  is_active?: boolean;
}

export interface WatchlistImportResult {
  rows_total: number;
  added: number;
  updated: number;
  errors: ImportWarning[];
  error_report_url: string | null;
  duration_ms: number;
}

/* ---------- alerts ---------- */

export type AlertType = 'watchlist_hit' | 'camera_offline' | 'intrusion' | 'frs_hit';
export type AlertOutcome = 'resolved' | 'false_positive' | 'duplicate' | 'other';

export interface AlertWatchlist {
  id: number;
  entity_type: 'vehicle' | 'person';
  plate_norm: string | null;
  plate_display: string | null;
  name: string | null;
  reason: WatchlistReason;
  priority: AlertPriority;
  source: WatchlistSource;
}

export interface AlertRead {
  id: number;
  plate_raw: string;
  plate_norm: string;
  confidence: number;
  captured_at: string;
  crop_url: string | null;
  crop_sha256: string | null;
}

export interface Alert {
  id: number;
  type: AlertType;
  status: AlertStatus;
  priority: AlertPriority;
  confidence_level: ConfidenceLevel | null;
  escalated: boolean;
  created_at: string;
  updated_at: string;
  latency_ms: number | null;
  camera: CameraSummary;
  watchlist: AlertWatchlist | null;
  read: AlertRead | null;
  sighting_id: number | null;
  plate_norm: string | null;
  snapshot_url: string | null;
  snapshot_sha256: string | null;
  read_count: number;
  last_read_at: string | null;
  acknowledged_by: number | null;
  acknowledged_by_username: string | null;
  acknowledged_at: string | null;
  closed_by: number | null;
  closed_by_username: string | null;
  closed_at: string | null;
  outcome: AlertOutcome | null;
  note: string | null;
  recording_available: boolean;
}

export interface AlertDetail extends Alert {
  reads: AlertRead[];
  events: EventItem[];
}

export interface AlertWsPayload extends Alert {
  sound: boolean;
  notify_title: string;
  notify_body: string;
}

export interface AlertUpdatePayload {
  id: number;
  status: AlertStatus;
  priority: AlertPriority;
  confidence_level: ConfidenceLevel | null;
  read_count: number;
  last_read_at: string | null;
  acknowledged_by_username: string | null;
  acknowledged_at: string | null;
  closed_by_username: string | null;
  closed_at: string | null;
  outcome: AlertOutcome | null;
  note: string | null;
  escalated: boolean;
  updated_at: string;
}

/* ---------- events ---------- */

export type ManualEventType = 'accident' | 'suspicious' | 'checkpoint' | 'other';
export type EventType = ManualEventType | 'watchlist_hit' | 'loop_reset' | 'intrusion' | 'camera_offline' | 'camera_online';

export interface EventItem {
  id: number;
  camera_id: number;
  camera: CameraSummary;
  occurred_at: string;
  type: EventType;
  type_label: string;
  note: string | null;
  sighting_id: number | null;
  read_id: number | null;
  alert_id: number | null;
  frame_url: string | null;
  frame_sha256: string | null;
  is_auto: boolean;
  created_by: number | null;
  created_by_username: string | null;
  created_at: string;
}

export interface EventInput {
  camera_id: number;
  type: ManualEventType;
  occurred_at?: string;
  note?: string;
  sighting_id?: number | null;
  read_id?: number | null;
}

/* ---------- dashboard ---------- */

export interface DashboardStats {
  generated_at: string;
  cameras: { total: number; online: number; degraded: number; offline: number; unknown: number; anpr_live: number; recording: number };
  reads: { last_1h: number; last_24h: number; total: number; last_read_at: string | null };
  sightings: { last_24h: number; total: number; valid_format_pct_24h: number | null };
  alerts: { new: number; acknowledged: number; last_24h: number; critical_open: number; avg_latency_ms_24h: number | null };
  watchlist: { active: number; vehicles: number; persons: number };
  object_counts_24h: Record<string, number>;
  events_24h: number;
  disk: { data_used_bytes: number; data_free_bytes: number; recordings_used_bytes: number };
  anpr_workers: AnprWorker[];
}

export interface DashboardCharts {
  window: { from: string; to: string; bucket: 'hour' | 'day' };
  vehicles_per_hour: { camera_id: number; camera_name: string; bucket_start: string; label_ist: string; sightings: number }[];
  top_plates: { plate_norm: string; plate_display: string; sightings: number; cameras: number; last_seen: string }[];
  alerts_per_camera_day: { camera_id: number; camera_name: string; bucket_start: string; label_ist: string; alerts: number }[];
  object_counts: { camera_id: number; camera_name: string; bucket_start: string; label_ist: string; class: string; count: number }[];
  reads_by_confidence: { bin: string; reads: number }[];
}

/* ---------- reports ---------- */

export type ReportType =
  | 'detections_csv'
  | 'detections_pdf'
  | 'route_pdf'
  | 'gap_csv'
  | 'gap_pdf'
  | 'quality_pdf'
  | 'cameras_csv'
  | 'import_errors_csv';

export interface ReportFile {
  id: number;
  type: ReportType;
  path: string;
  url: string;
  sha256: string;
  size_bytes: number;
  params: Record<string, unknown>;
  row_count: number | null;
  created_by_username: string | null;
  created_at: string;
}

export interface QualityReport {
  window: { from: string; to: string; camera_id: number | null };
  reads_total: number;
  reads_valid_format: number;
  valid_format_pct: number;
  sightings_total: number;
  unique_plates: number;
  mean_confidence: number;
  reads_per_camera: { camera_id: number; camera_name: string; reads: number; valid_pct: number; mean_conf: number }[];
  labelled: number;
  exact_match_pct: number | null;
  char_accuracy_pct: number | null;
  per_camera_accuracy: { camera_id: number; camera_name: string; labelled: number; exact_pct: number; char_accuracy_pct: number }[];
  confusions: { expected: string; got: string; count: number }[];
  sample: { read_id: number; plate_norm: string; true_plate: string; is_match: boolean; crop_url: string | null }[];
}

export interface QaSampleRead {
  id: number;
  plate_norm: string;
  plate_display: string;
  confidence: number;
  crop_url: string | null;
  camera: CameraSummary;
  captured_at: string;
}

/* ---------- recordings / clips ---------- */

export interface RecordingSegment {
  start: string;
  duration_s: number;
  end: string;
  url: string;
}
export interface Recordings {
  camera_id: number;
  playback_path: string;
  record_enabled: boolean;
  retention_h: number;
  segments: RecordingSegment[];
}
export interface PlayResolution {
  url: string | null;
  start: string | null;
  duration_s: number;
  available: boolean;
}
export interface Clip {
  id: number;
  camera_id: number;
  camera: CameraSummary;
  alert_id: number | null;
  sighting_id: number | null;
  start_at: string;
  duration_s: number;
  path: string;
  url: string;
  sha256: string;
  size_bytes: number;
  created_by: number | null;
  created_by_username: string | null;
  created_at: string;
}

/* ---------- objects / zones ---------- */

export interface ObjectCounts {
  items: { camera_id: number; bucket_start: string; label_ist: string; class: string; count: number }[];
  totals: Record<string, number>;
}

export interface Zone {
  id: number;
  camera_id: number;
  name: string;
  polygon: [number, number][];
  active_from: string | null;
  active_to: string | null;
  classes: string[];
  dwell_s: number;
  priority: AlertPriority;
  is_active: boolean;
}

/* ---------- evidence / external ---------- */

export interface EvidenceVerify {
  path: string;
  exists: boolean;
  entity: string | null;
  entity_id: number | null;
  stored_sha256: string | null;
  computed_sha256: string | null;
  match: boolean | null;
  size_bytes: number | null;
  checked_at: string;
}

export interface VahanLookup {
  source: string;
  adapter: string;
  plate: string;
  found: boolean;
  owner_name?: string;
  vehicle_class?: string;
  maker_model?: string;
  fuel?: string;
  colour?: string;
  registration_date?: string;
  rto?: string;
  insurance_valid_till?: string;
  fitness_valid_till?: string;
  note: string;
}

/* ---------- settings / admin ---------- */

export type SettingValue = string | number | boolean | Record<string, unknown> | unknown[] | null;

export interface SettingItem {
  key: string;
  value: SettingValue;
  is_secret: boolean;
  updated_by_username: string | null;
  updated_at: string | null;
}

export interface CatalogueTestResult {
  ok: boolean;
  status?: number;
  count?: number;
  sample?: Record<string, unknown>;
  mapped_sample?: Record<string, unknown>;
  unmapped_fields?: string[];
  duration_ms?: number;
  error?: string;
}

export interface PublicSettings {
  mock_sandbox: boolean;
  [key: string]: SettingValue;
}

export type WebhookEventType = 'alert.created' | 'alert.updated' | 'camera.offline' | 'camera.online' | 'event.created';

export interface Webhook {
  id: number;
  name: string;
  url: string;
  secret: string | null;
  event_types: WebhookEventType[];
  is_active: boolean;
  last_status: number | null;
  last_delivered_at: string | null;
  last_error: string | null;
  created_at: string;
}

export interface WebhookInput {
  name: string;
  url: string;
  secret?: string;
  event_types: WebhookEventType[];
  is_active?: boolean;
}

export interface ApiKey {
  id: number;
  name: string;
  key_prefix: string;
  scope: 'bulk' | 'internal';
  is_active: boolean;
  created_by_username: string | null;
  last_used_at: string | null;
  created_at: string;
}

export interface ApiKeyCreated {
  id: number;
  name: string;
  scope: 'bulk' | 'internal';
  key: string;
  key_prefix: string;
  created_at: string;
}

export interface UserRow extends User {
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface UserInput {
  username: string;
  password: string;
  full_name: string;
  role: Role;
  department_id?: number | null;
  district?: string | null;
}

export interface Department {
  id: number;
  code: string;
  name: string;
}

export interface AuditRow {
  id: number;
  ts: string;
  user_id: number | null;
  username: string | null;
  actor: string;
  role: string;
  action: string;
  entity: string | null;
  entity_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  ip: string | null;
  user_agent: string | null;
  request_id: string | null;
}

export interface Healthz {
  status: 'ok' | 'degraded';
  version: string;
  time: string;
  db: 'ok' | 'down';
  mediamtx: 'ok' | 'down';
  anpr_workers: number;
  uptime_s: number;
}

/* ---------- WebSocket envelopes (§9) ---------- */

export interface WsEnvelope<T = unknown> {
  type: string;
  ts: string;
  data: T;
}

export interface WsHello {
  user: string;
  role: Role;
  server_time: string;
  version: string;
  scope: { department_id: number | null; district: string | null };
}

export interface WsAlertStats {
  alerts_new: number;
  alerts_acknowledged: number;
  critical_open: number;
  reads_last_min: number;
  cameras_online: number;
}

export interface WsHealthStats {
  cameras: { total: number; online: number; degraded: number; offline: number; unknown: number };
  uptime_24h_pct: number | null;
  anpr_live_cameras: number;
  reads_last_min: number;
  disk_free_bytes: number;
}

export interface WsHealthChange {
  camera_id: number;
  name: string;
  status: CameraStatus;
  previous_status: CameraStatus;
  last_seen_at: string | null;
  checked_at: string;
  is_ready: boolean;
  has_video: boolean;
  bytes_delta: number;
  readers: number;
  source_flag: string;
  maintenance_status: MaintenanceStatus;
  district: string | null;
  department_code: string;
}

export interface WsAnprStatus {
  worker_id: string;
  mode: 'live' | 'preindex';
  gpu: boolean;
  cameras: { id: number; state: string; fps_actual: number }[];
  last_heartbeat_at: string;
  stale: boolean;
}

export interface WsObjectCounts {
  camera_id: number;
  minute: string;
  counts: Record<string, number>;
  final: boolean;
}

export interface WsSnapshot {
  camera_id: number;
  url: string;
  updated_at: string;
}
