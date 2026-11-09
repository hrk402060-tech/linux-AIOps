import { fireEvent, render, screen, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, expect, it, vi } from 'vitest';
import { AutomationPage } from './AutomationPage';
import { securityCheckTabs } from '../types/security-check';

beforeEach(() => { vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => ({ ok: true, json: async () => [] }))); });
it('summarizes automation modules and read-only boundary', async () => { render(<ConfigProvider><AutomationPage onNavigate={vi.fn()} /></ConfigProvider>); expect(await screen.findByText('自动化与变更')).toBeInTheDocument(); expect(screen.getByText('默认只读与人工确认')).toBeInTheDocument(); expect(screen.getByText('补丁与变更')).toBeInTheDocument(); });
it.each(securityCheckTabs)('keeps the automation shortcut to $key', async ({ key, label }) => {
  const onNavigate = vi.fn();
  render(<ConfigProvider><AutomationPage onNavigate={onNavigate} /></ConfigProvider>);
  const title = await screen.findByText(key === 'credential-risks' ? '弱口令风险' : label);
  const card = title.closest('.ant-card');
  expect(card).not.toBeNull();
  fireEvent.click(within(card as HTMLElement).getByRole('button', { name: '进入模块' }));
  expect(onNavigate).toHaveBeenCalledWith(key);
});
