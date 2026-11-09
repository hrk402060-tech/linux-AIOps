import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';

import { PasswordChangeGate } from './PasswordChangeGate';

afterEach(() => vi.unstubAllGlobals());

it('blocks a weak new password and accepts the eight-character policy', async () => {
  const onChanged = vi.fn();
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      id: 'user-1', username: 'ops-user', display_name: '运维用户',
      role: 'operator', enabled: true, must_change_password: false,
      created_at: '2026-09-18T00:00:00Z', last_login_at: null,
    }),
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider><PasswordChangeGate onChanged={onChanged} /></ConfigProvider>);

  fireEvent.change(screen.getByLabelText('当前密码'), { target: { value: 'Abcd12!x' } });
  fireEvent.change(screen.getByLabelText('新密码'), { target: { value: 'abcdefgh' } });
  fireEvent.change(screen.getByLabelText('确认新密码'), { target: { value: 'abcdefgh' } });
  fireEvent.click(screen.getByRole('button', { name: '修改密码并进入' }));
  expect((await screen.findAllByText('密码至少 8 位，且必须包含英文字母、数字和特殊字符')).length).toBeGreaterThan(0);
  expect(fetchMock).not.toHaveBeenCalled();

  fireEvent.change(screen.getByLabelText('新密码'), { target: { value: 'Zyxw98!q' } });
  fireEvent.change(screen.getByLabelText('确认新密码'), { target: { value: 'Zyxw98!q' } });
  fireEvent.click(screen.getByRole('button', { name: '修改密码并进入' }));

  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/change-password', expect.objectContaining({
    body: JSON.stringify({ current_password: 'Abcd12!x', new_password: 'Zyxw98!q' }),
  }));
});
