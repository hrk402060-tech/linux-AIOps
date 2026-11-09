import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Empty, List, message, Select, Tag } from 'antd';
import { ArrowLeft, CircleStop, Clock3, ExternalLink, PanelRightClose, PanelRightOpen, PlugZap, Server, ShieldCheck, TerminalSquare } from 'lucide-react';

import { getAssets } from '../api/assets';
import { createTerminalTicket, disconnectTerminalSession, getTerminalSessions } from '../api/terminal';
import { TerminalConsole } from '../components/TerminalConsole';
import { TerminalAssistantPanel } from '../components/TerminalAssistantPanel';
import { TerminalAssetCatalog } from '../components/TerminalAssetCatalog';
import type { Asset } from '../types/asset';
import type { TerminalConnectionDetails, TerminalSessionSummary, TerminalTicket } from '../types/terminal';
import { terminalAssetHref } from '../utils/terminalNavigation';

const sessionLabels: Record<TerminalSessionSummary['status'], string> = {
  waiting: '等待连接', connected: '已连接', closed: '已关闭',
  failed: '失败', terminated: '已终止', timeout: '已超时', revoked: '权限已撤销',
};

export function TerminalPage({ initialAssetId, standalone = false }: { initialAssetId?: string | null; standalone?: boolean }) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [assetsLoading, setAssetsLoading] = useState(true);
  const [assetsError, setAssetsError] = useState('');
  const assetsRequest = useRef(0);
  const [recentSessions, setRecentSessions] = useState<TerminalSessionSummary[]>([]);
  const [assetId, setAssetId] = useState(initialAssetId ?? '');
  const [catalogOpen, setCatalogOpen] = useState(!standalone && !initialAssetId);
  const [ticket, setTicket] = useState<TerminalTicket | null>(null);
  const connectionGeneration = useRef(0);
  const ticketAsset = useRef<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [connectionState, setConnectionState] = useState('未连接');
  const [connectionDetails, setConnectionDetails] = useState<TerminalConnectionDetails | null>(null);
  const [terminalContext, setTerminalContext] = useState('');
  const [assistantOpen, setAssistantOpen] = useState(true);
  const [historyError, setHistoryError] = useState('');
  const historyRequest = useRef(0);
  const [messageApi, contextHolder] = message.useMessage();
  const loadAssets = useCallback(async () => {
    const request = ++assetsRequest.current;
    setAssetsLoading(true);
    setAssetsError('');
    try {
      const nextAssets = await getAssets('enabled');
      if (request !== assetsRequest.current) return;
      setAssets(nextAssets);
      setAssetId((current) => current || nextAssets[0]?.id || '');
    } catch (error) {
      if (request !== assetsRequest.current) return;
      const detail = error instanceof Error ? error.message : '资产加载失败';
      setAssetsError(detail);
      messageApi.error(detail);
    } finally {
      if (request === assetsRequest.current) setAssetsLoading(false);
    }
  }, [messageApi]);
  useEffect(() => {
    void loadAssets();
    return () => { assetsRequest.current += 1; };
  }, [loadAssets]);
  const loadRecentSessions = useCallback(async () => {
    const request = ++historyRequest.current;
    try {
      const nextSessions = await getTerminalSessions(20);
      if (request !== historyRequest.current) return;
      setRecentSessions(nextSessions);
      setHistoryError('');
    } catch {
      if (request === historyRequest.current) setHistoryError('最近会话暂时不可用，不影响新建连接');
    }
  }, []);
  useEffect(() => {
    if (catalogOpen) return;
    void loadRecentSessions();
    return () => { historyRequest.current += 1; };
  }, [catalogOpen, loadRecentSessions]);
  useEffect(() => {
    if (initialAssetId) {
      setAssetId(initialAssetId);
      setCatalogOpen(false);
    }
  }, [initialAssetId]);
  useEffect(() => {
    connectionGeneration.current += 1;
    setTicket(null); ticketAsset.current = null;
    setConnecting(false); setConnectionState('未连接');
    setConnectionDetails(null);
    setDisconnecting(false);
    setTerminalContext('');
    return () => { connectionGeneration.current += 1; };
  }, [assetId]);
  const selected = useMemo(
    () => assets.find((item) => item.id === assetId) ?? null, [assetId, assets],
  );
  const connect = async () => {
    if (!selected || assetsLoading || assetsError) return;
    const generation = ++connectionGeneration.current;
    setConnecting(true); setTicket(null); setConnectionState('连接中');
    setDisconnecting(false);
    setConnectionDetails(null);
    setTerminalContext('');
    try {
      const next = await createTerminalTicket(selected.id);
      if (generation !== connectionGeneration.current) return;
      ticketAsset.current = selected.id;
      setTicket(next);
    }
    catch (error) {
      if (generation !== connectionGeneration.current) return;
      setConnectionState('连接失败');
      messageApi.error(error instanceof Error ? error.message : '连接失败');
    } finally {
      if (generation === connectionGeneration.current) setConnecting(false);
    }
  };
  const stateChanged = useCallback((state: string, details?: TerminalConnectionDetails) => {
    setConnectionState({ connected: '已连接', closed: '已关闭', failed: '连接失败' }[state] ?? state);
    if (state === 'connected') return;
    connectionGeneration.current += 1;
    setDisconnecting(false);
    setConnectionDetails(details ?? null);
    void loadRecentSessions();
  }, [loadRecentSessions]);
  const collectOutput = useCallback((value: string) => {
    setTerminalContext((current) => (current + value).slice(-12000));
  }, []);
  const disconnectCurrent = async () => {
    if (!ticket || disconnecting) return;
    const generation = connectionGeneration.current;
    setDisconnecting(true);
    setConnectionState('正在断开');
    try {
      await disconnectTerminalSession(ticket.session_id);
      if (generation !== connectionGeneration.current) return;
    } catch (error) {
      if (generation !== connectionGeneration.current) return;
      setDisconnecting(false);
      setConnectionState('已连接');
      messageApi.error(error instanceof Error ? error.message : '断开失败');
    }
  };
  const returnToPlatform = () => {
    window.location.href = window.location.pathname;
  };
  const assetStatusClass = (item: Asset) => item.status === 'online' ? 'is-online' : item.status === 'offline' ? 'is-offline' : 'is-unknown';
  const switchingLocked = connecting || disconnecting || connectionState === '已连接' || connectionState === '连接中';
  const standalonePath = selected ? terminalAssetHref(selected.id) : undefined;
  if (catalogOpen) return <section className="terminal-page terminal-page--catalog">{contextHolder}
    <header className="terminal-page-header"><h1>SSH 远程</h1><div className="terminal-page-header-meta"><Server size={17} /><span>{assets.length} 台服务器</span></div></header>
    <TerminalAssetCatalog assets={assets} loading={assetsLoading} error={assetsError} onReload={() => void loadAssets()}
      onOpenInPage={(id) => { setAssetId(id); setCatalogOpen(false); }} />
  </section>;
  return <section className={`terminal-page ${standalone ? 'terminal-page--standalone' : ''} ${ticket ? 'terminal-page--session' : ''}`}>{contextHolder}
    {standalone ? <header className="terminal-standalone-bar">
      <div className="terminal-standalone-title"><TerminalSquare size={18} /><strong>SSH 远程</strong><span>{selected ? `${selected.name} · ${selected.host}` : '选择服务器建立连接'}</span></div>
      <div className="terminal-standalone-actions"><Tag color={connectionState === '已连接' ? 'green' : 'default'}>{connectionState}</Tag><Button type="text" icon={<ArrowLeft size={16} />} onClick={returnToPlatform}>返回平台</Button></div>
    </header> : <header className="terminal-page-header">
      <div><span className="page-eyebrow">运维 / SSH 远程</span><h1>SSH 远程</h1><p>在浏览器建立安全终端，AI 仅分析终端输出并提供只读建议。</p></div>
      <div className="terminal-page-header-meta"><Button size="small" icon={<ArrowLeft size={15} />} disabled={switchingLocked} onClick={() => setCatalogOpen(true)}>服务器列表</Button>{selected ? <Button size="small" icon={<ExternalLink size={15} />} href={standalonePath} target="_blank" rel="noopener noreferrer">新标签打开</Button> : null}</div>
    </header>}
    {!assetsLoading && !assetsError && assetId && !selected ? <Alert role="alert" type="warning" message="服务器不存在或当前账号无权访问，请重新选择服务器" /> : null}
    {assetsError ? <Alert role="alert" type="error" message={assetsError} /> : null}
    <div className="terminal-workbench">
      <aside className="terminal-asset-rail">
        <div className="terminal-rail-heading"><strong>连接资产</strong><span>{assets.length}</span></div>
        <Select aria-label="选择服务器" value={assetId || undefined} placeholder="搜索服务器" showSearch optionFilterProp="label" onChange={setAssetId} disabled={switchingLocked}
          options={assets.map((item) => ({ value: item.id, label: `${item.name} · ${item.host}` }))} />
        <div className="terminal-asset-list">
          {assets.length ? assets.map((item) => <button type="button" key={item.id} disabled={switchingLocked} className={`terminal-asset-item ${item.id === assetId ? 'is-selected' : ''}`} onClick={() => setAssetId(item.id)}>
            <span className={`terminal-asset-status ${assetStatusClass(item)}`} /><span><strong>{item.name}</strong><small>{item.host} · {item.credential?.username ?? 'SSH 账号已保护'}</small></span>
          </button>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可连接资产" />}
        </div>
        <div className="terminal-recent-heading"><Clock3 size={15} /><strong>最近会话</strong></div>
        {historyError ? <small className="terminal-history-error">{historyError}</small> : null}
        <List size="small" split={false} dataSource={recentSessions.slice(0, 5)} locale={{ emptyText: '暂无会话记录' }} renderItem={(item) => <List.Item className="terminal-recent-item">
          <button type="button" disabled={switchingLocked} onClick={() => setAssetId(item.asset_id)}><strong>{item.asset_name}</strong><span>{sessionLabels[item.status]} · {new Date(item.requested_at).toLocaleString()}</span>
            {item.ended_at ? <span>结束于 {new Date(item.ended_at).toLocaleString()}</span> : null}
            {item.failure_reason ? <span title={item.failure_reason}>{item.failure_reason}</span> : null}</button>
        </List.Item>} />
      </aside>
      <main className="terminal-main">
        <div className="terminal-control-band">
          <div className="terminal-target"><Select aria-label="当前服务器" value={assetId || undefined} placeholder="选择服务器" showSearch optionFilterProp="label" onChange={setAssetId} disabled={switchingLocked}
            options={assets.map((item) => ({ value: item.id, label: item.name + ' · ' + item.host }))} />
            <Button type="primary" icon={<PlugZap size={17} />} loading={connecting} disabled={!selected || assetsLoading || Boolean(assetsError) || disconnecting || connectionState === '已连接' || connectionState === '连接中'} onClick={() => void connect()}>连接</Button>
            {connectionState === '已连接' || disconnecting ? <Button danger loading={disconnecting} icon={<CircleStop size={17} />} onClick={() => void disconnectCurrent()}>断开</Button> : null}</div>
          <div className="terminal-security"><ShieldCheck size={17} /><span>凭据仅在后端解密 · 终端票据单次有效</span><Tag color={connectionState === '已连接' ? 'green' : 'default'}>{connectionState}</Tag><Button type="text" icon={assistantOpen ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />} aria-label={assistantOpen ? '收起 AI 助手' : '展开 AI 助手'} onClick={() => setAssistantOpen((current) => !current)} /></div>
        </div>
        {connectionDetails?.message ? <div className="terminal-security" role="status"><span>{connectionDetails.message}</span>
          {connectionDetails.ended_at && !Number.isNaN(Date.parse(connectionDetails.ended_at)) ? <time aria-label="会话结束时间" dateTime={connectionDetails.ended_at}>{new Date(connectionDetails.ended_at).toLocaleString()}</time> : null}</div> : null}
        <div className={`terminal-workspace ${assistantOpen ? '' : 'terminal-workspace--assistant-collapsed'}`}><div className="terminal-stage">{selected && ticket && ticketAsset.current === selected.id
          ? <TerminalConsole asset={selected} ticket={ticket} onStateChange={stateChanged} onOutput={collectOutput} />
          : <div className="terminal-placeholder"><TerminalSquare size={30} /><strong>选择服务器并建立 SSH 会话</strong><span>连接后终端将在当前浏览器工作台打开</span></div>}</div>
          <div className="terminal-assistant-slot" aria-hidden={!assistantOpen}><TerminalAssistantPanel key={ticket?.session_id ?? assetId} sessionId={ticket?.session_id ?? null} context={terminalContext} connected={connectionState === '已连接'} /></div></div>
      </main>
    </div>
  </section>;
}
