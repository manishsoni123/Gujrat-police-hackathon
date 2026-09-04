/**
 * Global /ws/alerts subscription: feeds the UI store (bell, stats, live list),
 * raises toasts + sound + desktop notifications, and invalidates alert queries.
 * Returns the Ant notification context holder, which AppLayout renders once.
 */
import { useCallback, useRef, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import { notification, Button, Space } from 'antd';
import type { AlertUpdatePayload, AlertWsPayload, Paginated, WsAlertStats, WsEnvelope } from '@/api/types';
import { useUiStore } from '@/store/ui';
import { useAuthStore } from '@/store/auth';
import { useSocket } from './useSocket';
import { ALERT_PRIORITY } from '@/theme/colours';
import { alertsApi } from '@/api';
import { playAlertSound } from '@/utils/sound';

const MAX_TOASTS = 3;
/** Toasts start below the sticky 56 px header so they never sit on top of page or drawer header actions. */
const TOAST_TOP = 64;

/** True when the alert drawer for this alert id is already open (/alerts?id=<id>): a toast would only duplicate it. */
function alertDrawerOpenFor(pathname: string, id: number): boolean {
  if (!pathname.startsWith('/alerts')) return false;
  return new URLSearchParams(window.location.search).get('id') === String(id);
}

export function useAlertsSocket(): ReactNode {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const locRef = useRef(location.pathname);
  locRef.current = location.pathname;
  const canAck = useAuthStore((s) => s.user?.permissions.includes('alerts.ack') ?? false);
  const authenticated = useAuthStore((s) => s.status === 'authenticated');
  const [toastApi, contextHolder] = notification.useNotification({ maxCount: MAX_TOASTS, placement: 'topRight', top: TOAST_TOP });

  const showToast = useCallback(
    (a: AlertWsPayload) => {
      const colour = ALERT_PRIORITY[a.priority]?.colour ?? '#DC2626';
      const key = `alert-${a.id}`;
      const open = () => {
        toastApi.destroy(key);
        navigate(`/alerts?id=${a.id}`);
      };
      const ack = async () => {
        try {
          await alertsApi.ack(a.id, 'Acknowledged from notification');
          qc.invalidateQueries({ queryKey: ['alerts'] });
        } finally {
          toastApi.destroy(key);
        }
      };
      toastApi.open({
        key,
        message: <span style={{ fontWeight: 600, color: colour }}>{a.notify_title}</span>,
        description: (
          <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
            {a.snapshot_url ? (
              <img
                src={a.snapshot_url}
                alt={`Plate crop for ${a.plate_norm ?? 'alert'}`}
                style={{ width: 64, height: 48, objectFit: 'contain', background: '#111827', borderRadius: 4, border: '1px solid #E5E7EB' }}
              />
            ) : null}
            <div>{a.notify_body}</div>
          </div>
        ),
        duration: a.priority === 'critical' ? 0 : 8,
        style: { borderLeft: `4px solid ${colour}` },
        btn: (
          <Space>
            {canAck ? (
              <Button size="small" onClick={ack}>
                Acknowledge
              </Button>
            ) : null}
            <Button size="small" type="primary" onClick={open}>
              Open
            </Button>
          </Space>
        ),
      });
    },
    [toastApi, navigate, qc, canAck],
  );

  const onMessage = useCallback(
    (env: WsEnvelope) => {
      const ui = useUiStore.getState();
      switch (env.type) {
        case 'alert': {
          const a = env.data as AlertWsPayload;
          const onAlerts = locRef.current.startsWith('/alerts');
          ui.pushLiveAlert(a, !onAlerts);
          if (!alertDrawerOpenFor(locRef.current, a.id)) showToast(a);
          if (a.sound && ui.soundOn) playAlertSound(a.priority === 'critical');
          if (
            ui.desktopNotify &&
            (document.hidden || !document.hasFocus()) &&
            'Notification' in window &&
            Notification.permission === 'granted'
          ) {
            try {
              const n = new Notification(a.notify_title, {
                body: a.notify_body,
                icon: a.snapshot_url ?? '/favicon.svg',
                tag: `alert-${a.id}`,
              });
              n.onclick = () => {
                window.focus();
                navigate(`/alerts?id=${a.id}`);
                n.close();
              };
            } catch {
              /* notifications unsupported */
            }
          }
          qc.invalidateQueries({ queryKey: ['alerts'] });
          qc.invalidateQueries({ queryKey: ['dashboard'] });
          break;
        }
        case 'alert_update': {
          const u = env.data as AlertUpdatePayload;
          qc.setQueriesData<Paginated<AlertWsPayload> | undefined>({ queryKey: ['alerts', 'list'] }, (old) =>
            old ? { ...old, items: old.items.map((it) => (it.id === u.id ? { ...it, ...u } : it)) } : old,
          );
          qc.invalidateQueries({ queryKey: ['alerts'] });
          break;
        }
        case 'stats':
          ui.setAlertStats(env.data as WsAlertStats);
          break;
        default:
          break;
      }
    },
    [qc, showToast, navigate],
  );

  useSocket(authenticated ? '/ws/alerts' : null, {
    onMessage,
    onStatus: (s) => useUiStore.getState().setWsStatus(s),
    onOpen: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  });

  return contextHolder;
}
