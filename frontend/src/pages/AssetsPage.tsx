import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Button, Empty, Input, message, Modal, Select, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Activity, Archive, FileWarning, Pencil, Plus, Power, PowerOff, RefreshCw, RotateCcw, Search, Server, SquareTerminal, Trash2 } from 'lucide-react';

import { cleanupAsset, checkAsset, createAsset, getAssetCleanupPreview, getAssets, updateAsset, updateAssetLifecycle } from '../api/assets';
import { AssetFormDrawer } from '../components/AssetFormDrawer';
import type { Asset, AssetCleanupPreview, AssetCreate, AssetLifecycleStatus, AssetStatus } from '../types/asset';
import { environmentLabels, osLabels, statusLabels } from '../utils/assets';
import { matchesConfirmation } from '../utils/confirmation';

const statusColors: Record<AssetStatus, string> = { unknown: 'default', online: 'green', offline: 'red' };
const lifecycleLabels: Record<AssetLifecycleStatus, string> = { enabled: '启用', disabled: '停用', archived: '已归档' };
const cleanupPhrase = '清理测试资产';

export function AssetsPage({ onViewFindings, onOpenTerminal, initialAssetId = null, initialQuery = '' }: {
  onViewFindings?: (assetId: string) => void;
  onOpenTerminal?: (assetId: string) => void;
  initialAssetId?: string | null;
  initialQuery?: string;
}) {
  const [items, setItems] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [checkingId, setCheckingId] = useState<string | null>(null);
  const [query, setQuery] = useState(initialQuery);
  const [searchAssetId, setSearchAssetId] = useState(initialAssetId);
  const [status, setStatus] = useState<AssetStatus | 'all'>('all');
  const [osFamily, setOsFamily] = useState('all');
  const [lifecycleView, setLifecycleView] = useState('active');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<Asset | null>(null);
  const [cleanupTarget, setCleanupTarget] = useState<Asset | null>(null);
  const [cleanupPreview, setCleanupPreview] = useState<AssetCleanupPreview | null>(null);
  const [cleanupReason, setCleanupReason] = useState('');
  const [cleanupConfirmation, setCleanupConfirmation] = useState('');
  const cleanupPending = useRef(false), cleanupRequest = useRef(0);
  const cleanupFieldId = useId();
  const cleanupReasonLength = cleanupReason.trim().length;
  const cleanupReasonError = cleanupReason.length === 0 ? '' : cleanupReasonLength < 5
    ? '清理原因不足 5 个字符，请补充说明。' : cleanupReasonLength > 500 ? '清理原因不能超过 500 个字符。' : '';
  const cleanupConfirmationError = cleanupConfirmation.length > 0 && !matchesConfirmation(cleanupConfirmation, cleanupPhrase)
    ? `确认文本不匹配，请输入“${cleanupPhrase}”。` : '';
  const cleanupValid = Boolean(cleanupTarget && cleanupPreview?.allowed && cleanupPreview.asset_id === cleanupTarget.id
    && cleanupPreview.confirmation_phrase === `DELETE ${cleanupTarget.id}` && cleanupReasonLength >= 5
    && cleanupReasonLength <= 500 && matchesConfirmation(cleanupConfirmation, cleanupPhrase));
  const [messageApi, contextHolder] = message.useMessage();
  useEffect(() => () => { cleanupRequest.current += 1; }, []);

  const load = async () => {
    setLoading(true);
    try {
      setItems(await getAssets(lifecycleView));
    } catch (error) { messageApi.error(error instanceof Error ? error.message : '加载失败'); }
    finally { setLoading(false); }
  };

  useEffect(() => { void load(); }, [lifecycleView]);

  const filtered = useMemo(() => items.filter((item) => {
    const keyword = query.trim().toLowerCase();
    const text = `${item.name} ${item.host} ${item.group_name} ${item.labels.join(' ')}`.toLowerCase();
    return (!searchAssetId || item.id === searchAssetId) && (!keyword || text.includes(keyword)) && (status === 'all' || item.status === status)
      && (osFamily === 'all' || item.os_family === osFamily);
  }), [items, osFamily, query, searchAssetId, status]);

  const save = async (payload: AssetCreate) => {
    setSubmitting(true);
    try {
      const saved = editing ? await updateAsset(editing.id, payload) : await createAsset(payload);
      if (editing) messageApi.success('资产已更新');
      else if (saved.status === 'online') messageApi.success('资产已创建并完成 SSH 登录检查');
      else messageApi.warning(saved.check_message ?? '资产已创建，但 SSH 连接检查失败');
      setDrawerOpen(false); setEditing(null); await load();
    } catch (error) { messageApi.error(error instanceof Error ? error.message : '保存失败'); }
    finally { setSubmitting(false); }
  };

  const check = async (item: Asset) => {
    setCheckingId(item.id);
    try {
      const checked = await checkAsset(item.id);
      setItems((current) => current.map((asset) => asset.id === item.id ? checked : asset));
      messageApi.success(checked.check_message ?? '检查完成');
    } catch (error) { messageApi.error(error instanceof Error ? error.message : '检查失败'); }
    finally { setCheckingId(null); }
  };

  const changeLifecycle = async (item: Asset, value: AssetLifecycleStatus) => {
    try {
      await updateAssetLifecycle(item.id, value, value === 'archived' ? '资产归档' : value === 'disabled' ? '暂停运维操作' : '恢复使用');
      messageApi.success(value === 'archived' ? '资产已归档' : value === 'disabled' ? '资产已停用' : '资产已启用');
      await load();
    } catch (error) { messageApi.error(error instanceof Error ? error.message : '状态更新失败'); }
  };

  const openCleanup = async (item: Asset) => {
    if (cleanupPending.current) return;
    const requestId = ++cleanupRequest.current;
    setCleanupTarget(item); setCleanupReason(''); setCleanupConfirmation(''); setCleanupPreview(null);
    try {
      const preview = await getAssetCleanupPreview(item.id);
      if (requestId === cleanupRequest.current) setCleanupPreview(preview);
    } catch (error) {
      if (requestId === cleanupRequest.current) {
        messageApi.error(error instanceof Error ? error.message : '依赖预览失败'); setCleanupTarget(null);
      }
    }
  };
  const closeCleanup = () => {
    if (cleanupPending.current) return;
    cleanupRequest.current += 1;
    setCleanupTarget(null); setCleanupPreview(null); setCleanupReason(''); setCleanupConfirmation('');
  };

  const confirmCleanup = async () => {
    if (!cleanupTarget || !cleanupPreview || !cleanupValid || cleanupPending.current) return;
    cleanupPending.current = true;
    setSubmitting(true);
    try {
      await cleanupAsset(cleanupTarget.id, cleanupReason.trim(), cleanupPreview.confirmation_phrase);
      messageApi.success('测试资产及其专属数据已清理，审计记录已保留');
      setCleanupTarget(null); setCleanupPreview(null); setCleanupReason(''); setCleanupConfirmation(''); await load();
    } catch (error) { messageApi.error(error instanceof Error ? error.message : '清理失败'); }
    finally { cleanupPending.current = false; setSubmitting(false); }
  };

  const renderActions = (item: Asset) => <div className="row-actions"><Tooltip title="打开终端"><Button type="text" disabled={!item.credential_id || item.lifecycle_status !== 'enabled'} icon={<SquareTerminal size={17} />} aria-label={`打开 ${item.name} 终端`} onClick={() => onOpenTerminal?.(item.id)} /></Tooltip><Tooltip title="查看漏洞"><Button type="text" icon={<FileWarning size={17} />} aria-label={`查看 ${item.name} 漏洞`} onClick={() => onViewFindings?.(item.id)} /></Tooltip><Tooltip title="SSH 登录检查"><Button type="text" disabled={item.lifecycle_status !== 'enabled'} icon={<Activity size={17} />} aria-label={`检查 ${item.name}`} loading={checkingId === item.id} onClick={() => void check(item)} /></Tooltip><Tooltip title="编辑"><Button type="text" icon={<Pencil size={17} />} aria-label={`编辑 ${item.name}`} onClick={() => { setEditing(item); setDrawerOpen(true); }} /></Tooltip>{item.lifecycle_status === 'archived' ? <Tooltip title="恢复"><Button type="text" icon={<RotateCcw size={17} />} aria-label={`恢复 ${item.name}`} onClick={() => void changeLifecycle(item, 'enabled')} /></Tooltip> : <><Tooltip title={item.lifecycle_status === 'enabled' ? '停用' : '启用'}><Button type="text" icon={item.lifecycle_status === 'enabled' ? <PowerOff size={17} /> : <Power size={17} />} aria-label={`${item.lifecycle_status === 'enabled' ? '停用' : '启用'} ${item.name}`} onClick={() => void changeLifecycle(item, item.lifecycle_status === 'enabled' ? 'disabled' : 'enabled')} /></Tooltip><Tooltip title="归档，历史证据保留"><Button type="text" icon={<Archive size={17} />} aria-label={`归档 ${item.name}`} onClick={() => void changeLifecycle(item, 'archived')} /></Tooltip></>}{item.environment === 'test' ? <Tooltip title="清理测试数据"><Button type="text" danger icon={<Trash2 size={17} />} aria-label={`清理 ${item.name} 测试数据`} onClick={() => void openCleanup(item)} /></Tooltip> : null}</div>;

  const columns: ColumnsType<Asset> = [
    { title: '资产', key: 'asset', render: (_, item) => <div className="asset-identity"><span><Server size={17} /></span><div><strong>{item.name}</strong><small>{item.host}:{item.ssh_port}</small><small className="asset-mobile-meta">{osLabels[item.os_family]} · {environmentLabels[item.environment]} · {item.group_name}{item.credential ? ` · SSH ${item.credential.username}` : ' · SSH 未配置'}</small></div></div> },
    { title: '操作系统', dataIndex: 'os_family', key: 'os', width: 170, render: (value) => osLabels[value as Asset['os_family']] },
    { title: '环境 / 分组', key: 'group', width: 170, responsive: ['md'], render: (_, item) => <div className="stacked-cell"><strong>{environmentLabels[item.environment]}</strong><small>{item.group_name}</small></div> },
    { title: 'SSH 登录', key: 'credential', width: 180, responsive: ['lg'], render: (_, item) => item.credential ? <div className="stacked-cell"><strong>{item.credential.username}</strong><small>已加密保存</small></div> : <span className="muted">未配置</span> },
    { title: '状态', key: 'status', width: 155, render: (_, item) => <div className="stacked-cell"><Tooltip title={item.check_message ?? '尚未检查'}><Tag color={statusColors[item.status]}>{statusLabels[item.status]}{item.latency_ms ? ` · ${item.latency_ms}ms` : ''}</Tag></Tooltip><small>{lifecycleLabels[item.lifecycle_status]}</small></div> },
    { title: '操作', key: 'actions', width: 310, align: 'right', render: (_, item) => renderActions(item) },
  ];

  const openCreate = () => { setEditing(null); setDrawerOpen(true); };
  return (
    <section className="assets-page">
      {contextHolder}
      <div className="page-heading"><div><p className="eyebrow">主机清单</p><h1>资产管理</h1></div><Button type="primary" icon={<Plus size={17} />} onClick={openCreate}>新建资产</Button></div>
      <div className="metric-band"><div><span>资产总数</span><strong>{items.length}</strong></div><div><span>在线</span><strong>{items.filter(({ status: value }) => value === 'online').length}</strong></div><div><span>生产环境</span><strong>{items.filter(({ environment }) => environment === 'production').length}</strong></div><div><span>待检查</span><strong className="metric-text">{items.filter(({ status: value }) => value === 'unknown').length}</strong></div></div>
      <div className="asset-toolbar"><Input allowClear prefix={<Search size={16} />} placeholder="搜索名称、地址、分组或标签" value={query} onChange={(event) => { setQuery(event.target.value); setSearchAssetId(null); }} /><Select value={lifecycleView} onChange={setLifecycleView} options={[{ value: 'active', label: '当前资产' }, { value: 'enabled', label: '仅启用' }, { value: 'disabled', label: '仅停用' }, { value: 'archived', label: '已归档' }, { value: 'all', label: '全部生命周期' }]} /><Select value={status} onChange={setStatus} options={[{ value: 'all', label: '全部状态' }, ...Object.entries(statusLabels).map(([value, label]) => ({ value, label }))]} /><Select value={osFamily} onChange={setOsFamily} options={[{ value: 'all', label: '全部系统' }, ...Object.entries(osLabels).map(([value, label]) => ({ value, label }))]} /><Tooltip title="刷新"><Button icon={<RefreshCw size={17} />} aria-label="刷新" loading={loading} onClick={() => void load()} /></Tooltip></div>
      <div className="table-frame assets-table-frame"><Table<Asset> rowKey="id" columns={columns} dataSource={filtered} loading={loading} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 820 }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无资产"><Button type="primary" icon={<Plus size={16} />} onClick={openCreate}>新建资产</Button></Empty> }} /></div>
      <AssetFormDrawer open={drawerOpen} asset={editing} submitting={submitting} onClose={() => { setDrawerOpen(false); setEditing(null); }} onSubmit={save} />
      <Modal title="清理测试资产数据" open={cleanupTarget !== null} confirmLoading={submitting} okText="确认清理" cancelText="返回"
        okButtonProps={{ danger: true, disabled: submitting || !cleanupValid }} cancelButtonProps={{ disabled: submitting }}
        onOk={() => void confirmCleanup()} onCancel={closeCleanup} closable={!submitting} maskClosable={!submitting} keyboard={!submitting} destroyOnHidden>
        {cleanupTarget ? <p>清理对象：<strong>{cleanupTarget.name}</strong> · <span>{cleanupTarget.host}:{cleanupTarget.ssh_port}</span></p> : null}
        {cleanupPreview ? <div className="cleanup-preview">
          <p>{cleanupPreview.allowed ? '此操作会物理清理该测试资产的专属数据，并永久保留独立审计。' : cleanupPreview.reason === '仅 test 环境资产允许物理清理' ? '仅测试环境资产允许清理。' : cleanupPreview.reason ?? '当前资产不允许清理。'}</p>
          <p>扫描 {cleanupPreview.scan_tasks} · 漏洞 {cleanupPreview.vulnerability_findings} · 报告 {cleanupPreview.reports} · 监控 {cleanupPreview.monitoring_records} · 终端会话 {cleanupPreview.terminal_sessions}</p>
          <Input.TextArea aria-label="清理原因" placeholder="填写清理原因" value={cleanupReason} disabled={submitting} maxLength={500}
            status={cleanupReasonError ? 'error' : undefined} aria-invalid={Boolean(cleanupReasonError)}
            aria-describedby={`${cleanupFieldId}-reason-hint${cleanupReasonError ? ` ${cleanupFieldId}-reason-error` : ''}`}
            onChange={(event) => setCleanupReason(event.target.value)} rows={2} />
          <p id={cleanupFieldId + '-reason-hint'}>清理原因需 5 至 500 个字符，首尾空格不计入。</p>
          {cleanupReasonError ? <p id={cleanupFieldId + '-reason-error'} role="alert">{cleanupReasonError}</p> : null}
          <p className="confirmation-prompt">请输入确认文本：<strong>{cleanupPhrase}</strong></p>
          <Input aria-label="二次确认" placeholder={`请输入“${cleanupPhrase}”`} value={cleanupConfirmation} disabled={submitting}
            status={cleanupConfirmationError ? 'error' : undefined} aria-invalid={Boolean(cleanupConfirmationError)}
            aria-describedby={cleanupConfirmationError ? cleanupFieldId + '-confirmation-error' : undefined}
            onChange={(event) => setCleanupConfirmation(event.target.value)} autoComplete="off" />
          {cleanupConfirmationError ? <p id={cleanupFieldId + '-confirmation-error'} role="alert">{cleanupConfirmationError}</p> : null}
          {cleanupTarget && (cleanupPreview.asset_id !== cleanupTarget.id || cleanupPreview.confirmation_phrase !== `DELETE ${cleanupTarget.id}`)
            ? <p role="alert">清理预览无效，请关闭后重新获取。</p> : null}
        </div> : <p>正在获取清理预览，请稍候。</p>}
      </Modal>
    </section>
  );
}
