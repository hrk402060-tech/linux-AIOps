import { useEffect, useRef, useState } from 'react';
import { Alert, Button, ConfigProvider, Spin, Tabs } from 'antd';
import { ArrowLeft, Bell, Bot, Database, KeyRound, Link2, RefreshCw, Settings2, ShieldCheck, Workflow } from 'lucide-react';
import type { AuthUser } from '../api/auth';
import { getPlatformSettings, type PlatformSettingsResponse } from '../api/system';
import { AuthPolicyPanel } from '../components/AuthPolicyPanel';
import { AISettingsPanel } from '../components/AISettingsPanel';
import { CategorySettingsForm } from '../components/settings/CategorySettingsForm';
import { IdentitySettingsPanel } from '../components/settings/IdentitySettingsPanel';
import { NotificationSettingsPanel } from '../components/settings/NotificationSettingsPanel';
import { StorageSettingsPanel } from '../components/settings/StorageSettingsPanel';
import { settingsCategories, type SettingsCategory } from '../components/settings/settingsCategories';
import './platformSettings.css';

const icons = {
  basic: Settings2, connection: Link2, tasks: Workflow, notifications: Bell,
  authentication: KeyRound, security: ShieldCheck, ai: Bot, storage: Database,
};
type Props = { currentUser: AuthUser; onBack: () => void; initialCategory?: SettingsCategory };

export function PlatformSettingsPage(props: Props) {
  if (props.currentUser.role !== 'admin') return <section className="platform-settings-page">
    <Alert type="error" showIcon message="仅管理员可以访问系统设置" />
    <Button onClick={props.onBack}>返回工作台</Button>
  </section>;
  return <AdminSettingsPage key={props.currentUser.id} {...props} />;
}

function AdminSettingsPage({ onBack, initialCategory = 'basic' }: Props) {
  const [source, setSource] = useState<PlatformSettingsResponse | null>(null);
  const [category, setCategory] = useState<SettingsCategory>(initialCategory);
  const selected = settingsCategories.find((entry) => entry.key === category) ?? settingsCategories[0];
  const [tab, setTab] = useState(selected.tabs[0][0]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const load = async () => {
    const request = ++generation.current;
    setLoading(true); setError('');
    try {
      const result = await getPlatformSettings();
      if (request === generation.current) setSource(result);
    } catch (cause) {
      if (request === generation.current) setError(cause instanceof Error ? cause.message : '系统设置加载失败');
    } finally { if (request === generation.current) setLoading(false); }
  };
  useEffect(() => { void load(); return () => { generation.current += 1; }; }, []);
  const changeCategory = (next: SettingsCategory) => {
    setCategory(next);
    setTab(settingsCategories.find((entry) => entry.key === next)?.tabs[0][0] ?? '');
  };
  const content = () => {
    if (category === 'security') return <AuthPolicyPanel />;
    if (category === 'ai' && tab === 'model') return <AISettingsPanel />;
    if (category === 'authentication' && tab !== 'local') return <IdentitySettingsPanel provider={tab === 'ldap' ? 'ldap' : 'oidc'} />;
    if (category === 'notifications' && tab !== 'delivery') return <NotificationSettingsPanel channel={tab === 'email' ? 'email' : 'webhook'} />;
    if (!source) return null;
    return <>
      <CategorySettingsForm key={category} category={category} tab={tab} source={source} onSaved={setSource} />
      {category === 'storage' ? <StorageSettingsPanel tab={tab === 'cleanup' ? 'cleanup' : 'backup'} /> : null}
    </>;
  };
  return <ConfigProvider theme={{ token: { colorPrimary: '#168478', colorInfo: '#168478', colorLink: '#168478', borderRadius: 5 } }}>
    <section className="platform-settings-page">
      <div className="page-heading">
        <h1>系统设置</h1>
        <div className="settings-header-actions">
          <Button icon={<ArrowLeft size={16} />} onClick={onBack}>返回工作台</Button>
          <Button aria-label="刷新系统设置" icon={<RefreshCw size={16} />} loading={loading} onClick={() => void load()} />
        </div>
      </div>
      {error ? <Alert type="error" showIcon message={error} /> : null}
      <div className="platform-settings-layout">
        <nav className="platform-settings-categories" aria-label="设置分类">
          {settingsCategories.map((entry) => {
            const Icon = icons[entry.key];
            return <button key={entry.key} type="button" aria-current={entry.key === category ? 'page' : undefined}
              className={entry.key === category ? 'is-active' : ''} onClick={() => changeCategory(entry.key)}>
              <Icon size={17} /><span>{entry.label}</span>
            </button>;
          })}
        </nav>
        <section className="platform-settings-main" aria-label={selected.label}>
          <h2>{selected.label}</h2>
          <Tabs activeKey={tab} onChange={setTab} items={selected.tabs.map(([key, label]) => ({ key, label }))} />
          <Spin spinning={loading}>
            <div className="settings-tab-body">{content()}</div>
          </Spin>
        </section>
      </div>
    </section>
  </ConfigProvider>;
}
