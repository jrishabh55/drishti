import type { ReactNode } from 'react'
import type { SessionState } from '#/lib/kite-api'

export type Tone = 'ok' | 'warn' | 'err' | 'run' | 'info'

export function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`es-pill ${tone}`}>{children}</span>
}

export function Card({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="space-y-3 rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)] p-4">
      <h2 className="text-sm font-semibold">{title}</h2>
      {hint && <p className="text-xs text-[var(--text-4)]">{hint}</p>}
      {children}
    </section>
  )
}

export function sessionPill(s: SessionState): { tone: Tone; label: string } {
  switch (s) {
    case 'active': return { tone: 'ok', label: 'Connected today' }
    case 'expired': return { tone: 'warn', label: 'Session expired (06:00 IST) · log in again' }
    case 'revoked': return { tone: 'err', label: 'Kite rejected the session · log in again' }
    default: return { tone: 'warn', label: 'Not logged in today' }
  }
}

export function tradeTone(status: string): Tone {
  if (status === 'closed') return 'info'
  if (status === 'protected') return 'ok'
  if (status === 'needs_attention' || status === 'entry_rejected') return 'err'
  if (status.endsWith('_sent')) return 'run'
  return 'warn'
}
