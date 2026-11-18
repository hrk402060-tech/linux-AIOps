import { apiFetch, parseApiResponse } from './client';

export type CatalogItem = { page: string; label: string; write: boolean };
export type CustomRole = { id: string; name: string; permissions: string[] };
export type UserGroup = { id: string; name: string; role_ids: string[] };
export type UserAssignment = { user_id: string; role_ids: string[]; group_ids: string[]; permissions: string[] };
export type AccessPolicyUpdate = { roles: CustomRole[]; groups: UserGroup[]; users: UserAssignment[] };
export type AccessPolicy = AccessPolicyUpdate & { revision: string; catalog: CatalogItem[] };

const path = '/api/v1/auth/access-policy';

export function getAccessPolicy(): Promise<AccessPolicy> {
  return apiFetch(path).then((response) => parseApiResponse<AccessPolicy>(response, '功能授权读取失败'));
}

export function saveAccessPolicy(revision: string, update: AccessPolicyUpdate): Promise<AccessPolicy> {
  return apiFetch(path, {
    method: 'PUT', headers: { 'Content-Type': 'application/json', 'If-Match': revision }, body: JSON.stringify(update),
  }).then((response) => parseApiResponse<AccessPolicy>(response, '功能授权保存失败'));
}
