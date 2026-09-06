/**
 * Credential masking for URLs rendered by the UI (defence in depth).
 *
 * The API already masks every URL field it returns (`rtsp_url`, `whep_url`, `hls_url`, audit diffs, import
 * warnings - CONTRACT Amendments 2026-09-05), so the SPA normally only ever sees `scheme://user:***@host/…`.
 * These helpers make sure that no page renders a raw `user:password@` even if some field slips through
 * (an older API build, a free-text message that embeds a URL, a mock). Passwords containing `@` or `:` are
 * handled: the userinfo is everything up to the *last* `@` before the path/query/fragment/whitespace.
 */

const AUTHORITY_RE = /([a-z][a-z0-9+.-]*:\/\/)([^\s/?#]+)@/gi;

/** `rtsp://mail%40x.com:s3cr@t@host:8554/stream/cam01` → `rtsp://mail%40x.com:***@host:8554/stream/cam01`. Idempotent. */
export function maskUrlCredentials(text: string | null | undefined): string {
  if (!text) return '';
  return text.replace(AUTHORITY_RE, (_m, scheme: string, userinfo: string) => {
    const colon = userinfo.indexOf(':');
    if (colon < 0) return `${scheme}${userinfo}@`; // user only, nothing secret
    return `${scheme}${userinfo.slice(0, colon)}:***@`;
  });
}

/** Recursively masks every string inside a JSON-like value (audit before/after payloads, test results). */
export function maskSecretsDeep<T>(value: T): T {
  if (typeof value === 'string') return maskUrlCredentials(value) as unknown as T;
  if (Array.isArray(value)) return value.map((v) => maskSecretsDeep(v)) as unknown as T;
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    Object.entries(value as Record<string, unknown>).forEach(([k, v]) => {
      out[k] = maskSecretsDeep(v);
    });
    return out as T;
  }
  return value;
}
