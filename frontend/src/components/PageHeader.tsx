import { useEffect, type ReactNode } from 'react';
import { Typography, Breadcrumb } from 'antd';
import { Link } from 'react-router-dom';

interface PageHeaderProps {
  title: string;
  description?: ReactNode;
  extra?: ReactNode;
  breadcrumb?: { label: string; to?: string }[];
  /** Browser tab title; defaults to `title`. */
  docTitle?: string;
}

/** Consistent page header: title (20px/600), one-line description, primary action on the right. */
export function PageHeader({ title, description, extra, breadcrumb, docTitle }: PageHeaderProps) {
  useEffect(() => {
    document.title = `${docTitle ?? title} · Sentinel Gujarat`;
  }, [title, docTitle]);
  return (
    <div className="sg-page-header">
      <div style={{ minWidth: 0 }}>
        {breadcrumb ? (
          <Breadcrumb
            style={{ marginBottom: 4, fontSize: 12 }}
            items={breadcrumb.map((b) => ({ title: b.to ? <Link to={b.to}>{b.label}</Link> : b.label }))}
          />
        ) : null}
        <Typography.Title level={3} style={{ margin: 0, fontSize: 20, fontWeight: 600, lineHeight: '28px' }}>
          {title}
        </Typography.Title>
        {description ? (
          <Typography.Text type="secondary" style={{ display: 'block', marginTop: 2 }}>
            {description}
          </Typography.Text>
        ) : null}
      </div>
      {extra ? <div className="sg-page-header-extra">{extra}</div> : null}
    </div>
  );
}
