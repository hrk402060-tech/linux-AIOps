import { render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { BaselinesPage } from './BaselinesPage';
import { DashboardPage } from './DashboardPage';
import { CmdbPage } from './CmdbPage';
import { ObservabilityPage } from './ObservabilityPage';
import { ReportsPage } from './ReportsPage';
import { VulnerabilitiesPage } from './VulnerabilitiesPage';

afterEach(() => vi.unstubAllGlobals());

const views = [
  { name: '总览', element: <DashboardPage onNavigate={() => undefined} />, shell: '.dashboard-data-shell', error: '总览数据读取失败' },
  { name: '报告', element: <ReportsPage />, shell: '.reports-data-shell', error: '报告数据读取失败' },
  { name: '基线', element: <BaselinesPage />, shell: '.baselines-data-shell', error: '基线数据读取失败' },
  { name: '漏洞', element: <VulnerabilitiesPage />, shell: '.vulnerabilities-data-shell', error: '漏洞数据读取失败' },
  { name: '监控', element: <ObservabilityPage />, shell: '.observability-data-shell', error: '接口暂不可用' },
  { name: '配置项管理', element: <CmdbPage currentUser={{
    id: 'admin', username: 'admin', display_name: '管理员', role: 'admin',
    enabled: true, must_change_password: false, created_at: '', last_login_at: null,
  }} />, shell: '.cmdb-data-shell', error: '配置项数据读取失败' },
];

describe('first-load data truthfulness', () => {
  it.each(views)('$name does not present a failed request as zero or a successful empty result', async (view) => {
    let reject: (reason: Error) => void = () => { throw new Error('Request not initialized'); };
    const pending = new Promise<Response>((_, rejectRequest) => { reject = rejectRequest; });
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(pending));
    const { container } = render(<ConfigProvider theme={{ token: { motion: false } }}>{view.element}</ConfigProvider>);
    const shell = container.querySelector(view.shell);
    expect(shell).toHaveClass('is-loading');
    reject(new Error('接口暂不可用'));
    await screen.findAllByText(view.error);
    expect(shell).toHaveAttribute('hidden');
    expect(shell).not.toBeVisible();
    expect(screen.queryByText('还没有可观测服务')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /重\s*试/ })).toBeEnabled();
  });
});
