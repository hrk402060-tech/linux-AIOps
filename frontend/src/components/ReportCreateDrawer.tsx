import { Button, Drawer, Form, Segmented, Select } from 'antd';
import { FileOutput } from 'lucide-react';

import type { ReportCreate, ReportFormat } from '../types/report';
import type { ScanTask } from '../types/scan-task';

export function ReportCreateDrawer({ open, scans, submitting, onClose, onSubmit }: {
  open: boolean; scans: ScanTask[]; submitting: boolean; onClose: () => void;
  onSubmit: (payload: ReportCreate) => Promise<void>;
}) {
  const [form] = Form.useForm<ReportCreate>();
  const submit = async (values: ReportCreate) => {
    await onSubmit(values);
    form.resetFields();
  };
  return <Drawer title="生成扫描报告" width={460} open={open} onClose={onClose} destroyOnHidden
    footer={<div className="drawer-footer"><span className="encryption-label"><FileOutput size={15} />生成不可变报告快照</span><Button type="primary" loading={submitting} onClick={() => form.submit()}>生成报告</Button></div>}>
    <Form form={form} layout="vertical" initialValues={{ format: 'pdf' }} onFinish={(values) => void submit(values)}>
      <Form.Item name="scan_task_id" label="已完成扫描" rules={[{ required: true, message: '请选择扫描任务' }]}>
        <Select showSearch optionFilterProp="label" placeholder="选择扫描任务" options={scans.map((item) => ({
          value: item.id, label: item.name + ' · ' + item.asset_snapshot.map((asset) => asset.name).join('、'),
        }))} />
      </Form.Item>
      <Form.Item name="format" label="报告格式" rules={[{ required: true }]}>
        <Segmented block options={[{ label: 'PDF', value: 'pdf' }, { label: 'Word', value: 'docx' }]} onChange={(value) => form.setFieldValue('format', value as ReportFormat)} />
      </Form.Item>
    </Form>
  </Drawer>;
}
