import { Alert, Button, Descriptions, Form, Input, Modal, Select, Space, Table, Tag, Tooltip, message } from 'antd';
import { ChevronRight, GitBranch, Link2, Pencil, RotateCcw, Server, Unlink } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { createServiceDependency, getServiceDependencies, updateServiceDependency } from '../api/cmdb';
import type { Asset } from '../types/asset';
import type { CmdbEntityRow, CmdbServiceAsset, CmdbServiceDependency, CmdbServiceTree } from '../types/cmdb';

const typeLabels = { business: '业务系统', application: '应用', service: '服务', api: 'API' };
const lifecycleLabels = { active: '运行中', disabled: '已停用', archived: '已归档' };

interface Props {
  row: CmdbEntityRow;
  assets: Asset[];
  services: CmdbServiceTree[];
  canWrite: boolean;
  onClose: () => void;
  onEdit: (row: CmdbEntityRow) => void;
  onBind: (serviceId: string, assetId: string, role: string) => Promise<void>;
  onUnbind: (relation: CmdbServiceAsset) => Promise<void>;
  onRestore: (relation: CmdbServiceAsset) => Promise<void>;
}

function relatedAssets(row: CmdbEntityRow): CmdbServiceAsset[] {
  if (row.type === 'service') return row.service?.assets ?? [];
  const services = row.type === 'business' ? row.business.applications.flatMap((item) => item.services)
    : row.type === 'application' ? row.application?.services ?? [] : row.service ? [row.service] : [];
  return Array.from(new Map(services.flatMap((service) => service.assets)
    .filter((relation) => relation.status === 'active').map((relation) => [relation.asset_id, relation])).values());
}

export function CmdbDetailPanel({ row, assets, services, canWrite, onClose, onEdit, onBind, onUnbind, onRestore }: Props) {
  const [bindOpen, setBindOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<{ asset_id: string; relation_role: string }>();
  const [dependencyForm] = Form.useForm<{ target_service_id: string; dependency_type: string; description?: string }>();
  const [dependencies, setDependencies] = useState<CmdbServiceDependency[] | null>(null);
  const [dependencyOpen, setDependencyOpen] = useState(false);
  const [dependencyLoading, setDependencyLoading] = useState(false);
  const [dependencySubmitting, setDependencySubmitting] = useState(false);
  const dependencyRequest = useRef(0);
  const [messages, holder] = message.useMessage();
  const service = row.type === 'service' ? row.service : undefined;
  const serverRelations = relatedAssets(row);
  const path = [
    { type: '业务系统', entity: row.business },
    ...(row.application ? [{ type: '应用', entity: row.application }] : []),
    ...(row.service ? [{ type: '服务', entity: row.service }] : []),
    ...(row.api ? [{ type: 'API', entity: row.api }] : []),
  ];
  const availableAssets = assets.filter(
    (asset) => !service?.assets.some((relation) => relation.asset_id === asset.id),
  );
  useEffect(() => {
    setDependencies(null); setDependencyLoading(false); setBindOpen(false); setDependencyOpen(false);
    dependencyRequest.current += 1;
    return () => { dependencyRequest.current += 1; };
  }, [row.key]);
  const submitBind = async () => {
    if (!service) return;
    const values = await form.validateFields();
    setSubmitting(true);
    try { await onBind(service.id, values.asset_id, values.relation_role); setBindOpen(false); form.resetFields(); }
    catch (error) { messages.error(error instanceof Error ? error.message : '服务器绑定失败'); }
    finally { setSubmitting(false); }
  };
  const loadDependencies = async () => {
    if (!service) return;
    const request = ++dependencyRequest.current;
    setDependencyLoading(true);
    try {
      const result = await getServiceDependencies(service.id);
      if (request === dependencyRequest.current) setDependencies(result);
    } catch (error) { if (request === dependencyRequest.current) messages.error(error instanceof Error ? error.message : '服务依赖加载失败'); }
    finally { if (request === dependencyRequest.current) setDependencyLoading(false); }
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
  const descriptionItems = [
    { key: 'type', label: '类型', children: typeLabels[row.type] },
    { key: 'code', label: '编码', children: row.entity.code },
    { key: 'status', label: '生命周期', children: <Tag>{lifecycleLabels[row.entity.lifecycle_status]}</Tag> },
    ...(row.type === 'service' ? [{ key: 'service-type', label: '服务类型', children: row.service?.service_type }] : []),
    ...(row.api ? [{ key: 'endpoint', label: '接口', children: <code>{row.api.http_method} {row.api.path}</code> }] : []),
    { key: 'description', label: '说明', children: row.entity.description || '未填写' },
  ];
  return <>{holder}
    <aside className="cmdb-inspector" aria-label={`${typeLabels[row.type]}详情`}>
      <header className="cmdb-inspector-heading"><div><span>{typeLabels[row.type]}详情</span><h2>{row.entity.name}</h2></div>
        <Tooltip title="收起详情"><Button type="text" aria-label="收起详情" icon={<ChevronRight size={18} />} onClick={onClose} /></Tooltip>
      </header>
      <div className="cmdb-inspector-body">
        <section aria-label="基本信息"><div className="cmdb-inspector-section-heading"><h3>基本信息</h3>
          {canWrite ? <Button size="small" type="text" icon={<Pencil size={14} />} onClick={() => onEdit(row)}>编辑</Button> : null}</div>
          <Descriptions size="small" column={1} items={descriptionItems} />
        </section>
        <section aria-label="归属关系"><h3>归属关系</h3><ol className="cmdb-inspector-path">{path.map((item, index) => <li key={`${item.type}-${item.entity.id}`} aria-current={index === path.length - 1 ? 'true' : undefined}><span>{item.type}</span><strong>{item.entity.name}</strong><small>{item.entity.code}</small></li>)}</ol></section>
        <section aria-label="关联服务器"><div className="cmdb-inspector-section-heading"><h3><Server size={15} aria-hidden="true" />关联服务器</h3>
          {service && canWrite ? <Button size="small" icon={<Link2 size={14} />} disabled={availableAssets.length === 0} onClick={() => setBindOpen(true)}>绑定服务器</Button> : null}</div>
          <Table<CmdbServiceAsset> size="small" rowKey="id" pagination={false} dataSource={serverRelations} scroll={{ x: service ? 420 : 250 }} locale={{ emptyText: '尚未绑定服务器' }} columns={[
            { title: '服务器', render: (_, item) => <div className="stacked-cell"><strong>{item.asset_name}</strong><small>{item.asset_host}</small></div> },
            ...(service ? [{ title: '角色', dataIndex: 'relation_role', width: 100, render: (value: string) => <Tag>{value}</Tag> }] : []),
            { title: '关系状态', dataIndex: 'status', width: 100, render: (value) => <Tag color={value === 'active' ? 'green' : 'default'}>{value === 'active' ? '当前有效' : '已解除'}</Tag> },
            ...(service && canWrite ? [{ title: '操作', width: 60, align: 'right' as const, render: (_: unknown, item: CmdbServiceAsset) => item.status === 'active'
              ? <Button type="text" danger aria-label={`解绑 ${item.asset_name}`} icon={<Unlink size={16} />} onClick={() => void onUnbind(item).catch((error: unknown) => messages.error(error instanceof Error ? error.message : '服务器解绑失败'))} />
              : <Button type="text" aria-label={`恢复关系 ${item.asset_name}`} icon={<RotateCcw size={16} />} onClick={() => void onRestore(item).catch((error: unknown) => messages.error(error instanceof Error ? error.message : '服务器关系恢复失败'))} /> }] : []),
          ]} />
        </section>
        {service ? <section aria-label="服务依赖">
          <div className="cmdb-inspector-section-heading"><h3>服务依赖</h3><Space wrap size={4}>{canWrite ? <Button size="small" icon={<GitBranch size={14} />} onClick={() => setDependencyOpen(true)}>添加依赖</Button> : null}<Button size="small" loading={dependencyLoading} onClick={() => void loadDependencies()}>加载依赖</Button></Space></div>
          {dependencies ? <Table<CmdbServiceDependency> size="small" scroll={{ x: 380 }} rowKey="id" pagination={false} dataSource={dependencies} locale={{ emptyText: '尚未登记服务依赖' }} columns={[
            { title: '方向', render: (_, item) => item.source_service_id === service.id ? `当前服务 → ${item.target_service_name}` : `${item.source_service_name} → 当前服务` },
            { title: '类型', dataIndex: 'dependency_type', width: 110 },
            { title: '状态', dataIndex: 'status', width: 90, render: (value) => <Tag color={value === 'active' ? 'green' : 'default'}>{value === 'active' ? '有效' : '已停用'}</Tag> },
            ...(canWrite ? [{ title: '操作', width: 70, align: 'right' as const, render: (_: unknown, item: CmdbServiceDependency) => item.status === 'active' ? <Button type="text" danger onClick={() => void disableDependency(item)}>停用</Button> : null }] : []),
          ]} /> : <Alert type="info" showIcon message="依赖关系尚未加载" description="点击“加载依赖”读取当前服务的上下游关系。" />}
        </section> : null}
      </div>
    </aside>
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
