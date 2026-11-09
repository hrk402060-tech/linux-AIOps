const knownMessages: Record<string, string> = {
  'Authentication required': '登录已失效，请重新登录',
  'Asset not found': '服务器资产不存在，请刷新列表',
  'Credential not found': '连接凭据不存在，请重新选择',
  'Asset name already exists': '资产名称已存在，请使用其他名称',
  'Cross-site request rejected': '请求来源校验失败，请从平台页面重新操作',
  'Request origin is not allowed': '请求来源未被允许，请联系管理员检查访问地址',
  'Asset has immutable scan or terminal history': '资产包含需保留的扫描或终端历史，请归档资产',
};
const fieldLabels: Record<string, string> = {
  reason: '操作原因', confirmation: '确认文本', name: '名称', host: '服务器地址',
  credential_id: '连接凭据', asset_ids: '服务器资产', ssh_username: 'SSH 用户名',
  ssh_secret: 'SSH 密码或私钥', ssh_port: 'SSH 端口', scan_type: '扫描类型',
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function lookup(labels: Record<string, string>, key: string): string {
  return Object.hasOwn(labels, key) ? labels[key] : key;
}

function validationMessage(value: unknown): string | null {
  if (!isRecord(value)) return null;
  const location = Array.isArray(value.loc) ? value.loc.filter((part) => typeof part === 'string') : [];
  const field = location.filter((part) => !['body', 'query', 'path'].includes(part)).at(-1);
  const prefix = field ? lookup(fieldLabels, field) + '：' : '';
  const context = isRecord(value.ctx) ? value.ctx : {};
  if (value.type === 'string_too_short' && typeof context.min_length === 'number') {
    return prefix + `至少需要 ${context.min_length} 个字符`;
  }
  if (value.type === 'string_too_long' && typeof context.max_length === 'number') {
    return prefix + `最多允许 ${context.max_length} 个字符`;
  }
  if (value.type === 'missing') return prefix + '不能为空';
  if (typeof value.msg === 'string' && value.msg.trim()) {
    return prefix + lookup(knownMessages, value.msg);
  }
  return null;
}

export function apiErrorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail.trim()) return lookup(knownMessages, detail);
  if (Array.isArray(detail)) {
    const messages = detail.map(validationMessage).filter((message): message is string => message !== null);
    if (messages.length) return [...new Set(messages)].join('；');
  }
  return fallback;
}
