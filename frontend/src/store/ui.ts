/** UI state: sidebar, sound / desktop-notification preferences, live alert feed, WS status. */
import { create } from 'zustand';
import type { AlertWsPayload, WsAlertStats, WsHealthStats } from '@/api/types';

function readBool(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v === '1';
  } catch {
    return fallback;
  }
}
function writeBool(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, value ? '1' : '0');
  } catch {
    /* ignore */
  }
}

export type WsStatus = 'connecting' | 'open' | 'closed' | 'error';

interface UiState {
  sidebarCollapsed: boolean;
  soundOn: boolean;
  desktopNotify: boolean;
  desktopBannerDismissed: boolean;
  searchOpen: boolean;
  wsStatus: WsStatus;
  alertStats: WsAlertStats | null;
  healthStats: WsHealthStats | null;
  /** Alerts received live in this session (newest first, max 50). */
  liveAlerts: AlertWsPayload[];
  /** Ids of alerts that arrived while the user was not on /alerts (drives the sidebar dot). */
  unseenAlertIds: number[];
  lastAlertAt: number | null;
  toggleSidebar: () => void;
  setSidebarCollapsed: (v: boolean) => void;
  setSoundOn: (v: boolean) => void;
  setDesktopNotify: (v: boolean) => void;
  dismissDesktopBanner: () => void;
  setSearchOpen: (v: boolean) => void;
  setWsStatus: (s: WsStatus) => void;
  setAlertStats: (s: WsAlertStats) => void;
  setHealthStats: (s: WsHealthStats) => void;
  pushLiveAlert: (a: AlertWsPayload, markUnseen: boolean) => void;
  markAlertsSeen: () => void;
}

export const useUiStore = create<UiState>()((set) => ({
  sidebarCollapsed: readBool('sg.sidebar_collapsed', false),
  soundOn: readBool('sg.sound', true),
  desktopNotify: readBool('sg.desktop_notify', false),
  desktopBannerDismissed: readBool('sg.desktop_banner_dismissed', false),
  searchOpen: false,
  wsStatus: 'closed',
  alertStats: null,
  healthStats: null,
  liveAlerts: [],
  unseenAlertIds: [],
  lastAlertAt: null,
  toggleSidebar: () =>
    set((s) => {
      writeBool('sg.sidebar_collapsed', !s.sidebarCollapsed);
      return { sidebarCollapsed: !s.sidebarCollapsed };
    }),
  setSidebarCollapsed: (v) => {
    writeBool('sg.sidebar_collapsed', v);
    set({ sidebarCollapsed: v });
  },
  setSoundOn: (v) => {
    writeBool('sg.sound', v);
    set({ soundOn: v });
  },
  setDesktopNotify: (v) => {
    writeBool('sg.desktop_notify', v);
    set({ desktopNotify: v });
  },
  dismissDesktopBanner: () => {
    writeBool('sg.desktop_banner_dismissed', true);
    set({ desktopBannerDismissed: true });
  },
  setSearchOpen: (v) => set({ searchOpen: v }),
  setWsStatus: (wsStatus) => set({ wsStatus }),
  setAlertStats: (alertStats) => set({ alertStats }),
  setHealthStats: (healthStats) => set({ healthStats }),
  pushLiveAlert: (a, markUnseen) =>
    set((s) => ({
      liveAlerts: [a, ...s.liveAlerts.filter((x) => x.id !== a.id)].slice(0, 50),
      unseenAlertIds: markUnseen ? [a.id, ...s.unseenAlertIds.filter((id) => id !== a.id)] : s.unseenAlertIds,
      lastAlertAt: Date.now(),
    })),
  markAlertsSeen: () => set({ unseenAlertIds: [] }),
}));
