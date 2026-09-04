/** Per-camera health: uptime summary, transitions and the check log. */
import { useQuery } from '@tanstack/react-query';
import { Descriptions, Table, Tag, Typography } from 'antd';
import { camerasApi } from '@/api';
import { IstTime } from '@/components/IstTime';
import { StatusTag } from '@/components/Tags';
import { fmtBytes, fmtPct } from '@/utils/format';
import { InlineLoading, ErrorState } from '@/components/States';

export function HealthLogTable({ cameraId, hours = 24 }: { cameraId: number; hours?: number }) {
  const q = useQuery({ queryKey: ['health', 'camera', cameraId, hours], queryFn: () => camerasApi.health(cameraId, hours), refetchInterval: 60_000 });
  if (q.isLoading) return <InlineLoading />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const h = q.data;
  if (!h) return null;
  return (
    <div>
      <Descriptions size="small" column={4} style={{ marginBottom: 12 }}>
        <Descriptions.Item label="Status">
          <StatusTag status={h.status} />
        </Descriptions.Item>
        <Descriptions.Item label={`Uptime ${hours} h`}>{fmtPct(h.uptime_pct)}</Descriptions.Item>
        <Descriptions.Item label="Checks">{h.checks}</Descriptions.Item>
        <Descriptions.Item label="Last seen">
          <IstTime value={h.last_seen_at} />
        </Descriptions.Item>
      </Descriptions>
      {h.transitions.length ? (
        <div style={{ marginBottom: 12 }}>
          <Typography.Text strong style={{ fontSize: 13 }}>
            Transitions
          </Typography.Text>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
            {h.transitions.slice(0, 12).map((t, i) => (
              <Tag key={i} style={{ margin: 0 }}>
                <IstTime value={t.at} /> · {t.from} → {t.to}
              </Tag>
            ))}
          </div>
        </div>
      ) : null}
      <Table
        size="small"
        rowKey={(r) => r.checked_at}
        dataSource={h.log.slice(0, 200)}
        pagination={{ pageSize: 20, size: 'small' }}
        columns={[
          { title: 'Checked (IST)', dataIndex: 'checked_at', width: 150, render: (v: string) => <IstTime value={v} /> },
          { title: 'Ready', dataIndex: 'is_ready', width: 70, render: (v: boolean) => (v ? <Tag color="green" style={{ margin: 0 }}>yes</Tag> : <Tag color="red" style={{ margin: 0 }}>no</Tag>) },
          { title: 'Video', dataIndex: 'has_video', width: 70, render: (v: boolean) => (v ? 'yes' : 'no') },
          { title: 'Bytes Δ', dataIndex: 'bytes_delta', width: 100, render: (v: number) => fmtBytes(v) },
          { title: 'Readers', dataIndex: 'readers', width: 80 },
          { title: 'Source', dataIndex: 'source_flag', width: 100 },
          { title: 'Status after', dataIndex: 'status_after', width: 120, render: (v: string) => <StatusTag status={v} size="small" /> },
        ]}
      />
    </div>
  );
}
