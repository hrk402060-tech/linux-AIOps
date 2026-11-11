import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider, Modal } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { UserSecurityPage } from './UserSecurityPage';

afterEach(() => { Modal.destroyAll(); vi.unstubAllGlobals(); });

it('lets a user scan a QR code and confirm personal MFA', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith('/auth/me/mfa/setup')) return Promise.resolve({ ok: true, json: async () => ({
      enabled: false, secret: 'JBSWY3DPEHPK3PXP',
      otpauth_uri: 'otpauth://totp/Linux:mfa-user?secret=JBSWY3DPEHPK3PXP',
    }) });
    if (url.endsWith('/auth/me/mfa/confirm')) return Promise.resolve({ ok: true, json: async () => ({
      enabled: true, recovery_codes: ['AAAAA-BBBBB'],
    }) });
    return Promise.resolve({ ok: true, json: async () => ({
      enabled: false, enforcement: 'off', required: false, can_disable: true,
    }) });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider><UserSecurityPage onLogout={vi.fn()} /></ConfigProvider>);

  fireEvent.click(await screen.findByRole('button', { name: '启用 MFA' }));
  const dialog = within(await screen.findByRole('dialog'));
  expect(dialog.getByLabelText('MFA 二维码')).toBeInTheDocument();
  expect(dialog.getByText('无法扫码时手动绑定')).toBeInTheDocument();
  fireEvent.change(dialog.getByLabelText('验证器验证码'), { target: { value: '123456' } });
  fireEvent.click(dialog.getByRole('button', { name: '确认启用' }));
  expect(await screen.findByText('恢复码只显示这一次，请安全保存。')).toBeInTheDocument();
  expect(screen.queryByText('当前策略要求保持 MFA 绑定')).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/me/mfa/confirm', expect.objectContaining({ method: 'POST' }));
});

it('keeps unknown MFA state unavailable while loading and after a failed request', async () => {
  let finish: ((response: Response) => void) | undefined;
  const fetchMock = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }))
    .mockResolvedValue({ ok: true, json: async () => ({
      enabled: false, enforcement: 'off', required: false, can_disable: true,
    }) });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider><UserSecurityPage onLogout={vi.fn()} /></ConfigProvider>);
  expect(screen.queryByText('未启用', { selector: '.ant-tag' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '启用 MFA' })).not.toBeInTheDocument();
  await act(async () => finish?.({
    ok: false, status: 503, json: async () => ({ detail: '个人安全服务暂时不可用' }),
  } as Response));
  expect(await screen.findByRole('alert')).toHaveTextContent('个人安全服务暂时不可用');
  expect(screen.queryByRole('button', { name: '启用 MFA' })).not.toBeInTheDocument();
  expect(screen.queryByText('未启用', { selector: '.ant-tag' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '刷新个人安全设置' }));
  expect(await screen.findByRole('button', { name: '启用 MFA' })).toBeEnabled();
});

it('keeps the disable action unavailable when policy requires MFA', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    enabled: true, enforcement: 'all', required: true, can_disable: false,
  }) }));
  render(<ConfigProvider><UserSecurityPage onLogout={vi.fn()} /></ConfigProvider>);
  await waitFor(() => expect(screen.getByText('由管理员策略强制')).toBeInTheDocument());
  expect(screen.queryByRole('button', { name: '关闭 MFA' })).not.toBeInTheDocument();
});

it('renders embedded personal MFA without adding another page heading and keeps refresh available', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    enabled: true, enforcement: 'off', required: false, can_disable: true,
  }) });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider><UserSecurityPage embedded onLogout={vi.fn()} /></ConfigProvider>);
  expect(await screen.findByRole('button', { name: '关闭 MFA' })).toBeEnabled();
  expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '刷新个人安全设置' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
});
