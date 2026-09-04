/**
 * Auth state (zustand). Token persisted under localStorage `sg.token`
 * (CONTRACT §2.1). The user object with permissions comes from /auth/me.
 */
import { create } from 'zustand';
import type { Me, Permission, Role } from '@/api/types';

const TOKEN_KEY = 'sg.token';

function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function writeToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable */
  }
}

export type AuthStatus = 'idle' | 'loading' | 'authenticated' | 'anonymous';

interface AuthState {
  token: string | null;
  user: Me | null;
  status: AuthStatus;
  /** Why the last logout happened (shown on the login page). */
  logoutReason: 'expired' | 'manual' | null;
  setSession: (token: string, user: Me) => void;
  setUser: (user: Me) => void;
  setStatus: (status: AuthStatus) => void;
  logout: (reason?: 'expired' | 'manual') => void;
  hasPermission: (p: Permission) => boolean;
  hasRole: (...roles: Role[]) => boolean;
}

export const useAuthStore = create<AuthState>()((set, get) => ({
  token: readToken(),
  user: null,
  status: readToken() ? 'loading' : 'anonymous',
  logoutReason: null,
  setSession: (token, user) => {
    writeToken(token);
    set({ token, user, status: 'authenticated', logoutReason: null });
  },
  setUser: (user) => set({ user, status: 'authenticated' }),
  setStatus: (status) => set({ status }),
  logout: (reason = 'manual') => {
    if (get().status === 'anonymous' && !get().token) return;
    writeToken(null);
    set({ token: null, user: null, status: 'anonymous', logoutReason: reason });
  },
  hasPermission: (p) => get().user?.permissions.includes(p) ?? false,
  hasRole: (...roles) => {
    const r = get().user?.role;
    return r ? roles.includes(r) : false;
  },
}));

export function useCurrentUser(): Me | null {
  return useAuthStore((s) => s.user);
}

export function usePermission(p: Permission): boolean {
  return useAuthStore((s) => s.user?.permissions.includes(p) ?? false);
}
