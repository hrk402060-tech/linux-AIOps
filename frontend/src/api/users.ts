import type { AuthUser, UserRole } from './auth';
import { apiFetch, parseApiResponse } from './client';

export interface UserCreate {
  username: string;
  display_name?: string;
  password: string;
  role: UserRole;
}

export interface UserList {
  total: number;
  items: AuthUser[];
}

export function getUsers(): Promise<UserList> {
  return apiFetch('/api/v1/users')
    .then((response) => parseApiResponse<UserList>(response, '用户列表加载失败'));
}

export function createUser(payload: UserCreate): Promise<AuthUser> {
  return apiFetch('/api/v1/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then((response) => parseApiResponse<AuthUser>(response, '用户创建失败'));
}

export function setUserEnabled(userId: string, enabled: boolean): Promise<AuthUser> {
  return apiFetch(`/api/v1/users/${encodeURIComponent(userId)}/enabled`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  }).then((response) => parseApiResponse<AuthUser>(response, '用户状态更新失败'));
}

export function resetUserPassword(userId: string, password: string): Promise<void> {
  return apiFetch(`/api/v1/users/${encodeURIComponent(userId)}/password`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  }).then((response) => parseApiResponse<void>(response, '密码重置失败'));
}
