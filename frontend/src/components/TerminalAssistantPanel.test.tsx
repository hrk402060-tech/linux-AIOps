import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { TerminalAssistantPanel } from './TerminalAssistantPanel';

const command = {
  id: 'command-1', analysis_id: 'analysis-1', terminal_session_id: 'session-1',
  command: 'uptime', purpose: '查看负载', validation_step: '确认负载趋势',
  risk_level: 'low' as const, status: 'pending_confirmation' as const,
  confirmation_phrase: 'EXECUTE 12345678', requested_by: 'admin', reviewed_by: null,
  reviewed_at: null, executed_at: null, created_at: '2026-09-08T08:00:00Z',
};
const analysis = {
  id: 'analysis-1', terminal_session_id: 'session-1', mode: 'controlled' as const,
  redacted_context: 'API_TOKEN=[REDACTED]', diagnosis: '检测到负载异常。',
  plan: ['确认负载'], validation_steps: ['复查负载'], operator: 'admin',
  created_at: '2026-09-08T08:00:00Z', commands: [command],
};
const response = (body: unknown, status = 200) => ({
  ok: status >= 200 && status < 300, status, json: async () => body,
});
const chatResponse = { conversation_id: 'conversation-1', engine: 'openai-compatible:test-model', reply: '你好！', analysis, history: [] };

describe('TerminalAssistantPanel', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('sends the question separately from terminal evidence', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(chatResponse, 201));
    vi.stubGlobal('fetch', fetchMock);
    render(<TerminalAssistantPanel sessionId="session-1" context="sshd active" connected onExecute={vi.fn()} executedCommandId={null} />);
    fireEvent.change(screen.getByLabelText('向终端 AI 提问'), { target: { value: '安装 k8s' } });
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(await screen.findByText('你好！')).toBeInTheDocument();
    expect(screen.queryByText(analysis.diagnosis)).not.toBeInTheDocument();
    expect(screen.queryByText('研判')).not.toBeInTheDocument();
    const body = String(fetchMock.mock.calls[0][1]?.body);
    expect(body).toContain('安装 k8s');
    expect(body).toContain('sshd active');
  });

  it('forces analysis-only requests and never exposes execution for returned commands', async () => {
    const onExecute = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(response(chatResponse, 201));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider><TerminalAssistantPanel sessionId="session-1" context="load high" connected onExecute={onExecute} executedCommandId={null} /></ConfigProvider>);

    expect(screen.getByText('只读分析')).toBeInTheDocument();
    expect(screen.queryByText('辅助操作')).not.toBeInTheDocument();
    expect(screen.queryByText('受控执行')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('向终端 AI 提问'), { target: { value: '为什么负载突然升高？' } });
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(await screen.findByText('你好！')).toBeInTheDocument();
    expect(screen.getByLabelText('运维助手对话记录')).toHaveTextContent('为什么负载突然升高？');
    expect(screen.queryByRole('button', { name: '确认' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '执行' })).not.toBeInTheDocument();
    expect(screen.queryByText('建议命令')).not.toBeInTheDocument();
    expect(onExecute).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({ mode: 'analysis_only' });
    expect(String(fetchMock.mock.calls[0][1]?.body)).toContain('为什么负载突然升高？');
  });

  it('does not send while an IME composition is active', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<TerminalAssistantPanel sessionId="session-1" context="sshd active" connected onExecute={vi.fn()} executedCommandId={null} />);
    const input = screen.getByLabelText('向终端 AI 提问');
    expect(input).toHaveAttribute('maxlength', '4000');
    fireEvent.change(input, { target: { value: '安装 k8s' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', isComposing: true });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('sends on Enter and keeps Shift+Enter available for a new line', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(chatResponse, 201));
    vi.stubGlobal('fetch', fetchMock);
    render(<TerminalAssistantPanel sessionId="session-1" context="sshd active" connected onExecute={vi.fn()} executedCommandId={null} />);
    const input = screen.getByLabelText('向终端 AI 提问');
    fireEvent.change(input, { target: { value: '安装 k8s' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', shiftKey: true });
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(await screen.findByLabelText('运维助手对话记录')).toHaveTextContent('安装 k8s');
    await waitFor(() => expect(input).toHaveValue(''));
  });

  it('includes prior turns in the next question without sending analysis metadata', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(chatResponse, 201))
      .mockResolvedValueOnce(response({ ...chatResponse, conversation_id: 'conversation-2' }, 201));
    vi.stubGlobal('fetch', fetchMock);
    render(<TerminalAssistantPanel sessionId="session-1" context="sshd active" connected onExecute={vi.fn()} executedCommandId={null} />);
    const input = screen.getByLabelText('向终端 AI 提问');
    fireEvent.change(input, { target: { value: '安装 k8s' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(input).toHaveValue(''));
    fireEvent.change(input, { target: { value: '如何验证安装成功？' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const body = JSON.parse(String(fetchMock.mock.calls[1][1]?.body)) as {
      message: string; context: string; history: { role: string; content: string }[];
    };
    expect(body.message).toBe('如何验证安装成功？');
    expect(body.context).toBe('sshd active');
    expect(body.history).toEqual([
      { role: 'user', content: '安装 k8s' },
      { role: 'assistant', content: '你好！' },
    ]);
  });

  it('keeps the question when the request fails and displays the backend error', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ detail: '模型服务暂时不可用' }, 503));
    vi.stubGlobal('fetch', fetchMock);
    render(<TerminalAssistantPanel sessionId="session-1" context="sshd active" connected onExecute={vi.fn()} executedCommandId={null} />);
    const input = screen.getByLabelText('向终端 AI 提问');
    fireEvent.change(input, { target: { value: '如何排查负载？' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    expect(await screen.findByText('模型服务暂时不可用')).toBeInTheDocument();
    expect(input).toHaveValue('如何排查负载？');
    expect(input).toBeEnabled();
  });

  it('disables the question and request controls while disconnected', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<TerminalAssistantPanel sessionId={null} context="" connected={false} onExecute={vi.fn()} executedCommandId={null} />);
    expect(screen.getByLabelText('向终端 AI 提问')).toBeDisabled();
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '分析终端输出' })).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows the question immediately and prevents duplicate requests while waiting', async () => {
    let completeRequest!: (value: ReturnType<typeof response>) => void;
    const pending = new Promise<ReturnType<typeof response>>((resolve) => { completeRequest = resolve; });
    const fetchMock = vi.fn().mockReturnValue(pending);
    vi.stubGlobal('fetch', fetchMock);
    render(<TerminalAssistantPanel sessionId="session-1" context="load high" connected onExecute={vi.fn()} executedCommandId={null} />);
    const input = screen.getByLabelText('向终端 AI 提问');
    fireEvent.change(input, { target: { value: '怎么排查？' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    expect(screen.getByRole('log')).toHaveTextContent('怎么排查？');
    expect(screen.getByRole('log')).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('status')).toHaveTextContent('正在思考');
    expect(input).toBeDisabled();
    expect(screen.getByRole('button', { name: '清空对话' })).toBeDisabled();
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    expect(fetchMock).toHaveBeenCalledOnce();
    completeRequest(response(chatResponse, 201));
    await waitFor(() => expect(screen.getByRole('log')).toHaveAttribute('aria-busy', 'false'));
    expect(input).toBeEnabled();
  });

  it('clears the visible conversation and the history sent in the next request', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(chatResponse, 201))
      .mockResolvedValueOnce(response({ ...chatResponse, conversation_id: 'conversation-2' }, 201));
    vi.stubGlobal('fetch', fetchMock);
    render(<TerminalAssistantPanel sessionId="session-1" context="load high" connected onExecute={vi.fn()} executedCommandId={null} />);
    const input = screen.getByLabelText('向终端 AI 提问');
    fireEvent.change(input, { target: { value: '上一条问题' } });
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByText('你好！');
    await userEvent.click(screen.getByRole('button', { name: '清空对话' }));
    expect(screen.getByRole('log')).not.toHaveTextContent('上一条问题');
    expect(screen.getByRole('log')).not.toHaveTextContent(analysis.diagnosis);
    expect(screen.getByText('有什么需要处理？')).toBeInTheDocument();
    fireEvent.change(input, { target: { value: '新的问题' } });
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const body = JSON.parse(String(fetchMock.mock.calls[1][1]?.body)) as { history: unknown[] };
    expect(body.history).toEqual([]);
  });

  it('analyzes terminal output without discarding an unsent question', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(chatResponse, 201));
    vi.stubGlobal('fetch', fetchMock);
    render(<TerminalAssistantPanel sessionId="session-1" context="load high" connected onExecute={vi.fn()} executedCommandId={null} />);
    const input = screen.getByLabelText('向终端 AI 提问');
    fireEvent.change(input, { target: { value: '尚未发送的问题' } });
    await userEvent.click(screen.getByRole('button', { name: '分析终端输出' }));
    await screen.findByText('你好！');
    await waitFor(() => expect(screen.getByRole('log')).toHaveAttribute('aria-busy', 'false'));
    expect(input).toHaveValue('尚未发送的问题');
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body)) as { message: string; context: string };
    expect(body.message).toBe('请分析当前终端输出');
    expect(body.context).toBe('load high');
  });

  it('keeps distinct messages when the backend repeats a conversation id', async () => {
    const errors = vi.spyOn(console, 'error');
    const fetchMock = vi.fn().mockResolvedValue(response(chatResponse, 201));
    vi.stubGlobal('fetch', fetchMock);
    try {
      render(<TerminalAssistantPanel sessionId="session-1" context="load high" connected onExecute={vi.fn()} executedCommandId={null} />);
      const input = screen.getByLabelText('向终端 AI 提问');
      fireEvent.change(input, { target: { value: '第一个问题' } });
      await userEvent.click(screen.getByRole('button', { name: '发送' }));
      await waitFor(() => expect(input).toHaveValue(''));
      fireEvent.change(input, { target: { value: '第二个问题' } });
      await userEvent.click(screen.getByRole('button', { name: '发送' }));
      await waitFor(() => expect(screen.getAllByText('你好！')).toHaveLength(2));
      expect(screen.getByRole('log').querySelectorAll('article')).toHaveLength(4);
      expect(errors.mock.calls.some((args) => args.some((value) => String(value).includes('same key')))).toBe(false);
    } finally {
      errors.mockRestore();
    }
  });

  it('renders a normal answer once without unrelated diagnostic sections', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({
      ...chatResponse, reply: '**你好**，可以帮你排查 Linux 问题。',
      analysis: { ...analysis, mode: 'analysis_only', commands: [] },
    }, 201));
    vi.stubGlobal('fetch', fetchMock);
    render(<TerminalAssistantPanel sessionId="session-1" context="load high" connected onExecute={vi.fn()} executedCommandId={null} />);
    fireEvent.change(screen.getByLabelText('向终端 AI 提问'), { target: { value: '你好' } });
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(await screen.findByText('你好', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.queryByText(analysis.diagnosis)).not.toBeInTheDocument();
    expect(screen.queryByText('研判')).not.toBeInTheDocument();
    expect(screen.queryByText('排障计划')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '确认' })).not.toBeInTheDocument();
  });
});
