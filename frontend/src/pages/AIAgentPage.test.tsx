import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { getAgentWorkspace } from '../api/ai-agent';
import type { AuthUser } from '../api/auth';
import type { AgentFinding, AgentRecommendation, AgentWorkspace } from '../types/ai-agent';
import { AIAgentPage } from './AIAgentPage';

const admin: AuthUser = {
  id: 'admin-1', username: 'security-admin', display_name: '管理员', role: 'admin',
  enabled: true, must_change_password: false, created_at: '', last_login_at: null,
};
const finding: AgentFinding = {
  id: 'finding-agent-1', asset_id: 'asset-1', disposition_status: 'open',
  cve_id: 'CVE-2026-3001', title: 'Kernel privilege boundary issue',
  asset_name: 'openEuler gateway', severity: 'critical', conclusion: 'confirmed',
  confidence: 'high', package_name: 'kernel', installed_version: '5.10.0-1',
  fixed_version: '5.10.0-2', source_name: 'openEuler Security Advisories',
};
const recommendation: AgentRecommendation = {
  id: 'advice-1', finding_id: finding.id, cve_id: finding.cve_id,
  asset_name: finding.asset_name, provider_id: 'local-rules', provider_name: '本地研判引擎',
  status: 'pending_review', priority: 'urgent', risk_analysis: '严重漏洞，应优先处理。',
  remediation_steps: ['在维护窗口升级 kernel 至 5.10.0-2', '重启后确认版本'],
  validation_steps: ['重新执行只读扫描'], rollback_plan: '使用变更系统准备的回滚方案。',
  evidence_snapshot: [{ type: 'source', value: finding.source_name }],
  created_at: '2026-09-04T10:00:00Z', reviewed_at: null, reviewed_by: null, can_execute: false,
};
const workspace: AgentWorkspace = {
  providers: [
    { id: 'local-rules', name: '本地研判引擎', kind: 'local', status: 'ready', requires_api_key: false },
    { id: 'external-1', name: '外部提供商', kind: 'external', status: 'disabled', requires_api_key: true, configured: true, enabled: false, api_key_masked: '****1234' },
  ],
  summary: { pending_findings: 1, pending_review: 0, approved: 0, urgent_alerts: 1 },
  findings: [finding], recommendations: [], queue_total: 1, queue_page: 1,
  history_total: 0, history_page: 1,
};
const response = (body: unknown, status = 200): Response => ({
  ok: status >= 200 && status < 300, status, json: async () => body,
}) as Response;
function stubApi(initial: AgentWorkspace = workspace) {
  let current = initial;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname === '/api/v1/agent' && !init?.method) return response(current);
    if (url.pathname.endsWith('/providers/config')) return response(current.providers);
    throw new Error(`Unexpected request ${init?.method ?? 'GET'} ${url.pathname}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return { fetchMock, setWorkspace: (value: AgentWorkspace) => { current = value; } };
}
function renderPage(role: AuthUser['role'] = 'admin') {
  return render(<ConfigProvider theme={{ token: { motion: false } }}>
    <AIAgentPage currentUser={{ ...admin, role }} />
  </ConfigProvider>);
}
function expectMetric(label: string, value: number) {
  expect(screen.getByText(label, { selector: '.agent-metrics span' }).parentElement).toHaveTextContent(String(value));
}
function deferredResponse() {
  let resolve: (value: Response) => void = () => undefined;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => {
  sessionStorage.setItem('sentinel_admin_token', 'test-token');
  stubApi();
});

describe('AIAgentPage', () => {
  it.each(['admin', 'operator', 'auditor'] as const)('keeps %s vulnerability analysis separate from administrator model management', async (role) => {
    const { fetchMock } = stubApi();
    renderPage(role);
    await screen.findAllByText(finding.cve_id);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: '配置 API Key' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '配置审计' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '启用' })).not.toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '推理引擎' })).toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([input]) => !String(input).includes('/providers/config'))).toBe(true);
  });

  it('shows Chinese severity while keeping the analysis workflow focused', async () => {
    renderPage();
    await screen.findAllByText(finding.cve_id);
    expect(screen.getByText('严重')).toHaveClass('ant-tag-red');
    expect(screen.queryByText('数据不出域')).not.toBeInTheDocument();
    expect(screen.queryByText('AI 不会直接执行修复')).not.toBeInTheDocument();
    expect(screen.getByText('研判队列')).toBeInTheDocument();
    expect(screen.getByText('研判与修复建议')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '执行修复' })).not.toBeInTheDocument();
  });

  it('serializes queue and history server pagination', async () => {
    const { fetchMock } = stubApi();
    await getAgentWorkspace({ queuePage: 2, queuePageSize: 20, historyPage: 3, historyPageSize: 10 });
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/v1/agent?queue_page=2&queue_page_size=20&history_page=3&history_page_size=10', expect.anything(),
    );
  });

  it('uses authoritative counts after triage and review without executing', async () => {
    const { fetchMock, setWorkspace } = stubApi();
    renderPage();
    await screen.findAllByText(finding.cve_id);
    setWorkspace({ ...workspace, recommendations: [recommendation], history_total: 1,
      summary: { pending_findings: 8, pending_review: 7, approved: 4, urgent_alerts: 9 } });
    fetchMock.mockResolvedValueOnce(response(recommendation));
    fireEvent.click(screen.getByRole('button', { name: '生成研判建议' }));
    expect(await screen.findByText(recommendation.risk_analysis)).toBeInTheDocument();
    expectMetric('待研判漏洞', 8);
    expectMetric('待人工审核', 7);
    const reviewed: AgentRecommendation = { ...recommendation, status: 'approved',
      reviewed_at: '2026-09-04T10:05:00Z', reviewed_by: 'security-admin' };
    setWorkspace({ ...workspace, recommendations: [reviewed], history_total: 1,
      summary: { pending_findings: 6, pending_review: 2, approved: 11, urgent_alerts: 3 } });
    fetchMock.mockResolvedValueOnce(response(reviewed));
    fireEvent.click(screen.getByRole('button', { name: '人工确认建议' }));
    expect(await screen.findByText('security-admin')).toBeInTheDocument();
    expect(screen.getByText(new Date(reviewed.reviewed_at as string).toLocaleString('zh-CN'))).toBeInTheDocument();
    expectMetric('待人工审核', 2);
    expectMetric('已确认建议', 11);
    expect(screen.queryByRole('button', { name: '人工确认建议' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '执行修复' })).not.toBeInTheDocument();
    const writes = fetchMock.mock.calls.filter(([, init]) => init?.method && init.method !== 'GET');
    expect(writes).toHaveLength(2);
    expect(writes[1][0]).toBe('/api/v1/agent/recommendations/advice-1/review');
    expect(JSON.parse(writes[1][1]?.body as string)).toEqual({ decision: 'approved' });
  });

  it('selects full global historical advice and reports unknown legacy reviewer', async () => {
    const oldAdvice: AgentRecommendation = { ...recommendation, id: 'advice-old', finding_id: 'old-finding',
      cve_id: 'CVE-2025-9001', status: 'rejected', reviewed_at: '2026-09-04T10:05:00Z',
      reviewed_by: null, risk_analysis: '旧扫描建议证据' };
    const { fetchMock, setWorkspace } = stubApi({ ...workspace,
      recommendations: [recommendation], history_total: 51 });
    renderPage();
    await screen.findByText(recommendation.risk_analysis);
    expect(screen.getByText('全局建议历史')).toBeInTheDocument();
    setWorkspace({ ...workspace, recommendations: [oldAdvice], history_total: 51, history_page: 2 });
    fireEvent.click(within(screen.getByRole('navigation', { name: '建议历史分页' })).getByTitle('2'));
    expect(await screen.findByText(oldAdvice.risk_analysis)).toBeInTheDocument();
    expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('history_page=2');
    expect(screen.getByText('历史记录未记载')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '人工确认建议' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成研判建议' })).toBeDisabled();
  });

  it('switches historical recommendations by ID without assuming loaded history is complete', async () => {
    const second = { ...recommendation, id: 'advice-2', risk_analysis: '另一条人工研判建议' };
    stubApi({ ...workspace, recommendations: [recommendation, second], history_total: 70 });
    renderPage();
    await screen.findByText(recommendation.risk_analysis);
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '全局建议历史' }));
    fireEvent.click(await screen.findByText(new RegExp('advice-2$'), { selector: '.ant-select-item-option-content' }));
    expect(await screen.findByText(second.risk_analysis)).toBeInTheDocument();
    expect(screen.getByText('本页 2 / 共 70 条')).toBeInTheDocument();
  });

  it('removes stale queue and recommendation selections after refresh', async () => {
    const { setWorkspace } = stubApi({ ...workspace, recommendations: [recommendation], history_total: 1 });
    renderPage();
    await screen.findByText(recommendation.risk_analysis);
    setWorkspace({ ...workspace, findings: [], recommendations: [], queue_total: 0 });
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    expect(await screen.findByText('暂无待研判漏洞')).toBeInTheDocument();
    expect(screen.queryByText(recommendation.risk_analysis)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成研判建议' })).toBeDisabled();
  });

  it('pages the authoritative queue and drops a finding absent from the new page', async () => {
    const secondFinding = { ...finding, id: 'finding-agent-2', cve_id: 'CVE-2026-3002' };
    const { fetchMock, setWorkspace } = stubApi({ ...workspace, queue_total: 51 });
    renderPage();
    await screen.findAllByText(finding.cve_id);
    setWorkspace({ ...workspace, findings: [secondFinding], queue_total: 51, queue_page: 2 });
    fireEvent.click(within(screen.getByRole('navigation', { name: '研判队列分页' })).getByTitle('2'));
    expect(await screen.findAllByText(secondFinding.cve_id)).not.toHaveLength(0);
    expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('queue_page=2');
    expect(screen.queryByText(finding.cve_id)).not.toBeInTheDocument();
    expect(screen.getByText('本页 1 / 共 51 项')).toBeInTheDocument();
  });

  it('refreshes authoritative state after stale triage rejection', async () => {
    const { fetchMock, setWorkspace } = stubApi();
    renderPage();
    await screen.findAllByText(finding.cve_id);
    setWorkspace({ ...workspace, findings: [], queue_total: 0,
      summary: { ...workspace.summary, pending_findings: 0, urgent_alerts: 0 } });
    fetchMock.mockResolvedValueOnce(response({ detail: '漏洞证据已失效' }, 409));
    fireEvent.click(screen.getByRole('button', { name: '生成研判建议' }));
    expect(await screen.findByText('漏洞证据已失效')).toBeInTheDocument();
    expect(await screen.findByText('暂无待研判漏洞')).toBeInTheDocument();
    expectMetric('待研判漏洞', 0);
    expect(screen.getByRole('button', { name: '生成研判建议' })).toBeDisabled();
  });

  it('preserves selection only while it belongs to the refreshed server arrays', async () => {
    const { setWorkspace } = stubApi({ ...workspace, recommendations: [recommendation], history_total: 1 });
    renderPage();
    await screen.findByText(recommendation.risk_analysis);
    const updated = { ...recommendation, risk_analysis: '服务端更新后的研判证据' };
    setWorkspace({ ...workspace, recommendations: [updated], history_total: 1 });
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    expect(await screen.findByText(updated.risk_analysis)).toBeInTheDocument();
    expect(screen.queryByText(recommendation.risk_analysis)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '人工确认建议' })).toBeEnabled();
  });

  it('refreshes a concurrent review conflict and never overwrites the authoritative reviewer', async () => {
    const reviewed: AgentRecommendation = { ...recommendation, status: 'rejected',
      reviewed_at: '2026-09-04T10:05:00Z', reviewed_by: 'other-operator' };
    const { fetchMock, setWorkspace } = stubApi({ ...workspace, recommendations: [recommendation], history_total: 1 });
    renderPage();
    await screen.findByText(recommendation.risk_analysis);
    setWorkspace({ ...workspace, recommendations: [reviewed], history_total: 1 });
    fetchMock.mockResolvedValueOnce(response({ detail: '建议已被其他操作人审核' }, 409));
    fireEvent.click(screen.getByRole('button', { name: '人工确认建议' }));
    expect(await screen.findByText('other-operator')).toBeInTheDocument();
    expect(screen.getByText('建议已被其他操作人审核')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '人工确认建议' })).not.toBeInTheDocument();
  });

  it('ignores late refresh results and keeps the latest server workspace', async () => {
    const { fetchMock } = stubApi();
    const older = deferredResponse();
    const newer = deferredResponse();
    fetchMock.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    newer.resolve(response({ ...workspace, findings: [{ ...finding, title: '最新工作区证据' }],
      summary: { ...workspace.summary, approved: 42 } }));
    expect(await screen.findByText('最新工作区证据')).toBeInTheDocument();
    older.resolve(response({ ...workspace, findings: [{ ...finding, title: '过时工作区证据' }],
      summary: { ...workspace.summary, approved: 1 } }));
    await waitFor(() => expectMetric('已确认建议', 42));
    expect(screen.queryByText('过时工作区证据')).not.toBeInTheDocument();
  });

  it('prevents repeated review submission while the first request is pending', async () => {
    const { fetchMock, setWorkspace } = stubApi({ ...workspace, recommendations: [recommendation], history_total: 1 });
    renderPage();
    await screen.findByText(recommendation.risk_analysis);
    const pending = deferredResponse();
    fetchMock.mockReturnValueOnce(pending.promise);
    const button = screen.getByRole('button', { name: '人工确认建议' });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1);
    const reviewed: AgentRecommendation = { ...recommendation, status: 'approved', reviewed_by: 'security-admin', reviewed_at: '2026-09-04T10:05:00Z' };
    setWorkspace({ ...workspace, recommendations: [reviewed], history_total: 1 });
    pending.resolve(response(reviewed));
    expect(await screen.findByText('security-admin')).toBeInTheDocument();
  });

  it.each(['admin', 'operator'] as const)('allows %s human triage/review, never execution', async (role) => {
    stubApi({ ...workspace, recommendations: [recommendation], history_total: 1 });
    renderPage(role);
    await screen.findByText(recommendation.risk_analysis);
    expect(screen.getByRole('button', { name: '生成研判建议' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '人工确认建议' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '驳回' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: '执行修复' })).not.toBeInTheDocument();
    if (role === 'operator') expect(screen.queryByRole('button', { name: '配置 API Key' })).not.toBeInTheDocument();
  });

  it('keeps auditor read-only including provider mutations', async () => {
    stubApi({ ...workspace, recommendations: [recommendation], history_total: 1 });
    renderPage('auditor');
    await screen.findByText(recommendation.risk_analysis);
    expect(screen.queryByRole('button', { name: '生成研判建议' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '人工确认建议' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '驳回' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '配置 API Key' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '启用' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '配置审计' })).not.toBeInTheDocument();
  });
});
