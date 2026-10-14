/** /ws/reads/{camera_id} subscription for the camera page (live reads, sightings, counts, snapshots, events, overlay boxes). */
import { useCallback, useState } from 'react';
import type { Detection, EventItem, Sighting, WsEnvelope, WsObjectCounts, WsOverlay, WsSnapshot } from '@/api/types';
import { useAuthStore } from '@/store/auth';
import { publishOverlay } from './overlayBus';
import { useSocket } from './useSocket';

export interface ReadsSocketState {
  reads: Detection[];
  sightings: Sighting[];
  counts: WsObjectCounts | null;
  snapshot: WsSnapshot | null;
  events: EventItem[];
  connected: boolean;
}

/** The state is owned by one camera: a different `cameraId` starts from `emptyState`. */
interface CameraReadsState extends ReadsSocketState {
  cameraId: number | null;
}

const MAX_READS = 50;

const emptyState = (cameraId: number | null): CameraReadsState => ({
  cameraId,
  reads: [],
  sightings: [],
  counts: null,
  snapshot: null,
  events: [],
  connected: false,
});

/**
 * Camera an envelope is about: `camera_id` (object_counts, snapshot, overlay, event) or `camera.id`
 * (read, sighting); null when the payload names none.
 */
function envelopeCameraId(data: unknown): number | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as { camera_id?: unknown; camera?: { id?: unknown } | null };
  if (typeof d.camera_id === 'number') return d.camera_id;
  if (d.camera && typeof d.camera.id === 'number') return d.camera.id;
  return null;
}

export function useReadsSocket(cameraId: number | null, enabled = true): ReadsSocketState {
  const authenticated = useAuthStore((s) => s.status === 'authenticated');
  const [stored, setState] = useState<CameraReadsState>(() => emptyState(cameraId));
  // A caller can stay mounted while `cameraId` changes (a route element reused across /cameras/:id), and
  // the new camera must not inherit the previous one's reads, snapshot or counts: its socket may never
  // send replacements (no ANPR worker). Reset while rendering, so stale values are never painted.
  let state = stored;
  if (stored.cameraId !== cameraId) {
    state = emptyState(cameraId);
    setState(state);
  }

  const onMessage = useCallback(
    (env: WsEnvelope) => {
      const about = envelopeCameraId(env.data);
      if (about !== null && about !== cameraId) return; // another camera's frame
      const update = (fn: (s: CameraReadsState) => CameraReadsState) =>
        setState((s) => (s.cameraId === cameraId ? fn(s) : s));
      switch (env.type) {
        case 'read':
          update((s) => ({ ...s, reads: [env.data as Detection, ...s.reads].slice(0, MAX_READS) }));
          break;
        case 'sighting': {
          const sg = env.data as Sighting;
          update((s) => ({ ...s, sightings: [sg, ...s.sightings.filter((x) => x.id !== sg.id)].slice(0, 20) }));
          break;
        }
        case 'object_counts':
          update((s) => ({ ...s, counts: env.data as WsObjectCounts }));
          break;
        case 'snapshot':
          update((s) => ({ ...s, snapshot: env.data as WsSnapshot }));
          break;
        case 'event':
          update((s) => ({ ...s, events: [env.data as EventItem, ...s.events].slice(0, 20) }));
          break;
        case 'overlay':
          publishOverlay(env.data as WsOverlay); // straight to the canvas, no re-render
          break;
        default:
          break;
      }
    },
    [cameraId],
  );

  const path = authenticated && enabled && cameraId ? `/ws/reads/${cameraId}` : null;
  useSocket(path, {
    onMessage,
    onStatus: (st) => setState((s) => (s.cameraId === cameraId ? { ...s, connected: st === 'open' } : s)),
  });

  return {
    reads: state.reads,
    sightings: state.sightings,
    counts: state.counts,
    snapshot: state.snapshot,
    events: state.events,
    // useSocket drops a closed socket's handlers, so no 'closed' status arrives once the path goes away.
    connected: path !== null && state.connected,
  };
}
