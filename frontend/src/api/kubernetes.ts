import { ApiError, apiFetch } from './client';

export type KubernetesClusterLifecycle = 'active' | 'disabled' | 'archived';

export interface KubernetesCluster {
  id: string;
  name: string;
  endpoint: string | null;
  context: string | null;
  service_id: string | null;
  lifecycle: KubernetesClusterLifecycle;
  connection_status: string;
  authorization_status: string;
  version: string | null;
  capabilities: Record<string, unknown> | null;
  collection_interval_seconds: 0 | 60 | 300;
  server_cert_expires_at: string | null;
  ca_cert_expires_at: string | null;
  client_cert_expires_at: string | null;
  credential_revision: number;
  revision: number;
  last_tested_at: string | null;
  last_success_at: string | null;
  error_code: string | null;
  error_message: string | null;
  secret_configured: boolean;
  identity_hint: string | null;
  updated_at: string;
}

export interface KubernetesClusterList {
  total: number;
  items: KubernetesCluster[];
}

export interface KubernetesClusterCreateInput {
  name: string;
  endpoint?: string;
  kubeconfig: string;
  context: string;
  service_id?: string;
  test_id: string;
}

export interface KubernetesClusterUpdateInput {
  name?: string;
  service_id?: string | null;
  lifecycle?: KubernetesClusterLifecycle;
  expected_revision: number;
  reason?: string;
  collection_interval_seconds?: 0 | 60 | 300;
}

export interface KubernetesClusterDraftInput {
  kubeconfig: string;
  context: string;
  endpoint?: string;
  reason?: string;
}

export interface KubernetesCredentialRotateInput extends KubernetesClusterDraftInput {
  expected_revision: number;
  test_id: string;
  reason: string;
}

export interface KubernetesClusterTestResult {
  cluster_id?: string;
  test_id?: string | null;
  status: string;
  summary?: string;
  version?: string | null;
  metrics_capable?: boolean;
  server_cert_expires_at?: string | null;
  capabilities?: Record<string, unknown> | null;
  error_code?: string | null;
  error_message?: string | null;
  steps?: Array<{ name: string; status: string; code?: string | null; value?: string | null }>;
  cluster?: KubernetesCluster;
}

const endpoint = '/api/v1/kubernetes/clusters';
const failureMessages: Record<string, string> = {
  timeout: '连接超时，请检查网络与端口',
  network_error: '网络连接失败，请检查 API Server 地址与端口',
  connection_failed: '无法连接集群，请检查网络、端口和 TLS 证书',
  tls_failed: '服务端证书校验失败，请检查证书链和主机名',
  tls_certificate: '服务端证书校验失败，请检查证书链、主机名和有效期',
  tls_chain: '服务端证书链验证失败',
  tls_expired: '服务端证书尚未生效或已过期',
  tls_hostname: '服务端证书与 API Server 主机名不匹配',
  tls_error: 'TLS 连接或客户端证书验证失败',
  certificate_expired: '配置中的证书已过期',
  client_certificate_invalid: '客户端证书或私钥缺失',
  unauthorized: '集群认证失败，请更新凭据',
  authentication_failed: '集群认证失败，请更新凭据',
  upstream_unauthorized: '集群身份认证失败',
  upstream_forbidden: '集群身份缺少所需权限',
  upstream_throttled: '集群 API 正在限流，请稍后重试',
  upstream_unavailable: '集群 API 暂时不可用',
  forbidden: '集群读取权限不足，请检查只读授权',
  permission_denied: '集群读取权限不足，请检查只读授权',
  permission_checks_incomplete: '授权检查未完成，请检查身份和读取权限',
  authorization_incomplete: '自权限检查不可用或结果不完整，请检查命名空间权限',
  unsafe_permissions: '集群身份具有固定读取范围以外的权限，请收紧授权',
  identity_rejected: '请使用专用只读身份，勿使用默认或管理员身份',
  target_policy_unconfigured: '尚未配置 Kubernetes API 允许网段，请联系管理员',
  target_policy_invalid: 'Kubernetes API 目标策略配置无效，请联系管理员',
  target_policy_rejected: 'API Server 地址不在管理员批准的目标范围内',
  identity_incomplete: '集群未返回可验证的真实身份',
  identity_unavailable: '无法验证集群身份，请检查认证和发现接口权限',
  version_unavailable: '无法读取集群版本，请检查版本接口',
  invalid_version: '集群版本响应无效',
  unsupported_version: '集群版本响应无效或不受支持',
  invalid_metrics: 'Metrics API 返回格式无效',
  invalid_response: '集群响应格式无效',
  response_too_large: '检测响应超过大小限制',
  resolver_busy: 'DNS 解析繁忙，请稍后重试',
  dns_failed: 'API Server DNS 解析失败或结果无效',
  path_rejected: '检测请求超出固定白名单',
  target_rejected: 'API Server 目标或凭据配置不允许',
  redirect_rejected: '禁止 API Server 重定向',
  unexpected_status: '集群 API 响应状态异常',
  api_unavailable: '集群所需 API 不可用',
  resource_unsupported: '集群未提供所需资源接口',
  metrics_unavailable: '指标接口不可用',
  'selected context was not found': '所选 context 不存在，请检查 kubeconfig',
  'an explicit context is required': '请输入明确的 kubeconfig context',
  'endpoint does not match selected context': 'API Server 地址与所选 context 不一致',
  'API Server must be a permitted HTTPS endpoint': 'API Server 必须是允许访问的 HTTPS 地址',
  'cluster name already exists': '集群名称已存在',
  'cluster changed; refresh and retry': '集群配置已变化，请刷新后重试',
  'connection test has expired; test the cluster again': '连接测试已失效，请重新测试',
  'only a successful connection test can be saved': '请先完成连接测试',
  'credential test must pass before rotation': '请先测试新凭据并确认通过',
};
const fixedBackendMessages = new Set([
  'API Server 地址无效', 'API Server 地址不在允许的目标范围内', 'API Server 必须为允许的 HTTPS 地址',
  'kubeconfig 含重复键或非字符串键', 'kubeconfig 超过大小限制', 'kubeconfig 对象过多或包含别名',
  'kubeconfig 嵌套过深', 'kubeconfig YAML 格式无效', 'kubeconfig 必须为单个对象',
  'kubeconfig 缺少集群、上下文或身份', 'kubeconfig 列表条目无效', 'kubeconfig 条目名称重复或格式无效',
  'kubeconfig 内嵌证书数据无效', '证书无效、尚未生效或已过期', '客户端证书与私钥必须同时提供',
  '客户端证书与私钥不匹配或身份不允许', 'kubeconfig 类型或字段无效', '必须显式选择有效的 context',
  'context 包含不允许的配置', 'context 引用无效', 'context 引用不存在的集群或身份',
  '禁止代理、证书文件路径或未知集群配置', '禁止认证插件、文件路径、冒充或未知身份配置',
  '禁止跳过 TLS 验证', '缺少 API Server 地址', 'API Server 与所选 context 不一致', '静态 Token 格式无效',
  '请选择静态 Token 或内嵌客户端证书中的一种认证方式',
  '连接测试未通过，请修正后重试', '连接测试已失效或配置已变化，请重新测试',
  '集群凭据不可用，请轮换凭据', '保存的集群凭据无法读取，请轮换凭据',
  '集群已变化或名称已存在，请刷新后重试', '集群已变化，请刷新后重试',
  '关联业务服务不存在，请选择有效服务', '已归档集群不能轮换凭据', '集群已停用，启用后可测试连接',
  '集群参数无效，请检查名称、配置和修订号', '请提供 kubeconfig 和 context',
  '已接入集群测试只使用已保存配置',
  '集群目标允许范围未配置，请联系管理员', '集群目标策略配置无效，请联系管理员',
  'API Server 地址不在集群目标策略允许范围内',
  '预览或审批记录不存在', '预览或审批已处理或过期', '仅发起人可提交，审批需由另一位管理员确认',
  '集群配置或目标策略已变化，请重新预览', '受信 Chart 已变化或不可用，请重新预览',
  '受信 Chart 已变化，请重新预览', '集群预检已失效，请刷新快照后重新预览', '发起人不再具备管理员权限',
]);

export function kubernetesFailureMessage(code: string | null | undefined, fallback = '集群请求失败，请重试'): string {
  if (code && Object.hasOwn(failureMessages, code)) return failureMessages[code];
  return code && fixedBackendMessages.has(code) ? code : fallback;
}

export function kubernetesRequestError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof DOMException && ['AbortError', 'TimeoutError'].includes(error.name)) return failureMessages.timeout;
  return '集群请求失败，请检查网络后重试';
}

export async function request<T>(
  url: string, options?: RequestInit, fallback = 'Kubernetes 集群请求失败', timeout = 15_000,
): Promise<T> {
  const response = await apiFetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    signal: options?.signal
      ? AbortSignal.any([options.signal, AbortSignal.timeout(timeout)]) : AbortSignal.timeout(timeout),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown; code?: unknown } | null;
    const code = typeof body?.code === 'string' ? body.code : typeof body?.detail === 'string' ? body.detail : null;
    const statusMessages: Record<number, string> = {
      401: '登录已失效，请重新登录', 403: '当前账号无权管理 Kubernetes 集群',
      404: '集群不存在或已不可访问', 409: '集群配置已变化，请刷新后重试',
      422: '输入无效，请检查必填项、长度和测试凭据', 429: '操作过于频繁，请稍后重试',
    };
    throw new ApiError(kubernetesFailureMessage(code, statusMessages[response.status] ?? fallback), response.status, code);
  }
  return await response.json() as T;
}

export function getKubernetesClusters(params: {
  offset?: number;
  limit?: number;
  search?: string;
  status?: KubernetesClusterLifecycle;
} = {}): Promise<KubernetesClusterList> {
  const query = new URLSearchParams({
    offset: String(params.offset ?? 0),
    limit: String(params.limit ?? 50),
  });
  if (params.search?.trim()) query.set('search', params.search.trim());
  if (params.status) query.set('status', params.status);
  return request<KubernetesClusterList>(`${endpoint}?${query.toString()}`, undefined, 'Kubernetes 集群列表加载失败');
}

export function createKubernetesCluster(payload: KubernetesClusterCreateInput): Promise<KubernetesCluster> {
  return request<KubernetesCluster>(endpoint, {
    method: 'POST',
    body: JSON.stringify(payload),
  }, 'Kubernetes 集群接入失败，请检查 kubeconfig、context 和权限');
}

export function updateKubernetesCluster(id: string, payload: KubernetesClusterUpdateInput): Promise<KubernetesCluster> {
  return request<KubernetesCluster>(`${endpoint}/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }, 'Kubernetes 集群配置更新失败，请刷新后重试');
}

export function testKubernetesCluster(id: string, payload: { reason: string }): Promise<KubernetesClusterTestResult> {
  return request<KubernetesClusterTestResult>(`${endpoint}/${encodeURIComponent(id)}/tests`, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(40_000),
  }, 'Kubernetes 集群连接测试失败，请检查网络、证书和授权', 40_000);
}

export function testKubernetesClusterDraft(payload: KubernetesClusterDraftInput): Promise<KubernetesClusterTestResult> {
  return request<KubernetesClusterTestResult>('/api/v1/kubernetes/cluster-tests', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(40_000),
  }, 'Kubernetes 集群连接测试失败，请检查 kubeconfig、context 和权限', 40_000);
}

export function rotateKubernetesClusterCredential(id: string, payload: KubernetesCredentialRotateInput): Promise<KubernetesCluster> {
  return request<KubernetesCluster>(`${endpoint}/${encodeURIComponent(id)}/credential`, {
    method: 'PUT',
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(40_000),
  }, 'Kubernetes 凭据轮换失败，请重新测试 kubeconfig', 40_000);
}
