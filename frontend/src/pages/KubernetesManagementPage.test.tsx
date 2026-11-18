import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { KubernetesManagementPage } from './KubernetesManagementPage';

vi.mock('./KubernetesClustersPage', () => ({
  KubernetesClustersPage: ({ onOpenOverview }: { onOpenOverview: (cluster: { id: string; name: string }) => void }) =>
    <button onClick={() => onOpenOverview({ id: 'cluster-15', name: 'K8s 15' })}>打开已选集群总览</button>,
}));
vi.mock('./KubernetesOverviewPage', () => ({
  KubernetesOverviewPage: ({ initialCluster, onOpenConnections }: {
    initialCluster?: { id: string }; onOpenConnections: () => void;
  }) => <><p>总览目标 {initialCluster?.id ?? '未选择'}</p><button onClick={onOpenConnections}>返回接入</button></>,
}));
describe('Kubernetes management tabs', () => {
  it('shows the changes tab only for administrators', () => {
    const view = render(<KubernetesManagementPage currentUser={{ role: 'auditor' }} />);
    expect(screen.queryByRole('tab', { name: '集群变更' })).not.toBeInTheDocument();
    view.rerender(<KubernetesManagementPage currentUser={{ role: 'admin' }} />);
    expect(screen.getByRole('tab', { name: '集群变更' })).toBeInTheDocument();
  });
  it('preserves the existing entry and passes the selected cluster into overview', () => {
    render(<KubernetesManagementPage currentUser={{ role: 'admin' }} />);
    expect(screen.getByRole('tab', { name: '集群接入' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('button', { name: '打开已选集群总览' }));
    expect(screen.getByText('总览目标 cluster-15')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '集群总览' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('button', { name: '返回接入' }));
    expect(screen.getByRole('button', { name: '打开已选集群总览' })).toBeInTheDocument();
  });
});
