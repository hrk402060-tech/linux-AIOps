import { useEffect, useRef } from 'react';
import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';

import type { Asset } from '../types/asset';
import type { TerminalConnectionDetails, TerminalTicket } from '../types/terminal';

type ConnectionState = 'connected' | 'closed' | 'failed';
type StateListener = (state: ConnectionState, details?: TerminalConnectionDetails) => void;
interface TerminalMessage extends TerminalConnectionDetails { type: string; data?: string; code?: string; }
interface TerminalEvents { onStateChange: StateListener; onOutput?: (value: string) => void; }

const endMessages: Record<string, string> = {
  closed: '会话已关闭', terminated: '会话已由用户断开', timeout: '会话已超时',
  failed: 'SSH 连接失败', revoked: '登录已失效，会话已断开',
};

function readTerminalMessage(data: unknown): TerminalMessage | null {
  try {
    const value: unknown = typeof data === 'string' ? JSON.parse(data) : null;
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const record = value as Record<string, unknown>;
    if (typeof record.type !== 'string') return null;
    for (const key of ['data', 'message', 'reason', 'ended_at', 'code']) {
      if (record[key] != null && typeof record[key] !== 'string') return null;
    }
    return record as unknown as TerminalMessage;
  } catch { return null; }
}

function handleTerminalMessage(
  message: TerminalMessage, terminal: Terminal, events: TerminalEvents,
  finish: (state: 'closed' | 'failed', details: TerminalConnectionDetails) => void,
  resize: () => void,
) {
  if (message.type === 'error' && message.code === 'ai_execution_disabled') {
    terminal.writeln(message.message ?? 'AI 仅提供只读建议，不执行终端命令');
    return;
  }
  if (message.type === 'connected') {
    terminal.clear(); events.onStateChange('connected'); resize();
  } else if (message.type === 'closed' || message.type === 'error') {
    finish(message.type === 'closed' ? 'closed' : 'failed', {
      reason: message.reason ?? (message.type === 'closed' ? 'closed' : 'failed'),
      message: message.message, ended_at: message.ended_at,
    });
  } else if (message.type === 'output') {
    terminal.write(message.data ?? ''); events.onOutput?.(message.data ?? '');
  }
}

function bindTerminalSocket(socket: WebSocket, terminal: Terminal, ticket: TerminalTicket, events: TerminalEvents) {
  let disposed = false;
  let finished = false;
  const send = (payload: object) => {
    if (!disposed && !finished && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
  };
  const resize = () => send({ type: 'resize', columns: terminal.cols, rows: terminal.rows });
  const finish = (state: 'closed' | 'failed', details: TerminalConnectionDetails) => {
    if (disposed || finished) return;
    finished = true;
    const message = details.message || endMessages[details.reason ?? ''] || '会话已断开';
    terminal.writeln(''); terminal.writeln(message);
    events.onStateChange(state, { ...details, message });
    socket.close(1000);
  };
  const transportError = () => finish('failed', {
    reason: 'transport_error', message: '终端连接已中断，请重新连接',
  });
  socket.addEventListener('open', () => {
    if (disposed || finished) { socket.close(1000); return; }
    send({ type: 'auth', ticket: ticket.ticket, columns: terminal.cols, rows: terminal.rows });
  });
  socket.addEventListener('message', (event) => {
    if (disposed || finished) return;
    const message = readTerminalMessage(event.data);
    if (!message) {
      finish('failed', { reason: 'protocol_error', message: '终端响应异常，请重新连接' });
      return;
    }
    handleTerminalMessage(message, terminal, events, finish, resize);
  });
  socket.addEventListener('error', transportError);
  socket.addEventListener('close', (event) => {
    if (event.code === 1000) finish('closed', { reason: 'closed' });
    else transportError();
  });
  return {
    resize, input: (data: string) => send({ type: 'input', data }),
    dispose: () => { disposed = true; socket.close(1000); },
  };
}

export function TerminalConsole({ asset, ticket, onStateChange, onOutput }: {
  asset: Asset; ticket: TerminalTicket; onStateChange: StateListener;
  onOutput?: (value: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current) return;
    const terminal = new Terminal({
      cursorBlink: true, convertEol: true, fontSize: 14, scrollback: 5000,
      fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace',
      theme: { background: '#101713', foreground: '#d7e5dc', cursor: '#75d3aa' },
    });
    const fit = new FitAddon();
    terminal.loadAddon(fit); terminal.open(container.current); fit.fit();
    terminal.writeln('正在建立加密 SSH 会话...');
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(protocol + '//' + window.location.host + ticket.websocket_path);
    const connection = bindTerminalSocket(socket, terminal, ticket, { onStateChange, onOutput });
    const input = terminal.onData((data) => {
      connection.input(data);
    });
    const observer = new ResizeObserver(() => {
      fit.fit();
      connection.resize();
    });
    observer.observe(container.current);
    return () => {
      connection.dispose(); observer.disconnect(); input.dispose(); terminal.dispose();
    };
  }, [asset.id, ticket, onOutput, onStateChange]);
  return <div className="terminal-console" ref={container} aria-label={asset.name + ' SSH 终端'} />;
}
