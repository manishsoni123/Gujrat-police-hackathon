/** Vehicle search (/vehicles): normalised plate search, exact hits, fuzzy candidates with confirm/reject, Build route, VAHAN lookup. */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Input, Modal, Pagination, Segmented, Select, Space, Tag, Tooltip, Typography, message } from 'antd';
import { CarOutlined, CheckOutlined, CloseOutlined, NodeIndexOutlined, SearchOutlined, IdcardOutlined } from '@ant-design/icons';
import { PageHeader } from '@/components/PageHeader';
import { externalApi, vehiclesApi } from '@/api';
import type { SarthiLookup, SearchHit, VahanLookup } from '@/api/types';
import { CONFIRMATION_LABEL, MATCH_LABEL, fuzzyDescription } from '@/utils/labels';
import { normalisePlate, formatPlate } from '@/utils/plate';
import { PlateText } from '@/components/PlateText';
import { IstTime } from '@/components/IstTime';
import { CropThumb, ConfidenceBar } from '@/components/Misc';
import { StatusTag, DepartmentTag } from '@/components/Tags';
import { EmptyState, ErrorState } from '@/components/States';
import { RangeIst, type IsoRange } from '@/components/RangeIst';
import { useCameraOptions } from '@/hooks/useCamerasOptions';
import { usePermission } from '@/store/auth';
import { hoursAgoIso, nowIso } from '@/utils/time';
import { errorMessage } from '@/api/client';

function HitCard({ hit, onDecision, canConfirm }: { hit: SearchHit; onDecision?: (d: 'confirmed' | 'rejected') => void; canConfirm: boolean }) {
  const navigate = useNavigate();
  const fuzzy = hit.match === 'fuzzy';
  return (
    <Card size="small" style={{ borderLeft: `4px solid ${fuzzy ? (hit.confirmation === 'confirmed' ? '#16A34A' : hit.confirmation === 'rejected' ? '#9CA3AF' : '#D97706') : '#1E4DB7'}` }} styles={{ body: { padding: 12 } }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <CropThumb src={hit.best_crop_url} alt={`Best crop for ${hit.plate_display}`} width={150} height={48} sha256={hit.frame_sha256} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <Space size={6} wrap>
              <PlateText plate={hit.plate_norm} />
              {fuzzy ? (
                <Tooltip title={`Levenshtein distance ${hit.distance} · match score ${hit.score.toFixed(2)}`}>
                  <Tag color="orange" style={{ margin: 0 }}>{MATCH_LABEL.fuzzy} · {fuzzyDescription(hit.distance ?? 0, hit.score)}</Tag>
                </Tooltip>
              ) : (
                <Tag color="blue" style={{ margin: 0 }}>{MATCH_LABEL.exact}</Tag>
              )}
              {hit.confirmation ? <Tag color={hit.confirmation === 'confirmed' ? 'green' : 'default'} style={{ margin: 0 }}>{CONFIRMATION_LABEL[hit.confirmation] ?? hit.confirmation}</Tag> : null}
            </Space>
            <span style={{ fontSize: 12, color: '#6B7280' }}>
              <IstTime value={hit.first_seen} /> → <IstTime value={hit.last_seen} /> · {hit.read_count} reads
            </span>
          </div>
          <div style={{ marginTop: 6, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <Button type="link" style={{ padding: 0, fontWeight: 600 }} onClick={() => navigate(`/cameras/${hit.camera.id}`)}>
              {hit.camera.name}
            </Button>
            <StatusTag status={hit.camera.status} size="small" />
            <DepartmentTag code={hit.camera.department_code} name={hit.camera.department_name} size="small" />
            <span style={{ fontSize: 12, color: '#6B7280' }}>{hit.camera.district ?? '—'}</span>
            <ConfidenceBar value={hit.best_conf} width={60} />
          </div>
          {fuzzy && hit.sample_reads?.length ? (
            <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {hit.sample_reads.map((r) => (
                <div key={r.id} style={{ fontSize: 11, color: '#4B5563', textAlign: 'center' }}>
                  <CropThumb src={r.crop_url} alt={`Raw read ${r.plate_raw}`} width={96} height={32} preview={false} />
                  <div><code>{r.plate_raw}</code> · {Math.round(r.confidence * 100)} %</div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
        {fuzzy && canConfirm && onDecision ? (
          <Segmented
            size="small"
            value={hit.confirmation ?? 'pending'}
            options={[
              { value: 'confirmed', icon: <CheckOutlined />, label: 'Confirm' },
              { value: 'rejected', icon: <CloseOutlined />, label: 'Reject' },
            ]}
            onChange={(v) => onDecision(v as 'confirmed' | 'rejected')}
          />
        ) : null}
      </div>
    </Card>
  );
}

function VahanModal({ plate, open, onClose }: { plate: string; open: boolean; onClose: () => void }) {
  const q = useQuery({ queryKey: ['vahan', plate], queryFn: () => externalApi.vahan(plate), enabled: open && Boolean(plate) });
  const v: VahanLookup | undefined = q.data;
  return (
    <Modal open={open} onCancel={onClose} footer={null} title={<Space><IdcardOutlined /> Owner lookup · {formatPlate(plate)}</Space>} destroyOnClose>
      {q.isLoading ? <Typography.Text type="secondary">Querying the VAHAN adapter…</Typography.Text> : null}
      {q.isError ? <Alert type="error" showIcon message={errorMessage(q.error)} /> : null}
      {v ? (
        <div>
          <Alert type="info" showIcon message={v.source} description={v.note} style={{ marginBottom: 12 }} />
          {v.found ? (
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="Owner">{v.owner_name}</Descriptions.Item>
              <Descriptions.Item label="Vehicle">{v.maker_model} · {v.vehicle_class} · {v.colour} · {v.fuel}</Descriptions.Item>
              <Descriptions.Item label="Registered">{v.registration_date} · {v.rto}</Descriptions.Item>
              <Descriptions.Item label="Insurance valid till">{v.insurance_valid_till}</Descriptions.Item>
              <Descriptions.Item label="Fitness valid till">{v.fitness_valid_till}</Descriptions.Item>
            </Descriptions>
          ) : (
            <EmptyState compact title="No record for this registration" description="The mock adapter returns 'not found' for one plate in five to exercise the empty path." />
          )}
        </div>
      ) : null}
    </Modal>
  );
}

function SarthiModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [input, setInput] = useState('');
  const [dl, setDl] = useState('');
  const q = useQuery({ queryKey: ['sarthi', dl], queryFn: () => externalApi.sarthi(dl), enabled: open && Boolean(dl) });
  const v: SarthiLookup | undefined = q.data;
  const submit = () => setDl(input.replace(/[\s-]/g, '').toUpperCase());
  return (
    <Modal open={open} onCancel={onClose} footer={null} title={<Space><IdcardOutlined /> Driving-licence lookup (SARTHI)</Space>} destroyOnClose>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Looks a licence number up through the SARTHI adapter (holder, validity, vehicle classes). The hosted demo answers from the mock adapter; the real NIC gateway plugs into the same adapter interface.
      </Typography.Paragraph>
      <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
        <Input placeholder="Licence number, e.g. GJ01 20200012345" value={input} onChange={(e) => setInput(e.target.value)} onPressEnter={submit} autoFocus aria-label="Driving licence number" style={{ fontFamily: 'ui-monospace, monospace' }} />
        <Button type="primary" icon={<SearchOutlined />} onClick={submit} loading={q.isFetching} disabled={!input.trim()}>
          Lookup
        </Button>
      </Space.Compact>
      {q.isError ? <Alert type="error" showIcon message={errorMessage(q.error)} /> : null}
      {v ? (
        <div>
          <Alert type="info" showIcon message={v.source} description={v.note} style={{ marginBottom: 12 }} />
          {v.found ? (
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="Licence">{v.dl_number}</Descriptions.Item>
              <Descriptions.Item label="Holder">{v.holder_name}{v.dob ? ` · born ${v.dob}` : ''}</Descriptions.Item>
              <Descriptions.Item label="Valid">{v.valid_from} → {v.valid_till}</Descriptions.Item>
              <Descriptions.Item label="Classes">{(v.classes ?? []).join(', ') || '—'}</Descriptions.Item>
              <Descriptions.Item label="Issued by">{v.rto}</Descriptions.Item>
              <Descriptions.Item label="Status">
                <Tag color={v.status === 'active' ? 'green' : v.status === 'suspended' ? 'red' : 'default'} style={{ margin: 0 }}>{v.status ?? '—'}</Tag>
              </Descriptions.Item>
            </Descriptions>
          ) : (
            <EmptyState compact title="No record for this licence number" description="The mock adapter returns 'not found' for one number in five to exercise the empty path." />
          )}
        </div>
      ) : null}
    </Modal>
  );
}

const EXACT_PAGE = 10;
const SEARCH_LIMIT = 100;

export function VehicleSearchPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [sp] = useSearchParams();
  const canConfirm = usePermission('route.confirm');
  const canLookup = usePermission('external.lookup');
  const { options: cameraOptions } = useCameraOptions();
  const [input, setInput] = useState(sp.get('q') ?? '');
  const [submitted, setSubmitted] = useState(sp.get('q') ?? '');
  const [range, setRange] = useState<IsoRange>({ from: hoursAgoIso(2), to: nowIso() });
  const [cameraId, setCameraId] = useState<number | undefined>();
  const [vahanOpen, setVahanOpen] = useState(false);
  const [sarthiOpen, setSarthiOpen] = useState(false);
  const [exactPage, setExactPage] = useState(1);

  const preview = useMemo(() => (input.trim() ? normalisePlate(input) : null), [input]);
  const norm = useMemo(() => (submitted.trim() ? normalisePlate(submitted) : null), [submitted]);
  const search = useQuery({
    queryKey: ['vehicles', 'search', norm?.plate_norm, range, cameraId],
    queryFn: () => vehiclesApi.search({ q: submitted, from: range.from, to: range.to, camera_id: cameraId, limit: SEARCH_LIMIT }),
    enabled: Boolean(norm?.plate_norm),
  });
  useEffect(() => {
    document.title = 'Vehicle search · Sentinel Gujarat';
  }, []);

  const confirm = useMutation({
    mutationFn: (d: { sighting_id: number; decision: 'confirmed' | 'rejected' }) => vehiclesApi.confirm(norm?.plate_norm ?? '', [d]),
    onSuccess: (_r, d) => {
      message.success(`Sighting #${d.sighting_id} ${d.decision}`);
      qc.invalidateQueries({ queryKey: ['vehicles'] });
    },
  });

  const submit = () => setSubmitted(input);
  const buildRoute = () => {
    if (!norm) return;
    const q = new URLSearchParams();
    if (range.from) q.set('from', range.from);
    if (range.to) q.set('to', range.to);
    navigate(`/vehicles/${encodeURIComponent(norm.plate_norm)}/route?${q.toString()}`);
  };
  const r = search.data;
  const pending = r?.fuzzy.filter((h) => !h.confirmation).length ?? 0;
  const exactCapped = (r?.exact.length ?? 0) >= SEARCH_LIMIT;
  const exactPageItems = useMemo(() => (r?.exact ?? []).slice((exactPage - 1) * EXACT_PAGE, exactPage * EXACT_PAGE), [r, exactPage]);
  useEffect(() => setExactPage(1), [r]);

  return (
    <div>
      <PageHeader title="Vehicle search" description="Plates are normalised (spaces, hyphens, IND prefix, OCR confusions) before an exact search on sightings and a near-match search on raw reads. Confirm or reject near matches, then build the route. Default window: the last 2 hours." />
      <Card style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <div style={{ flex: '1 1 340px' }}>
            <Input
              size="large"
              prefix={<CarOutlined style={{ color: '#9CA3AF' }} />}
              placeholder="Enter a registration, e.g. GJ 01 AB 1234"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onPressEnter={submit}
              allowClear
              autoFocus
              aria-label="Registration number"
              style={{ fontFamily: 'ui-monospace, monospace', letterSpacing: 1 }}
            />
            <div style={{ fontSize: 12, color: '#6B7280', marginTop: 6, minHeight: 18 }}>
              {preview ? (
                <span>
                  Will search for <PlateText plate={preview.plate_norm} size="small" invalid={!preview.is_valid_format} />{' '}
                  {preview.is_valid_format ? <span style={{ color: '#16A34A' }}>{preview.pattern === 'bh' ? 'BH series' : 'standard format'}{preview.substitutions ? ` · ${preview.substitutions} confusion fix${preview.substitutions > 1 ? 'es' : ''}` : ''}</span> : <span style={{ color: '#D97706' }}>not a valid Indian format - searched as typed</span>}
                </span>
              ) : (
                'Exact matches come from sightings; near matches come from raw reads that differ by up to two characters (or look very similar), so an OCR slip does not hide the vehicle.'
              )}
            </div>
          </div>
          <RangeIst value={range} onChange={setRange} />
          <Select allowClear showSearch optionFilterProp="label" placeholder="Any camera" style={{ width: 220 }} size="large" options={cameraOptions} value={cameraId} onChange={setCameraId} aria-label="Camera" />
          <Button type="primary" size="large" icon={<SearchOutlined />} onClick={submit} loading={search.isFetching} disabled={!preview}>
            Search
          </Button>
        </div>
      </Card>

      {search.isError ? <ErrorState error={search.error} onRetry={() => void search.refetch()} /> : null}
      {!norm ? (
        <Card>
          <EmptyState title="Enter a registration number to begin" description="Try GJ 01 AB 1234 - the seeded stolen vehicle that crosses four Gandhinagar cameras every loop." icon={<CarOutlined style={{ fontSize: 44, color: '#CBD5E1' }} />} />
        </Card>
      ) : null}
      {r ? (
        <div>
          <Card size="small" style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
              <Space wrap>
                <PlateText plate={r.query.normalised} size="large" invalid={!r.query.is_valid_format} />
                <span style={{ color: '#4B5563' }}>
                  {exactCapped ? <Tooltip title={`The search returns the ${SEARCH_LIMIT} most recent exact sightings; narrow the window or pick a camera to see the rest.`}><strong>first {SEARCH_LIMIT}</strong></Tooltip> : <strong>{r.exact.length}</strong>} exact sighting{r.exact.length === 1 ? '' : 's'} on <strong>{r.cameras_seen}</strong> camera{r.cameras_seen === 1 ? '' : 's'} · <strong>{r.fuzzy.length}</strong> near match{r.fuzzy.length === 1 ? '' : 'es'}{pending ? ` (${pending} awaiting decision)` : ''}
                </span>
                <span style={{ fontSize: 12, color: '#6B7280' }}>
                  <IstTime value={r.query.from} /> → <IstTime value={r.query.to} />
                </span>
              </Space>
              <Space>
                {canLookup ? (
                  <Button icon={<IdcardOutlined />} onClick={() => setVahanOpen(true)}>
                    Lookup owner (VAHAN)
                  </Button>
                ) : null}
                {canLookup ? (
                  <Button icon={<IdcardOutlined />} onClick={() => setSarthiOpen(true)}>
                    Lookup licence (SARTHI)
                  </Button>
                ) : null}
                <Button type="primary" icon={<NodeIndexOutlined />} onClick={buildRoute} disabled={!r.exact.length && !r.fuzzy.some((h) => h.confirmation === 'confirmed')}>
                  Build route
                </Button>
              </Space>
            </div>
          </Card>
          <div className="sg-grid sg-grid-2" style={{ alignItems: 'start' }}>
            <Card size="small" title={exactCapped ? `Exact matches (first ${SEARCH_LIMIT})` : `Exact matches (${r.exact.length})`} extra={r.exact.length > EXACT_PAGE ? <Pagination simple size="small" current={exactPage} pageSize={EXACT_PAGE} total={r.exact.length} onChange={setExactPage} /> : null} styles={{ body: { display: 'grid', gap: 8 } }}>
              {r.exact.length ? exactPageItems.map((h) => <HitCard key={h.id} hit={h} canConfirm={false} />) : <EmptyState compact title="No exact sightings in this window" description="Widen the window or check the near matches on the right." />}
              {exactCapped ? <Typography.Text type="secondary" style={{ fontSize: 12 }}>Showing the {SEARCH_LIMIT} most recent exact sightings. Narrow the time window or choose a camera to see older ones; the route uses the full window.</Typography.Text> : null}
            </Card>
            <Card size="small" title={`Near matches (${r.fuzzy.length})`} extra={<span style={{ fontSize: 12, color: '#6B7280' }}>{canConfirm ? 'Decisions are audited and drive the route' : 'Read-only'}</span>} styles={{ body: { display: 'grid', gap: 8 } }}>
              {r.fuzzy.length ? (
                r.fuzzy.map((h) => <HitCard key={h.id} hit={h} canConfirm={canConfirm} onDecision={(d) => confirm.mutate({ sighting_id: h.id, decision: d })} />)
              ) : (
                <EmptyState compact title="No near matches" description="No raw read in this window is within two characters of the plate you searched." />
              )}
            </Card>
          </div>
          {norm ? <VahanModal plate={norm.plate_norm} open={vahanOpen} onClose={() => setVahanOpen(false)} /> : null}
          <SarthiModal open={sarthiOpen} onClose={() => setSarthiOpen(false)} />
        </div>
      ) : null}
    </div>
  );
}
