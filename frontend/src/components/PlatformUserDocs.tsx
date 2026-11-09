import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Empty, Input } from 'antd';
import type { InputRef } from 'antd';
import { ArrowRight, BookOpen, Search } from 'lucide-react';
import type { PageKey } from '../App';
import { ReadOnlyDiagnosticsHelp } from './ReadOnlyDiagnosticsHelp';
import { docGroups, matchesDoc, userDocs } from './user-docs';
import type { UserDocEntry, UserDocGroup } from './user-docs/types';
import { CommandBlock } from './user-docs/CommandBlock';

const groupLabel = (group: UserDocGroup) =>
  docGroups.find((item) => item.key === group)?.label ?? group;

function DocIntro() {
  return <section id="doc-overview" className="user-doc-overview">
    <h2>系统介绍</h2>
    <p>Linux 智能综合运维平台将服务器资产、监控告警、人工运维与安全检查关联起来。
      下方按实际功能说明用途、准备条件、操作和适用命令。</p>
    <h2>整体流程</h2>
    <div className="help-flow">{['资产登记', '监控发现', '只读定位', '通知管理员',
      '人工确认处理', '验证恢复', '审计留痕'].map((step, index) =>
      <span key={step}>{index > 0 ? '→ ' : ''}{step}</span>)}</div>
    <p>命令中的 SERVER_IP、PUBLIC_KEY、账号、路径及命名空间均须替换为实际值。
      命令仅供管理员参考，本页不会执行命令、授予权限或连接目标。</p>
  </section>;
}

function DocArticle({ entry, onNavigate }: {
  entry: UserDocEntry; onNavigate: (page: PageKey) => void;
}) {
  return <article className="user-doc-article" aria-label={entry.title}>
    <div className="user-doc-breadcrumb">{groupLabel(entry.group)} / 使用文档</div>
    <h1>{entry.title}</h1>
    <p className="user-doc-lead">{entry.purpose}</p>
    {entry.id === 'dashboard' && <DocIntro />}
    <section id="doc-purpose">
      <h2>作用</h2>
      <p>{entry.purpose}</p>
    </section>
    <section id="doc-requirements">
      <h2>需要准备</h2>
      <ul>{entry.requirements.map((text) => <li key={text}>{text}</li>)}</ul>
    </section>
    <section id="doc-steps">
      <h2>使用步骤</h2>
      <ol>{entry.steps.map((text) => <li key={text}>{text}</li>)}</ol>
    </section>
    <section id="doc-commands">
      <h2>执行命令</h2>
      {entry.commands?.map((command) =>
        <CommandBlock key={command.title} title={command.title} text={command.text} />) ?? <p>
          {entry.id === 'diagnostics'
            ? '按下方连接类型准备权限并执行验证；配置仅由目标管理员手动操作。'
            : '本功能在页面中操作，无需执行服务器命令。'}
        </p>}
      {entry.id === 'diagnostics' && <ReadOnlyDiagnosticsHelp embedded
        onOpenAlerts={() => onNavigate('alerts')} />}
    </section>
    <section id="doc-notes">
      <h2>注意事项</h2>
      <ul>{entry.notes.map((text) => <li key={text}>{text}</li>)}</ul>
    </section>
    <Button icon={<ArrowRight size={15} />} onClick={() => onNavigate(entry.page)}>
      打开相关功能
    </Button>
  </article>;
}

function DocSidebar({ activeGroup, selectedId, searching, entries, onSelect, onGroup }: {
  activeGroup: UserDocGroup;
  selectedId: string;
  searching: boolean;
  entries: UserDocEntry[];
  onSelect: (entry: UserDocEntry) => void;
  onGroup: (group: UserDocGroup) => void;
}) {
  return <aside className="user-docs-sidebar" aria-label="文档目录">
    <div className="user-docs-sidebar-title"><BookOpen size={16} />文档目录</div>
    {searching ? <div className="user-doc-search-results">
      <p>搜索结果 · {entries.length} 项</p>
      {entries.map((entry) => <button key={entry.id} type="button"
        aria-label={entry.title}
        className={`user-doc-nav-item${selectedId === entry.id ? ' is-active' : ''}`}
        aria-current={selectedId === entry.id ? 'page' : undefined}
        onClick={() => onSelect(entry)}>
        <span>{entry.title}</span><small>{groupLabel(entry.group)}</small>
      </button>)}
    </div> : docGroups.map((group) => {
      const groupEntries = userDocs.filter((entry) => entry.group === group.key);
      return <section key={group.key} className={`user-doc-nav-group${activeGroup === group.key ? ' is-active' : ''}`}>
        <button type="button" className={`user-doc-group-button${activeGroup === group.key ? ' is-active' : ''}`}
          aria-label={group.label}
          aria-expanded={activeGroup === group.key} onClick={() => onGroup(group.key)}>
          {group.label}<small>{groupEntries.length}</small>
        </button>
        {activeGroup === group.key && <div className="user-doc-nav-items">
          {groupEntries.map((entry) => <button key={entry.id} type="button"
            className={`user-doc-nav-item${selectedId === entry.id ? ' is-active' : ''}`}
            aria-current={selectedId === entry.id ? 'page' : undefined}
            onClick={() => onSelect(entry)}>{entry.title}</button>)}
        </div>}
      </section>;
    })}
  </aside>;
}

function DocToc({ entry }: { entry: UserDocEntry }) {
  const sections = [
    ...(entry.id === 'dashboard' ? [{ id: 'doc-overview', label: '系统介绍' }] : []),
    { id: 'doc-purpose', label: '作用' },
    { id: 'doc-requirements', label: '需要准备' },
    { id: 'doc-steps', label: '使用步骤' },
    { id: 'doc-commands', label: '执行命令' },
    { id: 'doc-notes', label: '注意事项' },
  ];
  return <aside className="user-docs-toc" aria-label="本文目录">
    <p>本页目录</p>
    <nav>{sections.map((section) =>
      <a key={section.id} href={`#${section.id}`}>{section.label}</a>)}</nav>
  </aside>;
}

function DocTopNav({ activeGroup, selectedId, onGroup, onIntro }: {
  activeGroup: UserDocGroup; selectedId: string;
  onGroup: (group: UserDocGroup) => void; onIntro: () => void;
}) {
  const intro = activeGroup === 'workspace' && selectedId === 'dashboard';
  const sections = [
    { label: '快速开始', active: intro, select: onIntro },
    { label: '功能指南', active: activeGroup === 'workspace' && !intro,
      select: () => onGroup('workspace') },
    { label: '运维流程', active: activeGroup === 'monitoring', select: () => onGroup('monitoring') },
    { label: '安全与权限', active: activeGroup === 'security' || activeGroup === 'system',
      select: () => onGroup('security') },
  ];
  return <nav className="user-docs-top-nav" aria-label="文档导航">
    {sections.map((item) => <button key={item.label} type="button"
      className={item.active ? 'is-active' : ''}
      aria-current={item.active ? 'page' : undefined} onClick={item.select}>{item.label}</button>)}
  </nav>;
}

export function PlatformUserDocs({ onNavigate }: { onNavigate: (page: PageKey) => void }) {
  const [group, setGroup] = useState<UserDocGroup>('workspace');
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState('dashboard');
  const [navigation, setNavigation] = useState(0);
  const searchRef = useRef<InputRef>(null);
  const articleRef = useRef<HTMLDivElement>(null);
  const searching = query.trim().length > 0;
  const entries = useMemo(() => userDocs.filter((entry) =>
    searching ? matchesDoc(entry, query) : entry.group === group), [group, query, searching]);
  const selectedEntry = entries.find((entry) => entry.id === selectedId) ?? entries[0];

  useEffect(() => {
    if (selectedEntry && selectedEntry.id !== selectedId) setSelectedId(selectedEntry.id);
  }, [selectedEntry, selectedId]);
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener('keydown', focusSearch);
    return () => window.removeEventListener('keydown', focusSearch);
  }, []);
  useEffect(() => {
    if (navigation > 0) articleRef.current?.scrollIntoView?.({ block: 'start' });
  }, [navigation]);
  const resetArticle = () => {
    if (window.location.hash) {
      window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search);
    }
    setNavigation((value) => value + 1);
  };

  const selectEntry = (entry: UserDocEntry) => {
    setSelectedId(entry.id);
    setGroup(entry.group);
    resetArticle();
  };
  const selectGroup = (nextGroup: UserDocGroup) => {
    setQuery('');
    setGroup(nextGroup);
    const first = userDocs.find((entry) =>
      entry.group === nextGroup && (nextGroup !== 'workspace' || entry.id !== 'dashboard'));
    if (first) setSelectedId(first.id);
    resetArticle();
  };
  const selectIntro = () => {
    setQuery('');
    setGroup('workspace');
    setSelectedId('dashboard');
    resetArticle();
  };

  return <div className="platform-help user-docs docs-site">
    <DocTopNav activeGroup={selectedEntry?.group ?? group} selectedId={selectedEntry?.id ?? ''}
      onGroup={selectGroup} onIntro={selectIntro} />
    <div className="user-docs-searchbar">
      <Input ref={searchRef} allowClear aria-label="搜索使用文档" placeholder="搜索功能、前置条件或命令"
        prefix={<Search size={16} />} value={query}
        onChange={(event) => setQuery(event.target.value)} />
      <kbd>{/Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl'}</kbd><kbd>K</kbd>
    </div>
    <div className="user-docs-layout">
      <DocSidebar activeGroup={group} selectedId={selectedEntry?.id ?? ''} searching={searching}
        entries={entries} onSelect={selectEntry} onGroup={selectGroup} />
      <div className="user-docs-article-shell" ref={articleRef}>
        {selectedEntry ? <DocArticle key={selectedEntry.id} entry={selectedEntry} onNavigate={onNavigate} /> :
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未找到相关文档" />}
      </div>
      {selectedEntry && <DocToc entry={selectedEntry} />}
    </div>
  </div>;
}
