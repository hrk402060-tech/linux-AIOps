import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  getKubernetesClusters, kubernetesFailureMessage, testKubernetesClusterDraft,
} from './kubernetes';

afterEach(() => vi.unstubAllGlobals());

describe('Kubernetes fixed failure messages', () => {
  it.each([
    ['tls_chain', '服务端证书链验证失败'],
    ['tls_expired', '服务端证书尚未生效或已过期'],
    ['tls_hostname', '服务端证书与 API Server 主机名不匹配'],
    ['identity_rejected', '请使用专用只读身份，勿使用默认或管理员身份'],
    ['target_policy_unconfigured', '尚未配置 Kubernetes API 允许网段，请联系管理员'],
    ['target_policy_invalid', 'Kubernetes API 目标策略配置无效，请联系管理员'],
    ['target_policy_rejected', 'API Server 地址不在管理员批准的目标范围内'],
    ['unsafe_permissions', '集群身份具有固定读取范围以外的权限，请收紧授权'],
    ['authorization_incomplete', '自权限检查不可用或结果不完整，请检查命名空间权限'],
    ['upstream_unauthorized', '集群身份认证失败'],
    ['upstream_forbidden', '集群身份缺少所需权限'],
    ['upstream_throttled', '集群 API 正在限流，请稍后重试'],
    ['api_unavailable', '集群所需 API 不可用'],
    ['certificate_expired', '配置中的证书已过期'],
  ])('preserves the useful reason for %s', (code, message) => {
    expect(kubernetesFailureMessage(code)).toBe(message);
  });

  it('keeps fixed kubeconfig validation and expired proof messages from backend responses', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        detail: '禁止认证插件、文件路径、冒充或未知身份配置',
      }), { status: 400 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        detail: '连接测试已失效或配置已变化，请重新测试',
      }), { status: 400 })));
    const payload = { kubeconfig: 'private-config', context: 'prod' };
    await expect(testKubernetesClusterDraft(payload)).rejects.toThrow('禁止认证插件、文件路径、冒充或未知身份配置');
    await expect(testKubernetesClusterDraft(payload)).rejects.toThrow('连接测试已失效或配置已变化，请重新测试');
  });

  it('does not reflect an arbitrary error detail or unknown error code', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: 'unsafe-upstream-secret',
    }), { status: 403 })));
    await expect(getKubernetesClusters()).rejects.toThrow('当前账号无权管理 Kubernetes 集群');
    expect(kubernetesFailureMessage('unsafe-upstream-secret', '检测未通过')).toBe('检测未通过');
  });
});
