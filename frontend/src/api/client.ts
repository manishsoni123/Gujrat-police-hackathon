/**
 * Single fetch wrapper for the Sentinel Gujarat API.
 *
 * - Adds `Authorization: Bearer <token>` from the auth store.
 * - Serialises query params (dropping undefined / empty values).
 * - Converts non-2xx responses to `ApiError` with the contract's
 *   `{detail, code, errors[]}` shape.
 * - A 401 logs the user out (the router then redirects to /login).
 */
import type { ApiErrorBody, ApiErrorItem } from './types';
import { useAuthStore } from '@/store/auth';

export const API_PREFIX = '/api';

export class ApiError extends Error {
  status: number;
  code: string;
  detail: string;
  errors: ApiErrorItem[];
  body: ApiErrorBody | null;

  constructor(status: number, body: ApiErrorBody | null, fallback: string) {
    super(body?.detail ?? fallback);
    this.name = 'ApiError';
    this.status = status;
    this.code = body?.code ?? (status === 0 ? 'network_error' : 'http_error');
    this.detail = body?.detail ?? fallback;
    this.errors = body?.errors ?? [];
    this.body = body;
  }
}

export type QueryValue = string | number | boolean | null | undefined;
export type Query = Record<string, QueryValue>;

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  query?: Query;
  body?: unknown;
  formData?: FormData;
  signal?: AbortSignal;
  /** Skip the automatic logout on 401 (used by the login form itself). */
  noAuthRedirect?: boolean;
}

export function buildQuery(query?: Query): string {
  if (!query) return '';
  const sp = new URLSearchParams();
  Object.entries(query).forEach(([k, v]) => {
    if (v === undefined || v === null || v === '') return;
    sp.set(k, String(v));
  });
  const s = sp.toString();
  return s ? `?${s}` : '';
}

/** Absolute API path for a contract path such as "/cameras". */
export function apiPath(path: string, query?: Query): string {
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${API_PREFIX}${p}${buildQuery(query)}`;
}

export function authHeaders(): Record<string, string> {
  const token = useAuthStore.getState().token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function parseError(res: Response): Promise<ApiError> {
  let body: ApiErrorBody | null = null;
  try {
    const text = await res.text();
    if (text) body = JSON.parse(text) as ApiErrorBody;
  } catch {
    body = null;
  }
  return new ApiError(res.status, body, `${res.status} ${res.statusText || 'Request failed'}`);
}

/** Core request helper. `path` is relative to `/api` (contract style). */
export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const url = apiPath(path, opts.query);
  const headers: Record<string, string> = { Accept: 'application/json', ...authHeaders() };
  let body: BodyInit | undefined;
  if (opts.formData) {
    body = opts.formData;
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(url, { method: opts.method ?? 'GET', headers, body, signal: opts.signal, credentials: 'same-origin' });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    throw new ApiError(0, { detail: 'Network error - the API is unreachable', code: 'network_error' }, 'Network error');
  }
  if (res.status === 401 && !opts.noAuthRedirect) {
    useAuthStore.getState().logout('expired');
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get('content-type') ?? '';
  if (ct.includes('application/json')) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

export interface DownloadResult {
  filename: string;
  sha256: string | null;
  size: number;
  blob: Blob;
}

function filenameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header);
  return m ? decodeURIComponent(m[1]) : fallback;
}

/**
 * Download a file (CSV/PDF/media) with the auth header, then hand it to the
 * browser as a save. Returns the file name and the `X-Sentinel-Sha256` hash.
 * `url` may be a contract API path ("/reports/detections") or an absolute
 * site path ("/media/reports/...").
 */
export async function downloadFile(url: string, query?: Query, fallbackName = 'download'): Promise<DownloadResult> {
  const full = url.startsWith('/media') || url.startsWith('/api') || url.startsWith('http') ? `${url}${buildQuery(query)}` : apiPath(url, query);
  const res = await fetch(full, { headers: authHeaders(), credentials: 'same-origin' });
  if (res.status === 401) useAuthStore.getState().logout('expired');
  if (!res.ok) throw await parseError(res);
  const blob = await res.blob();
  const filename = filenameFromDisposition(res.headers.get('content-disposition'), fallbackName);
  const sha256 = res.headers.get('x-sentinel-sha256');
  const a = document.createElement('a');
  const objectUrl = URL.createObjectURL(blob);
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 10_000);
  return { filename, sha256, size: blob.size, blob };
}

/** Media URL with the token as a query param, for elements that cannot send headers when cookies are unavailable. */
export function mediaUrl(path: string | null | undefined): string | null {
  if (!path) return null;
  return path;
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

export function errorMessage(e: unknown): string {
  if (isApiError(e)) return e.detail;
  if (e instanceof Error) return e.message;
  return 'Unexpected error';
}
