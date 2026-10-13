/**
 * Fan-out of `/ws/reads/{camera_id}` `overlay` frames to the drawing layer. Kept outside React state:
 * frames arrive several times a second and only the canvas needs them, not a page re-render.
 *
 * The bus also keeps each camera's frame cadence (the worker's processed frame rate for it), for every
 * frame the socket delivers, whether or not a canvas is mounted. The canvas mounts only once WebRTC is
 * playing and remounts after every reconnect or toggle, and it needs the cadence for its very first frame.
 */
import type { WsOverlay } from '@/api/types';

type Listener = (frame: WsOverlay, arrivedAt: number) => void;

/** An arrival gap longer than this is an outage (socket reconnect, worker restart), not the cadence. */
export const OUTAGE_GAP_MS = 8000;
/** Weight of the newest inter-frame interval in the cadence average. */
const CADENCE_WEIGHT = 0.3;

const listeners = new Map<number, Set<Listener>>();
const cadence = new Map<number, { lastAt: number; avgMs: number | null }>();

/** Average inter-frame interval after a new arrival `gapMs` after the previous one. */
function nextCadence(avgMs: number | null, gapMs: number): number | null {
  if (!(gapMs > 0) || gapMs > OUTAGE_GAP_MS) return avgMs;
  return avgMs === null ? gapMs : avgMs + CADENCE_WEIGHT * (gapMs - avgMs);
}

export function publishOverlay(frame: WsOverlay): void {
  const id = frame.camera_id;
  if (typeof id !== 'number') return;
  const at = performance.now();
  const prev = cadence.get(id);
  cadence.set(id, { lastAt: at, avgMs: prev ? nextCadence(prev.avgMs, at - prev.lastAt) : null });
  listeners.get(id)?.forEach((fn) => fn(frame, at));
}

/** Average interval between a camera's `overlay` frames in ms; null until two frames have arrived. */
export function overlayCadenceMs(cameraId: number): number | null {
  return cadence.get(cameraId)?.avgMs ?? null;
}

export function subscribeOverlay(cameraId: number, fn: Listener): () => void {
  let set = listeners.get(cameraId);
  if (!set) {
    set = new Set();
    listeners.set(cameraId, set);
  }
  set.add(fn);
  return () => {
    const current = listeners.get(cameraId);
    current?.delete(fn);
    if (current && current.size === 0) listeners.delete(cameraId);
  };
}
