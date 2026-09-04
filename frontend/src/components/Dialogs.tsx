/** ConfirmDialog (destructive actions) and ExportDialog (purpose-limitation notice + hash result). */
import { useState, type ReactNode } from 'react';
import { Alert, Button, Descriptions, Modal, Radio, Space, Typography, message } from 'antd';
import { CopyOutlined, DownloadOutlined, ExclamationCircleOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import type { DownloadResult } from '@/api/client';
import { errorMessage } from '@/api/client';
import { fmtBytes } from '@/utils/format';

export const PURPOSE_NOTICE =
  "Exports contain personal data (vehicle registrations, images). Use is limited to the investigation or administrative purpose stated in your department's SOP. Every export is logged with your username and hash.";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  content?: ReactNode;
  okText?: string;
  danger?: boolean;
  loading?: boolean;
  onOk: () => void | Promise<void>;
  onCancel: () => void;
}

export function ConfirmDialog({ open, title, content, okText = 'Confirm', danger = true, loading, onOk, onCancel }: ConfirmDialogProps) {
  return (
    <Modal
      open={open}
      title={
        <Space>
          <ExclamationCircleOutlined style={{ color: danger ? '#DC2626' : '#D97706' }} />
          {title}
        </Space>
      }
      okText={okText}
      okButtonProps={{ danger, loading }}
      onOk={onOk}
      onCancel={onCancel}
      destroyOnClose
    >
      {content}
    </Modal>
  );
}

export type ExportFormat = 'csv' | 'pdf';

interface ExportDialogProps {
  open: boolean;
  title: string;
  /** Human summary of the filters that will be applied. */
  filters: { label: string; value: ReactNode }[];
  formats?: ExportFormat[];
  onExport: (format: ExportFormat) => Promise<DownloadResult>;
  onClose: () => void;
}

/** Every export goes through this dialog: notice, filter summary, format choice, then file name + SHA-256. */
export function ExportDialog({ open, title, filters, formats = ['csv', 'pdf'], onExport, onClose }: ExportDialogProps) {
  const [format, setFormat] = useState<ExportFormat>(formats[0]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DownloadResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await onExport(format);
      setResult(r);
      message.success(`Exported ${r.filename}`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const close = () => {
    setResult(null);
    setError(null);
    onClose();
  };

  const copyHash = async () => {
    if (!result?.sha256) return;
    try {
      await navigator.clipboard.writeText(result.sha256);
      message.success('SHA-256 copied');
    } catch {
      message.warning('Clipboard unavailable');
    }
  };

  return (
    <Modal
      open={open}
      title={
        <Space>
          <DownloadOutlined />
          {title}
        </Space>
      }
      onCancel={close}
      destroyOnClose
      footer={
        result ? (
          <Button type="primary" onClick={close}>
            Done
          </Button>
        ) : (
          <Space>
            <Button onClick={close}>Cancel</Button>
            <Button type="primary" icon={<DownloadOutlined />} loading={busy} onClick={run}>
              Export {format.toUpperCase()}
            </Button>
          </Space>
        )
      }
    >
      <Alert type="warning" showIcon message="Purpose limitation" description={PURPOSE_NOTICE} style={{ marginBottom: 16 }} />
      <Descriptions size="small" column={1} bordered items={filters.map((f, i) => ({ key: i, label: f.label, children: f.value }))} />
      {!result ? (
        <div style={{ marginTop: 16 }}>
          <Typography.Text strong>Format</Typography.Text>
          <div style={{ marginTop: 6 }}>
            <Radio.Group value={format} onChange={(e) => setFormat(e.target.value as ExportFormat)} optionType="button" buttonStyle="solid">
              {formats.map((f) => (
                <Radio.Button key={f} value={f}>
                  {f.toUpperCase()}
                </Radio.Button>
              ))}
            </Radio.Group>
          </div>
        </div>
      ) : (
        <Alert
          type="success"
          showIcon
          icon={<SafetyCertificateOutlined />}
          style={{ marginTop: 16 }}
          message={`Saved ${result.filename} (${fmtBytes(result.size)})`}
          description={
            <div>
              <div style={{ fontSize: 12, color: '#6B7280' }}>SHA-256 (evidence hash)</div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <code style={{ fontSize: 12, wordBreak: 'break-all' }}>{result.sha256 ?? 'not provided by server'}</code>
                {result.sha256 ? <Button size="small" icon={<CopyOutlined />} onClick={copyHash} /> : null}
              </div>
            </div>
          }
        />
      )}
      {error ? <Alert type="error" showIcon message={error} style={{ marginTop: 12 }} /> : null}
    </Modal>
  );
}
