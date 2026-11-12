import { useEffect, useRef, useState } from 'react';
import { Button, Input, Select, Spin, Tag, message } from 'antd';
import { Archive, ArchiveRestore, Bot, CheckCircle2, CircleAlert, ClipboardCheck, Send, ShieldCheck } from 'lucide-react';

import { getAssets } from '../api/assets';
import type { Asset } from '../types/asset';
import { TerminalChatMarkdown } from '../components/TerminalChatMarkdown';
import { OperationsConversationSidebar } from '../components/OperationsConversationSidebar';
import { useOperationsConversations } from './useOperationsConversations';
import type { OpsAssistantMessage, OpsAssistantChatResponse } from '../api/ops-assistant';
import './operations-assistant.css';

interface Props { userId?: string; initialAssetId?: string; initialContext?: string; }

export function OperationsAssistantPage({ userId = '', initialAssetId, initialContext }: Props) {
  return <OperationsAssistantWorkspace key={userId} initialAssetId={initialAssetId} initialContext={initialContext} />;
}

function OperationsAssistantWorkspace({ initialAssetId, initialContext }: Pick<Props, 'initialAssetId' | 'initialContext'>) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [assetsLoading, setAssetsLoading] = useState(true);
  const [messageApi, holder] = message.useMessage();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');
  const assistant = useOperationsConversations({ restoreLatest: !initialAssetId && !initialContext });
  const initialApplied = useRef(false);

  useEffect(() => {
    let active = true;
    setAssetsLoading(true);
    const expire = () => { active = false; setAssets([]); setEditingId(null); setEditingTitle(''); messageApi.destroy(); };
    window.addEventListener('sentinel:session-expired', expire);
    getAssets('enabled').then((items) => {
      if (active) setAssets(items);
    }).catch((error) => {
      if (active) messageApi.warning(error instanceof Error ? error.message : '服务器资产加载失败，可继续进行通用问答');
    }).finally(() => { if (active) setAssetsLoading(false); });
    return () => { active = false; window.removeEventListener('sentinel:session-expired', expire); };
  }, [messageApi]);
  useEffect(() => {
    if (initialApplied.current || assetsLoading || (!initialAssetId && !initialContext)) return;
    initialApplied.current = true;
    assistant.newDraft();
    if (initialAssetId && assets.some((asset) => asset.id === initialAssetId)) assistant.setAssetId(initialAssetId);
    if (initialContext) assistant.setContext(initialContext);
  }, [assetsLoading, assets, initialAssetId, initialContext]);
  const saveTitle = async () => {
    const title = editingTitle.trim();
    if (!title) return;
    if (await assistant.update({ title })) { setEditingId(null); setEditingTitle(''); }
  };
  const activeMessages = assistant.active?.messages ?? [];
  const engine = [...activeMessages].reverse().find((item) => item.analysis)?.analysis?.engine;
  const canSend = !assistant.locked && !assistant.sending && !assistant.detailLoading && !assistant.listLoading && !assistant.archived && !assistant.active?.archived && Boolean(assistant.question.trim());

  return <section className="operations-assistant-page">{holder}
    <div className="page-heading">
      <div><p className="eyebrow">只读诊断与排障建议</p><h1>AI 运维助手</h1></div>
      {assistant.active ? <Button aria-label={assistant.active.archived ? '恢复会话' : '归档会话'} icon={assistant.active.archived ? <ArchiveRestore size={16} /> : <Archive size={16} />} disabled={assistant.sending || assistant.detailLoading || assistant.locked} onClick={() => void assistant.update({ archived: !assistant.active?.archived })}>{assistant.active.archived ? '恢复会话' : '归档会话'}</Button> : null}
    </div>
    <div className="operations-assistant-safety">
      <span><ShieldCheck size={19} /><strong>只读分析</strong><small>AI 不会删除、修改、安装、部署或执行命令</small></span>
      <span><CircleAlert size={19} /><strong>人工确认边界</strong><small>检查命令仅供管理员人工确认后使用</small></span>
      <span><Bot size={19} /><strong>{engine || '本地运维推理'}</strong><small>上下文先脱敏，再交给模型分析</small></span>
    </div>
    <div className="operations-assistant-layout">
      <OperationsConversationSidebar
        items={assistant.items} activeId={assistant.active?.id ?? null} archived={assistant.archived}
        loading={assistant.listLoading} error={assistant.listError} editingId={editingId} editingTitle={editingTitle}
        onSelect={(id) => { setEditingId(null); void assistant.open(id); }} onNew={() => { setEditingId(null); assistant.newDraft(); }} onToggleArchived={() => { setEditingId(null); void assistant.reload(!assistant.archived); }}
        onRetry={() => void assistant.reload(assistant.archived, true)} onEdit={(item) => { setEditingId(item.id); setEditingTitle(item.title); }}
        onEditTitle={setEditingTitle} onSaveTitle={() => void saveTitle()} onCancelEdit={() => setEditingId(null)}
        hasMore={assistant.items.length < assistant.total} moreLoading={assistant.moreLoading} onLoadMore={() => void assistant.loadMoreConversations()}
      />
      <main className="operations-assistant-main">
        <div className="operations-assistant-context">
          <div className="operations-assistant-context-field">
            <label htmlFor="operations-assistant-asset">关联服务器（可选）</label>
            <Select id="operations-assistant-asset" aria-label="关联服务器" allowClear showSearch optionFilterProp="label" loading={assetsLoading} disabled={Boolean(assistant.active) || assistant.locked} placeholder="通用问答，不绑定服务器" value={assistant.assetId} onChange={assistant.setAssetId} options={assets.map((asset) => ({ value: asset.id, label: `${asset.name} · ${asset.host}` }))} />
          </div>
          <div className="operations-assistant-context-field">
            <label htmlFor="operations-assistant-evidence">补充只读证据（可选）</label>
            <Input.TextArea id="operations-assistant-evidence" aria-label="补充只读证据" maxLength={12000} value={assistant.context} onChange={(event) => assistant.setContext(event.target.value)} placeholder="粘贴脱敏后的日志、监控指标或错误信息；不要输入密码、令牌和私钥" autoSize={{ minRows: 2, maxRows: 5 }} disabled={Boolean(assistant.active) || assistant.sending || assistant.locked} />
          </div>
          <div className="operations-assistant-context-status">
            <Tag color={assistant.assetId ? 'blue' : 'default'}>{assistant.assetId ? '已关联资产' : '通用问答'}</Tag>
            <small>{assistant.active ? (assistant.active.archived ? '此会话已归档，仅可查看' : '服务器上下文已固定') : '连接服务器后可继续补充现场证据'}</small>
          </div>
        </div>
        {assistant.error ? <div className="operations-assistant-error" role="alert">{assistant.error}</div> : null}
        <div className="operations-assistant-workspace">
          <div className="operations-assistant-chat" role="log" aria-label="AI 运维助手对话记录" aria-busy={assistant.sending}>
            {assistant.detailLoading ? <div className="operations-assistant-empty"><Spin /><span>正在恢复会话…</span></div> : activeMessages.length ? <>
              {assistant.active && activeMessages.length < assistant.active.total_messages ? <Button size="small" onClick={() => void assistant.loadMoreMessages()} loading={assistant.moreLoading}>加载更早消息</Button> : null}
              {activeMessages.map((turn) => <MessageTurn key={turn.id} turn={turn} />)}
            </> : <div className="operations-assistant-empty"><Bot size={36} /><strong>{assistant.locked ? '会话已失效' : assistant.active?.archived ? '归档会话只读查看' : '描述你遇到的运维问题'}</strong><span>{assistant.locked ? '请重新登录后继续' : '例如：分析这台服务器负载升高的可能原因，并给出只读检查与验证步骤'}</span></div>}
            {assistant.sending ? <div className="operations-assistant-thinking" role="status"><Spin size="small" />正在分析，只读请求处理中…</div> : null}
          </div>
          <footer className="operations-assistant-composer">
            <Input.TextArea aria-label="向 AI 运维助手提问" maxLength={4000} value={assistant.question} onChange={(event) => assistant.setQuestion(event.target.value)} onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void assistant.send(); }
            }} placeholder={assistant.archived ? '归档会话不可继续提问' : '输入 Linux、Kubernetes、Docker 或监控告警问题…'} autoSize={{ minRows: 2, maxRows: 5 }} disabled={assistant.locked || assistant.archived || assistant.detailLoading || assistant.listLoading || assistant.sending} />
            <div><small>Enter 发送，Shift + Enter 换行</small><Button type="primary" icon={<Send size={15} />} loading={assistant.sending} disabled={!canSend} onClick={() => void assistant.send()}>发送</Button></div>
          </footer>
        </div>
      </main>
    </div>
  </section>;
}

function MessageTurn({ turn }: { turn: OpsAssistantMessage }) {
  return <article className={`operations-assistant-message ${turn.role} ${turn.status}`}><span className="operations-assistant-message-role">{turn.role === 'user' ? '你' : <><Bot size={14} /> AI 运维助手</>}</span>
    {turn.role === 'user' ? <><p>{turn.content}</p>{turn.status === 'failed' ? <small className="operations-assistant-message-error">回答失败：{turn.error_message ?? '请求失败'}</small> : turn.status === 'pending' ? <small>正在等待回答</small> : null}</> : <AssistantAnswer turn={turn} />}
    <time className="operations-assistant-message-time" dateTime={turn.created_at}>{new Date(turn.created_at).toLocaleString('zh-CN')}</time></article>;
}

function AssistantAnswer({ turn }: { turn: OpsAssistantMessage }) {
  const analysis = turn.analysis as OpsAssistantChatResponse | null;
  return <div className="operations-assistant-answer"><TerminalChatMarkdown content={turn.content} />
    {analysis ? <div className="operations-assistant-evidence">
      <section><h3><CircleAlert size={15} />判断</h3><p>{analysis.diagnosis}</p></section>
      <section><h3><ClipboardCheck size={15} />排障计划</h3><ol>{analysis.plan.map((item) => <li key={item}>{item}</li>)}</ol></section>
      <section><h3><CheckCircle2 size={15} />验证步骤</h3><ol>{analysis.validation_steps.map((item) => <li key={item}>{item}</li>)}</ol></section>
      {analysis.read_only_checks.length ? <section><h3><ShieldCheck size={15} />只读检查建议</h3><ul>{analysis.read_only_checks.map((item) => <li key={`${item.command}-${item.purpose}`}><code>{item.command}</code><span>{item.purpose}；验证：{item.validation_step}</span></li>)}</ul></section> : null}
      {analysis.context_sources.length ? <div className="operations-assistant-sources"><strong>本次依据</strong>{analysis.context_sources.map((source) => <Tag key={source}>{source}</Tag>)}</div> : null}
    </div> : null}
  </div>;
}
