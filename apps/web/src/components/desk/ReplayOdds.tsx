import { useState } from 'react'
import { Button } from '#/components/ui/button'
import { api } from '#/lib/api'
import type { TimeMachineRequest, TimeMachineResult } from '#/lib/desk-types'
import { inr, pct01, rMult } from '#/lib/desk-format'
import { SourceBadge } from './SourceBadge'

/* Time Machine panel: 10,000 replays + the real history + one honest verdict.
   Used on the plan page (intraday) and inside LevelsPanel (swing). Runs on a
   click, never on every keystroke — each run is ~1s of server CPU. */

function Odd({ label, value, tone, sub }: { label: string; value: string; tone?: string; sub?: string }) {
  return (
    <div className="rounded-[var(--r-md)] border border-[var(--line-1)] bg-[var(--bg-2)] px-3 py-2">
      <div className="text-[10.5px] uppercase tracking-wider text-[var(--text-4)]">{label}</div>
      <div className="es-mono mt-0.5 text-lg" style={{ color: tone ?? 'var(--text-1)' }}>{value}</div>
      {sub && <div className="text-[11px] text-[var(--text-4)]">{sub}</div>}
    </div>
  )
}

/** Fan chart: 5/25/50/75/95th percentile bands of the replayed price per bar,
 *  with the stop, entry and target as horizontal lines. Plain SVG. */
export function FanChart({ result }: { result: TimeMachineResult }) {
  const { bands, band_times: times } = result.mc
  const { entry, stop, target } = result.plan
  if (!bands.length) return null
  const W = 560, H = 180, P = 8
  const lo = Math.min(stop, ...bands.map((b) => b[0]))
  const hi = Math.max(target, ...bands.map((b) => b[4]))
  const x = (i: number) => P + (i / Math.max(bands.length - 1, 1)) * (W - 2 * P)
  const y = (v: number) => P + (1 - (v - lo) / Math.max(hi - lo, 1e-9)) * (H - 2 * P)
  const area = (a: number, b: number) =>
    bands.map((r, i) => `${x(i)},${y(r[a])}`).join(' ') + ' ' +
    bands.map((r, i) => [i, r] as const).reverse().map(([i, r]) => `${x(i)},${y(r[b])}`).join(' ')
  const line = (v: number) => <line x1={P} x2={W - P} y1={y(v)} y2={y(v)} />
  return (
    <figure className="space-y-1">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
        aria-label={`Replayed price range from ${times[0]} to ${times.at(-1)}`}>
        <polygon points={area(0, 4)} fill="var(--accent-bg)" />
        <polygon points={area(1, 3)} fill="var(--accent-bg)" />
        <polyline points={bands.map((r, i) => `${x(i)},${y(r[2])}`).join(' ')}
          fill="none" stroke="var(--accent-hi)" strokeWidth={1.5} />
        <g strokeDasharray="4 4" strokeWidth={1}>
          <g stroke="var(--ok)">{line(target)}</g>
          <g stroke="var(--text-3)">{line(entry)}</g>
          <g stroke="var(--err)">{line(stop)}</g>
        </g>
      </svg>
      <figcaption className="flex justify-between text-[11px] text-[var(--text-4)]">
        <span>{times[0]}</span>
        <span>median, 25–75% and 5–95% of replays · target / entry / stop dashed</span>
        <span>{times.at(-1)}</span>
      </figcaption>
    </figure>
  )
}

/** Histogram of the price at the end of each replay (1st–99th percentile). */
export function CloseHistogram({ result }: { result: TimeMachineResult }) {
  const { bins, lo, hi } = result.mc.hist
  const max = Math.max(...bins, 1)
  const { entry } = result.plan
  const entryPos = hi > lo ? (entry - lo) / (hi - lo) : 0.5
  return (
    <figure className="space-y-1">
      <div className="relative flex h-16 items-end gap-px">
        {bins.map((b, i) => (
          <div key={i} className="flex-1 rounded-t-sm"
            style={{ height: `${(b / max) * 100}%`, background: 'var(--line-3)' }} />
        ))}
        {entryPos >= 0 && entryPos <= 1 && (
          <div className="absolute bottom-0 top-0 w-px" title={`entry ${inr(entry)}`}
            style={{ left: `${entryPos * 100}%`, background: 'var(--text-2)' }} />
        )}
      </div>
      <figcaption className="es-mono flex justify-between text-[11px] text-[var(--text-4)]">
        <span>{inr(lo)}</span><span>price at {result.mc.until} (line = entry)</span><span>{inr(hi)}</span>
      </figcaption>
    </figure>
  )
}

export function ReplayOddsView({ result }: { result: TimeMachineResult }) {
  const { mc, history: h } = result
  const until = result.mode === 'swing' ? `${mc.bars_left} sessions` : mc.until
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <SourceBadge source={result.source} asOf={result.data_as_of} />
        <span className="es-pill">{mc.replays.toLocaleString('en-IN')} replays · seed {mc.seed}</span>
        <span className="es-pill">pool: {mc.pool_sessions} {result.mode === 'swing' ? 'daily bars' : 'sessions'}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Odd label="Target first" value={pct01(mc.p_target)} tone="var(--ok)" />
        <Odd label="Stop first" value={pct01(mc.p_stop)} tone="var(--err)" />
        <Odd label="Neither" value={pct01(mc.p_neither)} sub={`still open at ${until}`} />
        <Odd label="Green" value={pct01(mc.p_green)} sub="target, or above entry" />
      </div>
      <p className="text-sm font-medium">{result.verdict}</p>
      {result.verdict_notes.map((n) => (
        <p key={n} className="text-xs text-[var(--text-3)]">{n}</p>
      ))}
      <FanChart result={result} />
      <CloseHistogram result={result} />
      <div className="grid gap-1 text-xs text-[var(--text-2)]">
        <div>
          Price at {until}: median <span className="es-mono">{inr(mc.median_close)}</span>, 90% of
          replays between <span className="es-mono">{inr(mc.p5_close)}</span> and{' '}
          <span className="es-mono">{inr(mc.p95_close)}</span>. Average R{' '}
          <span className="es-mono">{rMult(mc.avg_R, 3)}</span>.
        </div>
        <div>
          {h.rule ? (
            <>History: the same rule on those {h.sessions} real sessions fired {h.triggered} times —{' '}
              {h.target} target, {h.stop} stop, {h.close} neither. Average R{' '}
              <span className="es-mono">{rMult(h.avg_R, 3)}</span>.</>
          ) : (h.note ?? 'No history replay for this setup.')}
        </div>
      </div>
      {result.warnings.map((w) => (
        <p key={w} className="text-xs text-[var(--run)]">{w}</p>
      ))}
      <p className="text-[11px] text-[var(--text-4)]">{result.note}</p>
    </div>
  )
}

export function ReplayOdds({ request, label = 'Run the Time Machine' }: {
  request: TimeMachineRequest | null
  label?: string
}) {
  const [result, setResult] = useState<TimeMachineResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run() {
    if (!request) return
    setBusy(true); setError(null)
    try {
      setResult(await api.desk.timeMachine(request))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" onClick={run} disabled={!request || busy}>
          {busy ? 'Replaying 10,000 times…' : result ? 'Run again' : label}
        </Button>
        <span className="text-xs text-[var(--text-4)]">
          Replays the rest of the {request?.mode === 'swing' ? 'horizon' : 'session'} from the
          stock's own recent bars. A simulation, not a prediction.
        </span>
      </div>
      {error && <p className="text-sm text-[var(--err)]">{error}</p>}
      {result && <ReplayOddsView result={result} />}
    </section>
  )
}
