import { Button, Card, Descriptions, Space, Table, Typography, message } from 'antd';
import { ApiOutlined, BellOutlined, GithubOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { PageHeader } from '@/components/PageHeader';
import { healthApi } from '@/api';
import { fmtDuration } from '@/utils/time';
import { Logo } from '@/components/Logo';
import { useUiStore } from '@/store/ui';

const LICENCES = [
  { component: 'React 18, react-router, TanStack Query, zustand', licence: 'MIT' },
  { component: 'Ant Design 5, @ant-design/icons', licence: 'MIT' },
  { component: 'Leaflet, leaflet.markercluster (React bindings are in-repo, components/leaflet.tsx)', licence: 'BSD-2 / MIT' },
  { component: 'hls.js', licence: 'Apache-2.0' },
  { component: 'Recharts, dayjs, Vite, TypeScript', licence: 'MIT / Apache-2.0' },
  { component: 'Inter font (@fontsource/inter)', licence: 'SIL OFL 1.1' },
  { component: 'FastAPI, SQLAlchemy, Pydantic, uvicorn', licence: 'MIT' },
  { component: 'PostgreSQL 16 + PostGIS, pg_trgm, fuzzystrmatch', licence: 'PostgreSQL / GPL-2.0 (PostGIS)' },
  { component: 'MediaMTX', licence: 'MIT' },
  { component: 'FFmpeg (LGPL/GPL build noted in docs/LICENCES.md)', licence: 'LGPL-2.1+' },
  { component: 'PaddleOCR, open-image-models plate detector, YOLOX-s, onnxruntime', licence: 'Apache-2.0 / MIT' },
  { component: 'Caddy', licence: 'Apache-2.0' },
  { component: 'OpenStreetMap base tiles', licence: 'ODbL (data) · tile usage policy' },
];

export function AboutPage() {
  const hz = useQuery({ queryKey: ['healthz'], queryFn: healthApi.healthz, retry: 0 });
  const desktopNotify = useUiStore((s) => s.desktopNotify);

  const testNotification = async () => {
    if (!('Notification' in window)) {
      message.warning('This browser does not support desktop notifications');
      return;
    }
    let perm = Notification.permission;
    if (perm === 'default') perm = await Notification.requestPermission();
    if (perm !== 'granted') {
      message.info('Permission not granted');
      return;
    }
    const n = new Notification('CRITICAL · Test alert GJ 01 AB 1234', { body: 'Sentinel Gujarat · desktop notification test', icon: '/favicon.svg', tag: 'test' });
    setTimeout(() => n.close(), 6000);
    message.success('Test notification sent');
  };

  return (
    <div>
      <PageHeader title="About" description="Product, version, team, open-source components and integration surface." />
      <div className="sg-grid sg-grid-2">
        <Card>
          <Logo size={44} colour="#0B1F3A" />
          <Typography.Paragraph style={{ marginTop: 16 }}>
            Sentinel Gujarat is a unified CCTV registry, viewing and analytics platform built for the Gujarat Police Innovation Challenge 2026
            (CCTV Integration Hackathon). It implements <strong>Model 1</strong> (centralised registry + GIS) and <strong>Model 2</strong>{' '}
            (viewing and analytics: ANPR, watchlist correlation, route reconstruction, reports) with a documented roadmap to Models 3 and 4.
          </Typography.Paragraph>
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="Version">1.0.0-phase1 (git tag v1.0-phase1)</Descriptions.Item>
            <Descriptions.Item label="Team">Dynatech Consultancy - Category 2 (Company / Systems Integrator)</Descriptions.Item>
            <Descriptions.Item label="API status">
              {hz.data ? `${hz.data.status} · DB ${hz.data.db} · MediaMTX ${hz.data.mediamtx} · ${hz.data.anpr_workers} ANPR worker(s) · up ${fmtDuration(hz.data.uptime_s)}` : hz.isError ? 'unavailable' : 'checking…'}
            </Descriptions.Item>
            <Descriptions.Item label="Time zone">All times rendered in IST (Asia/Kolkata); stored in UTC</Descriptions.Item>
          </Descriptions>
          <Space style={{ marginTop: 16 }} wrap>
            <Button icon={<ApiOutlined />} href="/api/docs" target="_blank" rel="noreferrer">
              OpenAPI docs (/api/docs)
            </Button>
            <Button icon={<GithubOutlined />} href="/api/openapi.json" target="_blank" rel="noreferrer">
              openapi.json
            </Button>
            <Button icon={<BellOutlined />} onClick={() => void testNotification()}>
              Test desktop notification{desktopNotify ? '' : ' (enable first)'}
            </Button>
          </Space>
        </Card>
        <Card title="Open-source components" extra={<Typography.Text type="secondary">Full list in docs/LICENCES.md</Typography.Text>}>
          <Table size="small" pagination={false} rowKey="component" dataSource={LICENCES} columns={[{ title: 'Component', dataIndex: 'component' }, { title: 'Licence', dataIndex: 'licence', width: 200 }]} />
          <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0, fontSize: 12 }}>
            No proprietary VMS, SDK or cloud AI service is used in the demo path. The Ultralytics plate detector (AGPL-3.0) is a disclosed fallback only.
          </Typography.Paragraph>
        </Card>
      </div>
      <Card title="Integration surface" style={{ marginTop: 12 }}>
        <Descriptions column={2} size="small" bordered>
          <Descriptions.Item label="Bulk onboarding">POST /api/v1/cameras/bulk (X-API-Key, scope bulk)</Descriptions.Item>
          <Descriptions.Item label="Catalogue pull">Configurable host exposing GET /api/ingest (Settings → Catalogue)</Descriptions.Item>
          <Descriptions.Item label="CSV templates">Cameras and watchlist templates from the Import and Watchlist pages</Descriptions.Item>
          <Descriptions.Item label="Outbound webhooks">alert.created, alert.updated, camera.offline, camera.online, event.created (HMAC-SHA256 signed)</Descriptions.Item>
          <Descriptions.Item label="Streams">RTSP over TCP in; WebRTC (WHEP) and HLS out via an internal MediaMTX relay</Descriptions.Item>
          <Descriptions.Item label="Government systems">VAHAN and SARTHI mock adapters; eGujCop CSV mapping; AFIS/NAFIS roadmap</Descriptions.Item>
        </Descriptions>
      </Card>
    </div>
  );
}
