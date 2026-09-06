/** Video wall: 4 / 9 / 16 grids, camera picker per slot, layout saved per user, 16-grid = 4 live + 12 snapshot tiles. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Card, Segmented, Select, Space, Tag, Tooltip, Typography, message } from 'antd';
import { FullscreenOutlined, PlusOutlined, SaveOutlined, CloseOutlined } from '@ant-design/icons';
import { PageHeader } from '@/components/PageHeader';
import { StreamPlayer } from '@/components/StreamPlayer';
import { wallApi } from '@/api';
import type { WallLayout } from '@/api/types';
import { useCameraOptions } from '@/hooks/useCamerasOptions';
import { EmptyState, ErrorState } from '@/components/States';
import { StatusTag, CodecTag } from '@/components/Tags';
import { cameraSourceLabel } from '@/utils/labels';
import { useHealthSocket } from '@/ws/useHealthSocket';

const LIVE_SLOTS_IN_16 = 4;

export function WallPage() {
  const qc = useQueryClient();
  const { cameras, options, byId } = useCameraOptions();
  const layoutQ = useQuery({ queryKey: ['wall-layout'], queryFn: wallApi.get });
  const [layout, setLayout] = useState<WallLayout | null>(null);
  const [dirty, setDirty] = useState(false);
  const [offlineIds, setOfflineIds] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (layoutQ.data && !layout) setLayout(layoutQ.data);
  }, [layoutQ.data, layout]);

  useHealthSocket({
    onHealth: (h) =>
      setOfflineIds((prev) => {
        const next = new Set(prev);
        if (h.status === 'offline') next.add(h.camera_id);
        else next.delete(h.camera_id);
        return next;
      }),
  });

  const save = useMutation({
    mutationFn: (l: WallLayout) => wallApi.put(l),
    onSuccess: (l) => {
      qc.setQueryData(['wall-layout'], l);
      setDirty(false);
      message.success('Wall layout saved');
    },
  });

  const grid = layout?.grid ?? 4;
  const slots = useMemo(() => {
    const out: (number | null)[] = Array.from({ length: grid }, () => null);
    layout?.tiles.forEach((t) => {
      if (t.slot < grid) out[t.slot] = t.camera_id;
    });
    return out;
  }, [layout, grid]);

  const setGrid = (g: 4 | 9 | 16) => {
    setLayout((l) => ({ grid: g, tiles: (l?.tiles ?? []).filter((t) => t.slot < g) }));
    setDirty(true);
  };
  const setSlot = useCallback((slot: number, cameraId: number | null) => {
    setLayout((l) => {
      const tiles = (l?.tiles ?? []).filter((t) => t.slot !== slot);
      if (cameraId !== null) tiles.push({ slot, camera_id: cameraId });
      return { grid: l?.grid ?? 4, tiles: tiles.sort((a, b) => a.slot - b.slot) };
    });
    setDirty(true);
  }, []);

  const autoFill = () => {
    const pool = cameras.filter((c) => c.status !== 'retired').sort((a, b) => Number(b.anpr_enabled) - Number(a.anpr_enabled) || (a.status === 'online' ? -1 : 1));
    const tiles = pool.slice(0, grid).map((c, i) => ({ slot: i, camera_id: c.id }));
    setLayout({ grid, tiles });
    setDirty(true);
  };

  const fullscreen = () => {
    const el = document.getElementById('sg-wall');
    if (el && !document.fullscreenElement) void el.requestFullscreen?.();
    else if (document.fullscreenElement) void document.exitFullscreen();
  };

  const systems = useMemo(() => {
    const srcs = new Set(slots.map((id) => (id ? byId.get(id)?.source : null)).filter(Boolean));
    return Array.from(srcs) as string[];
  }, [slots, byId]);

  if (layoutQ.isError && !layout) return <ErrorState error={layoutQ.error} onRetry={() => void layoutQ.refetch()} />;

  return (
    <div>
      <PageHeader
        title="Live Wall"
        description={
          <span>
            Simultaneous live tiles via WebRTC with automatic HLS fallback. The 16-grid shows {LIVE_SLOTS_IN_16} live tiles and 12 snapshot tiles refreshed every second.
            {systems.length > 1 ? (
              <Tooltip title={`Feeds from ${systems.length} different systems on one wall: ${systems.map(cameraSourceLabel).join(', ')}`}>
                <Tag color="blue" style={{ marginLeft: 8 }}>
                  {systems.length} systems
                </Tag>
              </Tooltip>
            ) : null}
          </span>
        }
        extra={
          <Space wrap>
            <Segmented options={[{ label: '4', value: 4 }, { label: '9', value: 9 }, { label: '16', value: 16 }]} value={grid} onChange={(v) => setGrid(v as 4 | 9 | 16)} />
            <Button icon={<PlusOutlined />} onClick={autoFill}>
              Auto-fill
            </Button>
            <Button icon={<FullscreenOutlined />} onClick={fullscreen}>
              Fullscreen
            </Button>
            <Button type="primary" icon={<SaveOutlined />} disabled={!dirty || !layout} loading={save.isPending} onClick={() => layout && save.mutate(layout)}>
              Save layout
            </Button>
          </Space>
        }
      />
      {slots.every((s) => s === null) ? (
        <Card>
          <EmptyState
            title="The wall is empty"
            description="Pick a camera for each tile or auto-fill with ANPR-enabled cameras. Your layout is saved to your user account."
            actions={
              <Button type="primary" icon={<PlusOutlined />} onClick={autoFill}>
                Auto-fill {grid} tiles
              </Button>
            }
          />
        </Card>
      ) : null}
      <div id="sg-wall" className={`sg-wall sg-wall-${grid}`} style={{ background: '#0B0F19', padding: 8, borderRadius: 8 }}>
        {slots.map((cameraId, i) => {
          const cam = cameraId ? byId.get(cameraId) : undefined;
          const snapshotOnly = grid === 16 && i >= LIVE_SLOTS_IN_16;
          return (
            <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {cam && cameraId ? (
                <StreamPlayer cameraId={cameraId} title={cam.name} snapshotOnly={snapshotOnly} compact offline={offlineIds.has(cameraId) || cam.status === 'offline'} />
              ) : (
                <div className="sg-player" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', border: '1px dashed #334155' }}>
                  <Typography.Text style={{ color: '#94A3B8' }}>Slot {i + 1} - choose a camera</Typography.Text>
                </div>
              )}
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', minWidth: 0, overflow: 'hidden' }}>
                <Select
                  showSearch
                  allowClear
                  size="small"
                  placeholder={`Slot ${i + 1}`}
                  style={{ flex: '1 1 auto', minWidth: 0 }}
                  value={cameraId ?? undefined}
                  options={options}
                  optionFilterProp="label"
                  onChange={(v) => setSlot(i, (v as number | undefined) ?? null)}
                  aria-label={`Camera for slot ${i + 1}`}
                />
                {cam ? <span style={{ flex: 'none' }}><StatusTag status={cam.status} live={cam.live} size="small" short /></span> : null}
                {cam && grid < 16 ? <span style={{ flex: 'none' }}><CodecTag codec={cam.codec} /></span> : null}
                {snapshotOnly ? <Tag style={{ margin: 0 }}>Snapshot</Tag> : null}
                {cameraId ? <Button size="small" type="text" icon={<CloseOutlined />} onClick={() => setSlot(i, null)} aria-label="Clear slot" style={{ color: '#94A3B8' }} /> : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
