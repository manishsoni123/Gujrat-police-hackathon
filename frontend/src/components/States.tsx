/** Empty / error / loading states used by every page (CONTRACT §11.3). */
import type { ReactNode } from 'react';
import { Alert, Button, Card, Empty, Skeleton, Space, Typography } from 'antd';
import { ReloadOutlined, InboxOutlined } from '@ant-design/icons';
import { errorMessage } from '@/api/client';

interface EmptyStateProps {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  icon?: ReactNode;
  compact?: boolean;
}

export function EmptyState({ title, description, actions, icon, compact }: EmptyStateProps) {
  return (
    <div style={{ padding: compact ? '20px 12px' : '48px 16px', textAlign: 'center' }}>
      <Empty
        image={icon ?? <InboxOutlined style={{ fontSize: compact ? 36 : 48, color: '#CBD5E1' }} />}
        imageStyle={{ height: compact ? 44 : 60 }}
        description={
          <div>
            <Typography.Text strong style={{ fontSize: compact ? 14 : 15 }}>
              {title}
            </Typography.Text>
            {description ? (
              <Typography.Paragraph type="secondary" style={{ margin: '6px auto 0', maxWidth: 460 }}>
                {description}
              </Typography.Paragraph>
            ) : null}
          </div>
        }
      >
        {actions ? <Space wrap style={{ justifyContent: 'center', marginTop: 4 }}>{actions}</Space> : null}
      </Empty>
    </div>
  );
}

interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}

export function ErrorState({ error, onRetry, title = 'Could not load data' }: ErrorStateProps) {
  return (
    <Alert
      type="error"
      showIcon
      message={title}
      description={errorMessage(error)}
      action={
        onRetry ? (
          <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>
            Retry
          </Button>
        ) : undefined
      }
      style={{ marginBottom: 16 }}
    />
  );
}

/** Title + three card skeletons for first paint. */
export function PageSkeleton({ cards = 3 }: { cards?: number }) {
  return (
    <div>
      <Skeleton active paragraph={{ rows: 1, width: '40%' }} title={{ width: '25%' }} style={{ marginBottom: 12 }} />
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.min(cards, 3)}, 1fr)`, gap: 12 }}>
        {Array.from({ length: cards }).map((_, i) => (
          <Card key={i} size="small">
            <Skeleton active paragraph={{ rows: 3 }} />
          </Card>
        ))}
      </div>
    </div>
  );
}

export function InlineLoading({ rows = 3 }: { rows?: number }) {
  return <Skeleton active paragraph={{ rows }} />;
}
