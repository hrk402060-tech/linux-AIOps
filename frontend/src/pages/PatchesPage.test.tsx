import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import type { PatchAdvice } from '../api/patches';
import { PatchesPage } from './PatchesPage';

const advice: PatchAdvice = {
  id: 'finding-1', finding_id: 'finding-1', asset_id: 'asset-1', asset_name: '生产数据库',
  cve_id: 'CVE-2026-1234', package_name: 'openssl', current_version: '1.1.1', target_version: '1.1.2',
  severity: 'high', source_name: 'openEuler', vendor_url: null, evidence_chain: [],
  status: 'pending_approval', execution_enabled: false, approval_phrase: 'APPROVE finding-1',
  target_version_relation: 'higher_than_current', target_version_issue: null,
  rollback_plan: '变更前记录当前版本，失败后人工恢复并复扫。',
  history_count: 1, latest_scan_id: 'scan-1', latest_scan_name: '扫描', latest_detected_at: '2026-10-12T01:00:00Z',
};
const response = (body: unknown) => ({ ok: true, json: async () => body });
async function showApproval(overrides: Partial<PatchAdvice> = {}) {
  const fetchMock = vi.fn().mockResolvedValueOnce(response([{ ...advice, ...overrides }]));
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><PatchesPage /></ConfigProvider>);
  fireEvent.click(await screen.findByRole('button', { name: '确认建议' }));
  return { fetchMock, dialog: within(await screen.findByRole('dialog', { name: '人工确认补丁建议' })) };
}
afterEach(() => vi.unstubAllGlobals());
it.each<{ name: string; overrides: Partial<PatchAdvice>; statusText: string; explanation: string }>([
  { name: '无法可靠比较的发行版版本', statusText: '需人工核对', explanation: '发行版版本无法安全比较，请核对厂商公告',
    overrides: { status: 'manual_confirmation', target_version_relation: 'unknown',
      target_version_issue: '发行版版本无法安全比较，请核对厂商公告' } },
  { name: '漏洞证据未确认', statusText: '需人工核对', explanation: '漏洞尚未确认，需人工核对厂商公告与证据',
    overrides: { status: 'manual_confirmation', target_version_issue: '漏洞尚未确认，需人工核对厂商公告与证据' } },
  { name: '相同版本', statusText: '目标版本异常', explanation: '目标版本与当前版本相同，不能作为升级建议',
    overrides: { status: 'invalid_target', target_version: advice.current_version, target_version_relation: 'equal_to_current' } },
  { name: '降级版本', statusText: '目标版本异常', explanation: '目标版本低于当前版本，禁止确认降级建议',
    overrides: { status: 'invalid_target', target_version_relation: 'lower_than_current' } },
  { name: '待审批但比较关系未知', statusText: '需人工核对', explanation: '版本比较证据不足，请人工核对厂商公告',
    overrides: { target_version_relation: 'unknown' } },
  { name: '待审批但比较关系缺失', statusText: '需人工核对', explanation: '版本比较证据不足，请人工核对厂商公告',
    overrides: { target_version_relation: undefined } },
])('blocks $name without an approval request', async ({ overrides, statusText, explanation }) => {
  const fetchMock = vi.fn().mockResolvedValue(response([{ ...advice, ...overrides }]));
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><PatchesPage /></ConfigProvider>);
  const approve = await screen.findByRole('button', { name: '确认建议' });
  expect(approve).toBeDisabled();
  expect(screen.getByText(statusText, { selector: '.ant-tag' })).toBeInTheDocument();
  expect(screen.getByText(explanation)).toBeInTheDocument();
  fireEvent.click(approve);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it('does not offer automatic updates without an eligible recommendation', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => [] }));
  render(<ConfigProvider><PatchesPage /></ConfigProvider>);
  expect(await screen.findByText('补丁与变更中心')).toBeInTheDocument();
  expect(screen.getByText('支持自动更新')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '自动更新' })).not.toBeInTheDocument();
});

async function showExecution() {
  const rpmAdvice: PatchAdvice = {
    ...advice, current_version: '0:1.1.1-1', target_version: '0:1.1.1-2',
    execution_enabled: true, status: 'manual_confirmation', target_version_relation: 'unknown',
  };
  const fetchMock = vi.fn().mockResolvedValueOnce(response([rpmAdvice]));
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><PatchesPage /></ConfigProvider>);
  fireEvent.click(await screen.findByRole('button', { name: '自动更新' }));
  const dialog = within(await screen.findByRole('dialog', { name: '自动更新软件包' }));
  return { rpmAdvice, fetchMock, dialog };
}

it('submits a package update only after a separate explicit confirmation', async () => {
  const { rpmAdvice, dialog, fetchMock } = await showExecution();
  expect(dialog.queryByText(/YUM \/ DNF 仓库/)).not.toBeInTheDocument();
  expect(dialog.getByText(/0:1\.1\.1-2/)).toBeInTheDocument();
  const confirm = dialog.getByRole('button', { name: '确认并更新' });
  fireEvent.change(dialog.getByLabelText('更新原因'), { target: { value: '  安全修复  ' } });
  fireEvent.change(dialog.getByLabelText('确认文本'), { target: { value: '确认补丁建议' } });
  expect(confirm).toBeDisabled();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fireEvent.change(dialog.getByLabelText('确认文本'), { target: { value: '  确认更新软件包  ' } });
  expect(confirm).toBeEnabled();
  const updated = { ...rpmAdvice, execution_enabled: false, status: 'pending_verification' };
  fetchMock.mockResolvedValueOnce(response(updated)).mockResolvedValueOnce(response([updated]));
  fireEvent.click(confirm); fireEvent.click(confirm);
  await waitFor(() => expect(screen.getByText('待复扫验证', { selector: '.ant-tag' })).toBeInTheDocument());
  expect(fetchMock).toHaveBeenCalledTimes(3);
  expect(fetchMock.mock.calls[1]).toEqual(['/api/v1/patches/finding-1/execute', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ reason: '安全修复', confirmation: '确认更新软件包' }),
  })]);
  expect(screen.queryByRole('button', { name: '自动更新' })).not.toBeInTheDocument();
}, 15_000);

it('reloads the advice after an update failure without automatically resubmitting', async () => {
  const { rpmAdvice, dialog, fetchMock } = await showExecution();
  fetchMock.mockResolvedValueOnce({ ok: false, json: async () => ({ detail: '更新结果未确认，请复扫' }) })
    .mockResolvedValueOnce(response([{ ...rpmAdvice, execution_enabled: false, status: 'pending_verification' }]));
  fireEvent.change(dialog.getByLabelText('确认文本'), { target: { value: '确认更新软件包' } });
  const confirm = dialog.getByRole('button', { name: '确认并更新' });
  expect(confirm).toBeDisabled();
  fireEvent.change(dialog.getByLabelText('更新原因'), { target: { value: '维护窗口修复' } });
  fireEvent.click(confirm);
  expect(await screen.findByText('更新结果未确认，请复扫')).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText('待复扫验证', { selector: '.ant-tag' })).toBeInTheDocument());
  expect(fetchMock).toHaveBeenCalledTimes(3);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
}, 15_000);
it('shows Chinese severity, target and exact human confirmation without displaying a machine token', async () => {
  const { dialog, fetchMock } = await showApproval();
  expect(screen.getByText('高危', { selector: '.ant-tag' })).toBeInTheDocument();
  expect(dialog.getByText('确认补丁建议', { selector: 'strong' })).toBeInTheDocument();
  expect(dialog.getByText(advice.asset_name)).toBeInTheDocument();
  expect(dialog.getByText(advice.cve_id)).toBeInTheDocument();
  expect(dialog.getByText(advice.package_name)).toBeInTheDocument();
  expect(dialog.queryByText(advice.approval_phrase)).not.toBeInTheDocument();
  expect(dialog.getByText('审批原因需 2 至 1000 个字符，首尾空格不计入。')).toBeInTheDocument();
  const confirm = dialog.getByRole('button', { name: '确认并等待验证' });
  expect(confirm).toBeDisabled();
  fireEvent.change(dialog.getByLabelText('确认文本'), { target: { value: 'APPROVE finding-1' } });
  expect(confirm).toBeDisabled();
  expect(dialog.getByText('确认文本不匹配，请输入“确认补丁建议”。')).toBeInTheDocument();
  fireEvent.change(dialog.getByLabelText('确认文本'), { target: { value: '确认补丁建议' } });
  fireEvent.change(dialog.getByLabelText('审批原因'), { target: { value: ' 1 ' } });
  expect(confirm).toBeDisabled();
  expect(dialog.getByText('审批原因不足 2 个字符，请补充说明。')).toBeInTheDocument();
  expect(dialog.getByLabelText('审批原因')).toHaveAttribute('aria-invalid', 'true');
  fireEvent.change(dialog.getByLabelText('审批原因'), { target: { value: 'x'.repeat(1001) } });
  expect(confirm).toBeDisabled();
  expect(dialog.getByText('审批原因不能超过 1000 个字符。')).toBeInTheDocument();
  fireEvent.change(dialog.getByLabelText('审批原因'), { target: { value: '  变更已经完成  ' } });
  fireEvent.change(dialog.getByLabelText('确认文本'), { target: { value: '  确认补丁建议  ' } });
  expect(confirm).toBeEnabled();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fetchMock.mockResolvedValueOnce(response({ ...advice, status: 'pending_verification' }))
    .mockResolvedValueOnce(response([{ ...advice, status: 'pending_verification' }]));
  fireEvent.click(confirm);
  await waitFor(() => expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/patches/finding-1/approve'));
  expect(fetchMock.mock.calls[1][1]).toEqual(expect.objectContaining({
    method: 'POST', body: JSON.stringify({ reason: '变更已经完成', confirmation: 'APPROVE finding-1' }),
  }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(screen.queryByRole('button', { name: '自动更新' })).not.toBeInTheDocument();
}, 15_000);
it('requires fresh confirmation after closing and reopening', async () => {
  const { dialog, fetchMock } = await showApproval();
  fireEvent.change(dialog.getByLabelText('确认文本'), { target: { value: '确认补丁建议' } });
  expect(dialog.getByRole('button', { name: '确认并等待验证' })).toBeEnabled();
  fireEvent.click(dialog.getByRole('button', { name: /取\s*消/ }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: '确认建议' }));
  const reopened = within(await screen.findByRole('dialog')); 
  expect(reopened.getByLabelText('确认文本')).toHaveValue('');
  expect(reopened.getByRole('button', { name: '确认并等待验证' })).toBeDisabled();
  expect(fetchMock).toHaveBeenCalledTimes(1);
}, 15_000);
it.each(['', 'APPROVE different-finding'])('rejects an invalid server approval phrase %s', async (approval_phrase) => {
  const { dialog, fetchMock } = await showApproval({ approval_phrase });
  fireEvent.change(dialog.getByLabelText('确认文本'), { target: { value: '确认补丁建议' } });
  expect(dialog.getByRole('button', { name: '确认并等待验证' })).toBeDisabled();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
it('does not submit twice while human approval is pending', async () => {
  const { dialog, fetchMock } = await showApproval();
  let finish: (value: ReturnType<typeof response>) => void = () => { throw new Error('Missing request'); };
  fetchMock.mockReturnValueOnce(new Promise<ReturnType<typeof response>>((resolve) => { finish = resolve; }))
    .mockResolvedValueOnce(response([{ ...advice, status: 'pending_verification' }]));
  fireEvent.change(dialog.getByLabelText('确认文本'), { target: { value: '确认补丁建议' } });
  const confirm = dialog.getByRole('button', { name: '确认并等待验证' });
  fireEvent.click(confirm); fireEvent.click(confirm);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  await act(async () => { finish(response({ ...advice, status: 'pending_verification' })); });
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});
