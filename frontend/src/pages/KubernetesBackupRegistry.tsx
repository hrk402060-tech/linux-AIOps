import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Form, Input, InputNumber, Pagination, Space, Spin, Tag } from 'antd';
import { FileCheck, Plus } from 'lucide-react';
import { kubernetesRequestError } from '../api/kubernetes';
import {
  listBackups, registerBackup, saveRestoreTarget, verifyBackup, type BackupRegistration, type KubernetesBackup,
} from '../api/kubernetesBackups';

type Fields = Omit<BackupRegistration, 'expected_revision' | 'namespaces'> & { namespaces: string };
const keyPattern = /^[a-zA-Z0-9][a-zA-Z0-9_.-]*(\/[a-zA-Z0-9][a-zA-Z0-9_.-]*)*$/;
const labelPattern = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/;
const statuses = {
  registered: ['待校验', 'default'], verified: ['文件校验通过', 'success'], invalid: ['文件校验失败', 'error'],
};

function Registration({ onSubmit, busy }: { onSubmit: (values: Fields) => void; busy: boolean }) {
  return <Form<Fields> layout="vertical" disabled={busy} onFinish={onSubmit}>
    <div className="k8s-preflight-fields">
      <Form.Item name="name" label="备份名称" rules={[{ required: true, pattern: labelPattern }]}>
        <Input maxLength={63} />
      </Form.Item>
      <Form.Item name="storage_key" label="文件相对路径" rules={[{ required: true, pattern: keyPattern }]}>
        <Input maxLength={255} />
      </Form.Item>
      <Form.Item name="namespaces" label="命名空间（逗号分隔）" rules={[{ required: true, validator: (_, value?: string) => {
        const names = (value ?? '').split(/[,，]/).map((part) => part.trim());
        return names.length <= 500 && names.every((name) => name.length <= 63 && labelPattern.test(name))
          ? Promise.resolve() : Promise.reject(new Error('请输入有效的命名空间'));
      } }]}>
        <Input maxLength={32000} />
      </Form.Item>
      <Form.Item name="sha256" label="SHA-256" rules={[{ required: true, pattern: /^[a-fA-F0-9]{64}$/ }]}>
        <Input maxLength={64} />
      </Form.Item>
      <Form.Item name="byte_size" label="文件大小（字节）"
        rules={[{ required: true, type: 'integer', min: 1, max: 2147483647 }]}>
        <InputNumber min={1} max={2147483647} precision={0} />
      </Form.Item>
      <Form.Item name="include_persistent_volumes" valuePropName="checked" initialValue={false}>
        <Checkbox>包含持久卷数据（登记值）</Checkbox>
      </Form.Item>
    </div>
    <Button type="primary" htmlType="submit" loading={busy}>保存登记</Button>
  </Form>;
}

function RestoreTarget({ item, busy, onSubmit }: {
  item: KubernetesBackup; busy: boolean; onSubmit: (value: { target_key: string; reason: string }) => void;
}) {
  return <Form layout="vertical" disabled={busy} initialValues={item.restore_target ?? undefined} onFinish={onSubmit}>
    <div className="k8s-preflight-fields">
      <Form.Item name="target_key" label="候选目标标识" rules={[{ required: true, pattern: keyPattern }]}>
        <Input maxLength={255} />
      </Form.Item>
      <Form.Item name="reason" label="登记原因" rules={[{ required: true, whitespace: true }]}>
        <Input maxLength={500} />
      </Form.Item>
    </div>
    <Button htmlType="submit" loading={busy}>保存候选目标</Button>
  </Form>;
}

export function KubernetesBackupRegistry({ clusterId, revision, mode, onChanged }: {
  clusterId: string; revision: number; mode: 'backup' | 'restore'; onChanged?: () => void;
}) {
  const [data, setData] = useState<Awaited<ReturnType<typeof listBackups>>>();
  const [page, setPage] = useState(1);
  const [reload, setReload] = useState(0);
  const [editing, setEditing] = useState<string>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const mutation = useRef<AbortController>();

  useEffect(() => {
    const controller = new AbortController();
    setError('');
    setData(undefined);
    listBackups(clusterId, (page - 1) * 10, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setData(value); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(kubernetesRequestError(reason)); });
    return () => controller.abort();
  }, [clusterId, page, reload]);
  useEffect(() => () => mutation.current?.abort(), [clusterId, revision, mode]);

  async function change(action: (signal: AbortSignal) => Promise<KubernetesBackup>, close: boolean) {
    if (mutation.current) return;
    const controller = new AbortController();
    mutation.current = controller;
    setError('');
    setBusy(true);
    try {
      const item = await action(controller.signal);
      if (controller.signal.aborted) return;
      if (close) setEditing(undefined);
      onChanged?.();
      if (data?.items.some((entry) => entry.id === item.id)) {
        setData({ ...data, items: data.items.map((entry) => entry.id === item.id ? item : entry) });
      } else {
        setPage(1);
        setReload((value) => value + 1);
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(kubernetesRequestError(reason));
    } finally {
      if (mutation.current === controller) mutation.current = undefined;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <section className="k8s-backup-registry" aria-label="备份记录">
    <div className="k8s-helm-approval-heading">
      <h3>备份记录</h3>
      {mode === 'backup' && <Button icon={<Plus size={16} />} disabled={busy}
        onClick={() => setEditing(editing === 'new' ? undefined : 'new')}>
        {editing === 'new' ? '收起登记' : '登记已有备份'}
      </Button>}
    </div>
    {editing === 'new' && <Registration busy={busy} onSubmit={(fields) => void change((signal) =>
      registerBackup(clusterId, {
        ...fields, expected_revision: revision, sha256: fields.sha256.toLowerCase(),
        namespaces: fields.namespaces.split(/[,，]/).map((value) => value.trim()),
      }, signal), true)} />}
    {error && <Alert type="error" showIcon message={error}
      action={!data && <Button onClick={() => setReload((value) => value + 1)}>重试</Button>} />}
    {!data && !error && <Spin aria-label="正在读取备份记录" />}
    {data?.total === 0 && <div>暂无备份记录</div>}
    {data?.items.map((item) => <div key={item.id} className="k8s-backup-row">
      <Space wrap>
        <strong>{item.name}</strong>
        <Tag color={statuses[item.status][1]}>{statuses[item.status][0]}</Tag>
        {item.cluster_revision !== revision && <Tag>旧集群修订</Tag>}
        <span>{item.namespaces.join('、')}</span>
        <Button disabled={busy} icon={<FileCheck size={15} />}
          onClick={() => void change((signal) => verifyBackup(clusterId, item.id, signal), false)}>校验文件</Button>
        {mode === 'restore' && item.status === 'verified' && item.cluster_revision === revision &&
          <Button disabled={busy} onClick={() => setEditing(editing === item.id ? undefined : item.id)}>
            {editing === item.id ? '收起' : item.restore_target ? '编辑候选目标' : '登记候选目标'}
          </Button>}
      </Space>
      {item.verified_at && <div>校验时间 {new Date(item.verified_at).toLocaleString()}</div>}
      {item.restore_target && <div>候选目标 {item.restore_target.target_key} · 隔离待验证</div>}
      {item.verification_error && <Alert type="error" message={item.verification_error} />}
      {editing === item.id && item.status === 'verified' && <RestoreTarget item={item} busy={busy}
        onSubmit={(fields) => void change((signal) => saveRestoreTarget(clusterId, item.id,
          { ...fields, expected_revision: revision }, signal), true)} />}
    </div>)}
    {data && data.total > 10 && <Pagination simple current={page} total={data.total} pageSize={10}
      disabled={busy} onChange={(value) => { setEditing(undefined); setPage(value); }} />}
  </section>;
}
