/** /ws/reads/{camera_id} subscription for the camera page (live reads, sightings, counts, snapshots, events). */
import { useCallback, useState } from 'react';
import type { Detection, EventItem, Sighting, WsEnvelope, WsObjectCounts, WsSnapshot } from '@/api/types';
import { useAuthStore } from '@/store/auth';
import { useSocket } from './useSocket';

export interface ReadsSocketState {
  reads: Detection[];
  sightings: Sighting[];
  counts: WsObjectCounts | null;
  snapshot: WsSnapshot | null;
  events: EventItem[];
  connected: boolean;
}

const MAX_READS = 50;

export function useReadsSocket(cameraId: number | null, enabled = true): ReadsSocketState {
  const authenticated = useAuthStore((s) => s.status === 'authenticated');
  const [state, setState] = useState<ReadsSocketState>({
    reads: [],
    sightings: [],
    counts: null,
    snapshot: null,
    events: [],
    connected: false,
  });

  const onMessage = useCallback((env: WsEnvelope) => {
    switch (env.type) {
      case 'read':
        setState((s) => ({ ...s, reads: [env.data as Detection, ...s.reads].slice(0, MAX_READS) }));
        break;
      case 'sighting': {
        const sg = env.data as Sighting;
        setState((s) => ({ ...s, sightings: [sg, ...s.sightings.filter((x) => x.id !== sg.id)].slice(0, 20) }));
        break;
      }
      case 'object_counts':
        setState((s) => ({ ...s, counts: env.data as WsObjectCounts }));
        break;
      case 'snapshot':
        setState((s) => ({ ...s, snapshot: env.data as WsSnapshot }));
        break;
      case 'event':
        setState((s) => ({ ...s, events: [env.data as EventItem, ...s.events].slice(0, 20) }));
        break;
      default:
        break;
    }
  }, []);

  useSocket(authenticated && enabled && cameraId ? `/ws/reads/${cameraId}` : null, {
    onMessage,
    onStatus: (st) => setState((s) => ({ ...s, connected: st === 'open' })),
  });

  return state;
}
