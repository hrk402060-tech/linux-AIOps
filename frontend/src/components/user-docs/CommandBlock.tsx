import { useState } from 'react';
import { Button } from 'antd';
import { Check, Copy } from 'lucide-react';

export function CommandBlock({ title, text }: { title: string; text: string }) {
  const [status, setStatus] = useState('');
  const copy = async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const input = document.createElement('textarea');
        const focused = document.activeElement;
        input.value = text;
        input.style.position = 'fixed';
        input.style.opacity = '0';
        document.body.append(input);
        input.select();
        try {
          if (!document.execCommand('copy')) throw new Error('Copy unavailable');
        } finally {
          input.remove();
          if (focused instanceof HTMLElement) focused.focus({ preventScroll: true });
        }
      }
      setStatus('已复制');
    } catch {
      setStatus('复制失败，请手动选择命令');
    }
  };
  return <div className="user-doc-command">
    <div className="user-doc-command-heading">
      <span>{title}</span>
      <Button type="text" size="small" aria-label={`复制命令：${title}`}
        icon={status === '已复制' ? <Check size={14} /> : <Copy size={14} />}
        onClick={() => void copy()}>{status === '已复制' ? '已复制' : '复制'}</Button>
    </div>
    <pre><code>{text}</code></pre>
    {status.startsWith('复制失败') && <p role="status">{status}</p>}
  </div>;
}
