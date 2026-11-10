import { useId, useState } from 'react';
import { Empty } from 'antd';

export interface ChartSeries { key: string; label: string; color: string; dash?: string }
export interface ChartPoint { time: string; values: Record<string, number | null>; note?: string }
export interface ChartThreshold { value: number; label: string; color: string }
interface Props {
  title: string;
  unit: string;
  points: ChartPoint[];
  series: ChartSeries[];
  thresholds?: ChartThreshold[];
  maximum?: number;
  gapSeconds?: number;
}
const W = 720; const H = 220; const L = 52; const R = 16; const T = 22; const B = 36;

export function TrendChart({ title, unit, points, series, thresholds = [], maximum, gapSeconds }: Props) {
  const id = useId();
  const [hover, setHover] = useState<number | null>(null);
  const valid = points.filter((point) => Number.isFinite(Date.parse(point.time)))
    .sort((a, b) => Date.parse(a.time) - Date.parse(b.time));
  const observed = (value: number | null | undefined): value is number => typeof value === 'number'
    && Number.isFinite(value) && value >= 0 && (maximum === undefined || value <= maximum);
  const times = valid.map((point) => Date.parse(point.time));
  const start = times[0] ?? 0; const end = times.at(-1) ?? start;
  const maxValue = Math.max(1, ...valid.flatMap((point) => Object.values(point.values).filter(observed)), ...thresholds.map((v) => v.value));
  const cap = maximum ?? Math.ceil(maxValue * 1.15 * 10) / 10;
  const x = (time: number) => start === end ? (L + W - R) / 2 : L + ((time - start) / (end - start)) * (W - L - R);
  const y = (value: number) => H - B - (value / cap) * (H - T - B);
  const active = hover !== null ? valid[hover] : undefined;
  const paths = (key: string): string => {
    let path = ''; let previous: number | null = null;
    valid.forEach((point) => {
      const value = point.values[key]; const time = Date.parse(point.time);
      if (!observed(value)) { previous = null; return; }
      const move = previous === null || Boolean(gapSeconds && time - previous > gapSeconds * 1000);
      path += `${move ? 'M' : 'L'}${x(time)},${y(value)} `;
      previous = time;
    });
    return path;
  };
  const timeLabel = (time: number) => new Date(time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
  const hasValue = valid.some((point) => series.some((item) => observed(point.values[item.key])));
  const hoverAt = (clientX: number, svg: SVGSVGElement) => {
    const box = svg.getBoundingClientRect();
    const scale = Math.max(Math.min(box.width / W, (box.height || H) / H), 0.001);
    const offset = (clientX - box.left - (box.width - W * scale) / 2) / scale;
    const nearest = times.reduce((best, time, index) => Math.abs(x(time) - offset)
      < Math.abs(x(times[best]) - offset) ? index : best, 0);
    setHover(nearest);
  };
  return <div className="fleet-chart">
    <div className="fleet-chart-legend">{series.map((item) => <span key={item.key}>
      <svg width="22" height="8" aria-hidden="true"><line x1="0" x2="22" y1="4" y2="4" stroke={item.color} strokeWidth="2" strokeDasharray={item.dash} /></svg>{item.label}
    </span>)}<small>{unit}</small></div>
    {thresholds.length ? <div className="fleet-chart-legend">{thresholds.map((item) => <span key={item.label}>
      <svg width="22" height="8" aria-hidden="true"><line x1="0" x2="22" y1="4" y2="4" stroke={item.color} strokeWidth="2" strokeDasharray="5 5" /></svg>
      {item.label} {item.value.toFixed(1)}
    </span>)}</div> : null}
    {!hasValue ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前范围暂无有效趋势样本" /> : <div className="fleet-chart-plot" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={id}>
        <title id={id}>{title}</title>
        {[0, 0.25, 0.5, 0.75, 1].map((fraction) => <g key={fraction}>
          <line className="fleet-chart-grid" x1={L} x2={W - R} y1={y(fraction * cap)} y2={y(fraction * cap)} />
          <text x={L - 8} y={y(fraction * cap) + 4} textAnchor="end">{(fraction * cap).toFixed(cap <= 10 ? 1 : 0)}</text>
        </g>)}
        {[0, 0.5, 1].map((fraction) => <text key={fraction} x={L + fraction * (W - L - R)} y={H - 9}
          textAnchor={fraction === 0 ? 'start' : fraction === 1 ? 'end' : 'middle'}>{timeLabel(start + fraction * (end - start))}</text>)}
        {thresholds.map((item) => <g key={item.label}>
          <line x1={L} x2={W - R} y1={y(item.value)} y2={y(item.value)} stroke={item.color} strokeDasharray="5 5" opacity=".65" />
        </g>)}
        {series.map((item) => <g key={item.key}>
          <path d={paths(item.key)} fill="none" stroke={item.color} strokeWidth="2.5" strokeDasharray={item.dash} />
          {valid.map((point, index) => {
            const value = point.values[item.key];
            return !observed(value) ? null : <circle key={index} cx={x(times[index])} cy={y(value)} r="2.5" fill={item.color} />;
          })}
        </g>)}
        {active ? <line x1={x(Date.parse(active.time))} x2={x(Date.parse(active.time))} y1={T} y2={H - B} stroke="#94a3b8" strokeDasharray="3 3" /> : null}
        <rect x={L} y={T} width={W - L - R} height={H - T - B} fill="transparent"
          onMouseMove={(event) => {
            hoverAt(event.clientX, event.currentTarget.ownerSVGElement!);
          }} onPointerDown={(event) => hoverAt(event.clientX, event.currentTarget.ownerSVGElement!)} />
      </svg>
      {active ? <div className="fleet-chart-tooltip" role="status">
        <strong>{new Date(active.time).toLocaleString('zh-CN', { hour12: false })}</strong>
        {series.map((item) => <span key={item.key}>{item.label}：{!observed(active.values[item.key]) ? '—' : `${active.values[item.key]!.toFixed(2)} ${unit}`}</span>)}
        {active.note ? <small>{active.note}</small> : null}
      </div> : null}
    </div>}
  </div>;
}
