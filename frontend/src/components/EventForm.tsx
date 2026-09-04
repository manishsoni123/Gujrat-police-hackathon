/** "Tag event" modal (manual event types only, CONTRACT §5.13). */
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { DatePicker, Form, Input, Modal, Select, message } from 'antd';
import { eventsApi } from '@/api';
import type { ManualEventType } from '@/api/types';
import { useCameraOptions } from '@/hooks/useCamerasOptions';
import { dayjs, istToUtcIso, toIst } from '@/utils/time';
import { ApiError } from '@/api/client';

interface EventFormProps {
  open: boolean;
  onClose: () => void;
  cameraId?: number;
  sightingId?: number | null;
  readId?: number | null;
  defaultOccurredAt?: string | null;
}

const TYPES: { value: ManualEventType; label: string }[] = [
  { value: 'accident', label: 'Accident' },
  { value: 'suspicious', label: 'Suspicious activity' },
  { value: 'checkpoint', label: 'Checkpoint' },
  { value: 'other', label: 'Other' },
];

export function EventForm({ open, onClose, cameraId, sightingId, readId, defaultOccurredAt }: EventFormProps) {
  const qc = useQueryClient();
  const { options, loading } = useCameraOptions();
  const [form] = Form.useForm<{ camera_id: number; type: ManualEventType; occurred_at: dayjs.Dayjs; note?: string }>();
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});

  const create = useMutation({
    mutationFn: eventsApi.create,
    onSuccess: () => {
      message.success('Event tagged');
      qc.invalidateQueries({ queryKey: ['events'] });
      form.resetFields();
      onClose();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.errors.length) {
        const m: Record<string, string> = {};
        e.errors.forEach((x) => {
          if (x.field) m[x.field] = x.message;
        });
        setServerErrors(m);
      }
    },
  });

  const submit = async () => {
    const v = await form.validateFields();
    setServerErrors({});
    create.mutate({ camera_id: v.camera_id, type: v.type, occurred_at: istToUtcIso(v.occurred_at), note: v.note?.trim() || undefined, sighting_id: sightingId ?? undefined, read_id: readId ?? undefined });
  };

  return (
    <Modal open={open} title="Tag event" onCancel={onClose} onOk={submit} okText="Save event" okButtonProps={{ loading: create.isPending }} destroyOnClose>
      <Form form={form} layout="vertical" requiredMark={false} initialValues={{ camera_id: cameraId, type: 'suspicious', occurred_at: toIst(defaultOccurredAt ?? undefined) ?? dayjs().tz('Asia/Kolkata') }}>
        <Form.Item name="camera_id" label="Camera" rules={[{ required: true }]} validateStatus={serverErrors.camera_id ? 'error' : undefined} help={serverErrors.camera_id}>
          <Select showSearch options={options} loading={loading} optionFilterProp="label" disabled={Boolean(cameraId)} placeholder="Select camera" />
        </Form.Item>
        <Form.Item name="type" label="Event type" rules={[{ required: true }]} validateStatus={serverErrors.type ? 'error' : undefined} help={serverErrors.type}>
          <Select options={TYPES} />
        </Form.Item>
        <Form.Item name="occurred_at" label="Occurred at (IST)" rules={[{ required: true }]}>
          <DatePicker showTime style={{ width: '100%' }} format="DD MMM YYYY, HH:mm:ss" />
        </Form.Item>
        <Form.Item name="note" label="Note" rules={[{ max: 500 }]} validateStatus={serverErrors.note ? 'error' : undefined} help={serverErrors.note}>
          <Input.TextArea rows={3} placeholder="What happened? Reference an FIR or checkpoint if applicable." />
        </Form.Item>
        {sightingId ? <div style={{ fontSize: 12, color: '#6B7280' }}>Linked to sighting #{sightingId}{readId ? ` and read #${readId}` : ''}.</div> : null}
      </Form>
    </Modal>
  );
}
