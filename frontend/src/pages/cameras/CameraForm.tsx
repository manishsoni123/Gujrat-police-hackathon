/** Add / edit camera modal with the full CONTRACT schema and a map click to set lat/lon. */
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Col, DatePicker, Form, Input, InputNumber, Modal, Row, Select, Switch, Tabs, Typography, message } from 'antd';
import { Marker, useMapEvents } from '@/components/leaflet';
import L from 'leaflet';
import { camerasApi } from '@/api';
import { ApiError } from '@/api/client';
import type { Camera, CameraImportRow } from '@/api/types';
import { useDepartments } from '@/hooks/useCamerasOptions';
import { BaseMap } from '@/components/MapView';
import { GANDHINAGAR_CENTER } from '@/utils/geo';
import { dayjs } from '@/utils/time';
import { cameraTypeLabel } from '@/utils/format';

const pinIcon = L.divIcon({ className: '', html: '<div class="sg-route-marker" style="width:18px;height:18px;font-size:10px">●</div>', iconSize: [18, 18], iconAnchor: [9, 9] });

function ClickPicker({ value, onChange }: { value: [number, number] | null; onChange: (p: [number, number]) => void }) {
  useMapEvents({ click: (e) => onChange([Number(e.latlng.lat.toFixed(6)), Number(e.latlng.lng.toFixed(6))]) });
  return value ? <Marker position={value} icon={pinIcon} /> : null;
}

interface CameraFormProps {
  open: boolean;
  onClose: () => void;
  camera?: Camera | null;
  onSaved?: (c: Camera) => void;
}

type FormValues = Omit<CameraImportRow, 'install_date' | 'amc_expiry'> & { install_date?: dayjs.Dayjs | null; amc_expiry?: dayjs.Dayjs | null };

const ENUM = {
  type: ['analog', 'ip', 'ptz', 'dome', 'bullet', 'anpr', 'other'],
  ownership: [{ value: 'govt_dept', label: 'Government department' }, { value: 'private', label: 'Private (society / business)' }, { value: 'public_facing', label: 'Public-facing' }],
  connectivity: ['lan', 'fibre', '4g', '5g', 'leased_line', 'wifi', 'other'],
  codec: ['H264', 'H265', 'MJPEG', 'UNKNOWN'],
  maintenance: [{ value: 'ok', label: 'OK' }, { value: 'under_maintenance', label: 'Under maintenance' }, { value: 'faulty', label: 'Faulty' }, { value: 'decommissioned', label: 'Decommissioned' }],
  source: [{ value: 'manual', label: 'Manual' }, { value: 'own', label: 'Own feed (private camera)' }],
};

export function CameraForm({ open, onClose, camera, onSaved }: CameraFormProps) {
  const qc = useQueryClient();
  const { departments } = useDepartments();
  const [form] = Form.useForm<FormValues>();
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [pos, setPos] = useState<[number, number] | null>(camera?.lat != null && camera?.lon != null ? [camera.lat, camera.lon] : null);
  const editing = Boolean(camera);

  useEffect(() => {
    if (!open) return;
    setFieldErrors({});
    if (camera) {
      form.setFieldsValue({
        ...camera,
        department_code: camera.department_code,
        install_date: camera.install_date ? dayjs(camera.install_date) : null,
        amc_expiry: camera.amc_expiry ? dayjs(camera.amc_expiry) : null,
      } as FormValues);
      setPos(camera.lat != null && camera.lon != null ? [camera.lat, camera.lon] : null);
    } else {
      form.resetFields();
      form.setFieldsValue({ type: 'ip', ownership: 'govt_dept', codec: 'H264', maintenance_status: 'ok', source: 'manual', anpr_enabled: false, record_enabled: false } as FormValues);
      setPos(null);
    }
  }, [open, camera, form]);

  const deptOptions = useMemo(() => departments.map((d) => ({ value: d.code, label: `${d.name} (${d.code})` })), [departments]);

  const save = useMutation({
    mutationFn: async (v: FormValues) => {
      const body: CameraImportRow = {
        ...v,
        lat: pos ? pos[0] : null,
        lon: pos ? pos[1] : null,
        install_date: v.install_date ? v.install_date.format('YYYY-MM-DD') : null,
        amc_expiry: v.amc_expiry ? v.amc_expiry.format('YYYY-MM-DD') : null,
      };
      Object.keys(body).forEach((k) => {
        const key = k as keyof CameraImportRow;
        if (body[key] === undefined || body[key] === '') delete body[key];
      });
      if (camera) {
        const { source: _s, external_id: _e, ...patch } = body;
        return camerasApi.update(camera.id, patch);
      }
      return camerasApi.create(body);
    },
    onSuccess: (c) => {
      message.success(editing ? 'Camera updated' : `Camera "${c.name}" added and relay path ${c.relay_path ?? ''} created`);
      qc.invalidateQueries({ queryKey: ['cameras'] });
      qc.invalidateQueries({ queryKey: ['geo'] });
      onSaved?.(c);
      onClose();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.errors.length) {
        const m: Record<string, string> = {};
        e.errors.forEach((x) => {
          if (x.field) m[x.field] = x.message;
        });
        setFieldErrors(m);
      }
    },
  });

  const fe = (name: string) => ({ validateStatus: fieldErrors[name] ? ('error' as const) : undefined, help: fieldErrors[name] });

  const submit = async () => {
    const v = await form.validateFields();
    setFieldErrors({});
    save.mutate(v);
  };

  const center = pos ?? GANDHINAGAR_CENTER;

  return (
    <Modal open={open} onCancel={onClose} onOk={submit} title={editing ? `Edit camera · ${camera?.name}` : 'Add camera'} okText={editing ? 'Save changes' : 'Add camera'} okButtonProps={{ loading: save.isPending }} width={900} destroyOnClose>
      <Form form={form} layout="vertical" requiredMark="optional" size="middle">
        <Tabs
          items={[
            {
              key: 'basics',
              label: 'Identity & location',
              children: (
                <Row gutter={16}>
                  <Col span={12}>
                    <Form.Item name="external_id" label="External ID" rules={[{ required: true, max: 64 }]} {...fe('external_id')} extra={editing ? 'Cannot be changed after creation' : 'Catalogue id, NVR channel id or your own reference'}>
                      <Input disabled={editing} placeholder="OWN-GATE-01" />
                    </Form.Item>
                    <Form.Item name="name" label="Name" rules={[{ required: true, max: 160 }]} {...fe('name')}>
                      <Input placeholder="Dynatech Office Gate (private society camera)" />
                    </Form.Item>
                    <Row gutter={8}>
                      <Col span={12}>
                        <Form.Item name="department_code" label="Department" {...fe('department_code')}>
                          <Select showSearch options={deptOptions} optionFilterProp="label" placeholder="Select" />
                        </Form.Item>
                      </Col>
                      <Col span={12}>
                        <Form.Item name="type" label="Type" {...fe('type')}>
                          <Select options={ENUM.type.map((t) => ({ value: t, label: cameraTypeLabel(t) }))} />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={8}>
                      <Col span={12}>
                        <Form.Item name="ownership" label="Ownership" {...fe('ownership')}>
                          <Select options={ENUM.ownership} />
                        </Form.Item>
                      </Col>
                      <Col span={12}>
                        {!editing ? (
                          <Form.Item name="source" label="Source">
                            <Select options={ENUM.source} />
                          </Form.Item>
                        ) : null}
                      </Col>
                    </Row>
                    <Form.Item name="address" label="Address" {...fe('address')}>
                      <Input />
                    </Form.Item>
                    <Row gutter={8}>
                      <Col span={8}>
                        <Form.Item name="district" label="District" {...fe('district')}>
                          <Input placeholder="Gandhinagar" />
                        </Form.Item>
                      </Col>
                      <Col span={8}>
                        <Form.Item name="police_station" label="Police station" {...fe('police_station')}>
                          <Input />
                        </Form.Item>
                      </Col>
                      <Col span={8}>
                        <Form.Item name="ward" label="Ward" {...fe('ward')}>
                          <Input />
                        </Form.Item>
                      </Col>
                    </Row>
                  </Col>
                  <Col span={12}>
                    <Typography.Text strong>Location - click the map to set coordinates</Typography.Text>
                    <div style={{ marginTop: 8 }}>
                      <BaseMap height={260} center={center} zoom={pos ? 14 : 11}>
                        <ClickPicker value={pos} onChange={setPos} />
                      </BaseMap>
                    </div>
                    <Row gutter={8} style={{ marginTop: 8 }}>
                      <Col span={12}>
                        <Form.Item label="Latitude" {...fe('lat')}>
                          <InputNumber value={pos?.[0]} min={-90} max={90} step={0.0001} style={{ width: '100%' }} onChange={(v) => setPos([Number(v ?? 0), pos?.[1] ?? 72.6])} />
                        </Form.Item>
                      </Col>
                      <Col span={12}>
                        <Form.Item label="Longitude" {...fe('lon')}>
                          <InputNumber value={pos?.[1]} min={-180} max={180} step={0.0001} style={{ width: '100%' }} onChange={(v) => setPos([pos?.[0] ?? 23.2, Number(v ?? 0)])} />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={8}>
                      <Col span={12}>
                        <Form.Item name="heading_deg" label="Heading (°)" {...fe('heading_deg')}>
                          <InputNumber min={0} max={359} style={{ width: '100%' }} />
                        </Form.Item>
                      </Col>
                      <Col span={12}>
                        <Form.Item name="fov_deg" label="Field of view (°)" {...fe('fov_deg')}>
                          <InputNumber min={1} max={360} style={{ width: '100%' }} />
                        </Form.Item>
                      </Col>
                    </Row>
                  </Col>
                </Row>
              ),
            },
            {
              key: 'stream',
              label: 'Stream & analytics',
              children: (
                <Row gutter={16}>
                  <Col span={14}>
                    <Form.Item name="rtsp_url" label="RTSP URL" {...fe('rtsp_url')} extra="rtsp:// or rtsps://; credentials allowed. The relay pulls this over TCP on demand." rules={[{ pattern: /^rtsps?:\/\//, message: 'Must start with rtsp:// or rtsps://' }]}>
                      <Input placeholder="rtsp://mediamtx:8554/own_gate" />
                    </Form.Item>
                    <Row gutter={8}>
                      <Col span={8}>
                        <Form.Item name="codec" label="Codec" {...fe('codec')}>
                          <Select options={ENUM.codec.map((c) => ({ value: c, label: c }))} />
                        </Form.Item>
                      </Col>
                      <Col span={8}>
                        <Form.Item name="resolution" label="Resolution" {...fe('resolution')} rules={[{ pattern: /^\d{2,5}x\d{2,5}$/, message: 'WIDTHxHEIGHT' }]}>
                          <Input placeholder="1920x1080" />
                        </Form.Item>
                      </Col>
                      <Col span={8}>
                        <Form.Item name="fps" label="FPS" {...fe('fps')}>
                          <InputNumber min={1} max={60} style={{ width: '100%' }} />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={8}>
                      <Col span={12}>
                        <Form.Item name="anpr_enabled" label="Live ANPR" valuePropName="checked" extra="Worker decodes at 5 fps and posts reads">
                          <Switch />
                        </Form.Item>
                      </Col>
                      <Col span={12}>
                        <Form.Item name="record_enabled" label="Record (12 h rolling)" valuePropName="checked" extra="fMP4 segments on the relay path">
                          <Switch />
                        </Form.Item>
                      </Col>
                    </Row>
                  </Col>
                  <Col span={10}>
                    <Form.Item name="connectivity_type" label="Connectivity" {...fe('connectivity_type')}>
                      <Select allowClear options={ENUM.connectivity.map((c) => ({ value: c, label: c.toUpperCase().replace('_', ' ') }))} />
                    </Form.Item>
                    <Form.Item name="bandwidth_kbps" label="Bandwidth (kbps)" {...fe('bandwidth_kbps')}>
                      <InputNumber min={0} style={{ width: '100%' }} />
                    </Form.Item>
                    <Form.Item name="vms_platform" label="VMS / NVR platform" {...fe('vms_platform')}>
                      <Input placeholder="Milestone, Hikvision NVR, none" />
                    </Form.Item>
                    <Form.Item name="nvr_id" label="NVR / DVR id" {...fe('nvr_id')}>
                      <Input />
                    </Form.Item>
                  </Col>
                </Row>
              ),
            },
            {
              key: 'asset',
              label: 'Asset & maintenance',
              children: (
                <Row gutter={16}>
                  <Col span={12}>
                    <Row gutter={8}>
                      <Col span={12}>
                        <Form.Item name="vendor" label="Vendor" {...fe('vendor')}>
                          <Input />
                        </Form.Item>
                      </Col>
                      <Col span={12}>
                        <Form.Item name="model" label="Model" {...fe('model')}>
                          <Input />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Form.Item name="install_date" label="Install date" {...fe('install_date')}>
                      <DatePicker style={{ width: '100%' }} format="DD MMM YYYY" />
                    </Form.Item>
                    <Form.Item name="storage_location" label="Storage location" {...fe('storage_location')}>
                      <Input placeholder="NVR at police station" />
                    </Form.Item>
                    <Form.Item name="retention_days" label="Retention (days)" {...fe('retention_days')}>
                      <InputNumber min={0} max={3650} style={{ width: '100%' }} />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item name="maintenance_status" label="Maintenance status" {...fe('maintenance_status')}>
                      <Select options={ENUM.maintenance} />
                    </Form.Item>
                    <Form.Item name="amc_vendor" label="AMC vendor" {...fe('amc_vendor')}>
                      <Input />
                    </Form.Item>
                    <Form.Item name="amc_expiry" label="AMC expiry" {...fe('amc_expiry')}>
                      <DatePicker style={{ width: '100%' }} format="DD MMM YYYY" />
                    </Form.Item>
                  </Col>
                </Row>
              ),
            },
          ]}
        />
      </Form>
    </Modal>
  );
}
