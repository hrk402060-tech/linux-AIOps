import type { PageKey } from '../../App';

export type UserDocGroup = 'workspace' | 'monitoring' | 'security' | 'system';
export interface UserDocCommand {
  title: string;
  text: string;
}
export interface UserDocEntry {
  id: string;
  group: UserDocGroup;
  title: string;
  page: PageKey;
  purpose: string;
  requirements: string[];
  steps: string[];
  notes: string[];
  commands?: UserDocCommand[];
}
