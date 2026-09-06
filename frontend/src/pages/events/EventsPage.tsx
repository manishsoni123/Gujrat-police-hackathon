/** Events (/events): manual tags and automatic events with filters; "Tag event" for events.write. */
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Card, Select, Space, Switch, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { PageHeader } from '@/components/PageHeader';
import { rowProps, useListQuery } from '@/hooks/useListQuery';
import { eventsApi } from '@/api';
import type { EventItem } from '@/api/types';
import { EventTypeTag, DepartmentTag, StatusTag } from '@/components/Tags';
import { IstTime } from '@/components/IstTime';
import { CropThumb } from '@/components/Misc';
import { EmptyState, ErrorState } from '@/components/States';
import { RangeIst, type IsoRange } from '@/components/RangeIst';
import { EventForm } from '@/components/EventForm';
import { useCameraOptions, useDepartments } from '@/hooks/useCamerasOptions';
import { usePermission } from '@/store/auth';
import { EVENT_TYPE } from '@/theme/colours';

const MANUAL = ['accident', 'suspicious', 'checkpoint', 'other'];
const AUTO = ['watchlist_hit', 'loop_reset', 'intrusion', 'camera_offline', 'camera_online'];

export function EventsPage() {
  const navigate = useNavigate();
  const canWrite = usePermission('events.write');
  const { options: cameraOptions } = useCameraOptions();
  const { options: deptOptions } = useDepartments();
  const [types, setTypes] = useState<string[]>([]);
  const [cameraId, setCameraId] = useState<number | undefined>();
  const [deptId, setDeptId] = useState<number | undefined>();
  const [range, setRange] = useState<IsoRange>({});
  const [manualOnly, setManualOnly] = useState(false);
  const [formOpen, setFormOpen] = useState(false);

  const filters = useMemo(() => ({ type: types.length ? types.join(',') : undefined, camera_id: cameraId, department_id: deptId, from: range.from, to: range.to, is_auto: manualOnly ? false : undefined }), [types, cameraId, deptId, range, manualOnly]);
  const list = useListQuery<EventItem>({ key: ['events', 'list'], fetcher: eventsApi.list, filters, defaultSort: 'occurred_at', defaultOrder: 'desc', refetchInterval: 30_000 });

  const columns: ColumnsType<EventItem> = [
    { title: 'Type', dataIndex: 'type', width: 160, render: (v: string) => <EventTypeTag type={v} /> },
    { title: 'Occurred (IST)', dataIndex: 'occurred_at', key: 'occurred_at', sorter: true, width: 150, render: (v: string) => <IstTime value={v} /> },
    { title: 'Camera', dataIndex: ['camera', 'name'], width: 260, ellipsis: true, render: (v: string, e) => <span><Button type="link" style={{ padding: 0, fontWeight: 600 }} onClick={(ev) => { ev.stopPropagation(); navigate(`/cameras/${e.camera.id}`); }}>{v}</Button><div style={{ fontSize: 11, color: '#6B7280' }}><DepartmentTag code={e.camera.department_code} size="small" /> {e.camera.district ?? '—'}</div></span> },
    { title: 'Note', dataIndex: 'note', width: 340, ellipsis: { showTitle: false }, render: (v: string | null) => (v ? <Tooltip title={v} placement="topLeft"><span>{v}</span></Tooltip> : <span style={{ color: '#9CA3AF' }}>—</span>) },
    { title: 'Links', width: 170, render: (_v, e) => <Space size={4} wrap>{e.alert_id ? <Button size="small" type="link" style={{ padding: 0 }} onClick={(ev) => { ev.stopPropagation(); navigate(`/alerts?id=${e.alert_id}`); }}>alert #{e.alert_id}</Button> : null}{e.read_id ? <Button size="small" type="link" style={{ padding: 0 }} onClick={(ev) => { ev.stopPropagation(); navigate(`/detections?id=${e.read_id}`); }}>read #{e.read_id}</Button> : null}{e.sighting_id ? <span style={{ fontSize: 12, color: '#6B7280' }}>sighting #{e.sighting_id}</span> : null}</Space> },
    { title: 'Frame', dataIndex: 'frame_url', width: 90, render: (u: string | null, e) => (u ? <CropThumb src={u} alt={`Frame for ${e.type_label}`} width={72} height={40} sha256={e.frame_sha256} /> : <span style={{ color: '#D1D5DB' }}>—</span>) },
    { title: 'Origin', dataIndex: 'is_auto', width: 130, render: (auto: boolean, e) => (auto ? <Tag style={{ margin: 0 }}>automatic</Tag> : <span>{e.created_by_username ?? 'manual'}</span>) },
    { title: 'Camera status', dataIndex: ['camera', 'status'], width: 110, render: (v: string) => <StatusTag status={v} size="small" /> },
  ];

  const hasFilters = Boolean(types.length || cameraId || deptId || range.from || manualOnly);
  const empty = !list.query.isLoading && list.total === 0;

  return (
    <div>
      <PageHeader
        title="Events"
        description={`Operator tags (accident, suspicious, checkpoint, other) and automatic events (watchlist hit, loop reset, intrusion, camera offline/online) · ${list.total} in the window.`}
        extra={
          <Space wrap>
            <Button icon={<ReloadOutlined />} loading={list.query.isFetching} onClick={() => void list.query.refetch()}>
              Refresh
            </Button>
            {canWrite ? (
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setFormOpen(true)}>
                Tag event
              </Button>
            ) : null}
          </Space>
        }
      />
      <Card styles={{ body: { padding: 0 } }}>
        <div className="sg-toolbar" style={{ padding: '12px 16px 0' }}>
          <div className="sg-toolbar-left">
            <Select mode="multiple" allowClear placeholder="Event type" style={{ minWidth: 240 }} value={types} onChange={setTypes} maxTagCount="responsive" aria-label="Event type" options={[{ label: 'Manual', options: MANUAL.map((t) => ({ value: t, label: EVENT_TYPE[t].label })) }, { label: 'Automatic', options: AUTO.map((t) => ({ value: t, label: EVENT_TYPE[t].label })) }]} />
            <Select allowClear showSearch optionFilterProp="label" placeholder="Camera" style={{ width: 230 }} options={cameraOptions} value={cameraId} onChange={setCameraId} aria-label="Camera" />
            <Select allowClear showSearch optionFilterProp="label" placeholder="Department" style={{ width: 190 }} options={deptOptions} value={deptId} onChange={setDeptId} aria-label="Department" />
            <RangeIst value={range} onChange={setRange} />
          </div>
          <div className="sg-toolbar-right">
            <Space size={6}><Switch size="small" checked={manualOnly} onChange={setManualOnly} aria-label="Manual only" /><span style={{ fontSize: 12, color: '#6B7280' }}>Manual tags only</span></Space>
          </div>
        </div>
        {list.query.isError ? (
          <div style={{ padding: 16 }}><ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} /></div>
        ) : empty ? (
          <EmptyState title={hasFilters ? 'No events match these filters' : 'No events in the last 24 hours'} description={hasFilters ? 'Widen the window or clear a filter.' : 'Loop resets and watchlist hits are logged automatically; operators can tag accidents, suspicious activity and checkpoints from any camera or read.'} actions={canWrite && !hasFilters ? <Button type="primary" icon={<PlusOutlined />} onClick={() => setFormOpen(true)}>Tag event</Button> : undefined} />
        ) : (
          <Table<EventItem> className="sg-table" size="middle" rowKey="id" columns={columns} dataSource={list.items} loading={list.query.isLoading} pagination={list.pagination} onChange={list.onTableChange} sticky scroll={{ x: 1420 }} onRow={(e) => rowProps(() => navigate(`/cameras/${e.camera.id}`))} />
        )}
      </Card>
      <EventForm open={formOpen} onClose={() => setFormOpen(false)} />
    </div>
  );
}
