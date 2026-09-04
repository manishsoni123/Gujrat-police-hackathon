/**
 * Reports (/reports): Output report (CSV/PDF), Route report (PDF), Gap report (CSV/PDF),
 * Analytics quality (JSON view + PDF + spot-check labelling), History with SHA-256 + Verify.
 */
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Checkbox, Descriptions, Input, Modal, Select, Slider, Space, Statistic, Table, Tag, Tooltip, Typography, message } from 'antd';
import { CarOutlined, DownloadOutlined, ExperimentOutlined, FileTextOutlined, RadarChartOutlined, SafetyCertificateOutlined, ReloadOutlined, CheckOutlined } from '@ant-design/icons';
import { PageHeader } from '@/components/PageHeader';
import { evidenceApi, gapApi, reportsApi, vehiclesApi } from '@/api';
import type { EvidenceVerify, QaSampleRead, ReportFile } from '@/api/types';
import { ExportDialog } from '@/components/Dialogs';
import { RangeIst, type IsoRange } from '@/components/RangeIst';
import { IstTime } from '@/components/IstTime';
import { HashText, CropThumb } from '@/components/Misc';
import { EmptyState, ErrorState } from '@/components/States';
import { PlateText } from '@/components/PlateText';
import { useCameraOptions, useDepartments } from '@/hooks/useCamerasOptions';
import { usePermission } from '@/store/auth';
import { fmtBytes, fmtPct, humanise } from '@/utils/format';
import { fmtIst, hoursAgoIso, nowIso } from '@/utils/time';
import { normalisePlate, formatPlate } from '@/utils/plate';
import { errorMessage } from '@/api/client';

const TYPE_LABEL: Record<string, string> = {
  detections_csv: 'Output report (CSV)',
  detections_pdf: 'Output report (PDF)',
  route_pdf: 'Route report (PDF)',
  gap_csv: 'Gap analysis (CSV)',
  gap_pdf: 'Gap analysis (PDF)',
  quality_pdf: 'Analytics quality (PDF)',
  cameras_csv: 'Camera registry export',
  import_errors_csv: 'Import error report',
};

function QaModal({ open, onClose, cameraId }: { open: boolean; onClose: () => void; cameraId?: number }) {
  const qc = useQueryClient();
  const sample = useQuery({ queryKey: ['qa', 'sample', cameraId], queryFn: () => reportsApi.qaSample({ camera_id: cameraId, n: 30 }), enabled: open });
  const [labels, setLabels] = useState<Record<number, string>>({});
  const save = useMutation({
    mutationFn: () => reportsApi.qaLabels(Object.entries(labels).map(([id, v]) => ({ read_id: Number(id), true_plate: v }))),
    onSuccess: (r) => {
      message.success(`${r.saved} labels saved · ${r.exact} exact · char accuracy ${r.char_accuracy_pct} %`);
      qc.invalidateQueries({ queryKey: ['reports', 'quality'] });
      qc.invalidateQueries({ queryKey: ['qa'] });
      setLabels({});
      onClose();
    },
  });
  const items: QaSampleRead[] = sample.data?.items ?? [];
  return (
    <Modal open={open} onCancel={onClose} width={820} title="Spot-check 30 random crops" okText={`Save ${Object.keys(labels).length} label${Object.keys(labels).length === 1 ? '' : 's'}`} okButtonProps={{ disabled: !Object.keys(labels).length, loading: save.isPending }} onOk={() => save.mutate()} destroyOnClose>
      <Typography.Paragraph type="secondary">Type what a human reads on each crop. Pre-filled with the OCR result - correct it or leave blank for unreadable. Labels drive the measured accuracy in the quality report (A8).</Typography.Paragraph>
      {sample.isError ? <ErrorState error={sample.error} onRetry={() => void sample.refetch()} /> : null}
      <div style={{ maxHeight: 460, overflow: 'auto', display: 'grid', gap: 8 }}>
        {items.map((r) => (
          <div key={r.id} style={{ display: 'grid', gridTemplateColumns: '150px 1fr 1fr', gap: 12, alignItems: 'center', border: '1px solid #E5E7EB', borderRadius: 6, padding: 8 }}>
            <CropThumb src={r.crop_url} alt={`Crop ${r.plate_display}`} width={140} height={44} preview={false} />
            <div style={{ fontSize: 12 }}>
              <div>OCR: <PlateText plate={r.plate_norm} size="small" /> · {Math.round(r.confidence * 100)} %</div>
              <div style={{ color: '#6B7280' }}>{r.camera.name} · <IstTime value={r.captured_at} /></div>
            </div>
            <Input size="small" placeholder="true plate (blank = unreadable)" value={labels[r.id] ?? ''} onChange={(e) => setLabels((l) => ({ ...l, [r.id]: e.target.value }))} onFocus={() => !(r.id in labels) && setLabels((l) => ({ ...l, [r.id]: formatPlate(r.plate_norm) }))} aria-label={`True plate for read ${r.id}`} />
          </div>
        ))}
        {!sample.isLoading && !items.length ? <EmptyState compact title="No unlabelled reads" description="Every valid read in the window already has a label." /> : null}
      </div>
    </Modal>
  );
}

export function ReportsPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const canExport = usePermission('reports.export');
  const canGap = usePermission('cameras.export');
  const canLabel = usePermission('events.write');
  const { options: cameraOptions } = useCameraOptions();
  const { options: deptOptions } = useDepartments();

  const [outRange, setOutRange] = useState<IsoRange>({ from: hoursAgoIso(2), to: nowIso() });
  const [outCamera, setOutCamera] = useState<number | undefined>();
  const [outDept, setOutDept] = useState<number | undefined>();
  const [outMinConf, setOutMinConf] = useState(0);
  const [outValid, setOutValid] = useState(true);
  const [outOpen, setOutOpen] = useState(false);

  const [routePlate, setRoutePlate] = useState('GJ 01 AB 1234');
  const [routeRange, setRouteRange] = useState<IsoRange>({ from: hoursAgoIso(24), to: nowIso() });
  const [routeOpen, setRouteOpen] = useState(false);
  const routeNorm = useMemo(() => normalisePlate(routePlate), [routePlate]);

  const [gapOpen, setGapOpen] = useState(false);
  const [qualityCamera, setQualityCamera] = useState<number | undefined>();
  const [qualityOpen, setQualityOpen] = useState(false);
  const [qaOpen, setQaOpen] = useState(false);
  const quality = useQuery({ queryKey: ['reports', 'quality', qualityCamera], queryFn: () => reportsApi.quality({ camera_id: qualityCamera }) });

  const history = useQuery({ queryKey: ['reports', 'history'], queryFn: () => reportsApi.history({ page_size: 50 }), enabled: canExport });
  const [verifyResult, setVerifyResult] = useState<Record<number, EvidenceVerify>>({});
  const verify = useMutation({
    mutationFn: (f: ReportFile) => evidenceApi.verify(f.path),
    onSuccess: (r, f) => setVerifyResult((m) => ({ ...m, [f.id]: r })),
  });
  const download = async (f: ReportFile) => {
    try {
      const r = await reportsApi.download(f.url, f.path.split('/').pop() ?? 'report');
      message.success(`Downloaded ${r.filename}`);
      qc.invalidateQueries({ queryKey: ['audit'] });
    } catch (e) {
      message.error(errorMessage(e));
    }
  };

  const qr = quality.data;

  return (
    <div>
      <PageHeader title="Reports" description="Every generated file is hashed (SHA-256), watermarked with your username and IST time, recorded in the evidence ledger and audited. Exports carry a purpose-limitation notice." />
      <div className="sg-grid sg-grid-2" style={{ alignItems: 'start', marginBottom: 12 }}>
        <Card title={<Space><FileTextOutlined /> Output report - detected plates</Space>} extra={<Tag color="blue" style={{ margin: 0 }}>O1 · Phase 1 deliverable</Tag>}>
          <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>Timestamp (IST + UTC), camera id and name, department, coordinates, plate, raw OCR, confidence, sighting id, crop path and hash for every read in the window (max 7 days). PDF adds a summary, thumbnails of the first 50 reads and the analytics-quality section.</Typography.Paragraph>
          <div style={{ display: 'grid', gap: 10 }}>
            <RangeIst value={outRange} onChange={setOutRange} style={{ width: '100%' }} />
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <Select allowClear showSearch optionFilterProp="label" placeholder="All cameras" style={{ flex: 1, minWidth: 180 }} options={cameraOptions} value={outCamera} onChange={setOutCamera} aria-label="Camera" />
              <Select allowClear showSearch optionFilterProp="label" placeholder="All departments" style={{ flex: 1, minWidth: 160 }} options={deptOptions} value={outDept} onChange={setOutDept} aria-label="Department" />
            </div>
            <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
              <span style={{ fontSize: 12, color: '#6B7280', whiteSpace: 'nowrap' }}>Min confidence {outMinConf} %</span>
              <Slider min={0} max={100} step={5} value={outMinConf} onChange={setOutMinConf} style={{ flex: 1 }} />
              <Checkbox checked={outValid} onChange={(e) => setOutValid(e.target.checked)}>Valid format only</Checkbox>
            </div>
            <Button type="primary" icon={<DownloadOutlined />} disabled={!canExport || !outRange.from} onClick={() => setOutOpen(true)}>
              Generate CSV / PDF
            </Button>
            {!canExport ? <Typography.Text type="secondary" style={{ fontSize: 12 }}>Your role can view reports but not export them.</Typography.Text> : null}
          </div>
        </Card>
        <div style={{ display: 'grid', gap: 12 }}>
          <Card title={<Space><CarOutlined /> Route report</Space>} extra={<Tag color="blue" style={{ margin: 0 }}>O2 · Phase 2 output</Tag>}>
            <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>Per-plate PDF: sightings table in IST, numbered map, crops, plausibility flags, evidence hash footer.</Typography.Paragraph>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <div>
                <Input value={routePlate} onChange={(e) => setRoutePlate(e.target.value)} placeholder="GJ 01 AB 1234" style={{ width: 220, fontFamily: 'ui-monospace, monospace', letterSpacing: 1 }} aria-label="Plate" />
                <div style={{ fontSize: 12, marginTop: 4, color: routeNorm.plate_norm ? (routeNorm.is_valid_format ? '#16A34A' : '#D97706') : '#6B7280' }}>
                  {routeNorm.plate_norm ? `Stored as ${formatPlate(routeNorm.plate_norm)}${routeNorm.is_valid_format ? '' : ' (not a valid Indian format)'}` : 'Enter a registration'}
                </div>
              </div>
              <RangeIst value={routeRange} onChange={setRouteRange} style={{ width: 300 }} />
              <Button icon={<DownloadOutlined />} type="primary" disabled={!canExport || !routeNorm.plate_norm} onClick={() => setRouteOpen(true)}>
                PDF
              </Button>
              <Button onClick={() => navigate(`/vehicles/${routeNorm.plate_norm}/route`)} disabled={!routeNorm.plate_norm}>
                Open route
              </Button>
            </div>
          </Card>
          <Card title={<Space><RadarChartOutlined /> Gap-analysis report</Space>}>
            <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>Coverage by district and police station, zero-coverage cells, uncovered POIs, department gaps, ageing infrastructure and recommendations. CSV with a section column, or PDF with a static map.</Typography.Paragraph>
            <Space>
              <Button type="primary" icon={<DownloadOutlined />} disabled={!canGap} onClick={() => setGapOpen(true)}>
                Generate CSV / PDF
              </Button>
              <Button onClick={() => navigate('/gap-analysis')}>Open analysis</Button>
            </Space>
          </Card>
        </div>
      </div>

      <Card
        title={<Space><ExperimentOutlined /> Analytics quality (ANPR accuracy evidence)</Space>}
        style={{ marginBottom: 12 }}
        extra={
          <Space wrap>
            <Select allowClear showSearch optionFilterProp="label" placeholder="All live cameras" style={{ width: 240 }} options={cameraOptions} value={qualityCamera} onChange={setQualityCamera} aria-label="Camera" />
            {canLabel ? <Button icon={<CheckOutlined />} onClick={() => setQaOpen(true)}>Spot-check 30 crops</Button> : null}
            <Button icon={<ReloadOutlined />} loading={quality.isFetching} onClick={() => void quality.refetch()} aria-label="Refresh" />
            <Button type="primary" icon={<DownloadOutlined />} disabled={!canExport} onClick={() => setQualityOpen(true)}>PDF</Button>
          </Space>
        }
      >
        {quality.isError ? <ErrorState error={quality.error} onRetry={() => void quality.refetch()} /> : null}
        {qr ? (
          <div>
            <div className="sg-grid sg-grid-6" style={{ marginBottom: 12 }}>
              <Statistic title="Reads (24 h)" value={qr.reads_total} />
              <Statistic title="Valid format" value={fmtPct(qr.valid_format_pct)} />
              <Statistic title="Sightings" value={qr.sightings_total} />
              <Statistic title="Unique plates" value={qr.unique_plates} />
              <Statistic title="Mean confidence" value={Math.round(qr.mean_confidence * 100)} suffix="%" />
              <Statistic title="Labelled reads" value={qr.labelled} />
            </div>
            <div className="sg-grid sg-grid-3">
              <Card size="small" title="Measured accuracy (human-labelled sample)">
                {qr.labelled === 0 ? (
                  <EmptyState
                    compact
                    title="No labelled sample yet"
                    description={canLabel ? 'Spot-check 30 random crops to measure exact-plate and character accuracy against a human reading.' : 'An operator labels a sample of crops to measure exact-plate and character accuracy; nothing has been labelled in this window yet.'}
                    actions={canLabel ? <Button icon={<CheckOutlined />} onClick={() => setQaOpen(true)}>Spot-check 30 crops</Button> : undefined}
                  />
                ) : (
                  <div>
                    <div style={{ display: 'flex', gap: 24 }}>
                      <Statistic title="Exact plate match" value={qr.exact_match_pct ?? '—'} suffix={qr.exact_match_pct !== null ? '%' : ''} valueStyle={{ color: '#16A34A' }} />
                      <Statistic title="Character accuracy" value={qr.char_accuracy_pct ?? '—'} suffix={qr.char_accuracy_pct !== null ? '%' : ''} valueStyle={{ color: '#1E4DB7' }} />
                    </div>
                    <Table size="small" pagination={false} rowKey="camera_id" dataSource={qr.per_camera_accuracy} style={{ marginTop: 8 }} locale={{ emptyText: <EmptyState compact title="No per-camera breakdown" description="Labels exist but none fall on a camera in this window." /> }} columns={[{ title: 'Camera', dataIndex: 'camera_name', ellipsis: true }, { title: 'n', dataIndex: 'labelled', width: 50, align: 'right' }, { title: 'Exact', dataIndex: 'exact_pct', width: 80, align: 'right', render: (v: number) => fmtPct(v) }, { title: 'Chars', dataIndex: 'char_accuracy_pct', width: 80, align: 'right', render: (v: number) => fmtPct(v) }]} />
                  </div>
                )}
              </Card>
              <Card size="small" title="Reads per camera">
                <Table size="small" pagination={false} rowKey="camera_id" dataSource={qr.reads_per_camera} scroll={{ y: 220 }} locale={{ emptyText: <EmptyState compact title="No reads in the last 24 h" description="ANPR workers post reads within seconds of a plate being visible on a live camera." /> }} columns={[{ title: 'Camera', dataIndex: 'camera_name', ellipsis: true }, { title: 'Reads', dataIndex: 'reads', width: 70, align: 'right' }, { title: 'Valid', dataIndex: 'valid_pct', width: 80, align: 'right', render: (v: number) => fmtPct(v, 0) }, { title: 'Conf.', dataIndex: 'mean_conf', width: 70, align: 'right', render: (v: number) => `${Math.round(v * 100)} %` }]} />
              </Card>
              <Card size="small" title="Character confusions">
                {qr.confusions.length ? (
                  <Table size="small" pagination={false} rowKey={(r) => `${r.expected}${r.got}`} dataSource={qr.confusions} columns={[{ title: 'Expected', dataIndex: 'expected', width: 90, render: (v: string) => <code>{v}</code> }, { title: 'OCR read', dataIndex: 'got', width: 90, render: (v: string) => <code>{v}</code> }, { title: 'Count', dataIndex: 'count', align: 'right' }]} />
                ) : (
                  <EmptyState compact title="No confusions recorded yet" description="Label a sample of crops to populate this table." />
                )}
                <div style={{ fontSize: 12, color: '#6B7280', marginTop: 8 }}>The normaliser already corrects position-aware confusions (0↔O, 1↔I, 5↔S, 8↔B, 2↔Z, 6↔G); the table shows what remained.</div>
              </Card>
            </div>
          </div>
        ) : null}
      </Card>

      <Card title={<Space><SafetyCertificateOutlined /> Generated files (evidence ledger)</Space>} styles={{ body: { padding: 0 } }} extra={<Button size="small" icon={<ReloadOutlined />} loading={history.isFetching} onClick={() => void history.refetch()}>Refresh</Button>}>
        {!canExport ? (
          <EmptyState compact title="Report history is available to roles that can export" description="Operators and administrators see the files they generated with their SHA-256 hashes." />
        ) : history.isError ? (
          <div style={{ padding: 16 }}><ErrorState error={history.error} onRetry={() => void history.refetch()} /></div>
        ) : history.data && !history.data.items.length ? (
          <EmptyState compact title="No reports generated yet" description="Files you generate above appear here with their hash and can be re-verified at any time." />
        ) : (
          <Table<ReportFile>
            className="sg-table"
            size="small"
            rowKey="id"
            loading={history.isLoading}
            dataSource={history.data?.items ?? []}
            pagination={{ pageSize: 10, size: 'small', showSizeChanger: false }}
            columns={[
              { title: 'Type', dataIndex: 'type', width: 190, render: (v: string) => <Tag style={{ margin: 0 }}>{TYPE_LABEL[v] ?? humanise(v)}</Tag> },
              { title: 'File', dataIndex: 'path', ellipsis: true, render: (v: string) => <code style={{ fontSize: 12 }}>{v.split('/').pop()}</code> },
              { title: 'Rows', dataIndex: 'row_count', width: 80, align: 'right', render: (v: number | null) => v ?? '—' },
              { title: 'Size', dataIndex: 'size_bytes', width: 90, align: 'right', render: (v: number) => fmtBytes(v) },
              { title: 'By', dataIndex: 'created_by_username', width: 120, render: (v: string | null) => v ?? '—' },
              { title: 'Generated (IST)', dataIndex: 'created_at', width: 150, render: (v: string) => <IstTime value={v} /> },
              { title: 'SHA-256', dataIndex: 'sha256', width: 190, render: (v: string) => <HashText hash={v} /> },
              {
                title: '',
                width: 200,
                render: (_v, f) => (
                  <Space size={4}>
                    <Button size="small" icon={<DownloadOutlined />} onClick={() => void download(f)}>Download</Button>
                    <Tooltip title={verifyResult[f.id] ? (verifyResult[f.id].match ? `Verified ${fmtIst(verifyResult[f.id].checked_at)}` : 'Mismatch!') : 'Recompute the hash on the server and compare'}>
                      <Button size="small" icon={<SafetyCertificateOutlined />} loading={verify.isPending && verify.variables?.id === f.id} type={verifyResult[f.id] ? (verifyResult[f.id].match ? 'primary' : 'default') : 'default'} danger={verifyResult[f.id]?.match === false} onClick={() => verify.mutate(f)}>
                        {verifyResult[f.id] ? (verifyResult[f.id].match ? 'Verified' : 'Mismatch') : 'Verify'}
                      </Button>
                    </Tooltip>
                  </Space>
                ),
              },
            ]}
            expandable={{ expandedRowRender: (f) => <Descriptions size="small" column={2}><Descriptions.Item label="Path"><code>{f.path}</code></Descriptions.Item><Descriptions.Item label="Parameters"><code style={{ fontSize: 11 }}>{JSON.stringify(f.params)}</code></Descriptions.Item><Descriptions.Item label="SHA-256" span={2}><HashText hash={f.sha256} full /></Descriptions.Item></Descriptions> }}
          />
        )}
      </Card>
      {verify.isError ? <Alert type="error" showIcon style={{ marginTop: 12 }} message={errorMessage(verify.error)} /> : null}

      <ExportDialog open={outOpen} title="Output report - detected plates" filters={[{ label: 'Window (IST)', value: `${fmtIst(outRange.from)} → ${fmtIst(outRange.to)}` }, { label: 'Camera', value: outCamera ? cameraOptions.find((c) => c.value === outCamera)?.label : 'all' }, { label: 'Department', value: outDept ? deptOptions.find((d) => d.value === outDept)?.label : 'all' }, { label: 'Min confidence', value: `${outMinConf} %` }, { label: 'Valid format only', value: outValid ? 'yes' : 'no' }]} onExport={(fmt) => reportsApi.detections(fmt, { from: outRange.from, to: outRange.to, camera_id: outCamera, department_id: outDept, min_conf: outMinConf ? outMinConf / 100 : undefined, valid_only: outValid })} onClose={() => setOutOpen(false)} />
      <ExportDialog open={routeOpen} title={`Route report · ${formatPlate(routeNorm.plate_norm)}`} formats={['pdf']} filters={[{ label: 'Plate', value: formatPlate(routeNorm.plate_norm) }, { label: 'Window (IST)', value: `${fmtIst(routeRange.from)} → ${fmtIst(routeRange.to)}` }]} onExport={() => vehiclesApi.routePdf(routeNorm.plate_norm, { from: routeRange.from, to: routeRange.to })} onClose={() => setRouteOpen(false)} />
      <ExportDialog open={gapOpen} title="Gap-analysis report" filters={[{ label: 'Parameters', value: 'defaults from Settings (coverage 150 m, POI 300 m, grid 500 m, ageing 5 y)' }]} onExport={(fmt) => gapApi.export(fmt)} onClose={() => setGapOpen(false)} />
      <ExportDialog open={qualityOpen} title="Analytics quality report" formats={['pdf']} filters={[{ label: 'Camera', value: qualityCamera ? cameraOptions.find((c) => c.value === qualityCamera)?.label : 'all live cameras' }, { label: 'Window', value: 'last 24 h' }]} onExport={() => reportsApi.qualityPdf({ camera_id: qualityCamera })} onClose={() => setQualityOpen(false)} />
      <QaModal open={qaOpen} onClose={() => setQaOpen(false)} cameraId={qualityCamera} />
    </div>
  );
}
