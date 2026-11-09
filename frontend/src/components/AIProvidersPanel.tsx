import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Empty, Input, Modal, Space, Table, Tag } from 'antd';
import { History, RefreshCw } from 'lucide-react';
import { getProviderConfigs, saveProviderConfig, setProviderEnabled, validateProviderConfig } from '../api/ai-agent';
import { getAIProviderAudit, type AuditLogEntry } from '../api/audit-log';
import type { AgentProvider } from '../types/ai-agent';

export function AIProvidersPanel() {
  const [providers, setProviders] = useState<AgentProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [target, setTarget] = useState<AgentProvider | null>(null);
  const [apiKey, setApiKey] = useState('');
  const [auditOpen, setAuditOpen] = useState(false);
  const [auditItems, setAuditItems] = useState<AuditLogEntry[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditPage, setAuditPage] = useState(1);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditError, setAuditError] = useState('');
  const mounted = useRef(true);
  const mutation = useRef(false);
  const loadSequence = useRef(0);
  const auditSequence = useRef(0);
  const load = async () => {
    const sequence = ++loadSequence.current;
    setLoading(true); setReady(false); setError('');
    try {
      const result = await getProviderConfigs();
      if (!mounted.current || sequence !== loadSequence.current) return;
      setProviders(result); setReady(true);
    } catch (cause) {
      if (mounted.current && sequence === loadSequence.current) setError(cause instanceof Error ? cause.message : '漏洞模型配置加载失败');
    } finally { if (mounted.current && sequence === loadSequence.current) setLoading(false); }
  };
  useEffect(() => {
    mounted.current = true; void load();
    return () => { mounted.current = false; loadSequence.current += 1; auditSequence.current += 1; };
  }, []);
  const mutate = async (request: () => Promise<unknown>, success: string, reload: boolean) => {
    if (!ready || loading || mutation.current) return;
    mutation.current = true; setWorking(true); setError(''); setNotice('');
    try {
      await request();
      if (!mounted.current) return;
      setNotice(success);
      if (reload) await load();
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : '模型配置操作失败');
    } finally { mutation.current = false; if (mounted.current) setWorking(false); }
  };
  const save = async () => {
    if (!target || apiKey.trim().length < 8 || apiKey.trim().length > 400) return;
    const provider = target;
    await mutate(async () => {
      await saveProviderConfig(provider.id, apiKey.trim(), provider.enabled ?? false);
      if (mounted.current) { setApiKey(''); setTarget(null); }
    }, '漏洞模型密钥已加密保存，外部推理仍未接通。', true);
  };
  const openAudit = async (page = 1) => {
    const sequence = ++auditSequence.current;
    setAuditOpen(true); setAuditLoading(true); setAuditPage(page);
    setAuditItems([]); setAuditTotal(0); setAuditError('');
    try {
      const result = await getAIProviderAudit(page);
      if (!mounted.current || sequence !== auditSequence.current) return;
      setAuditItems(result.items); setAuditTotal(result.total);
    } catch (cause) {
      if (mounted.current && sequence === auditSequence.current) setAuditError(cause instanceof Error ? cause.message : '配置审计加载失败');
    } finally { if (mounted.current && sequence === auditSequence.current) setAuditLoading(false); }
  };
  const blocked = !ready || loading || working;
  return <section className="user-management" aria-label="漏洞模型配置">
    <div className="panel-heading"><div><strong>漏洞模型配置</strong><span>本地研判及外部提供商密钥</span></div><Space><Button icon={<History size={16} />} onClick={() => void openAudit()}>AI 配置审计</Button><Button aria-label="刷新漏洞模型配置" icon={<RefreshCw size={16} />} loading={loading} disabled={working} onClick={() => void load()} /></Space></div>
    {error ? <Alert type="error" showIcon message={error} /> : null}
    {notice ? <Alert type="success" showIcon message={notice} /> : null}
    <div className="table-frame"><Table<AgentProvider> rowKey="id" size="small" loading={loading} dataSource={providers} pagination={false} scroll={{ x: 760 }} columns={[
      { title: '模型提供商', dataIndex: 'name' },
      { title: '推理状态', render: (_, item) => <Tag color={item.kind === 'local' ? 'green' : 'default'}>{item.kind === 'local' ? '本地就绪' : '外部推理未接通'}</Tag> },
      { title: '配置状态', render: (_, item) => item.kind === 'local' ? '无需密钥' : item.configured ? item.enabled ? '已保存、已启用' : '已保存、已停用' : '尚未配置' },
      { title: '已存密钥', render: (_, item) => item.api_key_masked ?? '-' },
      { title: '操作', render: (_, item) => item.kind === 'external' ? <Space wrap>
        <Button disabled={blocked} onClick={() => { setTarget(item); setApiKey(''); }}>配置密钥</Button>
        <Button disabled={blocked || !item.configured} onClick={() => void mutate(() => validateProviderConfig(item.id), '已存密钥可解密，未验证模型连接。', false)}>校验已存密钥</Button>
        <Button aria-label={item.enabled ? '停用' : '启用'} disabled={blocked || !item.configured} onClick={() => void mutate(() => setProviderEnabled(item.id, !item.enabled), item.enabled ? '模型配置已停用。' : '模型配置已启用，外部推理仍未接通。', true)}>{item.enabled ? '停用' : '启用'}</Button>
      </Space> : null },
    ]} /></div>
    <Modal title={`配置 ${target?.name ?? ''} 密钥`} open={target !== null} onCancel={() => { if (!working) { setTarget(null); setApiKey(''); } }} onOk={() => void save()} okText="加密保存" cancelText="取消" confirmLoading={working} okButtonProps={{ disabled: blocked || apiKey.trim().length < 8 || apiKey.trim().length > 400 }} cancelButtonProps={{ disabled: working }} maskClosable={!working} destroyOnHidden>
      <Input.Password aria-label="漏洞模型 API Key" autoComplete="new-password" maxLength={400} value={apiKey} disabled={working} onChange={(event) => setApiKey(event.target.value)} />
    </Modal>
    <Modal title="AI 配置审计" open={auditOpen} onCancel={() => { setAuditOpen(false); auditSequence.current += 1; }} footer={null} width={780}>
      {auditError ? <Alert type="error" message={auditError} action={<Button aria-label="重试配置审计" onClick={() => void openAudit(auditPage)}>重试</Button>} /> : null}
      <Table<AuditLogEntry> rowKey="id" size="small" loading={auditLoading} dataSource={auditItems} scroll={{ x: 720 }} pagination={{ current: auditPage, total: auditTotal, pageSize: 8, showSizeChanger: false, onChange: (page) => void openAudit(page) }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={auditError ? '加载失败' : '暂无配置审计记录'} /> }} columns={[
        { title: '提供商', dataIndex: 'target', width: 150 },
        { title: '动作', dataIndex: 'action', width: 110, render: (value: string) => <Tag>{({ configured: '配置', validated: '校验', enabled: '启用', disabled: '停用', cleared: '清除密钥' } as Record<string, string>)[value] ?? value}</Tag> },
        { title: '操作人', dataIndex: 'operator', width: 130 }, { title: '详情', dataIndex: 'detail' },
        { title: '时间', dataIndex: 'occurred_at', width: 180, render: (value: string) => new Date(value).toLocaleString('zh-CN') },
      ]} />
    </Modal>
  </section>;
}
