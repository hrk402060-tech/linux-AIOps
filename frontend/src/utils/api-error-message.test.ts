import { describe, expect, it } from 'vitest';
import { apiErrorMessage } from './api-error-message';

const fallback = '请求失败，请稍后重试';
describe('apiErrorMessage', () => {
  it.each(['constructor', '__proto__', 'toString'])('preserves reserved dictionary key %s as text', (key) => {
    expect(apiErrorMessage(key, fallback)).toBe(key);
    expect(apiErrorMessage([{ loc: ['body', key], type: 'value_error', msg: key }], fallback)).toBe(`${key}：${key}`);
  });
  it.each([
    ['Authentication required', '登录已失效，请重新登录'],
    ['Asset not found', '服务器资产不存在，请刷新列表'],
    ['Credential not found', '连接凭据不存在，请重新选择'],
    ['Asset name already exists', '资产名称已存在，请使用其他名称'],
    ['Cross-site request rejected', '请求来源校验失败，请从平台页面重新操作'],
    ['Request origin is not allowed', '请求来源未被允许，请联系管理员检查访问地址'],
  ])('translates known business error %s', (detail, expected) => {
    expect(apiErrorMessage(detail, fallback)).toBe(expected);
  });
  it.each(['删除确认文本不匹配', 'New vendor-specific failure: CVE-2026-1234'])('retains unrecognized details: %s', (detail) => {
    expect(apiErrorMessage(detail, fallback)).toBe(detail);
  });
  it.each([null, undefined, {}, '', 42])('uses a readable fallback for invalid detail %s', (detail) => {
    expect(apiErrorMessage(detail, fallback)).toBe(fallback);
  });
  it('explains a structured minimum-length error in Chinese', () => {
    expect(apiErrorMessage([{ loc: ['body', 'reason'], type: 'string_too_short',
      msg: 'String should have at least 3 characters', ctx: { min_length: 3 } }], fallback))
      .toBe('操作原因：至少需要 3 个字符');
  });
  it('explains structured maximum length and missing confirmation without exposing raw English', () => {
    expect(apiErrorMessage([
      { loc: ['body', 'reason'], type: 'string_too_long', msg: 'String too long', ctx: { max_length: 500 } },
      { loc: ['body', 'confirmation'], type: 'missing', msg: 'Field required' },
    ], fallback)).toBe('操作原因：最多允许 500 个字符；确认文本：不能为空');
  });
  it('preserves unfamiliar structured validation details and field location', () => {
    expect(apiErrorMessage([{ loc: ['body', 'host'], type: 'value_error',
      msg: 'Address denied by policy' }], fallback)).toBe('服务器地址：Address denied by policy');
  });
  it('does not render malformed objects as object Object', () => {
    expect(apiErrorMessage([null, { loc: ['body', 'reason'] }], fallback)).toBe(fallback);
  });
});
