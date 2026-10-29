/* Pure helpers for the evaluation screens — unit-tested. */
import type { BatchCell } from './evaluation-api'

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
    case 'queued': return 'waiting its turn'
    case 'running': return 'analysing'
    case 'pending': return `pending: holding window not yet closed`
    case 'scored': return `alpha ${signedFrac(c.score?.alpha)} vs ${c.score?.benchmark ?? 'benchmark'}`
    case 'unscored': return 'no readable rating'
    case 'error': return c.score?.reason ?? 'closes unavailable'
    default: return c.error ?? c.phase
  }
}
