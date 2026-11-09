export const PASSWORD_POLICY_MESSAGE = '密码至少 8 位，且必须包含英文字母、数字和特殊字符';

export function isValidPassword(value: string): boolean {
  return value.length >= 8
    && value.length <= 128
    && /[A-Za-z]/.test(value)
    && /[0-9]/.test(value)
    && /[^A-Za-z0-9\s]/.test(value);
}

export function validatePassword(_: unknown, value?: string): Promise<void> {
  if (!value || isValidPassword(value)) return Promise.resolve();
  return Promise.reject(new Error(PASSWORD_POLICY_MESSAGE));
}
