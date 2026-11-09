import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Alert, Checkbox, DatePicker, Form, Input, InputNumber, Modal, Select, Spin } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { getDiagnosticGrants, type DiagnosticAction, type DiagnosticGrant, type RabbitMQDiagnosticGrantInput } from '../../api/diagnostics';
import type { Asset } from '../../types/asset';

interface GrantValues {
  asset_id: string; port: number; username: string; password: string; ca_pem: string; vhost: string;
  action_ids: string[]; expires_at?: Dayjs; confirmation: string; reason: string; rabbitmq_confirmed: boolean;
}
const actionIds = ['rabbitmq.status', 'rabbitmq.nodes', 'rabbitmq.queues', 'rabbitmq.consumers'];
const validExpiry = (value?: Dayjs) => Boolean(value?.isValid() && value.valueOf() > Date.now()
  && value.valueOf() <= Date.now() + 90 * 86_400_000);
const validUser = (value: string) => /^[a-z_][a-z0-9_-]{0,63}$/.test(value)
  && !['root', 'admin', 'administrator', 'guest'].includes(value);
const validVhost = (value: string) => value.length >= 1 && value.length <= 128
  && !['.', '..'].includes(value)
  && !Array.from(value).some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127);
const validCa = (value: string) => {
  if (value.length > 32768) return false;
  const certificates = value.trim().split(/(?=-----BEGIN CERTIFICATE-----)/);
  return certificates.length >= 1 && certificates.length <= 5 && certificates.every((item) =>
    /^-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=]+(?:\s+[A-Za-z0-9+/=]+)*\s+-----END CERTIFICATE-----$/.test(item.trim()));
};

export function RabbitMQGrantForm({ assets, catalog, initialGrant, busy, canSubmit, onCancel, onSubmit, kindSelector }: {
  assets: Asset[]; catalog: DiagnosticAction[]; initialGrant: DiagnosticGrant | null; busy: boolean;
  canSubmit: boolean; onCancel: () => void; onSubmit: (values: RabbitMQDiagnosticGrantInput) => void;
  kindSelector?: ReactNode;
}) {
  const [form] = Form.useForm<GrantValues>();
  const values = Form.useWatch([], form) as Partial<GrantValues> | undefined;
  const [current, setCurrent] = useState(initialGrant);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const generation = useRef(0);
  const enabledAssets = assets.filter((item) => item.lifecycle_status === 'enabled');
  const asset = enabledAssets.find((item) => item.id === values?.asset_id);
  const available = catalog.filter((item) => actionIds.includes(item.id) && item.available && item.read_only);
  const writeValues = (grant: DiagnosticGrant | null, assetId?: string) => {
    setCurrent(grant); form.resetFields();
    const config = grant?.rabbitmq_config;
    form.setFieldsValue({ asset_id: assetId ?? grant?.asset_id, port: config?.port ?? 15671,
      username: config?.username ?? 'readonly', password: '', ca_pem: '', vhost: config?.vhost ?? '/',
      action_ids: grant?.action_ids ?? [], expires_at: grant ? dayjs(grant.expires_at) : undefined,
      confirmation: '', reason: '', rabbitmq_confirmed: false });
  };
  useEffect(() => { writeValues(initialGrant); return () => { generation.current += 1; }; }, []);
  useEffect(() => {
    if (!canSubmit) {
      generation.current += 1; setLoading(false);
      form.resetFields(['password', 'ca_pem', 'confirmation', 'reason', 'rabbitmq_confirmed']);
    }
  }, [canSubmit, form]);
  const selectAsset = async (assetId: string) => {
    const requestId = ++generation.current;
    writeValues(null, assetId); setLoadError(''); setLoading(true);
    try {
      const result = await getDiagnosticGrants(0, assetId);
      if (requestId !== generation.current) return;
      const next = result.items.find((item) => item.asset_id === assetId) ?? null;
      if (next && next.kind !== 'rabbitmq') setLoadError('该资产已有其他只读授权，不允许转换授权类型');
      else writeValues(next, assetId);
    } catch {
      if (requestId === generation.current) setLoadError('资产授权状态加载失败，请刷新后重试');
    } finally { if (requestId === generation.current) setLoading(false); }
  };
  const config = current?.rabbitmq_config;
  const password = values?.password ?? '';
  const rawCa = values?.ca_pem ?? '';
  const ca = rawCa.trim();
  const identityChanged = Boolean(current) && (asset?.host !== config?.host
    || values?.username?.trim() !== config?.username || values?.port !== config?.port
    || values?.vhost?.trim() !== config?.vhost);
  const needsPassword = !current?.secret_configured || identityChanged || rawCa.length > 0;
  const ready = Boolean(asset && canSubmit && !busy && !loading && !loadError
    && (!current || current.kind === 'rabbitmq') && Number.isInteger(values?.port)
    && (values?.port ?? 0) >= 1 && (values?.port ?? 0) <= 65535
    && validVhost(values?.vhost?.trim() ?? '')
    && validUser(values?.username?.trim() ?? '') && values?.action_ids?.length
    && values.action_ids.length <= 4 && new Set(values.action_ids).size === values.action_ids.length
    && values.action_ids.every((id) => available.some((item) => item.id === id))
    && (password ? password.length <= 4096 : !needsPassword)
    && (rawCa ? validCa(rawCa) : Boolean(current?.ca_fingerprint)) && validExpiry(values?.expires_at)
    && values?.confirmation?.trim() === asset.name && (values?.reason?.trim().length ?? 0) >= 3
    && (values?.reason?.trim().length ?? 0) <= 500 && values?.rabbitmq_confirmed === true);
  const submit = (input: GrantValues) => {
    if (!ready || !asset || !input.expires_at || !validExpiry(input.expires_at)) return;
    onSubmit({ kind: 'rabbitmq', asset_id: asset.id, expected_revision: current?.revision ?? 0,
      port: input.port, username: input.username.trim(), vhost: input.vhost.trim(),
      action_ids: input.action_ids, expires_at: input.expires_at.toISOString(),
      confirmation: input.confirmation.trim(), reason: input.reason.trim(), rabbitmq_confirmed: true,
      ...(password ? { password } : {}), ...(ca ? { ca_pem: ca } : {}) });
  };
  return <Modal title="配置 RabbitMQ 只读授权" open onCancel={() => { form.resetFields(); onCancel(); }}
    onOk={() => form.submit()} okText="保存只读授权" confirmLoading={busy} okButtonProps={{ disabled: !ready }}
    cancelButtonProps={{ disabled: busy }} closable={!busy} maskClosable={!busy} destroyOnHidden width={660}>
    {kindSelector}
    <Alert type="info" showIcon message="仅通过 HTTPS Management API 的固定 GET 读取状态、节点、队列和消费者摘要，不读取消息体，不执行写入、删除或管理操作。" />
    {loadError ? <Alert type="error" showIcon message={loadError} /> : null}
    {loading ? <Spin size="small" /> : null}
    <Form form={form} layout="vertical" disabled={busy || loading || !canSubmit} onFinish={submit} style={{ marginTop: 16 }}>
      <Form.Item name="asset_id" label="服务器资产"><Select aria-label="授权服务器" showSearch optionFilterProp="label"
        onChange={(id: string) => void selectAsset(id)} options={enabledAssets.map((item) => ({ value: item.id, label: `${item.name} · ${item.host}` }))} /></Form.Item>
      <Form.Item name="port" label="RabbitMQ Management API 端口"><InputNumber aria-label="RabbitMQ Management API 端口" min={1} max={65535} style={{ width: '100%' }} /></Form.Item>
      <Form.Item name="username" label="专用只读账号" extra="禁止使用 root、admin、administrator、guest 等账号"><Input aria-label="RabbitMQ 专用只读账号" autoComplete="off" maxLength={64} /></Form.Item>
      <Form.Item name="vhost" label="RabbitMQ vhost"><Input aria-label="RabbitMQ vhost" autoComplete="off" maxLength={128} /></Form.Item>
      <Form.Item name="action_ids" label="允许采集的证据"><Checkbox.Group options={catalog.filter((item) => actionIds.includes(item.id)).map((item) => ({
        value: item.id, label: item.label, disabled: !available.some((candidate) => candidate.id === item.id),
      }))} /></Form.Item>
      <Form.Item name="password" label="RabbitMQ 密码" extra={current?.secret_configured ? '目标、身份、端口和 vhost 未变化且 CA 留空时可保留已加密密码。' : '首次授权必须提供密码。'}>
        <Input.Password aria-label="RabbitMQ 密码" autoComplete="new-password" visibilityToggle={false} maxLength={4096} placeholder={current?.secret_configured ? '********' : ''} />
      </Form.Item>
      <Form.Item name="ca_pem" label="RabbitMQ CA PEM" extra={current?.ca_fingerprint ? `留空保留 CA：${current.ca_fingerprint}` : '首次授权必须提供 CA 证书链。'}
        validateStatus={rawCa && !validCa(rawCa) ? 'error' : undefined}
        help={rawCa && !validCa(rawCa) ? '需提供 1 至 5 份 PEM CA 证书，总长度不超过 32768 个字符' : undefined}>
        <Input.TextArea aria-label="RabbitMQ CA PEM" rows={3} autoComplete="off" maxLength={32768} />
      </Form.Item>
      <Form.Item name="expires_at" label="授权有效期"><DatePicker aria-label="授权有效期" showTime needConfirm={false}
        format="YYYY-MM-DD HH:mm:ss" minDate={dayjs().startOf('day')} maxDate={dayjs().add(90, 'day')} style={{ width: '100%' }} /></Form.Item>
      <Form.Item name="reason" label="授权原因"><Input.TextArea aria-label="授权原因" rows={2} maxLength={500} /></Form.Item>
      <Form.Item name="confirmation" label="资产名称确认" extra={`需输入：${asset?.name ?? '请先选择服务器'}`}><Input aria-label="资产名称确认" autoComplete="off" maxLength={100} /></Form.Item>
      <Form.Item name="rabbitmq_confirmed" valuePropName="checked" extra="管理员人工确认专用只读权限；状态和节点为集群摘要，队列和消费者限定在所选 vhost。">
        <Checkbox aria-label="已人工确认 RabbitMQ 只读权限">已人工确认 RabbitMQ 只读权限</Checkbox>
      </Form.Item>
    </Form>
  </Modal>;
}
