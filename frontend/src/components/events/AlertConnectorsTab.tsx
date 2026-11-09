import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Form, Input, Modal, Select, Space, Switch, Table, Tag, Typography, message } from 'antd';
import { FileText, KeyRound, Plus, RefreshCw } from 'lucide-react';
import type { AuthUser } from '../../api/auth';
import {
  createAlertConnector, createAlertConnectorTestRequest, getAlertConnectors,
  preflightAlertConnector, rotateAlertConnector, updateAlertConnector,
  type AlertConnector, type ConnectorPreflightResult, type ConnectorTestRequest,
} from '../../api/externalAlerts';
import { useAlertConnectorRuntime } from './useAlertConnectorRuntime';

interface ConnectorForm {
  name: string;
  source_type: string;
}

const sourceLabels: Record<string, string> = {
  prometheus: 'Prometheus / Alertmanager', zabbix: 'Zabbix', generic_webhook: '通用 Webhook',
};
const defaultPreflightSamples: Record<string, string> = {
  prometheus: JSON.stringify({
    receiver: 'ops',
    status: 'firing',
    alerts: [{
      fingerprint: 'DEMO-preflight',
      labels: { alertname: 'DemoAlert', instance: '192.0.2.10', severity: 'medium' },
      annotations: {
        summary: '示例告警（预检）',
        description: '仅用于校验告警样例格式，不会发送真实告警',
      },
    }],
  }, null, 2),
  zabbix: JSON.stringify({
    eventid: 'DEMO-preflight',
    name: '示例告警（预检）',
    severity: 'Warning',
    host: { host: '192.0.2.10' },
    operational_data: '仅用于校验告警样例格式，不会发送真实告警',
  }, null, 2),
  generic_webhook: JSON.stringify({
    external_id: 'DEMO-preflight',
    title: '示例告警（预检）',
    description: '仅用于校验告警样例格式，不会发送真实告警',
    severity: 'medium',
    asset_host: '192.0.2.10',
  }, null, 2),
};
const defaultPreflightSample = defaultPreflightSamples.generic_webhook;
const getDefaultPreflightSample = (sourceType: string) => {
  const key = sourceType === 'alertmanager' ? 'prometheus' : sourceType;
  return Object.hasOwn(defaultPreflightSamples, key) ? defaultPreflightSamples[key] : defaultPreflightSample;
};
const checkLabels: Record<string, string> = {
  payload: '样例格式',
  connector: '连接器配置',
  asset: '资产绑定',
  authorization: '只读授权配置',
  notifications: '通知配置',
  runtime: '运行链路',
};
const statusLabels: Record<string, string> = {
  passed: '通过', blocked: '未通过', unverified: '未验证',
};
const statusColors: Record<string, string> = {
  passed: 'green', blocked: 'red', unverified: 'gold',
};
const checkMessages: Record<string, Record<string, string>> = {
  payload: { passed: '样例格式有效', blocked: '样例格式未通过', unverified: '样例格式待验证' },
  connector: { passed: '连接器配置可用', blocked: '连接器配置未完成', unverified: '连接器配置待验证' },
  asset: { passed: '已匹配启用资产', blocked: '资产绑定未通过', unverified: '未使用真实资产' },
  authorization: { passed: '只读授权配置可用', blocked: '只读授权配置未完成', unverified: '未验证真实服务器权限' },
  notifications: { passed: '通知配置可用', blocked: '通知配置未完成', unverified: '未验证邮件收件' },
  runtime: { passed: '已验证运行链路', blocked: '运行链路未通过', unverified: '未接收真实告警' },
};

export function AlertConnectorsTab({ currentUser }: { currentUser: Pick<AuthUser, 'role'> }) {
  const [items, setItems] = useState<AlertConnector[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [oneTimeSecret, setOneTimeSecret] = useState<string | null>(null);
  const [rotateTarget, setRotateTarget] = useState<AlertConnector | null>(null);
  const [rotating, setRotating] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const [preflightTarget, setPreflightTarget] = useState<AlertConnector | null>(null);
  const [samplePayload, setSamplePayload] = useState(defaultPreflightSample);
  const [preflightResult, setPreflightResult] = useState<ConnectorPreflightResult | null>(null);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [preflightError, setPreflightError] = useState<string | null>(null);
  const [guideTarget, setGuideTarget] = useState<AlertConnector | null>(null);
  const [testRequest, setTestRequest] = useState<ConnectorTestRequest | null>(null);
  const [testRequestLoading, setTestRequestLoading] = useState(false);
  const [testRequestError, setTestRequestError] = useState<string | null>(null);
  const preflightSequence = useRef(0);
  const rotationInFlight = useRef(false);
  const [form] = Form.useForm<ConnectorForm>();
  const [messages, holder] = message.useMessage();
  const writable = currentUser.role === 'admin';
  const runtime = useAlertConnectorRuntime(writable);
  const load = async (refreshRuntime = true) => {
    setLoading(true);
    if (refreshRuntime) void runtime.refresh();
    try { setItems(await getAlertConnectors()); }
    catch (error) { messages.error(error instanceof Error ? error.message : '连接器加载失败'); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(false); }, []);
  const create = async () => {
    const values = await form.validateFields();
    setLoading(true);
    try {
      const created = await createAlertConnector(values);
      setCreateOpen(false); form.resetFields(); setOneTimeSecret(created.secret);
      await load();
    } catch (error) { messages.error(error instanceof Error ? error.message : '连接器创建失败'); }
    finally { setLoading(false); }
  };
  const toggle = async (item: AlertConnector, enabled: boolean) => {
    setLoading(true);
    try { await updateAlertConnector(item.id, enabled ? 'active' : 'disabled'); messages.success(enabled ? '连接器已启用' : '连接器已停用'); await load(); }
    catch (error) { messages.error(error instanceof Error ? error.message : '连接器更新失败'); }
    finally { setLoading(false); }
  };
  const rotate = async () => {
    if (!rotateTarget || confirmation.trim() !== rotateTarget.name || rotationInFlight.current) return;
    rotationInFlight.current = true;
    setRotating(true);
    try {
      const result = await rotateAlertConnector(rotateTarget.id, confirmation);
      setRotateTarget(null); setOneTimeSecret(result.secret); await load();
    } catch (error) { messages.error(error instanceof Error ? error.message : '密钥轮换失败'); }
    finally { rotationInFlight.current = false; setRotating(false); }
  };
  const openPreflight = (item: AlertConnector) => {
    preflightSequence.current += 1;
    setPreflightTarget(item);
    setSamplePayload(getDefaultPreflightSample(item.source_type));
    setPreflightResult(null);
    setPreflightError(null);
    setPreflightLoading(false);
  };
  const closePreflight = () => {
    preflightSequence.current += 1;
    setPreflightTarget(null);
    setSamplePayload(defaultPreflightSample);
    setPreflightResult(null);
    setPreflightError(null);
    setPreflightLoading(false);
  };
  const openGuide = (item: AlertConnector) => {
    setGuideTarget(item);
    setTestRequest(null);
    setTestRequestError(null);
  };
  const closeGuide = () => {
    if (!testRequestLoading) {
      setGuideTarget(null);
      setTestRequest(null);
      setTestRequestError(null);
    }
  };
  const generateTestRequest = async () => {
    if (!guideTarget || !writable || testRequestLoading) return;
    setTestRequestLoading(true);
    setTestRequestError(null);
    try {
      setTestRequest(await createAlertConnectorTestRequest(guideTarget.id));
    } catch (error) {
      setTestRequestError(error instanceof Error ? error.message : '测试请求生成失败');
    } finally {
      setTestRequestLoading(false);
    }
  };
  const updateSample = (value: string) => {
    preflightSequence.current += 1;
    setSamplePayload(value);
    setPreflightResult(null);
    setPreflightError(null);
  };
  const runPreflight = async () => {
    if (!writable || !preflightTarget || preflightLoading) return;
    try {
      const parsed: unknown = JSON.parse(samplePayload);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('invalid');
    } catch {
      setPreflightError('样例 JSON 格式不正确，请修正后重试');
      setPreflightResult(null);
      return;
    }
    const sequence = ++preflightSequence.current;
    setPreflightLoading(true);
    setPreflightError(null);
    setPreflightResult(null);
    try {
      const result = await preflightAlertConnector(preflightTarget.id, samplePayload);
      if (sequence === preflightSequence.current) setPreflightResult(result);
    } catch {
      if (sequence === preflightSequence.current) setPreflightError('接入预检暂不可用，请先完成连接器配置');
    } finally {
      if (sequence === preflightSequence.current) setPreflightLoading(false);
    }
  };
  useEffect(() => {
    if (!writable) closePreflight();
    return () => { preflightSequence.current += 1; };
  }, [writable]);
  return <div>{holder}
    <Alert
      type="info"
      showIcon
      message="HMAC Webhook 接入"
      description={<span>请求地址 <Typography.Text code>/api/v1/alert-ingest/{'{connector_id}'}</Typography.Text>；签名原文 <Typography.Text code>timestamp.nonce.raw_body</Typography.Text>，使用 HMAC-SHA256 计算后发送 <Typography.Text code>X-Alert-Timestamp</Typography.Text>、<Typography.Text code>X-Alert-Nonce</Typography.Text>、<Typography.Text code>X-Alert-Signature: sha256=...</Typography.Text>。密钥仅在创建或轮换后显示一次。</span>}
      style={{ marginBottom: 14 }}
    />
    <div className="tab-toolbar"><Space>{writable ? <Button type="primary" icon={<Plus size={16} />} onClick={() => setCreateOpen(true)}>新建连接器</Button> : <Tag>只读</Tag>}<Button aria-label="刷新连接器" icon={<RefreshCw size={16} />} onClick={() => void load()} /></Space></div>
    {runtime.notice}
    <div className="table-frame"><Table<AlertConnector> rowKey="id" loading={loading} dataSource={items} pagination={false} scroll={{ x: writable ? 1440 : 880 }} columns={[
      { title: '连接器', dataIndex: 'name', render: (value: string, item) => <div className="asset-identity"><span><KeyRound size={16} /></span><div><strong>{value}</strong><small>{sourceLabels[item.source_type] ?? item.source_type}</small></div></div> },
      { title: '接入密钥', dataIndex: 'secret_masked', render: (value: string) => <Typography.Text code>{value}</Typography.Text> },
      { title: '最近更新', dataIndex: 'updated_at', render: (value: string) => new Date(value).toLocaleString() },
      { title: '状态', dataIndex: 'status', render: (status: AlertConnector['status'], item) => writable ? <Switch aria-label={`${item.name}启用状态`} checked={status === 'active'} onChange={(value) => void toggle(item, value)} /> : <Tag color={status === 'active' ? 'green' : 'default'}>{status === 'active' ? '已启用' : '已停用'}</Tag> },
      ...runtime.columns,
      { title: '操作', align: 'right', render: (_, item) => <Space wrap>
        <Button aria-label={`接入说明 ${item.name}`} icon={<FileText size={15} />} onClick={() => openGuide(item)}>接入说明</Button>
        {writable ? <><Button aria-label={`接入预检 ${item.name}`} onClick={() => openPreflight(item)}>接入预检</Button><Button aria-label={`轮换 ${item.name} 密钥`} disabled={rotating} onClick={() => { setConfirmation(''); setRotateTarget(item); }}>轮换密钥</Button></> : null}
      </Space> },
    ]} /></div>
    {runtime.modal}
    <Modal title="新建告警连接器" open={createOpen} onCancel={() => { setCreateOpen(false); form.resetFields(); }} onOk={() => void create()} okText="创建连接器" confirmLoading={loading} destroyOnHidden><Form form={form} layout="vertical"><Form.Item name="name" label="连接器名称" rules={[{ required: true }, { min: 2 }]}><Input maxLength={120} /></Form.Item><Form.Item name="source_type" label="来源类型" rules={[{ required: true }]}><Select options={Object.entries(sourceLabels).map(([value, label]) => ({ value, label }))} /></Form.Item></Form></Modal>
    <Modal title="确认轮换接入密钥" open={rotateTarget !== null} onCancel={() => { if (!rotationInFlight.current) setRotateTarget(null); }} onOk={() => void rotate()} okText="确认轮换" cancelText="取消" confirmLoading={rotating} okButtonProps={{ disabled: rotating || !rotateTarget || confirmation.trim() !== rotateTarget.name }} cancelButtonProps={{ disabled: rotating }} closable={!rotating} maskClosable={!rotating} keyboard={!rotating} destroyOnHidden><Alert type="warning" showIcon message="轮换后旧密钥立即失效，请同步更新告警发送端。" /><p>请输入确认文本：<strong>{rotateTarget?.name}</strong></p><Input aria-label="轮换确认文本" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} disabled={rotating} /></Modal>
    <Modal title="一次性接入密钥" open={oneTimeSecret !== null} onCancel={() => setOneTimeSecret(null)} onOk={() => setOneTimeSecret(null)} okText="我已安全保存" cancelButtonProps={{ style: { display: 'none' } }} destroyOnHidden><Alert type="warning" showIcon message="关闭后无法再次查看明文；需要新密钥时必须执行轮换。" /><div className="one-time-secret"><Typography.Text code copyable>{oneTimeSecret}</Typography.Text></div></Modal>
    <Modal
      title={`告警接入向导：${guideTarget?.name ?? ''}`}
      open={guideTarget !== null}
      onCancel={closeGuide}
      footer={null}
      width={720}
      destroyOnHidden
    >
      <Alert
        type="info"
        showIcon
        message="接入协议"
        description="告警平台向下方地址发送 POST JSON；平台只保存加密密钥，浏览器不会读取密钥明文。"
      />
      <div style={{ display: 'grid', gap: 12, marginTop: 16 }}>
        <div><Typography.Text strong>Webhook 地址</Typography.Text><div><Typography.Text code copyable>{`/api/v1/alert-ingest/${guideTarget?.id ?? ''}`}</Typography.Text></div></div>
        <div><Typography.Text strong>签名算法</Typography.Text><div><Typography.Text code>HMAC-SHA256</Typography.Text></div></div>
        <div><Typography.Text strong>签名原文</Typography.Text><div><Typography.Text code>timestamp.nonce.raw_body</Typography.Text></div></div>
        <div><Typography.Text strong>请求头</Typography.Text><div><Typography.Text code>X-Alert-Timestamp、X-Alert-Nonce、X-Alert-Signature: sha256=...</Typography.Text></div></div>
        <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
          时间戳与 nonce 必须在重放窗口内唯一；当前窗口由服务端配置，重复 nonce、过期时间戳或签名不匹配都会拒绝。
        </Typography.Paragraph>
        {writable ? <Button onClick={() => void generateTestRequest()} loading={testRequestLoading}>生成测试请求</Button> : <Tag>仅管理员可生成测试请求</Tag>}
        {testRequestError ? <Alert type="error" showIcon message={testRequestError} /> : null}
        {testRequest ? <div style={{ display: 'grid', gap: 8 }}>
          <Alert type="success" showIcon message="测试请求已生成" description={testRequest.warning} />
          <Typography.Text strong>请求头</Typography.Text>
          <Input.TextArea value={JSON.stringify(testRequest.headers, null, 2)} readOnly autoSize={{ minRows: 4, maxRows: 8 }} />
          <Typography.Text strong>请求体</Typography.Text>
          <Input.TextArea value={testRequest.body} readOnly autoSize={{ minRows: 4, maxRows: 8 }} />
        </div> : null}
      </div>
    </Modal>
    <Modal
      title={`接入预检：${preflightTarget?.name ?? ''}`}
      open={writable && preflightTarget !== null}
      onCancel={closePreflight}
      footer={null}
      destroyOnHidden
      width={680}
    >
      <Alert
        type="info"
        showIcon
        message="只读预检"
        description="无真实告警环境时，本预检仅检查连接器配置和样例；不会验证真实签名、服务器权限/连通性或邮件收件，也不会发送真实告警或邮件。"
        style={{ marginBottom: 16 }}
      />
      <Typography.Text strong>告警样例</Typography.Text>
      <Input.TextArea
        aria-label="预检样例 JSON"
        value={samplePayload}
        onChange={(event) => updateSample(event.target.value)}
        autoSize={{ minRows: 8, maxRows: 14 }}
        spellCheck={false}
        style={{ marginTop: 8, fontFamily: 'monospace' }}
        disabled={preflightLoading}
      />
      {preflightError ? <Alert type="error" showIcon message={preflightError} style={{ marginTop: 12 }} /> : null}
      <Space style={{ marginTop: 16 }}>
        <Button type="primary" onClick={() => void runPreflight()} loading={preflightLoading}>运行预检</Button>
        <Button aria-label="关闭" onClick={closePreflight}>关闭</Button>
      </Space>
      {preflightResult ? <div style={{ marginTop: 20 }}>
        <Alert
          type={preflightResult.configuration_ready ? 'info' : 'warning'}
          showIcon
          message="预检完成，未发送告警/邮件"
          description={preflightResult.configuration_ready
            ? '配置预检通过；真实运行链路尚未验证，需接入监控环境后验收。'
            : '配置预检未通过，请补齐标为未通过的配置；真实运行链路尚未验证。'}
        />
        <div style={{ marginTop: 12, display: 'grid', gap: 8 }}>
          {preflightResult.checks.map((check) => <div key={check.key} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
            <div style={{ minWidth: 0 }}>
              <strong>{checkLabels[check.key] ?? '检查项'}</strong>
              <Typography.Paragraph type="secondary" style={{ margin: '4px 0 0' }}>
                {checkMessages[check.key]?.[check.status] ?? '检查结果待验证'}
              </Typography.Paragraph>
            </div>
            <Tag color={statusColors[check.status] ?? 'default'}>{statusLabels[check.status] ?? '未验证'}</Tag>
          </div>)}
        </div>
      </div> : null}
    </Modal>
  </div>;
}
