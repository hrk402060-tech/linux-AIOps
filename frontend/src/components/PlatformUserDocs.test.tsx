import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { describe, expect, it, vi } from 'vitest';
import { PlatformUserDocs } from './PlatformUserDocs';
import { userDocs } from './user-docs';
import type { PageKey } from '../App';

function renderDocs() {
  const navigate = vi.fn();
  render(<ConfigProvider theme={{ token: { motion: false } }}>
    <PlatformUserDocs onNavigate={navigate} />
  </ConfigProvider>);
  return navigate;
}

describe('使用文档', () => {
  it('覆盖全部现有入口及安全检查子功能，不新增移除的凭据入口', () => {
    const pages: PageKey[] = ['dashboard', 'server-monitoring', 'assets', 'cmdb', 'automation', 'terminal',
      'ops-assistant', 'patches', 'observability', 'probes', 'alerts', 'incidents',
      'scans', 'schedules', 'baselines', 'exposures', 'credential-risks', 'findings',
      'knowledge', 'agent', 'reports', 'system', 'security-settings', 'audit-log'];
    expect(new Set(userDocs.map((entry) => entry.page))).toEqual(new Set(pages));
    expect(userDocs.find((entry) => entry.id === 'cmdb')).toMatchObject({
      title: '系统/接口监控', page: 'cmdb',
    });
    expect(new Set(userDocs.map((entry) => entry.id)).size).toBe(userDocs.length);
    for (const entry of userDocs) {
      expect(entry.purpose).toBeTruthy();
      expect(entry.requirements.length).toBeGreaterThan(0);
      expect(entry.steps.length).toBeGreaterThan(0);
      expect(entry.notes.length).toBeGreaterThan(0);
    }
  });

  it('按文档站导航浏览四个分类、文章和章节目录', () => {
    renderDocs();
    const directory = screen.getByRole('complementary', { name: '文档目录' });
    for (const group of ['工作台', '监控告警', '安全中心', '系统管理']) {
      expect(within(directory).getByRole('button', { name: group })).toBeVisible();
    }
    for (const title of ['快速开始', '功能指南', '运维流程', '安全与权限']) {
      expect(screen.getByRole('button', { name: title })).toBeVisible();
    }
    expect(screen.getByRole('heading', { name: '作用' })).toBeVisible();
    expect(screen.getByRole('heading', { name: '需要准备' })).toBeVisible();
    expect(screen.getByRole('complementary', { name: '本文目录' })).toBeVisible();
    expect(screen.getByRole('link', { name: '作用' })).toHaveAttribute('href', '#doc-purpose');
    expect(screen.getByText(/uptime\s+free -m\s+df -P\s+df -Pi/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '安全与权限' }));
    expect(screen.getByRole('button', { name: '漏洞扫描' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '漏洞扫描' }));
    expect(screen.getByRole('heading', { name: '漏洞扫描', level: 1 })).toBeVisible();
    expect(screen.getByText(/纯版本号不能可靠推断 backport/)).toBeVisible();
  });

  it('只有一个顶部选中项，系统文档可访问，切换文章清除旧章节', () => {
    renderDocs();
    const nav = screen.getByRole('navigation', { name: '文档导航' });
    expect(within(nav).getByRole('button', { current: 'page' })).toHaveTextContent('快速开始');
    fireEvent.click(screen.getByRole('button', { name: '功能指南' }));
    expect(within(nav).getByRole('button', { current: 'page' })).toHaveTextContent('功能指南');
    window.history.replaceState({}, '', '/?view=docs#doc-notes');
    fireEvent.click(screen.getByRole('button', { name: '服务器资产' }));
    expect(window.location.hash).toBe('');
    fireEvent.click(screen.getByRole('button', { name: '系统管理' }));
    fireEvent.click(screen.getByRole('button', { name: 'AI 配置' }));
    expect(screen.getByRole('heading', { name: 'AI 配置', level: 1 })).toBeVisible();
  });

  it('键盘快捷键聚焦搜索，命令按原内容复制且没有网络请求', async () => {
    const clipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    const writeText = vi.fn().mockResolvedValue(undefined);
    const fetchMock = vi.fn();
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    vi.stubGlobal('fetch', fetchMock);
    try {
      renderDocs();
      fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
      expect(screen.getByRole('textbox', { name: '搜索使用文档' })).toHaveFocus();
      fireEvent.click(screen.getByRole('button', { name: /复制命令：目标服务器人工核验/ }));
      await screen.findByText('已复制');
      expect(writeText).toHaveBeenCalledWith('uptime\nfree -m\ndf -P\ndf -Pi');
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      if (clipboard) Object.defineProperty(navigator, 'clipboard', clipboard);
      else Reflect.deleteProperty(navigator, 'clipboard');
      vi.unstubAllGlobals();
    }
  });

  it('HTTP 下回退复制并报告失败，清理临时输入框', async () => {
    const clipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    const execCommand = Object.getOwnPropertyDescriptor(document, 'execCommand');
    const copy = vi.fn().mockReturnValueOnce(true).mockReturnValue(false);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    Object.defineProperty(document, 'execCommand', { configurable: true, value: copy });
    try {
      renderDocs();
      const button = screen.getByRole('button', { name: /复制命令：目标服务器人工核验/ });
      fireEvent.click(button);
      await screen.findByText('已复制');
      expect(document.querySelector('textarea')).toBeNull();
      fireEvent.click(button);
      await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('复制失败'));
      expect(document.querySelector('textarea')).toBeNull();
    } finally {
      if (clipboard) Object.defineProperty(navigator, 'clipboard', clipboard);
      else Reflect.deleteProperty(navigator, 'clipboard');
      if (execCommand) Object.defineProperty(document, 'execCommand', execCommand);
      else Reflect.deleteProperty(document, 'execCommand');
    }
  });

  it('搜索跨工作台匹配前置条件和命令，清空后恢复分类', () => {
    renderDocs();
    const search = screen.getByRole('textbox', { name: '搜索使用文档' });
    for (const command of ['--askpass', 'SHOW GLOBAL STATUS', 'SLOWLOG GET 16', '/api/overview']) {
      fireEvent.change(search, { target: { value: command } });
      expect(screen.getByRole('button', { name: '只读诊断授权与执行' })).toBeVisible();
    }
    fireEvent.change(search, { target: { value: '  StrictHostKeyChecking  ' } });
    fireEvent.click(screen.getByRole('button', { name: '只读诊断授权与执行' }));
    fireEvent.click(screen.getByText('SSH（Linux）只读查询', { exact: true }));
    expect(screen.getByText(/StrictHostKeyChecking=yes/)).toBeVisible();
    fireEvent.change(search, { target: { value: '配置批准网段' } });
    fireEvent.click(screen.getByRole('button', { name: 'API 拨测' }));
    expect(screen.getByText(/无需为批准网段修改 .env/)).toBeVisible();
    fireEvent.change(search, { target: { value: '不存在的功能abc' } });
    expect(screen.getByText('未找到相关文档')).toBeVisible();
    fireEvent.change(search, { target: { value: '' } });
    expect(screen.getByRole('button', { name: '快速开始' })).toBeVisible();
  });

  it('只读授权说明与命令仅展示，打开功能复用原 PageKey', () => {
    const navigate = renderDocs();
    fireEvent.click(screen.getByRole('button', { name: '运维流程' }));
    fireEvent.click(screen.getByRole('button', { name: '只读诊断授权与执行' }));
    expect(screen.getByText(/验证会真实采集/)).toBeVisible();
    expect(screen.getByText(/人工确认也不会把诊断授权升级为写权限/)).toBeVisible();
    const entry = screen.getByRole('article', { name: '只读诊断授权与执行' });
    fireEvent.click(within(entry).getByRole('button', { name: '打开相关功能' }));
    expect(navigate).toHaveBeenCalledWith('alerts');
    expect(screen.queryByRole('button', { name: /执行命令|自动授权/ })).not.toBeInTheDocument();
  });

  it('使用文档保留六类只读诊断命令与权限限制', () => {
    renderDocs();
    fireEvent.click(screen.getByRole('button', { name: '运维流程' }));
    fireEvent.click(screen.getByRole('button', { name: '只读诊断授权与执行' }));
    for (const [title, content] of [
      ['SSH（Linux）只读查询', /StrictHostKeyChecking=yes/],
      ['Docker 只读查询', /当前项目未提供 Docker 代理安装器/],
      ['K8s 只读查询', /当前不采集节点、日志/],
      ['MySQL 只读查询', /SHOW GLOBAL STATUS/],
      ['Redis 只读查询', /SLOWLOG GET 16/],
      ['RabbitMQ 只读查询', /api\/overview/],
    ] as const) {
      fireEvent.click(screen.getByText(title, { exact: true }));
      expect(screen.getByText(content)).toBeVisible();
    }
  });
});
