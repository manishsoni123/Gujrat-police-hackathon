/**
 * Application shell: collapsible navy sidebar with grouped navigation,
 * top bar (global plate search Ctrl+K, alert bell, WS status, IST clock,
 * user menu with role badge), desktop-notification banner.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Alert, Avatar, Badge, Button, Dropdown, Layout, Menu, Space, Tag, Tooltip, Typography, Modal, Form, Input, message } from 'antd';
import type { MenuProps } from 'antd';
import {
  AlertOutlined,
  ApartmentOutlined,
  AppstoreOutlined,
  AuditOutlined,
  BellOutlined,
  CarOutlined,
  CloudUploadOutlined,
  DashboardOutlined,
  EnvironmentOutlined,
  EyeOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  FlagOutlined,
  HeartOutlined,
  InfoCircleOutlined,
  KeyOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  RadarChartOutlined,
  SearchOutlined,
  SettingOutlined,
  SoundOutlined,
  TeamOutlined,
  UserOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import { Logo, LogoMark } from './Logo';
import { useAuthStore } from '@/store/auth';
import { useUiStore } from '@/store/ui';
import { useAlertsSocket } from '@/ws/useAlertsSocket';
import { useHealthSocket } from '@/ws/useHealthSocket';
import { authApi } from '@/api';
import { nowIst } from '@/utils/time';
import { RoleTag } from './Tags';
import { GlobalSearch } from './GlobalSearch';
import { NotificationBell } from './NotificationBell';
import { usePublicSettings } from '@/hooks/useCamerasOptions';
import type { Permission } from '@/api/types';

const { Sider, Header, Content } = Layout;

interface NavItem {
  key: string;
  label: string;
  icon: React.ReactNode;
  permission?: Permission | Permission[];
  adminOnly?: boolean;
}
interface NavGroup {
  title: string;
  items: NavItem[];
}

const NAV: NavGroup[] = [
  {
    title: 'Overview',
    items: [
      { key: '/dashboard', label: 'Dashboard', icon: <DashboardOutlined /> },
      { key: '/map', label: 'Map', icon: <EnvironmentOutlined /> },
      { key: '/wall', label: 'Live Wall', icon: <AppstoreOutlined /> },
    ],
  },
  {
    title: 'Registry',
    items: [
      { key: '/cameras', label: 'Cameras', icon: <VideoCameraOutlined /> },
      { key: '/cameras/import', label: 'Import', icon: <CloudUploadOutlined />, permission: 'cameras.write' },
      { key: '/health', label: 'Health', icon: <HeartOutlined /> },
      { key: '/gap-analysis', label: 'Gap Analysis', icon: <RadarChartOutlined /> },
    ],
  },
  {
    title: 'Intelligence',
    items: [
      { key: '/detections', label: 'Detections', icon: <EyeOutlined /> },
      { key: '/vehicles', label: 'Vehicle Search', icon: <CarOutlined /> },
      { key: '/watchlist', label: 'Watchlist', icon: <FlagOutlined /> },
      { key: '/alerts', label: 'Alerts', icon: <AlertOutlined /> },
      { key: '/events', label: 'Events', icon: <FileSearchOutlined /> },
    ],
  },
  {
    title: 'Reports',
    items: [{ key: '/reports', label: 'Reports', icon: <FileTextOutlined /> }],
  },
  {
    title: 'Administration',
    items: [
      { key: '/users', label: 'Users', icon: <TeamOutlined />, permission: 'admin.users' },
      { key: '/settings', label: 'Settings', icon: <SettingOutlined />, permission: 'admin.settings' },
      { key: '/audit', label: 'Audit Log', icon: <AuditOutlined />, permission: 'admin.audit' },
      { key: '/about', label: 'About', icon: <InfoCircleOutlined /> },
    ],
  },
];

function IstClock() {
  const [now, setNow] = useState(nowIst());
  useEffect(() => {
    const t = setInterval(() => setNow(nowIst()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <Tooltip title={`${now.format('dddd, DD MMMM YYYY')} · Asia/Kolkata (UTC+05:30)`}>
      <div style={{ fontVariantNumeric: 'tabular-nums', fontSize: 13, color: '#374151', whiteSpace: 'nowrap' }}>
        <span style={{ fontWeight: 600 }}>{now.format('HH:mm:ss')}</span>
        <span style={{ color: '#6B7280', marginLeft: 6 }}>{now.format('DD MMM')} IST</span>
      </div>
    </Tooltip>
  );
}

function WsStatusDot() {
  const status = useUiStore((s) => s.wsStatus);
  const meta = {
    open: { colour: '#16A34A', label: 'Live connection to the alert channel' },
    connecting: { colour: '#D97706', label: 'Connecting to the alert channel…' },
    closed: { colour: '#9CA3AF', label: 'Alert channel disconnected — reconnecting with backoff' },
    error: { colour: '#DC2626', label: 'Alert channel error — reconnecting' },
  }[status];
  return (
    <Tooltip title={meta.label}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#4B5563' }}>
        <span className={status === 'open' ? 'sg-dot sg-dot-pulse' : 'sg-dot'} style={{ background: meta.colour, width: 9, height: 9 }} />
        {status === 'open' ? 'Live' : status === 'connecting' ? 'Connecting' : 'Offline'}
      </span>
    </Tooltip>
  );
}

function ChangePasswordModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [form] = Form.useForm<{ current_password: string; new_password: string; confirm: string }>();
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const v = await form.validateFields();
    setBusy(true);
    try {
      await authApi.changePassword(v.current_password, v.new_password);
      message.success('Password changed');
      form.resetFields();
      onClose();
    } catch (e) {
      message.error(e instanceof Error ? e.message : 'Could not change password');
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} title="Change password" onCancel={onClose} onOk={submit} okButtonProps={{ loading: busy }} destroyOnClose>
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item name="current_password" label="Current password" rules={[{ required: true }]}>
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item
          name="new_password"
          label="New password"
          extra="At least 10 characters with one letter and one digit."
          rules={[
            { required: true },
            { min: 10, message: 'At least 10 characters' },
            { pattern: /^(?=.*[A-Za-z])(?=.*\d).+$/, message: 'Needs a letter and a digit' },
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          name="confirm"
          label="Confirm new password"
          dependencies={['new_password']}
          rules={[
            { required: true },
            ({ getFieldValue }) => ({
              validator: (_r, v) => (v === getFieldValue('new_password') ? Promise.resolve() : Promise.reject(new Error('Passwords do not match'))),
            }),
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const perms = useAuthStore((s) => s.user?.permissions ?? []);
  const logout = useAuthStore((s) => s.logout);
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const setCollapsed = useUiStore((s) => s.setSidebarCollapsed);
  const soundOn = useUiStore((s) => s.soundOn);
  const setSoundOn = useUiStore((s) => s.setSoundOn);
  const unseen = useUiStore((s) => s.unseenAlertIds.length);
  const markSeen = useUiStore((s) => s.markAlertsSeen);
  const setSearchOpen = useUiStore((s) => s.setSearchOpen);
  const desktopNotify = useUiStore((s) => s.desktopNotify);
  const setDesktopNotify = useUiStore((s) => s.setDesktopNotify);
  const bannerDismissed = useUiStore((s) => s.desktopBannerDismissed);
  const dismissBanner = useUiStore((s) => s.dismissDesktopBanner);
  const [pwOpen, setPwOpen] = useState(false);
  const publicSettings = usePublicSettings();

  const toastHolder = useAlertsSocket();
  useHealthSocket();

  useEffect(() => {
    if (location.pathname.startsWith('/alerts')) markSeen();
  }, [location.pathname, markSeen]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setSearchOpen]);

  const menuItems = useMemo<MenuProps['items']>(() => {
    const can = (p?: Permission | Permission[]) => {
      if (!p) return true;
      const list = Array.isArray(p) ? p : [p];
      return list.some((x) => perms.includes(x));
    };
    return NAV.map((g) => ({
      type: 'group' as const,
      key: g.title,
      label: g.title,
      children: g.items
        .filter((it) => can(it.permission))
        .map((it) => ({
          key: it.key,
          icon: it.icon,
          label: (
            <span>
              {it.label}
              {it.key === '/alerts' && unseen > 0 && !location.pathname.startsWith('/alerts') ? <span className="sg-nav-dot" aria-label="New alerts" /> : null}
            </span>
          ),
        })),
    })).filter((g) => g.children.length > 0);
  }, [perms, unseen, location.pathname]);

  const selectedKey = useMemo(() => {
    const p = location.pathname;
    if (p.startsWith('/cameras/import')) return '/cameras/import';
    if (p.startsWith('/cameras')) return '/cameras';
    if (p.startsWith('/vehicles')) return '/vehicles';
    if (p.startsWith('/settings')) return '/settings';
    const first = `/${p.split('/')[1] ?? ''}`;
    return first;
  }, [location.pathname]);

  const doLogout = async () => {
    try {
      await authApi.logout();
    } catch {
      /* ignore */
    }
    logout('manual');
    navigate('/login');
  };

  const enableDesktop = async () => {
    if (!('Notification' in window)) {
      message.warning('This browser does not support desktop notifications');
      return;
    }
    const r = await Notification.requestPermission();
    if (r === 'granted') {
      setDesktopNotify(true);
      message.success('Desktop notifications enabled');
    } else message.info('Desktop notifications were not enabled');
    dismissBanner();
  };

  const showBanner = !bannerDismissed && !desktopNotify && typeof Notification !== 'undefined' && Notification.permission === 'default';

  const userMenu: MenuProps = {
    items: [
      { key: 'me', label: <div style={{ padding: '4px 0' }}><div style={{ fontWeight: 600 }}>{user?.full_name}</div><div style={{ fontSize: 12, color: '#6B7280' }}>{user?.username}{user?.department_name ? ` · ${user.department_name}` : ''}{user?.district ? ` · ${user.district}` : ''}</div></div>, disabled: true },
      { type: 'divider' },
      { key: 'sound', icon: <SoundOutlined />, label: soundOn ? 'Alert sound: on' : 'Alert sound: off', onClick: () => setSoundOn(!soundOn) },
      { key: 'desktop', icon: <BellOutlined />, label: desktopNotify ? 'Desktop notifications: on' : 'Enable desktop notifications', onClick: () => (desktopNotify ? setDesktopNotify(false) : void enableDesktop()) },
      { key: 'password', icon: <KeyOutlined />, label: 'Change password', onClick: () => setPwOpen(true) },
      { key: 'about', icon: <InfoCircleOutlined />, label: 'About Sentinel Gujarat', onClick: () => navigate('/about') },
      { type: 'divider' },
      { key: 'logout', icon: <LogoutOutlined />, label: 'Sign out', danger: true, onClick: () => void doLogout() },
    ],
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {toastHolder}
      <Sider
        className="sg-sider"
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        width={240}
        collapsedWidth={64}
        breakpoint="lg"
        trigger={null}
        style={{ position: 'sticky', top: 0, height: '100vh', overflow: 'hidden' }}
      >
        {/* Flex column: fixed logo, scrollable navigation, collapse bar in normal flow (never covers a menu item). */}
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
          <div style={{ height: 56, flexShrink: 0, display: 'flex', alignItems: 'center', padding: collapsed ? '0 16px' : '0 20px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
            <Link to="/dashboard" aria-label="Sentinel Gujarat home" style={{ display: 'flex', alignItems: 'center' }}>
              {collapsed ? <LogoMark size={30} /> : <Logo size={30} />}
            </Link>
          </div>
          <nav className="sg-sider-scroll" aria-label="Main navigation" style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden' }}>
            <Menu theme="dark" mode="inline" selectedKeys={[selectedKey]} items={menuItems} onClick={({ key }) => navigate(key)} style={{ borderInlineEnd: 0 }} />
          </nav>
          <div style={{ flexShrink: 0, padding: '6px 12px', borderTop: '1px solid rgba(255,255,255,0.08)', background: '#0B1F3A' }}>
            <Button type="text" block icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed(!collapsed)} style={{ color: 'rgba(255,255,255,0.7)', justifyContent: collapsed ? 'center' : 'flex-start' }} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
              {collapsed ? null : 'Collapse'}
            </Button>
          </div>
        </div>
      </Sider>
      <Layout>
        <Header style={{ display: 'flex', alignItems: 'center', gap: 16, borderBottom: '1px solid #E5E7EB', position: 'sticky', top: 0, zIndex: 900, lineHeight: 'normal' }}>
          <Button onClick={() => setSearchOpen(true)} icon={<SearchOutlined />} style={{ minWidth: 300, justifyContent: 'flex-start', color: '#6B7280', background: '#F3F4F6', borderColor: 'transparent' }} aria-label="Search plate or camera (Ctrl+K)">
            <span style={{ flex: 1, textAlign: 'left' }}>Search plate, camera, alert…</span>
            <kbd className="sg-kbd">Ctrl K</kbd>
          </Button>
          {publicSettings.data?.mock_sandbox ? (
            <Tooltip title="The catalogue import points at the built-in mock sandbox (MOCK_SANDBOX=1)">
              <Tag color="orange" style={{ margin: 0, fontWeight: 600 }}>
                MOCK SANDBOX
              </Tag>
            </Tooltip>
          ) : null}
          <div style={{ flex: 1 }} />
          <Space size={18} align="center">
            <WsStatusDot />
            <IstClock />
            <Tooltip title={soundOn ? 'Alert sound on' : 'Alert sound off'}>
              <Button type="text" icon={<SoundOutlined style={{ color: soundOn ? '#1E4DB7' : '#9CA3AF' }} />} onClick={() => setSoundOn(!soundOn)} aria-label="Toggle alert sound" />
            </Tooltip>
            <NotificationBell />
            <Dropdown menu={userMenu} trigger={['click']} placement="bottomRight">
              <Button type="text" style={{ height: 40, paddingInline: 6 }} aria-label="User menu">
                <Space size={8}>
                  <Badge dot={false}>
                    <Avatar size={30} style={{ background: '#1E4DB7' }} icon={<UserOutlined />} />
                  </Badge>
                  <div style={{ textAlign: 'left', lineHeight: 1.1 }}>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>{user?.full_name ?? user?.username}</div>
                    <div style={{ marginTop: 2 }}>{user ? <RoleTag role={user.role} /> : null}</div>
                  </div>
                </Space>
              </Button>
            </Dropdown>
          </Space>
        </Header>
        <Content className="sg-content">
          {showBanner ? (
            <Alert
              type="info"
              showIcon
              icon={<BellOutlined />}
              closable
              onClose={dismissBanner}
              style={{ marginBottom: 12 }}
              message="Enable desktop notifications for alerts"
              description="Watchlist hits and camera-offline alerts will be shown as OS notifications even when this tab is in the background."
              action={
                <Button size="small" type="primary" onClick={() => void enableDesktop()}>
                  Enable
                </Button>
              }
            />
          ) : null}
          <Outlet />
        </Content>
        <div style={{ padding: '8px 24px', fontSize: 12, color: '#9CA3AF', display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <span>
            <ApartmentOutlined /> Sentinel Gujarat 1.0.0-phase1 · Dynatech Consultancy
          </span>
          <span>All times shown in IST (Asia/Kolkata)</span>
        </div>
      </Layout>
      <GlobalSearch />
      <ChangePasswordModal open={pwOpen} onClose={() => setPwOpen(false)} />
      <Typography.Text style={{ display: 'none' }}>{user?.username}</Typography.Text>
    </Layout>
  );
}
