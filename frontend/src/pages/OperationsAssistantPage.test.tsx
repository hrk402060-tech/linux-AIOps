import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { OperationsAssistantPage } from './OperationsAssistantPage';
import { answer, conversation, installConversationApi, response, turn } from './operations-assistant.test-support';

beforeEach(() => installConversationApi());

const mount = (userId = 'user-1') => render(<ConfigProvider><OperationsAssistantPage userId={userId} /></ConfigProvider>);
const send = async (prompt: string) => {
  fireEvent.change(screen.getByLabelText('向 AI 运维助手提问'), { target: { value: prompt } });
  await userEvent.click(screen.getByRole('button', { name: '发送' }));
};

describe('OperationsAssistantPage persisted conversations', () => {
  it('service_health: starts an asset-bound draft without restoring an unrelated conversation or sending', async () => {
    installConversationApi([conversation('existing', '旧服务器会话', [turn('u1', '旧服务器问题')])]);
    const original = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((url, init) => String(url).includes('/assets')
      ? Promise.resolve(response([{ id: 'asset-health', name: '订单服务器', host: '192.0.2.40', lifecycle_status: 'enabled' }]))
      : original(url, init));
    render(<ConfigProvider><OperationsAssistantPage userId="user-1" initialAssetId="asset-health"
      initialContext="订单 API：健康检查失败" /></ConfigProvider>);
    await waitFor(() => expect(screen.getByLabelText('补充只读证据')).toHaveValue('订单 API：健康检查失败'));
    expect(screen.getByLabelText('补充只读证据')).toBeEnabled();
    expect(screen.queryByText('旧服务器问题')).not.toBeInTheDocument();
    expect(screen.getByText('订单服务器 · 192.0.2.40')).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
  });
  it('restores the latest server conversation and its messages after remount', async () => {
    installConversationApi([conversation('recent', '负载排查', [turn('u1', '历史问题')])]);
    const page = mount();
    expect(await screen.findByText('历史问题')).toBeInTheDocument();
    expect(screen.queryByText('只读分析')).not.toBeInTheDocument();
    expect(screen.queryByText('人工确认边界')).not.toBeInTheDocument();
    expect(screen.getByLabelText('补充只读证据')).toBeDisabled();
    page.unmount();
    mount();
    expect(await screen.findByText('历史问题')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /打开会话 负载排查/ })).toHaveAttribute('aria-pressed', 'true');
  });

  it('creates a draft with evidence once, then sends only server conversation id and question', async () => {
    mount();
    await screen.findByText('暂无会话');
    fireEvent.change(screen.getByLabelText('补充只读证据'), { target: { value: 'load average=4.2' } });
    await send('如何排查负载？');
    expect(await screen.findByText(answer.reply)).toBeInTheDocument();
    await send('继续定位');
    await waitFor(() => expect(within(screen.getByRole('log')).getAllByText(answer.reply)).toHaveLength(2));
    const calls = vi.mocked(fetch).mock.calls;
    const create = calls.find(([url, init]) => String(url).endsWith('/conversations') && init?.method === 'POST');
    expect(JSON.parse(String(create?.[1]?.body))).toEqual({ asset_id: null, context: 'load average=4.2' });
    const chats = calls.filter(([url]) => String(url).endsWith('/chat'));
    expect(chats.map(([, init]) => JSON.parse(String(init?.body)))).toEqual([
      { conversation_id: 'created-1', message: '如何排查负载？', request_id: expect.any(String) },
      { conversation_id: 'created-1', message: '继续定位', request_id: expect.any(String) },
    ]);
    expect(screen.queryByRole('button', { name: /执行|批准/ })).not.toBeInTheDocument();
    expect(screen.getAllByText('uptime')).toHaveLength(2);
  });

  it('starts a clean new draft without deleting previous conversations', async () => {
    installConversationApi([conversation('existing', '历史会话', [turn('u1', '保留的问题')])]);
    mount();
    await screen.findByText('保留的问题');
    await userEvent.click(screen.getByRole('button', { name: '新建会话' }));
    expect(screen.queryByText('保留的问题')).not.toBeInTheDocument();
    expect(screen.getByLabelText('补充只读证据')).toBeEnabled();
    expect(screen.getByRole('button', { name: /打开会话 历史会话/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /打开会话 历史会话/ }));
    expect(await screen.findByText('保留的问题')).toBeInTheDocument();
  });

  it('keeps failed messages from the server and the retry draft when model fails', async () => {
    const api = installConversationApi();
    api.failChat = true;
    mount();
    await screen.findByText('暂无会话');
    await send('排查内存');
    expect(await screen.findByText('回答失败：模型服务暂时不可用')).toBeInTheDocument();
    expect(within(screen.getByRole('log')).getByText('排查内存')).toBeInTheDocument();
    expect(screen.getByLabelText('向 AI 运维助手提问')).toHaveValue('排查内存');
    api.failChat = false;
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByText(answer.reply);
    const chats = vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith('/chat'));
    expect(JSON.parse(String(chats[0][1]?.body)).request_id).not.toBe(JSON.parse(String(chats[1][1]?.body)).request_id);
  });

  it('renames and archives a conversation, then restores it from archived history', async () => {
    installConversationApi([conversation('existing', '旧标题', [turn('u1', '历史问题')])]);
    mount();
    await screen.findByText('历史问题');
    await userEvent.click(screen.getByRole('button', { name: '重命名会话' }));
    fireEvent.change(screen.getByLabelText('会话标题'), { target: { value: '新的会话标题' } });
    await userEvent.click(screen.getByRole('button', { name: '保存名称' }));
    expect(await screen.findByRole('button', { name: /打开会话 新的会话标题/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '归档会话' }));
    await userEvent.click(screen.getByRole('button', { name: '已归档' }));
    expect(screen.getByLabelText('向 AI 运维助手提问')).toBeDisabled();
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled();
    await userEvent.click(await screen.findByRole('button', { name: /打开会话 新的会话标题/ }));
    await screen.findByText('历史问题');
    expect(screen.getByLabelText('向 AI 运维助手提问')).toBeDisabled();
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: '恢复会话' }));
    expect(await screen.findByRole('button', { name: /打开会话 新的会话标题/ })).toBeInTheDocument();
    expect(screen.getByLabelText('向 AI 运维助手提问')).toBeEnabled();
  });

  it('loads older pages while retaining message order', async () => {
    const messages = Array.from({ length: 102 }, (_, index) => turn(`u${index}`, `第 ${index + 1} 条历史`));
    installConversationApi([conversation('long', '长会话', messages)]);
    mount();
    await screen.findByText('第 102 条历史');
    expect(screen.queryByText('第 1 条历史')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '加载更早消息' }));
    expect(await screen.findByText('第 1 条历史')).toBeInTheDocument();
    const articles = within(screen.getByRole('log')).getAllByRole('article');
    expect(articles[0]).toHaveTextContent('第 1 条历史');
    expect(articles.at(-1)).toHaveTextContent('第 102 条历史');
  });

  it('shows list failures with retry and does not expose stale history', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => String(url).includes('/assets')
      ? response([]) : response({ detail: '会话列表暂不可用' }, 503)));
    mount();
    expect(await screen.findByText('会话列表暂不可用')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试加载会话' })).toBeInTheDocument();
    expect(screen.queryByText('历史问题')).not.toBeInTheDocument();
  });

  it('ignores a late chat result after switching to a new draft', async () => {
    const api = installConversationApi();
    let finish: ((value: Response) => void) | undefined;
    api.chatPromise = new Promise<Response>((resolve) => { finish = resolve; });
    mount();
    await screen.findByText('暂无会话');
    await send('迟到的问题');
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith('/chat'))).toBe(true));
    await userEvent.click(screen.getByRole('button', { name: '新建会话' }));
    finish?.(response(answer));
    await waitFor(() => expect(screen.getByLabelText('向 AI 运维助手提问')).toBeEnabled());
    expect(within(screen.getByRole('log')).queryByText(answer.reply)).not.toBeInTheDocument();
    expect(screen.getByText('描述你遇到的运维问题')).toBeInTheDocument();
  });

  it('clears account history on user change and ignores the previous user pending detail', async () => {
    const api = installConversationApi([conversation('old-user', '用户甲会话', [turn('u1', '用户甲的敏感证据')])]);
    const page = mount();
    await screen.findByText('用户甲的敏感证据');
    api.conversations = [];
    page.rerender(<ConfigProvider><OperationsAssistantPage userId="user-2" /></ConfigProvider>);
    expect(screen.queryByText('用户甲的敏感证据')).not.toBeInTheDocument();
    expect(await screen.findByText('暂无会话')).toBeInTheDocument();
  });

  it('clears sensitive state when the session expires', async () => {
    installConversationApi([conversation('existing', '历史会话', [turn('u1', '敏感内容')])]);
    mount();
    await screen.findByText('敏感内容');
    fireEvent(window, new Event('sentinel:session-expired'));
    expect(screen.queryByText('敏感内容')).not.toBeInTheDocument();
    expect(screen.getByLabelText('向 AI 运维助手提问')).toBeDisabled();
  });

  it('ignores the previous user detail response after changing account', async () => {
    const api = installConversationApi([conversation('previous', '甲的会话')]);
    let finish: ((value: Response) => void) | undefined;
    api.detailPromise = new Promise<Response>((resolve) => { finish = resolve; });
    const page = mount();
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('/conversations/previous?'))).toBe(true));
    api.conversations = [];
    page.rerender(<ConfigProvider><OperationsAssistantPage userId="new-user" /></ConfigProvider>);
    await screen.findByText('暂无会话');
    finish?.(response(conversation('previous', '甲的会话', [turn('sensitive', '旧账号秘密')])));
    await waitFor(() => expect(screen.getByLabelText('向 AI 运维助手提问')).toBeEnabled());
    expect(screen.queryByText('旧账号秘密')).not.toBeInTheDocument();
  });

  it('reuses the request id when retrying an unknown network result', async () => {
    const original = vi.mocked(fetch).getMockImplementation();
    let failed = false;
    vi.stubGlobal('fetch', vi.fn((url: RequestInfo | URL, init?: RequestInit) => {
      if (String(url).endsWith('/chat') && !failed) {
        failed = true;
        return Promise.reject(new Error('网络结果未知'));
      }
      return original?.(url, init);
    }));
    mount();
    await screen.findByText('暂无会话');
    await send('分析服务器负载');
    await screen.findByText('网络结果未知');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByText(answer.reply);
    const chats = vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith('/chat'));
    expect(JSON.parse(String(chats[0][1]?.body)).request_id).toBe(JSON.parse(String(chats[1][1]?.body)).request_id);
  });
});
