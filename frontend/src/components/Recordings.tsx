/**
 * Recorded viewing (V4): segment timeline for a camera, "Play recording" at a
 * timestamp (native <video>), and "Export clip" (POST /clips) with the hash.
 */
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Descriptions, Empty, Modal, Space, Spin, Tooltip, Typography, message } from 'antd';
import { PlayCircleOutlined, ScissorOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { recordingsApi } from '@/api';
import type { Clip, RecordingSegment } from '@/api/types';
import { fmtIst, fmtIstShort, toIst, dayjs } from '@/utils/time';
import { HashText } from './Misc';
import { fmtBytes } from '@/utils/format';
import { usePermission } from '@/store/auth';
import { errorMessage } from '@/api/client';

interface PlayRecordingModalProps {
  open: boolean;
  onClose: () => void;
  cameraId: number;
  cameraName?: string;
  /** Timestamp of the read/sighting; playback starts 10 s before. */
  at: string;
  alertId?: number | null;
  sightingId?: number | null;
}

export function PlayRecordingModal({ open, onClose, cameraId, cameraName, at, alertId, sightingId }: PlayRecordingModalProps) {
  const qc = useQueryClient();
  const canClip = usePermission('events.write');
  const play = useQuery({
    queryKey: ['recordings', 'play', cameraId, at],
    queryFn: () => recordingsApi.play(cameraId, at, 10, 30),
    enabled: open,
  });
  const [clip, setClip] = useState<Clip | null>(null);
  const createClip = useMutation({
    mutationFn: () => recordingsApi.createClip({ camera_id: cameraId, start_at: dayjs.utc(at).subtract(10, 'second').toISOString(), duration_s: 30, alert_id: alertId ?? undefined, sighting_id: sightingId ?? undefined }),
    onSuccess: (c) => {
      setClip(c);
      qc.invalidateQueries({ queryKey: ['clips'] });
      message.success('Evidence clip created');
    },
  });

  return (
    <Modal open={open} onCancel={onClose} footer={null} width={760} destroyOnClose title={`Recording · ${cameraName ?? `camera ${cameraId}`} · from ${fmtIst(dayjs.utc(at).subtract(10, 'second').toISOString())}`}>
      {play.isLoading ? (
        <div style={{ padding: 40, textAlign: 'center' }}>
          <Spin tip="Resolving segment…">
            <div style={{ width: 200, height: 30 }} />
          </Spin>
        </div>
      ) : play.isError ? (
        <Alert type="error" showIcon message={errorMessage(play.error)} />
      ) : play.data?.available && play.data.url ? (
        <div>
          <video src={play.data.url} controls autoPlay style={{ width: '100%', borderRadius: 8, background: '#000', maxHeight: 420 }} aria-label="Recorded footage" />
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12, flexWrap: 'wrap', gap: 8 }}>
            <Typography.Text type="secondary">
              Segment start {fmtIst(play.data.start)} · {play.data.duration_s} s · fMP4 from the MediaMTX playback server
            </Typography.Text>
            {canClip && !clip ? (
              <Button icon={<ScissorOutlined />} type="primary" loading={createClip.isPending} onClick={() => createClip.mutate()}>
                Export 30 s clip as evidence
              </Button>
            ) : null}
          </div>
          {clip ? (
            <Alert
              type="success"
              showIcon
              icon={<SafetyCertificateOutlined />}
              style={{ marginTop: 12 }}
              message={`Clip #${clip.id} stored (${fmtBytes(clip.size_bytes)})`}
              description={
                <Space direction="vertical" size={2}>
                  <span>
                    SHA-256: <HashText hash={clip.sha256} full />
                  </span>
                  <a href={clip.url} target="_blank" rel="noreferrer">
                    Open clip
                  </a>
                </Space>
              }
            />
          ) : null}
        </div>
      ) : (
        <Empty description="No recording covers this time. Recording is enabled only on ANPR-live cameras with 12 h rolling retention." />
      )}
    </Modal>
  );
}

interface RecordingsTimelineProps {
  cameraId: number;
  cameraName?: string;
}

export function RecordingsTimeline({ cameraId, cameraName }: RecordingsTimelineProps) {
  const rec = useQuery({ queryKey: ['recordings', cameraId], queryFn: () => recordingsApi.list(cameraId), refetchInterval: 60_000 });
  const [playing, setPlaying] = useState<RecordingSegment | null>(null);
  const clips = useQuery({ queryKey: ['clips', cameraId], queryFn: () => recordingsApi.clips({ camera_id: cameraId, page_size: 20 }) });

  const hours = useMemo(() => {
    const segs = rec.data?.segments ?? [];
    if (!segs.length) return [];
    const byHour = new Map<string, RecordingSegment[]>();
    segs.forEach((s) => {
      const key = toIst(s.start)?.format('DD MMM HH:00') ?? '?';
      byHour.set(key, [...(byHour.get(key) ?? []), s]);
    });
    return Array.from(byHour.entries());
  }, [rec.data]);

  if (rec.isLoading) return <Spin />;
  if (rec.isError) return <Alert type="error" showIcon message={errorMessage(rec.error)} />;
  const data = rec.data;
  if (!data) return null;

  return (
    <div>
      <Descriptions size="small" column={3} style={{ marginBottom: 12 }}>
        <Descriptions.Item label="Recording">{data.record_enabled ? 'Enabled' : 'Disabled'}</Descriptions.Item>
        <Descriptions.Item label="Playback path">
          <code>{data.playback_path}</code>
        </Descriptions.Item>
        <Descriptions.Item label="Retention">{data.retention_h} h rolling</Descriptions.Item>
      </Descriptions>
      {!data.segments.length ? (
        <Empty description={data.record_enabled ? 'No segments yet - the relay writes 1-minute fMP4 segments while the stream is being read.' : 'Recording is disabled on this camera. Enable it in the camera settings (ANPR-live cameras record by default).'} />
      ) : (
        <div style={{ display: 'grid', gap: 8 }}>
          {hours.map(([hour, segs]) => (
            <div key={hour}>
              <div style={{ fontSize: 12, color: '#6B7280', marginBottom: 4 }}>{hour} IST · {segs.length} segments</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                {segs.map((s) => (
                  <Tooltip key={s.start} title={`${fmtIstShort(s.start)} → ${fmtIstShort(s.end)} (${Math.round(s.duration_s)} s)`}>
                    <button
                      type="button"
                      onClick={() => setPlaying(s)}
                      aria-label={`Play segment starting ${fmtIst(s.start)}`}
                      style={{ width: 14, height: 22, borderRadius: 3, border: 0, background: playing?.start === s.start ? '#1E4DB7' : '#93C5FD', cursor: 'pointer', padding: 0 }}
                    />
                  </Tooltip>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      {playing ? (
        <div style={{ marginTop: 16 }}>
          <Typography.Text strong>
            <PlayCircleOutlined /> Segment {fmtIst(playing.start)}
          </Typography.Text>
          <video key={playing.start} src={playing.url} controls autoPlay style={{ width: '100%', marginTop: 8, borderRadius: 8, background: '#000', maxHeight: 360 }} aria-label={`Recording of ${cameraName ?? 'camera'}`} />
        </div>
      ) : null}
      {clips.data?.items.length ? (
        <div style={{ marginTop: 16 }}>
          <Typography.Text strong>Evidence clips</Typography.Text>
          <div style={{ display: 'grid', gap: 6, marginTop: 6 }}>
            {clips.data.items.map((c) => (
              <div key={c.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', border: '1px solid #E5E7EB', borderRadius: 6, padding: '6px 10px', fontSize: 12 }}>
                <span>
                  Clip #{c.id} · {fmtIstShort(c.start_at)} · {c.duration_s} s · {fmtBytes(c.size_bytes)} · by {c.created_by_username ?? '—'}
                </span>
                <Space>
                  <HashText hash={c.sha256} />
                  <a href={c.url} target="_blank" rel="noreferrer">
                    Open
                  </a>
                </Space>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
