/** Camera detail drawer: metadata tabs (Overview, Health history, Live reads, Recordings, Events) with Edit / Maintenance / Retire. */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, DatePicker, Descriptions, Drawer, Form, Input, Modal, Select, Space, Table, Tabs, Tag, Typography, message } from 'antd';
import { DeleteOutlined, EditOutlined, PlayCircleOutlined, ToolOutlined } from '@ant-design/icons';
import { camerasApi, detectionsApi, eventsApi } from '@/api';
import type { Camera, CameraDetail, MaintenanceUpdate } from '@/api/types';
import { AmcTag, BoolTag, CodecTag, DepartmentTag, LocationConfidenceTag, MaintenanceTag, StatusTag, EventTypeTag, codecLabel } from '@/components/Tags';
import { rowProps } from '@/hooks/useListQuery';
import { IstTime } from '@/components/IstTime';
import { maskUrlCredentials } from '@/utils/url';
import { PlateText } from '@/components/PlateText';
import { ConfidenceBar, CropThumb } from '@/components/Misc';
import { ConfirmDialog } from '@/components/Dialogs';
import { cameraTypeLabel, fmtHeadingFov, fmtLatLon, fmtPct, humanise, withCacheBuster } from '@/utils/format';
import { fmtIstDate, dayjs, istToUtcIso, toIst } from '@/utils/time';
import { usePermission } from '@/store/auth';
import { RecordingsTimeline } from '@/components/Recordings';
import { EmptyState } from '@/components/States';
import { HealthLogTable } from './HealthLogTable';

interface CameraDrawerProps {
  cameraId: number | null;
  onClose: () => void;
  onEdit: (c: Camera) => void;
}

function MaintenanceModal({ camera, open, onClose }: { camera: CameraDetail; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [form] = Form.useForm<{ maintenance_status: MaintenanceUpdate['maintenance_status']; last_maintenance_at?: dayjs.Dayjs | null; maintenance_note?: string; amc_vendor?: string; amc_expiry?: dayjs.Dayjs | null }>();
  const m = useMutation({
    mutationFn: (body: MaintenanceUpdate) => camerasApi.maintenance(camera.id, body),
    onSuccess: () => {
      message.success('Maintenance status updated');
      qc.invalidateQueries({ queryKey: ['cameras'] });
      qc.invalidateQueries({ queryKey: ['health'] });
      onClose();
    },
  });
  return (
    <Modal
      open={open}
      onCancel={onClose}
      title="Maintenance status"
      okText="Save"
      okButtonProps={{ loading: m.isPending }}
      onOk={async () => {
        const v = await form.validateFields();
        m.mutate({ maintenance_status: v.maintenance_status, last_maintenance_at: istToUtcIso(v.last_maintenance_at) ?? null, maintenance_note: v.maintenance_note ?? null, amc_vendor: v.amc_vendor ?? null, amc_expiry: v.amc_expiry ? v.amc_expiry.format('YYYY-MM-DD') : null });
      }}
      destroyOnClose
    >
      <Form form={form} layout="vertical" initialValues={{ maintenance_status: camera.maintenance_status, last_maintenance_at: toIst(camera.last_maintenance_at), maintenance_note: camera.maintenance_note ?? undefined, amc_vendor: camera.amc_vendor ?? undefined, amc_expiry: camera.amc_expiry ? dayjs(camera.amc_expiry) : null }}>
        <Form.Item name="maintenance_status" label="Status" rules={[{ required: true }]}>
          <Select options={[{ value: 'ok', label: 'OK' }, { value: 'under_maintenance', label: 'Under maintenance' }, { value: 'faulty', label: 'Faulty' }, { value: 'decommissioned', label: 'Decommissioned' }]} />
        </Form.Item>
        <Form.Item name="last_maintenance_at" label="Last maintenance (IST)">
          <DatePicker showTime style={{ width: '100%' }} format="DD MMM YYYY, HH:mm" />
        </Form.Item>
        <Form.Item name="maintenance_note" label="Note">
          <Input.TextArea rows={2} />
        </Form.Item>
        <Form.Item name="amc_vendor" label="AMC vendor">
          <Input />
        </Form.Item>
        <Form.Item name="amc_expiry" label="AMC expiry">
          <DatePicker style={{ width: '100%' }} format="DD MMM YYYY" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export function CameraDrawer({ cameraId, onClose, onEdit }: CameraDrawerProps) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const canWrite = usePermission('cameras.write');
  const [retireOpen, setRetireOpen] = useState(false);
  const [maintOpen, setMaintOpen] = useState(false);
  const cam = useQuery({ queryKey: ['cameras', 'detail', cameraId], queryFn: () => camerasApi.get(cameraId as number), enabled: cameraId !== null });
  const reads = useQuery({ queryKey: ['detections', 'camera', cameraId], queryFn: () => detectionsApi.list({ camera_id: cameraId as number, page_size: 10 }), enabled: cameraId !== null });
  const events = useQuery({ queryKey: ['events', 'camera', cameraId], queryFn: () => eventsApi.list({ camera_id: cameraId as number, page_size: 10 }), enabled: cameraId !== null });
  const retire = useMutation({
    mutationFn: () => camerasApi.retire(cameraId as number),
    onSuccess: () => {
      message.success('Camera retired - relay path removed, ANPR stops on the next config reload');
      qc.invalidateQueries({ queryKey: ['cameras'] });
      qc.invalidateQueries({ queryKey: ['geo'] });
      setRetireOpen(false);
      onClose();
    },
  });

  const c = cam.data;
  return (
    <Drawer
      open={cameraId !== null}
      onClose={onClose}
      width={720}
      title={
        c ? (
          <Space wrap>
            <span>{c.name}</span>
            <StatusTag status={c.status} live={c.live} />
            <MaintenanceTag status={c.maintenance_status} size="small" />
            <CodecTag codec={c.codec} />
            {c.anpr_enabled ? <Tag color="blue" style={{ margin: 0 }}>ANPR</Tag> : null}
            {c.record_enabled ? <Tag style={{ margin: 0 }}>REC</Tag> : null}
          </Space>
        ) : (
          'Camera'
        )
      }
      extra={
        c ? (
          <Space>
            <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => navigate(`/cameras/${c.id}`)}>
              View live
            </Button>
            {canWrite ? (
              <>
                <Button icon={<ToolOutlined />} onClick={() => setMaintOpen(true)}>
                  Maintenance
                </Button>
                <Button icon={<EditOutlined />} onClick={() => onEdit(c)}>
                  Edit
                </Button>
                <Button danger icon={<DeleteOutlined />} onClick={() => setRetireOpen(true)} disabled={c.status === 'retired'}>
                  Retire
                </Button>
              </>
            ) : null}
          </Space>
        ) : null
      }
      loading={cam.isLoading}
    >
      {c ? (
        <Tabs
          items={[
            {
              key: 'overview',
              label: 'Overview',
              children: (
                <div>
                  <div style={{ display: 'grid', gap: 16, marginBottom: 16 }}>
                    <div style={{ display: 'flex', gap: 16, alignItems: 'stretch' }}>
                    <div style={{ width: 260, flexShrink: 0 }}>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        Latest snapshot
                      </Typography.Text>
                      {c.snapshot_url ? (
                        <img src={withCacheBuster(c.snapshot_url) ?? undefined} alt={`Snapshot of ${c.name}`} style={{ width: '100%', borderRadius: 8, border: '1px solid #E5E7EB', background: '#0b0f19', marginTop: 4 }} />
                      ) : (
                        <div style={{ height: 124, background: '#F3F4F6', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#9CA3AF', fontSize: 12, marginTop: 4 }}>No snapshot yet</div>
                      )}
                    </div>
                    <div style={{ flex: 1, display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, alignContent: 'start' }}>
                      {[['Reads 24 h', c.reads_24h], ['Sightings 24 h', c.sightings_24h], ['Open alerts', c.recent_alerts], ['Uptime 24 h', fmtPct(c.uptime_24h_pct, 0)], ['Last seen', <IstTime key="ls" value={c.last_seen_at} />], ['Status since', <IstTime key="ss" value={c.last_status_change_at} />]].map(([label, value]) => (
                        <div key={String(label)} style={{ border: '1px solid #E5E7EB', borderRadius: 8, padding: '8px 10px' }}>
                          <div style={{ fontSize: 11, color: '#6B7280' }}>{label}</div>
                          <div style={{ fontSize: 16, fontWeight: 600, marginTop: 2 }}>{value}</div>
                        </div>
                      ))}
                    </div>
                    </div>
                    <Descriptions size="small" column={2} bordered labelStyle={{ width: 130 }}>
                      <Descriptions.Item label="External id">{c.external_id}</Descriptions.Item>
                      <Descriptions.Item label="Source">{humanise(c.source)}</Descriptions.Item>
                      <Descriptions.Item label="Department">
                        <DepartmentTag code={c.department_code} name={c.department_name} /> <span style={{ marginLeft: 6 }}>{c.department_name}</span>
                      </Descriptions.Item>
                      <Descriptions.Item label="Type / ownership">
                        {cameraTypeLabel(c.type)} · {humanise(c.ownership)}
                      </Descriptions.Item>
                      <Descriptions.Item label="District">{c.district ?? '—'}</Descriptions.Item>
                      <Descriptions.Item label="Police station">{c.police_station ?? '—'}</Descriptions.Item>
                      <Descriptions.Item label="Address" span={2}>
                        {c.address ?? '—'}
                      </Descriptions.Item>
                      <Descriptions.Item label="Coordinates">{fmtLatLon(c.lat, c.lon)}</Descriptions.Item>
                      <Descriptions.Item label="Heading / FoV">
                        {fmtHeadingFov(c.heading_deg, c.fov_deg)}
                      </Descriptions.Item>
                      {c.location_confidence || c.metadata?.enrichment ? (
                        <Descriptions.Item label="Location confidence" span={2}>
                          <LocationConfidenceTag confidence={c.location_confidence} />
                          {c.metadata?.enrichment?.source ? <span style={{ marginLeft: 8, fontSize: 12, color: '#6B7280' }}>source: {c.metadata.enrichment.source}</span> : null}
                          {c.metadata?.enrichment?.notes ? (
                            <div style={{ marginTop: 4, fontSize: 12, color: '#4B5563' }} title="Team note explaining how the camera name was interpreted (media/cameras_enrichment.csv)">
                              {c.metadata.enrichment.notes}
                            </div>
                          ) : null}
                        </Descriptions.Item>
                      ) : null}
                      <Descriptions.Item label="Relay path">
                        <code>{c.relay_path ?? '—'}</code>
                      </Descriptions.Item>
                      <Descriptions.Item label="Stream">
                        {codecLabel(c.codec)} · {c.resolution ?? '—'} · {c.fps ?? '—'} fps
                      </Descriptions.Item>
                      <Descriptions.Item label="RTSP URL" span={2}>
                        <code style={{ fontSize: 12, wordBreak: 'break-all' }} title="Credentials are always masked (user:***@host)">{c.rtsp_url ? maskUrlCredentials(c.rtsp_url) : '— (no stream URL: never checked, surfaces in gap report)'}</code>
                      </Descriptions.Item>
                      <Descriptions.Item label="Connectivity">
                        {c.connectivity_type ? c.connectivity_type.toUpperCase() : '—'} {c.bandwidth_kbps ? `· ${c.bandwidth_kbps} kbps` : ''}
                      </Descriptions.Item>
                      <Descriptions.Item label="VMS / NVR">
                        {c.vms_platform ?? '—'} {c.nvr_id ? `· ${c.nvr_id}` : ''}
                      </Descriptions.Item>
                      <Descriptions.Item label="Storage">
                        {c.storage_location ?? '—'} {c.retention_days != null ? `· ${c.retention_days} d retention` : ''}
                      </Descriptions.Item>
                      <Descriptions.Item label="Vendor / model">
                        {c.vendor ?? '—'} {c.model ? `· ${c.model}` : ''}
                      </Descriptions.Item>
                      <Descriptions.Item label="Installed">
                        {fmtIstDate(c.install_date)} {c.age_years != null ? `(${c.age_years.toFixed(1)} y)` : ''}
                      </Descriptions.Item>
                      <Descriptions.Item label="AMC">
                        <AmcTag status={c.amc_status} size="small" /> {c.amc_vendor ? ` ${c.amc_vendor}` : ''} {c.amc_expiry ? ` · ${fmtIstDate(c.amc_expiry)}` : ''}
                      </Descriptions.Item>
                      <Descriptions.Item label="Maintenance">
                        <MaintenanceTag status={c.maintenance_status} size="small" /> {c.maintenance_note ? ` ${c.maintenance_note}` : ''}
                      </Descriptions.Item>
                      <Descriptions.Item label="Last maintenance">
                        <IstTime value={c.last_maintenance_at} />
                      </Descriptions.Item>
                      <Descriptions.Item label="Catalogue live">
                        <BoolTag value={c.live} yes="Yes" no="No (not streaming)" />
                      </Descriptions.Item>
                      <Descriptions.Item label="Created">
                        <IstTime value={c.created_at} /> {c.created_by_username ? `by ${c.created_by_username}` : `via ${c.created_via}`}
                      </Descriptions.Item>
                    </Descriptions>
                  </div>
                </div>
              ),
            },
            { key: 'health', label: 'Health history', children: <HealthLogTable cameraId={c.id} /> },
            {
              key: 'reads',
              label: 'Live reads',
              children: reads.data?.items.length ? (
                <Table
                  size="small"
                  rowKey="id"
                  pagination={false}
                  dataSource={reads.data.items}
                  onRow={(r) => rowProps(() => navigate(`/detections?id=${r.id}`))}
                  columns={[
                    { title: 'Crop', dataIndex: 'crop_url', width: 110, render: (u: string | null, r) => <CropThumb src={u} alt={`Plate crop ${r.plate_display}`} sha256={r.crop_sha256} /> },
                    { title: 'Plate', dataIndex: 'plate_norm', render: (p: string, r) => <PlateText plate={p} raw={r.plate_raw} invalid={!r.is_valid_format} /> },
                    { title: 'Confidence', dataIndex: 'confidence', width: 150, render: (v: number) => <ConfidenceBar value={v} /> },
                    { title: 'Captured (IST)', dataIndex: 'captured_at', width: 150, render: (v: string) => <IstTime value={v} /> },
                  ]}
                />
              ) : (
                <EmptyState compact title="No plate reads yet" description="ANPR workers post reads within seconds of a plate being visible on this camera." actions={<Button onClick={() => navigate(`/cameras/${c.id}`)}>Open live view</Button>} />
              ),
            },
            { key: 'recordings', label: 'Recordings', children: <RecordingsTimeline cameraId={c.id} cameraName={c.name} /> },
            {
              key: 'events',
              label: 'Events',
              children: events.data?.items.length ? (
                <Table
                  size="small"
                  rowKey="id"
                  pagination={false}
                  dataSource={events.data.items}
                  columns={[
                    { title: 'Type', dataIndex: 'type', width: 150, render: (t: string) => <EventTypeTag type={t} size="small" /> },
                    { title: 'Occurred (IST)', dataIndex: 'occurred_at', width: 150, render: (v: string) => <IstTime value={v} /> },
                    { title: 'Note', dataIndex: 'note', ellipsis: true },
                    { title: 'By', dataIndex: 'created_by_username', width: 120, render: (u: string | null, r) => (r.is_auto ? <Tag style={{ margin: 0 }}>auto</Tag> : u) },
                  ]}
                />
              ) : (
                <EmptyState compact title="No events on this camera" description="Manual tags (accident, suspicious, checkpoint) and automatic events (watchlist hit, loop reset, offline) are listed here." />
              ),
            },
          ]}
        />
      ) : null}
      {c ? <MaintenanceModal camera={c} open={maintOpen} onClose={() => setMaintOpen(false)} /> : null}
      <ConfirmDialog
        open={retireOpen}
        title="Retire this camera?"
        okText="Retire camera"
        loading={retire.isPending}
        content={
          <div>
            <p>
              <strong>{c?.name}</strong> will be marked retired. Its relay path is deleted and ANPR stops on the next config reload. Historic reads, sightings and alerts are kept.
            </p>
          </div>
        }
        onOk={() => retire.mutate()}
        onCancel={() => setRetireOpen(false)}
      />
    </Drawer>
  );
}
