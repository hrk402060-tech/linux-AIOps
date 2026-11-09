import { useEffect, useRef, useState } from 'react';
import { Button, Form, Input, Modal } from 'antd';
import { ArrowRight, CircleHelp, Eye, EyeOff, Headphones, KeyRound, Layers, LockKeyhole, UserRound } from 'lucide-react';

import { ApiError } from '../api/client';
import { login, type AuthUser } from '../api/auth';
import type { PetState } from './LoginCompanion';
import { LoginPlatformScene } from './LoginPlatformScene';
import './AccessGate.css';

export function AccessGate({ onAuthenticated }: { onAuthenticated: (user: AuthUser) => void }) {
  const [loading, setLoading] = useState(false);
  const [petState, setPetState] = useState<PetState>('idle');
  const [mfaRequired, setMfaRequired] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const loadingRef = useRef(false);
  const successTimer = useRef<number | undefined>(undefined);
  useEffect(() => () => {
    if (successTimer.current !== undefined) window.clearTimeout(successTimer.current);
  }, []);

  const authenticate = async ({ username, password, otp_code }: {
    username: string; password: string; otp_code?: string;
  }) => {
    if (loadingRef.current || successTimer.current !== undefined) return;
    loadingRef.current = true;
    setLoading(true);
    setPetState('loading');
    setErrorMessage('');
    try {
      const user = await login(username.trim(), password, otp_code);
      setPetState('success');
      successTimer.current = window.setTimeout(() => {
        setPetState('welcome');
        onAuthenticated(user);
      }, 1300);
    } catch (error) {
      if (error instanceof ApiError && error.code === 'mfa_required') {
        setMfaRequired(true);
        setPetState('idle');
      } else {
        if (error instanceof ApiError && error.code === 'mfa_setup_required') setMfaRequired(false);
        setPetState('failure');
        setErrorMessage(error instanceof Error ? error.message : '登录失败');
      }
    } finally {
      if (successTimer.current === undefined) {
        loadingRef.current = false;
        setLoading(false);
      }
    }
  };

  return (
    <main className="platform-login" data-state={petState}>
      <div className="pl-screen">
        <header className="pl-header">
          <div className="pl-brand">
            <span className="pl-mark" aria-hidden="true"><span /><span /><span /><span /></span>
            <span>LINUX OPS</span>
          </div>
          <button className="pl-header-help" type="button" aria-label="帮助与支持" onClick={() => setHelpOpen(true)}>
            <CircleHelp size={17} aria-hidden="true" /><span>帮助与支持</span>
          </button>
        </header>
        <div className="pl-main">
          <section className="pl-identity" aria-labelledby="platform-login-title">
            <p className="pl-eyebrow">OPERATIONS PLATFORM</p>
            <h1 id="platform-login-title"><span>Linux 智能</span><span>综合运维平台</span></h1>
            <p className="pl-intro">连接资产、监控、自动化与安全治理。</p>
          </section>
          <section className="pl-architecture" aria-label="平台架构">
            <LoginPlatformScene state={petState} />
          </section>
          <section className="pl-auth" aria-labelledby="platform-login-heading">
            <div className="pl-auth-heading">
              <h2 id="platform-login-heading">登录工作台</h2>
              <p>欢迎回来，请使用平台账号登录</p>
            </div>
            <Form layout="vertical" requiredMark={false} onFinish={authenticate}
              onValuesChange={() => setErrorMessage('')}>
              <Form.Item name="username" label="用户名"
                rules={[{ required: true, whitespace: true, message: '请输入用户名' }]}>
                <Input size="large" prefix={<UserRound size={18} aria-hidden="true" />}
                  autoComplete="username" maxLength={64} placeholder="请输入用户名" disabled={loading}
                  onFocus={() => { if (!loadingRef.current) setPetState('input'); }}
                  onBlur={() => { if (!loadingRef.current) setPetState('idle'); }} />
              </Form.Item>
              <div className="pl-password-field">
                <button className="pl-forgot" type="button" onClick={() => setHelpOpen(true)}>忘记密码？</button>
                <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
                  <Input size="large" type={passwordVisible ? 'text' : 'password'}
                    prefix={<LockKeyhole size={18} aria-hidden="true" />}
                    suffix={<button className="pl-eye" type="button"
                      aria-label={passwordVisible ? '隐藏密码' : '显示密码'} aria-pressed={passwordVisible}
                      disabled={loading} onClick={() => setPasswordVisible((value) => !value)}>
                      {passwordVisible ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
                    </button>}
                    autoComplete="current-password" maxLength={128} placeholder="请输入密码" disabled={loading}
                    onFocus={() => { if (!loadingRef.current) setPetState('closed'); }}
                    onBlur={() => { if (!loadingRef.current) setPetState('idle'); }} />
                </Form.Item>
              </div>
              {mfaRequired ? <Form.Item name="otp_code" label="MFA 验证码"
                extra="输入验证器的 6 位验证码，或一次性恢复码">
                <Input size="large" autoComplete="one-time-code" maxLength={32}
                  placeholder="输入 MFA 验证码" disabled={loading} />
              </Form.Item> : null}
              <div className="pl-feedback">
                {errorMessage ? <p className="pl-error" role="alert">{errorMessage}</p>
                  : <p role="status" aria-live="polite">{petState === 'success'
                    ? '验证成功，正在进入工作台' : loading ? '正在验证账号，请稍候' : ''}</p>}
              </div>
              <Button className="pl-submit" block size="large" type="primary" htmlType="submit"
                aria-label={mfaRequired ? '验证并登录' : '登录'} loading={loading} disabled={loading}>
                <span>{mfaRequired ? '验证并登录' : '登录'}</span>
                {!loading ? <ArrowRight size={18} aria-hidden="true" /> : null}
              </Button>
            </Form>
            <div className="pl-auth-bottom">
              <span><KeyRound size={14} aria-hidden="true" />统一身份认证</span>
              <button className="pl-contact" type="button" onClick={() => setHelpOpen(true)}>
                <Headphones size={15} aria-hidden="true" />联系管理员
              </button>
            </div>
          </section>
        </div>
        <footer className="pl-footer">
          <span>Linux 智能综合运维平台</span>
          <span><Layers size={13} aria-hidden="true" />资产 · 监控 · 自动化 · 治理</span>
        </footer>
      </div>
      <Modal title="账号帮助" open={helpOpen} onCancel={() => setHelpOpen(false)}
        getContainer={false} width={400} closable={false}
        footer={<Button type="primary" onClick={() => setHelpOpen(false)}>知道了</Button>}>
        <p className="pl-help-text">账号开通、密码重置或二次验证问题，请联系平台管理员。</p>
      </Modal>
    </main>
  );
}
