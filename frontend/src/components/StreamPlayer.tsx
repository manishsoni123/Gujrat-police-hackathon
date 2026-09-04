/**
 * StreamPlayer (CONTRACT §11.3): GET /streams/{id} → WHEP (5 s ICE timeout)
 * → hls.js (10 s manifest timeout) → snapshot mode (refresh every 1 s).
 * Manual mode toggle, "codec · mode · latency" caption, offline banner with
 * retry every 15 s, peer connection torn down on unmount.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Dropdown, Spin, Tooltip } from 'antd';
import { ExpandOutlined, ReloadOutlined, SettingOutlined } from '@ant-design/icons';
import Hls from 'hls.js';
import { streamsApi } from '@/api';
import { authHeaders } from '@/api/client';
import type { StreamInfo } from '@/api/types';
import { withCacheBuster } from '@/utils/format';

export type PlayerMode = 'auto' | 'whep' | 'hls' | 'snapshot';
export type ActiveMode = 'whep' | 'hls' | 'snapshot';
type Phase = 'loading' | 'connecting' | 'playing' | 'fallback' | 'snapshot' | 'error';

export interface StreamPlayerProps {
  cameraId: number;
  playPath?: string;
  autoplay?: boolean;
  muted?: boolean;
  mode?: PlayerMode;
  onModeChange?: (mode: ActiveMode) => void;
  /** Camera name shown in the tile title (wall). */
  title?: string;
  /** Force snapshot-only (16-grid wall tiles). */
  snapshotOnly?: boolean;
  /** External signal (WS health) that the camera is offline. */
  offline?: boolean;
  /** Snapshot URL pushed by WS `snapshot` messages. */
  snapshotUrl?: string | null;
  compact?: boolean;
  onInfo?: (info: StreamInfo) => void;
}

const ICE_TIMEOUT_MS = 5000;
const HLS_TIMEOUT_MS = 10000;
const RETRY_MS = 15000;

const MODE_LABEL: Record<ActiveMode, string> = { whep: 'WebRTC', hls: 'HLS', snapshot: 'Snapshot' };

export function StreamPlayer(props: StreamPlayerProps) {
  const { cameraId, autoplay = true, muted = true, mode = 'auto', onModeChange, title, snapshotOnly, offline, snapshotUrl, compact, onInfo } = props;
  const videoRef = useRef<HTMLVideoElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const hlsRef = useRef<Hls | null>(null);
  const sessionRef = useRef<string | null>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const generation = useRef(0);

  const [info, setInfo] = useState<StreamInfo | null>(null);
  const [phase, setPhase] = useState<Phase>('loading');
  const [active, setActive] = useState<ActiveMode | null>(null);
  const [statusText, setStatusText] = useState('Loading stream…');
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  const [snapTick, setSnapTick] = useState(Date.now());
  const [manualMode, setManualMode] = useState<PlayerMode>(mode);
  const [error, setError] = useState<string | null>(null);

  const clearTimers = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  };
  const later = (fn: () => void, ms: number) => {
    const t = setTimeout(fn, ms);
    timers.current.push(t);
    return t;
  };

  const teardown = useCallback(() => {
    clearTimers();
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }
    if (pcRef.current) {
      try {
        pcRef.current.close();
      } catch {
        /* ignore */
      }
      pcRef.current = null;
    }
    if (sessionRef.current) {
      const s = sessionRef.current;
      sessionRef.current = null;
      fetch(s, { method: 'DELETE', headers: authHeaders(), credentials: 'same-origin' }).catch(() => undefined);
    }
    const v = videoRef.current;
    if (v) {
      v.srcObject = null;
      v.removeAttribute('src');
      v.load();
    }
  }, []);

  const setModeActive = useCallback(
    (m: ActiveMode) => {
      setActive(m);
      onModeChange?.(m);
    },
    [onModeChange],
  );

  const startSnapshot = useCallback(
    (reason: string) => {
      teardown();
      setPhase('snapshot');
      setStatusText(reason);
      setModeActive('snapshot');
    },
    [teardown, setModeActive],
  );

  const startHls = useCallback(
    (si: StreamInfo, gen: number) => {
      const v = videoRef.current;
      if (!v) return;
      setPhase('fallback');
      setStatusText('Falling back to HLS…');
      const fail = (why: string) => {
        if (generation.current !== gen) return;
        startSnapshot(`Snapshot mode — ${why}`);
      };
      if (Hls.isSupported()) {
        const hls = new Hls({
          lowLatencyMode: true,
          liveSyncDurationCount: 3,
          manifestLoadingTimeOut: HLS_TIMEOUT_MS,
          levelLoadingTimeOut: HLS_TIMEOUT_MS,
          xhrSetup: (xhr) => {
            const h = authHeaders();
            if (h.Authorization) xhr.setRequestHeader('Authorization', h.Authorization);
            xhr.withCredentials = true;
          },
        });
        hlsRef.current = hls;
        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          if (generation.current !== gen) return;
          v.play().catch(() => undefined);
          setPhase('playing');
          setModeActive('hls');
          setStatusText('');
        });
        hls.on(Hls.Events.ERROR, (_e, data) => {
          if (data.fatal) fail('stream unavailable');
        });
        hls.loadSource(si.hls_url);
        hls.attachMedia(v);
        later(() => {
          if (generation.current === gen && hlsRef.current && v.readyState < 2) fail('HLS timed out');
        }, HLS_TIMEOUT_MS + 2000);
      } else if (v.canPlayType('application/vnd.apple.mpegurl')) {
        v.src = si.hls_url;
        v.play().then(() => {
          setPhase('playing');
          setModeActive('hls');
        }).catch(() => fail('playback blocked'));
      } else {
        fail('HLS unsupported');
      }
    },
    [startSnapshot, setModeActive],
  );

  const startWhep = useCallback(
    async (si: StreamInfo, gen: number) => {
      const v = videoRef.current;
      if (!v || typeof RTCPeerConnection === 'undefined') {
        startHls(si, gen);
        return;
      }
      setPhase('connecting');
      setStatusText('Connecting WebRTC…');
      const pc = new RTCPeerConnection({ iceServers: [] });
      pcRef.current = pc;
      const stream = new MediaStream();
      pc.addTransceiver('video', { direction: 'recvonly' });
      pc.addTransceiver('audio', { direction: 'recvonly' });
      pc.ontrack = (ev) => {
        stream.addTrack(ev.track);
        v.srcObject = stream;
        v.play().catch(() => undefined);
      };
      const bail = (why: string) => {
        if (generation.current !== gen) return;
        if (pcRef.current) {
          pcRef.current.close();
          pcRef.current = null;
        }
        setStatusText(why);
        startHls(si, gen);
      };
      const iceTimer = later(() => {
        if (pc.iceConnectionState !== 'connected' && pc.iceConnectionState !== 'completed') bail('WebRTC timed out');
      }, ICE_TIMEOUT_MS);
      pc.oniceconnectionstatechange = () => {
        if (generation.current !== gen) return;
        if (pc.iceConnectionState === 'connected' || pc.iceConnectionState === 'completed') {
          clearTimeout(iceTimer);
          setPhase('playing');
          setModeActive('whep');
          setStatusText('');
        } else if (pc.iceConnectionState === 'failed' || pc.iceConnectionState === 'disconnected') {
          bail('WebRTC connection lost');
        }
      };
      try {
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        await new Promise<void>((resolve) => {
          if (pc.iceGatheringState === 'complete') resolve();
          const t = setTimeout(resolve, 1000);
          pc.onicegatheringstatechange = () => {
            if (pc.iceGatheringState === 'complete') {
              clearTimeout(t);
              resolve();
            }
          };
        });
        const res = await fetch(si.whep_url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/sdp', ...authHeaders() },
          body: pc.localDescription?.sdp ?? offer.sdp,
          credentials: 'same-origin',
        });
        if (generation.current !== gen) return;
        if (!res.ok) throw new Error(`WHEP ${res.status}`);
        const loc = res.headers.get('location');
        if (loc) sessionRef.current = loc.startsWith('http') ? loc : new URL(loc, window.location.origin).pathname;
        const answer = await res.text();
        await pc.setRemoteDescription({ type: 'answer', sdp: answer });
      } catch (e) {
        bail(e instanceof Error && e.message.startsWith('WHEP') ? 'WebRTC unavailable' : 'WebRTC failed');
      }
    },
    [startHls, setModeActive],
  );

  const start = useCallback(async () => {
    generation.current += 1;
    const gen = generation.current;
    teardown();
    setError(null);
    setPhase('loading');
    setStatusText('Loading stream…');
    let si: StreamInfo;
    try {
      si = await streamsApi.get(cameraId);
    } catch (e) {
      if (generation.current !== gen) return;
      setError(e instanceof Error ? e.message : 'Stream lookup failed');
      setPhase('error');
      return;
    }
    if (generation.current !== gen) return;
    setInfo(si);
    onInfo?.(si);
    const want: PlayerMode = snapshotOnly ? 'snapshot' : manualMode;
    if (want === 'snapshot') {
      startSnapshot(si.snapshot_url ? 'Snapshot mode' : 'Snapshot mode — no snapshot yet');
      return;
    }
    if (want === 'hls') {
      startHls(si, gen);
      return;
    }
    await startWhep(si, gen);
  }, [cameraId, manualMode, snapshotOnly, teardown, startSnapshot, startHls, startWhep, onInfo]);

  useEffect(() => {
    if (!autoplay) return undefined;
    void start();
    return () => {
      generation.current += 1;
      teardown();
    };
  }, [start, autoplay, teardown]);

  // Snapshot refresh every second while in snapshot mode; retry live every 15 s in auto mode.
  useEffect(() => {
    if (phase !== 'snapshot' && phase !== 'error') return undefined;
    const t = setInterval(() => setSnapTick(Date.now()), 1000);
    let retry: ReturnType<typeof setTimeout> | null = null;
    if (!snapshotOnly && manualMode === 'auto') retry = setTimeout(() => void start(), RETRY_MS);
    return () => {
      clearInterval(t);
      if (retry) clearTimeout(retry);
    };
  }, [phase, manualMode, snapshotOnly, start]);

  // Latency indicator: buffered-end minus current time (HLS) or a nominal figure for WebRTC.
  useEffect(() => {
    if (phase !== 'playing') {
      setLatencyMs(null);
      return undefined;
    }
    const t = setInterval(() => {
      const v = videoRef.current;
      if (!v) return;
      if (active === 'hls' && v.buffered.length) {
        const end = v.buffered.end(v.buffered.length - 1);
        setLatencyMs(Math.max(0, Math.round((end - v.currentTime) * 1000)));
      } else if (active === 'whep') {
        const pc = pcRef.current;
        if (!pc) return;
        pc.getStats()
          .then((stats) => {
            let jitterMs = 0;
            stats.forEach((r) => {
              if (r.type === 'inbound-rtp' && r.kind === 'video' && typeof r.jitterBufferDelay === 'number' && r.jitterBufferEmittedCount) {
                jitterMs = Math.round((r.jitterBufferDelay / r.jitterBufferEmittedCount) * 1000);
              }
            });
            setLatencyMs(jitterMs > 0 ? jitterMs + 150 : 400);
          })
          .catch(() => undefined);
      }
    }, 2000);
    return () => clearInterval(t);
  }, [phase, active]);

  const snapshotSrc = withCacheBuster(snapshotUrl ?? info?.snapshot_url ?? null, snapTick);
  const showVideo = phase === 'playing' || phase === 'connecting' || phase === 'fallback';

  const fullscreen = () => {
    const el = containerRef.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else void el.requestFullscreen?.();
  };

  const modeMenu = {
    items: [
      { key: 'auto', label: 'Auto (WebRTC → HLS → Snapshot)' },
      { key: 'whep', label: 'WebRTC' },
      { key: 'hls', label: 'HLS' },
      { key: 'snapshot', label: 'Snapshot' },
    ],
    selectedKeys: [manualMode],
    onClick: ({ key }: { key: string }) => setManualMode(key as PlayerMode),
  };

  return (
    <div className="sg-player" ref={containerRef} tabIndex={0} aria-label={`Live stream ${title ?? info?.name ?? cameraId}`}>
      <video ref={videoRef} muted={muted} playsInline autoPlay controls={false} style={{ display: showVideo ? undefined : 'none' }} />
      {!showVideo ? (
        snapshotSrc ? (
          <img src={snapshotSrc} alt={`Latest snapshot of ${title ?? info?.name ?? `camera ${cameraId}`}`} />
        ) : (
          <div style={{ width: '100%', height: '100%', background: '#0b0f19' }} />
        )
      ) : null}
      {phase === 'loading' || phase === 'connecting' || phase === 'fallback' ? (
        <div className="sg-player-overlay">
          <Spin />
          <span>{statusText}</span>
        </div>
      ) : null}
      {phase === 'error' ? (
        <div className="sg-player-overlay">
          <span>{error ?? 'Stream unavailable'}</span>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => void start()}>
            Retry
          </Button>
        </div>
      ) : null}
      {phase === 'snapshot' && !snapshotSrc ? (
        <div className="sg-player-overlay">
          <span>{statusText}</span>
          <span style={{ fontSize: 11, opacity: 0.7 }}>No snapshot has been posted by the ANPR worker yet</span>
        </div>
      ) : null}
      {offline ? <div className="sg-player-banner">Camera reported offline by the health poller — retrying every 15 s</div> : null}
      <div className="sg-player-topbar">
        <div className="sg-player-title">{title ?? info?.name ?? `Camera ${cameraId}`}</div>
        {!snapshotOnly ? (
          <div className="sg-player-controls sg-no-print">
            <Dropdown menu={modeMenu} trigger={['click']}>
              <Tooltip title="Playback mode">
                <Button size="small" icon={<SettingOutlined />} aria-label="Playback mode" />
              </Tooltip>
            </Dropdown>
            <Tooltip title="Reconnect">
              <Button size="small" icon={<ReloadOutlined />} onClick={() => void start()} aria-label="Reconnect" />
            </Tooltip>
            <Tooltip title="Fullscreen">
              <Button size="small" icon={<ExpandOutlined />} onClick={fullscreen} aria-label="Fullscreen" />
            </Tooltip>
          </div>
        ) : null}
      </div>
      <div className="sg-player-caption">
        {phase === 'playing' ? <span className="sg-live-badge">LIVE</span> : null}
        {info ? <span>{info.codec === 'H265' ? 'H.265→H.264' : info.codec === 'H264' ? 'H.264' : info.codec}</span> : null}
        <span>·</span>
        <span>{active ? MODE_LABEL[active] : phase === 'loading' ? '…' : 'connecting'}</span>
        {latencyMs !== null && !compact ? (
          <>
            <span>·</span>
            <span>{latencyMs < 1000 ? `${latencyMs} ms` : `${(latencyMs / 1000).toFixed(1)} s`}</span>
          </>
        ) : null}
        {phase === 'snapshot' && info?.snapshot_stale ? <span style={{ color: '#FCD34D' }}>· stale</span> : null}
      </div>
    </div>
  );
}
