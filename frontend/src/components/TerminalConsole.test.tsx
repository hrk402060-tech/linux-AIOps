import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TerminalConsole } from './TerminalConsole';
import type { Asset } from '../types/asset';

const terminalInput = vi.hoisted(() => ({
  handler: null as ((data: string) => void) | null,
  resize: null as (() => void) | null, write: vi.fn(), writeln: vi.fn(), clear: vi.fn(),
}));

vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    cols = 80;
    rows = 24;
    loadAddon() {}
    open() {}
    writeln = terminalInput.writeln;
    clear = terminalInput.clear;
    write = terminalInput.write;
    dispose() {}
    onData(handler: (data: string) => void) { terminalInput.handler = handler; return { dispose() {} }; }
  },
}));
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }));

class TestSocket extends EventTarget {
  static OPEN = 1;
  static instances: TestSocket[] = [];
  readyState = 1;
  send = vi.fn();
  close = vi.fn();
  constructor() { super(); TestSocket.instances.push(this); }
}
const ticket = { ticket: 'test', session_id: 'session-1', websocket_path: '/api/v1/terminal/ws', expires_at: '' };
const asset = { id: 'asset-1', name: 'host' } as Asset;

describe('TerminalConsole disconnect', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('WebSocket', TestSocket);
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: () => void) { terminalInput.resize = callback; }
      observe() {}
      disconnect() {}
    });
  });

  it('keeps manual input but ignores legacy assistant dispatch requests', () => {
    const legacy = { commandRequest: { id: 'old-approved', sequence: 1 } };
    render(<TerminalConsole asset={asset} ticket={ticket} onStateChange={vi.fn()} {...legacy} />);
    const socket = TestSocket.instances[0];
    expect(socket.send).not.toHaveBeenCalled();
    act(() => terminalInput.handler?.('uptime\n'));
    expect(socket.send).toHaveBeenCalledWith(JSON.stringify({ type: 'input', data: 'uptime\n' }));
    expect(socket.send).not.toHaveBeenCalledWith(expect.stringContaining('approved_command'));
  });

  afterEach(() => { vi.unstubAllGlobals(); TestSocket.instances = []; terminalInput.resize = null; });

  it('reports server closure once and ignores subsequent transport closure', () => {
    const onStateChange = vi.fn();
    render(<TerminalConsole asset={asset} ticket={ticket} onStateChange={onStateChange} />);
    const socket = TestSocket.instances[0];
    const endedAt = '2026-11-03T09:00:00Z';
    act(() => {
      socket.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({
        type: 'closed', reason: 'terminated', ended_at: endedAt,
      }) }));
      socket.dispatchEvent(new CloseEvent('close', { code: 1006 }));
    });
    expect(onStateChange).toHaveBeenCalledTimes(1);
    expect(onStateChange).toHaveBeenCalledWith('closed', {
      reason: 'terminated', message: '会话已由用户断开', ended_at: endedAt,
    });
    expect(socket.close).toHaveBeenCalledWith(1000);
  });

  it('preserves the first server error and blocks later messages, input and resize', () => {
    const onStateChange = vi.fn();
    const onOutput = vi.fn();
    render(<TerminalConsole asset={asset} ticket={ticket} onStateChange={onStateChange} onOutput={onOutput} />);
    const socket = TestSocket.instances[0];
    act(() => {
      socket.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ type: 'error', message: 'SSH 认证失败' }) }));
      socket.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ type: 'connected' }) }));
      socket.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ type: 'output', data: 'stale' }) }));
      socket.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ type: 'closed', reason: 'failed' }) }));
      socket.dispatchEvent(new CloseEvent('close', { code: 1000 }));
      terminalInput.handler?.('uptime\n');
      terminalInput.resize?.();
    });
    expect(onStateChange).toHaveBeenCalledTimes(1);
    expect(onStateChange).toHaveBeenCalledWith('failed', expect.objectContaining({ message: 'SSH 认证失败' }));
    expect(onOutput).not.toHaveBeenCalled();
    expect(terminalInput.write).not.toHaveBeenCalled();
    expect(terminalInput.clear).not.toHaveBeenCalled();
    expect(socket.send).not.toHaveBeenCalled();
    expect(socket.close).toHaveBeenCalledWith(1000);
  });

  it('reports WebSocket transport errors once without exposing browser event details', () => {
    const onStateChange = vi.fn();
    render(<TerminalConsole asset={asset} ticket={ticket} onStateChange={onStateChange} />);
    const socket = TestSocket.instances[0];
    act(() => {
      socket.dispatchEvent(new Event('error'));
      socket.dispatchEvent(new CloseEvent('close', { code: 1006 }));
    });
    expect(onStateChange).toHaveBeenCalledTimes(1);
    expect(onStateChange).toHaveBeenCalledWith('failed', expect.objectContaining({
      reason: 'transport_error', message: '终端连接已中断，请重新连接',
    }));
  });

  it('reports an abnormal close when no server terminal event arrives', () => {
    const onStateChange = vi.fn();
    render(<TerminalConsole asset={asset} ticket={ticket} onStateChange={onStateChange} />);
    act(() => TestSocket.instances[0].dispatchEvent(new CloseEvent('close', { code: 1006 })));
    expect(onStateChange).toHaveBeenCalledWith('failed', expect.objectContaining({ reason: 'transport_error' }));
  });

  it('keeps the manual shell usable when legacy AI execution is rejected', () => {
    const onStateChange = vi.fn();
    render(<TerminalConsole asset={asset} ticket={ticket} onStateChange={onStateChange} />);
    const socket = TestSocket.instances[0];
    act(() => {
      socket.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({
        type: 'error', code: 'ai_execution_disabled', message: 'AI 不执行终端命令',
      }) }));
      terminalInput.handler?.('uptime\n');
    });
    expect(onStateChange).not.toHaveBeenCalled();
    expect(socket.close).not.toHaveBeenCalled();
    expect(terminalInput.writeln).toHaveBeenCalledWith('AI 不执行终端命令');
    expect(socket.send).toHaveBeenCalledWith(JSON.stringify({ type: 'input', data: 'uptime\n' }));
  });

  it.each(['{', 'null', '[]', '{"type":"output","data":42}'])('fails safely on malformed terminal frame %s', (data) => {
    const onStateChange = vi.fn();
    render(<TerminalConsole asset={asset} ticket={ticket} onStateChange={onStateChange} />);
    const socket = TestSocket.instances[0];
    act(() => socket.dispatchEvent(new MessageEvent('message', { data })));
    expect(onStateChange).toHaveBeenCalledTimes(1);
    expect(onStateChange).toHaveBeenCalledWith('failed', expect.objectContaining({
      reason: 'protocol_error', message: '终端响应异常，请重新连接',
    }));
    expect(socket.close).toHaveBeenCalledWith(1000);
  });

  it('closes on unmount and ignores stale open and message events', () => {
    const onStateChange = vi.fn();
    const { unmount } = render(<TerminalConsole asset={asset} ticket={ticket} onStateChange={onStateChange} />);
    const socket = TestSocket.instances[0];
    unmount();
    act(() => {
      socket.dispatchEvent(new Event('open'));
      socket.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ type: 'connected' }) }));
    });
    expect(socket.close).toHaveBeenCalledWith(1000);
    expect(socket.send).not.toHaveBeenCalled();
    expect(onStateChange).not.toHaveBeenCalled();
  });
});
