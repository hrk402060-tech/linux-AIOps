import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Descriptions, Form, Input, Modal, QRCode, Spin, Tag, message } from 'antd';
import { KeyRound, RefreshCw, ShieldCheck } from 'lucide-react';
import {
  confirmOwnMfa, getOwnMfa, resetOwnMfa, setupOwnMfa,
  type MfaSelfStatus, type MfaSetup,
} from '../api/auth';

export function UserSecurityPage({ onLogout }: { onLogout: () => void }) {
  const [status, setStatus] = useState<MfaSelfStatus | null>(null);
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState('');
  const savingRef = useRef(false);
  const [messages, holder] = message.useMessage();

  const load = async () => {
    if (savingRef.current) return;
    setLoading(true);
    setStatus(null);
    setLoadError('');
    try { setStatus(await getOwnMfa()); }
    catch (error) { setLoadError(error instanceof Error ? error.message : '个人安全设置加载失败'); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);

  const beginSetup = async () => {
    if (!status || loading || savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    try { setSetup(await setupOwnMfa()); }
    catch (error) { messages.error(error instanceof Error ? error.message : 'MFA 设置失败'); }
    finally { savingRef.current = false; setSaving(false); }
  };
  const confirm = async ({ code }: { code: string }) => {
    if (!status || !setup || savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    try {
      const result = await confirmOwnMfa(code);
      setRecoveryCodes(result.recovery_codes);
      setSetup(null);
      setStatus((current) => current ? { ...current, enabled: true } : current);
    } catch (error) { messages.error(error instanceof Error ? error.message : 'MFA 验证失败'); }
    finally { savingRef.current = false; setSaving(false); }
  };
  const reset = () => {
    if (!status?.can_disable || loading || savingRef.current) return;
    Modal.confirm({
      title: '关闭个人 MFA？',
      content: '关闭后当前会话会失效，需要重新登录；强制策略下无法关闭。',
      okText: '确认关闭', cancelText: '取消',
      onOk: async () => {
        if (savingRef.current) return;
        savingRef.current = true;
        setSaving(true);
        try { await resetOwnMfa(); messages.success('MFA 已关闭'); onLogout(); }
        catch (error) { messages.error(error instanceof Error ? error.message : 'MFA 关闭失败'); }
        finally { savingRef.current = false; setSaving(false); }
      },
    });
  };

  return <section className="system-page">{holder}
    <div className="page-heading">
      <div><p className="eyebrow">账号安全</p><h1>个人安全</h1></div>
      <Button aria-label="刷新个人安全设置" icon={<RefreshCw size={17} />} loading={loading} disabled={saving || setup !== null || recoveryCodes !== null} onClick={() => void load()} />
    </div>
    {loadError ? <Alert type="error" showIcon message={loadError} /> : null}
    <Spin spinning={loading}>
      <Card title={<><ShieldCheck size={17} /> MFA 多因素认证</>} extra={status ? status.enabled ? <Tag color="green">已启用</Tag> : <Tag>未启用</Tag> : <Tag>{loading ? '加载中' : '状态未知'}</Tag>}>
        <p>使用验证器应用扫码绑定。平台不会显示已保存的 MFA 密钥，恢复码只在绑定完成时展示一次。</p>
        {status?.required ? <Alert type="warning" showIcon message="当前策略要求保持 MFA 绑定" description="如需重置，请联系管理员调整策略或执行管理员重置。" /> : null}
        <div style={{ marginTop: 16 }}>
          {status ? !status.enabled ? <Button type="primary" icon={<KeyRound size={16} />} loading={saving} onClick={() => void beginSetup()}>启用 MFA</Button>
            : status.can_disable ? <Button danger loading={saving} onClick={reset}>关闭 MFA</Button> : <Tag color="orange">由管理员策略强制</Tag> : null}
        </div>
      </Card>
    </Spin>
    <Modal title="扫码绑定 MFA" open={setup !== null} footer={null} onCancel={() => { if (!savingRef.current) setSetup(null); }}
      closable={!saving} maskClosable={!saving} keyboard={!saving} destroyOnHidden>
      {setup ? <>
        <Alert type="info" showIcon message="请使用 Google Authenticator、Microsoft Authenticator 等验证器扫描二维码。" />
        <div style={{ display: 'flex', justifyContent: 'center', padding: '20px 0 12px' }}>
          <QRCode type="svg" value={setup.otpauth_uri} size={220} aria-label="MFA 二维码" />
        </div>
        <details style={{ marginBottom: 16 }}>
          <summary style={{ cursor: 'pointer', color: '#1677ff' }}>无法扫码时手动绑定</summary>
          <Descriptions column={1} size="small" style={{ marginTop: 12 }} items={[
            { key: 'secret', label: '绑定密钥', children: <code>{setup.secret}</code> },
            { key: 'uri', label: '绑定地址', children: <code style={{ wordBreak: 'break-all' }}>{setup.otpauth_uri}</code> },
          ]} />
        </details>
        <Form layout="vertical" onFinish={(values) => void confirm(values)}>
          <Form.Item name="code" label="验证器验证码" rules={[{ required: true, pattern: /^\d{6}$/, message: '请输入 6 位数字验证码' }]}>
            <Input autoComplete="one-time-code" inputMode="numeric" maxLength={6} disabled={saving} />
          </Form.Item>
          <Button block type="primary" htmlType="submit" loading={saving}>确认启用</Button>
        </Form>
      </> : null}
    </Modal>
    <Modal title="MFA 已启用" open={recoveryCodes !== null} footer={<Button type="primary" onClick={() => { setRecoveryCodes(null); onLogout(); }}>保存恢复码并重新登录</Button>} closable={false}>
      <Alert type="warning" showIcon message="恢复码只显示这一次，请安全保存。" />
      <pre className="mfa-recovery-codes">{recoveryCodes?.join('\n')}</pre>
    </Modal>
  </section>;
}
