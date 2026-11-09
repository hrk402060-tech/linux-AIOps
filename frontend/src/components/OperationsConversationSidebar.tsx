import { Button, Empty, Input, Spin } from 'antd';
import { Archive, ChevronLeft, MessageSquarePlus, Pencil, RefreshCw } from 'lucide-react';
import type { OpsAssistantConversationSummary } from '../api/ops-assistant';

interface Props {
  items: OpsAssistantConversationSummary[];
  activeId: string | null;
  archived: boolean;
  loading: boolean;
  error: string;
  editingId: string | null;
  editingTitle: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  onToggleArchived: () => void;
  onRetry: () => void;
  onEdit: (item: OpsAssistantConversationSummary) => void;
  onEditTitle: (title: string) => void;
  onSaveTitle: () => void;
  onCancelEdit: () => void;
  hasMore: boolean;
  moreLoading: boolean;
  onLoadMore: () => void;
}

export function OperationsConversationSidebar({
  items, activeId, archived, loading, error, editingId, editingTitle, onSelect, onNew,
  onToggleArchived, onRetry, onEdit, onEditTitle, onSaveTitle, onCancelEdit, hasMore, moreLoading, onLoadMore,
}: Props) {
  return <aside className="operations-assistant-conversations" aria-label="会话列表">
    <header>
      <div><strong>运维会话</strong><small>{archived ? '已归档历史' : '最近会话'}</small></div>
      <Button aria-label="新建会话" icon={<MessageSquarePlus size={16} />} onClick={onNew}>新建</Button>
    </header>
    <div className="operations-assistant-conversation-actions">
      <Button type={archived ? 'default' : 'primary'} size="small" icon={archived ? <ChevronLeft size={14} /> : <Archive size={14} />} onClick={onToggleArchived}>
        {archived ? '返回当前' : '已归档'}
      </Button>
    </div>
    {loading ? <div className="operations-assistant-list-status"><Spin size="small" />加载会话…</div> : error ? <div className="operations-assistant-list-status error"><span>{error}</span><Button type="link" icon={<RefreshCw size={14} />} onClick={onRetry}>重试加载会话</Button></div> : items.length ? <div className="operations-assistant-conversation-list">
      {items.map((item) => editingId === item.id ? <div className="operations-assistant-conversation-edit" key={item.id}>
        <Input aria-label="会话标题" maxLength={80} value={editingTitle} onChange={(event) => onEditTitle(event.target.value)} />
        <div><Button size="small" type="primary" onClick={onSaveTitle}>保存名称</Button><Button size="small" onClick={onCancelEdit}>取消</Button></div>
      </div> : <div className={`operations-assistant-conversation-item ${activeId === item.id ? 'active' : ''}`} key={item.id}>
        <button type="button" aria-label={`打开会话 ${item.title}`} aria-pressed={activeId === item.id} onClick={() => onSelect(item.id)}>
          <strong>{item.title || '未命名会话'}</strong><small>{formatTime(item.updated_at)}{item.asset_id ? ' · 已关联服务器' : ''}</small>
        </button>
        {activeId === item.id ? <Button type="text" size="small" aria-label="重命名会话" icon={<Pencil size={14} />} onClick={() => onEdit(item)} /> : null}
      </div>)}
    </div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无会话" />}
    {hasMore ? <Button type="link" loading={moreLoading} onClick={onLoadMore}>加载更多会话</Button> : null}
  </aside>;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
