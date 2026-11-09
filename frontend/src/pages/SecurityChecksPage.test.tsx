import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SecurityChecksPage } from './SecurityChecksPage';
import { securityCheckTabs, type SecurityCheckPageKey } from '../types/security-check';

const endpoints: Record<SecurityCheckPageKey, string> = {
  schedules: '/api/v1/scan-schedules', baselines: '/api/v1/baselines',
  exposures: '/api/v1/exposures', 'credential-risks': '/api/v1/credential-risks',
};
const buttons: Record<SecurityCheckPageKey, string> = {
  schedules: '新建计划', baselines: '新建检查', exposures: '新建检测', 'credential-risks': '新建评估',
};
function show(activeKey: SecurityCheckPageKey = 'schedules') {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [] });
  vi.stubGlobal('fetch', fetchMock);
  function Harness() {
    const [page, setPage] = useState(activeKey);
    return <ConfigProvider theme={{ token: { motion: false } }}>
      <SecurityChecksPage activeKey={page} onNavigate={setPage} />
    </ConfigProvider>;
  }
  return { ...render(<Harness />), fetchMock };
}
afterEach(() => vi.unstubAllGlobals());

describe('SecurityChecksPage', () => {
  it.each(securityCheckTabs)('opens the existing $key page with one heading and only active requests', async ({ key, label }) => {
    const { fetchMock } = show(key);
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.getByRole('heading', { name: '安全检查' })).toBeInTheDocument();
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(securityCheckTabs.map((tab) => tab.label));
    expect(screen.getByRole('tab', { name: label })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: buttons[key] })).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(endpoints[key], expect.any(Object)));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const paths = fetchMock.mock.calls.map(([path]) => path as string);
    expect(paths.filter((path) => Object.values(endpoints).includes(path))).toEqual([endpoints[key]]);
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
  });
  it('switches tabs, destroys old dialogs and clears unsaved forms without writing', async () => {
    const { fetchMock } = show();
    fireEvent.click(screen.getByRole('button', { name: '新建计划' }));
    const dialog = within(await screen.findByRole('dialog', { name: '新建扫描计划' }));
    fireEvent.change(dialog.getByLabelText('计划名称'), { target: { value: '未保存计划' } });
    fireEvent.click(screen.getByRole('tab', { name: '基线合规' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: '新建检查' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '新建计划' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '定时扫描' }));
    fireEvent.click(screen.getByRole('button', { name: '新建计划' }));
    expect(within(await screen.findByRole('dialog')).getByLabelText('计划名称')).toHaveValue('');
    expect(fetchMock.mock.calls.filter(([path]) => path === endpoints.schedules)).toHaveLength(2);
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
  });
  it('navigates with the original PageKey when a tab is clicked', () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => [] }));
    const onNavigate = vi.fn();
    render(<ConfigProvider><SecurityChecksPage activeKey="schedules" onNavigate={onNavigate} /></ConfigProvider>);
    fireEvent.click(screen.getByRole('tab', { name: '暴露面检测' }));
    expect(onNavigate).toHaveBeenCalledWith('exposures');
  });
});
