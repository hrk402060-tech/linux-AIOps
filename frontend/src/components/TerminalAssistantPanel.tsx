import { KeyboardEvent, useEffect, useRef, useState } from 'react';
import { Button, Input, message, Tag, Tooltip } from 'antd';
import { Bot, Send, TerminalSquare, Trash2 } from 'lucide-react';

import { chatTerminalAssistant } from '../api/terminal';
import type { TerminalAssistantChatMessage } from '../types/terminal';
import { TerminalChatMarkdown } from './TerminalChatMarkdown';
import './TerminalAssistantPanel.css';

type ChatMessage = TerminalAssistantChatMessage & { id: string };

type Props = {
  sessionId: string | null; context: string; connected: boolean;
  // Old callers may provide these props; the read-only assistant never consumes them.
  onExecute?: (commandId: string) => void; executedCommandId?: string | null;
};

export function TerminalAssistantPanel({ sessionId, context, connected }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [question, setQuestion] = useState('');
  const [engine, setEngine] = useState('');
  const [messageApi, holder] = message.useMessage();
  const chat = useRef<HTMLDivElement>(null);
  const sequence = useRef(0);
  const submitting = useRef(false);
  const followLatest = useRef(true);

  useEffect(() => {
    if (chat.current && followLatest.current) chat.current.scrollTop = chat.current.scrollHeight;
  }, [messages, loading]);

  const analyze = async (analyzeOutput = false) => {
    if (!sessionId || !connected || loading || submitting.current) return;
    const prompt = analyzeOutput ? '请分析当前终端输出' : question.trim();
    if (!prompt || (analyzeOutput && !context.trim())) return;
    submitting.current = true;
    setLoading(true);
    followLatest.current = true;
    const id = `question-${++sequence.current}`;
    const history: TerminalAssistantChatMessage[] = messages.slice(-20).map(({ role, content }) => ({ role, content: content.slice(0, 4000) }));
    setMessages((current) => [...current, { id, role: 'user', content: prompt }]);
    try {
      const result = await chatTerminalAssistant(sessionId, 'analysis_only', prompt, context, history);
      setEngine(result.engine);
      setMessages((current) => [...current,
        { id: `${id}-answer`, role: 'assistant', content: result.reply },
      ]);
      if (!analyzeOutput) setQuestion('');
    } catch (error) {
      setMessages((current) => current.filter((item) => item.id !== id));
      messageApi.error(error instanceof Error ? error.message : '分析失败');
    } finally {
      submitting.current = false;
      setLoading(false);
    }
  };

  const handleQuestionKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (!loading) void analyze();
    }
  };

  const clearConversation = () => {
    if (submitting.current || loading) return;
    setQuestion('');
    setMessages([]);
    setEngine('');
  };

  return <aside className="terminal-assistant">{holder}
    <header className="terminal-assistant-heading"><span><Bot size={19} /></span><div className="assistant-heading-copy"><strong>AI 运维助手</strong><small title={engine}>{engine.replace('openai-compatible:', '') || (connected ? 'SSH 已连接' : '等待连接')}</small></div><Tooltip title="清空对话"><Button type="text" icon={<Trash2 size={16} />} aria-label="清空对话" disabled={loading || !messages.length} onClick={clearConversation} /></Tooltip></header>
    <div className="assistant-mode"><Tag color="blue">只读分析</Tag></div>
    <div className="assistant-chat" ref={chat} role="log" aria-label="运维助手对话记录" aria-busy={loading} onScroll={() => { const el = chat.current; if (el) followLatest.current = el.scrollHeight - el.scrollTop - el.clientHeight < 64; }}>
      {messages.length ? messages.map((turn) => <article className={`assistant-chat-message ${turn.role}`} key={turn.id}>
        <span>{turn.role === 'user' ? '你' : <><Bot size={14} /> AI</>}</span>
        {turn.role === 'user' ? <p>{turn.content}</p> : <TerminalChatMarkdown content={turn.content} />}
      </article>) : <div className="assistant-chat-empty"><Bot size={28} /><strong>{connected ? '有什么需要处理？' : '等待 SSH 连接'}</strong></div>}
      {loading && submitting.current ? <div className="assistant-thinking" role="status"><span />正在思考…</div> : null}
    </div>
    <footer className="assistant-composer"><Input.TextArea aria-label="向终端 AI 提问" maxLength={4000} value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={handleQuestionKeyDown} placeholder={connected ? '输入运维问题…' : '连接服务器后提问'} autoSize={{ minRows: 2, maxRows: 5 }} disabled={!connected || loading} /><div className="assistant-composer-actions"><Tooltip title="分析终端输出"><Button type="text" icon={<TerminalSquare size={17} />} aria-label="分析终端输出" disabled={!connected || loading || !context.trim()} onClick={() => void analyze(true)} /></Tooltip><Button type="primary" icon={<Send size={15} />} loading={loading} disabled={!connected || !question.trim()} onClick={() => void analyze()}>发送</Button></div></footer>
  </aside>;
}
