import { useEffect, useRef, useState } from 'react';
import { Activity, ClipboardCheck, Layers, Server, Workflow } from 'lucide-react';

import type { PetState } from './LoginCompanion';
import type { PlatformDomain } from './loginPlatformModel';
import type { PlatformRenderer } from './loginPlatformRenderer';
import './LoginPlatformScene.css';

const domains = [
  { key: 'assets', name: '资产与配置项', icon: Server },
  { key: 'monitoring', name: '监控与告警', icon: Activity },
  { key: 'automation', name: '自动化与任务', icon: Workflow },
  { key: 'governance', name: '安全与审计', icon: ClipboardCheck },
] as const;

export function LoginPlatformScene({ state }: { state: PetState }) {
  const stage = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const coreLabel = useRef<HTMLDivElement>(null);
  const labels = useRef<Partial<Record<PlatformDomain, HTMLButtonElement>>>({});
  const renderer = useRef<PlatformRenderer | null>(null);
  const [ready, setReady] = useState(false);
  const [selected, setSelected] = useState<PlatformDomain | null>(null);
  const selection = useRef<PlatformDomain | null>(null);
  function resetView(): void {
    selection.current = null;
    setSelected(null);
    renderer.current?.reset();
  }

  useEffect(() => {
    let active = true;
    let current: PlatformRenderer | null = null;
    async function initialize(): Promise<void> {
      try {
        const { createPlatformRenderer } = await import('./loginPlatformRenderer');
        if (!active || !stage.current || !canvas.current || !coreLabel.current) return;
        const nodes = labels.current;
        if (!nodes.assets || !nodes.monitoring || !nodes.automation || !nodes.governance) return;
        current = await createPlatformRenderer({
          stage: stage.current, canvas: canvas.current, coreLabel: coreLabel.current,
          labels: {
            assets: nodes.assets, monitoring: nodes.monitoring,
            automation: nodes.automation, governance: nodes.governance,
          },
          onAvailability: (available) => { if (active) setReady(available); },
        });
        if (!active) { current.dispose(); return; }
        renderer.current = current;
        if (selection.current) current.select(selection.current);
        setReady(true);
      } catch (error) {
        if (active) {
          setReady(false);
          console.warn('Platform diagram is using its static fallback.', error);
        }
      }
    }
    void initialize();
    return () => {
      active = false;
      renderer.current = null;
      current?.dispose();
    };
  }, []);

  return <div className="pl-scene" data-ready={ready} data-auth-state={state}>
    <div className="pl-scene-viewport" ref={stage}>
    <div className="pl-scene-fallback" role="img" aria-label="四类运维能力连接统一工作台的架构示意">
      <div className="pl-fallback-base" aria-hidden="true" />
      <div className="pl-fallback-core" aria-hidden="true"><Layers size={24} /></div>
      {domains.map(({ key, icon: Icon }) => <div className={`pl-fallback-node pl-node-${key}`} key={key} aria-hidden="true"><Icon size={28} /></div>)}
    </div>
    <canvas ref={canvas} className="pl-scene-canvas" aria-label="平台三维架构图"
      onDoubleClick={() => { if (ready) resetView(); }}
      tabIndex={ready ? 0 : -1} onKeyDown={(event) => {
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          event.preventDefault();
          renderer.current?.rotate(event.key === 'ArrowLeft' ? -1 : 1);
        } else if (event.key === '+' || event.key === '=' || event.key === '-') {
          event.preventDefault();
          renderer.current?.zoom(event.key === '-' ? -1 : 1);
        } else if (event.key === 'Home') {
          event.preventDefault();
          resetView();
        }
      }} />
    <div className="pl-scene-labels" aria-label="平台能力">
      {domains.map(({ key, name, icon: Icon }) => <button key={key} type="button"
        className={`pl-scene-label pl-label-${key}`} aria-pressed={selected === key}
        ref={(element) => { if (element) labels.current[key] = element; }}
        onClick={() => {
          selection.current = key;
          setSelected(key);
          renderer.current?.select(key);
        }}>
        <Icon size={17} aria-hidden="true" /><span>{name}</span>
      </button>)}
    </div>
    <div className="pl-core-label" ref={coreLabel}>统一工作台</div>
    </div>
  </div>;
}
