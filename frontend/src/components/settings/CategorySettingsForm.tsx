import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Form } from 'antd';
import { Save } from 'lucide-react';
import { savePlatformSettings, type PlatformSettingsCategory, type PlatformSettingsResponse } from '../../api/system';
import { SettingsFields } from './SettingsFields';
import type { SettingsCategory } from './settingsCategories';

type Values = Record<string, string | number | boolean | null | undefined>;
function apiCategory(category: SettingsCategory): PlatformSettingsCategory {
  return category === 'ai' ? 'ai_policy' : category as PlatformSettingsCategory;
}

export function CategorySettingsForm({ category, tab, source, onSaved }: {
  category: SettingsCategory; tab: string; source: PlatformSettingsResponse;
  onSaved: (source: PlatformSettingsResponse) => void;
}) {
  const key = apiCategory(category);
  const [form] = Form.useForm<Values>();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const stored = source.settings[key];
  useEffect(() => {
    form.setFieldsValue(stored as Values);
  }, [stored, form]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const save = async (fields: Values) => {
    if (inFlight.current || !stored) return;
    const values = { ...stored, ...form.getFieldsValue(true), ...fields };
    inFlight.current = true; setSaving(true); setError(''); setNotice('');
    try {
      const result = await savePlatformSettings(key, values, source.revisions?.[key]);
      if (!mounted.current) return;
      onSaved(result);
      const mode = typeof result.apply_mode === 'string' ? result.apply_mode : result.apply_mode?.[key];
      setNotice(mode ? `设置已保存：${mode}` : '设置已保存');
      if (category === 'basic') window.dispatchEvent(new Event('sentinel:preferences-changed'));
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : '设置保存失败');
    } finally { inFlight.current = false; if (mounted.current) setSaving(false); }
  };
  if (!stored) return <Alert type="error" showIcon message="该分类配置未能读取，请重新加载" />;
  return <>
    {error ? <Alert type="error" showIcon message={error} /> : null}
    {notice ? <Alert type="success" showIcon message={notice} /> : null}
    <Form form={form} layout="vertical" disabled={saving}
      onFinish={(values) => void save(values)} className="platform-settings-form">
      <SettingsFields category={category} tab={tab} />
      <div className="platform-settings-actions">
        <Button type="primary" htmlType="submit" icon={<Save size={16} />} loading={saving}>保存设置</Button>
      </div>
    </Form>
  </>;
}
