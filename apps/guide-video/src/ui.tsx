import React from 'react';
import {C, R} from './theme';

export const Panel: React.FC<{
  title?: string;
  note?: string;
  children?: React.ReactNode;
  style?: React.CSSProperties;
  accent?: boolean;
}> = ({title, note, children, style, accent}) => (
  <div
    style={{
      background: C.bg1,
      border: `1px solid ${accent ? C.accentLine : C.line1}`,
      borderRadius: R.lg,
      padding: 24,
      display: 'flex',
      flexDirection: 'column',
      gap: 16,
      boxShadow: '0 8px 24px rgba(0,0,0,0.35)',
      ...style,
    }}
  >
    {title ? (
      <div style={{display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 16}}>
        <div style={{fontSize: 24, fontWeight: 600, color: C.text1, letterSpacing: -0.2}}>{title}</div>
        {note ? <div style={{fontSize: 17, color: C.text3}}>{note}</div> : null}
      </div>
    ) : null}
    {children}
  </div>
);

export const Pill: React.FC<{tone?: keyof typeof TONES; children: React.ReactNode}> = ({tone = 'neutral', children}) => {
  const t = TONES[tone];
  return (
    <span
      style={{
        background: t.bg,
        color: t.fg,
        border: `1px solid ${t.fg}44`,
        borderRadius: 999,
        padding: '5px 14px',
        fontSize: 17,
        fontWeight: 600,
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  );
};

const TONES = {
  neutral: {bg: C.bg3, fg: C.text2},
  accent: {bg: C.accentBg, fg: C.accentHi},
  ok: {bg: C.okBg, fg: C.ok},
  warn: {bg: C.runBg, fg: C.run},
  err: {bg: C.errBg, fg: C.err},
  info: {bg: C.infoBg, fg: C.info},
} as const;

export const Field: React.FC<{label: string; value: React.ReactNode; mono?: boolean; tone?: string}> = ({
  label,
  value,
  mono,
  tone,
}) => (
  <div style={{display: 'flex', flexDirection: 'column', gap: 6}}>
    <div style={{fontSize: 16, color: C.text4, textTransform: 'uppercase', letterSpacing: 1}}>{label}</div>
    <div
      style={{
        fontSize: 22,
        color: tone ?? C.text1,
        fontWeight: 600,
        fontFamily: mono ? 'ui-monospace, Menlo, monospace' : undefined,
      }}
    >
      {value}
    </div>
  </div>
);

export const Row: React.FC<{cells: React.ReactNode[]; widths: number[]; head?: boolean; tone?: string}> = ({
  cells,
  widths,
  head,
  tone,
}) => (
  <div style={{display: 'flex', gap: 12, alignItems: 'center', padding: '10px 0', borderBottom: `1px solid ${C.line1}`}}>
    {cells.map((c, i) => (
      <div
        key={i}
        style={{
          flex: widths[i],
          fontSize: head ? 16 : 20,
          color: head ? C.text4 : (tone ?? C.text2),
          fontWeight: head ? 600 : 500,
          textTransform: head ? 'uppercase' : undefined,
          letterSpacing: head ? 1 : undefined,
        }}
      >
        {c}
      </div>
    ))}
  </div>
);

/** Tiny deterministic candle sketch — illustrative shape, never a quoted price series. */
export const Candles: React.FC<{seed?: number; width?: number; height?: number; bars?: number}> = ({
  seed = 7,
  width = 520,
  height = 180,
  bars = 28,
}) => {
  let s = seed;
  const rnd = () => ((s = (s * 1103515245 + 12345) % 2147483648) / 2147483648);
  const w = width / bars;
  let y = height * 0.55;
  return (
    <svg width={width} height={height} style={{display: 'block'}}>
      {Array.from({length: bars}, (_, i) => {
        const mid = height * 0.5;
        const d = (rnd() - 0.5) * height * 0.16 + (mid - y) * 0.18; // pull back to the middle
        const open = y;
        y = Math.max(18, Math.min(height - 18, y + d));
        const up = y <= open;
        const top = Math.min(open, y);
        const h = Math.max(4, Math.abs(y - open));
        return (
          <g key={i}>
            <rect x={i * w + w * 0.18} y={top} width={w * 0.64} height={h} fill={up ? C.ok : C.err} opacity={0.85} />
            <rect x={i * w + w * 0.46} y={top - 8} width={1.5} height={h + 16} fill={up ? C.ok : C.err} opacity={0.45} />
          </g>
        );
      })}
    </svg>
  );
};

export const Bars: React.FC<{values: number[]; width?: number; height?: number}> = ({
  values,
  width = 520,
  height = 160,
}) => {
  const max = Math.max(...values.map(Math.abs), 1);
  const w = width / values.length;
  const mid = height / 2;
  return (
    <svg width={width} height={height} style={{display: 'block'}}>
      <line x1={0} y1={mid} x2={width} y2={mid} stroke={C.line2} strokeWidth={1} />
      {values.map((v, i) => {
        const h = (Math.abs(v) / max) * (height / 2 - 10);
        return (
          <rect
            key={i}
            x={i * w + w * 0.2}
            y={v >= 0 ? mid - h : mid}
            width={w * 0.6}
            height={h}
            rx={3}
            fill={v >= 0 ? C.ok : C.err}
            opacity={0.85}
          />
        );
      })}
    </svg>
  );
};
