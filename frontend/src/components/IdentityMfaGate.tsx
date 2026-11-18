import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Form, Input, Modal, QRCode, Spin } from 'antd';
import type { AuthUser, MfaSetup } from '../api/auth';
import { confirmIdentityMfa, getIdentityMfaStatus, setupIdentityMfa, verifyIdentityMfa } from '../api/identitySources';

export function IdentityMfaGate({ onAuthenticated, onCancel }: {
  onAuthenticated: (user: AuthUser) => void; onCancel: () => void;
}) {
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [recovery, setRecovery] = useState<{ user: AuthUser; recovery_codes: string[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const mutation = useRef(false);
  useEffect(() => {
    const request = ++generation.current;
    const load = async () => {
      try {
        const status = await getIdentityMfaStatus();
        if (!status.required) throw new Error('外部登录验证已过期，请重新登录');
        const nextSetup = status.enrollment_required ? await setupIdentityMfa() : null;
        if (request === generation.current) { setSetup(nextSetup); setReady(true); }
      } catch (cause) {
        if (request === generation.current) setError(cause instanceof Error ? cause.message : '身份验证准备失败');
      } finally { if (request === generation.current) setLoading(false); }
    };
    void load();
    return () => { generation.current += 1; };
  }, []);
  const submit = async ({ code }: { code: string }) => {
    if (mutation.current || !ready) return;
    mutation.current = true; setWorking(true); setError('');
    const request = generation.current;
    try {
      if (setup) {
        const result = await confirmIdentityMfa(code);
        if (request === generation.current) setRecovery(result);
      } else {
        const user = await verifyIdentityMfa(code);
        if (request === generation.current) onAuthenticated(user);
      }
    } catch (cause) {
      if (request === generation.current) setError(cause instanceof Error ? cause.message : 'MFA 验证失败');
    } finally { mutation.current = false; if (request === generation.current) setWorking(false); }
  };
  return <Modal title={recovery ? '保存 MFA 恢复码' : setup ? '首次绑定 MFA' : '身份二次验证'} open footer={null}
    onCancel={onCancel} closable={!working && !recovery} maskClosable={false} keyboard={!working && !recovery}>
    <Spin spinning={loading}>
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {recovery ? <>
        <Alert type="warning" showIcon message="恢复码只显示一次，请安全保存。" />
        <pre className="mfa-recovery-codes">{recovery.recovery_codes.join('\n')}</pre>
        <Button type="primary" block onClick={() => onAuthenticated(recovery.user)}>已保存，进入平台</Button>
      </> : <Form layout="vertical" onFinish={(values) => void submit(values)} disabled={loading || working || !ready}>
        {setup ? <div style={{ display: 'flex', justifyContent: 'center', padding: '20px 0' }}>
          <QRCode type="svg" value={setup.otpauth_uri} size={220} aria-label="MFA 二维码" />
        </div> : null}
        <Form.Item name="code" label={setup ? '验证器验证码' : '验证码或恢复码'}
          rules={[{ required: true, ...(setup ? { pattern: /^\d{6}$/, message: '请输入 6 位数字验证码' } : {}) }]}>
          <Input autoComplete="one-time-code" maxLength={32} />
        </Form.Item>
        <Button type="primary" htmlType="submit" block loading={working}>{setup ? '确认绑定' : '验证并登录'}</Button>
      </Form>}
    </Spin>
  </Modal>;
}
