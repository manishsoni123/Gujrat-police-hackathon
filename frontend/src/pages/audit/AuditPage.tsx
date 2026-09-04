/** Audit log (/audit, admin): filters, table, before/after diff viewer, CSV export. */
import { useEffect, useMemo, useState } from 'react';
import { Button, Card, Input, Select, Space, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { DownloadOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { PageHeader } from '@/components/PageHeader';
import { useListQuery } from '@/hooks/useListQuery';
import { auditApi } from '@/api';
import type { AuditRow } from '@/api/types';
import { IstTime } from '@/components/IstTime';
import { RoleTag } from '@/components/Tags';
import { EmptyState, ErrorState } from '@/components/States';
import { ExportDialog } from '@/components/Dialogs';
import { RangeIst, type IsoRange } from '@/components/RangeIst';
import { fmtIst } from '@/utils/time';

const ACTION_GROUPS = ['auth.', 'user.', 'apikey.', 'camera.', 'stream.', 'recording.', 'clip.', 'watchlist.', 'alert.', 'event.', 'vehicle.', 'report.', 'evidence.', 'settings.', 'webhook.', 'zone.', 'qa.', 'retention.', 'external.'];

function DiffView({ before, after }: { before: Record<string, unknown> | null; after: Record<string, unknown> | null }) {
  const keys = Array.from(new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})]));
  if (!keys.length) return <Typography.Text type="secondary">No before/after payload recorded for this action.</Typography.Text>;
  const fmt = (v: unknown) => (v === undefined ? '' : typeof v === 'string' ? v : JSON.stringify(v));
  return (
    <Table
      size="small"
      pagination={false}
      rowKey={(r) => r.key}
      dataSource={keys.map((k) => ({ key: k, before: before?.[k], after: after?.[k], changed: fmt(before?.[k]) !== fmt(after?.[k]) }))}
      columns={[
        { title: 'Field', dataIndex: 'key', width: 200, render: (k: string) => <code>{k}</code> },
        { title: 'Before', dataIndex: 'before', render: (v: unknown, r) => <code className={r.changed && v !== undefined ? 'sg-diff-removed' : ''} style={{ fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{v === undefined ? '—' : fmt(v)}</code> },
        { title: 'After', dataIndex: 'after', render: (v: unknown, r) => <code className={r.changed && v !== undefined ? 'sg-diff-added' : ''} style={{ fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{v === undefined ? '—' : fmt(v)}</code> },
      ]}
    />
  );
}

export function AuditPage() {
  const [user, setUser] = useState('');
  const [debouncedUser, setDebouncedUser] = useState('');
  const [action, setAction] = useState<string | undefined>();
  const [entity, setEntity] = useState<string | undefined>();
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [range, setRange] = useState<IsoRange>({});
  const [exportOpen, setExportOpen] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedUser(user);
      setDebouncedQ(q);
    }, 300);
    return () => clearTimeout(t);
  }, [user, q]);

  const filters = useMemo(() => ({ user: debouncedUser || undefined, action, entity, q: debouncedQ || undefined, from: range.from, to: range.to }), [debouncedUser, action, entity, debouncedQ, range]);
  const list = useListQuery<AuditRow>({ key: ['audit', 'list'], fetcher: auditApi.list, filters, defaultSort: 'ts', defaultOrder: 'desc', pageSize: 50 });

  const columns: ColumnsType<AuditRow> = [
    { title: 'Time (IST)', dataIndex: 'ts', key: 'ts', sorter: true, width: 160, render: (v: string) => <IstTime value={v} /> },
    { title: 'Actor', dataIndex: 'actor', width: 200, render: (v: string, r) => <span><strong>{v}</strong> <RoleTag role={r.role} /></span> },
    { title: 'Action', dataIndex: 'action', width: 200, render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code> },
    { title: 'Entity', width: 180, render: (_v, r) => (r.entity ? <span>{r.entity}{r.entity_id ? <Tag style={{ marginLeft: 6 }}>#{r.entity_id}</Tag> : null}</span> : <span style={{ color: '#9CA3AF' }}>—</span>) },
    { title: 'IP', dataIndex: 'ip', width: 130, render: (v: string | null) => v ?? <span style={{ color: '#9CA3AF' }}>—</span> },
    { title: 'Client', dataIndex: 'user_agent', ellipsis: true, render: (v: string | null) => <span style={{ fontSize: 12, color: '#6B7280' }}>{v ?? '—'}</span> },
    { title: 'Diff', width: 70, render: (_v, r) => (r.before || r.after ? <Tag color="blue" style={{ margin: 0 }}>yes</Tag> : <span style={{ color: '#D1D5DB' }}>—</span>) },
  ];

  const hasFilters = Boolean(debouncedUser || action || entity || debouncedQ || range.from);
  const empty = !list.query.isLoading && list.total === 0;

  return (
    <div>
      <PageHeader
        title="Audit log"
        description={`Append-only record of logins, edits, imports, exports, stream views, searches and alert actions with user, role, IP and before/after diff · ${list.total.toLocaleString('en-IN')} entries in the window (default 7 days).`}
        extra={
          <Space wrap>
            <Button icon={<ReloadOutlined />} loading={list.query.isFetching} onClick={() => void list.query.refetch()}>
              Refresh
            </Button>
            <Button type="primary" icon={<DownloadOutlined />} onClick={() => setExportOpen(true)}>
              Export CSV
            </Button>
          </Space>
        }
      />
      <Card styles={{ body: { padding: 0 } }}>
        <div className="sg-toolbar" style={{ padding: '12px 16px 0' }}>
          <div className="sg-toolbar-left">
            <Input allowClear placeholder="Username or id" value={user} onChange={(e) => setUser(e.target.value)} style={{ width: 170 }} aria-label="User" />
            <Select allowClear showSearch placeholder="Action prefix" style={{ width: 170 }} options={ACTION_GROUPS.map((a) => ({ value: a, label: a }))} value={action} onChange={setAction} aria-label="Action" />
            <Select allowClear placeholder="Entity" style={{ width: 150 }} options={['cameras', 'alerts', 'watchlist', 'settings', 'report_files', 'users', 'api_keys', 'events', 'clips'].map((e) => ({ value: e, label: e }))} value={entity} onChange={setEntity} aria-label="Entity" />
            <RangeIst value={range} onChange={setRange} />
            <Input allowClear prefix={<SearchOutlined style={{ color: '#9CA3AF' }} />} placeholder="Search action, actor, entity id" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 240 }} aria-label="Search" />
          </div>
        </div>
        {list.query.isError ? (
          <div style={{ padding: 16 }}><ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} /></div>
        ) : empty ? (
          <EmptyState title={hasFilters ? 'No audit entries match these filters' : 'No audit entries in the last 7 days'} description="Every login and every write is recorded; the table cannot be edited or purged (database trigger)." actions={hasFilters ? <Button onClick={() => { setUser(''); setAction(undefined); setEntity(undefined); setQ(''); setRange({}); }}>Clear filters</Button> : undefined} />
        ) : (
          <Table<AuditRow>
            className="sg-table"
            size="small"
            rowKey="id"
            columns={columns}
            dataSource={list.items}
            loading={list.query.isLoading}
            pagination={list.pagination}
            onChange={list.onTableChange}
            sticky
            scroll={{ x: 1100 }}
            expandable={{ expandedRowRender: (r) => <div style={{ padding: '4px 8px' }}><div style={{ fontSize: 12, color: '#6B7280', marginBottom: 6 }}>Request {r.request_id ?? '—'} · {fmtIst(r.ts)}</div><DiffView before={r.before} after={r.after} /></div>, rowExpandable: () => true }}
          />
        )}
      </Card>
      <ExportDialog open={exportOpen} title="Export audit log" formats={['csv']} filters={[{ label: 'User', value: debouncedUser || 'all' }, { label: 'Action', value: action ?? 'all' }, { label: 'Window', value: range.from ? `${fmtIst(range.from)} → ${fmtIst(range.to)}` : 'last 7 days' }, { label: 'Rows', value: list.total }]} onExport={() => auditApi.exportCsv(filters)} onClose={() => setExportOpen(false)} />
    </div>
  );
}
