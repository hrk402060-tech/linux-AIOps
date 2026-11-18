import { act, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { PageKey } from '../App';
import { getPlatformPreferences } from '../api/system';
import { applyPlatformPreferences, defaultPlatformPreferences, formatPlatformDateTime } from '../utils/platformPreferences';
import { usePlatformPreferences } from './usePlatformPreferences';

vi.mock('../api/system', () => ({ getPlatformPreferences: vi.fn() }));
function Harness() {
  const [page, setPage] = useState<PageKey>('dashboard');
  const platform = usePlatformPreferences('preference-user', page, setPage);
  return <><output aria-label="当前页面">{page}</output><output aria-label="平台名称">{platform.platform_name}</output></>;
}
beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  vi.mocked(getPlatformPreferences).mockResolvedValue({
    ...defaultPlatformPreferences, platform_name: '运维平台', default_page: 'server-monitoring',
  });
});
afterEach(() => {
  applyPlatformPreferences(defaultPlatformPreferences);
  window.history.replaceState({}, '', '/');
});
it('applies the saved default only when there is no existing page selection', async () => {
  render(<Harness />);
  await waitFor(() => expect(screen.getByLabelText('当前页面')).toHaveTextContent('server-monitoring'));
  expect(document.title).toBe('运维平台');
});
it.each(['memory', 'link'])('does not replace an existing %s page selection', async (source) => {
  if (source === 'memory') localStorage.setItem('sentinel_navigation:preference-user', JSON.stringify({ activePage: 'assets' }));
  else window.history.replaceState({}, '', '/?page=assets');
  render(<Harness />);
  await screen.findByText('运维平台');
  expect(screen.getByLabelText('当前页面')).toHaveTextContent('dashboard');
});
it('refreshes name, density and timezone immediately after a saved settings event', async () => {
  render(<Harness />);
  await screen.findByText('运维平台');
  vi.mocked(getPlatformPreferences).mockResolvedValue({
    ...defaultPlatformPreferences, platform_name: '正式运维平台', timezone: 'UTC', display_density: 'compact',
  });
  act(() => window.dispatchEvent(new Event('sentinel:preferences-changed')));
  await screen.findByText('正式运维平台');
  expect(document.title).toBe('正式运维平台');
  expect(document.documentElement.dataset.density).toBe('compact');
  expect(document.documentElement.dataset.timezone).toBe('UTC');
  expect(formatPlatformDateTime('2026-11-18T00:00:00Z')).toBe(
    new Date('2026-11-18T00:00:00Z').toLocaleString('zh-CN', { timeZone: 'UTC' }),
  );
});
