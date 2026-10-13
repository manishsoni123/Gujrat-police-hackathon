/**
 * Live detection boxes over the StreamPlayer <video> (CONTRACT amendment 2026-10-12): plate boxes
 * (labelled with the track's read once OCR produced one) and object boxes from `/ws/reads/{id}`
 * `overlay` frames. Coordinates are fractions of the decoded frame, mapped onto the letterboxed
 * (`object-fit: contain`) picture.
 *
 * Frames arrive at the worker's processed rate for the camera: several per second on the GPU profile,
 * about one every 1.4 s on the 8-camera CPU worker (0.2-2 fps measured). A frame therefore stays up for
 * STALE_INTERVALS observed inter-frame intervals (at least MIN_STALE_MS, at most MAX_STALE_MS) and fades
 * only in the last part of that window, so boxes hold steady between updates at any cadence and still
 * disappear soon after frames stop.
 */
import { useEffect, useRef, type RefObject } from 'react';
import type { WsOverlay, WsOverlayBox } from '@/api/types';
import { subscribeOverlay } from '@/ws/overlayBus';

const MIN_STALE_MS = 1500;
const MAX_STALE_MS = 8000;
const STALE_INTERVALS = 2.5;
/** Full opacity for this share of the stale window, then a linear fade to 0. */
const FADE_FROM = 0.6;
/** Weight of the newest inter-frame interval in the cadence average. */
const CADENCE_WEIGHT = 0.3;
const REDRAW_MS = 250;
const PLATE_COLOUR = '#FACC15';
const OBJECT_COLOUR: Record<string, string> = {
  person: '#F472B6',
  bicycle: '#34D399',
  motorcycle: '#FB923C',
  car: '#60A5FA',
  bus: '#A78BFA',
  truck: '#F87171',
};

interface Props {
  cameraId: number;
  videoRef: RefObject<HTMLVideoElement>;
}

/**
 * Average inter-frame interval after a new arrival `gapMs` after the previous one. A gap longer than
 * MAX_STALE_MS is an outage (socket reconnect, worker restart), not the cadence, and is left out.
 */
function nextCadence(avgMs: number | null, gapMs: number): number | null {
  if (!(gapMs > 0) || gapMs > MAX_STALE_MS) return avgMs;
  return avgMs === null ? gapMs : avgMs + CADENCE_WEIGHT * (gapMs - avgMs);
}

/** How long a frame stays on screen without a newer one, for the observed cadence. */
function staleWindow(avgMs: number | null): number {
  if (avgMs === null) return MIN_STALE_MS;
  return Math.min(MAX_STALE_MS, Math.max(MIN_STALE_MS, STALE_INTERVALS * avgMs));
}

function frameAlpha(ageMs: number, staleMs: number): number {
  const fadeFrom = staleMs * FADE_FROM;
  if (ageMs <= fadeFrom) return 1;
  return Math.max(0, 1 - (ageMs - fadeFrom) / (staleMs - fadeFrom));
}

export function DetectionOverlay({ cameraId, videoRef }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let latest: { frame: WsOverlay; at: number } | null = null;
    let cadenceMs: number | null = null;

    const draw = () => {
      const canvas = canvasRef.current;
      const video = videoRef.current;
      const ctx = canvas?.getContext('2d');
      if (!canvas || !video || !ctx) return;
      const cw = canvas.clientWidth;
      const ch = canvas.clientHeight;
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== Math.round(cw * dpr) || canvas.height !== Math.round(ch * dpr)) {
        canvas.width = Math.round(cw * dpr);
        canvas.height = Math.round(ch * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cw, ch);
      if (!latest) return;
      const age = performance.now() - latest.at;
      const stale = staleWindow(cadenceMs);
      if (age >= stale) {
        latest = null;
        return;
      }
      const { frame } = latest;
      // The picture inside the element: object-fit contain letterboxes it.
      const vw = video.videoWidth || frame.frame_w;
      const vh = video.videoHeight || frame.frame_h;
      const scale = Math.min(cw / vw, ch / vh);
      const dw = vw * scale;
      const dh = vh * scale;
      const ox = (cw - dw) / 2;
      const oy = (ch - dh) / 2;
      ctx.globalAlpha = frameAlpha(age, stale);
      ctx.font = '600 12px Inter, system-ui, sans-serif';
      ctx.textBaseline = 'top';
      const box = (b: WsOverlayBox, colour: string, text: string) => {
        const [x, y, w, h] = b.box;
        const rx = ox + x * dw;
        const ry = oy + y * dh;
        ctx.strokeStyle = colour;
        ctx.lineWidth = 2;
        ctx.strokeRect(rx, ry, w * dw, h * dh);
        const tw = ctx.measureText(text).width + 8;
        const ty = ry >= 18 ? ry - 18 : ry + h * dh;
        ctx.fillStyle = colour;
        ctx.fillRect(rx, ty, tw, 18);
        ctx.fillStyle = '#0A0A0A';
        ctx.fillText(text, rx + 4, ty + 3);
      };
      for (const o of frame.objects) box(o, OBJECT_COLOUR[o.cls ?? ''] ?? '#E5E7EB', `${o.cls ?? 'object'} ${Math.round(o.conf * 100)}%`);
      for (const p of frame.plates) box(p, PLATE_COLOUR, p.label ?? 'plate');
      ctx.globalAlpha = 1;
    };

    let lastArrival: number | null = null;
    const unsubscribe = subscribeOverlay(cameraId, (frame) => {
      const now = performance.now();
      if (lastArrival !== null) cadenceMs = nextCadence(cadenceMs, now - lastArrival);
      lastArrival = now;
      latest = { frame, at: now };
      draw();
    });
    const timer = window.setInterval(draw, REDRAW_MS); // fade-out and resize without a new frame
    return () => {
      unsubscribe();
      window.clearInterval(timer);
    };
  }, [cameraId, videoRef]);

  return <canvas ref={canvasRef} className="sg-player-detections" aria-hidden="true" />;
}
