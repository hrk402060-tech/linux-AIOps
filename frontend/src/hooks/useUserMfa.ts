import { useState } from 'react';
import { Modal } from 'antd';
import {
  confirmUserMfa, resetUserMfa, setupUserMfa,
  type AuthUser, type MfaSetup,
} from '../api/auth';

interface UserMfaOptions {
  currentUser: AuthUser;
  loadUsers: () => Promise<void>;
  messages: {
    success: (content: string) => void;
    error: (content: string) => void;
  };
  onLogout?: () => void;
}

export function useUserMfa({ currentUser, loadUsers, messages, onLogout }: UserMfaOptions) {
  const [target, setTarget] = useState<AuthUser | null>(null);
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(false);
  const close = () => {
    setTarget(null);
    setSetup(null);
    setRecoveryCodes(null);
  };
  const open = async (user: AuthUser) => {
    setTarget(user);
    setSetup(null);
    setRecoveryCodes(null);
    setLoading(true);
    try { setSetup(await setupUserMfa(user.id)); }
    catch (error) {
      setTarget(null);
      messages.error(error instanceof Error ? error.message : 'MFA 设置失败');
    } finally { setLoading(false); }
  };
  const confirm = async ({ code }: { code: string }) => {
    if (!target) return;
    setLoading(true);
    try {
      const result = await confirmUserMfa(target.id, code);
      setRecoveryCodes(result.recovery_codes);
      messages.success('MFA 已启用');
      await loadUsers();
    } catch (error) { messages.error(error instanceof Error ? error.message : 'MFA 验证失败'); }
    finally { setLoading(false); }
  };
  const reset = (user: AuthUser) => {
    Modal.confirm({
      title: `重置 ${user.username} 的 MFA？`,
      content: `重置后原恢复码、登录会话和 SSH 连接立即失效；启用强制 MFA 时，该用户需由管理员重新绑定后才能登录。${user.id === currentUser.id ? '当前账号也将退出登录，请确认有其他管理员可协助绑定。' : ''}`,
      okText: '确认重置', cancelText: '取消',
      onOk: async () => {
        try {
          await resetUserMfa(user.id);
          messages.success('MFA 已重置');
          if (user.id === currentUser.id) {
            if (onLogout) onLogout();
            else window.dispatchEvent(new Event('sentinel:session-expired'));
          } else await loadUsers();
        } catch (error) { messages.error(error instanceof Error ? error.message : 'MFA 重置失败'); }
      },
    });
  };
  return { target, setup, recoveryCodes, loading, close, open, confirm, reset };
}
