/**
 * Boots the session (token → /auth/me), and provides route guards:
 * RequireAuth (redirects to /login), RequirePermission (403 page).
 */
import { useEffect, useState, type ReactNode } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { Spin } from 'antd';
import { authApi } from '@/api';
import { ApiError } from '@/api/client';
import { useAuthStore } from '@/store/auth';
import type { Permission } from '@/api/types';
import { ForbiddenPage } from '@/pages/misc/ForbiddenPage';

export function AuthBootstrap({ children }: { children: ReactNode }) {
  const token = useAuthStore((s) => s.token);
  const status = useAuthStore((s) => s.status);
  const setUser = useAuthStore((s) => s.setUser);
  const logout = useAuthStore((s) => s.logout);
  const [retries, setRetries] = useState(0);

  useEffect(() => {
    if (!token || status !== 'loading') return undefined;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    const run = () => {
      authApi
        .me()
        .then((u) => {
          if (!cancelled) setUser(u);
        })
        .catch((e: unknown) => {
          if (cancelled) return;
          // Only a definitive rejection of the token ends the session. A network blip, an aborted
          // request (navigation while the API is slow) or a 5xx keeps the stored token and retries;
          // discarding it here used to log the user out on every transient failure.
          if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
            logout('expired');
            return;
          }
          attempt += 1;
          setRetries(attempt);
          timer = setTimeout(run, Math.min(8000, 1000 * attempt));
        });
    };
    run();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [token, status, setUser, logout]);

  if (status === 'loading') {
    return (
      <div style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#F5F7FA' }}>
        <Spin size="large" tip={retries ? `Restoring session… API not reachable, retrying (${retries})` : 'Restoring session…'}>
          <div style={{ width: 200, height: 40 }} />
        </Spin>
      </div>
    );
  }
  return <>{children}</>;
}

export function RequireAuth() {
  const status = useAuthStore((s) => s.status);
  const location = useLocation();
  if (status !== 'authenticated') return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <Outlet />;
}

export function RequirePermission({ permission, children }: { permission: Permission | Permission[]; children: ReactNode }) {
  const perms = useAuthStore((s) => s.user?.permissions ?? []);
  const needed = Array.isArray(permission) ? permission : [permission];
  const ok = needed.some((p) => perms.includes(p));
  if (!ok) return <ForbiddenPage />;
  return <>{children}</>;
}
