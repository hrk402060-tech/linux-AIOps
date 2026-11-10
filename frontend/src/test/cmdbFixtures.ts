import type { AuthUser } from '../api/auth';
import type { Asset } from '../types/asset';
import type { CmdbTreeResponse } from '../types/cmdb';

export const admin: AuthUser = {
  id: 'user-1', username: 'admin', display_name: '平台管理员', role: 'admin',
  enabled: true, must_change_password: false, created_at: '', last_login_at: null,
};
export const auditor: AuthUser = { ...admin, id: 'user-2', username: 'audit', role: 'auditor' };
export const tree: CmdbTreeResponse = {
  businesses: [{
    id: 'business-1', code: 'PAY', name: '支付业务', description: '核心交易域',
    owner_user_id: null, lifecycle_status: 'active', lifecycle_reason: null,
    archived_at: null, created_at: '', updated_at: '', applications: [{
      id: 'application-1', business_id: 'business-1', code: 'GATEWAY', name: '支付网关',
      description: '', owner_user_id: null, lifecycle_status: 'active', lifecycle_reason: null,
      archived_at: null, created_at: '', updated_at: '', services: [{
        id: 'service-1', business_id: 'business-1', application_id: 'application-1',
        code: 'PAY-API', name: '支付 API', description: '', owner_user_id: null,
        lifecycle_status: 'active', lifecycle_reason: null, archived_at: null,
        service_type: 'api', created_at: '', updated_at: '', apis: [{
          id: 'api-1', business_id: 'business-1', application_id: 'application-1',
          service_id: 'service-1', code: 'CREATE-ORDER', name: '创建订单', description: '',
          owner_user_id: null, lifecycle_status: 'active', lifecycle_reason: null,
          archived_at: null, http_method: 'POST', path: '/orders', created_at: '', updated_at: '',
        }], assets: [{
          id: 'relation-1', business_id: 'business-1', application_id: 'application-1',
          service_id: 'service-1', asset_id: 'asset-1', asset_name: '生产节点',
          asset_host: '192.0.2.10', relation_role: 'primary', status: 'active',
          linked_at: '', unlinked_at: null, created_at: '', updated_at: '',
        }],
      }],
    }],
  }],
};
export const assets: Asset[] = [{
  id: 'asset-1', name: '生产节点', host: '192.0.2.10', ssh_port: 22,
  os_family: 'openeuler', environment: 'production', group_name: '核心区',
  credential_id: null, credential: null, labels: [], status: 'online', lifecycle_status: 'enabled',
  lifecycle_reason: null, lifecycle_operator: null, archived_at: null, latency_ms: 5,
  check_message: null, last_checked_at: '', created_at: '', updated_at: '',
}];
