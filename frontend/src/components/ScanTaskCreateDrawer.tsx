import { Button, Drawer, Form, Input, Segmented, Select } from 'antd';
import { LockKeyhole } from 'lucide-react';

import type { Asset } from '../types/asset';
import type { ScanTaskCreate, ScanType } from '../types/scan-task';

interface Props {
  open: boolean;
  assets: Asset[];
  submitting: boolean;
  onClose: () => void;
  onSubmit: (payload: ScanTaskCreate) => Promise<void>;
}

export function ScanTaskCreateDrawer({ open, assets, submitting, onClose, onSubmit }: Props) {
  const [form] = Form.useForm<ScanTaskCreate>();
  const close = () => { form.resetFields(); onClose(); };
  return (
    <Drawer
      title="新建扫描任务"
      open={open}
      onClose={close}
      width={500}
      destroyOnHidden
      footer={<div className="drawer-footer"><span className="encryption-label"><LockKeyhole size={14} />只读、非破坏执行</span><div><Button onClick={close}>取消</Button><Button type="primary" loading={submitting} onClick={() => form.submit()}>创建任务</Button></div></div>}
    >
      <Form form={form} layout="vertical" initialValues={{ scan_type: 'comprehensive' }} onFinish={onSubmit}>
        <Form.Item name="name" label="任务名称" rules={[{ required: true, message: '请输入任务名称' }, { min: 2, max: 120 }]}>
          <Input placeholder="例如：生产区每周漏洞检查" />
        </Form.Item>
        <Form.Item name="scan_type" label="扫描类型" rules={[{ required: true }]}>
          <Segmented<ScanType> block options={[
            { label: '主机漏洞', value: 'host_vulnerability' },
            { label: '服务漏洞', value: 'service_vulnerability' },
            { label: '综合扫描', value: 'comprehensive' },
          ]} />
        </Form.Item>
        <Form.Item name="asset_ids" label="目标资产" rules={[{ required: true, message: '请选择至少一个资产' }]}>
          <Select
            mode="multiple"
            optionFilterProp="label"
            placeholder="选择已登记的 Linux 资产"
            options={assets.map((item) => ({ value: item.id, label: item.name + ' · ' + item.host }))}
          />
        </Form.Item>
      </Form>
    </Drawer>
  );
}
