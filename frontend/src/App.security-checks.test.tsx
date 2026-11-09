import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App, { type PageKey } from './App';
import { getCurrentUser } from './api/auth';
import { securityCheckTabs } from './types/security-check';

vi.mock('./api/auth', () => ({ getCurrentUser: vi.fn(), logout: vi.fn() }));
vi.mock('./pages/DashboardPage', () => ({ DashboardPage: () => <div>总览内容</div> }));
vi.mock('./pages/SecurityChecksPage', () => ({
  SecurityChecksPage: ({ activeKey }: { activeKey: PageKey }) => <output aria-label="安全检查当前页签">{activeKey}</output>,
}));
vi.mock('./components/AppShell', () => ({
  AppShell: ({ children, onNavigate }: { children: ReactNode; onNavigate: (page: PageKey) => void }) => <>
    {securityCheckTabs.map(({ key, label }) => <button key={key} onClick={() => onNavigate(key)}>{label}</button>)}
    {children}
  </>,
}));
beforeEach(() => vi.mocked(getCurrentUser).mockResolvedValue({
  id: 'user-1', username: 'admin', display_name: '管理员', role: 'admin', enabled: true,
  must_change_password: false, created_at: '', last_login_at: null,
}));
describe('App security check integration', () => {
  it.each(securityCheckTabs)('keeps the old $key navigation and opens the correct unified tab', async ({ key, label }) => {
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: label }));
    expect(screen.getByRole('status', { name: '安全检查当前页签' })).toHaveTextContent(key);
    expect(screen.queryByText('总览内容')).not.toBeInTheDocument();
  });
});
