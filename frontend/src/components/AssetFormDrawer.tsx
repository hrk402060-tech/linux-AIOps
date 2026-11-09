import { useEffect } from 'react';
import { Button, Drawer, Form, Input, InputNumber, Select, Space } from 'antd';
import { Eye, KeyRound, Server } from 'lucide-react';

import type { Asset, AssetCreate, AssetEnvironment, OsFamily } from '../types/asset';
import { osOptions } from '../utils/assets';

interface AssetFormValues {
  name: string; host: string; ssh_port: number; os_family: OsFamily;
  environment: AssetEnvironment; group_name: string; ssh_username?: string; ssh_credential_type?: 'password' | 'private_key'; ssh_secret?: string; labels: string[];
}

interface AssetFormDrawerProps {
  open: boolean; asset: Asset | null; submitting: boolean;
  onClose: () => void; onSubmit: (payload: AssetCreate) => Promise<void>;
}

const environments = [
  { value: 'production', label: '生产' }, { value: 'staging', label: '预发布' },
  { value: 'test', label: '测试' }, { value: 'development', label: '开发' },
  { value: 'other', label: '其他' },
];

export function AssetFormDrawer(props: AssetFormDrawerProps) {
  const { open, asset, submitting, onClose, onSubmit } = props;
  const [form] = Form.useForm<AssetFormValues>();

  useEffect(() => {
    if (!open) return;
    form.resetFields();
    form.setFieldsValue(asset
      ? { name: asset.name, host: asset.host, ssh_port: asset.ssh_port, os_family: asset.os_family,
        environment: asset.environment, group_name: asset.group_name, labels: asset.labels }
      : { ssh_port: 22, os_family: 'auto', environment: 'production', group_name: '未分组', ssh_credential_type: 'password', labels: [] });
  }, [asset, form, open]);

  const submit = async (values: AssetFormValues) => {
    const { ssh_username, ssh_secret, ssh_credential_type, ...metadata } = values;
    const authentication = ssh_username?.trim() && ssh_secret
      ? { ssh_username: ssh_username.trim(), ssh_secret, ssh_credential_type } : {};
    await onSubmit({ ...metadata, ...authentication, credential_id: null, labels: values.labels ?? [] });
    form.resetFields();
  };

  return (
    <Drawer title={asset ? '编辑资产' : '新建资产'} open={open} width={520} destroyOnClose
      onClose={() => { form.resetFields(); onClose(); }}
      footer={<div className="drawer-footer"><span className="encryption-label"><Eye size={15} />默认只读</span><Space><Button onClick={onClose}>取消</Button><Button type="primary" loading={submitting} onClick={() => form.submit()}>保存</Button></Space></div>}>
      <Form form={form} layout="vertical" requiredMark="optional" onFinish={submit}>
        <Form.Item name="name" label="资产名称" rules={[{ required: true, whitespace: true, message: '请输入资产名称' }, { min: 2, max: 100 }]}>
          <Input prefix={<Server size={16} />} placeholder="例如：openEuler 核心数据库" />
        </Form.Item>
        <div className="form-grid">
          <Form.Item name="host" label="主机地址" rules={[{ required: true, whitespace: true, message: '请输入 IP 或主机名' }]}><Input placeholder="10.20.0.15" /></Form.Item>
          <Form.Item name="ssh_port" label="SSH 端口" rules={[{ required: true }]}><InputNumber min={1} max={65535} /></Form.Item>
        </div>
        <div className="form-grid equal">
          <Form.Item name="os_family" label="操作系统" rules={[{ required: true }]}><Select options={osOptions} showSearch optionFilterProp="label" /></Form.Item>
          <Form.Item name="environment" label="环境" rules={[{ required: true }]}><Select options={environments} /></Form.Item>
        </div>
        <Form.Item name="group_name" label="资产分组" rules={[{ required: true, whitespace: true }]}><Input placeholder="例如：核心业务" /></Form.Item>
        <div className="form-grid equal">
          <Form.Item name="ssh_username" label="SSH 用户名" dependencies={['ssh_secret', 'ssh_credential_type']}
            rules={[({ getFieldValue }) => ({ required: !asset || Boolean(getFieldValue('ssh_secret') || getFieldValue('ssh_credential_type')), whitespace: true, message: '请输入 SSH 用户名' }), { max: 64 }]}>
            <Input prefix={<KeyRound size={16} />} placeholder="例如：root" autoComplete="username" />
          </Form.Item>
          <Form.Item name="ssh_credential_type" label="SSH 认证方式" required={!asset} extra="新增资产必填；编辑时留空保留原认证">
            <Select onChange={() => form.setFieldValue('ssh_secret', undefined)} options={[{ value: 'password', label: '密码' }, { value: 'private_key', label: '私钥' }]} />
          </Form.Item>
        </div>
        <Form.Item noStyle shouldUpdate={(prev, next) => prev.ssh_credential_type !== next.ssh_credential_type}>
          {({ getFieldValue }) => <Form.Item name="ssh_secret" label={getFieldValue('ssh_credential_type') === 'private_key' ? 'SSH 私钥' : 'SSH 密码'} dependencies={['ssh_username', 'ssh_credential_type']} extra={asset ? '留空则保留当前认证信息' : '用于建立 SSH 远程连接，保存后仅服务端加密保存'} rules={[({ getFieldValue: read }) => ({ required: !asset || Boolean(read('ssh_username')?.trim() || read('ssh_credential_type')), message: '请输入 SSH 密码或私钥' }), { max: 16384 }]}>{getFieldValue('ssh_credential_type') === 'private_key' ? <Input.TextArea placeholder={asset ? '留空则保持原凭据' : '粘贴 SSH 私钥'} autoComplete="off" rows={5} /> : <Input.Password placeholder={asset ? '留空则保持原凭据' : '输入登录密码'} autoComplete="new-password" />}</Form.Item>}
        </Form.Item>
        <Form.Item name="labels" label="标签"><Select mode="tags" tokenSeparators={[',']} placeholder="输入后回车，例如 database" maxCount={20} /></Form.Item>
      </Form>
    </Drawer>
  );
}
