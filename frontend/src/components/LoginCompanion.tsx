import { useEffect, useRef, useState, type CSSProperties } from 'react';
import './LoginCompanion.css';

export type PetState = 'idle' | 'input' | 'closed' | 'loading' | 'failure' | 'success' | 'welcome' | 'wave';
const characters = ['primary', 'secondary', 'tertiary', 'sun'] as const;
const labels: Record<PetState, string> = {
  idle: '待机', input: '输入中', closed: '闭眼', loading: '加载中',
  failure: '登录失败', success: '登录成功', welcome: '欢迎', wave: '挥手',
};
type CharacterStyle = CSSProperties & Record<'--delay' | '--look-x' | '--look-y' | '--gaze-x' | '--gaze-y', string>;

export function LoginCompanion({ state }: { state: PetState }) {
  const [hovered, setHovered] = useState(false);
  const [pointer, setPointer] = useState({ x: 0, y: 0 });
  const [blinking, setBlinking] = useState(-1);
  const scene = useRef<HTMLDivElement>(null);
  const visibleState = state === 'idle' && hovered ? 'wave' : state;

  useEffect(() => {
    const follow = (event: PointerEvent) => {
      const bounds = scene.current?.getBoundingClientRect();
      if (!bounds?.width || !bounds.height) return;
      setPointer({
        x: Math.max(-1, Math.min(1, (event.clientX - bounds.left - bounds.width / 2) / bounds.width)),
        y: Math.max(-1, Math.min(1, (event.clientY - bounds.top - bounds.height / 2) / bounds.height)),
      });
    };
    const reset = () => setPointer({ x: 0, y: 0 });
    window.addEventListener('pointermove', follow);
    document.addEventListener('pointerleave', reset);
    window.addEventListener('blur', reset);
    return () => {
      window.removeEventListener('pointermove', follow);
      document.removeEventListener('pointerleave', reset);
      window.removeEventListener('blur', reset);
    };
  }, []);

  useEffect(() => {
    let timer: number;
    let reopen: number;
    const blink = () => {
      setBlinking(Math.floor(Math.random() * characters.length));
      reopen = window.setTimeout(() => setBlinking(-1), 170);
      timer = window.setTimeout(blink, 1800 + Math.random() * 2400);
    };
    timer = window.setTimeout(blink, 1000 + Math.random() * 1800);
    return () => { window.clearTimeout(timer); window.clearTimeout(reopen); };
  }, []);

  return (
    <div ref={scene} className={`login-companion pet-${visibleState}`} role="img"
      aria-label={`登录助手，${labels[visibleState]}`} data-state={visibleState}
      onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
      <div className="scene-characters" aria-hidden="true">
        {characters.map((character, index) => {
          const strength = [12, 8, 17, 22][index];
          const style: CharacterStyle = {
            '--delay': `${index * 150}ms`, '--look-x': `${pointer.x * strength}px`,
            '--look-y': `${pointer.y * strength * 0.45}px`,
            '--gaze-x': `${pointer.x * [7, 5, 5, 4][index]}px`,
            '--gaze-y': `${pointer.y * 3}px`,
          };
          return (
            <div key={character} className={`scene-character character-${character}`} style={style}>
              <div className="character-tracking"><div className="character-pose">
                <div className={`character-head${blinking === index ? ' is-blinking' : ''}`}>
                  <div className="character-face"><span className="pet-eye left" /><span className="pet-eye right" /><span className="pet-mouth" /></div>
                </div>
                {character !== 'sun' && <div className="character-body" />}
                <span className="character-hand hand-left" /><span className="character-hand hand-right" />
              </div></div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
