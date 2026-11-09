export type CmdbLifecycleStatus = 'active' | 'disabled' | 'archived';
export type CmdbEntityType = 'business' | 'application' | 'service' | 'api';
export type CmdbRelationStatus = 'active' | 'unlinked';
export type CmdbDependencyStatus = 'active' | 'disabled';

export interface CmdbBaseEntity {
  id: string;
  code: string;
  name: string;
  description: string | null;
  owner_user_id: string | null;
  lifecycle_status: CmdbLifecycleStatus;
  lifecycle_reason: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CmdbBusiness extends CmdbBaseEntity {}

export interface CmdbApplication extends CmdbBaseEntity {
  business_id: string;
}

export interface CmdbService extends CmdbBaseEntity {
  business_id: string;
  application_id: string;
  service_type: string;
}

export interface CmdbApi extends CmdbBaseEntity {
  business_id: string;
  application_id: string;
  service_id: string;
  http_method: string;
  path: string;
}

export interface CmdbServiceAsset {
  id: string;
  business_id: string;
  application_id: string;
  service_id: string;
  asset_id: string;
  asset_name: string;
  asset_host: string;
  relation_role: string;
  status: CmdbRelationStatus;
  linked_at: string;
  unlinked_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CmdbServiceDependency {
  id: string;
  source_service_id: string;
  source_service_name: string;
  target_service_id: string;
  target_service_name: string;
  dependency_type: string;
  description: string | null;
  status: CmdbDependencyStatus;
  created_at: string;
  updated_at: string;
}

export interface CmdbServiceTree extends CmdbService {
  apis: CmdbApi[];
  assets: CmdbServiceAsset[];
}

export interface CmdbApplicationTree extends CmdbApplication {
  services: CmdbServiceTree[];
}

export interface CmdbBusinessTree extends CmdbBusiness {
  applications: CmdbApplicationTree[];
}

export interface CmdbTreeResponse {
  businesses: CmdbBusinessTree[];
}

export type CmdbEntity = CmdbBusiness | CmdbApplication | CmdbService | CmdbApi;

export interface CmdbEntityRow {
  key: string;
  type: CmdbEntityType;
  entity: CmdbEntity;
  business: CmdbBusinessTree;
  application?: CmdbApplicationTree;
  service?: CmdbServiceTree;
  api?: CmdbApi;
  depth: number;
}

export interface CmdbEntityPayload {
  code: string;
  name: string;
  description?: string;
  owner_user_id?: string | null;
  business_id?: string;
  application_id?: string;
  service_id?: string;
  service_type?: string;
  http_method?: string;
  path?: string;
}
