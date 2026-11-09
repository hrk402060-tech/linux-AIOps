import { useState } from 'react';
import { Button, Drawer, Form, Input, Space, Upload } from 'antd';
import { FileCheck2, ShieldCheck, Upload as UploadIcon } from 'lucide-react';

import type { KnowledgeImportPayload } from '../types/knowledge';

interface Props {
  open: boolean;
  submitting: boolean;
  onClose: () => void;
  onSubmit: (payload: KnowledgeImportPayload) => Promise<void>;
}

interface FormValues {
  key_id: string;
}

function encodeBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 8192) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
  }
  return btoa(binary);
}

export function KnowledgeImportDrawer({ open, submitting, onClose, onSubmit }: Props) {
  const [form] = Form.useForm<FormValues>();
  const [dataFile, setDataFile] = useState<File | null>(null);
  const [signatureFile, setSignatureFile] = useState<File | null>(null);

  const close = () => {
    form.resetFields();
    setDataFile(null);
    setSignatureFile(null);
    onClose();
  };

  const submit = async ({ key_id }: FormValues) => {
    if (!dataFile || !signatureFile) return;
    await onSubmit({
      filename: dataFile.name,
      package_content: encodeBase64(await dataFile.arrayBuffer()),
      signature: encodeBase64(await signatureFile.arrayBuffer()),
      key_id,
    });
    close();
  };

  return (
    <Drawer title="导入离线签名包" open={open} width="min(500px, 100vw)" onClose={close} destroyOnClose
      footer={<div className="drawer-footer"><span className="encryption-label"><ShieldCheck size={15} />Ed25519 签名校验</span><Space><Button onClick={close}>取消</Button><Button type="primary" loading={submitting} disabled={!dataFile || !signatureFile} onClick={() => form.submit()}>校验并导入</Button></Space></div>}>
      <Form form={form} layout="vertical" requiredMark="optional" onFinish={submit}>
        <Form.Item label="漏洞数据包" required>
          <Upload maxCount={1} accept=".json,application/json" beforeUpload={(file) => { setDataFile(file); return false; }} onRemove={() => { setDataFile(null); }}>
            <Button aria-label="漏洞数据包" icon={<UploadIcon size={16} />}>选择 JSON 数据包</Button>
          </Upload>
        </Form.Item>
        <Form.Item label="签名文件" required>
          <Upload maxCount={1} accept=".sig,.signature,application/octet-stream" beforeUpload={(file) => { setSignatureFile(file); return false; }} onRemove={() => { setSignatureFile(null); }}>
            <Button aria-label="签名文件" icon={<FileCheck2 size={16} />}>选择原始签名文件</Button>
          </Upload>
        </Form.Item>
        <Form.Item name="key_id" label="可信密钥标识" rules={[{ required: true, whitespace: true, message: '请输入服务端登记的密钥标识' }, { max: 100 }]}>
          <Input placeholder="例如：openeuler-2026" autoComplete="off" />
        </Form.Item>
      </Form>
    </Drawer>
  );
}
