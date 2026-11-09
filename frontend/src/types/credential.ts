export type CredentialType = 'password' | 'private_key';

export interface Credential {
  id: string;
  name: string;
  credential_type: CredentialType;
  username: string;
  secret_mask: string;
  key_version: string;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  asset_count: number;
}

export interface CredentialCreate {
  name: string;
  credential_type: CredentialType;
  username: string;
  secret: string;
}

export interface CredentialUpdate {
  name?: string;
  username?: string;
  secret?: string;
}
