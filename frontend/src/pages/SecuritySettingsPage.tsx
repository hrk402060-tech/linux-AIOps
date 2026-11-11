import { AuthPolicyPanel } from '../components/AuthPolicyPanel';
import { AISettingsPanel } from '../components/AISettingsPanel';

export function SecuritySettingsPage() {
  return <section className="system-page">
    <div className="page-heading">
      <div><p className="eyebrow">平台安全</p><h1>安全设置</h1><p>管理员配置 MFA 强制范围、登录安全和运维 AI 接入。</p></div>
    </div>
    <AuthPolicyPanel />
    <AISettingsPanel />
  </section>;
}
