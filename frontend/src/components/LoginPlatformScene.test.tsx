import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';

import { LoginPlatformScene } from './LoginPlatformScene';

const renderer = vi.hoisted(() => ({
  create: vi.fn(),
  select: vi.fn(),
  dispose: vi.fn(),
  rotate: vi.fn(),
  zoom: vi.fn(),
  reset: vi.fn(),
}));
vi.mock('./loginPlatformRenderer', () => ({
  createPlatformRenderer: renderer.create,
}));

beforeEach(() => {
  vi.clearAllMocks();
  renderer.create.mockResolvedValue(renderer);
});

it('keeps all four operations domains available without claiming live data', async () => {
  const { container } = render(<LoginPlatformScene state="idle" />);
  for (const name of ['资产与配置项', '监控与告警', '自动化与任务', '安全与审计']) {
    expect(screen.getByRole('button', { name })).toHaveAttribute('aria-pressed', 'false');
  }
  expect(screen.getByText('统一工作台')).toBeInTheDocument();
  await waitFor(() => expect(container.querySelector('.pl-scene')).toHaveAttribute('data-ready', 'true'));
  fireEvent.click(screen.getByRole('button', { name: '监控与告警' }));
  expect(screen.getByRole('button', { name: '监控与告警' })).toHaveAttribute('aria-pressed', 'true');
  expect(renderer.select).toHaveBeenLastCalledWith('monitoring');
  expect(screen.queryByText('运行正常')).not.toBeInTheDocument();
});

it('keeps the accessible illustration when WebGL cannot initialize', async () => {
  renderer.create.mockRejectedValueOnce(new Error('WebGL unavailable'));
  const { container } = render(<LoginPlatformScene state="loading" />);
  await waitFor(() => expect(container.querySelector('.pl-scene')).toHaveAttribute('data-ready', 'false'));
  expect(screen.getByRole('img', { name: /四类运维能力连接统一工作台/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '安全与审计' })).toBeEnabled();
  expect(screen.queryByRole('toolbar', { name: '三维视图控制' })).not.toBeInTheDocument();
  expect(container.querySelector('.pl-scene')).toHaveAttribute('data-auth-state', 'loading');
  fireEvent.doubleClick(screen.getByLabelText('平台三维架构图'));
  expect(renderer.reset).not.toHaveBeenCalled();
});

it('resets the view and selection by double-clicking the canvas without a toolbar', async () => {
  const { container } = render(<LoginPlatformScene state="idle" />);
  await waitFor(() => expect(container.querySelector('.pl-scene')).toHaveAttribute('data-ready', 'true'));
  expect(screen.queryByRole('toolbar')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '放大视图' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '资产与配置项' }));
  fireEvent.doubleClick(screen.getByLabelText('平台三维架构图'));
  expect(renderer.reset).toHaveBeenCalledOnce();
  expect(screen.getByRole('button', { name: '资产与配置项' })).toHaveAttribute('aria-pressed', 'false');
});

it('limits camera keyboard shortcuts to the focused canvas', async () => {
  const { container } = render(<><input aria-label="登录用户名" /><LoginPlatformScene state="idle" /></>);
  await waitFor(() => expect(container.querySelector('.pl-scene')).toHaveAttribute('data-ready', 'true'));
  const canvas = screen.getByLabelText('平台三维架构图');
  fireEvent.keyDown(canvas, { key: 'ArrowLeft' });
  expect(renderer.rotate).toHaveBeenLastCalledWith(-1);
  fireEvent.keyDown(canvas, { key: 'ArrowRight' });
  expect(renderer.rotate).toHaveBeenLastCalledWith(1);
  fireEvent.keyDown(canvas, { key: '+' });
  expect(renderer.zoom).toHaveBeenLastCalledWith(1);
  fireEvent.keyDown(canvas, { key: '-' });
  expect(renderer.zoom).toHaveBeenLastCalledWith(-1);
  fireEvent.keyDown(canvas, { key: 'Home' });
  expect(renderer.reset).toHaveBeenCalledOnce();
  renderer.rotate.mockClear();
  renderer.zoom.mockClear();
  renderer.reset.mockClear();
  fireEvent.keyDown(screen.getByRole('textbox', { name: '登录用户名' }), { key: 'ArrowLeft' });
  fireEvent.keyDown(screen.getByRole('textbox', { name: '登录用户名' }), { key: '+' });
  fireEvent.keyDown(screen.getByRole('textbox', { name: '登录用户名' }), { key: 'Home' });
  expect(renderer.rotate).not.toHaveBeenCalled();
  expect(renderer.zoom).not.toHaveBeenCalled();
  expect(renderer.reset).not.toHaveBeenCalled();
  for (const modifier of ['ctrlKey', 'metaKey', 'altKey']) {
    expect(fireEvent.keyDown(canvas, { key: '+', [modifier]: true })).toBe(true);
  }
  expect(renderer.zoom).not.toHaveBeenCalled();
});

it('cleans up a renderer that finishes loading after the login page unmounts', async () => {
  let finish: ((value: { select: typeof renderer.select; dispose: typeof renderer.dispose }) => void) | undefined;
  renderer.create.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const view = render(<LoginPlatformScene state="idle" />);
  await waitFor(() => expect(renderer.create).toHaveBeenCalledOnce());
  view.unmount();
  await act(async () => { finish?.({ select: renderer.select, dispose: renderer.dispose }); });
  expect(renderer.dispose).toHaveBeenCalledOnce();
});

it('does not recreate the WebGL context when login status changes', async () => {
  const view = render(<LoginPlatformScene state="idle" />);
  await waitFor(() => expect(renderer.create).toHaveBeenCalledOnce());
  view.rerender(<LoginPlatformScene state="success" />);
  expect(view.container.querySelector('.pl-scene')).toHaveAttribute('data-auth-state', 'success');
  expect(renderer.create).toHaveBeenCalledOnce();
  view.unmount();
  expect(renderer.dispose).toHaveBeenCalledOnce();
});
