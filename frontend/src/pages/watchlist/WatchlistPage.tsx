/** Watchlist (/watchlist): table with filters, add/edit modal, CSV import with template, deactivate/delete. */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Checkbox, DatePicker, Form, Input, Modal, Select, Space, Switch, Table, Tooltip, Upload, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { DeleteOutlined, DownloadOutlined, EditOutlined, InboxOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, UploadOutlined } from '@ant-design/icons';
import { PageHeader } from '@/components/PageHeader';
import { rowProps, useListQuery } from '@/hooks/useListQuery';
import { watchlistApi } from '@/api';
import { ApiError, errorMessage } from '@/api/client';
import type { WatchlistEntry, WatchlistImportResult, WatchlistInput } from '@/api/types';
import { ColourTag, PriorityTag, ReasonTag } from '@/components/Tags';
import { PlateText } from '@/components/PlateText';
import { IstTime } from '@/components/IstTime';
import { EmptyState, ErrorState } from '@/components/States';
import { ConfirmDialog } from '@/components/Dialogs';
import { usePermission } from '@/store/auth';
import { normalisePlate, formatPlate } from '@/utils/plate';
import { dayjs, istToUtcIso, toIst } from '@/utils/time';
import { humanise } from '@/utils/format';

const REASONS = ['stolen', 'wanted', 'blacklisted', 'missing', 'suspect', 'arrested', 'unidentified_body', 'other'];
const PRIORITIES = ['critical', 'high', 'medium', 'low'];
const SOURCES = ['own', 'egujcop', 'vahan', 'manual', 'import'];

type FormValues = Omit<WatchlistInput, 'expires_at'> & { expires_at?: dayjs.Dayjs | null };

function EntryModal({ open, onClose, entry }: { open: boolean; onClose: () => void; entry: WatchlistEntry | null }) {
  const qc = useQueryClient();
  const [form] = Form.useForm<FormValues>();
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [conflict, setConflict] = useState<number | null>(null);
  const entity = Form.useWatch('entity_type', form);
  const plateValue = Form.useWatch('plate', form);
  const preview = plateValue ? normalisePlate(plateValue) : null;

  useEffect(() => {
    if (!open) return;
    setFieldErrors({});
    setConflict(null);
    if (entry) form.setFieldsValue({ entity_type: entry.entity_type, plate: entry.plate_norm ?? undefined, name: entry.name ?? undefined, reason: entry.reason, priority: entry.priority, source: entry.source, notes: entry.notes ?? undefined, expires_at: toIst(entry.expires_at), is_active: entry.is_active });
    else {
      form.resetFields();
      form.setFieldsValue({ entity_type: 'vehicle', reason: 'stolen', priority: 'high', source: 'manual', is_active: true });
    }
  }, [open, entry, form]);

  const save = useMutation({
    mutationFn: (v: FormValues) => {
      const body: WatchlistInput = { ...v, expires_at: v.expires_at ? istToUtcIso(v.expires_at) : null, plate: v.entity_type === 'vehicle' ? v.plate : undefined };
      return entry ? watchlistApi.update(entry.id, body) : watchlistApi.create(body);
    },
    onSuccess: (w) => {
      message.success(entry ? 'Watchlist entry updated' : `${w.plate_display ?? w.name} added to the watchlist - the matcher reloads immediately`);
      qc.invalidateQueries({ queryKey: ['watchlist'] });
      onClose();
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        if (e.status === 409) setConflict((e.body?.existing_id as number | undefined) ?? -1);
        const m: Record<string, string> = {};
        e.errors.forEach((x) => {
          if (x.field) m[x.field] = x.message;
        });
        setFieldErrors(m);
      }
    },
  });

  return (
    <Modal open={open} onCancel={onClose} title={entry ? 'Edit watchlist entry' : 'Add to watchlist'} okText={entry ? 'Save' : 'Add entry'} okButtonProps={{ loading: save.isPending }} onOk={async () => save.mutate(await form.validateFields())} destroyOnClose width={560}>
      {conflict !== null ? <Alert type="warning" showIcon message="This plate is already on the active watchlist" description={conflict > 0 ? `Existing entry #${conflict}. Edit that entry instead of adding a duplicate.` : undefined} style={{ marginBottom: 12 }} /> : null}
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item name="entity_type" label="Entity">
          <Select options={[{ value: 'vehicle', label: 'Vehicle (plate)' }, { value: 'person', label: 'Person (face recognition, roadmap)' }]} disabled={Boolean(entry)} />
        </Form.Item>
        {entity !== 'person' ? (
          <Form.Item name="plate" label="Registration" rules={[{ required: true, message: 'Enter the registration' }]} validateStatus={fieldErrors.plate ? 'error' : undefined} help={fieldErrors.plate ?? (preview ? (preview.is_valid_format ? `Stored as ${formatPlate(preview.plate_norm)}` : 'Not a valid Indian format - the API will reject it') : undefined)}>
            <Input placeholder="GJ 27 XY 3456" style={{ fontFamily: 'ui-monospace, monospace', letterSpacing: 1 }} autoFocus />
          </Form.Item>
        ) : null}
        <Form.Item name="name" label={entity === 'person' ? 'Name' : 'Description (vehicle, case reference)'} rules={entity === 'person' ? [{ required: true }] : []} validateStatus={fieldErrors.name ? 'error' : undefined} help={fieldErrors.name}>
          <Input placeholder="White Maruti Swift – FIR 123/2026" />
        </Form.Item>
        <Space style={{ display: 'flex' }} align="start">
          <Form.Item name="reason" label="Reason" rules={[{ required: true }]} style={{ width: 200 }}>
            <Select options={REASONS.map((r) => ({ value: r, label: humanise(r) }))} />
          </Form.Item>
          <Form.Item name="priority" label="Priority" style={{ width: 150 }}>
            <Select options={PRIORITIES.map((p) => ({ value: p, label: humanise(p) }))} />
          </Form.Item>
          <Form.Item name="source" label="Source" style={{ width: 150 }}>
            <Select options={SOURCES.map((s) => ({ value: s, label: s === 'egujcop' ? 'eGujCop' : s === 'vahan' ? 'VAHAN' : humanise(s) }))} />
          </Form.Item>
        </Space>
        <Form.Item name="notes" label="Notes">
          <Input.TextArea rows={2} />
        </Form.Item>
        <Space style={{ display: 'flex' }} align="start">
          <Form.Item name="expires_at" label="Expires (IST)" style={{ width: 260 }}>
            <DatePicker showTime style={{ width: '100%' }} format="DD MMM YYYY, HH:mm" />
          </Form.Item>
          <Form.Item name="is_active" label="Active" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Space>
        <div style={{ fontSize: 12, color: '#6B7280' }}>Alert priority = max(entry priority, reason floor: stolen/wanted → critical, blacklisted/arrested → high, missing → medium, suspect → low), one step lower for possible (fuzzy) matches.</div>
      </Form>
    </Modal>
  );
}

function ImportModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [dryRun, setDryRun] = useState(false);
  const [result, setResult] = useState<WatchlistImportResult | null>(null);
  const run = useMutation({
    mutationFn: (f: File) => watchlistApi.importCsv(f, dryRun),
    onSuccess: (r) => {
      setResult(r);
      qc.invalidateQueries({ queryKey: ['watchlist'] });
    },
  });
  return (
    <Modal open={open} onCancel={onClose} title="Import watchlist CSV" footer={null} destroyOnClose>
      <Space style={{ marginBottom: 12 }}>
        <Button icon={<DownloadOutlined />} onClick={() => watchlistApi.template().catch((e) => message.error(errorMessage(e)))}>
          Download template
        </Button>
        <span style={{ fontSize: 12, color: '#6B7280' }}>plate, entity_type, name, reason, priority, source, notes, expires_at, is_active</span>
      </Space>
      <Upload.Dragger accept=".csv" maxCount={1} beforeUpload={(f) => { setFile(f); setResult(null); return false; }} onRemove={() => setFile(null)}>
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p className="ant-upload-text">Click or drop the CSV here</p>
        <p className="ant-upload-hint">Vehicles upsert on the normalised plate; persons are inserted. eGujCop exports map onto these columns.</p>
      </Upload.Dragger>
      <Space style={{ marginTop: 12 }}>
        <Button type="primary" icon={<UploadOutlined />} disabled={!file} loading={run.isPending} onClick={() => file && run.mutate(file)}>
          {dryRun ? 'Validate' : 'Import'}
        </Button>
        <Checkbox checked={dryRun} onChange={(e) => setDryRun(e.target.checked)}>Dry run</Checkbox>
      </Space>
      {run.isError ? <Alert type="error" showIcon style={{ marginTop: 12 }} message={errorMessage(run.error)} /> : null}
      {result ? (
        <div style={{ marginTop: 12 }}>
          <Alert type={result.errors.length ? 'warning' : 'success'} showIcon message={`${result.rows_total} rows · ${result.added} added · ${result.updated} updated · ${result.errors.length} errors · ${result.duration_ms} ms`} />
          {result.errors.length ? (
            <Table size="small" style={{ marginTop: 8 }} pagination={false} rowKey={(r, i) => `${r.row}-${i}`} dataSource={result.errors} columns={[{ title: 'Row', dataIndex: 'row', width: 60 }, { title: 'Field', dataIndex: 'field', width: 110 }, { title: 'Message', dataIndex: 'message' }]} />
          ) : null}
        </div>
      ) : null}
    </Modal>
  );
}

export function WatchlistPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const canWrite = usePermission('watchlist.write');
  const [q, setQ] = useState('');
  const [debounced, setDebounced] = useState('');
  const [entity, setEntity] = useState<string | undefined>();
  const [reason, setReason] = useState<string | undefined>();
  const [priority, setPriority] = useState<string | undefined>();
  const [active, setActive] = useState<'true' | 'false' | 'all'>('true');
  const [modal, setModal] = useState<{ open: boolean; entry: WatchlistEntry | null }>({ open: false, entry: null });
  const [importOpen, setImportOpen] = useState(false);
  const [remove, setRemove] = useState<WatchlistEntry | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  const filters = useMemo(() => ({ q: debounced || undefined, entity_type: entity, reason, priority, is_active: active }), [debounced, entity, reason, priority, active]);
  const list = useListQuery<WatchlistEntry>({ key: ['watchlist', 'list'], fetcher: watchlistApi.list, filters, defaultSort: 'created_at', defaultOrder: 'desc' });

  const del = useMutation({
    mutationFn: (id: number) => watchlistApi.remove(id),
    onSuccess: () => {
      message.success('Entry deleted - existing alerts keep the plate for history');
      qc.invalidateQueries({ queryKey: ['watchlist'] });
      setRemove(null);
    },
  });
  const toggle = useMutation({
    mutationFn: (w: WatchlistEntry) => watchlistApi.update(w.id, { is_active: !w.is_active }),
    onSuccess: (w) => {
      message.success(w.is_active ? 'Entry activated' : 'Entry deactivated');
      qc.invalidateQueries({ queryKey: ['watchlist'] });
    },
  });

  const columns: ColumnsType<WatchlistEntry> = [
    { title: 'Plate / name', key: 'plate_norm', sorter: true, width: 340, fixed: 'left', render: (_v, w) => <div><div>{w.plate_norm ? <PlateText plate={w.plate_norm} /> : <ColourTag colour="#7C3AED" label="Person" />}</div><div style={{ fontSize: 12, color: '#4B5563', marginTop: 2 }}>{w.name ?? '—'}</div></div> },
    { title: 'Reason', dataIndex: 'reason', width: 140, render: (v: string) => <ReasonTag reason={v} size="small" /> },
    { title: 'Priority', dataIndex: 'priority', key: 'priority', sorter: true, width: 100, render: (v: string) => <PriorityTag priority={v} size="small" /> },
    { title: 'Source', dataIndex: 'source', width: 90, render: (v: string) => (v === 'egujcop' ? 'eGujCop' : v === 'vahan' ? 'VAHAN' : humanise(v)) },
    {
      title: 'State',
      width: 130,
      render: (_v, w) => {
        const expired = Boolean(w.expires_at && Date.parse(w.expires_at) < Date.now());
        if (!w.is_active) return <ColourTag colour="#6B7280" label="Inactive" size="small" />;
        if (expired) return <ColourTag colour="#D97706" label="Expired" size="small" title="Past its expiry; the matcher ignores it" />;
        return <ColourTag colour="#16A34A" label="Active" size="small" dot />;
      },
    },
    { title: 'Expires', dataIndex: 'expires_at', width: 130, render: (v: string | null) => (v ? <IstTime value={v} /> : <span style={{ color: '#9CA3AF' }}>never</span>) },
    { title: 'Hits', dataIndex: 'hit_count', key: 'hit_count', sorter: true, width: 80, align: 'right', render: (v: number, w) => <Tooltip title={`${w.alerts_24h} alerts in 24 h`}><span style={{ fontWeight: v ? 600 : 400, color: v ? '#DC2626' : '#9CA3AF' }}>{v}</span></Tooltip> },
    { title: 'Last hit', dataIndex: 'last_hit_at', key: 'last_hit_at', sorter: true, width: 130, render: (v: string | null) => <IstTime value={v} /> },
    { title: 'Added', dataIndex: 'created_at', key: 'created_at', sorter: true, width: 150, render: (v: string, w) => <span><IstTime value={v} /><div style={{ fontSize: 11, color: '#6B7280' }}>{w.added_by_username ?? '—'}</div></span> },
    {
      title: '',
      width: canWrite ? 150 : 60,
      render: (_v, w) => (
        <Space size={2} onClick={(e) => e.stopPropagation()}>
          {w.plate_norm ? <Tooltip title="Search sightings"><Button size="small" type="text" icon={<SearchOutlined />} onClick={() => navigate(`/vehicles?q=${w.plate_norm}`)} aria-label="Search" /></Tooltip> : null}
          {canWrite ? (
            <>
              <Tooltip title="Edit"><Button size="small" type="text" icon={<EditOutlined />} onClick={() => setModal({ open: true, entry: w })} aria-label="Edit" /></Tooltip>
              <Tooltip title={w.is_active ? 'Deactivate' : 'Activate'}><Switch size="small" checked={w.is_active} loading={toggle.isPending && toggle.variables?.id === w.id} onChange={() => toggle.mutate(w)} aria-label="Active" /></Tooltip>
              <Tooltip title="Delete"><Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => setRemove(w)} aria-label="Delete" /></Tooltip>
            </>
          ) : null}
        </Space>
      ),
    },
  ];

  const hasFilters = Boolean(debounced || entity || reason || priority || active !== 'true');
  const empty = !list.query.isLoading && list.total === 0;

  return (
    <div>
      <PageHeader
        title="Watchlist"
        description={`Statewide list of vehicles and persons correlated with every accepted read in real time · ${list.total} entr${list.total === 1 ? 'y' : 'ies'}${hasFilters ? ' matching filters' : ''}.`}
        extra={
          canWrite ? (
            <Space wrap>
              <Button icon={<UploadOutlined />} onClick={() => setImportOpen(true)}>
                Import CSV
              </Button>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setModal({ open: true, entry: null })}>
                Add to watchlist
              </Button>
            </Space>
          ) : null
        }
      />
      <Card styles={{ body: { padding: 0 } }}>
        <div className="sg-toolbar" style={{ padding: '12px 16px 0' }}>
          <div className="sg-toolbar-left">
            <Input allowClear prefix={<SearchOutlined style={{ color: '#9CA3AF' }} />} placeholder="Plate or name" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 240 }} aria-label="Search watchlist" />
            <Select allowClear placeholder="Entity" style={{ width: 120 }} options={[{ value: 'vehicle', label: 'Vehicle' }, { value: 'person', label: 'Person' }]} value={entity} onChange={setEntity} aria-label="Entity" />
            <Select allowClear placeholder="Reason" style={{ width: 160 }} options={REASONS.map((r) => ({ value: r, label: humanise(r) }))} value={reason} onChange={setReason} aria-label="Reason" />
            <Select allowClear placeholder="Priority" style={{ width: 120 }} options={PRIORITIES.map((p) => ({ value: p, label: humanise(p) }))} value={priority} onChange={setPriority} aria-label="Priority" />
            <Select style={{ width: 130 }} options={[{ value: 'true', label: 'Active' }, { value: 'false', label: 'Inactive' }, { value: 'all', label: 'All' }]} value={active} onChange={setActive} aria-label="Active filter" />
          </div>
          <div className="sg-toolbar-right">
            <Button icon={<ReloadOutlined />} loading={list.query.isFetching} onClick={() => void list.query.refetch()} aria-label="Refresh" />
          </div>
        </div>
        {list.query.isError ? (
          <div style={{ padding: 16 }}><ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} /></div>
        ) : empty ? (
          <EmptyState title={hasFilters ? 'No entries match these filters' : 'The watchlist is empty'} description={hasFilters ? 'Clear a filter or include inactive entries.' : 'Add a plate manually or import an eGujCop / own CSV. Alerts fire within seconds of the next read.'} actions={canWrite && !hasFilters ? <Button type="primary" icon={<PlusOutlined />} onClick={() => setModal({ open: true, entry: null })}>Add to watchlist</Button> : undefined} />
        ) : (
          <Table<WatchlistEntry> className="sg-table" size="middle" rowKey="id" columns={columns} dataSource={list.items} loading={list.query.isLoading} pagination={list.pagination} onChange={list.onTableChange} sticky scroll={{ x: 1480 }} rowClassName={(w) => (!w.is_effective ? 'sg-row-retired' : '')} onRow={(w) => rowProps(canWrite ? () => setModal({ open: true, entry: w }) : undefined)} />
        )}
      </Card>
      <EntryModal open={modal.open} entry={modal.entry} onClose={() => setModal({ open: false, entry: null })} />
      <ImportModal open={importOpen} onClose={() => setImportOpen(false)} />
      <ConfirmDialog open={remove !== null} title="Delete watchlist entry?" okText="Delete" loading={del.isPending} content={<p><strong>{remove?.plate_display ?? remove?.name}</strong> will be removed. Alerts already raised keep the plate for history. Prefer deactivating if the case may reopen.</p>} onOk={() => { if (remove) del.mutate(remove.id); }} onCancel={() => setRemove(null)} />
    </div>
  );
}
