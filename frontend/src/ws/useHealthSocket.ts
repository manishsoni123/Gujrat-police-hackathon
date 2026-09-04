/** /ws/health subscription: health stats tiles, status changes, ANPR worker heartbeats. */
import { useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { WsAnprStatus, WsEnvelope, WsHealthChange, WsHealthStats } from '@/api/types';
import { useUiStore } from '@/store/ui';
import { useAuthStore } from '@/store/auth';
import { useSocket } from './useSocket';

export interface HealthSocketHandlers {
  onHealth?: (change: WsHealthChange) => void;
  onAnprStatus?: (status: WsAnprStatus) => void;
  enabled?: boolean;
}

export function useHealthSocket(handlers: HealthSocketHandlers = {}): void {
  const qc = useQueryClient();
  const authenticated = useAuthStore((s) => s.status === 'authenticated');
  const { onHealth, onAnprStatus, enabled = true } = handlers;

  const onMessage = useCallback(
    (env: WsEnvelope) => {
      switch (env.type) {
        case 'stats':
          useUiStore.getState().setHealthStats(env.data as WsHealthStats);
          break;
        case 'health': {
          const change = env.data as WsHealthChange;
          onHealth?.(change);
          qc.invalidateQueries({ queryKey: ['cameras'] });
          qc.invalidateQueries({ queryKey: ['health'] });
          qc.invalidateQueries({ queryKey: ['geo'] });
          break;
        }
        case 'anpr_status':
          onAnprStatus?.(env.data as WsAnprStatus);
          break;
        default:
          break;
      }
    },
    [qc, onHealth, onAnprStatus],
  );

  useSocket(authenticated && enabled ? '/ws/health' : null, { onMessage });
}
