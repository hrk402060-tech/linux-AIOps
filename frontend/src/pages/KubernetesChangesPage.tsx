import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Form, Input, InputNumber, Pagination, Select, Space, Tag, Tooltip } from 'antd';
import { ListChecks, RefreshCw } from 'lucide-react';
import { getKubernetesClusters, kubernetesRequestError, type KubernetesCluster } from '../api/kubernetes';
import { checkKubernetesChange, getHelmCharts, previewHelmChange, type ChangeParameters, type ChangePreflight, type HelmChart } from '../api/kubernetesChanges';
import { HelmResources, KubernetesHelmApprovals } from './KubernetesHelmApprovals';
import './kubernetesChanges.css';

type Action = ChangeParameters['action'];
type ChangeFields = { additional_nodes?: number; target_version?: string; namespace?: string;
  backup_name?: string; release_name?: string; chart_id?: string; chart_digest?: string };

const PAGE_SIZE = 50;
const actions: Array<{ value: Action; label: string }> = [
  { value: 'scale', label: '节点扩容' }, { value: 'upgrade', label: '版本升级' },
  { value: 'backup', label: '集群备份' }, { value: 'restore', label: '集群恢复' },
  { value: 'helm', label: 'Helm 部署' },
];
const labelPattern = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/;
const statuses = {
  passed: { label: '通过', color: 'success' }, blocked: { label: '阻断', color: 'error' },
  unverified: { label: '待验证', color: 'warning' },
};

function parameters(action: Action, fields: ChangeFields): ChangeParameters {
  const namespace = fields.namespace?.trim() ?? '';
  switch (action) {
    case 'scale': return { action, additional_nodes: fields.additional_nodes ?? 0 };
    case 'upgrade': return { action, target_version: fields.target_version?.trim() ?? '' };
    case 'backup': return { action, namespace };
    case 'restore': return { action, namespace, backup_name: fields.backup_name?.trim() ?? '' };
    case 'helm': return { action, namespace, release_name: fields.release_name?.trim() ?? '',
      chart_id: fields.chart_id?.trim() ?? '', chart_digest: fields.chart_digest?.trim() ?? '' };
  }
}

function TextField({ name, label, maxLength, pattern }: {
  name: keyof ChangeFields; label: string; maxLength: number; pattern: RegExp;
}) {
  return <Form.Item name={name} label={label} rules={[
    { required: true, message: `请输入${label}` }, { pattern, message: `${label}格式无效` },
  ]}><Input maxLength={maxLength} autoComplete="off" /></Form.Item>;
}

function ChangeInputs({ action }: { action: Action }) {
  if (action === 'scale') return <Form.Item name="additional_nodes" label="新增节点数" rules={[
    { required: true, type: 'integer', min: 1, max: 100, message: '请输入 1 到 100 之间的整数' },
  ]}><InputNumber min={1} max={100} precision={0} /></Form.Item>;
  if (action === 'upgrade') return <TextField name="target_version" label="目标版本" maxLength={13}
    pattern={/^v?[1-9]\d?\.(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,3})$/} />;
  return <>
    <TextField name="namespace" label={action === 'restore' ? '恢复目标命名空间' : '命名空间'}
      maxLength={63} pattern={labelPattern} />
    {action === 'restore' && <TextField name="backup_name" label="备份名称" maxLength={63} pattern={labelPattern} />}
    {action === 'helm' && <>
      <TextField name="release_name" label="发布名称" maxLength={53} pattern={labelPattern} />
    </>}
  </>;
}

function PreflightResult({ result }: { result: ChangePreflight }) {
  const blocked = result.status === 'blocked';
  return <section className="k8s-preflight-result" aria-label="预检结果" aria-live="polite">
    <Alert type={blocked ? 'error' : 'warning'} showIcon
      message={blocked ? '预检受阻 · 未执行' : '基础预检通过 · 待验证 · 未执行'} />
    <dl>
      <div><dt>登记版本</dt><dd>{result.current_version ?? '未知'}</dd></div>
      <div><dt>就绪节点</dt><dd>{result.node_count === null || result.ready_nodes === null
        ? '未知' : `${result.ready_nodes} / ${result.node_count}`}</dd></div>
      <div><dt>快照时间</dt><dd>{result.observed_at ? new Date(result.observed_at).toLocaleString() : '无有效快照'}</dd></div>
      <div><dt>审计编号</dt><dd>{result.trace_id}</dd></div>
    </dl>
    <ul>{result.checks.map((check) => <li key={check.code}>
      <Tag color={statuses[check.status].color}>{statuses[check.status].label}</Tag><span>{check.label}</span>
    </li>)}</ul>
  </section>;
}

export function KubernetesChangesPage({ initialCluster }: { initialCluster?: KubernetesCluster }) {
  const [form] = Form.useForm<ChangeFields>();
  const [page, setPage] = useState(1);
  const [reload, setReload] = useState(0);
  const [clusters, setClusters] = useState<KubernetesCluster[]>([]);
  const [total, setTotal] = useState(0);
  const [clusterId, setClusterId] = useState(initialCluster?.id);
  const [action, setAction] = useState<Action>('scale');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [listError, setListError] = useState('');
  const [error, setError] = useState('');
  const [result, setResult] = useState<ChangePreflight>();
  const [helmPreview, setHelmPreview] = useState<Awaited<ReturnType<typeof previewHelmChange>>>();
  const [helmCharts, setHelmCharts] = useState<HelmChart[]>([]);
  const [helmEnabled, setHelmEnabled] = useState(false);
  const requestRef = useRef<AbortController>();
  const cluster = clusters.find((item) => item.id === clusterId);

  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setListError('');
    getKubernetesClusters({ status: 'active', offset: (page - 1) * PAGE_SIZE, limit: PAGE_SIZE })
      .then((response) => {
        if (ignore) return;
        setClusters(response.items);
        setTotal(response.total);
        setClusterId((current) => response.items.some((item) => item.id === current) ? current : undefined);
      })
      .catch((reason: unknown) => { if (!ignore) setListError(kubernetesRequestError(reason)); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; };
  }, [page, reload]);
  useEffect(() => () => requestRef.current?.abort(), []);

  function clearResult() { setResult(undefined); setHelmPreview(undefined); setError(''); }

  useEffect(() => {
    if (action !== 'helm') return;
    const controller = new AbortController();
    getHelmCharts(controller.signal).then((catalog) => {
      if (!controller.signal.aborted) { setHelmCharts(catalog.charts); setHelmEnabled(catalog.enabled); }
    }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(kubernetesRequestError(reason)); });
    return () => controller.abort();
  }, [action]);

  async function run(fields: ChangeFields) {
    if (!cluster || requestRef.current || loading || listError) return;
    const controller = new AbortController();
    requestRef.current = controller;
    clearResult();
    setBusy(true);
    try {
      if (action === 'helm') {
        const value = await previewHelmChange(cluster.id, cluster.revision,
          { chart_id: fields.chart_id?.trim() ?? '', chart_digest: helmCharts.find((chart) => chart.id === fields.chart_id)?.digest ?? '',
            namespace: fields.namespace?.trim() ?? '', release_name: fields.release_name?.trim() ?? '' }, controller.signal);
        if (!controller.signal.aborted) { setHelmPreview(value); setResult(value.preflight); }
      } else {
        const value = await checkKubernetesChange(cluster.id, cluster.revision, parameters(action, fields), controller.signal);
        if (!controller.signal.aborted) setResult(value);
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(kubernetesRequestError(reason));
    } finally {
      requestRef.current = undefined;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <section className="k8s-preflight">
    <div className="page-heading"><h2>集群变更预检</h2><Tooltip title="刷新集群">
      <Button aria-label="刷新集群" disabled={busy} loading={loading} icon={<RefreshCw size={16} />}
        onClick={() => { clearResult(); setReload((value) => value + 1); }} />
    </Tooltip></div>
    {listError && <Alert type="error" showIcon message={listError} />}
    <Space wrap className="k8s-preflight-controls">
      <Select aria-label="目标集群" placeholder="选择集群" value={clusterId} loading={loading}
        disabled={busy || loading || !!listError} showSearch optionFilterProp="label"
        notFoundContent="无启用的集群" options={clusters.map((item) => ({ label: item.name, value: item.id }))}
        onChange={(id) => { clearResult(); setClusterId(id); }} />
      <Select aria-label="变更类型" value={action} options={actions} disabled={busy}
        onChange={(value) => { clearResult(); form.resetFields(); setAction(value); }} />
      {total > PAGE_SIZE && <Pagination simple current={page} pageSize={PAGE_SIZE} total={total} disabled={busy || loading}
        onChange={(value) => { clearResult(); setPage(value); }} />}
    </Space>
    <Form form={form} layout="vertical" preserve={false} disabled={busy} onValuesChange={clearResult} onFinish={run}>
      <div className="k8s-preflight-fields" key={action}><ChangeInputs action={action} />{action === 'helm' && <Form.Item noStyle shouldUpdate>
        {() => <>
        <Form.Item name="chart_id" label="受信 Chart" rules={[{ required: true, message: '请选择受信 Chart' }]}>
          <Select aria-label="受信 Chart" disabled={!helmEnabled} placeholder={helmEnabled ? '选择已登记版本' : '未启用受信 Chart'}
            options={helmCharts.map((chart) => ({ label: `${chart.name} · ${chart.version}`, value: chart.id,
              digest: chart.digest }))}
            />
        </Form.Item></>}
      </Form.Item>}</div>
      <Button type="primary" htmlType="submit" icon={<ListChecks size={16} />}
        disabled={!cluster || loading || !!listError} loading={busy}>运行预检</Button>
    </Form>
    {error && <Alert type="error" showIcon message={error} />}
    {result && <PreflightResult result={result} />}
    {helmPreview && <section className="k8s-helm-resources" aria-label="Helm 资源预览">
      <h3>资源预览 · {helmPreview.chart.name} {helmPreview.chart.version}</h3>
      <HelmResources items={helmPreview.resources} />
    </section>}
    {action === 'helm' && cluster && !listError &&
      <KubernetesHelmApprovals key={`${cluster.id}:${cluster.revision}:${reload}`} clusterId={cluster.id} preview={helmPreview} />}
  </section>;
}
