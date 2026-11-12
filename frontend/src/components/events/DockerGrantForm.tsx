import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Alert, Checkbox, DatePicker, Form, Input, Modal, Select, Spin } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { getDiagnosticGrants, type DiagnosticAction, type DiagnosticGrant, type DockerDiagnosticGrantInput } from '../../api/diagnostics';
import type { Asset } from '../../types/asset';

interface GrantValues {
  asset_id: string; endpoint: string; proxy_identity: string; token: string; ca_pem: string;
  action_ids: string[]; expires_at?: Dayjs; confirmation: string; reason: string; proxy_confirmed: boolean;
}
const actionIds = ['docker.containers', 'docker.resources'];
const validExpiry = (value?: Dayjs) => Boolean(value?.isValid() && value.valueOf() > Date.now()
  && value.valueOf() <= Date.now() + 90 * 86_400_000);
const validIdentity = (value: string) => value.length >= 1 && value.length <= 64
  && [...value].every((char) => char.charCodeAt(0) >= 33 && char.charCodeAt(0) <= 126);
const validToken = (value: string) => value.length >= 1 && value.length <= 16384
  && [...value].every((char) => char.charCodeAt(0) >= 33 && char.charCodeAt(0) <= 126);
const validCa = (value: string) => {
  if (value.length > 32768) return false;
  const certificates = value.trim().split(/(?=-----BEGIN CERTIFICATE-----)/);
  return certificates.length >= 1 && certificates.length <= 5 && certificates.every((certificate) =>
    /^-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=]+(?:\s+[A-Za-z0-9+/=]+)*\s+-----END CERTIFICATE-----$/.test(certificate.trim()));
};
function validIp(value: string): boolean {
  const parts = value.split('.');
  if (parts.length === 4 && parts.every((part) => /^\d{1,3}$/.test(part) && Number(part) <= 255 && String(Number(part)) === part)) {
    return parts[0] !== '0' && parts[0] !== '127' && Number(parts[0]) < 224 && !(parts[0] === '169' && parts[1] === '254');
  }
  if (!/^[0-9a-f:]+$/i.test(value) || !value.includes(':')) return false;
  const groups = value.split('::');
  if (groups.length > 2) return false;
  const left = groups[0] ? groups[0].split(':') : [];
  const right = groups[1] ? groups[1].split(':') : [];
  const missing = 8 - left.length - right.length;
  if (missing < 0 || (groups.length === 1 && missing !== 0) || (groups.length === 2 && missing === 0)) return false;
  const full = [...left, ...Array<string>(missing).fill('0'), ...right];
  if (!full.every((item) => /^[0-9a-f]{1,4}$/i.test(item))) return false;
  const number = full.reduce((result, item) => (result << 16n) + BigInt(parseInt(item, 16)), 0n);
  return number > 1n && (number >> 118n) !== 0x3fan && (number >> 120n) !== 0xffn && (number >> 32n) !== 0xffffn;
}
function validEndpoint(value: string, asset?: Asset): boolean {
  const match = /^https:\/\/(\[[0-9a-f:.]+\]|[0-9.]+)(?::([0-9]{1,5}))?$/i.exec(value);
  if (!match || !asset) return false;
  const host = match[1].replace(/^\[|\]$/g, '');
  const assetHost = asset.host.replace(/^\[|\]$/g, '');
  if (!validIp(host) || !validIp(assetHost)
    || (match[2] && (Number(match[2]) < 1 || Number(match[2]) > 65535))) return false;
  try {
    return new URL(value).hostname === new URL(`https://${assetHost.includes(':') ? `[${assetHost}]` : assetHost}`).hostname;
  } catch { return false; }
}

export function DockerGrantForm({ assets, catalog, initialGrant, busy, canSubmit, onCancel, onSubmit, kindSelector }: {
  assets: Asset[]; catalog: DiagnosticAction[]; initialGrant: DiagnosticGrant | null; busy: boolean;
  canSubmit: boolean; onCancel: () => void; onSubmit: (values: DockerDiagnosticGrantInput) => void; kindSelector?: ReactNode;
}) {
  const [form] = Form.useForm<GrantValues>();
  const values = Form.useWatch([], form) as Partial<GrantValues> | undefined;
  const [current, setCurrent] = useState(initialGrant);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const generation = useRef(0);
  const enabledAssets = assets.filter((item) => item.lifecycle_status === 'enabled' && item.credential_id == null && item.credential == null);
  const asset = enabledAssets.find((item) => item.id === values?.asset_id);
  const available = catalog.filter((item) => actionIds.includes(item.id) && item.available && item.read_only);
  const writeValues = (grant: DiagnosticGrant | null, assetId?: string) => {
    setCurrent(grant); form.resetFields();
    const config = grant?.docker_config;
    const target = enabledAssets.find((item) => item.id === (assetId ?? grant?.asset_id));
    form.setFieldsValue({ asset_id: assetId ?? grant?.asset_id,
      endpoint: config?.endpoint ?? (target ? `https://${target.host.includes(':') && !target.host.startsWith('[') ? `[${target.host}]` : target.host}` : ''),
      proxy_identity: config?.proxy_identity ?? '', token: '', ca_pem: '', action_ids: grant?.action_ids ?? [],
      expires_at: grant ? dayjs(grant.expires_at) : undefined, confirmation: '', reason: '', proxy_confirmed: false });
  };
  useEffect(() => { writeValues(initialGrant); return () => { generation.current += 1; }; }, []);
  useEffect(() => {
    if (!canSubmit) form.resetFields(['token', 'ca_pem', 'confirmation', 'reason', 'proxy_confirmed']);
  }, [canSubmit, form]);
  const selectAsset = async (assetId: string) => {
    const requestId = ++generation.current;
    writeValues(null, assetId); setLoadError(''); setLoading(true);
    try {
      const result = await getDiagnosticGrants(0, assetId);
      if (requestId !== generation.current) return;
      const next = result.items.find((item) => item.asset_id === assetId) ?? null;
      if (next && next.kind !== 'docker') setLoadError('该资产已有其他只读授权，不允许转换授权类型');
      else writeValues(next, assetId);
    } catch {
      if (requestId === generation.current) setLoadError('资产授权状态加载失败，请刷新后重试');
    } finally { if (requestId === generation.current) setLoading(false); }
  };
  const config = current?.docker_config;
  const tokenValue = values?.token ?? '';
  const caValue = values?.ca_pem?.trim() ?? '';
  const identityChanged = Boolean(current) && (values?.endpoint?.trim() !== config?.endpoint
    || values?.proxy_identity?.trim() !== config?.proxy_identity);
  const needsToken = !current?.secret_configured || identityChanged || Boolean(caValue);
  const needsCa = !current?.ca_fingerprint;
  const ready = Boolean(asset && canSubmit && !busy && !loading && !loadError && validEndpoint(values?.endpoint?.trim() ?? '', asset)
    && validIdentity(values?.proxy_identity?.trim() ?? '') && validExpiry(values?.expires_at)
    && values?.action_ids?.length && values.action_ids.length <= 2 && values.action_ids.every((id) => available.some((item) => item.id === id))
    && (tokenValue ? validToken(tokenValue) : !needsToken) && (caValue ? validCa(values?.ca_pem ?? '') : !needsCa)
    && values?.confirmation?.trim() === asset.name
    && (values?.reason?.trim().length ?? 0) >= 3 && (values?.reason?.trim().length ?? 0) <= 500
    && values?.proxy_confirmed === true);
  const submit = (input: GrantValues) => {
    if (!ready || !asset || !input.expires_at) return;
    onSubmit({ kind: 'docker', asset_id: asset.id, expected_revision: current?.revision ?? 0,
      endpoint: input.endpoint.trim(), proxy_identity: input.proxy_identity.trim(), action_ids: input.action_ids,
      expires_at: input.expires_at.toISOString(), confirmation: input.confirmation.trim(), reason: input.reason.trim(),
      proxy_confirmed: true, ...(tokenValue ? { token: tokenValue } : {}), ...(caValue ? { ca_pem: caValue } : {}) });
  };
  return <Modal title="配置 Docker 只读授权" open onCancel={() => { form.resetFields(); onCancel(); }}
    onOk={() => form.submit()} okText="保存只读授权" confirmLoading={busy} okButtonProps={{ disabled: !ready }}
    cancelButtonProps={{ disabled: busy }} closable={!busy} maskClosable={!busy} destroyOnHidden width={660}>
    {kindSelector}
    {loadError ? <Alert type="error" showIcon message={loadError} /> : null}
    {loading ? <Spin size="small" /> : null}
    <Form form={form} layout="vertical" disabled={busy || loading} onFinish={submit} style={{ marginTop: 16 }}>
      <Form.Item name="asset_id" label="代理映射资产"><Select aria-label="授权服务器" showSearch optionFilterProp="label"
        onChange={(id: string) => void selectAsset(id)} options={enabledAssets.map((item) => ({ value: item.id, label: `${item.name} · ${item.host}` }))} /></Form.Item>
      <Form.Item name="endpoint" label="Docker 代理 HTTPS 地址" extra="必须使用与服务器资产一致的字面量 IP HTTPS 地址。"
        validateStatus={values?.endpoint && !validEndpoint(values.endpoint.trim(), asset) ? 'error' : undefined}
        help={values?.endpoint && !validEndpoint(values.endpoint.trim(), asset) ? '地址必须匹配资产 IP，且不得包含路径或域名' : undefined}>
        <Input aria-label="Docker 代理 HTTPS 地址" autoComplete="off" maxLength={280} />
      </Form.Item>
      <Form.Item name="proxy_identity" label="代理身份"><Input aria-label="代理身份" autoComplete="off" maxLength={64} /></Form.Item>
      <Form.Item name="action_ids" label="允许采集的证据"><Checkbox.Group options={catalog.filter((item) => actionIds.includes(item.id)).map((item) => ({
        value: item.id, label: item.label, disabled: !available.some((candidate) => candidate.id === item.id),
      }))} /></Form.Item>
      <Form.Item name="token" label="代理 Token" extra={current?.secret_configured ? '目标、身份未变化且 CA 留空时可留空保留；变更目标、身份或 CA 必须使用新 Token。' : '首次授权必须提供代理 Token。'}
        validateStatus={tokenValue && !validToken(tokenValue) ? 'error' : undefined}
        help={tokenValue && !validToken(tokenValue) ? 'Token 只能包含无空格的可打印 ASCII 字符，最多 16384 个字符' : undefined}>
        <Input.Password aria-label="代理 Token" autoComplete="new-password" maxLength={16384} visibilityToggle={false} placeholder={current?.secret_configured ? '********' : ''} />
      </Form.Item>
      <Form.Item name="ca_pem" label="代理 CA PEM" extra={current?.ca_fingerprint ? `留空保留 CA：${current.ca_fingerprint}` : '首次授权必须提供代理 CA 证书链。'}
        validateStatus={caValue && !validCa(values?.ca_pem ?? '') ? 'error' : undefined} help={caValue && !validCa(values?.ca_pem ?? '') ? '需提供 1 至 5 份 PEM CA 证书' : undefined}>
        <Input.TextArea aria-label="代理 CA PEM" rows={3} autoComplete="off" maxLength={32768} />
      </Form.Item>
      <Form.Item name="expires_at" label="授权有效期"><DatePicker aria-label="授权有效期" showTime needConfirm={false}
        format="YYYY-MM-DD HH:mm:ss" minDate={dayjs().startOf('day')} maxDate={dayjs().add(90, 'day')} style={{ width: '100%' }} /></Form.Item>
      <Form.Item name="reason" label="授权原因"><Input.TextArea aria-label="授权原因" rows={2} maxLength={500} /></Form.Item>
      <Form.Item name="confirmation" label="资产名称确认" extra={`需输入：${asset?.name ?? '请先选择服务器'}`}><Input aria-label="资产名称确认" autoComplete="off" maxLength={100} /></Form.Item>
      <Form.Item name="proxy_confirmed" valuePropName="checked" extra="管理员人工确认代理仅提供 Docker 只读接口。">
        <Checkbox aria-label="已人工确认 Docker 代理只读范围">已人工确认 Docker 代理只读范围</Checkbox>
      </Form.Item>
    </Form>
  </Modal>;
}
