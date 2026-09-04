/**
 * Generic WebSocket hook implementing CONTRACT §9: `{type, ts, data}` envelopes, client ping
 * every 25 s, close code 4401 → stop (re-login needed), reconnect with jittered backoff
 * 1 → 2 → 4 → 8 → 15 s.
 *
 * Auth: the first attempt relies on the HttpOnly `sg_session` cookie that login sets on this
 * origin (the upgrade request carries it and `extract_token` on the API reads it), so the JWT
 * never appears in the URL and therefore never in Caddy/uvicorn access logs. Only if the server
 * answers 4401 to a cookie-less upgrade does the hook retry once with `?token=` (the contract's
 * query-string form), e.g. on a dev origin where the cookie is missing.
 */
import { useEffect, useRef } from 'react';
import type { WsEnvelope } from '@/api/types';
import { useAuthStore } from '@/store/auth';

export interface SocketOptions {
  enabled?: boolean;
  onMessage: (env: WsEnvelope) => void;
  onOpen?: () => void;
  onStatus?: (status: 'connecting' | 'open' | 'closed' | 'error') => void;
}

const BACKOFF = [1000, 2000, 4000, 8000, 15000];
const PING_MS = 25_000;

function wsBase(): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}`;
}

export function useSocket(path: string | null, opts: SocketOptions): void {
  const token = useAuthStore((s) => s.token);
  const cbRef = useRef(opts);
  cbRef.current = opts;
  const enabled = opts.enabled ?? true;

  useEffect(() => {
    if (!path || !token || !enabled) return undefined;
    let ws: WebSocket | null = null;
    let attempt = 0;
    let closedByUs = false;
    let useTokenParam = false;
    let pingTimer: ReturnType<typeof setInterval> | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const clearTimers = () => {
      if (pingTimer) clearInterval(pingTimer);
      pingTimer = null;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = null;
    };

    const connect = () => {
      if (closedByUs) return;
      cbRef.current.onStatus?.('connecting');
      const url = useTokenParam ? `${wsBase()}${path}${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}` : `${wsBase()}${path}`;
      try {
        ws = new WebSocket(url);
      } catch {
        cbRef.current.onStatus?.('error');
        scheduleReconnect();
        return;
      }
      ws.onopen = () => {
        attempt = 0;
        cbRef.current.onStatus?.('open');
        cbRef.current.onOpen?.();
        pingTimer = setInterval(() => {
          if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }));
        }, PING_MS);
      };
      ws.onmessage = (ev) => {
        try {
          const env = JSON.parse(String(ev.data)) as WsEnvelope;
          if (env && typeof env.type === 'string') cbRef.current.onMessage(env);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onerror = () => {
        cbRef.current.onStatus?.('error');
      };
      ws.onclose = (ev) => {
        if (pingTimer) clearInterval(pingTimer);
        pingTimer = null;
        cbRef.current.onStatus?.('closed');
        if (closedByUs) return;
        if (ev.code === 4401) {
          if (!useTokenParam) {
            // The cookie did not authenticate this upgrade: retry once with the token in the query string.
            useTokenParam = true;
            reconnectTimer = setTimeout(connect, 250);
            return;
          }
          // Unauthorised with an explicit token: the next API call will log the user out.
          return;
        }
        scheduleReconnect();
      };
    };

    const scheduleReconnect = () => {
      if (closedByUs) return;
      const base = BACKOFF[Math.min(attempt, BACKOFF.length - 1)];
      attempt += 1;
      const jitter = Math.floor(Math.random() * 400);
      reconnectTimer = setTimeout(connect, base + jitter);
    };

    connect();
    return () => {
      closedByUs = true;
      clearTimers();
      if (ws) {
        ws.onclose = null;
        ws.onerror = null;
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
    };
  }, [path, token, enabled]);
}
