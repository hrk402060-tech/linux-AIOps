import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { FeatureAccessPanel } from './FeatureAccessPanel';
import { getAccessPolicy, saveAccessPolicy, type AccessPolicy } from '../api/accessPolicy';
import { ApiError } from '../api/client';
import type { AuthUser } from '../api/auth';

vi.mock('../api/accessPolicy', () => ({ getAccessPolicy: vi.fn(), saveAccessPolicy: vi.fn() }));

const operator: AuthUser = { id: 'op-1', username: 'operator', display_name: null, role: 'operator',
  enabled: true, must_change_password: false, created_at: '', last_login_at: null };
const initial: AccessPolicy = { revision: 'rev-1', roles: [], groups: [], users: [],
  catalog: [{ page: 'assets', label: '服务器资产', write: true }] };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getAccessPolicy).mockResolvedValue(initial);
});

it('saves an administrator-defined role with the loaded policy revision', async () => {
  vi.mocked(saveAccessPolicy).mockImplementation(async (revision, update) => ({ ...initial, ...update, revision }));
  render(<FeatureAccessPanel users={[operator]} />);
  fireEvent.click(await screen.findByRole('button', { name: '新建角色' }));
  fireEvent.change(screen.getByRole('textbox', { name: '角色名称' }), { target: { value: '资产观察员' } });
  fireEvent.click(screen.getByRole('checkbox', { name: '服务器资产 查看' }));
  fireEvent.click(screen.getByRole('button', { name: '应用到草稿' }));
  expect(await screen.findByText('资产观察员')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '保存授权策略' }));
  await waitFor(() => expect(saveAccessPolicy).toHaveBeenCalledWith('rev-1', {
    roles: [{ id: expect.any(String), name: '资产观察员', permissions: ['assets:read'] }], users: [], groups: [],
  }));
});

it('keeps a denied user managed and retains the draft on conflict', async () => {
  vi.mocked(saveAccessPolicy).mockRejectedValue(new ApiError('策略版本不一致', 409));
  render(<FeatureAccessPanel users={[operator]} />);
  fireEvent.click(await screen.findByRole('tab', { name: '用户授权' }));
  fireEvent.click(await screen.findByRole('button', { name: '配置功能权限 operator' }));
  fireEvent.click(screen.getByRole('button', { name: '应用到草稿' }));
  await screen.findByText('未开放');
  fireEvent.click(screen.getByRole('button', { name: '保存授权策略' }));
  expect(await screen.findByText('授权策略已被其他管理员修改，当前草稿仍保留。')).toBeVisible();
  expect(screen.getByText('未开放')).toBeVisible();
  expect(saveAccessPolicy).toHaveBeenCalledWith('rev-1', {
    roles: [], groups: [], users: [{ user_id: 'op-1', role_ids: [], group_ids: [], permissions: [] }],
  });
});
