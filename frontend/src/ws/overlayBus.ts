/**
 * Fan-out of `/ws/reads/{camera_id}` `overlay` frames to the drawing layer. Kept outside React state:
 * frames arrive several times a second and only the canvas needs them, not a page re-render.
 */
import type { WsOverlay } from '@/api/types';

type Listener = (frame: WsOverlay) => void;

const listeners = new Map<number, Set<Listener>>();

export function publishOverlay(frame: WsOverlay): void {
  listeners.get(frame.camera_id)?.forEach((fn) => fn(frame));
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
