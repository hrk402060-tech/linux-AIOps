import { Alert, Button, Descriptions, Drawer, Form, Input, Modal, Select, Space, Table, Tag, message } from 'antd';
import { GitBranch, Link2, Pencil, RotateCcw, Unlink } from 'lucide-react';
import { useEffect, useState } from 'react';

import { createServiceDependency, getServiceDependencies, updateServiceDependency } from '../api/cmdb';
import type { Asset } from '../types/asset';
import type { CmdbEntityRow, CmdbServiceAsset, CmdbServiceDependency, CmdbServiceTree } from '../types/cmdb';

const typeLabels = { business: '业务系统', application: '应用', service: '服务', api: 'API' };
const lifecycleLabels = { active: '运行中', disabled: '已停用', archived: '已归档' };

interface Props {
  row: CmdbEntityRow | null;
  assets: Asset[];
  services: CmdbServiceTree[];
  canWrite: boolean;
  onClose: () => void;
  onEdit: (row: CmdbEntityRow) => void;
  onBind: (serviceId: string, assetId: string, role: string) => Promise<void>;
  onUnbind: (relation: CmdbServiceAsset) => Promise<void>;
  onRestore: (relation: CmdbServiceAsset) => Promise<void>;
}

export function CmdbDetailDrawer({ row, assets, services, canWrite, onClose, onEdit, onBind, onUnbind, onRestore }: Props) {
  const [bindOpen, setBindOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<{ asset_id: string; relation_role: string }>();
  const [dependencyForm] = Form.useForm<{ target_service_id: string; dependency_type: string; description?: string }>();
  const [dependencies, setDependencies] = useState<CmdbServiceDependency[] | null>(null);
  const [dependencyOpen, setDependencyOpen] = useState(false);
  const [dependencyLoading, setDependencyLoading] = useState(false);
  const [dependencySubmitting, setDependencySubmitting] = useState(false);
  const [messages, holder] = message.useMessage();
  const service = row?.type === 'service' ? row.service : undefined;
  const activeRelations = service?.assets.filter((item) => item.status === 'active') ?? [];
  const availableAssets = assets.filter(
    (asset) => !service?.assets.some((relation) => relation.asset_id === asset.id),
  );
  useEffect(() => { setDependencies(null); }, [row?.entity.id]);
  const submitBind = async () => {
    if (!service) return;
    const values = await form.validateFields();
    setSubmitting(true);
    try { await onBind(service.id, values.asset_id, values.relation_role); setBindOpen(false); form.resetFields(); }
    finally { setSubmitting(false); }
  };
  const loadDependencies = async () => {
    if (!service) return;
    setDependencyLoading(true);
    try { setDependencies(await getServiceDependencies(service.id)); }
    catch (error) { messages.error(error instanceof Error ? error.message : '服务依赖加载失败'); }
    finally { setDependencyLoading(false); }
  };
  const submitDependency = async () => {
    if (!service) return;
    const values = await dependencyForm.validateFields();
    setDependencySubmitting(true);
    try {
      await createServiceDependency(service.id, values);
      messages.success('服务依赖已添加');
      setDependencyOpen(false);
      dependencyForm.resetFields();
      await loadDependencies();
    } catch (error) { messages.error(error instanceof Error ? error.message : '服务依赖创建失败'); }
    finally { setDependencySubmitting(false); }
  };
  const disableDependency = async (item: CmdbServiceDependency) => {
    try {
      await updateServiceDependency(item.id, { status: 'disabled' });
      messages.success('服务依赖已停用');
      await loadDependencies();
    } catch (error) { messages.error(error instanceof Error ? error.message : '服务依赖更新失败'); }
  };
  const descriptionItems = row ? [
    { key: 'type', label: '类型', children: typeLabels[row.type] },
    { key: 'code', label: '编码', children: row.entity.code },
    { key: 'status', label: '生命周期', children: <Tag>{lifecycleLabels[row.entity.lifecycle_status]}</Tag> },
    { key: 'business', label: '业务系统', children: row.business.name },
    ...(row.application ? [{ key: 'application', label: '应用', children: row.application.name }] : []),
    ...(row.service ? [{ key: 'service', label: '服务', children: row.service.name }] : []),
    ...(row.api ? [{ key: 'endpoint', label: '接口', children: <code>{row.api.http_method} {row.api.path}</code> }] : []),
    { key: 'description', label: '说明', children: row.entity.description || '未填写' },
  ] : [];
  return <>{holder}
    <Drawer title={row ? `${typeLabels[row.type]}详情` : ''} width={720} open={row !== null} onClose={onClose}
      extra={row && canWrite ? <Button icon={<Pencil size={16} />} onClick={() => onEdit(row)}>编辑</Button> : null}>
      {row ? <div className="cmdb-detail"><div className="cmdb-detail-heading"><div><strong>{row.entity.name}</strong><span>{row.entity.code}</span></div><Tag>{typeLabels[row.type]}</Tag></div><Descriptions size="small" column={1} items={descriptionItems} />
        {service ? <section><div className="panel-heading"><div><strong>承载服务器</strong><span>仅当前有效关系进入服务统计</span></div>{canWrite ? <Button type="primary" icon={<Link2 size={16} />} disabled={availableAssets.length === 0} onClick={() => setBindOpen(true)}>绑定服务器</Button> : null}</div>
          <Table<CmdbServiceAsset> rowKey="id" pagination={false} dataSource={service.assets} locale={{ emptyText: '尚未绑定服务器' }} columns={[
            { title: '服务器', render: (_, item) => <div className="stacked-cell"><strong>{item.asset_name}</strong><small>{item.asset_host}</small></div> },
            { title: '角色', dataIndex: 'relation_role', width: 130, render: (value) => <Tag>{value}</Tag> },
            { title: '关系状态', dataIndex: 'status', width: 100, render: (value) => <Tag color={value === 'active' ? 'green' : 'default'}>{value === 'active' ? '当前有效' : '已解除'}</Tag> },
            ...(canWrite ? [{ title: '操作', width: 70, align: 'right' as const, render: (_: unknown, item: CmdbServiceAsset) => item.status === 'active'
              ? <Button type="text" danger aria-label={`解绑 ${item.asset_name}`} icon={<Unlink size={16} />} onClick={() => void onUnbind(item)} />
              : <Button type="text" aria-label={`恢复关系 ${item.asset_name}`} icon={<RotateCcw size={16} />} onClick={() => void onRestore(item)} /> }] : []),
          ]} />
          <div className="panel-heading cmdb-dependency-heading"><div><strong>服务依赖</strong><span>仅展示已登记的上下游关系</span></div><Space>{canWrite ? <Button icon={<GitBranch size={16} />} onClick={() => setDependencyOpen(true)}>添加依赖</Button> : null}<Button loading={dependencyLoading} onClick={() => void loadDependencies()}>加载依赖</Button></Space></div>
          {dependencies ? <Table<CmdbServiceDependency> rowKey="id" pagination={false} dataSource={dependencies} locale={{ emptyText: '尚未登记服务依赖' }} columns={[
            { title: '方向', render: (_, item) => item.source_service_id === service.id ? `当前服务 → ${item.target_service_name}` : `${item.source_service_name} → 当前服务` },
            { title: '类型', dataIndex: 'dependency_type', width: 110 },
            { title: '状态', dataIndex: 'status', width: 90, render: (value) => <Tag color={value === 'active' ? 'green' : 'default'}>{value === 'active' ? '有效' : '已停用'}</Tag> },
            ...(canWrite ? [{ title: '操作', width: 70, align: 'right' as const, render: (_: unknown, item: CmdbServiceDependency) => item.status === 'active' ? <Button type="text" danger onClick={() => void disableDependency(item)}>停用</Button> : null }] : []),
          ]} /> : <Alert type="info" showIcon message="依赖关系尚未加载" description="点击“加载依赖”读取当前服务的上下游关系。" />}
        </section> : null}
      </div> : null}
    </Drawer>
    <Modal title="绑定承载服务器" open={bindOpen} confirmLoading={submitting} okText="确认绑定" onOk={() => void submitBind()} onCancel={() => setBindOpen(false)} destroyOnHidden>
      <Form form={form} layout="vertical" initialValues={{ relation_role: 'primary' }}><Form.Item name="asset_id" label="服务器" rules={[{ required: true }]}><Select showSearch optionFilterProp="label" options={availableAssets.map((asset) => ({ value: asset.id, label: `${asset.name} · ${asset.host}` }))} /></Form.Item><Form.Item name="relation_role" label="关系角色" rules={[{ required: true }]}><Select options={['primary', 'secondary', 'worker', 'database', 'other'].map((value) => ({ value, label: value }))} /></Form.Item></Form>
    </Modal>
    <Modal title="添加服务依赖" open={dependencyOpen} confirmLoading={dependencySubmitting} okText="确认添加" onOk={() => void submitDependency()} onCancel={() => setDependencyOpen(false)} destroyOnHidden>
      <Form form={dependencyForm} layout="vertical">
        <Form.Item name="target_service_id" label="依赖服务" rules={[{ required: true, message: '请选择依赖服务' }]}>
          <Select showSearch optionFilterProp="label" options={services.filter((item) => item.id !== service?.id && item.lifecycle_status === 'active').map((item) => ({ value: item.id, label: `${item.name} · ${item.code}` }))} />
        </Form.Item>
        <Form.Item name="dependency_type" label="依赖类型" initialValue="calls" rules={[{ required: true, message: '请输入依赖类型' }]}><Input placeholder="例如 calls、reads、publishes" /></Form.Item>
        <Form.Item name="description" label="说明"><Input.TextArea rows={3} maxLength={2000} /></Form.Item>
      </Form>
    </Modal>
  </>;
}
