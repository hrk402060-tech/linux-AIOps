import { apiFetch, parseApiResponse } from './client';
import type { AuthUser, MfaSetup } from './auth';

export type IdentityProvider = 'ldap' | 'oidc';
export interface IdentitySourceState {
  revision?: string;
  secret_configured: boolean; tested_at: string | null; test_success: boolean;
  verified_admin_login: boolean; updated_at: string | null;
}
export interface LdapSource {
  server_url: string; base_dn: string; bind_dn: string; bind_password?: string | null;
  user_filter: string; username_attribute: string; subject_attribute: string;
  display_name_attribute: string; timeout_seconds: number; start_tls?: boolean;
}
export interface OidcSource {
  issuer_url: string; client_id: string; client_secret?: string | null; redirect_uri: string;
  scopes: string[]; timeout_seconds: number;
}
export type IdentitySource = (LdapSource | OidcSource) & IdentitySourceState;
export interface IdentityTest { success: boolean; message: string; checked_at: string; latency_ms: number }
const base = '/api/v1/system/identity-sources';
export async function getIdentitySource(provider: IdentityProvider): Promise<IdentitySource> {
  return parseApiResponse<IdentitySource>(await apiFetch(`${base}/${provider}`), '身份源配置加载失败');
}
export async function saveIdentitySource(provider: IdentityProvider, payload: LdapSource | OidcSource, revision?: string): Promise<IdentitySource> {
  return parseApiResponse<IdentitySource>(await apiFetch(`${base}/${provider}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json', ...(revision ? { 'If-Match': revision } : {}) }, body: JSON.stringify(payload),
  }), '身份源配置保存失败');
}
export async function testIdentitySource(provider: IdentityProvider, payload: LdapSource | OidcSource): Promise<IdentityTest> {
  return parseApiResponse<IdentityTest>(await apiFetch(`${base}/${provider}/test`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    signal: AbortSignal.timeout(payload.timeout_seconds * 3000 + 5000),
  }), '身份源连接测试失败');
}
export interface IdentityBinding {
  id?: string; subject: string; user_id: string | null; username?: string;
  kind?: 'linked' | 'invited'; role?: 'operator' | 'auditor'; expires_at?: string;
}
export interface IdentityInvitation {
  subject: string; username: string; display_name?: string;
  role: 'operator' | 'auditor'; expires_hours: number;
}
export async function getIdentityBindings(provider: IdentityProvider): Promise<IdentityBinding[]> {
  return parseApiResponse<IdentityBinding[]>(await apiFetch(`${base}/${provider}/bindings`), '身份绑定加载失败');
}
export async function createIdentityBinding(provider: IdentityProvider, subject: string, userId: string): Promise<IdentityBinding> {
  return parseApiResponse<IdentityBinding>(await apiFetch(`${base}/${provider}/bindings`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subject, user_id: userId }),
  }), '身份绑定保存失败');
}
export async function deleteIdentityBinding(provider: IdentityProvider, subject: string): Promise<void> {
  return parseApiResponse<void>(await apiFetch(`${base}/${provider}/bindings?subject=${encodeURIComponent(subject)}`, {
    method: 'DELETE',
  }), '身份绑定解除失败');
}
export async function inviteIdentityUser(provider: IdentityProvider, payload: IdentityInvitation): Promise<IdentityBinding> {
  return parseApiResponse<IdentityBinding>(await apiFetch(`${base}/${provider}/invitations`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }), '外部账号邀请失败');
}
export interface IdentityOptions { local_login_enabled: boolean; ldap_enabled: boolean; oidc_enabled: boolean }
export async function getIdentityOptions(): Promise<IdentityOptions> {
  return parseApiResponse<IdentityOptions>(await apiFetch('/api/v1/auth/identity/options'), '登录方式加载失败');
}
export async function loginLdap(username: string, password: string, otpCode?: string): Promise<AuthUser> {
  return parseApiResponse<AuthUser>(await apiFetch('/api/v1/auth/identity/ldap/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, otp_code: otpCode || undefined }),
    signal: AbortSignal.timeout(35_000),
  }), 'LDAP 登录失败');
}
export async function getIdentityMfaStatus(): Promise<{ required: boolean; enrollment_required: boolean }> {
  return parseApiResponse(await apiFetch('/api/v1/auth/identity/mfa/status'), '外部登录验证已过期，请重新登录');
}
export async function setupIdentityMfa(): Promise<MfaSetup> {
  return parseApiResponse(await apiFetch('/api/v1/auth/identity/mfa/setup', { method: 'POST' }), 'MFA 绑定准备失败');
}
export async function verifyIdentityMfa(code: string): Promise<AuthUser> {
  return parseApiResponse(await apiFetch('/api/v1/auth/identity/mfa/verify', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }),
  }), 'MFA 验证失败');
}
export async function confirmIdentityMfa(code: string): Promise<{ user: AuthUser; recovery_codes: string[] }> {
  return parseApiResponse(await apiFetch('/api/v1/auth/identity/mfa/confirm', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }),
  }), 'MFA 绑定失败');
}
