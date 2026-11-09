import { fireEvent, render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { describe, expect, it, vi } from 'vitest';
import { RedisGrantForm } from './RedisGrantForm';
import type { Asset } from '../../types/asset';

vi.mock('../../api/diagnostics', async () => ({
  ...(await vi.importActual<typeof import('../../api/diagnostics')>('../../api/diagnostics')),
  getDiagnosticGrants: vi.fn(),
}));

const asset = {
  id: 'asset-1', name: 'redis-1', host: '192.0.2.10', ssh_port: 22,
  os_family: 'linux', lifecycle_status: 'enabled', credential: null,
} as unknown as Asset;
const catalog = ['status', 'memory', 'clients', 'slowlog'].map((item) => ({
  id: `redis.${item}`, label: `Redis ${item}`, domain: 'redis', available: true,
  read_only: true, required_permission: 'redis:observe', status: 'available',
}));

describe('RedisGrantForm', () => {
  it('shows TLS-only read-only scope and confirmation fields', () => {
    render(<ConfigProvider><RedisGrantForm assets={[asset]} catalog={catalog}
      initialGrant={null} busy={false} canSubmit onCancel={vi.fn()} onSubmit={vi.fn()} /></ConfigProvider>);
    expect(screen.getByRole('dialog', { name: '配置 Redis 只读授权' })).toBeInTheDocument();
    expect(screen.getByText(/仅通过 TLS 读取 Redis 状态/)).toBeInTheDocument();
    expect(screen.getByLabelText('Redis 数据库编号')).toBeInTheDocument();
    expect(screen.getByLabelText('已人工确认 Redis 只读权限')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('资产名称确认'), { target: { value: 'redis-1' } });
    expect(screen.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });
});
