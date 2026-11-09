import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Alert, Checkbox, DatePicker, Form, Input, Modal, Select, Spin } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import {
  getDiagnosticGrants, type DiagnosticAction, type DiagnosticGrant, type KubernetesDiagnosticGrantInput,
} from '../../api/diagnostics';
import type { Asset } from '../../types/asset';

interface GrantValues {
  asset_id: string; endpoint: string; service_account_namespace: string; service_account_name: string;
  namespace_text: string; action_ids: string[]; expires_at?: Dayjs; token: string; ca_pem: string;
  confirmation: string; reason: string; rbac_confirmed: boolean;
}
const actionIds = ['kubernetes.pods', 'kubernetes.workloads', 'kubernetes.events'];
const validName = (value: string) => value.length >= 1 && value.length <= 63 && /^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/.test(value);
const namespacesOf = (value = '') => value.split(',').map((item) => item.trim());
const validNamespaces = (names: string[]) => names.length >= 1 && names.length <= 3
  && new Set(names).size === names.length && names.every(validName);
const validExpiry = (value?: Dayjs) => Boolean(value?.isValid() && value.valueOf() > Date.now()
  && value.valueOf() <= Date.now() + 90 * 86_400_000);
function safeIp(host: string): boolean {
  if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) {
    const parts = host.split('.');
    const octets = parts.map(Number);
    return octets.every((value, index) => value >= 0 && value <= 255 && String(value) === parts[index]) && octets[0] !== 0 && octets[0] !== 127
      && octets[0] < 224 && !(octets[0] === 169 && octets[1] === 254);
  }
  if (!/^[0-9a-f:]+$/i.test(host) || !host.includes(':')) return false;
  const parts = host.split('::');
  if (parts.length > 2) return false;
  const left = parts[0] ? parts[0].split(':') : [];
  const right = parts[1] ? parts[1].split(':') : [];
  const missing = 8 - left.length - right.length;
  if (missing < 0 || (parts.length === 1 && missing !== 0)) return false;
  const full = [...left, ...Array<string>(missing).fill('0'), ...right];
  if (!full.every((value) => /^[0-9a-f]{1,4}$/i.test(value))) return false;
  const number = full.reduce((result, value) => (result << 16n) + BigInt(parseInt(value, 16)), 0n);
  if (number <= 1n || (number >> 118n) === 0x3fan || (number >> 120n) === 0xffn) return false;
  return (number >> 32n) !== 0xffffn;
}
function validEndpoint(value: string, asset?: Asset): boolean {
  const match = /^https:\/\/(\[[0-9a-f:.]+\]|[0-9.]+)(?::([0-9]{1,5}))?$/i.exec(value);
  if (!match || !asset || (match[2] !== undefined && (Number(match[2]) < 1 || Number(match[2]) > 65535))
    || (!match[1].startsWith('[') && !safeIp(match[1]))) return false;
  try {
    const url = new URL(value);
    const host = url.hostname.replace(/^\[|\]$/g, '');
    const assetHost = asset.host.replace(/^\[|\]$/g, '');
    if (!safeIp(host) || !safeIp(assetHost)) return false;
    const normalizedAsset = new URL(`https://${assetHost.includes(':') ? `[${assetHost}]` : assetHost}:6443`).hostname;
    return url.hostname === normalizedAsset;
  } catch { return false; }
}
function validToken(token: string, values?: Partial<GrantValues>): boolean {
  if (!token || token.length > 16384 || !values?.expires_at) return false;
  try {
    const parts = token.split('.');
    if (parts.length !== 3 || parts.some((part) => !/^[A-Za-z0-9_-]+$/.test(part))) return false;
    const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/'))) as { sub?: unknown; exp?: unknown };
    return payload.sub === `system:serviceaccount:${values.service_account_namespace?.trim()}:${values.service_account_name?.trim()}`
      && typeof payload.exp === 'number' && Number.isInteger(payload.exp)
      && payload.exp * 1000 >= values.expires_at.valueOf();
  } catch { return false; }
}
function validCa(value: string): boolean {
  if (value.length > 32768) return false;
  const certificates = value.trim().split(/(?=-----BEGIN CERTIFICATE-----)/);
  return certificates.length >= 1 && certificates.length <= 5 && certificates.every((certificate) =>
    /^-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=]+(?:\s+[A-Za-z0-9+/=]+)*\s+-----END CERTIFICATE-----$/.test(certificate.trim()));
}

export function KubernetesGrantForm({ assets, catalog, initialGrant, busy, canSubmit, onCancel, onSubmit, kindSelector }: {
  assets: Asset[]; catalog: DiagnosticAction[]; initialGrant: DiagnosticGrant | null; busy: boolean;
  canSubmit: boolean; onCancel: () => void; onSubmit: (values: KubernetesDiagnosticGrantInput) => void; kindSelector?: ReactNode;
}) {
  const [form] = Form.useForm<GrantValues>();
  const values = Form.useWatch([], form) as Partial<GrantValues> | undefined;
  const [current, setCurrent] = useState(initialGrant);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const generation = useRef(0);
  const enabledAssets = assets.filter((item) => item.lifecycle_status === 'enabled'
    && item.credential_id == null && item.credential == null);
  const asset = enabledAssets.find((item) => item.id === values?.asset_id);
  const available = catalog.filter((item) => actionIds.includes(item.id) && item.available && item.read_only);
  const writeValues = (grant: DiagnosticGrant | null, assetId?: string) => {
    setCurrent(grant); form.resetFields();
    const config = grant?.kubernetes_config;
    const target = enabledAssets.find((item) => item.id === (assetId ?? grant?.asset_id));
    const host = target?.host;
    form.setFieldsValue({ asset_id: assetId ?? grant?.asset_id,
      endpoint: config?.endpoint ?? (host ? `https://${host.includes(':') && !host.startsWith('[') ? `[${host}]` : host}:6443` : ''),
      service_account_namespace: config?.service_account_namespace ?? '', service_account_name: config?.service_account_name ?? '',
      namespace_text: config?.namespaces.join(', ') ?? '', action_ids: grant?.action_ids ?? [],
      expires_at: grant ? dayjs(grant.expires_at) : undefined, token: '', ca_pem: '', confirmation: '', reason: '', rbac_confirmed: false });
  };
  useEffect(() => { writeValues(initialGrant); return () => { generation.current += 1; }; }, []);
  const selectAsset = async (assetId: string) => {
    const requestId = ++generation.current;
    writeValues(null, assetId); setLoadError(''); setLoading(true);
    try {
      const result = await getDiagnosticGrants(0, assetId);
      if (requestId !== generation.current) return;
      const next = result.items.find((item) => item.asset_id === assetId) ?? null;
      if (next && next.kind !== 'kubernetes') setLoadError('该资产已有 Linux 授权，不允许转换授权类型');
      else writeValues(next, assetId);
    } catch {
      if (requestId === generation.current) setLoadError('资产授权状态加载失败，请刷新后重试');
    } finally { if (requestId === generation.current) setLoading(false); }
  };
  const config = current?.kubernetes_config;
  const tokenValue = values?.token?.trim() ?? '';
  const caValue = values?.ca_pem?.trim() ?? '';
  const identityChanged = values?.endpoint?.trim() !== config?.endpoint
    || values?.service_account_namespace?.trim() !== config?.service_account_namespace
    || values?.service_account_name?.trim() !== config?.service_account_name;
  const needsToken = !current?.secret_configured || identityChanged || Boolean(caValue);
  const names = namespacesOf(values?.namespace_text);
  const ready = Boolean(asset && canSubmit && !busy && !loading && !loadError && validEndpoint(values?.endpoint?.trim() ?? '', asset)
    && validName(values?.service_account_namespace?.trim() ?? '') && validName(values?.service_account_name?.trim() ?? '')
    && validNamespaces(names) && validExpiry(values?.expires_at) && (values?.action_ids?.length ?? 0) >= 1
    && (values?.action_ids?.length ?? 0) <= 3 && values?.action_ids?.every((id) => available.some((item) => item.id === id))
    && (tokenValue ? validToken(tokenValue, values) : !needsToken)
    && (caValue ? validCa(values?.ca_pem ?? '') : Boolean(current?.ca_fingerprint))
    && values?.confirmation?.trim() === asset.name && (values?.reason?.trim().length ?? 0) >= 3
    && (values?.reason?.trim().length ?? 0) <= 500 && values?.rbac_confirmed === true);
  const submit = (input: GrantValues) => {
    if (!ready || !asset || !input.expires_at) return;
    onSubmit({ kind: 'kubernetes', asset_id: asset.id, expected_revision: current?.revision ?? 0,
      endpoint: input.endpoint.trim(), service_account_namespace: input.service_account_namespace.trim(),
      service_account_name: input.service_account_name.trim(), namespaces: namespacesOf(input.namespace_text),
      action_ids: input.action_ids, expires_at: input.expires_at.toISOString(),
      confirmation: input.confirmation.trim(), reason: input.reason.trim(), rbac_confirmed: true,
      ...(tokenValue ? { token: tokenValue } : {}), ...(caValue ? { ca_pem: caValue } : {}) });
  };
  return <Modal title="配置 Kubernetes 只读授权" open onCancel={() => { form.resetFields(); onCancel(); }}
    onOk={() => form.submit()} okText="保存只读授权" confirmLoading={busy}
    okButtonProps={{ disabled: !ready }} cancelButtonProps={{ disabled: busy }}
    closable={!busy} maskClosable={!busy} destroyOnHidden width={680}>
    {kindSelector}
    <Alert type="info" showIcon message="仅采集指定命名空间的 Pod、工作负载与事件；不支持 Secrets、日志、exec 或节点采集。" />
    {loadError ? <Alert type="error" showIcon message={loadError} /> : null}
    {loading ? <Spin size="small" /> : null}
    <Form form={form} layout="vertical" disabled={busy || loading} onFinish={submit} style={{ marginTop: 16 }}>
      <Form.Item name="asset_id" label="集群映射资产" extra="选择未绑定 SSH 凭据的现有资产作为集群 API 映射。">
        <Select aria-label="授权服务器" showSearch optionFilterProp="label"
        onChange={(id: string) => void selectAsset(id)} options={enabledAssets.map((item) => ({
          value: item.id, label: `${item.name} · ${item.host}`,
        }))} /></Form.Item>
      <Form.Item name="endpoint" label="API Server HTTPS 地址" extra="HTTPS 地址必须绑定资产 IP；可指定端口，省略时为 443；不允许路径。"
        validateStatus={values?.endpoint && !validEndpoint(values.endpoint.trim(), asset) ? 'error' : undefined}
        help={values?.endpoint && !validEndpoint(values.endpoint.trim(), asset) ? '需使用资产 IP；不允许域名、不安全 IP 或路径（含末尾 /）' : undefined}>
        <Input aria-label="API Server HTTPS 地址" autoComplete="off" maxLength={200} />
      </Form.Item>
      <Form.Item name="service_account_namespace" label="ServiceAccount 所在命名空间">
        <Input aria-label="ServiceAccount 所在命名空间" autoComplete="off" maxLength={63} />
      </Form.Item>
      <Form.Item name="service_account_name" label="ServiceAccount 名称">
        <Input aria-label="ServiceAccount 名称" autoComplete="off" maxLength={63} />
      </Form.Item>
      <Form.Item name="namespace_text" label="允许的命名空间" extra="1 至 3 个明确命名空间，以逗号分隔；不接受通配符或全部集群。"
        validateStatus={values?.namespace_text && !validNamespaces(names) ? 'error' : undefined}
        help={values?.namespace_text && !validNamespaces(names) ? '需填写 1 至 3 个不重复的 DNS-label 名称' : undefined}>
        <Input aria-label="允许的命名空间" autoComplete="off" maxLength={200} />
      </Form.Item>
      <Form.Item name="action_ids" label="允许采集的证据"><Checkbox.Group options={available.map((item) => ({
        value: item.id, label: item.label,
      }))} /></Form.Item>
      <Form.Item name="token" label="ServiceAccount Token"
        validateStatus={tokenValue && !validToken(tokenValue, values) ? 'error' : undefined}
        help={tokenValue && !validToken(tokenValue, values) ? '需使用所填专用 SA 的 JWT，且有效期覆盖授权有效期' : undefined}
        extra={<>{current?.secret_configured ? '仅目标、SA 身份及 CA 不变时可留空保留；变更时必须提供新 Token。' : '首次授权必须提供专用 SA JWT；有效期需覆盖授权有效期。'}
          <div>本地仅检查 JWT 的 SA 身份与有效期，不验证签名；真实身份由 Kubernetes API 认证。</div></>}>
        <Input.Password aria-label="ServiceAccount Token" autoComplete="new-password" maxLength={16384}
          visibilityToggle={false} placeholder={current?.secret_configured ? '********' : ''} />
      </Form.Item>
      <Form.Item name="ca_pem" label="集群 CA PEM"
        validateStatus={caValue && !validCa(values?.ca_pem ?? '') ? 'error' : undefined}
        help={caValue && !validCa(values?.ca_pem ?? '') ? '需提供 1 至 5 份 PEM 证书，总长度不超过 32768 字符' : undefined}
        extra={current?.ca_fingerprint ? `留空保留 CA：${current.ca_fingerprint}；更新 CA 必须同时更新 Token。` : '首次授权必须提供集群 CA 证书链。'}>
        <Input.TextArea aria-label="集群 CA PEM" rows={3} autoComplete="off" maxLength={32768} />
      </Form.Item>
      <Form.Item name="expires_at" label="授权有效期"><DatePicker aria-label="授权有效期" showTime needConfirm={false}
        format="YYYY-MM-DD HH:mm:ss" minDate={dayjs().startOf('day')} maxDate={dayjs().add(90, 'day')} style={{ width: '100%' }} />
      </Form.Item>
      <Form.Item name="reason" label="授权原因"><Input.TextArea aria-label="授权原因" rows={2} maxLength={500} /></Form.Item>
      <Form.Item name="confirmation" label="资产名称确认" extra={`需输入：${asset?.name ?? '请先选择集群映射资产'}`}>
        <Input aria-label="资产名称确认" autoComplete="off" maxLength={100} />
      </Form.Item>
      <Form.Item name="rbac_confirmed" valuePropName="checked" extra="管理员人工核对 RBAC；平台不证明该身份没有额外权限。">
        <Checkbox aria-label="已人工核对专用 ServiceAccount 只读 RBAC">已人工核对专用 ServiceAccount 只读 RBAC</Checkbox>
      </Form.Item>
    </Form>
  </Modal>;
}
