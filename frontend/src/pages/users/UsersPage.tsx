/** Users (/users, admin): list, create, edit role/department/district, reset password, deactivate. */
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button, Card, Form, Input, Modal, Select, Space, Table, Tag, Tooltip, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { EditOutlined, KeyOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, StopOutlined } from '@ant-design/icons';
import { PageHeader } from '@/components/PageHeader';
import { useListQuery } from '@/hooks/useListQuery';
import { usersApi } from '@/api';
import { ApiError } from '@/api/client';
import type { Role, UserInput, UserRow } from '@/api/types';
import { RoleTag } from '@/components/Tags';
import { IstTime } from '@/components/IstTime';
import { EmptyState, ErrorState } from '@/components/States';
import { ConfirmDialog } from '@/components/Dialogs';
import { useDepartments, useDistricts } from '@/hooks/useCamerasOptions';
import { useCurrentUser } from '@/store/auth';

const ROLES: { value: Role; label: string; hint: string }[] = [
  { value: 'admin', label: 'Administrator', hint: 'Everything, statewide' },
  { value: 'dept_admin', label: 'Department admin', hint: 'Camera CRUD and analytics scoped to one department (and district)' },
  { value: 'operator', label: 'Operator', hint: 'Watchlist, alerts, routes, reports; no camera edits' },
  { value: 'viewer', label: 'Viewer', hint: 'Read-only' },
];
const PASSWORD_RULES = [{ required: true }, { min: 10, message: 'At least 10 characters' }, { pattern: /^(?=.*[A-Za-z])(?=.*\d).+$/, message: 'Needs a letter and a digit' }];

function UserModal({ open, user, onClose }: { open: boolean; user: UserRow | null; onClose: () => void }) {
  const qc = useQueryClient();
  const { options: deptOptions } = useDepartments();
  const districts = useDistricts();
  const [form] = Form.useForm<UserInput & { is_active?: boolean }>();
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const role = Form.useWatch('role', form);
  useEffect(() => {
    if (!open) return;
    setFieldErrors({});
    if (user) form.setFieldsValue({ username: user.username, full_name: user.full_name, role: user.role, department_id: user.department_id, district: user.district, is_active: user.is_active });
    else {
      form.resetFields();
      form.setFieldsValue({ role: 'viewer' });
    }
  }, [open, user, form]);
  const save = useMutation({
    mutationFn: (v: UserInput & { is_active?: boolean }) => {
      if (user) {
        const { username: _u, password: _p, ...patch } = v;
        return usersApi.update(user.id, patch);
      }
      return usersApi.create(v);
    },
    onSuccess: (u) => {
      message.success(user ? 'User updated' : `User ${u.username} created`);
      qc.invalidateQueries({ queryKey: ['users'] });
      onClose();
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        const m: Record<string, string> = {};
        e.errors.forEach((x) => {
          if (x.field) m[x.field] = x.message;
        });
        if (e.status === 409) m.username = e.detail;
        setFieldErrors(m);
      }
    },
  });
  const fe = (n: string) => ({ validateStatus: fieldErrors[n] ? ('error' as const) : undefined, help: fieldErrors[n] });
  return (
    <Modal open={open} onCancel={onClose} title={user ? `Edit ${user.username}` : 'Create user'} okText={user ? 'Save' : 'Create'} okButtonProps={{ loading: save.isPending }} onOk={async () => save.mutate(await form.validateFields())} destroyOnClose>
      <Form form={form} layout="vertical" requiredMark="optional">
        <Form.Item name="username" label="Username" rules={[{ required: true, max: 64 }, { pattern: /^[a-z0-9_.-]+$/i, message: 'letters, digits, _ . -' }]} extra={user ? 'Usernames cannot be changed' : 'Stored lower-case'} {...fe('username')}>
          <Input disabled={Boolean(user)} autoFocus={!user} autoComplete="off" />
        </Form.Item>
        <Form.Item name="full_name" label="Full name" rules={[{ required: true, max: 120 }]} {...fe('full_name')}>
          <Input />
        </Form.Item>
        {!user ? (
          <Form.Item name="password" label="Initial password" rules={PASSWORD_RULES} extra="At least 10 characters with one letter and one digit." {...fe('password')}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        ) : null}
        <Form.Item name="role" label="Role" rules={[{ required: true }]} {...fe('role')}>
          <Select options={ROLES.map((r) => ({ value: r.value, label: <span>{r.label} <span style={{ color: '#9CA3AF', fontSize: 12 }}>· {r.hint}</span></span> }))} />
        </Form.Item>
        <Space style={{ display: 'flex' }} align="start">
          <Form.Item name="department_id" label="Department" rules={role === 'dept_admin' ? [{ required: true, message: 'Department admins need a department' }] : []} style={{ width: 260 }} extra={role !== 'dept_admin' ? 'Informational for non-scoped roles' : 'Scopes every camera-derived view'} {...fe('department_id')}>
            <Select allowClear showSearch optionFilterProp="label" options={deptOptions} />
          </Form.Item>
          <Form.Item name="district" label="District (optional scope)" style={{ width: 200 }} {...fe('district')}>
            <Select allowClear showSearch options={districts.map((d) => ({ value: d, label: d }))} disabled={role !== 'dept_admin'} />
          </Form.Item>
        </Space>
        {user ? (
          <Form.Item name="is_active" label="Active">
            <Select options={[{ value: true, label: 'Active' }, { value: false, label: 'Inactive (cannot sign in)' }]} />
          </Form.Item>
        ) : null}
      </Form>
    </Modal>
  );
}

function ResetPasswordModal({ user, onClose }: { user: UserRow | null; onClose: () => void }) {
  const [form] = Form.useForm<{ password: string; confirm: string }>();
  const m = useMutation({
    mutationFn: (v: { password: string }) => usersApi.resetPassword(user?.id as number, v.password),
    onSuccess: () => {
      message.success(`Password reset for ${user?.username}`);
      form.resetFields();
      onClose();
    },
  });
  return (
    <Modal open={user !== null} onCancel={onClose} title={`Reset password · ${user?.username}`} okText="Reset password" okButtonProps={{ loading: m.isPending }} onOk={async () => m.mutate(await form.validateFields())} destroyOnClose>
      <Form form={form} layout="vertical">
        <Form.Item name="password" label="New password" rules={PASSWORD_RULES}>
          <Input.Password autoComplete="new-password" autoFocus />
        </Form.Item>
        <Form.Item name="confirm" label="Confirm" dependencies={['password']} rules={[{ required: true }, ({ getFieldValue }) => ({ validator: (_r, v) => (v === getFieldValue('password') ? Promise.resolve() : Promise.reject(new Error('Passwords do not match'))) })]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export function UsersPage() {
  const qc = useQueryClient();
  const me = useCurrentUser();
  const [q, setQ] = useState('');
  const [debounced, setDebounced] = useState('');
  const [role, setRole] = useState<Role | undefined>();
  const [activeFilter, setActiveFilter] = useState<'true' | 'false' | undefined>();
  const [modal, setModal] = useState<{ open: boolean; user: UserRow | null }>({ open: false, user: null });
  const [reset, setReset] = useState<UserRow | null>(null);
  const [deactivate, setDeactivate] = useState<UserRow | null>(null);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 300);
    return () => clearTimeout(t);
  }, [q]);
  const filters = useMemo(() => ({ q: debounced || undefined, role, is_active: activeFilter }), [debounced, role, activeFilter]);
  const list = useListQuery<UserRow>({ key: ['users', 'list'], fetcher: usersApi.list, filters, defaultSort: 'username', defaultOrder: 'asc' });
  const deact = useMutation({
    mutationFn: (id: number) => usersApi.deactivate(id),
    onSuccess: () => {
      message.success('User deactivated');
      qc.invalidateQueries({ queryKey: ['users'] });
      setDeactivate(null);
    },
  });

  const columns: ColumnsType<UserRow> = [
    { title: 'Username', dataIndex: 'username', key: 'username', sorter: true, width: 190, render: (v: string, u) => <span><strong>{v}</strong>{u.id === me?.id ? <Tag style={{ marginLeft: 6 }}>you</Tag> : null}{!u.is_active ? <Tag style={{ marginLeft: 6 }}>inactive</Tag> : null}</span> },
    { title: 'Full name', dataIndex: 'full_name', ellipsis: true },
    { title: 'Role', dataIndex: 'role', key: 'role', sorter: true, width: 140, render: (v: string) => <RoleTag role={v} size="default" /> },
    { title: 'Scope', width: 260, render: (_v, u) => (u.department_name ? <span>{u.department_name}{u.district ? ` · ${u.district}` : ''}{u.role !== 'dept_admin' ? <span style={{ color: '#9CA3AF' }}> (informational)</span> : null}</span> : <span style={{ color: '#9CA3AF' }}>statewide</span>) },
    { title: 'Last login', dataIndex: 'last_login_at', key: 'last_login_at', sorter: true, width: 150, render: (v: string | null) => <IstTime value={v} /> },
    { title: 'Created', dataIndex: 'created_at', key: 'created_at', sorter: true, width: 150, render: (v: string) => <IstTime value={v} /> },
    {
      title: '',
      width: 150,
      render: (_v, u) => (
        <Space size={2} onClick={(e) => e.stopPropagation()}>
          <Tooltip title="Edit"><Button size="small" type="text" icon={<EditOutlined />} onClick={() => setModal({ open: true, user: u })} aria-label="Edit" /></Tooltip>
          <Tooltip title="Reset password"><Button size="small" type="text" icon={<KeyOutlined />} onClick={() => setReset(u)} aria-label="Reset password" /></Tooltip>
          <Tooltip title={u.id === me?.id ? 'You cannot deactivate yourself' : 'Deactivate'}><Button size="small" type="text" danger icon={<StopOutlined />} disabled={u.id === me?.id || !u.is_active} onClick={() => setDeactivate(u)} aria-label="Deactivate" /></Tooltip>
        </Space>
      ),
    },
  ];
  const empty = !list.query.isLoading && list.total === 0;

  return (
    <div>
      <PageHeader
        title="Users"
        description="Accounts and roles. Department admins are scoped in SQL to their department (and district); operators and viewers are statewide. The four jury accounts are seeded."
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModal({ open: true, user: null })}>
            Create user
          </Button>
        }
      />
      <Card styles={{ body: { padding: 0 } }}>
        <div className="sg-toolbar" style={{ padding: '12px 16px 0' }}>
          <div className="sg-toolbar-left">
            <Input allowClear prefix={<SearchOutlined style={{ color: '#9CA3AF' }} />} placeholder="Username or name" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 240 }} aria-label="Search users" />
            <Select allowClear placeholder="Role" style={{ width: 170 }} options={ROLES.map((r) => ({ value: r.value, label: r.label }))} value={role} onChange={setRole} aria-label="Role" />
            <Select allowClear placeholder="Active" style={{ width: 130 }} options={[{ value: 'true', label: 'Active' }, { value: 'false', label: 'Inactive' }]} value={activeFilter} onChange={setActiveFilter} aria-label="Active" />
          </div>
          <div className="sg-toolbar-right">
            <Button icon={<ReloadOutlined />} loading={list.query.isFetching} onClick={() => void list.query.refetch()} aria-label="Refresh" />
          </div>
        </div>
        {list.query.isError ? (
          <div style={{ padding: 16 }}><ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} /></div>
        ) : empty ? (
          <EmptyState title="No users match" description="Clear the filters or create a user." />
        ) : (
          <Table<UserRow> className="sg-table" size="middle" rowKey="id" columns={columns} dataSource={list.items} loading={list.query.isLoading} pagination={list.pagination} onChange={list.onTableChange} rowClassName={(u) => (!u.is_active ? 'sg-row-retired' : '')} onRow={(u) => ({ onClick: () => setModal({ open: true, user: u }) })} />
        )}
      </Card>
      <UserModal open={modal.open} user={modal.user} onClose={() => setModal({ open: false, user: null })} />
      <ResetPasswordModal user={reset} onClose={() => setReset(null)} />
      <ConfirmDialog open={deactivate !== null} title="Deactivate user?" okText="Deactivate" loading={deact.isPending} content={<p><strong>{deactivate?.username}</strong> will no longer be able to sign in; active sessions are rejected on their next request. The audit history is kept.</p>} onOk={() => { if (deactivate) deact.mutate(deactivate.id); }} onCancel={() => setDeactivate(null)} />
    </div>
  );
}
