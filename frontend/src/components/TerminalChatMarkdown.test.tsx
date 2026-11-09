import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { TerminalChatMarkdown } from './TerminalChatMarkdown';

describe('TerminalChatMarkdown', () => {
  afterEach(() => vi.restoreAllMocks());

  it('renders headings, lists, emphasis, and tables as structured content', () => {
    render(<TerminalChatMarkdown content={'## 排障建议\n\n先检查 **服务状态**。\n\n1. 查看负载\n2. 核对日志\n\n| 指标 | 状态 |\n| --- | --- |\n| CPU | 正常 |'} />);
    expect(screen.getByRole('heading', { level: 2, name: '排障建议' })).toBeInTheDocument();
    expect(screen.getByText('服务状态', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByRole('table')).toHaveTextContent('CPU');
  });

  it('copies only the code block without sending or executing commands', async () => {
    const user = userEvent.setup();
    const copy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined);
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    try {
      render(<TerminalChatMarkdown content={'```bash\nsystemctl status sshd\n```'} />);
      await user.click(screen.getByRole('button', { name: '复制代码' }));
      expect(copy).toHaveBeenCalledWith('systemctl status sshd\n');
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('copies through the fallback on HTTP without leaving a temporary input', async () => {
    const clipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    const execCommand = Object.getOwnPropertyDescriptor(document, 'execCommand');
    const copy = vi.fn().mockReturnValue(true);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    Object.defineProperty(document, 'execCommand', { configurable: true, value: copy });
    try {
      render(<TerminalChatMarkdown content={'```bash\nuptime\n```'} />);
      fireEvent.click(screen.getByRole('button', { name: '复制代码' }));
      await waitFor(() => expect(copy).toHaveBeenCalledWith('copy'));
      expect(document.querySelector('textarea')).toBeNull();
    } finally {
      if (clipboard) Object.defineProperty(navigator, 'clipboard', clipboard);
      else Reflect.deleteProperty(navigator, 'clipboard');
      if (execCommand) Object.defineProperty(document, 'execCommand', execCommand);
      else Reflect.deleteProperty(document, 'execCommand');
    }
  });

  it('does not render model-provided raw HTML or load remote images', () => {
    const { container } = render(<TerminalChatMarkdown content={'<script>alert(1)</script>\n\n<img src="https://example.invalid/tracker" onerror="alert(1)" />\n\n![远程图片](https://example.invalid/image.png)'} />);
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('远程图片')).toBeInTheDocument();
  });

  it('uses safe external links and rejects JavaScript URLs', () => {
    render(<TerminalChatMarkdown content={'[文档](https://example.com/docs)\n\n[恶意链接](javascript:alert%281%29)'} />);
    const docs = screen.getByRole('link', { name: '文档' });
    expect(docs).toHaveAttribute('href', 'https://example.com/docs');
    expect(docs).toHaveAttribute('target', '_blank');
    expect(docs).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByText('恶意链接').getAttribute('href')).not.toMatch(/^javascript:/i);
  });
});
