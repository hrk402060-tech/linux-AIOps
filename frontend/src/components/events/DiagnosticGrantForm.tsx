import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Alert, Checkbox, DatePicker, Form, Input, Modal, Select, Spin } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { getDiagnosticGrants, type DiagnosticAction, type DiagnosticGrant, type DiagnosticGrantInput } from '../../api/diagnostics';
import type { Asset } from '../../types/asset';

interface GrantValues {
  asset_id: string; username: string; private_key?: string; action_ids: string[];
  expires_at?: Dayjs; confirmation: string; reason: string; gateway_confirmed: boolean;
}
const validIdentity = (value: string, asset?: Asset) => /^[a-z_][a-z0-9_-]{0,63}$/.test(value)
  && !['root', 'admin', 'administrator'].includes(value) && value !== asset?.credential?.username;
const validExpiry = (value?: Dayjs) => Boolean(value?.isValid() && value.valueOf() > Date.now()
  && value.valueOf() <= Date.now() + 90 * 86_400_000);
const validPrivateKey = (value?: string) => Boolean(value?.trim() && value.trim().length >= 50 && value.length <= 16384);

export function DiagnosticGrantForm({ assets, catalog, initialGrant, busy, canSubmit, onCancel, onSubmit, kindSelector }: {
  assets: Asset[]; catalog: DiagnosticAction[]; initialGrant: DiagnosticGrant | null;
  busy: boolean; canSubmit: boolean; onCancel: () => void; onSubmit: (values: DiagnosticGrantInput) => void;
  kindSelector?: ReactNode;
}) {
  const [form] = Form.useForm<GrantValues>();
  const values = Form.useWatch([], form) as Partial<GrantValues> | undefined;
  const [current, setCurrent] = useState(initialGrant);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const generation = useRef(0);
  const enabledAssets = assets.filter((asset) => asset.lifecycle_status === 'enabled');
  const asset = enabledAssets.find((item) => item.id === values?.asset_id);
  const available = catalog.filter((action) => action.domain === 'linux' && action.available && action.read_only);
  const writeValues = (grant: DiagnosticGrant | null, assetId?: string) => {
    setCurrent(grant);
    form.resetFields();
    form.setFieldsValue({ asset_id: assetId ?? grant?.asset_id, username: grant?.username ?? '', private_key: '',
      action_ids: grant?.action_ids ?? [], expires_at: grant ? dayjs(grant.expires_at) : undefined,
      confirmation: '', reason: '', gateway_confirmed: false });
  };
  useEffect(() => { writeValues(initialGrant); return () => { generation.current += 1; }; }, []);
  const selectAsset = async (assetId: string) => {
    const requestId = ++generation.current;
    writeValues(null, assetId); setLoadError(''); setLoading(true);
    try {
      const result = await getDiagnosticGrants(0, assetId);
      if (requestId === generation.current) writeValues(result.items.find((item) => item.asset_id === assetId) ?? null, assetId);
    } catch (error) {
      if (requestId === generation.current) setLoadError(error instanceof Error ? error.message : '资产授权状态加载失败');
    } finally { if (requestId === generation.current) setLoading(false); }
  };
  const ready = Boolean(asset && canSubmit && !busy && !loading && !loadError
    && validIdentity(values?.username?.trim() ?? '', asset) && validExpiry(values?.expires_at)
    && (values?.action_ids?.length ?? 0) > 0
    && values?.action_ids?.every((id) => available.some((action) => action.id === id))
    && (values?.private_key?.trim() ? validPrivateKey(values.private_key) : current?.secret_configured)
    && values?.confirmation?.trim() === asset.name && (values?.reason?.trim().length ?? 0) >= 3
    && values?.gateway_confirmed === true);
  const submit = (input: GrantValues) => {
    if (!ready || !input.expires_at || !asset) return;
    onSubmit({ asset_id: asset.id, username: input.username.trim(), action_ids: input.action_ids,
      expires_at: input.expires_at.toISOString(), expected_revision: current?.revision ?? 0,
      confirmation: input.confirmation.trim(), reason: input.reason.trim(), gateway_confirmed: true,
      ...(input.private_key?.trim() ? { private_key: input.private_key } : {}) });
  };
  return <Modal title="配置只读诊断授权" open onCancel={onCancel} onOk={() => form.submit()}
    okText="保存只读授权" confirmLoading={busy} okButtonProps={{ disabled: !ready }}
    cancelButtonProps={{ disabled: busy }} closable={!busy} maskClosable={!busy} destroyOnHidden width={620}>
    {kindSelector}
    <Alert type="info" showIcon message="专用诊断身份仅允许固定 Linux 只读采集；不会复用人工 SSH 凭据。" />
    <p>目标服务器需由管理员安装限制网关，并为专用公钥配置 forced-command，禁用终端与转发；本次保存不会自动授权服务器。</p>
    {loadError ? <Alert type="error" showIcon message={loadError} /> : null}
    {loading ? <Spin size="small" /> : null}
    <Form form={form} layout="vertical" onFinish={submit} disabled={busy || loading} style={{ marginTop: 16 }}>
      <Form.Item name="asset_id" label="服务器" rules={[{ required: true }]}>
        <Select aria-label="授权服务器" showSearch optionFilterProp="label" onChange={(id: string) => void selectAsset(id)}
          options={enabledAssets.map((item) => ({ value: item.id, label: `${item.name} · ${item.host}` }))} />
      </Form.Item>
      <Form.Item name="username" label="专用只读账号" rules={[{ required: true }, {
        validator: (_, value: string) => validIdentity(value?.trim() ?? '', asset) ? Promise.resolve()
          : Promise.reject(new Error('需使用非 root 且不同于人工 SSH 的 Linux 账号')),
      }]}><Input aria-label="专用只读账号" maxLength={64} autoComplete="off" /></Form.Item>
      <Form.Item name="private_key" label="诊断私钥" extra={current?.secret_configured ? '留空保留已加密私钥' : '首次授权必须提供专用私钥'}
        rules={[{ required: !current?.secret_configured, whitespace: true, message: '请输入专用诊断私钥' }, {
          validator: (_, value: string) => !value?.trim() || validPrivateKey(value) ? Promise.resolve()
            : Promise.reject(new Error('私钥长度必须为 50 至 16384 个字符')),
        }]}>
        <Input.TextArea aria-label="诊断私钥" rows={3} autoComplete="off" maxLength={16384} placeholder={current?.secret_configured ? '********' : ''} />
      </Form.Item>
      <Form.Item name="action_ids" label="允许采集的证据" rules={[{ required: true, type: 'array', min: 1 }]}>
        <Checkbox.Group options={catalog.map((action) => ({ value: action.id,
          label: action.available && action.domain === 'linux' && action.read_only ? action.label : `${action.label}（暂未开放）`,
          disabled: !available.some((item) => item.id === action.id) }))} />
      </Form.Item>
      <Form.Item name="expires_at" label="授权有效期" rules={[{ validator: (_, value: Dayjs) => validExpiry(value)
        ? Promise.resolve() : Promise.reject(new Error('有效期必须晚于当前时间且不超过 90 天')) }]}>
        <DatePicker aria-label="授权有效期" showTime needConfirm={false} format="YYYY-MM-DD HH:mm:ss" minDate={dayjs().startOf('day')}
          maxDate={dayjs().add(90, 'day')} style={{ width: '100%' }} />
      </Form.Item>
      <Form.Item name="reason" label="授权原因" rules={[{ required: true }, { validator: (_, value: string) => (value?.trim().length ?? 0) >= 3
        ? Promise.resolve() : Promise.reject(new Error('请输入至少 3 个字符的原因')) }]}>
        <Input.TextArea aria-label="授权原因" rows={2} maxLength={500} />
      </Form.Item>
      <Form.Item name="confirmation" label="资产名称确认" extra={`需输入：${asset?.name ?? '请先选择服务器'}`}>
        <Input aria-label="资产名称确认" autoComplete="off" maxLength={100} />
      </Form.Item>
      <Form.Item name="gateway_confirmed" valuePropName="checked">
        <Checkbox aria-label="已在目标服务器人工安装限制网关">已在目标服务器人工安装限制网关</Checkbox>
      </Form.Item>
    </Form>
  </Modal>;
}
