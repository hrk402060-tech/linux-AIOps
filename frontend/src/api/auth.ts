import { apiFetch, parseApiResponse } from './client';

export type UserRole = 'admin' | 'operator' | 'auditor';

export interface AuthUser {
  id: string;
  username: string;
  display_name: string | null;
  role: UserRole;
  asset_scope?: 'all' | 'assigned';
  enabled: boolean;
  must_change_password: boolean;
  created_at: string;
  last_login_at: string | null;
  access_revision?: number;
  failed_login_attempts?: number;
  locked_until?: string | null;
  mfa_enabled?: boolean;
}

export interface UserCreate {
  username: string;
  display_name?: string;
  password: string;
  role: UserRole;
  asset_scope?: 'all' | 'assigned';
  must_change_password?: boolean;
}

export interface AssetAccessEntry {
  asset_id: string;
  can_ssh: boolean;
}

export interface UserAccess {
  user_id: string;
  asset_scope: 'all' | 'assigned';
  revision: number;
  entries: AssetAccessEntry[];
}

export interface MfaSetup {
  enabled: boolean;
  secret: string;
  otpauth_uri: string;
}

export interface MfaEnrollmentSetup extends MfaSetup {
  enrollment_token: string;
}

export interface MfaConfirmation {
  enabled: boolean;
  recovery_codes: string[];
}

export interface MfaSelfStatus {
  enabled: boolean;
  enforcement: 'off' | 'admins' | 'all';
  required: boolean;
  can_disable: boolean;
}

export interface AuthSession {
  id: string;
  user_id: string;
  username: string;
  display_name: string | null;
  role: UserRole;
  created_at: string;
  last_used_at: string;
  expires_at: string;
  client_ip: string | null;
  user_agent: string | null;
  status: 'active';
  is_current: boolean;
}

export interface AuthSessionPage {
  items: AuthSession[];
  total: number;
  offset: number;
  limit: number;
}

export function getUsers(): Promise<AuthUser[]> {
  return apiFetch('/api/v1/auth/users')
    .then((response) => parseApiResponse<AuthUser[]>(response, '用户列表加载失败'));
}

export function getActiveSessions(userId?: string): Promise<AuthSession[]> {
  const query = userId ? `?user_id=${encodeURIComponent(userId)}` : '';
  return apiFetch(`/api/v1/auth/sessions${query}`)
    .then((response) => parseApiResponse<AuthSession[]>(response, '活动会话加载失败'));
}

export function getActiveSessionsPage(
  userId: string, offset = 0, limit = 20,
): Promise<AuthSessionPage> {
  return apiFetch(
    `/api/v1/auth/sessions/page?user_id=${encodeURIComponent(userId)}&offset=${offset}&limit=${limit}`,
  ).then((response) => parseApiResponse<AuthSessionPage>(response, '活动会话加载失败'));
}

export function revokeUserSession(userId: string, sessionId: string): Promise<void> {
  return apiFetch(
    `/api/v1/auth/users/${encodeURIComponent(userId)}/sessions/${encodeURIComponent(sessionId)}/revoke`,
    { method: 'POST' },
  ).then((response) => parseApiResponse<void>(response, '会话撤销失败'));
}

export function createUser(payload: UserCreate): Promise<AuthUser> {
  return apiFetch('/api/v1/auth/users', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then((response) => parseApiResponse<AuthUser>(response, '用户创建失败'));
}

export function updateUser(
  id: string, payload: Partial<Pick<AuthUser, 'display_name' | 'role' | 'enabled'>>,
): Promise<AuthUser> {
  return apiFetch(`/api/v1/auth/users/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then((response) => parseApiResponse<AuthUser>(response, '用户更新失败'));
}

export function resetUserPassword(
  id: string, password: string, mustChangePassword = false,
): Promise<AuthUser> {
  return apiFetch(`/api/v1/auth/users/${id}/reset-password`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password, must_change_password: mustChangePassword }),
  }).then((response) => parseApiResponse<AuthUser>(response, '密码重置失败'));
}

export function unlockUser(id: string): Promise<AuthUser> {
  return apiFetch(`/api/v1/auth/users/${encodeURIComponent(id)}/unlock`, {
    method: 'POST',
  }).then((response) => parseApiResponse<AuthUser>(response, '解除登录锁定失败'));
}

export function setupUserMfa(id: string): Promise<MfaSetup> {
  return apiFetch(`/api/v1/auth/users/${encodeURIComponent(id)}/mfa/setup`, { method: 'POST' })
    .then((response) => parseApiResponse<MfaSetup>(response, 'MFA 设置失败'));
}

export function confirmUserMfa(id: string, code: string): Promise<MfaConfirmation> {
  return apiFetch(`/api/v1/auth/users/${encodeURIComponent(id)}/mfa/confirm`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  }).then((response) => parseApiResponse<MfaConfirmation>(response, 'MFA 验证失败'));
}

export function resetUserMfa(id: string): Promise<{ enabled: boolean }> {
  return apiFetch(`/api/v1/auth/users/${encodeURIComponent(id)}/mfa/reset`, { method: 'POST' })
    .then((response) => parseApiResponse<{ enabled: boolean }>(response, 'MFA 重置失败'));
}

export function getOwnMfa(): Promise<MfaSelfStatus> {
  return apiFetch('/api/v1/auth/me/mfa')
    .then((response) => parseApiResponse<MfaSelfStatus>(response, '个人 MFA 状态加载失败'));
}

export function setupOwnMfa(): Promise<MfaSetup> {
  return apiFetch('/api/v1/auth/me/mfa/setup', { method: 'POST' })
    .then((response) => parseApiResponse<MfaSetup>(response, '个人 MFA 设置失败'));
}

export function confirmOwnMfa(code: string): Promise<MfaConfirmation> {
  return apiFetch('/api/v1/auth/me/mfa/confirm', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  }).then((response) => parseApiResponse<MfaConfirmation>(response, '个人 MFA 验证失败'));
}

export function resetOwnMfa(): Promise<{ enabled: boolean }> {
  return apiFetch('/api/v1/auth/me/mfa/reset', { method: 'POST' })
    .then((response) => parseApiResponse<{ enabled: boolean }>(response, '个人 MFA 关闭失败'));
}

export function startMfaEnrollment(username: string, password: string): Promise<MfaEnrollmentSetup> {
  return apiFetch('/api/v1/auth/mfa/enroll/start', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  }).then((response) => parseApiResponse<MfaEnrollmentSetup>(response, '强制 MFA 绑定启动失败'));
}

export function confirmMfaEnrollment(
  enrollmentToken: string, code: string,
): Promise<MfaConfirmation> {
  return apiFetch('/api/v1/auth/mfa/enroll/confirm', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enrollment_token: enrollmentToken, code }),
  }).then((response) => parseApiResponse<MfaConfirmation>(response, '强制 MFA 验证失败'));
}

export function revokeUserSessions(id: string): Promise<void> {
  return apiFetch(`/api/v1/auth/users/${id}/revoke-sessions`, { method: 'POST' })
    .then((response) => parseApiResponse<void>(response, '会话撤销失败'));
}

export function getUserAssetAccess(id: string): Promise<UserAccess> {
  return apiFetch(`/api/v1/auth/users/${encodeURIComponent(id)}/asset-access`)
    .then((response) => parseApiResponse<UserAccess>(response, '资产权限加载失败'));
}

export function updateUserAssetAccess(
  id: string, payload: Omit<UserAccess, 'user_id' | 'revision'> & {
    expected_revision: number; reason: string; confirmation: string;
  },
): Promise<UserAccess> {
  return apiFetch(`/api/v1/auth/users/${encodeURIComponent(id)}/asset-access`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then((response) => parseApiResponse<UserAccess>(response, '资产权限保存失败'));
}

export function login(username: string, password: string, otpCode?: string): Promise<AuthUser> {
  return apiFetch('/api/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, otp_code: otpCode || undefined }),
  }).then((response) => parseApiResponse<AuthUser>(response, '用户名或密码错误'));
}

export function getCurrentUser(): Promise<AuthUser> {
  return apiFetch('/api/v1/auth/me')
    .then((response) => parseApiResponse<AuthUser>(response, '会话已失效'));
}

export function logout(): Promise<void> {
  return apiFetch('/api/v1/auth/logout', { method: 'POST' })
    .then((response) => parseApiResponse<void>(response, '退出失败'));
}

export function changePassword(currentPassword: string, newPassword: string): Promise<AuthUser> {
  return apiFetch('/api/v1/auth/change-password', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      current_password: currentPassword, new_password: newPassword,
    }),
  }).then((response) => parseApiResponse<AuthUser>(response, '密码修改失败'));
}
