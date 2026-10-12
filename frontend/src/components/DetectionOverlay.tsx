/**
 * Live detection boxes over the StreamPlayer <video> (CONTRACT amendment 2026-10-12): plate boxes
 * (labelled with the track's read once OCR produced one) and object boxes from `/ws/reads/{id}`
 * `overlay` frames. Coordinates are fractions of the decoded frame, mapped onto the letterboxed
 * (`object-fit: contain`) picture. A frame fades out when no newer one arrives within STALE_MS.
 */
import { useEffect, useRef, type RefObject } from 'react';
import type { WsOverlay, WsOverlayBox } from '@/api/types';
import { subscribeOverlay } from '@/ws/overlayBus';

const STALE_MS = 1500;
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

export function DetectionOverlay({ cameraId, videoRef }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let latest: { frame: WsOverlay; at: number } | null = null;

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
      if (age > STALE_MS) {
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
      ctx.globalAlpha = age < STALE_MS / 2 ? 1 : 1 - (age - STALE_MS / 2) / (STALE_MS / 2);
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

    const unsubscribe = subscribeOverlay(cameraId, (frame) => {
      latest = { frame, at: performance.now() };
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
