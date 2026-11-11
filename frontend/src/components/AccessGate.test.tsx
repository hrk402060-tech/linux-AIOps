import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AccessGate } from './AccessGate';
import { LoginCompanion } from './LoginCompanion';

vi.mock('./LoginPlatformScene', () => ({
  LoginPlatformScene: ({ state }: { state: string }) => (
    <div role="img" aria-label="平台架构示意" data-state={state} />
  ),
}));

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function renderLogin(onAuthenticated = vi.fn()) {
  return { onAuthenticated, ...render(<ConfigProvider><AccessGate onAuthenticated={onAuthenticated} /></ConfigProvider>) };
}

function enterCredentials(username = 'security-admin', password = 'Correct-Horse-Battery-2026') {
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: username } });
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: password } });
}

function platformScene() {
  return screen.getByRole('img', { name: '平台架构示意' });
}

function delayWelcome() {
  const original = window.setTimeout.bind(window);
  let finish: (() => void) | undefined;
  vi.spyOn(window, 'setTimeout').mockImplementation((handler, delay, ...args) => {
    if (delay === 1300 && typeof handler === 'function') {
      finish = () => handler(...args);
      return 123456;
    }
    return original(handler, delay, ...args);
  });
  return () => {
    expect(finish).toBeDefined();
    act(() => finish?.());
  };
}

function acceptedLogin(mustChangePassword = false) {
  return {
    ok: true, status: 200,
    json: async () => ({
      id: 'user-1', username: 'security-admin', display_name: '管理员',
      role: 'admin', enabled: true, must_change_password: mustChangePassword,
      created_at: '2026-09-15T00:00:00Z', last_login_at: null,
    }),
  };
}

it('shows the approved light platform login without a decorative form card or fake live status', () => {
  const { container } = renderLogin();
  expect(container.querySelector('.platform-login')).toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Linux 智能综合运维平台');
  expect(screen.getByRole('heading', { name: '登录工作台' })).toBeInTheDocument();
  expect(screen.getByRole('region', { name: '平台架构' })).toBeInTheDocument();
  expect(container.querySelector('.access-terminal')).toBeNull();
  expect(container.querySelector('.login-companion')).toBeNull();
  expect(container.querySelector('.ant-card')).toBeNull();
  expect(screen.queryByText('服务状态正常')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('MFA 验证码')).not.toBeInTheDocument();
});

it('validates required fields before calling authentication', async () => {
  vi.stubGlobal('fetch', vi.fn());
  renderLogin();
  fireEvent.click(screen.getByRole('button', { name: '登录' }));
  expect(await screen.findByText('请输入用户名')).toBeInTheDocument();
  expect(await screen.findByText('请输入密码')).toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

it('keeps cookie authentication, trims only username, blocks duplicates and preserves forced password change', async () => {
  const finishWelcome = delayWelcome();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(acceptedLogin(true)));
  const { onAuthenticated, container } = renderLogin();
  enterCredentials('  security-admin  ', ' Password-With-Spaces-2026 ');
  fireEvent.submit(container.querySelector('form')!);
  fireEvent.submit(container.querySelector('form')!);
  await waitFor(() => expect(platformScene()).toHaveAttribute('data-state', 'success'));
  expect(fetch).toHaveBeenCalledOnce();
  expect(fetch).toHaveBeenCalledWith('/api/v1/auth/login', expect.objectContaining({
    method: 'POST', credentials: 'include',
    body: JSON.stringify({ username: 'security-admin', password: ' Password-With-Spaces-2026 ' }),
  }));
  expect(screen.getByLabelText('用户名')).toBeDisabled();
  expect(screen.getByLabelText('密码')).toBeDisabled();
  expect(screen.getByRole('button', { name: '登录' })).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent('验证成功，正在进入工作台');
  fireEvent.focus(screen.getByLabelText('密码'));
  expect(platformScene()).toHaveAttribute('data-state', 'success');
  expect(onAuthenticated).not.toHaveBeenCalled();
  finishWelcome();
  expect(onAuthenticated).toHaveBeenCalledWith(expect.objectContaining({ must_change_password: true }));
  expect(screen.queryByText('访问令牌')).not.toBeInTheDocument();
});

it.each(['123456', 'recovery-code-ABC123'])('requests MFA only when required and submits code %s unchanged', async (code) => {
  const finishWelcome = delayWelcome();
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({ detail: 'mfa_required' }) })
    .mockResolvedValueOnce(acceptedLogin()));
  const { onAuthenticated } = renderLogin();
  enterCredentials();
  fireEvent.click(screen.getByRole('button', { name: '登录' }));
  const input = await screen.findByLabelText('MFA 验证码');
  expect(input).toHaveAttribute('maxlength', '32');
  expect(screen.getByText('输入验证器的 6 位验证码，或一次性恢复码')).toBeInTheDocument();
  expect(onAuthenticated).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: code } });
  fireEvent.click(screen.getByRole('button', { name: '验证并登录' }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/auth/login', expect.objectContaining({
    body: JSON.stringify({ username: 'security-admin', password: 'Correct-Horse-Battery-2026', otp_code: code }),
  }));
  await waitFor(() => expect(platformScene()).toHaveAttribute('data-state', 'success'));
  finishWelcome();
  expect(onAuthenticated).toHaveBeenCalledOnce();
});

it('enrolls required MFA with a QR code and requires a fresh login after saving recovery codes', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({ detail: 'mfa_setup_required' }) })
    .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({
      enabled: false, secret: 'SETUP-SECRET', otpauth_uri: 'otpauth://totp/Linux:admin?secret=SETUPSECRET',
      enrollment_token: 'enrollment-only-token',
    }) })
    .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({
      enabled: true, recovery_codes: ['RECOVERY-ONCE'],
    }) });
  vi.stubGlobal('fetch', fetchMock);
  const { onAuthenticated } = renderLogin();
  enterCredentials();
  fireEvent.click(screen.getByRole('button', { name: '登录' }));
  const dialog = within(await screen.findByRole('dialog', { name: '首次绑定 MFA' }));
  expect(dialog.getByLabelText('MFA 二维码')).toBeInTheDocument();
  expect(dialog.getByText('无法扫码时手动绑定')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/v1/auth/mfa/enroll/start', expect.objectContaining({
    body: JSON.stringify({ username: 'security-admin', password: 'Correct-Horse-Battery-2026' }),
  }));
  fireEvent.change(dialog.getByLabelText('验证器验证码'), { target: { value: '123456' } });
  fireEvent.click(dialog.getByRole('button', { name: '确认绑定' }));
  const recovery = within(await screen.findByRole('dialog', { name: 'MFA 已绑定' }));
  expect(recovery.getByText('RECOVERY-ONCE')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith('/api/v1/auth/mfa/enroll/confirm', expect.objectContaining({
    body: JSON.stringify({ enrollment_token: 'enrollment-only-token', code: '123456' }),
  }));
  expect(onAuthenticated).not.toHaveBeenCalled();
  fireEvent.click(recovery.getByRole('button', { name: '返回登录' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(screen.getByLabelText('用户名')).toHaveValue('');
  expect(screen.getByLabelText('密码')).toHaveValue('');
  expect(screen.queryByLabelText('MFA 验证码')).not.toBeInTheDocument();
  expect(onAuthenticated).not.toHaveBeenCalled();
});

it('shows enrollment confirmation errors inside the QR dialog without bypassing login', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({ detail: 'mfa_setup_required' }) })
    .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({
      enabled: false, secret: 'SETUP-SECRET', otpauth_uri: 'otpauth://totp/Linux:admin?secret=SETUPSECRET',
      enrollment_token: 'enrollment-only-token',
    }) })
    .mockResolvedValue({ ok: false, status: 400, json: async () => ({ detail: '验证码不正确，请重新输入' }) });
  vi.stubGlobal('fetch', fetchMock);
  const { onAuthenticated } = renderLogin();
  enterCredentials();
  fireEvent.click(screen.getByRole('button', { name: '登录' }));
  const dialog = within(await screen.findByRole('dialog', { name: '首次绑定 MFA' }));
  fireEvent.change(dialog.getByLabelText('验证器验证码'), { target: { value: 'ABCDEF' } });
  fireEvent.click(dialog.getByRole('button', { name: '确认绑定' }));
  expect(await dialog.findByText('请输入 6 位数字验证码')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);
  fireEvent.change(dialog.getByLabelText('验证器验证码'), { target: { value: '000000' } });
  fireEvent.click(dialog.getByRole('button', { name: '确认绑定' }));
  expect(await dialog.findByText('验证码不正确，请重新输入')).toBeInTheDocument();
  expect(dialog.getByLabelText('MFA 二维码')).toBeInTheDocument();
  expect(dialog.getByRole('button', { name: '确认绑定' })).toBeEnabled();
  expect(onAuthenticated).not.toHaveBeenCalled();
  expect(screen.queryByRole('dialog', { name: 'MFA 已绑定' })).not.toBeInTheDocument();
});

it('keeps credentials disabled while loading and shows a readable error after failure', async () => {
  let finishLogin: (() => void) | undefined;
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => new Promise((resolve) => {
    finishLogin = () => resolve({ ok: false, status: 401, json: async () => ({ detail: '登录失败' }) });
  })));
  const { onAuthenticated } = renderLogin();
  enterCredentials('operator', 'bad-password');
  fireEvent.click(screen.getByRole('button', { name: '登录' }));
  await waitFor(() => expect(platformScene()).toHaveAttribute('data-state', 'loading'));
  expect(screen.getByLabelText('用户名')).toBeDisabled();
  expect(screen.getByLabelText('密码')).toBeDisabled();
  expect(fetch).toHaveBeenCalledOnce();
  await act(async () => { finishLogin?.(); });
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('登录失败'));
  expect(platformScene()).toHaveAttribute('data-state', 'failure');
  expect(screen.getByRole('button', { name: '登录' })).toBeEnabled();
  expect(screen.queryByText('bad-password')).not.toBeInTheDocument();
  expect(onAuthenticated).not.toHaveBeenCalled();
});

it('passes input focus states to the platform scene', async () => {
  renderLogin();
  expect(platformScene()).toHaveAttribute('data-state', 'idle');
  await userEvent.click(screen.getByLabelText('用户名'));
  expect(platformScene()).toHaveAttribute('data-state', 'input');
  await userEvent.click(screen.getByLabelText('密码'));
  expect(platformScene()).toHaveAttribute('data-state', 'closed');
});

it('toggles password visibility using Chinese accessible commands without submitting', async () => {
  vi.stubGlobal('fetch', vi.fn());
  renderLogin();
  enterCredentials();
  expect(screen.getByLabelText('密码')).toHaveAttribute('type', 'password');
  await userEvent.click(screen.getByRole('button', { name: '显示密码' }));
  expect(screen.getByLabelText('密码')).toHaveAttribute('type', 'text');
  await userEvent.click(screen.getByRole('button', { name: '隐藏密码' }));
  expect(screen.getByLabelText('密码')).toHaveAttribute('type', 'password');
  expect(fetch).not.toHaveBeenCalled();
});

it.each(['帮助与支持', '忘记密码？', '联系管理员'])('opens useful account help from %s without making network requests', async (action) => {
  vi.stubGlobal('fetch', vi.fn());
  renderLogin();
  await userEvent.click(screen.getByRole('button', { name: action }));
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByText('账号开通、密码重置或二次验证问题，请联系平台管理员。')).toBeInTheDocument();
  expect(within(dialog).queryByRole('button', { name: 'Close' })).not.toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole('button', { name: '知道了' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(fetch).not.toHaveBeenCalled();
});

it('clears the success timer on unmount before handing control to the app', async () => {
  delayWelcome();
  const clearTimer = vi.spyOn(window, 'clearTimeout');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(acceptedLogin()));
  const { onAuthenticated, unmount } = renderLogin();
  enterCredentials();
  fireEvent.click(screen.getByRole('button', { name: '登录' }));
  await waitFor(() => expect(platformScene()).toHaveAttribute('data-state', 'success'));
  unmount();
  expect(clearTimer).toHaveBeenCalledWith(123456);
  expect(onAuthenticated).not.toHaveBeenCalled();
});

describe('legacy login companion retained for compatibility', () => {
  it('bounds pointer tracking with distinct strengths and resets on window blur', () => {
    const { container } = render(<LoginCompanion state="idle" />);
    const scene = screen.getByRole('img');
    vi.spyOn(scene, 'getBoundingClientRect').mockReturnValue({
      x: 0, y: 0, left: 0, top: 0, right: 400, bottom: 280, width: 400, height: 280, toJSON: () => ({}),
    });
    fireEvent(window, new MouseEvent('pointermove', { clientX: 1200, clientY: 800 }));
    const characters = container.querySelectorAll<HTMLElement>('.scene-character');
    expect(characters[0].style.getPropertyValue('--look-x')).toBe('12px');
    expect(characters[3].style.getPropertyValue('--look-x')).toBe('22px');
    expect(characters[0].style.getPropertyValue('--gaze-x')).toBe('7px');
    expect(characters[3].style.getPropertyValue('--gaze-x')).toBe('4px');
    fireEvent.blur(window);
    expect(characters[0].style.getPropertyValue('--look-x')).toBe('0px');
    expect(characters[3].style.getPropertyValue('--gaze-x')).toBe('0px');
  });

  it('keeps four staggered characters without a background grid', () => {
    const { container, rerender } = render(<LoginCompanion state="idle" />);
    const characters = container.querySelectorAll<HTMLElement>('.scene-character');
    expect(characters).toHaveLength(4);
    expect(Array.from(characters, (character) => character.style.getPropertyValue('--delay')))
      .toEqual(['0ms', '150ms', '300ms', '450ms']);
    expect(container.querySelector('.scene-grid')).toBeNull();
    rerender(<LoginCompanion state="success" />);
    expect(screen.getByRole('img')).toHaveAttribute('data-state', 'success');
    expect(container.querySelectorAll('.hand-right')).toHaveLength(4);
  });
});
