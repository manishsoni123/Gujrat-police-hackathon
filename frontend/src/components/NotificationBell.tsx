/** Header alert bell: unread (status=new) count from WS stats, dropdown with the latest live alerts. */
import { useNavigate } from 'react-router-dom';
import { Badge, Button, Dropdown, Tooltip, Typography } from 'antd';
import { BellOutlined } from '@ant-design/icons';
import { useUiStore } from '@/store/ui';
import { PriorityTag } from './Tags';
import { PlateText } from './PlateText';
import { IstTime } from './IstTime';
import { ALERT_PRIORITY } from '@/theme/colours';

export function NotificationBell() {
  const navigate = useNavigate();
  const stats = useUiStore((s) => s.alertStats);
  const live = useUiStore((s) => s.liveAlerts);
  const unseen = useUiStore((s) => s.unseenAlertIds);
  const count = stats?.alerts_new ?? unseen.length;

  const content = (
    <div style={{ width: 380, background: '#fff', border: '1px solid #E5E7EB', borderRadius: 8, boxShadow: '0 8px 24px rgba(16,24,40,0.12)' }}>
      <div style={{ padding: '10px 14px', borderBottom: '1px solid #E5E7EB', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Typography.Text strong>Alerts</Typography.Text>
        <span style={{ fontSize: 12, color: '#6B7280' }}>
          {stats ? `${stats.alerts_new} new · ${stats.alerts_acknowledged} acknowledged · ${stats.critical_open} critical open` : 'Waiting for live stats…'}
        </span>
      </div>
      <div style={{ maxHeight: 360, overflow: 'auto' }}>
        {live.length === 0 ? (
          <div style={{ padding: '24px 14px', textAlign: 'center', color: '#6B7280', fontSize: 13 }}>
            No alerts received in this session yet. New watchlist hits appear here in real time.
          </div>
        ) : (
          live.slice(0, 8).map((a) => (
            <button
              key={a.id}
              type="button"
              onClick={() => navigate(`/alerts?id=${a.id}`)}
              style={{
                display: 'flex',
                gap: 10,
                width: '100%',
                textAlign: 'left',
                padding: '10px 14px',
                border: 0,
                borderBottom: '1px solid #F3F4F6',
                borderLeft: `3px solid ${ALERT_PRIORITY[a.priority]?.colour ?? '#9CA3AF'}`,
                background: unseen.includes(a.id) ? '#FEF2F2' : '#fff',
                cursor: 'pointer',
              }}
            >
              {a.snapshot_url ? (
                <img src={a.snapshot_url} alt="" style={{ width: 56, height: 40, objectFit: 'contain', background: '#111827', borderRadius: 4, border: '1px solid #E5E7EB' }} />
              ) : null}
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', justifyContent: 'space-between' }}>
                  <PriorityTag priority={a.priority} size="small" />
                  <IstTime value={a.created_at} mode="relative" muted />
                </div>
                <div style={{ marginTop: 4, display: 'flex', gap: 8, alignItems: 'center' }}>
                  {a.plate_norm ? <PlateText plate={a.plate_norm} size="small" /> : <span style={{ fontWeight: 500 }}>{a.notify_title}</span>}
                </div>
                <div style={{ fontSize: 12, color: '#6B7280', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.notify_body}</div>
              </div>
            </button>
          ))
        )}
      </div>
      <div style={{ padding: '8px 14px', borderTop: '1px solid #E5E7EB', textAlign: 'right' }}>
        <Button type="link" size="small" onClick={() => navigate('/alerts')} style={{ padding: 0 }}>
          Open alert panel
        </Button>
      </div>
    </div>
  );

  return (
    <Dropdown dropdownRender={() => content} trigger={['click']} placement="bottomRight">
      <Tooltip title="Alerts">
        <Button type="text" aria-label={`Alerts, ${count} new`} style={{ paddingInline: 8 }}>
          <Badge count={count} overflowCount={99} size="small" offset={[2, 0]}>
            <BellOutlined style={{ fontSize: 18, color: '#374151' }} />
          </Badge>
        </Button>
      </Tooltip>
    </Dropdown>
  );
}
