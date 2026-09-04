/** Detections (/detections): plate-read table with crops, filters, and a detail drawer (frame, hash + verify, play recording, tag event, QA label). */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Checkbox, Descriptions, Drawer, Input, Select, Slider, Space, Table, Tag, Tooltip, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { PlayCircleOutlined, ReloadOutlined, SafetyCertificateOutlined, SearchOutlined, TagOutlined, CarOutlined, CheckOutlined } from '@ant-design/icons';
import { PageHeader } from '@/components/PageHeader';
import { useListQuery } from '@/hooks/useListQuery';
import { detectionsApi, evidenceApi, reportsApi } from '@/api';
import type { Detection, DetectionDetail, EvidenceVerify } from '@/api/types';
import { StatusTag, DepartmentTag, EventTypeTag } from '@/components/Tags';
import { IstTime } from '@/components/IstTime';
import { PlateText } from '@/components/PlateText';
import { ConfidenceBar, CropThumb, HashText } from '@/components/Misc';
import { EmptyState, ErrorState } from '@/components/States';
import { RangeIst, mediaRelativePath, type IsoRange } from '@/components/RangeIst';
import { PlayRecordingModal } from '@/components/Recordings';
import { EventForm } from '@/components/EventForm';
import { useCameraOptions, useDepartments } from '@/hooks/useCamerasOptions';
import { usePermission } from '@/store/auth';
import { normalisePlate, formatPlate } from '@/utils/plate';
import { fmtLatLon, fmtPlace, humanise } from '@/utils/format';
import { errorMessage } from '@/api/client';

/** Worker mode → operator-facing label. */
const MODE_LABEL: Record<string, string> = { live: 'Live', preindex: 'Pre-indexed' };
const modeLabel = (m: string) => MODE_LABEL[m] ?? humanise(m);

function VerifyResult({ r }: { r: EvidenceVerify }) {
  return (
    <Alert
      type={r.match ? 'success' : r.match === false ? 'error' : 'warning'}
      showIcon
      icon={<SafetyCertificateOutlined />}
      message={r.match ? 'Hash verified - file bytes match the stored SHA-256' : r.match === false ? 'Hash mismatch - file was modified after storage' : 'Path unknown to the evidence ledger'}
      description={
        <div style={{ fontSize: 12 }}>
          <div>Entity: {r.entity ?? '—'} #{r.entity_id ?? '—'} · {r.size_bytes ?? '—'} bytes · checked <IstTime value={r.checked_at} mode="full" /></div>
          <div>Stored: <code>{r.stored_sha256 ?? '—'}</code></div>
          <div>Computed: <code>{r.computed_sha256 ?? '—'}</code></div>
        </div>
      }
    />
  );
}

function DetectionDrawer({ id, onClose }: { id: number | null; onClose: () => void }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const canEvent = usePermission('events.write');
  const q = useQuery({ queryKey: ['detections', 'detail', id], queryFn: () => detectionsApi.get(id as number), enabled: id !== null });
  const [verify, setVerify] = useState<EvidenceVerify | null>(null);
  const [playOpen, setPlayOpen] = useState(false);
  const [eventOpen, setEventOpen] = useState(false);
  const [label, setLabel] = useState('');
  useEffect(() => {
    setVerify(null);
    setLabel('');
  }, [id]);
  const doVerify = useMutation({
    mutationFn: (path: string) => evidenceApi.verify(path),
    onSuccess: setVerify,
  });
  const saveLabel = useMutation({
    mutationFn: () => reportsApi.qaLabels([{ read_id: id as number, true_plate: label }]),
    onSuccess: (r) => {
      message.success(`Label saved · ${r.exact === 1 ? 'exact match' : 'mismatch recorded'} · running char accuracy ${r.char_accuracy_pct} %`);
      qc.invalidateQueries({ queryKey: ['detections'] });
    },
  });
  const d: DetectionDetail | undefined = q.data;
  const cropPath = mediaRelativePath(d?.crop_url);

  return (
    <Drawer open={id !== null} onClose={onClose} width={720} title={d ? <Space><span>Read #{d.id}</span><PlateText plate={d.plate_norm} raw={d.plate_raw} invalid={!d.is_valid_format} /> {d.watchlist_hit ? <Tag color="red" style={{ margin: 0 }}>watchlist hit</Tag> : null}</Space> : 'Plate read'} loading={q.isLoading}>
      {q.isError ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : null}
      {d ? (
        <div style={{ display: 'grid', gap: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>Plate crop</Typography.Text>
              <div style={{ marginTop: 4 }}>
                <CropThumb src={d.crop_url} alt={`Plate crop ${d.plate_display}`} width={320} height={96} sha256={d.crop_sha256} />
              </div>
            </div>
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>Full frame (best read of the sighting)</Typography.Text>
              <div style={{ marginTop: 4 }}>
                <CropThumb src={d.frame_url} alt={`Frame from ${d.camera.name}`} width={320} height={180} sha256={d.frame_sha256} />
              </div>
            </div>
          </div>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="Camera" span={2}>
              <Space>
                <Button type="link" style={{ padding: 0 }} onClick={() => navigate(`/cameras/${d.camera.id}`)}>{d.camera.name}</Button>
                <StatusTag status={d.camera.status} size="small" />
                <DepartmentTag code={d.camera.department_code} name={d.camera.department_name} size="small" />
                <span style={{ color: '#6B7280' }}>{d.camera.district ?? '—'} · {fmtLatLon(d.camera.lat, d.camera.lon)}</span>
              </Space>
            </Descriptions.Item>
            <Descriptions.Item label="Captured (IST)"><IstTime value={d.captured_at} mode="full" /></Descriptions.Item>
            <Descriptions.Item label="Confidence"><ConfidenceBar value={d.confidence} /></Descriptions.Item>
            <Descriptions.Item label="Raw OCR"><code>{d.plate_raw}</code></Descriptions.Item>
            <Descriptions.Item label="Valid format">{d.is_valid_format ? 'yes' : 'no (searchable, not alertable)'}</Descriptions.Item>
            <Descriptions.Item label="Source">
              <Tooltip title={`Decoder timing: stream PTS ${d.stream_pts ?? '—'} s, frame index ${d.frame_index ?? '—'} (timing is taken from PTS, not wall clock)`}>
                <span>{modeLabel(d.mode)}{d.frame_index != null ? ` · frame ${d.frame_index}` : ''}</span>
              </Tooltip>
            </Descriptions.Item>
            <Descriptions.Item label="Bounding box">{d.bbox ? d.bbox.join(', ') : '—'}</Descriptions.Item>
            <Descriptions.Item label="Sighting">{d.sighting ? <span>#{d.sighting.id} · {d.sighting.read_count} reads · <IstTime value={d.sighting.first_seen} /> → <IstTime value={d.sighting.last_seen} /></span> : '—'}</Descriptions.Item>
            <Descriptions.Item label="Alert">{d.alert_id ? <Button type="link" style={{ padding: 0 }} onClick={() => navigate(`/alerts?id=${d.alert_id}`)}>Alert #{d.alert_id}</Button> : '—'}</Descriptions.Item>
            <Descriptions.Item label="Crop SHA-256" span={2}><HashText hash={d.crop_sha256} full /></Descriptions.Item>
            <Descriptions.Item label="Frame SHA-256" span={2}><HashText hash={d.frame_sha256} full /></Descriptions.Item>
            <Descriptions.Item label="QA label" span={2}>{d.qa_label ? <span>{formatPlate(d.qa_label.true_plate) || 'unreadable'} · {d.qa_label.is_match ? <Tag color="green" style={{ margin: 0 }}>match</Tag> : <Tag color="red" style={{ margin: 0 }}>mismatch</Tag>}</span> : <span style={{ color: '#9CA3AF' }}>not labelled</span>}</Descriptions.Item>
          </Descriptions>
          <Space wrap>
            <Button icon={<SafetyCertificateOutlined />} loading={doVerify.isPending} disabled={!cropPath} onClick={() => cropPath && doVerify.mutate(cropPath)}>
              Verify hash
            </Button>
            <Button icon={<PlayCircleOutlined />} disabled={!d.sighting?.recording_available} onClick={() => setPlayOpen(true)}>
              Play recording
            </Button>
            <Button icon={<CarOutlined />} onClick={() => navigate(`/vehicles?q=${d.plate_norm}`)}>
              Search vehicle
            </Button>
            {canEvent ? (
              <Button icon={<TagOutlined />} onClick={() => setEventOpen(true)}>
                Tag event
              </Button>
            ) : null}
          </Space>
          {doVerify.isError ? <Alert type="error" showIcon message={errorMessage(doVerify.error)} /> : null}
          {verify ? <VerifyResult r={verify} /> : null}
          {canEvent ? (
            <Card size="small" title="Spot-check label (analytics quality)">
              <Space.Compact style={{ width: '100%' }}>
                <Input placeholder="Type the true plate as read by a human, or leave blank for unreadable" value={label} onChange={(e) => setLabel(e.target.value)} aria-label="True plate" />
                <Button type="primary" icon={<CheckOutlined />} loading={saveLabel.isPending} onClick={() => saveLabel.mutate()}>
                  Save label
                </Button>
              </Space.Compact>
              <div style={{ fontSize: 12, color: '#6B7280', marginTop: 6 }}>
                Preview: {label ? formatPlate(normalisePlate(label).plate_norm) : '(unreadable)'} · labels feed the accuracy section of the quality report.
              </div>
            </Card>
          ) : null}
          {d.events.length ? (
            <Card size="small" title="Linked events">
              {d.events.map((e) => (
                <div key={e.id} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, padding: '4px 0' }}>
                  <EventTypeTag type={e.type} size="small" /> <IstTime value={e.occurred_at} /> <span style={{ color: '#4B5563' }}>{e.note}</span>
                </div>
              ))}
            </Card>
          ) : null}
          {playOpen ? <PlayRecordingModal open onClose={() => setPlayOpen(false)} cameraId={d.camera.id} cameraName={d.camera.name} at={d.captured_at} sightingId={d.sighting_id} /> : null}
          <EventForm open={eventOpen} onClose={() => setEventOpen(false)} cameraId={d.camera.id} sightingId={d.sighting_id} readId={d.id} defaultOccurredAt={d.captured_at} />
        </div>
      ) : null}
    </Drawer>
  );
}

export function DetectionsPage() {
  const [sp, setSp] = useSearchParams();
  const { options: cameraOptions } = useCameraOptions();
  const { options: deptOptions } = useDepartments();
  const [plate, setPlate] = useState(sp.get('plate') ?? '');
  const [debounced, setDebounced] = useState(plate);
  const [fuzzy, setFuzzy] = useState(false);
  const [cameraId, setCameraId] = useState<number | undefined>(sp.get('camera_id') ? Number(sp.get('camera_id')) : undefined);
  const [deptId, setDeptId] = useState<number | undefined>();
  const [range, setRange] = useState<IsoRange>({});
  const [minConf, setMinConf] = useState(0);
  const [validOnly, setValidOnly] = useState(false);
  const [mode, setMode] = useState<string | undefined>();
  const [openId, setOpenId] = useState<number | null>(sp.get('id') ? Number(sp.get('id')) : null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(plate), 300);
    return () => clearTimeout(t);
  }, [plate]);
  useEffect(() => {
    const next = new URLSearchParams(sp);
    if (openId) next.set('id', String(openId));
    else next.delete('id');
    setSp(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openId]);

  const norm = useMemo(() => (debounced.trim() ? normalisePlate(debounced) : null), [debounced]);
  const filters = useMemo(
    () => ({ plate: norm?.plate_norm || undefined, fuzzy: norm && fuzzy ? 1 : undefined, camera_id: cameraId, department_id: deptId, from: range.from, to: range.to, min_conf: minConf > 0 ? minConf / 100 : undefined, valid_only: validOnly || undefined, mode }),
    [norm, fuzzy, cameraId, deptId, range, minConf, validOnly, mode],
  );
  const list = useListQuery<Detection>({ key: ['detections', 'list'], fetcher: detectionsApi.list, filters, defaultSort: 'captured_at', defaultOrder: 'desc', refetchInterval: 30_000 });

  const columns: ColumnsType<Detection> = [
    { title: 'Crop', dataIndex: 'crop_url', width: 116, render: (u: string | null, r) => <CropThumb src={u} alt={`Plate crop ${r.plate_display}`} sha256={r.crop_sha256} preview={false} /> },
    { title: 'Plate', dataIndex: 'plate_norm', key: 'plate_norm', sorter: true, width: 190, render: (p: string, r) => <Space size={6}><PlateText plate={p} raw={r.plate_raw} invalid={!r.is_valid_format} />{r.watchlist_hit ? <Tag color="red" style={{ margin: 0 }}>hit</Tag> : null}</Space> },
    { title: 'Camera', dataIndex: ['camera', 'name'], key: 'camera_name', sorter: true, ellipsis: true, render: (v: string, r) => <span><strong>{v}</strong><div style={{ fontSize: 11, color: '#6B7280' }}>{fmtPlace(r.camera.district, r.camera.police_station)}</div></span> },
    { title: 'Department', dataIndex: ['camera', 'department_code'], width: 120, render: (v: string, r) => <DepartmentTag code={v} name={r.camera.department_name} size="small" /> },
    { title: 'Captured (IST)', dataIndex: 'captured_at', key: 'captured_at', sorter: true, width: 150, render: (v: string) => <IstTime value={v} /> },
    { title: 'Confidence', dataIndex: 'confidence', key: 'confidence', sorter: true, width: 150, render: (v: number) => <ConfidenceBar value={v} /> },
    { title: 'Source', dataIndex: 'mode', width: 110, render: (v: string) => <Tag style={{ margin: 0 }}>{modeLabel(v)}</Tag> },
    { title: 'Format', dataIndex: 'is_valid_format', width: 90, render: (v: boolean) => (v ? <span style={{ color: '#16A34A' }}>Valid</span> : <Tooltip title="Not a valid Indian registration format: searchable, never alertable"><span style={{ color: '#D97706' }}>Invalid</span></Tooltip>) },
    { title: 'QA', dataIndex: 'qa_label', width: 70, render: (v: Detection['qa_label']) => (v ? (v.is_match ? <Tag color="green" style={{ margin: 0 }}>✓</Tag> : <Tag color="red" style={{ margin: 0 }}>✗</Tag>) : <span style={{ color: '#D1D5DB' }}>—</span>) },
  ];

  const hasFilters = Boolean(norm || cameraId || deptId || range.from || minConf || validOnly || mode);
  const empty = !list.query.isLoading && list.total === 0;

  return (
    <div>
      <PageHeader
        title="Detections"
        description={`Accepted (voted) plate reads with crop, confidence and IST time · ${list.total.toLocaleString('en-IN')} in the current window${hasFilters ? ' matching filters' : ''}. Click a row for the full frame, hashes and actions.`}
        extra={
          <Button icon={<ReloadOutlined />} loading={list.query.isFetching} onClick={() => void list.query.refetch()}>
            Refresh
          </Button>
        }
      />
      <Card styles={{ body: { padding: 0 } }}>
        <div className="sg-toolbar" style={{ padding: '12px 16px 0' }}>
          <div className="sg-toolbar-left">
            <Input allowClear prefix={<SearchOutlined style={{ color: '#9CA3AF' }} />} placeholder="Plate (GJ 01 AB 1234)" value={plate} onChange={(e) => setPlate(e.target.value)} style={{ width: 200 }} aria-label="Plate filter" suffix={norm ? <span style={{ fontSize: 11, color: norm.is_valid_format ? '#16A34A' : '#D97706' }}>{formatPlate(norm.plate_norm)}</span> : null} />
            <Checkbox checked={fuzzy} onChange={(e) => setFuzzy(e.target.checked)} disabled={!norm}>
              Fuzzy
            </Checkbox>
            <Select allowClear showSearch optionFilterProp="label" placeholder="Camera" style={{ width: 230 }} options={cameraOptions} value={cameraId} onChange={setCameraId} aria-label="Camera" />
            <Select allowClear showSearch optionFilterProp="label" placeholder="Department" style={{ width: 190 }} options={deptOptions} value={deptId} onChange={setDeptId} aria-label="Department" />
            <RangeIst value={range} onChange={setRange} />
            <Select allowClear placeholder="Source" style={{ width: 130 }} options={[{ value: 'live', label: 'Live' }, { value: 'preindex', label: 'Pre-indexed' }]} value={mode} onChange={setMode} aria-label="Source" />
          </div>
          <div className="sg-toolbar-right">
            <span style={{ fontSize: 12, color: '#6B7280' }}>Min conf.</span>
            <Slider min={0} max={100} step={5} value={minConf} onChange={setMinConf} style={{ width: 120, margin: '0 8px' }} tooltip={{ formatter: (v) => `${v} %` }} />
            <Checkbox checked={validOnly} onChange={(e) => setValidOnly(e.target.checked)}>
              Valid only
            </Checkbox>
          </div>
        </div>
        {list.query.isError ? (
          <div style={{ padding: 16 }}>
            <ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />
          </div>
        ) : empty ? (
          <EmptyState
            title={hasFilters ? 'No plate reads match these filters' : 'No plate reads in this window'}
            description={hasFilters ? 'Widen the time window, lower the confidence threshold or enable fuzzy matching.' : 'ANPR workers post reads within seconds of a plate being visible on a live camera.'}
            actions={
              hasFilters ? (
                <Button
                  onClick={() => {
                    setPlate('');
                    setCameraId(undefined);
                    setDeptId(undefined);
                    setRange({});
                    setMinConf(0);
                    setValidOnly(false);
                    setMode(undefined);
                  }}
                >
                  Clear filters
                </Button>
              ) : undefined
            }
          />
        ) : (
          <Table<Detection>
            className="sg-table"
            size="middle"
            rowKey="id"
            columns={columns}
            dataSource={list.items}
            loading={list.query.isLoading}
            pagination={list.pagination}
            onChange={list.onTableChange}
            sticky
            scroll={{ x: 1200 }}
            rowClassName={(r) => (r.watchlist_hit ? 'sg-row-new' : '')}
            onRow={(r) => ({ onClick: () => setOpenId(r.id) })}
          />
        )}
      </Card>
      <DetectionDrawer id={openId} onClose={() => setOpenId(null)} />
    </div>
  );
}
