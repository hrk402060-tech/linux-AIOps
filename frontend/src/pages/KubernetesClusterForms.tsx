import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Form, Input, Modal, Space, Tag } from 'antd';
import { TestTube2 } from 'lucide-react';
import {
  kubernetesFailureMessage, kubernetesRequestError, testKubernetesClusterDraft,
  type KubernetesCluster, type KubernetesClusterCreateInput, type KubernetesClusterDraftInput,
  type KubernetesClusterTestResult, type KubernetesClusterUpdateInput, type KubernetesCredentialRotateInput,
} from '../api/kubernetes';

interface ConnectionValues {
  name: string; service_id?: string; endpoint?: string; context: string; kubeconfig: string; reason?: string;
}
type ConnectionProof = { generation: number; result: KubernetesClusterTestResult };
const testLabels: Record<string, string> = {
  tls: 'TLS 证书', version: '集群版本', identity: '身份验证',
  resource_permissions: '读取授权', namespace_permissions: '命名空间授权', metrics: '指标接口',
};
const testStatusLabels: Record<string, string> = {
  passed: '通过', failed: '失败', pending: '待确认', degraded: '不可用',
};
const hasText = (value?: string) => Boolean(value?.trim());
const validProof = (proof: ConnectionProof | null, generation: number): proof is ConnectionProof =>
  Boolean(proof?.generation === generation && proof.result.status === 'passed' && proof.result.test_id?.trim());

export function KubernetesConnectionForm({ cluster, busy, onCancel, onCreate, onRotate }: {
  cluster: KubernetesCluster | null; busy: boolean; onCancel: () => void;
  onCreate: (payload: KubernetesClusterCreateInput) => Promise<void>;
  onRotate: (payload: KubernetesCredentialRotateInput) => Promise<void>;
}) {
  const [form] = Form.useForm<ConnectionValues>();
  const values = Form.useWatch([], form) as Partial<ConnectionValues> | undefined;
  const [proof, setProof] = useState<ConnectionProof | null>(null);
  const [testError, setTestError] = useState('');
  const [testing, setTesting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const generation = useRef(0);
  const testPending = useRef(false);
  const submitPending = useRef(false);
  const mounted = useRef(true);
  const rotating = cluster !== null;
  const locked = busy || submitting;
  const verified = validProof(proof, generation.current);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; generation.current += 1; form.resetFields(); };
  }, [form]);
  const invalidate = () => {
    generation.current += 1; setProof(null); setTestError('');
  };
  const runTest = async () => {
    if (testPending.current || locked || !hasText(values?.context) || !hasText(values?.kubeconfig)) return;
    testPending.current = true;
    const current = ++generation.current;
    setProof(null); setTestError(''); setTesting(true);
    const payload: KubernetesClusterDraftInput = {
      kubeconfig: form.getFieldValue('kubeconfig'), context: form.getFieldValue('context').trim(),
      endpoint: form.getFieldValue('endpoint')?.trim() || undefined, reason: '保存前连接测试',
    };
    try {
      const result = await testKubernetesClusterDraft(payload);
      if (!mounted.current || current !== generation.current) return;
      setProof({ generation: current, result });
      if (result.status !== 'passed') setTestError(kubernetesFailureMessage(result.error_code, '连接测试未通过，请检查各项结果'));
      else if (!result.test_id?.trim()) setTestError('测试凭据缺失，请重新测试');
    } catch (failure) {
      if (mounted.current && current === generation.current) setTestError(kubernetesRequestError(failure));
    } finally {
      testPending.current = false;
      if (mounted.current) setTesting(false);
    }
  };
  const submit = async (input: ConnectionValues) => {
    if (submitPending.current || locked || testing || !validProof(proof, generation.current)) return;
    submitPending.current = true; setSubmitting(true);
    const testId = proof.result.test_id!.trim();
    const connection = { kubeconfig: input.kubeconfig, context: input.context.trim(),
      endpoint: input.endpoint?.trim() || undefined, test_id: testId };
    generation.current += 1; setProof(null); setTestError('');
    form.setFieldsValue({ kubeconfig: '' });
    try {
      if (cluster) await onRotate({ ...connection, expected_revision: cluster.revision, reason: input.reason!.trim() });
      else await onCreate({ ...connection, name: input.name.trim(), service_id: input.service_id?.trim() || undefined });
    } finally {
      submitPending.current = false;
      if (mounted.current) setSubmitting(false);
    }
  };
  return <Modal title={rotating ? '轮换 Kubernetes 凭据' : '接入 Kubernetes 集群'} open destroyOnHidden
    onCancel={onCancel} onOk={() => form.submit()} okText={rotating ? '保存新凭据' : '接入并保存'}
    confirmLoading={locked} okButtonProps={{ disabled: locked || testing || !verified }}
    cancelButtonProps={{ disabled: locked }} closable={!locked} maskClosable={!locked} width={680}>
    <Form form={form} layout="vertical" disabled={locked} onFinish={(input) => void submit(input)}
      onValuesChange={(changed: Partial<ConnectionValues>) => {
        if (['endpoint', 'context', 'kubeconfig'].some((key) => Object.hasOwn(changed, key))) invalidate();
      }} initialValues={{ endpoint: cluster?.endpoint ?? '', context: cluster?.context ?? '' }}>
      {rotating ? null : <Form.Item name="name" label="集群名称" rules={[{ required: true, whitespace: true, max: 100, message: '请输入不超过 100 字符的集群名称' }]}>
        <Input autoComplete="off" maxLength={100} /></Form.Item>}
      {rotating ? null : <Form.Item name="service_id" label="关联业务服务 ID" rules={[{ max: 36, message: '业务服务 ID 不超过 36 字符' }]}>
        <Input autoComplete="off" maxLength={36} /></Form.Item>}
      <Form.Item name="endpoint" label="API Server 地址" rules={[{ max: 512, message: '地址不超过 512 字符' }]}>
        <Input autoComplete="off" maxLength={512} placeholder="留空时使用所选 context 的地址" /></Form.Item>
      <Form.Item name="context" label="kubeconfig context" rules={[{ required: true, whitespace: true, max: 128, message: '请输入不超过 128 字符的 context' }]}>
        <Input autoComplete="off" maxLength={128} /></Form.Item>
      <Form.Item name="kubeconfig" label="kubeconfig" rules={[{ required: true, whitespace: true, max: 262144, message: '请输入不超过 256 KiB 的 kubeconfig' }]}>
        <Input.TextArea rows={8} autoComplete="off" spellCheck={false} maxLength={262144} /></Form.Item>
      {rotating ? <Form.Item name="reason" label="轮换原因" rules={[{ required: true, whitespace: true, min: 3, max: 500, message: '请输入 3 至 500 字符的轮换原因' }]}>
        <Input.TextArea rows={2} maxLength={500} /></Form.Item> : null}
      <Button icon={<TestTube2 size={15} />} loading={testing} disabled={locked || testing || !hasText(values?.kubeconfig) || !hasText(values?.context)}
        onClick={() => void runTest()}>测试连接</Button>
      {testError ? <Alert style={{ marginTop: 12 }} type="error" showIcon message={testError} /> : null}
      {proof ? <ConnectionTestResult result={proof.result} /> : null}
    </Form>
  </Modal>;
}

export function ConnectionTestResult({ result }: { result: KubernetesClusterTestResult }) {
  return <div aria-label="连接测试结果" style={{ marginTop: 12 }}>
    <Space wrap>
      <Tag color={result.status === 'passed' ? 'green' : 'gold'}>{result.status === 'passed' ? '测试通过' : '测试未通过'}</Tag>
      <span>版本：{result.version || '—'}</span>
      <Tag color={result.metrics_capable ? 'green' : 'default'}>{result.metrics_capable ? '指标可用' : '指标未接入'}</Tag>
    </Space>
    {result.steps?.map((step, index) => <div key={`${step.name}:${index}`} style={{ marginTop: 6 }}>
      {testLabels[step.name] ?? '连接检查'}：{testStatusLabels[step.status] ?? '未确认'}
      {step.code ? ` · ${kubernetesFailureMessage(step.code, '检查未完成')}` : ''}
    </div>)}
  </div>;
}

export function KubernetesMetadataForm({ cluster, busy, onCancel, onSubmit }: {
  cluster: KubernetesCluster; busy: boolean; onCancel: () => void;
  onSubmit: (payload: KubernetesClusterUpdateInput) => Promise<void>;
}) {
  const [form] = Form.useForm<{ name: string; service_id?: string }>();
  return <Modal title="编辑 Kubernetes 集群" open destroyOnHidden onCancel={onCancel}
    onOk={() => form.submit()} okText="保存配置" confirmLoading={busy}
    okButtonProps={{ disabled: busy }} cancelButtonProps={{ disabled: busy }} closable={!busy} maskClosable={!busy}>
    <Form form={form} layout="vertical" disabled={busy} initialValues={{ name: cluster.name, service_id: cluster.service_id ?? '' }}
      onFinish={(input) => void onSubmit({ name: input.name.trim(), service_id: input.service_id?.trim() || null,
        expected_revision: cluster.revision })}>
      <Form.Item name="name" label="集群名称" rules={[{ required: true, whitespace: true, max: 100, message: '请输入集群名称' }]}>
        <Input maxLength={100} autoComplete="off" /></Form.Item>
      <Form.Item name="service_id" label="关联业务服务 ID" rules={[{ max: 36, message: '业务服务 ID 不超过 36 字符' }]}>
        <Input maxLength={36} autoComplete="off" /></Form.Item>
    </Form>
  </Modal>;
}

export function KubernetesActionDialog({ title, action, busy, onCancel, onSubmit }: {
  title: string; action: string; busy: boolean; onCancel: () => void; onSubmit: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState('');
  return <Modal title={title} open destroyOnHidden onCancel={onCancel}
    onOk={() => { if (!busy && reason.trim().length >= 3) void onSubmit(reason.trim()); }}
    okText={action} confirmLoading={busy} okButtonProps={{ disabled: busy || reason.trim().length < 3 }}
    cancelButtonProps={{ disabled: busy }} closable={!busy} maskClosable={!busy}>
    <label htmlFor="kubernetes-action-reason">操作原因</label>
    <Input.TextArea id="kubernetes-action-reason" aria-label="操作原因" value={reason} disabled={busy} rows={3} maxLength={500}
      onChange={(event) => setReason(event.target.value)} style={{ marginTop: 8 }} />
  </Modal>;
}
