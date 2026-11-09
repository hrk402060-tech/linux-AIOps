import { Form, Input, Modal, Select } from 'antd';
import { useEffect } from 'react';

import type {
  CmdbApplicationTree, CmdbBusinessTree, CmdbEntityPayload, CmdbEntityRow, CmdbEntityType,
  CmdbServiceTree,
} from '../types/cmdb';

const titles: Record<CmdbEntityType, string> = {
  business: '业务系统', application: '应用', service: '服务', api: 'API',
};

interface Props {
  type: CmdbEntityType | null;
  editing: CmdbEntityRow | null;
  businesses: CmdbBusinessTree[];
  applications: CmdbApplicationTree[];
  services: CmdbServiceTree[];
  submitting: boolean;
  onClose: () => void;
  onSubmit: (type: CmdbEntityType, payload: CmdbEntityPayload) => Promise<void>;
}

export function CmdbEntityModal({ type, editing, businesses, applications, services, submitting, onClose, onSubmit }: Props) {
  const [form] = Form.useForm<CmdbEntityPayload>();
  useEffect(() => {
    if (!type) return;
    const entity = editing?.entity;
    form.setFieldsValue({
      code: entity?.code, name: entity?.name, description: entity?.description ?? undefined,
      business_id: editing?.application?.business_id ?? editing?.business.id,
      application_id: editing?.service?.application_id ?? (editing?.type === 'application' ? editing.entity.id : undefined),
      service_id: editing?.type === 'service' ? editing.entity.id : undefined,
      service_type: editing?.service?.service_type ?? 'api',
      http_method: editing?.api?.http_method ?? 'GET',
      path: editing?.api?.path,
    });
  }, [editing, form, type]);
  const close = () => { form.resetFields(); onClose(); };
  return <Modal title={`${editing ? '编辑' : '新建'}${type ? titles[type] : ''}`} open={type !== null} confirmLoading={submitting}
    okText="保存" onCancel={close} onOk={() => void form.validateFields().then((values) => type && onSubmit(type, values))} destroyOnHidden>
    <Form form={form} layout="vertical">
      {type === 'application' ? <Form.Item name="business_id" label="所属业务系统" rules={[{ required: true }]}><Select disabled={Boolean(editing)} options={businesses.filter((item) => item.lifecycle_status === 'active').map((item) => ({ value: item.id, label: `${item.name} · ${item.code}` }))} /></Form.Item> : null}
      {type === 'service' ? <Form.Item name="application_id" label="所属应用" rules={[{ required: true }]}><Select disabled={Boolean(editing)} options={applications.filter((item) => item.lifecycle_status === 'active').map((item) => ({ value: item.id, label: `${item.name} · ${item.code}` }))} /></Form.Item> : null}
      {type === 'api' ? <Form.Item name="service_id" label="所属服务" rules={[{ required: true }]}><Select disabled={Boolean(editing)} options={services.filter((item) => item.lifecycle_status === 'active').map((item) => ({ value: item.id, label: `${item.name} · ${item.code}` }))} /></Form.Item> : null}
      <div className="form-grid equal"><Form.Item name="name" label="名称" rules={[{ required: true }, { min: 2 }]}><Input maxLength={120} /></Form.Item><Form.Item name="code" label="编码" rules={[{ required: true }, { pattern: /^[A-Za-z0-9._-]+$/, message: '仅支持字母、数字、点、下划线和短横线' }]}><Input maxLength={64} /></Form.Item></div>
      {type === 'service' ? <Form.Item name="service_type" label="服务类型" rules={[{ required: true }]}><Select options={['api', 'web', 'worker', 'database', 'middleware', 'other'].map((value) => ({ value, label: value.toUpperCase() }))} /></Form.Item> : null}
      {type === 'api' ? <div className="form-grid equal"><Form.Item name="http_method" label="HTTP 方法" rules={[{ required: true }]}><Select options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((value) => ({ value, label: value }))} /></Form.Item><Form.Item name="path" label="路径" rules={[{ required: true }, { pattern: /^\//, message: '路径必须以 / 开头' }]}><Input maxLength={240} /></Form.Item></div> : null}
      <Form.Item name="description" label="说明"><Input.TextArea rows={3} maxLength={500} /></Form.Item>
    </Form>
  </Modal>;
}
