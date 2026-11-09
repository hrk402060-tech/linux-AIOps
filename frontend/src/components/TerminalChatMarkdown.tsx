import { isValidElement, ReactNode, useRef, useState } from 'react';
import { Button, Tooltip } from 'antd';
import { Check, Copy } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

function CodeBlock({ children }: { children?: ReactNode }) {
  const block = useRef<HTMLPreElement>(null);
  const [status, setStatus] = useState('复制代码');
  const copy = async () => {
    const text = block.current?.textContent ?? '';
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const input = document.createElement('textarea');
        input.value = text;
        input.style.position = 'fixed';
        input.style.opacity = '0';
        document.body.appendChild(input);
        input.select();
        try {
          if (!document.execCommand('copy')) throw new Error('Copy unavailable');
        } finally {
          input.remove();
        }
      }
      setStatus('已复制');
    } catch {
      setStatus('复制失败，请手动选择代码');
    }
  };
  const props = isValidElement<{ className?: string }>(children) ? children.props : null;
  const language = props?.className?.replace('language-', '') || '代码';
  return <div className="assistant-code-block"><div className="assistant-code-toolbar"><span>{language}</span><Tooltip title={status}><Button type="text" size="small" aria-label="复制代码" icon={status === '已复制' ? <Check size={14} /> : <Copy size={14} />} onClick={() => void copy()} /></Tooltip></div><pre ref={block}>{children}</pre></div>;
}

export function TerminalChatMarkdown({ content }: { content: string }) {
  return <div className="assistant-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
    a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
    img: ({ alt }) => <span>{alt || '图片'}</span>,
    table: ({ children }) => <div className="assistant-table-scroll"><table>{children}</table></div>,
  }}>{content}</ReactMarkdown></div>;
}
