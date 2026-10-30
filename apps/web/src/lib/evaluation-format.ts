/* Pure helpers for the evaluation screens — unit-tested. */
import type { AnalyticsFilters, Attribution, BatchCell, OpenMark } from './evaluation-api'
import { inr, istTime, sourceLabel } from './desk-format'

/** Polyline points for values scaled into a w x h box (y grows downward). */
export function linePoints(values: number[], w: number, h: number, pad = 2): string {
  if (!values.length) return ''
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const span = hi - lo || 1
  const dx = values.length > 1 ? (w - 2 * pad) / (values.length - 1) : 0
  return values
    .map((v, i) => `${(pad + i * dx).toFixed(1)},${(h - pad - ((v - lo) / span) * (h - 2 * pad)).toFixed(1)}`)
    .join(' ')
}

/** 0.0123 -> "+1.23%" (a fraction). */
export function signedFrac(x: number | null | undefined, digits = 2): string {
  if (x == null || Number.isNaN(x)) return '—'
  return `${x >= 0 ? '+' : ''}${(x * 100).toFixed(digits)}%`
}

/** What the user is told about a cell, by phase. Never a guessed outcome. */
export function cellLabel(c: BatchCell): string {
  switch (c.phase) {
    case 'queued': return c.retry_at
      ? `retrying after ${new Date(c.retry_at).toLocaleTimeString()}: ${c.error ?? 'refused'}`
      : 'waiting its turn'
    case 'submitting': return 'starting now (already past the stop point)'
    case 'running': return 'analysing'
    case 'pending': return `pending: holding window not yet closed`
    case 'scored': return `alpha ${signedFrac(c.score?.alpha)} vs ${c.score?.benchmark ?? 'benchmark'}`
    case 'unscored': return 'no readable rating'
    case 'error': return c.score?.reason ?? 'closes unavailable'
    default: return c.error ?? c.phase
  }
}

/** Query string for the trading-performance / journal CSV filters. */
export function analyticsQuery(f: AnalyticsFilters): string {
  const q = new URLSearchParams({ mode: f.mode, portfolio_id: f.portfolio_id })
  if (f.strategy_id) q.set('strategy_id', f.strategy_id)
  if (f.strategy_id && f.strategy_id !== 'manual' && f.strategy_version && f.strategy_version > 0)
    q.set('strategy_version', String(f.strategy_version))
  return q.toString()
}

/** "ORB 15 v2"; an unattributed trade is Manual/legacy, never guessed. */
export function strategyLabel(a: Attribution): string {
  if (!a.strategy_id) return 'Manual/legacy'
  const name = a.strategy_name ?? a.strategy_id
  return a.strategy_version != null ? `${name} v${a.strategy_version}` : name
}

/** An open position's mark with its source and age. */
export function markLabel(m: OpenMark | null): string {
  if (!m) return '—'
  if (m.status === 'unavailable' || m.price == null) return 'no price'
  if (m.status === 'stale') return `stale ${inr(m.price)} · ${m.as_of?.slice(0, 16).replace('T', ' ') ?? '?'}`
  return `${sourceLabel(m.source).label} ${inr(m.price)} @ ${istTime(m.as_of)}`
}
