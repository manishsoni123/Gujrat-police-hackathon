/**
 * Camera-estate counts that separate "not streaming (catalogue live=false)" from real health states.
 *
 * The API status counters (`/dashboard/stats`, `/health/summary`, the `/ws/health` stats frame) bucket
 * every camera into online / degraded / offline / unknown. Cameras the catalogue marks `live=false`
 * are never pulled by the relay, so they must not be presented as red "offline": when the payload
 * carries a `not_streaming` counter the API has already excluded them; otherwise the counter is
 * derived from the per-camera `live` flag (geo features or the camera list) and the affected
 * buckets are corrected exactly, camera by camera.
 */
import type { CameraStatus } from '@/theme/colours';

export interface StatusCounts {
  total: number;
  online: number;
  degraded: number;
  offline: number;
  unknown: number;
  /** Cameras with catalogue `live=false` (excluded from the four buckets above). */
  not_streaming?: number;
}

export interface CameraStateRow {
  status: CameraStatus | string;
  live: boolean | null | undefined;
}

/** Rows in the not-streaming state (API status `not_streaming`, or catalogue live=false on older payloads). */
export function notStreaming<T extends CameraStateRow>(rows: T[]): T[] {
  return rows.filter((r) => r.status === 'not_streaming' || (r.live === false && r.status !== 'retired'));
}

/**
 * Returns counts with `not_streaming` populated and the health buckets corrected. `rows` may be
 * any per-camera list that carries `status` + `live` (geo features, `/cameras` items); when the
 * counts already carry `not_streaming`, `rows` is ignored.
 */
export function splitNotStreaming(counts: StatusCounts | null | undefined, rows: CameraStateRow[]): (StatusCounts & { not_streaming: number }) | null {
  if (!counts) return null;
  if (typeof counts.not_streaming === 'number') return { ...counts, not_streaming: counts.not_streaming };
  const out = { ...counts, not_streaming: 0 };
  notStreaming(rows).forEach((r) => {
    const key = r.status as keyof StatusCounts;
    if (key === 'online' || key === 'degraded' || key === 'offline' || key === 'unknown') {
      if (out[key] > 0) out[key] -= 1;
    }
    out.not_streaming += 1;
  });
  return out;
}
