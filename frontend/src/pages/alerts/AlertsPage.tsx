/**
 * Alerts (/alerts): live panel sorted by priority, filters, new-alert highlight,
 * sound / desktop-notification toggles, detail drawer with snapshot, camera,
 * mini-map, watchlist reason, reads, ack / close with note, play recording.
 */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert as AntAlert, Button, Card, Descriptions, Drawer, Form, Input, Modal, Select, Space, Switch, Table, Tag, Tooltip, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { BellOutlined, CheckOutlined, CloseCircleOutlined, PlayCircleOutlined, ReloadOutlined, SoundOutlined, VideoCameraOutlined, NodeIndexOutlined } from '@ant-design/icons';
import { Marker } from '@/components/leaflet';
import { PageHeader } from '@/components/PageHeader';
import { rowProps, useListQuery } from '@/hooks/useListQuery';
import { alertsApi } from '@/api';
import type { Alert, AlertDetail, AlertOutcome } from '@/api/types';
import { AlertStatusTag, ConfidenceLevelTag, DepartmentTag, EventTypeTag, PriorityTag, ReasonTag, StatusTag } from '@/components/Tags';
import { PlateText } from '@/components/PlateText';
import { IstTime } from '@/components/IstTime';
import { CropThumb, ConfidenceBar, HashText } from '@/components/Misc';
import { EmptyState, ErrorState } from '@/components/States';
import { BaseMap, cameraIcon } from '@/components/MapView';
import { PlayRecordingModal } from '@/components/Recordings';
import { useCameraOptions } from '@/hooks/useCamerasOptions';
import { usePermission } from '@/store/auth';
import { useUiStore } from '@/store/ui';
import { fmtLatency, fmtPlace, humanise } from '@/utils/format';
import { fmtIst } from '@/utils/time';
import { ALERT_PRIORITY } from '@/theme/colours';

const OUTCOMES: { value: AlertOutcome; label: string }[] = [
  { value: 'resolved', label: 'Resolved' },
  { value: 'false_positive', label: 'False positive' },
  { value: 'duplicate', label: 'Duplicate' },
  { value: 'other', label: 'Other' },
];

function AlertTitle({ a }: { a: Alert }) {
  if (a.type === 'camera_offline') return <span style={{ fontWeight: 600 }}>Camera offline · {a.camera.name}</span>;
  if (a.type === 'intrusion') return <span style={{ fontWeight: 600 }}>Intrusion · {a.camera.name}</span>;
  return (
    <Space size={6} wrap>
      <PlateText plate={a.plate_norm} />
      {a.watchlist ? <ReasonTag reason={a.watchlist.reason} size="small" /> : null}
      <ConfidenceLevelTag level={a.confidence_level} size="small" />
    </Space>
  );
}

function ActionModal({ alert, action, onClose }: { alert: Alert | null; action: 'ack' | 'close' | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [form] = Form.useForm<{ note?: string; outcome: AlertOutcome }>();
  const m = useMutation({
    mutationFn: (v: { note?: string; outcome: AlertOutcome }) => (action === 'ack' ? alertsApi.ack(alert?.id as number, v.note) : alertsApi.close(alert?.id as number, v.note, v.outcome)),
    onSuccess: () => {
      message.success(action === 'ack' ? 'Alert acknowledged' : 'Alert closed');
      qc.invalidateQueries({ queryKey: ['alerts'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
      form.resetFields();
      onClose();
    },
  });
  return (
    <Modal open={Boolean(alert && action)} onCancel={onClose} title={action === 'ack' ? `Acknowledge alert #${alert?.id}` : `Close alert #${alert?.id}`} okText={action === 'ack' ? 'Acknowledge' : 'Close alert'} okButtonProps={{ loading: m.isPending }} onOk={async () => m.mutate(await form.validateFields())} destroyOnClose>
      <Form form={form} layout="vertical" initialValues={{ outcome: 'resolved' }}>
        {action === 'close' ? (
          <Form.Item name="outcome" label="Outcome" rules={[{ required: true }]}>
            <Select options={OUTCOMES} />
          </Form.Item>
        ) : null}
        <Form.Item name="note" label="Note" extra="Recorded in the audit trail with your username and IP.">
          <Input.TextArea rows={3} placeholder={action === 'ack' ? 'Unit dispatched from Sector 7 PS' : 'Vehicle intercepted at Koba checkpost'} autoFocus />
        </Form.Item>
      </Form>
    </Modal>
  );
}

function AlertDrawer({ id, onClose, onAction }: { id: number | null; onClose: () => void; onAction: (a: Alert, action: 'ack' | 'close') => void }) {
  const navigate = useNavigate();
  const canAck = usePermission('alerts.ack');
  const q = useQuery({ queryKey: ['alerts', 'detail', id], queryFn: () => alertsApi.get(id as number), enabled: id !== null, refetchInterval: 15_000 });
  const [playOpen, setPlayOpen] = useState(false);
  const a: AlertDetail | undefined = q.data;
  const colour = a ? ALERT_PRIORITY[a.priority].colour : '#9CA3AF';
  return (
    <Drawer open={id !== null} onClose={onClose} width={760} loading={q.isLoading} title={a ? <Space wrap><span>Alert #{a.id}</span><PriorityTag priority={a.priority} /><AlertStatusTag status={a.status} />{a.escalated ? <Tooltip title="Unacknowledged for more than 5 minutes"><Tag color="red" style={{ margin: 0 }}>Escalated</Tag></Tooltip> : null}</Space> : 'Alert'} styles={{ header: { borderTop: `4px solid ${colour}` } }} extra={a && canAck && a.status !== 'closed' ? <Space>{a.status === 'new' ? <Button type="primary" icon={<CheckOutlined />} onClick={() => onAction(a, 'ack')}>Acknowledge</Button> : null}<Button type={a.status === 'new' ? 'default' : 'primary'} icon={<CloseCircleOutlined />} onClick={() => onAction(a, 'close')}>Close alert</Button></Space> : null}>
      {q.isError ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : null}
      {a ? (
        <div style={{ display: 'grid', gap: 14 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 260px', gap: 12 }}>
            <div>
              <div style={{ fontSize: 16, marginBottom: 6 }}><AlertTitle a={a} /></div>
              {a.watchlist ? <div style={{ color: '#4B5563' }}>{a.watchlist.name} · source {a.watchlist.source === 'egujcop' ? 'eGujCop' : humanise(a.watchlist.source)} · list priority {ALERT_PRIORITY[a.watchlist.priority]?.label ?? humanise(a.watchlist.priority)}</div> : null}
              {a.type === 'camera_offline' ? <div style={{ color: '#4B5563' }}>The camera did not answer three health checks in a row (one per minute). Closes by itself when the camera comes back.</div> : null}
              <div style={{ marginTop: 10 }}>
                <CropThumb src={a.snapshot_url} alt={a.plate_norm ? `Plate crop ${a.plate_norm}` : `Snapshot from ${a.camera.name}`} width={360} height={a.type === 'watchlist_hit' ? 100 : 200} sha256={a.snapshot_sha256} />
              </div>
              <div style={{ fontSize: 12, color: '#6B7280', marginTop: 4 }}>SHA-256 <HashText hash={a.snapshot_sha256} /></div>
            </div>
            <div>
              <BaseMap height={170} center={a.camera.lat !== null && a.camera.lon !== null ? [a.camera.lat, a.camera.lon] : undefined} zoom={13} scrollWheelZoom={false}>
                {a.camera.lat !== null && a.camera.lon !== null ? <Marker position={[a.camera.lat, a.camera.lon]} icon={cameraIcon({ id: a.camera.id, name: a.camera.name, lat: a.camera.lat, lon: a.camera.lon, status: a.camera.status, department_code: a.camera.department_code }, { selectedId: a.camera.id })} /> : null}
              </BaseMap>
              <div style={{ marginTop: 6, fontSize: 12 }}>
                <Button type="link" style={{ padding: 0, fontWeight: 600 }} onClick={() => navigate(`/cameras/${a.camera.id}`)}>{a.camera.name}</Button>
                <div style={{ color: '#6B7280' }}>{fmtPlace(a.camera.district, a.camera.police_station)}</div>
                <Space size={4} style={{ marginTop: 4 }}><StatusTag status={a.camera.status} size="small" /><DepartmentTag code={a.camera.department_code} name={a.camera.department_name} size="small" /></Space>
              </div>
            </div>
          </div>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="Raised (IST)"><IstTime value={a.created_at} mode="full" /></Descriptions.Item>
            <Descriptions.Item label="Read → alert latency">{fmtLatency(a.latency_ms)}</Descriptions.Item>
            <Descriptions.Item label="Reads attached">{a.read_count} · last <IstTime value={a.last_read_at} /></Descriptions.Item>
            <Descriptions.Item label="Read confidence">{a.read ? <ConfidenceBar value={a.read.confidence} /> : '—'}</Descriptions.Item>
            <Descriptions.Item label="Acknowledged">{a.acknowledged_at ? <span><IstTime value={a.acknowledged_at} /> by {a.acknowledged_by_username}</span> : <span style={{ color: '#9CA3AF' }}>not yet</span>}</Descriptions.Item>
            <Descriptions.Item label="Closed">{a.closed_at ? <span><IstTime value={a.closed_at} /> by {a.closed_by_username ?? 'system'} · {humanise(a.outcome)}</span> : <span style={{ color: '#9CA3AF' }}>open</span>}</Descriptions.Item>
            <Descriptions.Item label="Note" span={2}>{a.note ?? <span style={{ color: '#9CA3AF' }}>—</span>}</Descriptions.Item>
          </Descriptions>
          <Space wrap>
            <Button icon={<PlayCircleOutlined />} disabled={!a.recording_available || !a.read} onClick={() => setPlayOpen(true)}>
              Play recording
            </Button>
            {a.plate_norm ? (
              <Button icon={<NodeIndexOutlined />} onClick={() => navigate(`/vehicles/${a.plate_norm}/route`)}>
                Build route
              </Button>
            ) : null}
            <Button icon={<VideoCameraOutlined />} onClick={() => navigate(`/cameras/${a.camera.id}`)}>
              Open camera
            </Button>
          </Space>
          {a.reads.length ? (
            <Card size="small" title={`Attached reads (${a.reads.length})`} styles={{ body: { padding: 0 } }}>
              <Table size="small" rowKey="id" pagination={false} dataSource={a.reads} columns={[
                { title: 'Crop', dataIndex: 'crop_url', width: 110, render: (u: string | null, r) => <CropThumb src={u} alt={`Read ${r.plate_raw}`} width={90} height={34} sha256={r.crop_sha256} /> },
                { title: 'Raw', dataIndex: 'plate_raw', render: (v: string) => <code>{v}</code> },
                { title: 'Confidence', dataIndex: 'confidence', width: 140, render: (v: number) => <ConfidenceBar value={v} width={60} /> },
                { title: 'Captured (IST)', dataIndex: 'captured_at', width: 150, render: (v: string) => <IstTime value={v} /> },
              ]} />
            </Card>
          ) : null}
          {a.events.length ? (
            <Card size="small" title="Events">
              {a.events.map((e) => (
                <div key={e.id} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, padding: '3px 0' }}>
                  <EventTypeTag type={e.type} size="small" /> <IstTime value={e.occurred_at} /> <span style={{ color: '#4B5563' }}>{e.note}</span>
                </div>
              ))}
            </Card>
          ) : null}
          {playOpen && a.read ? <PlayRecordingModal open onClose={() => setPlayOpen(false)} cameraId={a.camera.id} cameraName={a.camera.name} at={a.read.captured_at} alertId={a.id} sightingId={a.sighting_id} /> : null}
        </div>
      ) : null}
    </Drawer>
  );
}

export function AlertsPage() {
  const [sp, setSp] = useSearchParams();
  const canAck = usePermission('alerts.ack');
  const { options: cameraOptions } = useCameraOptions();
  const soundOn = useUiStore((s) => s.soundOn);
  const setSoundOn = useUiStore((s) => s.setSoundOn);
  const desktopNotify = useUiStore((s) => s.desktopNotify);
  const setDesktopNotify = useUiStore((s) => s.setDesktopNotify);
  const unseen = useUiStore((s) => s.unseenAlertIds);
  const lastAlertAt = useUiStore((s) => s.lastAlertAt);
  const [status, setStatus] = useState<string[]>(['new', 'acknowledged']);
  const [type, setType] = useState<string | undefined>();
  const [priority, setPriority] = useState<string | undefined>();
  const [cameraId, setCameraId] = useState<number | undefined>();
  const [escalatedOnly, setEscalatedOnly] = useState(false);
  const [openId, setOpenId] = useState<number | null>(sp.get('id') ? Number(sp.get('id')) : null);
  const [action, setAction] = useState<{ alert: Alert | null; action: 'ack' | 'close' | null }>({ alert: null, action: null });
  const [flashIds, setFlashIds] = useState<number[]>([]);

  useEffect(() => {
    const idParam = sp.get('id');
    if (idParam && Number(idParam) !== openId) setOpenId(Number(idParam));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sp]);
  useEffect(() => {
    const next = new URLSearchParams(sp);
    if (openId) next.set('id', String(openId));
    else next.delete('id');
    setSp(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openId]);
  useEffect(() => {
    if (!lastAlertAt) return;
    const latest = useUiStore.getState().liveAlerts[0];
    if (!latest) return;
    setFlashIds((ids) => [latest.id, ...ids]);
    const t = setTimeout(() => setFlashIds((ids) => ids.filter((x) => x !== latest.id)), 2500);
    return () => clearTimeout(t);
  }, [lastAlertAt]);

  const filters = useMemo(() => ({ status: status.length ? status.join(',') : 'new,acknowledged,closed', type, priority, camera_id: cameraId, escalated: escalatedOnly || undefined }), [status, type, priority, cameraId, escalatedOnly]);
  const list = useListQuery<Alert>({ key: ['alerts', 'list'], fetcher: alertsApi.list, filters, defaultSort: 'priority', defaultOrder: 'desc', refetchInterval: 30_000 });

  const enableDesktop = async () => {
    if (!('Notification' in window)) {
      message.warning('This browser does not support desktop notifications');
      return;
    }
    const r = await Notification.requestPermission();
    if (r === 'granted') setDesktopNotify(true);
    else message.info('Permission was not granted');
  };

  const columns: ColumnsType<Alert> = [
    { title: 'Priority', dataIndex: 'priority', width: 110, render: (v: string, a) => <Space size={4}><PriorityTag priority={v} size="small" />{a.escalated ? <Tooltip title="Unacknowledged for more than 5 minutes"><Tag color="red" style={{ margin: 0 }}>!</Tag></Tooltip> : null}</Space> },
    { title: 'Snapshot', dataIndex: 'snapshot_url', width: 96, render: (u: string | null, a) => <CropThumb src={u} alt={a.plate_norm ? `Plate ${a.plate_norm}` : 'Snapshot'} width={80} height={32} preview={false} /> },
    { title: 'Alert', key: 'title', width: 300, render: (_v, a) => <div><AlertTitle a={a} /><div style={{ fontSize: 12, color: '#6B7280', marginTop: 2 }}>{a.watchlist?.name ?? (a.type === 'camera_offline' ? 'Camera health check' : humanise(a.type))}</div></div> },
    { title: 'Camera', dataIndex: ['camera', 'name'], width: 220, ellipsis: true, render: (v: string, a) => <span><strong>{v}</strong><div style={{ fontSize: 11, color: '#6B7280' }}>{a.camera.district ?? '—'} · {a.camera.department_name || a.camera.department_code}</div></span> },
    { title: 'Status', dataIndex: 'status', width: 130, render: (v: string) => <AlertStatusTag status={v} size="small" /> },
    { title: 'Raised', dataIndex: 'created_at', key: 'created_at', sorter: true, width: 150, render: (v: string) => <IstTime value={v} mode="relative" /> },
    { title: 'Latency', dataIndex: 'latency_ms', width: 90, align: 'right', render: (v: number | null) => fmtLatency(v) },
    { title: 'Reads', dataIndex: 'read_count', width: 70, align: 'right' },
    {
      title: '',
      width: canAck ? 190 : 0,
      className: 'sg-no-print',
      render: (_v, a) =>
        canAck && a.status !== 'closed' ? (
          <Space size={4} onClick={(e) => e.stopPropagation()}>
            {a.status === 'new' ? <Button size="small" icon={<CheckOutlined />} onClick={() => setAction({ alert: a, action: 'ack' })}>Ack</Button> : null}
            <Button size="small" icon={<CloseCircleOutlined />} onClick={() => setAction({ alert: a, action: 'close' })}>Close</Button>
          </Space>
        ) : null,
    },
  ];

  const empty = !list.query.isLoading && list.total === 0;
  const hasFilters = Boolean(type || priority || cameraId || escalatedOnly || status.join(',') !== 'new,acknowledged');

  return (
    <div>
      <PageHeader
        title="Alerts"
        description={`Watchlist hits, camera-offline and intrusion alerts sorted by priority then time · ${list.total} shown. New alerts arrive over the live channel with a toast, a sound and a desktop notification.`}
        extra={
          <Space wrap>
            <Tooltip title="Play a sound on every new alert">
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                <SoundOutlined style={{ color: soundOn ? '#1E4DB7' : '#9CA3AF' }} aria-hidden />
                <Switch size="small" checked={soundOn} onChange={setSoundOn} aria-label="Alert sound" />
                <span style={{ fontSize: 12, color: '#4B5563' }}>Sound</span>
              </label>
            </Tooltip>
            <Tooltip title="Browser notification when this tab is in the background (asks for permission the first time)">
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                <BellOutlined style={{ color: desktopNotify ? '#1E4DB7' : '#9CA3AF' }} aria-hidden />
                <Switch size="small" checked={desktopNotify} onChange={(v) => (v ? void enableDesktop() : setDesktopNotify(false))} aria-label="Desktop alerts" />
                <span style={{ fontSize: 12, color: '#4B5563' }}>Desktop alerts</span>
              </label>
            </Tooltip>
            <Button icon={<ReloadOutlined />} loading={list.query.isFetching} onClick={() => void list.query.refetch()}>
              Refresh
            </Button>
          </Space>
        }
      />
      {desktopNotify && typeof Notification !== 'undefined' && Notification.permission !== 'granted' ? <AntAlert type="warning" showIcon closable message="Desktop notifications are switched on but the browser permission is not granted." style={{ marginBottom: 12 }} /> : null}
      <Card styles={{ body: { padding: 0 } }}>
        <div className="sg-toolbar" style={{ padding: '12px 16px 0' }}>
          <div className="sg-toolbar-left">
            <Select mode="multiple" placeholder="Status" style={{ minWidth: 220 }} options={[{ value: 'new', label: 'New' }, { value: 'acknowledged', label: 'Acknowledged' }, { value: 'closed', label: 'Closed' }]} value={status} onChange={setStatus} maxTagCount="responsive" aria-label="Status" />
            <Select allowClear placeholder="Type" style={{ width: 150 }} options={[{ value: 'watchlist_hit', label: 'Watchlist hit' }, { value: 'camera_offline', label: 'Camera offline' }, { value: 'intrusion', label: 'Intrusion' }]} value={type} onChange={setType} aria-label="Type" />
            <Select allowClear placeholder="Priority" style={{ width: 120 }} options={(['critical', 'high', 'medium', 'low'] as const).map((p) => ({ value: p, label: ALERT_PRIORITY[p].label }))} value={priority} onChange={setPriority} aria-label="Priority" />
            <Select allowClear showSearch optionFilterProp="label" placeholder="Camera" style={{ width: 230 }} options={cameraOptions} value={cameraId} onChange={setCameraId} aria-label="Camera" />
            <Space size={6}><Switch size="small" checked={escalatedOnly} onChange={setEscalatedOnly} aria-label="Escalated only" /><span style={{ fontSize: 12, color: '#6B7280' }}>Escalated only</span></Space>
          </div>
          <div className="sg-toolbar-right">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>{unseen.length ? `${unseen.length} arrived while you were away` : 'Live channel connected'}</Typography.Text>
          </div>
        </div>
        {list.query.isError ? (
          <div style={{ padding: 16 }}><ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} /></div>
        ) : empty ? (
          <EmptyState title={hasFilters ? 'No alerts match these filters' : 'No open alerts'} description={hasFilters ? 'Include closed alerts or clear a filter.' : 'New watchlist hits appear here in real time - within seconds of the plate being read.'} actions={hasFilters ? <Button onClick={() => { setStatus(['new', 'acknowledged']); setType(undefined); setPriority(undefined); setCameraId(undefined); setEscalatedOnly(false); }}>Clear filters</Button> : undefined} />
        ) : (
          <Table<Alert>
            className="sg-table"
            size="middle"
            rowKey="id"
            columns={columns}
            dataSource={list.items}
            loading={list.query.isLoading}
            pagination={list.pagination}
            onChange={list.onTableChange}
            sticky
            scroll={{ x: 1150 }}
            rowClassName={(a) => [a.status === 'new' ? 'sg-row-new' : '', flashIds.includes(a.id) ? 'sg-row-flash' : ''].join(' ')}
            onRow={(a) => rowProps(() => setOpenId(a.id))}
          />
        )}
      </Card>
      <AlertDrawer id={openId} onClose={() => setOpenId(null)} onAction={(a, act) => setAction({ alert: a, action: act })} />
      <ActionModal alert={action.alert} action={action.action} onClose={() => setAction({ alert: null, action: null })} />
      <Typography.Text type="secondary" style={{ display: 'block', marginTop: 10, fontSize: 12 }}>
        Lifecycle: new → acknowledged → closed, each step audited with note, user and IP. Same plate on the same camera within 60 s attaches to the open alert instead of raising a new one. Times shown as {fmtIst(new Date().toISOString()).slice(-3)} in tooltips.
      </Typography.Text>
    </div>
  );
}
