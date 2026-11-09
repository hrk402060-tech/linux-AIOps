import { Alert, Button, Descriptions, Form, Input, Modal, QRCode, Spin } from 'antd';
import type { useUserMfa } from '../hooks/useUserMfa';

export function UserMfaModal({
  target, setup, recoveryCodes, loading, close, confirm,
}: ReturnType<typeof useUserMfa>) {
  return <Modal
    title={`设置 MFA${target ? `：${target.username}` : ''}`}
    open={target !== null}
    footer={null}
    onCancel={close}
    destroyOnHidden
  >
    {recoveryCodes ? <div>
      <Alert type="warning" showIcon message="恢复码只显示这一次，请由用户安全保存。" />
      <pre className="mfa-recovery-codes">{recoveryCodes.join('\n')}</pre>
      <Button block onClick={close}>完成</Button>
    </div> : <Spin spinning={loading}>
      {setup ? <><Alert type="info" showIcon message="请使用验证器扫描二维码完成绑定；无法扫码时可展开手动绑定信息。" />
        <div style={{ display: 'flex', justifyContent: 'center', padding: '20px 0 12px' }}>
          <QRCode type="svg" value={setup.otpauth_uri} size={220} aria-label="MFA 二维码" />
        </div>
        <details style={{ marginBottom: 16 }}>
          <summary style={{ cursor: 'pointer', color: '#1677ff' }}>无法扫码时手动绑定</summary>
          <Descriptions column={1} size="small" style={{ marginTop: 12 }} items={[
            { key: 'secret', label: '绑定密钥', children: <code>{setup.secret}</code> },
            { key: 'uri', label: '绑定地址', children: <code style={{ wordBreak: 'break-all' }}>{setup.otpauth_uri}</code> },
          ]} />
        </details>
        <Form layout="vertical" onFinish={(values) => void confirm(values)}>
          <Form.Item name="code" label="验证器验证码" rules={[{ required: true, len: 6, message: '请输入 6 位验证码' }]}>
            <Input autoComplete="one-time-code" inputMode="numeric" maxLength={6} />
          </Form.Item>
          <Button block type="primary" htmlType="submit" loading={loading}>确认启用</Button>
        </Form>
      </> : null}
    </Spin>}
  </Modal>;
}
