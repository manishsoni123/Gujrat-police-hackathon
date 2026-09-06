/**
 * Settings (/settings, admin). Tabs: Catalogue (source selector: organiser Sentinel sandbox with
 * stream credentials / portal login / enrichment CSV, or mock / generic host with field map; test),
 * Retention & privacy, Alerts & routes, Notifications, Webhooks (CRUD + test),
 * API keys (create shows the key once, revoke), Users (link). Deep links /settings/:tab.
 */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Form, Input, InputNumber, Modal, Select, Space, Switch, Table, Tabs, Tag, Tooltip, Typography, Upload, message } from 'antd';
import { ApiOutlined, CheckCircleOutlined, CopyOutlined, DeleteOutlined, ExperimentOutlined, KeyOutlined, PlusOutlined, SaveOutlined, SendOutlined, TeamOutlined, UploadOutlined } from '@ant-design/icons';
import { PageHeader } from '@/components/PageHeader';
import { apiKeysApi, settingsApi, webhooksApi } from '@/api';
import { ApiError, errorMessage } from '@/api/client';
import type { ApiKeyCreated, CatalogueSource, CatalogueTestResult, EnrichmentUploadResult, SettingItem, SettingValue, Webhook, WebhookEventType, WebhookInput } from '@/api/types';
import { ErrorState, PageSkeleton, EmptyState } from '@/components/States';
import { ALERT_PRIORITY } from '@/theme/colours';
import { ConfirmDialog } from '@/components/Dialogs';
import { IstTime } from '@/components/IstTime';
import { useAuthStore } from '@/store/auth';

const TABS = ['catalogue', 'retention', 'alerts', 'notifications', 'webhooks', 'api-keys', 'users'] as const;
type TabKey = (typeof TABS)[number];
const EVENT_TYPES: WebhookEventType[] = ['alert.created', 'alert.updated', 'camera.offline', 'camera.online', 'event.created'];

type Values = Record<string, SettingValue>;

function useSettingsForm(items: SettingItem[] | undefined, keys: string[]) {
  const [form] = Form.useForm<Values>();
  const qc = useQueryClient();
  const initial = useMemo(() => {
    const v: Values = {};
    (items ?? []).filter((s) => keys.includes(s.key)).forEach((s) => {
      v[s.key] = typeof s.value === 'object' && s.value !== null ? JSON.stringify(s.value, null, 2) : s.value;
    });
    return v;
  }, [items, keys]);
  useEffect(() => {
    form.setFieldsValue(initial);
  }, [initial, form]);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: (values: Values) => {
      const out: Values = {};
      Object.entries(values).forEach(([k, v]) => {
        if (v === undefined) return;
        if (typeof v === 'string' && /^\s*[[{]/.test(v)) {
          try {
            out[k] = JSON.parse(v) as SettingValue;
          } catch {
            throw new ApiError(422, { detail: `Setting ${k} is not valid JSON`, code: 'validation_error', errors: [{ field: k, message: 'invalid JSON' }] }, 'Invalid JSON');
          }
        } else out[k] = v;
      });
      return settingsApi.update(out);
    },
    onSuccess: () => {
      message.success('Settings saved');
      setFieldErrors({});
      qc.invalidateQueries({ queryKey: ['settings'] });
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        const m: Record<string, string> = {};
        e.errors.forEach((x) => {
          if (x.field) m[x.field] = x.message;
        });
        setFieldErrors(m);
      }
    },
  });
  const fe = (name: string) => ({ validateStatus: fieldErrors[name] ? ('error' as const) : undefined, help: fieldErrors[name] });
  return { form, save, fe };
}

const GENERIC_KEYS = ['catalogue.base_url', 'catalogue.auth_type', 'catalogue.auth_username', 'catalogue.auth_password', 'catalogue.auth_header', 'catalogue.timeout_s', 'catalogue.field_map', 'catalogue.department_aliases'];
const SENTINEL_KEYS = [
  'sandbox.stream_host',
  'sandbox.rtsp_port',
  'sandbox.whep_port',
  'sandbox.hls_base',
  'sandbox.stream_email',
  'sandbox.stream_password',
  'sandbox.probe_timeout_s',
  'sandbox.probe_parallel',
  'catalogue.portal_url',
  'catalogue.portal_email',
  'catalogue.portal_password',
  'catalogue.enrichment_path',
  'catalogue.cameras_json_path',
];
const SOURCE_OPTIONS: { value: CatalogueSource; label: string }[] = [
  { value: 'sentinel_portal', label: 'Organiser Sentinel sandbox (cctv.corp8.cloud, real cameras)' },
  { value: 'mock', label: 'Built-in mock sandbox (50 synthetic cameras, laptop tests)' },
  { value: 'generic_json', label: 'Generic catalogue host exposing GET /api/ingest' },
];
const CATALOGUE_KEYS = ['catalogue.source', ...GENERIC_KEYS, ...SENTINEL_KEYS];

function SentinelTestResult({ test }: { test: CatalogueTestResult }) {
  const cat = test.catalogue;
  return (
    <div style={{ marginTop: 12 }}>
      <Alert
        type={test.ok ? 'success' : 'error'}
        showIcon
        message={test.probe?.summary ?? test.error ?? (test.ok ? 'Stream reachable' : 'Stream not reachable')}
        description={
          <div style={{ fontSize: 12, display: 'grid', gap: 2 }}>
            <span>
              Stream relay <code>{test.stream?.host}:{test.stream?.rtsp_port}</code> (RTSP over TCP) as <code>{test.stream?.email || '(no e-mail)'}</code>
              {test.stream?.configured ? '' : ' - host, e-mail and access password must all be set'} {test.probe?.duration_ms != null ? `· probe ${(test.probe.duration_ms / 1000).toFixed(1)} s` : ''}
              {test.probe?.profile ? ` · profile ${test.probe.profile}` : ''}
            </span>
            <span>
              Catalogue ({cat?.mode === 'portal' ? 'portal login' : 'server-side / uploaded file'}): {cat?.ok ? <span><strong>{cat.count}</strong> cameras from <code>{cat.source}</code></span> : <span style={{ color: '#B91C1C' }}>{cat?.error ?? 'not checked'}</span>}
              {cat?.fallback ? <span> · fallback file <code>{cat.fallback.source}</code> ({cat.fallback.count} cameras)</span> : null}
            </span>
            <span>Enrichment CSV: {test.enrichment_path ? <code>{test.enrichment_path}</code> : <span style={{ color: '#92400E' }}>none found - upload one below</span>}</span>
            {cat?.notes?.length ? <span style={{ color: '#6B7280' }}>{cat.notes.join(' · ')}</span> : null}
          </div>
        }
      />
    </div>
  );
}

function CatalogueTab({ items }: { items: SettingItem[] }) {
  const { form, save, fe } = useSettingsForm(items, CATALOGUE_KEYS);
  const qc = useQueryClient();
  const source = (Form.useWatch('catalogue.source', form) as CatalogueSource | undefined) ?? 'mock';
  const authType = Form.useWatch('catalogue.auth_type', form);
  const [test, setTest] = useState<CatalogueTestResult | null>(null);
  const [enrich, setEnrich] = useState<EnrichmentUploadResult | null>(null);
  const runTest = useMutation({
    mutationFn: () => {
      const v = form.getFieldsValue();
      if (v['catalogue.source'] === 'sentinel_portal') {
        return settingsApi.testCatalogue({
          source: 'sentinel_portal',
          camera_id: 'cam01',
          stream_host: v['sandbox.stream_host'],
          rtsp_port: v['sandbox.rtsp_port'],
          whep_port: v['sandbox.whep_port'],
          stream_email: v['sandbox.stream_email'],
          stream_password: v['sandbox.stream_password'],
          portal_url: v['catalogue.portal_url'],
          portal_email: v['catalogue.portal_email'],
          portal_password: v['catalogue.portal_password'],
        });
      }
      return settingsApi.testCatalogue({ source: v['catalogue.source'], base_url: v['catalogue.base_url'], auth_type: v['catalogue.auth_type'], auth_username: v['catalogue.auth_username'], auth_password: v['catalogue.auth_password'], auth_header: v['catalogue.auth_header'], timeout_s: v['catalogue.timeout_s'] });
    },
    onSuccess: setTest,
  });
  const upload = useMutation({
    mutationFn: (f: File) => settingsApi.uploadEnrichment(f),
    onSuccess: (r) => {
      setEnrich(r);
      message.success(`${r.rows} enrichment rows loaded (${r.with_coordinates} with coordinates)`);
      qc.invalidateQueries({ queryKey: ['settings'] });
    },
  });
  const updated = items.find((s) => s.key === 'catalogue.source');
  const sentinel = source === 'sentinel_portal';
  const testButton = (
    <Button icon={<ExperimentOutlined />} loading={runTest.isPending} onClick={() => runTest.mutate()}>
      {sentinel ? 'Test cam01 with these credentials' : 'Test connection'}
    </Button>
  );
  return (
    <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)} requiredMark={false}>
      <Card size="small" title="Catalogue source" style={{ marginBottom: 12 }} extra={updated?.updated_at ? <span style={{ fontSize: 12, color: '#6B7280' }}>updated <IstTime value={updated.updated_at} /> by {updated.updated_by_username ?? '—'}</span> : null}>
        <div className="sg-grid sg-grid-2" style={{ alignItems: 'end' }}>
          <Form.Item name="catalogue.source" label="Where cameras come from" {...fe('catalogue.source')} style={{ marginBottom: 8 }}>
            <Select options={SOURCE_OPTIONS} />
          </Form.Item>
          <Space style={{ marginBottom: 8 }} wrap>
            <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={save.isPending}>
              Save
            </Button>
            {testButton}
          </Space>
        </div>
        <Typography.Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
          {sentinel
            ? 'Real sandbox: the catalogue is cameras.json (portal session or an uploaded copy), coordinates and departments come from the team enrichment CSV, and every camera is probed over RTSP/TCP on import. The MOCK SANDBOX badge disappears once this is saved.'
            : source === 'mock'
              ? 'Built-in mock served by the API container (50 synthetic cameras, 8 looping streams). Laptop tests and the integration checks use it; the header shows a MOCK SANDBOX badge.'
              : 'Any host that exposes the organiser-shaped GET /api/ingest; field map and department aliases below adapt to its JSON shape.'}
        </Typography.Paragraph>
        {runTest.isError ? <Alert type="error" showIcon style={{ marginTop: 12 }} message={errorMessage(runTest.error)} /> : null}
        {test && (test.source === 'sentinel_portal' || test.probe) ? <SentinelTestResult test={test} /> : null}
        {test && !(test.source === 'sentinel_portal' || test.probe) ? (
          <div style={{ marginTop: 12 }}>
            {test.ok ? (
              <Alert type="success" showIcon message={`Reachable · HTTP ${test.status} · ${test.count} cameras · ${test.duration_ms} ms`} description={test.unmapped_fields?.length ? <span>Unmapped source fields: {test.unmapped_fields.map((f) => <Tag key={f} style={{ margin: '0 4px 0 0' }}>{f}</Tag>)} - add them to the field map if needed.</span> : 'Every source field is mapped.'} />
            ) : (
              <Alert type="error" showIcon message="Catalogue not reachable" description={test.error} />
            )}
            {test.ok && test.sample ? (
              <div className="sg-grid sg-grid-2" style={{ marginTop: 8 }}>
                <div>
                  <Typography.Text strong style={{ fontSize: 12 }}>First catalogue item</Typography.Text>
                  <pre className="sg-json" style={{ maxHeight: 220 }}>{JSON.stringify(test.sample, null, 2)}</pre>
                </div>
                <div>
                  <Typography.Text strong style={{ fontSize: 12 }}>Mapped to CameraImportRow</Typography.Text>
                  <pre className="sg-json" style={{ maxHeight: 220 }}>{JSON.stringify(test.mapped_sample, null, 2)}</pre>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
      </Card>
      {sentinel ? (
        <div className="sg-grid sg-grid-2" style={{ alignItems: 'start' }}>
          <Card size="small" title="Organiser stream relay (MediaMTX)">
            <Alert type="info" showIcon style={{ marginBottom: 12 }} message="Access password, not the portal password" description="Streams are pulled as rtsp://<e-mail>:<access password>@<host>:<port>/stream/<id> over TCP. The e-mail is percent-encoded automatically. The password is stored as a secret: it is embedded in the relay's source URL only and masked in every response, export, audit row and log." />
            <Space style={{ display: 'flex' }} align="start" wrap>
              <Form.Item name="sandbox.stream_host" label="Host or IP" style={{ width: 220 }} rules={[{ required: true }]} {...fe('sandbox.stream_host')}>
                <Input placeholder="103.250.160.189" />
              </Form.Item>
              <Form.Item name="sandbox.rtsp_port" label="RTSP port" style={{ width: 110 }} {...fe('sandbox.rtsp_port')}>
                <InputNumber min={1} max={65535} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="sandbox.whep_port" label="WHEP port" style={{ width: 110 }} {...fe('sandbox.whep_port')}>
                <InputNumber min={1} max={65535} style={{ width: '100%' }} />
              </Form.Item>
            </Space>
            <Form.Item name="sandbox.stream_email" label="Registered e-mail" rules={[{ required: true }]} {...fe('sandbox.stream_email')}>
              <Input autoComplete="off" placeholder="you@example.com" />
            </Form.Item>
            <Form.Item name="sandbox.stream_password" label="Access password" extra="Masked after saving; leave the mask unchanged to keep the stored password." {...fe('sandbox.stream_password')}>
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Form.Item name="sandbox.hls_base" label="Reference HLS base (stored only, needs a portal session)" {...fe('sandbox.hls_base')}>
              <Input placeholder="https://cctv.corp8.cloud" />
            </Form.Item>
            <Space style={{ display: 'flex' }} align="start" wrap>
              <Form.Item name="sandbox.probe_timeout_s" label="Import probe timeout (s)" style={{ width: 190 }} {...fe('sandbox.probe_timeout_s')}>
                <InputNumber min={3} max={60} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="sandbox.probe_parallel" label="Parallel probes (max 6)" style={{ width: 190 }} extra="Pace the load: each probe opens its own copy of the stream." {...fe('sandbox.probe_parallel')}>
                <InputNumber min={1} max={6} style={{ width: '100%' }} />
              </Form.Item>
            </Space>
            <Space>
              <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={save.isPending}>
                Save
              </Button>
              {testButton}
            </Space>
          </Card>
          <div style={{ display: 'grid', gap: 12 }}>
            <Card size="small" title="Portal login (optional)">
              <Typography.Paragraph type="secondary" style={{ marginTop: 0, fontSize: 12 }}>
                With a portal password the importer logs in to the portal itself (cookie session) and downloads <code>/cameras.json</code>. Without it - the usual case - it uses the last uploaded <code>cameras.json</code> (Import page) or the server-side copy and says so in the import warnings.
              </Typography.Paragraph>
              <Form.Item name="catalogue.portal_url" label="Portal URL" {...fe('catalogue.portal_url')}>
                <Input placeholder="https://cctv.corp8.cloud" />
              </Form.Item>
              <Form.Item name="catalogue.portal_email" label="Portal e-mail" {...fe('catalogue.portal_email')}>
                <Input autoComplete="off" />
              </Form.Item>
              <Form.Item name="catalogue.portal_password" label="Portal password" extra="Masked after saving. Empty = no portal login (file fallback)." {...fe('catalogue.portal_password')}>
                <Input.Password autoComplete="new-password" />
              </Form.Item>
              <Form.Item name="catalogue.cameras_json_path" label="Server-side cameras.json (fallback)" {...fe('catalogue.cameras_json_path')}>
                <Input placeholder="/app/media/cameras.json" />
              </Form.Item>
              <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={save.isPending}>
                Save
              </Button>
            </Card>
            <Card size="small" title="Enrichment CSV (team-inferred coordinates and departments)">
              <Typography.Paragraph type="secondary" style={{ marginTop: 0, fontSize: 12 }}>
                <code>external_id,name,lat,lon,district,city,police_station,department_code,camera_type_guess,location_confidence,location_source,notes</code> keyed by the catalogue id. Confidence <em>exact / approx / guess</em> drives the map marker style. Missing department = UNASSIGNED.
              </Typography.Paragraph>
              <Form.Item name="catalogue.enrichment_path" label="Server-side path" {...fe('catalogue.enrichment_path')}>
                <Input placeholder="/app/media/cameras_enrichment.csv" />
              </Form.Item>
              <Upload accept=".csv,text/csv" maxCount={1} showUploadList={false} beforeUpload={(f) => { upload.mutate(f); return false; }}>
                <Button icon={<UploadOutlined />} loading={upload.isPending}>
                  Upload enrichment CSV
                </Button>
              </Upload>
              {upload.isError ? <Alert type="error" showIcon style={{ marginTop: 8 }} message={errorMessage(upload.error)} /> : null}
              {enrich ? (
                <Alert
                  type={enrich.warnings.length ? 'warning' : 'success'}
                  showIcon
                  style={{ marginTop: 8 }}
                  message={`${enrich.rows} rows · ${enrich.with_coordinates} with coordinates · exact ${enrich.confidence.exact} / approx ${enrich.confidence.approx} / guess ${enrich.confidence.guess}`}
                  description={
                    <span style={{ fontSize: 12 }}>
                      Saved as <code>{enrich.path}</code> · departments {enrich.departments.join(', ') || 'none'}
                      {enrich.unknown_columns.length ? ` · ignored columns ${enrich.unknown_columns.join(', ')}` : ''}
                      {enrich.warnings.length ? <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{enrich.warnings.slice(0, 8).map((w) => <li key={w}>{w}</li>)}</ul> : null}
                    </span>
                  }
                />
              ) : null}
            </Card>
          </div>
        </div>
      ) : (
        <div className="sg-grid sg-grid-2" style={{ alignItems: 'start' }}>
          <Card size="small" title="Catalogue host and credentials">
            <Alert type="info" showIcon style={{ marginBottom: 12 }} message="Phase 2 onboarding" description="Point this at the on-site environment's host; Import from catalogue then onboards every camera in one click. The importer calls {base_url}/api/ingest." />
            <Form.Item name="catalogue.base_url" label="Base URL" rules={[{ required: true }, { pattern: /^https?:\/\//, message: 'http(s)://host[:port][/prefix]' }]} {...fe('catalogue.base_url')}>
              <Input placeholder="http://10.0.0.5" />
            </Form.Item>
            <Space style={{ display: 'flex' }} align="start">
              <Form.Item name="catalogue.auth_type" label="Authentication" style={{ width: 180 }} {...fe('catalogue.auth_type')}>
                <Select options={[{ value: 'none', label: 'None' }, { value: 'basic', label: 'HTTP basic' }, { value: 'bearer', label: 'Bearer token' }, { value: 'header', label: 'Custom header' }]} />
              </Form.Item>
              <Form.Item name="catalogue.timeout_s" label="Timeout (s)" style={{ width: 120 }} {...fe('catalogue.timeout_s')}>
                <InputNumber min={5} max={300} style={{ width: '100%' }} />
              </Form.Item>
            </Space>
            {authType === 'basic' ? (
              <Form.Item name="catalogue.auth_username" label="Username" {...fe('catalogue.auth_username')}>
                <Input autoComplete="off" />
              </Form.Item>
            ) : null}
            {authType === 'basic' || authType === 'bearer' ? (
              <Form.Item name="catalogue.auth_password" label={authType === 'bearer' ? 'Token' : 'Password'} extra="Masked after saving; leave the mask unchanged to keep the stored secret." {...fe('catalogue.auth_password')}>
                <Input.Password autoComplete="new-password" />
              </Form.Item>
            ) : null}
            {authType === 'header' ? (
              <Form.Item name="catalogue.auth_header" label="Header (Name: value)" {...fe('catalogue.auth_header')}>
                <Input.Password placeholder="X-Api-Key: abc123" autoComplete="off" />
              </Form.Item>
            ) : null}
            <Space>
              <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={save.isPending}>
                Save
              </Button>
              {testButton}
            </Space>
          </Card>
          <Card size="small" title="Field map and department aliases">
            <Typography.Paragraph type="secondary" style={{ marginTop: 0, fontSize: 12 }}>Each target field lists candidate source paths in order (dotted paths allowed, e.g. <code>location.lat</code>); the first present, non-empty value wins. Wrapper objects (<code>cameras</code>, <code>data</code>, <code>items</code>, <code>results</code>, <code>streams</code>) are unwrapped automatically.</Typography.Paragraph>
            <Form.Item name="catalogue.field_map" label="Field map (JSON)" {...fe('catalogue.field_map')}>
              <Input.TextArea rows={14} style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }} spellCheck={false} />
            </Form.Item>
            <Form.Item name="catalogue.department_aliases" label="Department aliases (lower-case name → code, JSON)" {...fe('catalogue.department_aliases')}>
              <Input.TextArea rows={8} style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }} spellCheck={false} />
            </Form.Item>
            <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={save.isPending}>
              Save
            </Button>
          </Card>
        </div>
      )}
    </Form>
  );
}

function NumbersTab({ items, title, description, fields }: { items: SettingItem[]; title: string; description: string; fields: { key: string; label: string; min?: number; max?: number; step?: number; suffix?: string; help?: string }[] }) {
  const keys = fields.map((f) => f.key);
  const { form, save, fe } = useSettingsForm(items, keys);
  return (
    <Card size="small" title={title} style={{ maxWidth: 760 }}>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>{description}</Typography.Paragraph>
      <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)}>
        <div className="sg-grid sg-grid-2">
          {fields.map((f) => (
            <Form.Item key={f.key} name={f.key} label={f.label} extra={f.help} {...fe(f.key)}>
              <InputNumber min={f.min} max={f.max} step={f.step} style={{ width: '100%' }} addonAfter={f.suffix} />
            </Form.Item>
          ))}
        </div>
        <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={save.isPending}>
          Save
        </Button>
      </Form>
    </Card>
  );
}

function NotificationsTab({ items }: { items: SettingItem[] }) {
  const keys = ['notify.telegram_bot_token', 'notify.telegram_chat_id', 'notify.telegram_min_priority'];
  const { form, save, fe } = useSettingsForm(items, keys);
  return (
    <div className="sg-grid sg-grid-2" style={{ alignItems: 'start' }}>
      <Card size="small" title="Telegram (optional)">
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>One HTTP call per alert at or above the minimum priority, with the crop attached. Leave the token empty to disable.</Typography.Paragraph>
        <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)}>
          <Form.Item name="notify.telegram_bot_token" label="Bot token" extra="Masked after saving." {...fe('notify.telegram_bot_token')}>
            <Input.Password autoComplete="off" />
          </Form.Item>
          <Form.Item name="notify.telegram_chat_id" label="Chat id" {...fe('notify.telegram_chat_id')}>
            <Input />
          </Form.Item>
          <Form.Item name="notify.telegram_min_priority" label="Minimum priority" {...fe('notify.telegram_min_priority')}>
            <Select options={(['critical', 'high', 'medium', 'low'] as const).map((p) => ({ value: p, label: `${ALERT_PRIORITY[p].label} and above` }))} />
          </Form.Item>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={save.isPending}>
            Save
          </Button>
        </Form>
      </Card>
      <Card size="small" title="Built-in channels">
        <Descriptions size="small" column={1} bordered>
          <Descriptions.Item label="WebSocket">Every client receives alerts, updates and health stats in real time (scoped per department for dept admins).</Descriptions.Item>
          <Descriptions.Item label="Browser">Toast + sound in the app; OS notification when the tab is in the background (per-user opt-in in the header menu).</Descriptions.Item>
          <Descriptions.Item label="Webhooks">Signed JSON POST to any URL on alert / camera / event lifecycle - see the Webhooks tab.</Descriptions.Item>
          <Descriptions.Item label="Sound files">public/sounds/alert.wav, alert-critical.wav (CC0)</Descriptions.Item>
        </Descriptions>
      </Card>
    </div>
  );
}

function WebhooksTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['webhooks'], queryFn: async () => { const r = await webhooksApi.list(); return Array.isArray(r) ? r : r.items; } });
  const [editing, setEditing] = useState<Webhook | null | 'new'>(null);
  const [remove, setRemove] = useState<Webhook | null>(null);
  const [form] = Form.useForm<WebhookInput>();
  const [testResult, setTestResult] = useState<Record<number, { status: number | null; duration_ms: number; error: string | null }>>({});
  useEffect(() => {
    if (editing === 'new') {
      form.resetFields();
      form.setFieldsValue({ event_types: ['alert.created'], is_active: true });
    } else if (editing) form.setFieldsValue({ name: editing.name, url: editing.url, event_types: editing.event_types, is_active: editing.is_active });
  }, [editing, form]);
  const save = useMutation({
    mutationFn: (v: WebhookInput) => (editing && editing !== 'new' ? webhooksApi.update(editing.id, v) : webhooksApi.create(v)),
    onSuccess: () => {
      message.success('Webhook saved');
      qc.invalidateQueries({ queryKey: ['webhooks'] });
      setEditing(null);
    },
  });
  const del = useMutation({
    mutationFn: (id: number) => webhooksApi.remove(id),
    onSuccess: () => {
      message.success('Webhook deleted');
      qc.invalidateQueries({ queryKey: ['webhooks'] });
      setRemove(null);
    },
  });
  const test = useMutation({
    mutationFn: (id: number) => webhooksApi.test(id),
    onSuccess: (r, id) => {
      setTestResult((m) => ({ ...m, [id]: r }));
      qc.invalidateQueries({ queryKey: ['webhooks'] });
      message[r.error ? 'error' : 'success'](r.error ? `Delivery failed: ${r.error}` : `Ping delivered · HTTP ${r.status} · ${r.duration_ms} ms`);
    },
  });
  return (
    <Card size="small" title="Outbound webhooks" extra={<Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setEditing('new')}>Add webhook</Button>} styles={{ body: { padding: 0 } }}>
      {q.isError ? <div style={{ padding: 16 }}><ErrorState error={q.error} onRetry={() => void q.refetch()} /></div> : null}
      {q.data && !q.data.length ? (
        <EmptyState compact title="No webhooks configured" description="Add a URL to receive signed JSON (X-Sentinel-Signature, HMAC-SHA256) on alert.created, alert.updated, camera.offline, camera.online and event.created. Retries after 2, 4 and 8 s." actions={<Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing('new')}>Add webhook</Button>} />
      ) : (
        <Table<Webhook>
          size="small"
          rowKey="id"
          loading={q.isLoading}
          dataSource={q.data ?? []}
          pagination={false}
          columns={[
            { title: 'Name', dataIndex: 'name', width: 200, render: (v: string, w) => <span><strong>{v}</strong>{!w.is_active ? <Tag style={{ marginLeft: 6 }}>inactive</Tag> : null}</span> },
            { title: 'URL', dataIndex: 'url', ellipsis: true, render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code> },
            { title: 'Events', dataIndex: 'event_types', width: 260, render: (v: string[]) => <Space size={4} wrap>{v.map((e) => <Tag key={e} style={{ margin: 0 }}>{e}</Tag>)}</Space> },
            { title: 'Secret', dataIndex: 'secret', width: 80, render: (v: string | null) => (v ? <Tag color="green" style={{ margin: 0 }}>signed</Tag> : <span style={{ color: '#9CA3AF' }}>none</span>) },
            { title: 'Last delivery', width: 220, render: (_v, w) => (w.last_delivered_at ? <span><Tag color={w.last_status && w.last_status < 300 ? 'green' : 'red'} style={{ margin: 0 }}>HTTP {w.last_status}</Tag> <IstTime value={w.last_delivered_at} muted />{w.last_error ? <div style={{ fontSize: 11, color: '#DC2626' }}>{w.last_error}</div> : null}</span> : <span style={{ color: '#9CA3AF' }}>never</span>) },
            { title: '', width: 210, render: (_v, w) => <Space size={4}><Button size="small" icon={<SendOutlined />} loading={test.isPending && test.variables === w.id} onClick={() => test.mutate(w.id)}>Test</Button><Button size="small" onClick={() => setEditing(w)}>Edit</Button><Button size="small" danger icon={<DeleteOutlined />} onClick={() => setRemove(w)} aria-label="Delete" /></Space> },
          ]}
          expandable={{ expandedRowRender: (w) => (testResult[w.id] ? <span style={{ fontSize: 12 }}>Last test: HTTP {testResult[w.id].status ?? '—'} in {testResult[w.id].duration_ms} ms {testResult[w.id].error ? `· ${testResult[w.id].error}` : ''}</span> : <span style={{ fontSize: 12, color: '#6B7280' }}>Payload: {`{event, ts, delivery_id, data}`} with headers X-Sentinel-Event, X-Sentinel-Delivery, X-Sentinel-Signature, User-Agent SentinelGujarat/1.0.0-phase1.</span>) }}
        />
      )}
      <Modal open={editing !== null} onCancel={() => setEditing(null)} title={editing === 'new' ? 'Add webhook' : 'Edit webhook'} okText="Save" okButtonProps={{ loading: save.isPending }} onOk={async () => save.mutate(await form.validateFields())} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="Name" rules={[{ required: true, max: 80 }]}>
            <Input />
          </Form.Item>
          <Form.Item name="url" label="URL" rules={[{ required: true }, { pattern: /^https?:\/\//, message: 'http(s) URL' }]} extra="Local test sink: http://api:8000/api/mock-sandbox/webhook-sink">
            <Input />
          </Form.Item>
          <Form.Item name="secret" label="Signing secret (optional)" extra="HMAC-SHA256 of the body; leave blank to keep the existing secret when editing.">
            <Input.Password autoComplete="off" />
          </Form.Item>
          <Form.Item name="event_types" label="Events" rules={[{ required: true }]}>
            <Select mode="multiple" options={EVENT_TYPES.map((e) => ({ value: e, label: e }))} />
          </Form.Item>
          <Form.Item name="is_active" label="Active" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
      <ConfirmDialog open={remove !== null} title="Delete webhook?" okText="Delete" loading={del.isPending} content={<p><strong>{remove?.name}</strong> will stop receiving deliveries immediately.</p>} onOk={() => { if (remove) del.mutate(remove.id); }} onCancel={() => setRemove(null)} />
    </Card>
  );
}

function ApiKeysTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['api-keys'], queryFn: apiKeysApi.list });
  const [createOpen, setCreateOpen] = useState(false);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [revoke, setRevoke] = useState<{ id: number; name: string } | null>(null);
  const [form] = Form.useForm<{ name: string; scope: 'bulk' | 'internal' }>();
  const create = useMutation({
    mutationFn: (v: { name: string; scope: 'bulk' | 'internal' }) => apiKeysApi.create(v.name, v.scope),
    onSuccess: (k) => {
      setCreated(k);
      setCreateOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['api-keys'] });
    },
  });
  const rev = useMutation({
    mutationFn: (id: number) => apiKeysApi.revoke(id),
    onSuccess: () => {
      message.success('API key revoked');
      qc.invalidateQueries({ queryKey: ['api-keys'] });
      setRevoke(null);
    },
  });
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      message.success('Key copied');
    } catch {
      message.warning('Clipboard unavailable - select and copy the key manually');
    }
  };
  return (
    <div>
      {created ? (
        <Alert
          type="success"
          showIcon
          icon={<KeyOutlined />}
          closable
          onClose={() => setCreated(null)}
          style={{ marginBottom: 12 }}
          message={`API key "${created.name}" created (scope ${created.scope}) - copy it now, it is shown only once`}
          description={
            <Space>
              <code style={{ fontSize: 13, background: '#F3F4F6', padding: '4px 8px', borderRadius: 4 }}>{created.key}</code>
              <Button size="small" icon={<CopyOutlined />} onClick={() => void copy(created.key)}>
                Copy
              </Button>
            </Space>
          }
        />
      ) : null}
      <Card size="small" title="API keys" extra={<Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>Create key</Button>} styles={{ body: { padding: 0 } }}>
        {q.isError ? <div style={{ padding: 16 }}><ErrorState error={q.error} onRetry={() => void q.refetch()} /></div> : null}
        <Table
          size="small"
          rowKey="id"
          loading={q.isLoading}
          dataSource={q.data?.items ?? []}
          pagination={false}
          columns={[
            { title: 'Name', dataIndex: 'name', render: (v: string, k) => <span><strong>{v}</strong>{!k.is_active ? <Tag style={{ marginLeft: 6 }}>revoked</Tag> : null}</span> },
            { title: 'Key', dataIndex: 'key_prefix', width: 180, render: (v: string) => <code>sk_{v}…</code> },
            { title: 'Scope', dataIndex: 'scope', width: 110, render: (v: string) => <Tooltip title={v === 'bulk' ? 'POST /api/v1/cameras/bulk only' : 'ANPR worker → /api/internal/* only'}><Tag color={v === 'bulk' ? 'blue' : 'purple'} style={{ margin: 0 }}>{v}</Tag></Tooltip> },
            { title: 'Created by', dataIndex: 'created_by_username', width: 140, render: (v: string | null) => v ?? '—' },
            { title: 'Created', dataIndex: 'created_at', width: 150, render: (v: string) => <IstTime value={v} /> },
            { title: 'Last used', dataIndex: 'last_used_at', width: 150, render: (v: string | null) => <IstTime value={v} mode="relative" /> },
            { title: '', width: 100, render: (_v, k) => (k.is_active ? <Button size="small" danger onClick={() => setRevoke({ id: k.id, name: k.name })}>Revoke</Button> : null) },
          ]}
        />
      </Card>
      <Typography.Text type="secondary" style={{ display: 'block', marginTop: 10, fontSize: 12 }}>
        Keys are stored as SHA-256 hashes. Format <code>sk_</code> + 40 lower-case alphanumerics. Requests authenticated by key are audited as <code>apikey:&lt;name&gt;</code>.
      </Typography.Text>
      <Modal open={createOpen} onCancel={() => setCreateOpen(false)} title="Create API key" okText="Create" okButtonProps={{ loading: create.isPending }} onOk={async () => create.mutate(await form.validateFields())} destroyOnClose>
        <Form form={form} layout="vertical" initialValues={{ scope: 'bulk' }}>
          <Form.Item name="name" label="Name" rules={[{ required: true, max: 64 }]} extra="e.g. gsrtc-integration, health-dept-vms">
            <Input autoFocus />
          </Form.Item>
          <Form.Item name="scope" label="Scope">
            <Select options={[{ value: 'bulk', label: 'bulk - departmental bulk onboarding' }, { value: 'internal', label: 'internal - ANPR worker' }]} />
          </Form.Item>
        </Form>
      </Modal>
      <ConfirmDialog open={revoke !== null} title="Revoke API key?" okText="Revoke" loading={rev.isPending} content={<p>Requests with <strong>{revoke?.name}</strong> will be rejected with 401 immediately. This cannot be undone.</p>} onOk={() => { if (revoke) rev.mutate(revoke.id); }} onCancel={() => setRevoke(null)} />
    </div>
  );
}

export function SettingsPage() {
  const { tab } = useParams();
  const navigate = useNavigate();
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const active: TabKey = TABS.includes(tab as TabKey) ? (tab as TabKey) : 'catalogue';
  const q = useQuery({ queryKey: ['settings', 'all'], queryFn: settingsApi.list, enabled: isAdmin });

  if (q.isLoading) return <PageSkeleton cards={2} />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const items = q.data?.items ?? [];

  return (
    <div>
      <PageHeader title="Settings" description="Runtime configuration stored in the database (seeded from the environment on first start). Changes apply immediately and are audited with before/after values; secrets are masked." />
      <Tabs
        activeKey={active}
        onChange={(k) => navigate(k === 'users' ? '/users' : `/settings/${k}`)}
        items={[
          { key: 'catalogue', label: 'Catalogue', children: <CatalogueTab items={items} /> },
          {
            key: 'retention',
            label: 'Retention & privacy',
            children: (
              <NumbersTab
                items={items}
                title="Retention and privacy"
                description="A nightly job (02:30 IST) deletes expired reads with their crops, sighting frames, clips, reports and exports, then writes the counts to the audit log. Only metadata and plate crops leave the edge; raw video is never centralised in Model 2. Every export carries a purpose-limitation notice and a watermark."
                fields={[
                  { key: 'retention.days_reads', label: 'Plate reads and crops', min: 1, max: 3650, suffix: 'days', help: 'Alerts keep the plate text after the read is purged.' },
                  { key: 'retention.days_frames', label: 'Full frames', min: 1, max: 3650, suffix: 'days' },
                  { key: 'retention.days_clips', label: 'Clips, reports and exports', min: 1, max: 3650, suffix: 'days' },
                ]}
              />
            ),
          },
          {
            key: 'alerts',
            label: 'Alerts & routes',
            children: (
              <div style={{ display: 'grid', gap: 12 }}>
                <NumbersTab
                  items={items}
                  title="Alert matching"
                  description="Suppression attaches repeat reads of the same plate on the same camera to the open alert instead of raising a new one. Fuzzy (Levenshtein 1) matches require the read confidence to be at least the threshold and are marked 'possible' with priority stepped down one level."
                  fields={[
                    { key: 'alerts.suppression_s', label: 'Suppression window', min: 0, max: 3600, suffix: 's' },
                    { key: 'alerts.fuzzy_min_conf', label: 'Fuzzy match minimum confidence', min: 0, max: 1, step: 0.05 },
                    { key: 'alerts.escalate_minutes', label: 'Escalate unacknowledged after', min: 1, max: 1440, suffix: 'min' },
                  ]}
                />
                <NumbersTab
                  items={items}
                  title="Route reconstruction and gap analysis"
                  description="Speed above the threshold between consecutive sightings raises an amber plausibility flag. Gap-analysis defaults are used by the page and the report unless overridden per request."
                  fields={[
                    { key: 'route.speed_flag_kmh', label: 'Implausible speed above', min: 30, max: 400, suffix: 'km/h' },
                    { key: 'route.default_window_h', label: 'Default search window', min: 1, max: 720, suffix: 'h' },
                    { key: 'gap.coverage_radius_m', label: 'Coverage radius', min: 25, max: 2000, suffix: 'm' },
                    { key: 'gap.poi_radius_m', label: 'POI radius', min: 50, max: 5000, suffix: 'm' },
                    { key: 'gap.grid_m', label: 'Zero-coverage grid', min: 100, max: 5000, suffix: 'm' },
                    { key: 'gap.ageing_years', label: 'Ageing threshold', min: 1, max: 30, suffix: 'years' },
                  ]}
                />
              </div>
            ),
          },
          { key: 'notifications', label: 'Notifications', children: <NotificationsTab items={items} /> },
          { key: 'webhooks', label: <span><ApiOutlined /> Webhooks</span>, children: <WebhooksTab /> },
          { key: 'api-keys', label: <span><KeyOutlined /> API keys</span>, children: <ApiKeysTab /> },
          { key: 'users', label: <span><TeamOutlined /> Users</span>, children: null },
        ]}
      />
      <div style={{ marginTop: 12, fontSize: 12, color: '#6B7280' }}>
        <CheckCircleOutlined style={{ color: '#16A34A' }} /> Public UI settings (product name, map centre and zoom) are read by every client from <code>GET /api/settings/public</code>.
      </div>
    </div>
  );
}
