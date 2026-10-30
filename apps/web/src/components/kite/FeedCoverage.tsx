import type { LiveStatus } from '#/lib/kite-api'

export function FeedCoverage({ stream }: { stream: LiveStatus }) {
  const c = stream.bootstrap
  const live = stream.symbols.filter(s => s.source === 'live:kite').length
  return <div className="space-y-1 text-xs text-[var(--text-3)]" aria-live="polite">
    <p>Feed coverage: {live} Kite · {stream.symbols.length - live} delayed · {stream.symbols.length} subscribed.</p>
    {c && <p>Scanner history: {c.completed}/{c.total} series loaded · {c.failed} unavailable · {c.running ? 'filling in background' : 'finished'}.
      {' '}{c.session_ready} sessions backfilled.</p>}
    <p>Delayed fallback covers symbols that are unsubscribed, stale or still filling. Closed daily history and public fundamentals are separate from current quotes.</p>
  </div>
}
