/**
 * Typed API surface, one namespace per resource (CONTRACT §5).
 * Every function returns a Promise of the contract shape.
 */
import { api, downloadFile, type Query } from './client';
import type {
  Alert,
  AlertDetail,
  AlertOutcome,
  ApiKey,
  ApiKeyCreated,
  AuditRow,
  Camera,
  CameraDetail,
  CameraHealth,
  CameraImportRow,
  CatalogueTestResult,
  Clip,
  CsvImportResult,
  DashboardCharts,
  DashboardStats,
  Department,
  Detection,
  DetectionDetail,
  EnrichmentUploadResult,
  EventInput,
  EventItem,
  EvidenceVerify,
  GapAnalysis,
  GeoCameraProps,
  GeoCoverageProps,
  GeoDistrictProps,
  GeoFeatureCollection,
  GeoPoiProps,
  HealthSummary,
  Healthz,
  ListParams,
  LoginResponse,
  MaintenanceUpdate,
  Me,
  ObjectCounts,
  Paginated,
  PlayResolution,
  PublicSettings,
  QaSampleRead,
  QualityReport,
  Recordings,
  ReportFile,
  SandboxImportResult,
  SettingItem,
  SettingValue,
  Sighting,
  StreamInfo,
  UserInput,
  UserRow,
  SarthiLookup,
  VahanLookup,
  VehicleRoute,
  VehicleSearchResult,
  WallLayout,
  WatchlistEntry,
  WatchlistImportResult,
  WatchlistInput,
  Webhook,
  WebhookInput,
  Zone,
} from './types';

const q = (p?: ListParams | Query): Query | undefined => (p ? (p as Query) : undefined);

export const authApi = {
  login: (username: string, password: string) =>
    api<LoginResponse>('/auth/login', { method: 'POST', body: { username, password }, noAuthRedirect: true }),
  logout: () => api<void>('/auth/logout', { method: 'POST' }),
  me: () => api<Me>('/auth/me', { noAuthRedirect: true }),
  changePassword: (current_password: string, new_password: string) =>
    api<void>('/auth/change-password', { method: 'POST', body: { current_password, new_password } }),
};

export const camerasApi = {
  list: (params?: ListParams) => api<Paginated<Camera>>('/cameras', { query: q(params) }),
  get: (id: number) => api<CameraDetail>(`/cameras/${id}`),
  create: (row: CameraImportRow) => api<Camera>('/cameras', { method: 'POST', body: row }),
  update: (id: number, patch: Partial<CameraImportRow>) => api<Camera>(`/cameras/${id}`, { method: 'PUT', body: patch }),
  retire: (id: number) => api<void>(`/cameras/${id}`, { method: 'DELETE' }),
  maintenance: (id: number, body: MaintenanceUpdate) => api<Camera>(`/cameras/${id}/maintenance`, { method: 'PUT', body }),
  health: (id: number, hours = 24) => api<CameraHealth>(`/cameras/${id}/health`, { query: { hours } }),
  exportCsv: (params?: Query) => downloadFile('/cameras/export', { ...params, format: 'csv' }, 'cameras_export.csv'),
  template: () => downloadFile('/cameras/import/template', undefined, 'cameras_template.csv'),
  importSandbox: (measure_first_stream = true, dry_run = false, probe = true) =>
    api<SandboxImportResult>('/cameras/import/sandbox', { method: 'POST', body: { measure_first_stream, dry_run, probe } }),
  /** Organiser sandbox with an uploaded cameras.json and/or enrichment CSV (kept server-side as the "last upload"). */
  importSandboxFile: (files: { camerasJson?: File | null; enrichmentCsv?: File | null }, opts: { measure_first_stream?: boolean; dry_run?: boolean; probe?: boolean } = {}) => {
    const fd = new FormData();
    if (files.camerasJson) fd.append('cameras_json', files.camerasJson);
    if (files.enrichmentCsv) fd.append('enrichment_csv', files.enrichmentCsv);
    fd.append('dry_run', opts.dry_run ? 'true' : 'false');
    fd.append('measure_first_stream', opts.measure_first_stream === false ? 'false' : 'true');
    fd.append('probe', opts.probe === false ? 'false' : 'true');
    return api<SandboxImportResult>('/cameras/import/sandbox/file', { method: 'POST', formData: fd });
  },
  importCsv: (file: File, dry_run: boolean) => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('dry_run', dry_run ? 'true' : 'false');
    return api<CsvImportResult>('/cameras/import/csv', { method: 'POST', formData: fd });
  },
  departments: () => api<{ items: Department[] }>('/departments'),
};

export const geoApi = {
  cameras: (params?: Query) => api<GeoFeatureCollection<GeoCameraProps>>('/geo/cameras', { query: params }),
  districts: () => api<GeoFeatureCollection<GeoDistrictProps>>('/geo/districts'),
  pois: (params?: Query) => api<GeoFeatureCollection<GeoPoiProps>>('/geo/pois', { query: params }),
  coverage: (radius?: number, district?: string) =>
    api<GeoFeatureCollection<GeoCoverageProps>>('/geo/coverage', { query: { radius, district } }),
};

export const streamsApi = {
  get: (cameraId: number) => api<StreamInfo>(`/streams/${cameraId}`),
};

export const healthApi = {
  summary: () => api<HealthSummary>('/health/summary'),
  healthz: () => api<Healthz>('/healthz'),
};

export const wallApi = {
  get: () => api<WallLayout>('/me/wall-layout'),
  put: (layout: WallLayout) => api<WallLayout>('/me/wall-layout', { method: 'PUT', body: layout }),
};

export const gapApi = {
  get: (params?: Query) => api<GapAnalysis>('/gap-analysis', { query: params }),
  export: (format: 'csv' | 'pdf', params?: Query) =>
    downloadFile('/gap-analysis/export', { ...params, format }, `gap_analysis.${format}`),
};

export const detectionsApi = {
  list: (params?: ListParams) => api<Paginated<Detection>>('/detections', { query: q(params) }),
  get: (id: number) => api<DetectionDetail>(`/detections/${id}`),
  sightings: (params?: ListParams) => api<Paginated<Sighting>>('/sightings', { query: q(params) }),
};

export const vehiclesApi = {
  search: (params: Query) => api<VehicleSearchResult>('/vehicles/search', { query: params }),
  confirm: (plate: string, decisions: { sighting_id: number; decision: 'confirmed' | 'rejected' }[]) =>
    api<{ saved: number }>(`/vehicles/${encodeURIComponent(plate)}/confirm`, { method: 'POST', body: { decisions } }),
  route: (plate: string, params?: Query) => api<VehicleRoute>(`/vehicles/${encodeURIComponent(plate)}/route`, { query: params }),
  routePdf: (plate: string, params?: Query) =>
    downloadFile(`/vehicles/${encodeURIComponent(plate)}/route.pdf`, params, `route_${plate}.pdf`),
};

export const watchlistApi = {
  list: (params?: ListParams) => api<Paginated<WatchlistEntry>>('/watchlist', { query: q(params) }),
  create: (body: WatchlistInput) => api<WatchlistEntry>('/watchlist', { method: 'POST', body }),
  update: (id: number, body: Partial<WatchlistInput>) => api<WatchlistEntry>(`/watchlist/${id}`, { method: 'PUT', body }),
  remove: (id: number) => api<void>(`/watchlist/${id}`, { method: 'DELETE' }),
  template: () => downloadFile('/watchlist/import/template', undefined, 'watchlist_template.csv'),
  importCsv: (file: File, dry_run: boolean) => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('dry_run', dry_run ? 'true' : 'false');
    return api<WatchlistImportResult>('/watchlist/import/csv', { method: 'POST', formData: fd });
  },
};

export const alertsApi = {
  list: (params?: ListParams) => api<Paginated<Alert>>('/alerts', { query: q(params) }),
  get: (id: number) => api<AlertDetail>(`/alerts/${id}`),
  ack: (id: number, note?: string) => api<Alert>(`/alerts/${id}/ack`, { method: 'POST', body: { note } }),
  close: (id: number, note?: string, outcome: AlertOutcome = 'resolved') =>
    api<Alert>(`/alerts/${id}/close`, { method: 'POST', body: { note, outcome } }),
};

export const eventsApi = {
  list: (params?: ListParams) => api<Paginated<EventItem>>('/events', { query: q(params) }),
  create: (body: EventInput) => api<EventItem>('/events', { method: 'POST', body }),
};

export const dashboardApi = {
  stats: () => api<DashboardStats>('/dashboard/stats'),
  charts: (params?: Query) => api<DashboardCharts>('/dashboard/charts', { query: params }),
};

export const reportsApi = {
  detections: (format: 'csv' | 'pdf', params: Query) =>
    downloadFile('/reports/detections', { ...params, format }, `detections.${format}`),
  quality: (params?: Query) => api<QualityReport>('/reports/quality', { query: { ...params, format: 'json' } }),
  qualityPdf: (params?: Query) => downloadFile('/reports/quality', { ...params, format: 'pdf' }, 'quality.pdf'),
  history: (params?: ListParams) => api<Paginated<ReportFile>>('/reports/history', { query: q(params) }),
  download: (url: string, name: string) => downloadFile(url, undefined, name),
  qaSample: (params: Query) => api<{ items: QaSampleRead[] }>('/qa/sample', { query: params }),
  qaLabels: (labels: { read_id: number; true_plate: string }[]) =>
    api<{ saved: number; exact: number; char_accuracy_pct: number }>('/qa/labels', { method: 'POST', body: { labels } }),
};

export const recordingsApi = {
  list: (cameraId: number, params?: Query) => api<Recordings>(`/recordings/${cameraId}`, { query: params }),
  play: (cameraId: number, at: string, before_s = 10, duration_s = 30) =>
    api<PlayResolution>(`/recordings/${cameraId}/play`, { query: { at, before_s, duration_s } }),
  createClip: (body: { camera_id: number; start_at: string; duration_s?: number; alert_id?: number; sighting_id?: number }) =>
    api<Clip>('/clips', { method: 'POST', body }),
  clips: (params?: ListParams) => api<Paginated<Clip>>('/clips', { query: q(params) }),
};

export const objectsApi = {
  counts: (params: Query) => api<ObjectCounts>('/object-counts', { query: params }),
  zones: (cameraId: number) => api<Zone[]>('/zones', { query: { camera_id: cameraId } }),
};

export const evidenceApi = {
  verify: (path: string) => api<EvidenceVerify>('/evidence/verify', { query: { path } }),
};

export const externalApi = {
  vahan: (plate: string) => api<VahanLookup>(`/external/vahan/${encodeURIComponent(plate)}`),
  sarthi: (dlNumber: string) => api<SarthiLookup>(`/external/sarthi/${encodeURIComponent(dlNumber)}`),
};

export const settingsApi = {
  list: () => api<{ items: SettingItem[] }>('/settings'),
  update: (values: Record<string, SettingValue>) => api<{ items: SettingItem[] }>('/settings', { method: 'PUT', body: { values } }),
  testCatalogue: (overrides?: Record<string, SettingValue | undefined>) =>
    api<CatalogueTestResult>('/settings/catalogue/test', { method: 'POST', body: overrides ?? {} }),
  uploadEnrichment: (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return api<EnrichmentUploadResult>('/settings/catalogue/enrichment', { method: 'POST', formData: fd });
  },
  public: () => api<PublicSettings>('/settings/public'),
};

export const webhooksApi = {
  list: () => api<{ items: Webhook[] } | Webhook[]>('/webhooks'),
  create: (body: WebhookInput) => api<Webhook>('/webhooks', { method: 'POST', body }),
  update: (id: number, body: Partial<WebhookInput>) => api<Webhook>(`/webhooks/${id}`, { method: 'PUT', body }),
  remove: (id: number) => api<void>(`/webhooks/${id}`, { method: 'DELETE' }),
  test: (id: number) => api<{ status: number | null; duration_ms: number; error: string | null }>(`/webhooks/${id}/test`, { method: 'POST' }),
};

export const apiKeysApi = {
  list: () => api<{ items: ApiKey[] }>('/api-keys'),
  create: (name: string, scope: 'bulk' | 'internal') => api<ApiKeyCreated>('/api-keys', { method: 'POST', body: { name, scope } }),
  revoke: (id: number) => api<void>(`/api-keys/${id}`, { method: 'DELETE' }),
};

export const usersApi = {
  list: (params?: ListParams) => api<Paginated<UserRow>>('/users', { query: q(params) }),
  create: (body: UserInput) => api<UserRow>('/users', { method: 'POST', body }),
  update: (id: number, body: Partial<Omit<UserInput, 'password' | 'username'>> & { is_active?: boolean }) =>
    api<UserRow>(`/users/${id}`, { method: 'PUT', body }),
  resetPassword: (id: number, password: string) => api<void>(`/users/${id}/reset-password`, { method: 'POST', body: { password } }),
  deactivate: (id: number) => api<void>(`/users/${id}`, { method: 'DELETE' }),
};

export const auditApi = {
  list: (params?: ListParams) => api<Paginated<AuditRow>>('/audit', { query: q(params) }),
  exportCsv: (params?: Query) => downloadFile('/audit/export', { ...params, format: 'csv' }, 'audit_export.csv'),
};
