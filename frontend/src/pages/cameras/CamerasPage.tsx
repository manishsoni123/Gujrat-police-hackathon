/** Camera registry: search/filter/sort/paginate, export CSV, add/edit/retire, row → drawer. */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Button, Card, Input, Select, Space, Table, Tooltip, Switch } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { CloudUploadOutlined, DownloadOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { PageHeader } from '@/components/PageHeader';
import { useListQuery } from '@/hooks/useListQuery';
import { camerasApi } from '@/api';
import type { Camera } from '@/api/types';
import { StatusTag, MaintenanceTag, DepartmentTag, CodecTag, AmcTag } from '@/components/Tags';
import { IstTime } from '@/components/IstTime';
import { EmptyState, ErrorState } from '@/components/States';
import { usePermission } from '@/store/auth';
import { useDepartments, useDistricts } from '@/hooks/useCamerasOptions';
import { CameraDrawer } from './CameraDrawer';
import { CameraForm } from './CameraForm';
import { ExportDialog } from '@/components/Dialogs';
import { cameraTypeLabel, fmtPct } from '@/utils/format';

const TYPES = ['analog', 'ip', 'ptz', 'dome', 'bullet', 'anpr', 'other'];
const STATUSES = ['online', 'degraded', 'offline', 'unknown', 'retired'];

export function CamerasPage() {
  const navigate = useNavigate();
  const [sp, setSp] = useSearchParams();
  const canWrite = usePermission('cameras.write');
  const canExport = usePermission('cameras.export');
  const { options: deptOptions } = useDepartments();
  const districts = useDistricts();

  const [q, setQ] = useState(sp.get('q') ?? '');
  const [debouncedQ, setDebouncedQ] = useState(q);
  const [department, setDepartment] = useState<number | undefined>(sp.get('department_id') ? Number(sp.get('department_id')) : undefined);
  const [district, setDistrict] = useState<string | undefined>(sp.get('district') ?? undefined);
  const [type, setType] = useState<string | undefined>(sp.get('type') ?? undefined);
  const [status, setStatus] = useState<string[]>(sp.get('status') ? sp.get('status')!.split(',') : []);
  const [includeRetired, setIncludeRetired] = useState(false);
  const [openId, setOpenId] = useState<number | null>(sp.get('open') ? Number(sp.get('open')) : null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Camera | null>(null);
  const [exportOpen, setExportOpen] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  const filters = useMemo(
    () => ({ q: debouncedQ || undefined, department_id: department, district, type, status: status.length ? status.join(',') : undefined, include_retired: includeRetired || undefined }),
    [debouncedQ, department, district, type, status, includeRetired],
  );

  const list = useListQuery<Camera>({ key: ['cameras', 'list'], fetcher: camerasApi.list, filters, defaultSort: 'name', defaultOrder: 'asc' });

  useEffect(() => {
    const next = new URLSearchParams();
    if (openId) next.set('open', String(openId));
    if (debouncedQ) next.set('q', debouncedQ);
    if (status.length) next.set('status', status.join(','));
    setSp(next, { replace: true });
  }, [openId, debouncedQ, status, setSp]);

  const columns: ColumnsType<Camera> = [
    { title: 'Name', dataIndex: 'name', key: 'name', sorter: true, ellipsis: true, width: 260, fixed: 'left', render: (v: string, r) => <span style={{ fontWeight: 500, textDecoration: r.status === 'retired' ? 'line-through' : undefined }}>{v}</span> },
    { title: 'ID', dataIndex: 'external_id', key: 'external_id', width: 100, sorter: true, render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code> },
    { title: 'Department', dataIndex: 'department_code', key: 'department_name', width: 130, sorter: true, render: (v: string, r) => <DepartmentTag code={v} name={r.department_name} size="small" /> },
    { title: 'District', dataIndex: 'district', key: 'district', width: 130, sorter: true, render: (v: string | null) => v ?? <span style={{ color: '#9CA3AF' }}>—</span> },
    { title: 'Police station', dataIndex: 'police_station', width: 140, ellipsis: true, render: (v: string | null) => v ?? <span style={{ color: '#9CA3AF' }}>—</span> },
    { title: 'Type', dataIndex: 'type', width: 100, render: (v: string) => cameraTypeLabel(v) },
    { title: 'Codec', dataIndex: 'codec', width: 80, render: (v: string) => <CodecTag codec={v} /> },
    { title: 'Status', dataIndex: 'status', key: 'status', width: 110, sorter: true, render: (v: string) => <StatusTag status={v} size="small" /> },
    { title: 'Maint.', dataIndex: 'maintenance_status', width: 120, render: (v: string) => (v === 'ok' ? <span style={{ color: '#9CA3AF' }}>—</span> : <MaintenanceTag status={v} size="small" />) },
    { title: 'AMC', dataIndex: 'amc_status', key: 'amc_expiry', width: 110, sorter: true, render: (v: string) => (v === 'ok' || v === 'none' ? <span style={{ color: '#9CA3AF' }}>{v === 'ok' ? 'valid' : '—'}</span> : <AmcTag status={v} size="small" />) },
    { title: 'Uptime 24h', dataIndex: 'uptime_24h_pct', width: 100, align: 'right', render: (v: number | null) => fmtPct(v, 0) },
    { title: 'ANPR', dataIndex: 'anpr_enabled', width: 70, align: 'center', render: (v: boolean) => (v ? <span style={{ color: '#1E4DB7', fontWeight: 600 }}>on</span> : <span style={{ color: '#9CA3AF' }}>—</span>) },
    { title: 'Last seen', dataIndex: 'last_seen_at', key: 'last_seen_at', width: 140, sorter: true, render: (v: string | null) => <IstTime value={v} /> },
  ];

  const empty = !list.query.isLoading && list.total === 0;
  const hasFilters = Boolean(debouncedQ || department || district || type || status.length);

  return (
    <div>
      <PageHeader
        title="Cameras"
        description={`Camera registry · ${list.total} camera${list.total === 1 ? '' : 's'}${hasFilters ? ' matching filters' : ''}. Click a row for metadata, health history and streams.`}
        extra={
          <Space wrap>
            {canExport ? (
              <Button icon={<DownloadOutlined />} onClick={() => setExportOpen(true)}>
                Export CSV
              </Button>
            ) : null}
            {canWrite ? (
              <Button icon={<CloudUploadOutlined />} onClick={() => navigate('/cameras/import')}>
                Import
              </Button>
            ) : null}
            {canWrite ? (
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => {
                  setEditing(null);
                  setFormOpen(true);
                }}
              >
                Add camera
              </Button>
            ) : null}
          </Space>
        }
      />
      <Card styles={{ body: { padding: 0 } }}>
        <div className="sg-toolbar" style={{ padding: '12px 16px 0' }}>
          <div className="sg-toolbar-left">
            <Input allowClear prefix={<SearchOutlined style={{ color: '#9CA3AF' }} />} placeholder="Search name, id, address, police station" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 300 }} aria-label="Search cameras" />
            <Select allowClear placeholder="Department" style={{ width: 200 }} options={deptOptions} value={department} onChange={setDepartment} showSearch optionFilterProp="label" aria-label="Department" />
            <Select allowClear placeholder="District" style={{ width: 160 }} options={districts.map((d) => ({ value: d, label: d }))} value={district} onChange={setDistrict} showSearch aria-label="District" />
            <Select allowClear placeholder="Type" style={{ width: 120 }} options={TYPES.map((t) => ({ value: t, label: cameraTypeLabel(t) }))} value={type} onChange={setType} aria-label="Type" />
            <Select mode="multiple" allowClear placeholder="Status" style={{ minWidth: 160 }} options={STATUSES.map((s) => ({ value: s, label: s }))} value={status} onChange={setStatus} maxTagCount="responsive" aria-label="Status" />
          </div>
          <div className="sg-toolbar-right">
            <Tooltip title="Include retired cameras">
              <Space size={6}>
                <Switch size="small" checked={includeRetired} onChange={setIncludeRetired} aria-label="Include retired" />
                <span style={{ fontSize: 12, color: '#6B7280' }}>Retired</span>
              </Space>
            </Tooltip>
            <Button icon={<ReloadOutlined />} onClick={() => void list.query.refetch()} loading={list.query.isFetching} aria-label="Refresh" />
          </div>
        </div>
        {list.query.isError ? (
          <div style={{ padding: 16 }}>
            <ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />
          </div>
        ) : empty ? (
          <EmptyState
            title={hasFilters ? 'No cameras match these filters' : 'No cameras yet'}
            description={hasFilters ? 'Try clearing a filter or widening the search.' : 'Import from the sandbox catalogue, upload a CSV, push through the bulk API, or add one manually.'}
            actions={
              hasFilters ? (
                <Button
                  onClick={() => {
                    setQ('');
                    setDepartment(undefined);
                    setDistrict(undefined);
                    setType(undefined);
                    setStatus([]);
                  }}
                >
                  Clear filters
                </Button>
              ) : canWrite ? (
                <>
                  <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => navigate('/cameras/import')}>
                    Import from catalogue
                  </Button>
                  <Button icon={<PlusOutlined />} onClick={() => setFormOpen(true)}>
                    Add camera
                  </Button>
                </>
              ) : null
            }
          />
        ) : (
          <Table<Camera>
            className="sg-table"
            size="middle"
            rowKey="id"
            columns={columns}
            dataSource={list.items}
            loading={list.query.isLoading}
            pagination={list.pagination}
            onChange={list.onTableChange}
            sticky
            scroll={{ x: 1560 }}
            rowClassName={(r) => (r.status === 'retired' ? 'sg-row-retired' : '')}
            onRow={(r) => ({ onClick: () => setOpenId(r.id) })}
          />
        )}
      </Card>
      <CameraDrawer
        cameraId={openId}
        onClose={() => setOpenId(null)}
        onEdit={(c) => {
          setEditing(c);
          setFormOpen(true);
        }}
      />
      <CameraForm open={formOpen} onClose={() => setFormOpen(false)} camera={editing} />
      <ExportDialog
        open={exportOpen}
        title="Export camera registry"
        formats={['csv']}
        filters={[
          { label: 'Search', value: debouncedQ || 'all' },
          { label: 'Department', value: department ? deptOptions.find((d) => d.value === department)?.label : 'all' },
          { label: 'District', value: district ?? 'all' },
          { label: 'Status', value: status.length ? status.join(', ') : 'all' },
          { label: 'Rows', value: list.total },
        ]}
        onExport={() => camerasApi.exportCsv(filters)}
        onClose={() => setExportOpen(false)}
      />
    </div>
  );
}
