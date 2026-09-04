/** Ctrl+K command palette: plate search (with normalisation preview), camera jump, alert jump. */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Input, List, Modal, Space, Tag, Typography } from 'antd';
import { CarOutlined, SearchOutlined, VideoCameraOutlined, AlertOutlined } from '@ant-design/icons';
import { useUiStore } from '@/store/ui';
import { normalisePlate, formatPlate } from '@/utils/plate';
import { useCameraOptions } from '@/hooks/useCamerasOptions';
import { StatusTag } from './Tags';

interface Item {
  key: string;
  icon: React.ReactNode;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  to: string;
}

export function GlobalSearch() {
  const open = useUiStore((s) => s.searchOpen);
  const setOpen = useUiStore((s) => s.setSearchOpen);
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [active, setActive] = useState(0);
  const { cameras } = useCameraOptions();

  useEffect(() => {
    if (!open) {
      setQ('');
      setActive(0);
    }
  }, [open]);

  const items = useMemo<Item[]>(() => {
    const out: Item[] = [];
    const text = q.trim();
    if (!text) return out;
    const norm = normalisePlate(text);
    if (norm.plate_norm.length >= 4) {
      out.push({
        key: 'plate',
        icon: <CarOutlined />,
        title: (
          <Space>
            <span>Search vehicle</span>
            <Tag className="sg-mono" style={{ margin: 0 }}>
              {formatPlate(norm.plate_norm)}
            </Tag>
            {!norm.is_valid_format ? <Tag color="orange" style={{ margin: 0 }}>partial / invalid format</Tag> : null}
          </Space>
        ),
        subtitle: norm.substitutions ? `${norm.substitutions} OCR confusion fix${norm.substitutions > 1 ? 'es' : ''} applied` : 'Exact and fuzzy matches over the last 24 h',
        to: `/vehicles?q=${encodeURIComponent(norm.plate_norm)}`,
      });
    }
    const idMatch = /^#?(\d+)$/.exec(text);
    if (idMatch) {
      out.push({ key: 'alert', icon: <AlertOutlined />, title: `Open alert #${idMatch[1]}`, to: `/alerts?id=${idMatch[1]}` });
    }
    const lower = text.toLowerCase();
    cameras
      .filter((c) => c.name.toLowerCase().includes(lower) || c.external_id.toLowerCase() === lower || (c.district ?? '').toLowerCase().includes(lower))
      .slice(0, 6)
      .forEach((c) =>
        out.push({
          key: `cam-${c.id}`,
          icon: <VideoCameraOutlined />,
          title: (
            <Space>
              <span>{c.name}</span>
              <StatusTag status={c.status} size="small" />
            </Space>
          ),
          subtitle: `#${c.external_id} · ${c.department_name} · ${c.district ?? '—'}`,
          to: `/cameras/${c.id}`,
        }),
      );
    return out;
  }, [q, cameras]);

  const go = (it: Item) => {
    setOpen(false);
    navigate(it.to);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, items.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === 'Enter' && items[active]) {
      go(items[active]);
    }
  };

  return (
    <Modal open={open} onCancel={() => setOpen(false)} footer={null} closable={false} width={620} style={{ top: 80 }} destroyOnClose styles={{ body: { padding: 0 } }}>
      <div style={{ padding: 12, borderBottom: '1px solid #E5E7EB' }}>
        <Input
          autoFocus
          size="large"
          prefix={<SearchOutlined style={{ color: '#9CA3AF' }} />}
          placeholder="Type a plate (GJ 01 AB 1234), a camera name or an alert id…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKey}
          bordered={false}
          aria-label="Global search"
        />
      </div>
      {items.length ? (
        <List
          dataSource={items}
          renderItem={(it, i) => (
            <List.Item
              onClick={() => go(it)}
              onMouseEnter={() => setActive(i)}
              style={{ cursor: 'pointer', padding: '10px 16px', background: i === active ? '#EEF3FF' : undefined }}
            >
              <List.Item.Meta avatar={<span style={{ fontSize: 18, color: '#1E4DB7' }}>{it.icon}</span>} title={it.title} description={it.subtitle} />
            </List.Item>
          )}
        />
      ) : (
        <div style={{ padding: '18px 16px', color: '#6B7280', fontSize: 13 }}>
          <Typography.Text type="secondary">
            Plates are normalised automatically (spaces, hyphens and common OCR confusions). Use <kbd className="sg-kbd">↑</kbd> <kbd className="sg-kbd">↓</kbd> and <kbd className="sg-kbd">Enter</kbd>.
          </Typography.Text>
        </div>
      )}
    </Modal>
  );
}
