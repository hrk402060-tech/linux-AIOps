import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Empty, Input, Modal, Select, Space, Table, Tag, Tooltip, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Archive, Boxes, Eye, Pencil, Plus, Power, PowerOff, RefreshCw, RotateCcw, Search } from 'lucide-react';

import type { AuthUser } from '../api/auth';
import { bindServiceAsset, confirmCmdbDiscovery, createCmdbEntity, getCmdbDiscoveryPreview, getCmdbTree, updateCmdbEntity, updateServiceAsset, type CmdbDiscoveryResponse } from '../api/cmdb';
import { getAssets } from '../api/assets';
import { CmdbDetailDrawer } from '../components/CmdbDetailDrawer';
import { CmdbEntityModal } from '../components/CmdbEntityModal';
import type { Asset } from '../types/asset';
import type { CmdbApplicationTree, CmdbBusinessTree, CmdbEntityPayload, CmdbEntityRow, CmdbEntityType, CmdbLifecycleStatus, CmdbServiceAsset, CmdbServiceTree } from '../types/cmdb';

const typeLabels = { business: '业务系统', application: '应用', service: '服务', api: 'API' };
const lifecycleLabels = { active: '运行中', disabled: '已停用', archived: '已归档' };
const lifecycleColors = { active: 'green', disabled: 'orange', archived: 'default' };

function flattenTree(businesses: CmdbBusinessTree[]): CmdbEntityRow[] {
  const rows: CmdbEntityRow[] = [];
  businesses.forEach((business) => {
    rows.push({ key: `business-${business.id}`, type: 'business', entity: business, business, depth: 0 });
    business.applications.forEach((application) => {
      rows.push({ key: `application-${application.id}`, type: 'application', entity: application, business, application, depth: 1 });
      application.services.forEach((service) => {
        rows.push({ key: `service-${service.id}`, type: 'service', entity: service, business, application, service, depth: 2 });
        service.apis.forEach((api) => rows.push({ key: `api-${api.id}`, type: 'api', entity: api, business, application, service, api, depth: 3 }));
      });
    });
  });
  return rows;
}

export function CmdbPage({ currentUser, initialServiceId }: { currentUser: AuthUser; initialServiceId?: string }) {
  const [businesses, setBusinesses] = useState<CmdbBusinessTree[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [lifecycle, setLifecycle] = useState<CmdbLifecycleStatus | 'all'>('active');
  const [query, setQuery] = useState('');
  const [businessId, setBusinessId] = useState<string>();
  const [applicationId, setApplicationId] = useState<string>();
  const [serviceId, setServiceId] = useState<string | undefined>(initialServiceId);
  const [assetId, setAssetId] = useState<string>();
  const [modalType, setModalType] = useState<CmdbEntityType | null>(null);
  const [editing, setEditing] = useState<CmdbEntityRow | null>(null);
  const [selected, setSelected] = useState<CmdbEntityRow | null>(null);
  const [discovery, setDiscovery] = useState<CmdbDiscoveryResponse | null>(null);
  const [discoveryLoading, setDiscoveryLoading] = useState(false);
  const [discoveryTarget, setDiscoveryTarget] = useState<{ assetId: string; assetName: string; serviceName: string; source: 'systemd' | 'container' } | null>(null);
  const [discoveryApplicationId, setDiscoveryApplicationId] = useState<string>();
  const [discoveryConfirming, setDiscoveryConfirming] = useState(false);
  const [messages, holder] = message.useMessage();
  const canWrite = currentUser.role !== 'auditor';
  const isAdmin = currentUser.role === 'admin';
  const load = async () => {
    setLoading(true);
    setLoadError('');
    try { const [tree, serverAssets] = await Promise.all([getCmdbTree(lifecycle), getAssets('enabled')]); setBusinesses(tree.businesses); setAssets(serverAssets); }
    catch (error) { const text = error instanceof Error ? error.message : '服务目录加载失败'; setLoadError(text); messages.error(text); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [lifecycle]);
  const applications = useMemo(() => businesses.flatMap((item) => item.applications), [businesses]);
  const services = useMemo(() => applications.flatMap((item) => item.services), [applications]);
  const rows = useMemo(() => flattenTree(businesses).filter((row) => {
    const text = `${row.entity.name} ${row.entity.code} ${row.entity.description}`.toLowerCase();
    const hasAsset = !assetId || Boolean(row.service?.assets.some((relation) => relation.status === 'active' && relation.asset_id === assetId));
    return (!query.trim() || text.includes(query.trim().toLowerCase())) && (!businessId || row.business.id === businessId)
      && (!applicationId || row.application?.id === applicationId) && (!serviceId || row.service?.id === serviceId) && hasAsset;
  }), [applicationId, assetId, businessId, businesses, query, serviceId]);
  const openCreate = (type: CmdbEntityType) => { setEditing(null); setModalType(type); };
  const openEdit = (row: CmdbEntityRow) => { setEditing(row); setModalType(row.type); };
  const save = async (type: CmdbEntityType, payload: CmdbEntityPayload) => {
    setSubmitting(true);
    try {
      if (editing) await updateCmdbEntity(type, editing.entity.id, payload); else await createCmdbEntity(type, payload);
      messages.success(editing ? '配置项已更新' : '配置项已创建'); setModalType(null); setEditing(null); await load();
    } catch (error) { messages.error(error instanceof Error ? error.message : '保存失败'); }
    finally { setSubmitting(false); }
  };
  const changeLifecycle = (row: CmdbEntityRow, status: CmdbLifecycleStatus) => Modal.confirm({
    title: `${status === 'archived' ? '归档' : status === 'disabled' ? '停用' : '恢复'} ${row.entity.name}`,
    content: status === 'archived' ? '归档会保留配置和关系历史，不会物理删除。' : '状态变化将写入统一审计。',
    okText: '确认', onOk: async () => { await updateCmdbEntity(row.type, row.entity.id, { lifecycle_status: status, lifecycle_reason: '配置项管理页面操作' }); await load(); },
  });
  const bindAsset = async (id: string, targetAssetId: string, role: string) => { await bindServiceAsset(id, targetAssetId, role); messages.success('服务器已绑定'); setSelected(null); await load(); };
  const unbindAsset = async (relation: CmdbServiceAsset) => { await updateServiceAsset(relation.id, 'unlinked'); messages.success('服务器关系已解除，历史记录已保留'); setSelected(null); await load(); };
  const restoreAsset = async (relation: CmdbServiceAsset) => { await updateServiceAsset(relation.id, 'active'); messages.success('服务器关系已恢复'); setSelected(null); await load(); };
  const openDiscovery = async () => {
    setDiscoveryLoading(true);
    try { setDiscovery(await getCmdbDiscoveryPreview()); }
    catch (error) { messages.error(error instanceof Error ? error.message : '自动发现预览加载失败'); }
    finally { setDiscoveryLoading(false); }
  };
  const confirmDiscovery = async () => {
    if (!discoveryTarget || !discoveryApplicationId) return;
    setDiscoveryConfirming(true);
    try {
      await confirmCmdbDiscovery({
        application_id: discoveryApplicationId,
        asset_id: discoveryTarget.assetId,
        service_name: discoveryTarget.serviceName,
        source: discoveryTarget.source,
      });
      messages.success('发现服务已纳入配置项管理');
      setDiscoveryTarget(null);
      setDiscoveryApplicationId(undefined);
      setDiscovery(null);
      await load();
    } catch (error) { messages.error(error instanceof Error ? error.message : '自动发现确认失败'); }
    finally { setDiscoveryConfirming(false); }
  };
  const columns: ColumnsType<CmdbEntityRow> = [
    { title: '配置项', render: (_, row) => <div className="cmdb-identity" style={{ paddingLeft: row.depth * 18 }}><span><Boxes size={16} /></span><div><strong>{row.entity.name}</strong><small>{row.entity.code}</small></div></div> },
    { title: '类型', dataIndex: 'type', width: 100, render: (value: CmdbEntityType) => <Tag>{typeLabels[value]}</Tag> },
    { title: '所属路径', width: 260, render: (_, row) => [row.business.name, row.application?.name, row.service && row.type === 'api' ? row.service.name : null].filter(Boolean).join(' / ') || '-' },
    { title: '服务 / 接口', width: 180, render: (_, row) => row.type === 'service' ? <span>{row.service?.service_type} · {row.service?.assets.filter((item) => item.status === 'active').length ?? 0} 台服务器</span> : row.api ? <code>{row.api.http_method} {row.api.path}</code> : '-' },
    { title: '状态', width: 100, render: (_, row) => <Tag color={lifecycleColors[row.entity.lifecycle_status]}>{lifecycleLabels[row.entity.lifecycle_status]}</Tag> },
    { title: '操作', width: 210, align: 'right', render: (_, row) => <Space size={2}><Tooltip title="查看详情"><Button type="text" aria-label={`查看${typeLabels[row.type]} ${row.entity.name}`} icon={<Eye size={16} />} onClick={() => setSelected(row)} /></Tooltip>{canWrite ? <Tooltip title="编辑"><Button type="text" aria-label={`编辑${row.entity.name}`} icon={<Pencil size={16} />} onClick={() => openEdit(row)} /></Tooltip> : null}{canWrite && row.entity.lifecycle_status !== 'archived' ? <Tooltip title={row.entity.lifecycle_status === 'active' ? '停用' : '启用'}><Button type="text" icon={row.entity.lifecycle_status === 'active' ? <PowerOff size={16} /> : <Power size={16} />} aria-label={`${row.entity.lifecycle_status === 'active' ? '停用' : '启用'}${row.entity.name}`} onClick={() => changeLifecycle(row, row.entity.lifecycle_status === 'active' ? 'disabled' : 'active')} /></Tooltip> : null}{isAdmin ? row.entity.lifecycle_status === 'archived' ? <Tooltip title="恢复"><Button type="text" icon={<RotateCcw size={16} />} aria-label={`恢复${row.entity.name}`} onClick={() => changeLifecycle(row, 'active')} /></Tooltip> : <Tooltip title="归档"><Button type="text" icon={<Archive size={16} />} aria-label={`归档${row.entity.name}`} onClick={() => changeLifecycle(row, 'archived')} /></Tooltip> : null}</Space> },
  ];
  return <section className="cmdb-page">{holder}<div className="page-heading"><div><p className="eyebrow">业务服务建模与资产关系</p><h1>配置项管理</h1></div><Space className="cmdb-heading-actions"><Button aria-label="自动发现预览" loading={discoveryLoading} onClick={() => void openDiscovery()}>自动发现预览</Button><Button aria-label="刷新配置项管理" icon={<RefreshCw size={17} />} loading={loading} onClick={() => void load()} />{canWrite ? <><Button type="primary" icon={<Plus size={17} />} aria-label="新建业务系统" onClick={() => openCreate('business')}>新建业务</Button><Button onClick={() => openCreate('application')}>新建应用</Button><Button onClick={() => openCreate('service')}>新建服务</Button><Button onClick={() => openCreate('api')}>新建 API</Button></> : <Tag color="blue">审计员只读</Tag>}</Space></div>
    {loadError ? <Alert type="error" showIcon message="配置项数据读取失败" description={loadError} action={<Button onClick={() => void load()}>重试</Button>} /> : null}
    {!loading && !loadError && !businesses.length ? <Alert type="info" showIcon message="配置项管理还没有编目数据" description="先创建业务系统，再补充应用、服务和 API；服务绑定服务器后，监控、告警和漏洞才能形成影响关系。" action={canWrite ? <Button type="link" onClick={() => openCreate('business')}>开始建模</Button> : null} /> : null}
    <div hidden={Boolean(loadError)} className={`cmdb-data-shell${loading ? ' is-loading' : ''}`}>
    <div className={`metric-band cmdb-metrics${loading ? ' data-shell-loading' : ''}`}><div><span>业务系统</span><strong>{loading ? '—' : businesses.length}</strong></div><div><span>应用</span><strong>{loading ? '—' : applications.length}</strong></div><div><span>服务</span><strong>{loading ? '—' : services.length}</strong></div><div><span>API</span><strong>{loading ? '—' : services.reduce((sum, service) => sum + service.apis.length, 0)}</strong></div></div>
    <div className="cmdb-toolbar"><Input allowClear prefix={<Search size={15} />} placeholder="搜索名称、编码或说明" value={query} onChange={(event) => setQuery(event.target.value)} /><Select aria-label="生命周期" value={lifecycle} onChange={setLifecycle} options={[{ value: 'active', label: '当前有效' }, { value: 'disabled', label: '已停用' }, { value: 'archived', label: '已归档' }, { value: 'all', label: '全部历史' }]} /><Select allowClear aria-label="业务系统筛选" placeholder="业务系统" value={businessId} onChange={(value) => { setBusinessId(value); setApplicationId(undefined); setServiceId(undefined); }} options={businesses.map((item) => ({ value: item.id, label: item.name }))} /><Select allowClear aria-label="应用筛选" placeholder="应用" value={applicationId} onChange={(value) => { setApplicationId(value); setServiceId(undefined); }} options={applications.filter((item) => !businessId || item.business_id === businessId).map((item) => ({ value: item.id, label: item.name }))} /><Select allowClear aria-label="服务筛选" placeholder="服务" value={serviceId} onChange={setServiceId} options={services.filter((item) => !applicationId || item.application_id === applicationId).map((item) => ({ value: item.id, label: item.name }))} /><Select allowClear aria-label="服务器筛选" placeholder="服务器" value={assetId} onChange={setAssetId} options={assets.map((item) => ({ value: item.id, label: `${item.name} · ${item.host}` }))} /></div>
    <div className="table-frame"><Table<CmdbEntityRow> rowKey="key" loading={loading} dataSource={rows} columns={columns} scroll={{ x: 1050 }} pagination={{ pageSize: 20, showSizeChanger: false }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={query || businessId || applicationId || serviceId || assetId ? '暂无符合筛选条件的配置项' : '暂无配置项，请先创建业务系统'} /> }} /></div>
    </div><CmdbEntityModal type={modalType} editing={editing} businesses={businesses} applications={applications} services={services} submitting={submitting} onClose={() => { setModalType(null); setEditing(null); }} onSubmit={save} />
    <CmdbDetailDrawer row={selected} assets={assets} services={services} canWrite={canWrite} onClose={() => setSelected(null)} onEdit={(row) => { setSelected(null); openEdit(row); }} onBind={bindAsset} onUnbind={unbindAsset} onRestore={restoreAsset} />
    <Modal title="自动发现预览" open={discovery !== null} onCancel={() => setDiscovery(null)} footer={<Button onClick={() => setDiscovery(null)}>关闭</Button>} width={820}>
      <Alert type="info" showIcon message="只读发现结果" description="结果来自服务器最近一次只读采集。选择发现服务并指定应用后，管理员确认才会写入配置项管理。" />
      <Table rowKey="asset_id" size="small" pagination={false} dataSource={discovery?.assets ?? []} columns={[
        { title: '服务器', render: (_, item) => <div><strong>{item.asset_name}</strong><small>{item.host}</small></div> },
        { title: '采集状态', dataIndex: 'status' },
        { title: '发现服务', render: (_, item) => item.services.length ? <Space wrap>{item.services.map((service) => <Button key={`${service.source}-${service.name}`} size="small" disabled={!canWrite} onClick={() => setDiscoveryTarget({ assetId: item.asset_id, assetName: item.asset_name, serviceName: service.name, source: service.source })}>{service.name} · {service.source === 'systemd' ? 'systemd' : '容器'}</Button>)}</Space> : '未发现' },
        { title: '采集时间', render: (_, item) => new Date(item.observed_at).toLocaleString() },
      ]} locale={{ emptyText: '暂无可用的只读采集结果' }} />
    </Modal>
    <Modal title="确认纳入配置项管理" open={discoveryTarget !== null} okText="确认写入" confirmLoading={discoveryConfirming} okButtonProps={{ disabled: !discoveryApplicationId }} onOk={() => void confirmDiscovery()} onCancel={() => { setDiscoveryTarget(null); setDiscoveryApplicationId(undefined); }} destroyOnHidden>
      {discoveryTarget ? <><p>服务器：{discoveryTarget.assetName}</p><p>发现服务：<strong>{discoveryTarget.serviceName}</strong></p><Select aria-label="目标应用" placeholder="选择目标应用" value={discoveryApplicationId} onChange={setDiscoveryApplicationId} options={applications.map((item) => ({ value: item.id, label: item.name }))} style={{ width: '100%' }} /></> : null}
    </Modal>
  </section>;
}
