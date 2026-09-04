/** Split-layout login: brand panel with value proposition + jury role hint, form on the right. */
import { useEffect, useState } from 'react';
import { Alert, Button, Form, Input, Typography } from 'antd';
import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { Logo } from '@/components/Logo';
import { authApi } from '@/api';
import { errorMessage } from '@/api/client';
import { useAuthStore } from '@/store/auth';
import { unlockAudio } from '@/utils/sound';

const FEATURES = [
  { title: 'Unified registry', text: 'Every camera from 26 departments on one GIS map, onboarded from a catalogue, CSV, API or form.' },
  { title: 'Live and recorded viewing', text: 'WebRTC with HLS fallback through a single relay - departmental systems remain untouched.' },
  { title: 'ANPR, watchlist, route', text: 'Plates read in real time, correlated with the watchlist, and traced across cameras with evidence hashes.' },
];

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const status = useAuthStore((s) => s.status);
  const reason = useAuthStore((s) => s.logoutReason);
  const setSession = useAuthStore((s) => s.setSession);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.title = 'Sign in · Sentinel Gujarat';
  }, []);

  if (status === 'authenticated') {
    const from = (location.state as { from?: string } | null)?.from;
    return <Navigate to={from && from !== '/login' ? from : '/dashboard'} replace />;
  }

  const submit = async (v: { username: string; password: string }) => {
    unlockAudio();
    setBusy(true);
    setError(null);
    try {
      const res = await authApi.login(v.username.trim(), v.password);
      useAuthStore.setState({ token: res.access_token });
      const me = await authApi.me();
      setSession(res.access_token, me);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from && from !== '/login' ? from : '/dashboard', { replace: true });
    } catch (e) {
      useAuthStore.setState({ token: null });
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', display: 'grid', gridTemplateColumns: 'minmax(0, 1.1fr) minmax(420px, 0.9fr)' }} className="sg-login">
      <div className="sg-login-brand" style={{ padding: '48px 56px', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
        <Logo size={44} />
        <div style={{ maxWidth: 560 }}>
          <div style={{ fontSize: 13, letterSpacing: 2, textTransform: 'uppercase', opacity: 0.7, marginBottom: 14 }}>Gujarat Police · SCRB · CCTV Integration</div>
          <Typography.Title style={{ color: '#fff', fontSize: 38, lineHeight: 1.15, margin: 0, fontWeight: 700 }}>
            Unified CCTV registry, viewing and analytics for Gujarat Police
          </Typography.Title>
          <Typography.Paragraph style={{ color: 'rgba(255,255,255,0.78)', fontSize: 16, marginTop: 16 }}>
            Register every camera on a map, watch any of them live, read number plates automatically, get alerted when a watchlisted
            vehicle appears, and trace where it went.
          </Typography.Paragraph>
          <div style={{ display: 'grid', gap: 14, marginTop: 28 }}>
            {FEATURES.map((f) => (
              <div key={f.title} style={{ display: 'flex', gap: 12 }}>
                <div style={{ width: 8, height: 8, borderRadius: 4, background: '#60A5FA', marginTop: 8, flexShrink: 0 }} />
                <div>
                  <div style={{ fontWeight: 600 }}>{f.title}</div>
                  <div style={{ color: 'rgba(255,255,255,0.7)', fontSize: 13 }}>{f.text}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.55)' }}>
          Sentinel Gujarat 1.0.0-phase1 · Dynatech Consultancy · Open-source platform (Model 1 + Model 2)
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 32, background: '#F5F7FA' }}>
        <div style={{ width: '100%', maxWidth: 400, background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, padding: '36px 36px 28px', boxShadow: '0 4px 24px rgba(16,24,40,0.06)' }}>
          <Typography.Title level={3} style={{ marginTop: 0, marginBottom: 4 }}>
            Sign in
          </Typography.Title>
          <Typography.Text type="secondary">Use the credentials issued for your role.</Typography.Text>
          {reason === 'expired' ? <Alert type="warning" showIcon message="Your session expired. Please sign in again." style={{ marginTop: 16 }} /> : null}
          {error ? <Alert type="error" showIcon message={error} style={{ marginTop: 16 }} /> : null}
          <Form layout="vertical" onFinish={submit} style={{ marginTop: 20 }} requiredMark={false} size="large">
            <Form.Item name="username" label="Username" rules={[{ required: true, message: 'Enter your username' }]}>
              <Input prefix={<UserOutlined style={{ color: '#9CA3AF' }} />} autoComplete="username" autoFocus placeholder="jury_admin" />
            </Form.Item>
            <Form.Item name="password" label="Password" rules={[{ required: true, message: 'Enter your password' }]}>
              <Input.Password prefix={<LockOutlined style={{ color: '#9CA3AF' }} />} autoComplete="current-password" placeholder="••••••••••" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block loading={busy} style={{ marginTop: 4 }}>
              Sign in
            </Button>
          </Form>
          <div style={{ marginTop: 22, padding: '12px 14px', background: '#F8FAFC', border: '1px solid #E5E7EB', borderRadius: 8, fontSize: 12, color: '#4B5563' }}>
            <div style={{ fontWeight: 600, marginBottom: 4, color: '#111827' }}>Jury accounts</div>
            <div>
              <code>jury_admin</code> - full access · <code>jury_operator</code> - watchlist, alerts, routes, reports · <code>jury_viewer</code> - read-only
            </div>
            <div style={{ marginTop: 4 }}>Passwords are supplied in the submission form. No self-registration.</div>
          </div>
        </div>
      </div>
    </div>
  );
}
