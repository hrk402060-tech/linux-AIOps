export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(input, {
    ...init,
    credentials: 'include',
    headers: init.headers,
    signal: init.signal ?? AbortSignal.timeout(10_000),
  });
  if (response.status === 401 && !init.signal?.aborted && String(input) !== '/api/v1/auth/login') {
    window.dispatchEvent(new Event('sentinel:session-expired'));
  }
  return response;
}

export async function parseApiResponse<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    const detail = body?.detail ?? fallback;
    const mfaMessages: Record<string, string> = {
      mfa_required: '请输入 MFA 验证码或一次性恢复码',
      mfa_invalid: 'MFA 验证码错误或已使用',
      mfa_setup_required: '当前账号尚未绑定 MFA，请联系管理员完成绑定后登录',
    };
    const code = Object.hasOwn(mfaMessages, detail) ? detail : null;
    throw new ApiError(code ? mfaMessages[code] : detail, response.status, code);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}
