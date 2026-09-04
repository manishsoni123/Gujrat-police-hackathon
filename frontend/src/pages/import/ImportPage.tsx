/**
 * Camera onboarding (/cameras/import): sandbox catalogue pull with timed summary,
 * CSV upload with per-row errors + error CSV, and the bulk API card.
 */
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Checkbox, Descriptions, Space, Statistic, Table, Tag, Typography, Upload, message } from 'antd';
import { ApiOutlined, CloudDownloadOutlined, CloudUploadOutlined, DownloadOutlined, FileTextOutlined, InboxOutlined, LinkOutlined } from '@ant-design/icons';
import { PageHeader } from '@/components/PageHeader';
import { camerasApi, settingsApi } from '@/api';
import { downloadFile, errorMessage } from '@/api/client';
import type { CsvImportResult, ImportWarning, SandboxImportResult } from '@/api/types';
import { useAuthStore, usePermission } from '@/store/auth';
import { fmtDuration } from '@/utils/time';
import { IstTime } from '@/components/IstTime';
import { usePublicSettings } from '@/hooks/useCamerasOptions';

const BULK_CURL = `curl -X POST https://<host>/api/v1/cameras/bulk \\
  -H "X-API-Key: sk_<your bulk key>" \\
  -H "Content-Type: application/json" \\
  -d '{"cameras": [{
        "external_id": "GSRTC-101",
        "name": "Mehsana Depot Gate",
        "department_code": "GSRTC",
        "lat": 23.588, "lon": 72.369,
        "district": "Mehsana",
        "rtsp_url": "rtsp://10.0.0.5:554/ch1",
        "codec": "H264"
      }]}'`;

function IssueTable({ rows, kind }: { rows: ImportWarning[]; kind: 'error' | 'warning' }) {
  if (!rows.length) return null;
  return (
    <Table
      size="small"
      rowKey={(r, i) => `${r.row ?? r.index ?? 0}-${r.field ?? ''}-${i}`}
      pagination={rows.length > 8 ? { pageSize: 8, size: 'small' } : false}
      dataSource={rows}
      style={{ marginTop: 8 }}
      columns={[
        { title: 'Row', dataIndex: 'row', width: 70, render: (v: number | undefined, r) => v ?? r.index ?? '—' },
        { title: 'External id', dataIndex: 'external_id', width: 120, render: (v: string | undefined) => v ?? '—' },
        { title: 'Field', dataIndex: 'field', width: 140, render: (v: string | undefined) => (v ? <code>{v}</code> : '—') },
        { title: kind === 'error' ? 'Error' : 'Warning', dataIndex: 'message', render: (v: string) => <span style={{ color: kind === 'error' ? '#B91C1C' : '#92400E' }}>{v}</span> },
      ]}
    />
  );
}

function SandboxCard() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const canSettings = usePermission('admin.settings');
  const settings = useQuery({ queryKey: ['settings', 'all'], queryFn: settingsApi.list, enabled: canSettings, staleTime: 60_000 });
  const pub = usePublicSettings();
  const [measure, setMeasure] = useState(true);
  const [dryRun, setDryRun] = useState(false);
  const [result, setResult] = useState<SandboxImportResult | null>(null);
  const run = useMutation({
    mutationFn: () => camerasApi.importSandbox(measure, dryRun),
    onSuccess: (r) => {
      setResult(r);
      qc.invalidateQueries({ queryKey: ['cameras'] });
      qc.invalidateQueries({ queryKey: ['geo'] });
      message.success(dryRun ? `Dry run: ${r.fetched} cameras fetched, nothing written` : `${r.added} added · ${r.updated} updated · ${r.unchanged} unchanged`);
    },
  });
  const baseUrl = useMemo(() => {
    const v = settings.data?.items.find((s) => s.key === 'catalogue.base_url')?.value;
    return typeof v === 'string' ? v : null;
  }, [settings.data]);
  const authType = settings.data?.items.find((s) => s.key === 'catalogue.auth_type')?.value;

  return (
    <Card
      title={
        <Space>
          <CloudDownloadOutlined /> Catalogue import
        </Space>
      }
      extra={pub.data?.mock_sandbox ? <Tag color="orange" style={{ margin: 0 }}>MOCK SANDBOX</Tag> : null}
    >
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Pulls <code>GET {'{host}'}/api/ingest</code>, maps fields through the configurable field map, upserts every camera and creates its relay path. The elapsed time is the onboarding-efficiency figure shown to the jury.
      </Typography.Paragraph>
      <Descriptions size="small" column={1} bordered style={{ marginBottom: 12 }}>
        <Descriptions.Item label="Catalogue host">
          {baseUrl ? <code>{baseUrl}</code> : canSettings ? <span style={{ color: '#9CA3AF' }}>loading…</span> : <span style={{ color: '#6B7280' }}>Configured by an administrator in Settings → Catalogue</span>}
          {canSettings ? (
            <Button type="link" size="small" icon={<LinkOutlined />} onClick={() => navigate('/settings/catalogue')} style={{ marginLeft: 8 }}>
              change
            </Button>
          ) : null}
        </Descriptions.Item>
        <Descriptions.Item label="Authentication">{typeof authType === 'string' ? (authType === 'none' ? 'none' : authType) : '—'}</Descriptions.Item>
      </Descriptions>
      {!isAdmin ? <Alert type="info" showIcon message="A catalogue spans departments, so only administrators can run the catalogue import. Use the CSV import for your own department." style={{ marginBottom: 12 }} /> : null}
      <Space wrap>
        <Button type="primary" icon={<CloudDownloadOutlined />} loading={run.isPending} disabled={!isAdmin} onClick={() => run.mutate()}>
          {dryRun ? 'Dry run' : 'Import from catalogue'}
        </Button>
        <Checkbox checked={measure} onChange={(e) => setMeasure(e.target.checked)}>
          Measure first stream
        </Checkbox>
        <Checkbox checked={dryRun} onChange={(e) => setDryRun(e.target.checked)}>
          Dry run
        </Checkbox>
      </Space>
      {run.isError ? <Alert type="error" showIcon style={{ marginTop: 12 }} message="Import failed" description={errorMessage(run.error)} /> : null}
      {result ? (
        <div style={{ marginTop: 16 }}>
          <Alert
            type={result.errors.length ? 'warning' : 'success'}
            showIcon
            message={
              <span>
                <strong>{result.fetched}</strong> cameras fetched · <strong>{result.added}</strong> added · <strong>{result.updated}</strong> updated · <strong>{result.unchanged}</strong> unchanged
                {result.dry_run ? ' (dry run - nothing written)' : ''}
              </span>
            }
            description={
              <span>
                Onboarded in <strong>{fmtDuration(result.duration_ms / 1000)}</strong>
                {result.first_stream_ready_ms !== null ? (
                  <>
                    {' '}· first stream ready in <strong>{(result.first_stream_ready_ms / 1000).toFixed(1)} s</strong>
                  </>
                ) : null}{' '}
                · {result.anpr_enabled} cameras ANPR-enabled · {result.relay_paths_created} relay paths created{result.relay_paths_failed ? `, ${result.relay_paths_failed} failed` : ''} · source <code>{result.source_url}</code>
              </span>
            }
          />
          <div className="sg-grid sg-grid-4" style={{ marginTop: 12 }}>
            <Statistic title="Fetched" value={result.fetched} />
            <Statistic title="Added / updated" value={`${result.added} / ${result.updated}`} />
            <Statistic title="Duration" value={(result.duration_ms / 1000).toFixed(1)} suffix="s" />
            <Statistic title="First stream" value={result.first_stream_ready_ms === null ? '—' : (result.first_stream_ready_ms / 1000).toFixed(1)} suffix={result.first_stream_ready_ms === null ? '' : 's'} />
          </div>
          <div style={{ fontSize: 12, color: '#6B7280', marginTop: 8 }}>
            Started <IstTime value={result.started_at} mode="full" /> · finished <IstTime value={result.finished_at} mode="full" />
          </div>
          <IssueTable rows={result.errors} kind="error" />
          <IssueTable rows={result.warnings} kind="warning" />
          <Space style={{ marginTop: 12 }}>
            <Button onClick={() => navigate('/cameras')}>Open registry</Button>
            <Button onClick={() => navigate('/map')}>Show on map</Button>
          </Space>
        </div>
      ) : null}
    </Card>
  );
}

function CsvCard() {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [dryRun, setDryRun] = useState(false);
  const [result, setResult] = useState<CsvImportResult | null>(null);
  const run = useMutation({
    mutationFn: (f: File) => camerasApi.importCsv(f, dryRun),
    onSuccess: (r) => {
      setResult(r);
      qc.invalidateQueries({ queryKey: ['cameras'] });
      qc.invalidateQueries({ queryKey: ['geo'] });
      message[r.errors.length ? 'warning' : 'success'](`${r.rows_total} rows · ${r.added} added · ${r.updated} updated · ${r.errors.length} error${r.errors.length === 1 ? '' : 's'}`);
    },
  });
  const downloadErrors = async () => {
    if (!result?.error_report_url) return;
    try {
      await downloadFile(result.error_report_url, undefined, `import_errors_${result.job_id}.csv`);
    } catch (e) {
      message.error(errorMessage(e));
    }
  };
  return (
    <Card
      title={
        <Space>
          <FileTextOutlined /> CSV import
        </Space>
      }
      extra={
        <Button size="small" icon={<DownloadOutlined />} onClick={() => camerasApi.template().catch((e) => message.error(errorMessage(e)))}>
          Template
        </Button>
      }
    >
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        UTF-8, comma-separated, header row from the template (any column order; unknown columns are ignored with a warning). Rows are validated individually - valid rows are committed, invalid rows are listed with a downloadable error CSV.
      </Typography.Paragraph>
      <Upload.Dragger
        accept=".csv,text/csv"
        maxCount={1}
        beforeUpload={(f) => {
          setFile(f);
          setResult(null);
          return false;
        }}
        onRemove={() => setFile(null)}
        style={{ marginBottom: 12 }}
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">Click or drop a CSV file here</p>
        <p className="ant-upload-hint">Up to 200 MB / 20 000 rows. Use cameras_sample.csv from the repository for the jury demo (two rows are deliberately invalid).</p>
      </Upload.Dragger>
      <Space wrap>
        <Button type="primary" icon={<CloudUploadOutlined />} disabled={!file} loading={run.isPending} onClick={() => file && run.mutate(file)}>
          {dryRun ? 'Validate only' : 'Import CSV'}
        </Button>
        <Checkbox checked={dryRun} onChange={(e) => setDryRun(e.target.checked)}>
          Dry run (validate, write nothing)
        </Checkbox>
      </Space>
      {run.isError ? <Alert type="error" showIcon style={{ marginTop: 12 }} message="Import failed" description={errorMessage(run.error)} /> : null}
      {result ? (
        <div style={{ marginTop: 16 }}>
          <Alert
            type={result.errors.length ? 'warning' : 'success'}
            showIcon
            message={
              <span>
                <strong>{result.rows_total}</strong> rows · <strong>{result.added}</strong> added · <strong>{result.updated}</strong> updated · <strong>{result.errors.length}</strong> error{result.errors.length === 1 ? '' : 's'} · {result.warnings.length} warning{result.warnings.length === 1 ? '' : 's'}
                {result.dry_run ? ' (dry run)' : ''}
              </span>
            }
            description={`Job ${result.job_id} · ${result.duration_ms} ms · ${result.relay_paths_created} relay paths created${result.relay_paths_failed ? `, ${result.relay_paths_failed} failed` : ''}`}
            action={
              result.error_report_url ? (
                <Button size="small" icon={<DownloadOutlined />} onClick={() => void downloadErrors()}>
                  Error CSV
                </Button>
              ) : undefined
            }
          />
          <IssueTable rows={result.errors} kind="error" />
          <IssueTable rows={result.warnings} kind="warning" />
        </div>
      ) : null}
    </Card>
  );
}

function BulkApiCard() {
  const canKeys = usePermission('admin.apikeys');
  const navigate = useNavigate();
  return (
    <Card
      title={
        <Space>
          <ApiOutlined /> Bulk API (departmental systems)
        </Space>
      }
      extra={
        <Button size="small" href="/api/docs" target="_blank" rel="noreferrer" icon={<LinkOutlined />}>
          OpenAPI docs
        </Button>
      }
    >
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Departments push cameras with <code>POST /api/v1/cameras/bulk</code> and an API key of scope <code>bulk</code>. Same schema and validation as the CSV; up to 1 000 cameras per call; per-row results returned.
      </Typography.Paragraph>
      <pre className="sg-json" style={{ maxHeight: 260 }}>{BULK_CURL}</pre>
      <Space style={{ marginTop: 12 }} wrap>
        <Button
          size="small"
          onClick={() => {
            navigator.clipboard.writeText(BULK_CURL).then(() => message.success('curl example copied')).catch(() => message.warning('Clipboard unavailable'));
          }}
        >
          Copy example
        </Button>
        {canKeys ? (
          <Button size="small" onClick={() => navigate('/settings/api-keys')}>
            Manage API keys
          </Button>
        ) : null}
      </Space>
    </Card>
  );
}

export function ImportPage() {
  return (
    <div>
      <PageHeader title="Import cameras" description="Three onboarding paths with identical validation: pull the organiser catalogue, upload a CSV, or let departmental systems push through the bulk API." />
      <div className="sg-grid sg-grid-2" style={{ alignItems: 'start' }}>
        <SandboxCard />
        <div style={{ display: 'grid', gap: 12 }}>
          <CsvCard />
          <BulkApiCard />
        </div>
      </div>
    </div>
  );
}
