import { useState } from 'react';
import { Alert, Form, InputNumber, Modal } from 'antd';

import type { FleetAsset, ResourceKey, SeverityRules } from '../../types/monitoring';
import { saveMonitorRules } from '../../api/monitoring';
import { useMonitoringAction } from './useMonitoringAction';

const fields: { key: ResourceKey; label: string; unit: string; max: number }[] = [
  { key: 'cpu', label: 'CPU 使用率', unit: '%', max: 100 },
  { key: 'memory', label: '内存使用率', unit: '%', max: 100 },
  { key: 'disk', label: '分区使用率', unit: '%', max: 100 },
  { key: 'load', label: '每逻辑核负载', unit: '× 核数', max: 1000 },
];
export function RulesModal({ asset, onClose, onSaved }: { asset: FleetAsset; onClose: () => void; onSaved: () => void }) {
  const [rules, setRules] = useState<SeverityRules>(() => structuredClone(asset.policy.severity_rules));
  const action = useMonitoringAction();
  const valid = fields.every(({ key, max }) => rules[key].warning > 0
    && rules[key].warning < rules[key].critical && rules[key].critical <= max)
    && Number.isInteger(rules.duration_minutes) && rules.duration_minutes >= 0 && rules.duration_minutes <= 1440;
  const save = async () => {
    if (!valid) return;
    await action.run(() => saveMonitorRules(asset.asset_id, rules), () => { onSaved(); onClose(); });
  };
  return <Modal open title={`${asset.asset_name} · 资源状态规则`} onCancel={onClose} onOk={() => void save()}
    okText="保存规则" cancelText="取消" confirmLoading={action.busy} okButtonProps={{ disabled: !valid }} destroyOnHidden>
    <p className="fleet-rule-note">用于本页资源状态和阈值线。连续有效采样达到持续时间后确认关注或超限；缺测中断计时。现有告警通知策略独立保留。</p>
    {action.error ? <Alert type="error" showIcon message={action.error} /> : null}
    <Form layout="vertical" className="fleet-rule-form">
      <div className="fleet-rule-row fleet-rule-head"><span>指标</span><span>关注 ≥</span><span>超限 ≥</span></div>
      {fields.map(({ key, label, unit, max }) => <div className="fleet-rule-row" key={key}>
        <span>{label}<small>{unit}</small></span>
        {(['warning', 'critical'] as const).map((level) => <InputNumber key={level} aria-label={`${label}${level === 'warning' ? '关注' : '超限'}阈值`}
          min={0.01} max={max} step={key === 'load' ? 0.1 : 1} value={rules[key][level]}
          onChange={(value) => setRules((previous) => ({ ...previous,
            [key]: { ...previous[key], [level]: value ?? 0 } }))} />)}
      </div>)}
      <Form.Item label="持续时间（分钟）">
        <InputNumber aria-label="持续时间（分钟）" min={0} max={1440} precision={0} value={rules.duration_minutes}
          onChange={(value) => setRules((previous) => ({ ...previous, duration_minutes: value ?? -1 }))} />
      </Form.Item>
      {!valid ? <Alert type="warning" showIcon message="关注阈值必须小于超限阈值，持续时间必须为 0–1440 的整数。" /> : null}
    </Form>
  </Modal>;
}
