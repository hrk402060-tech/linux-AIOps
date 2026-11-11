import type { PageKey } from '../App';
import { userDocs } from '../components/user-docs';

const platformBaseHref = () => `${window.location.origin}${window.location.pathname}`;

export function userDocsHref(): string {
  return `${platformBaseHref()}?view=docs`;
}

export function platformPageHref(page?: PageKey): string {
  return page ? `${platformBaseHref()}?${new URLSearchParams({ page })}` : platformBaseHref();
}

export function requestedPlatformPage(): PageKey | null {
  const params = new URLSearchParams(window.location.search);
  if (params.get('view') === 'docs' || params.has('terminal_asset')) return null;
  if (params.get('page') === 'user-security') return 'security-settings';
  return userDocs.find((entry) => entry.page === params.get('page'))?.page ?? null;
}

export function openDocumentedPage(page: PageKey): void {
  const link = document.createElement('a');
  link.href = platformPageHref(page);
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.hidden = true;
  document.body.append(link);
  try {
    link.click();
  } finally {
    link.remove();
  }
}
