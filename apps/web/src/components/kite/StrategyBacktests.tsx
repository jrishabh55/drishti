import { useCallback, useEffect, useState } from 'react'
import { Pill } from '#/components/kite/KiteBits'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '#/components/ui/table'
import { inr, rMult } from '#/lib/desk-format'
import { kite, kiteError, type Backtest, type BacktestStats, type BacktestSummary, type Strategy } from '#/lib/kite-api'

const istDay = (offsetDays = 0) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' })
  .format(new Date(Date.now() + offsetDays * 86_400_000))

function Stats({ s }: { s?: BacktestStats }) {
  if (!s || !s.trades) return <span className="text-[var(--text-4)]">no trades</span>
  return (
    <span className="es-mono">
      {s.trades} trades · win {Math.round((s.win_rate ?? 0) * 100)}% · avg {rMult(s.avg_r_net)} · net {inr(s.pnl_net)}
      {' '}· max DD {inr(s.max_drawdown)}{s.drawdown_basis ? ` (by ${s.drawdown_basis})` : ''}
    </span>
  )
}

/** Saved, immutable historical tests of one strategy version. Never a prediction. */
export function StrategyBacktests({ strategy }: { strategy: Strategy }) {
  const [start, setStart] = useState(istDay(-30))
  const [end, setEnd] = useState(istDay(-1))
  const [split, setSplit] = useState('')
  const [syms, setSyms] = useState('')
  const [runs, setRuns] = useState<BacktestSummary[]>([])
  const [open, setOpen] = useState<Backtest | null>(null)
  const [compare, setCompare] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try { setRuns(await kite.backtests(strategy.id)) } catch (e) { setError(kiteError(e)) }
  }, [strategy.id])
  useEffect(() => { void load() }, [load])

  async function run() {
    setBusy(true); setError(null)
    try {
      const symbols = syms.split(',').map((x) => x.trim()).filter(Boolean)
      setOpen(await kite.runBacktest({
        strategy_id: strategy.id, start_date: start, end_date: end,
        split_date: split || null, symbols: symbols.length ? symbols : null,
      }))
      void load()
    } catch (e) { setError(kiteError(e)) } finally { setBusy(false) }
  }

  const compared = runs.filter((r) => compare.includes(r.id))
  return (
    <div className="space-y-2 rounded-[var(--r-sm)] border border-[var(--line-1)] p-2 text-xs">
      <p className="text-[var(--text-3)]">
        Replays v{strategy.version ?? 1} on past {strategy.interval ?? '5m'} bars with the same entry, exit and sizing rules.
        Up to 60 days and 20 symbols, replayed in time order on one virtual account with your desk risk limits.
        {strategy.scanner_id ? ' The scanner is replayed on daily bars before each date over the universe you list.' : ''}
        {' '}Historical simulation only — not a prediction.
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1.5"><span>From</span>
          <Input type="date" className="h-8 w-36" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label className="flex flex-col gap-1.5"><span>To</span>
          <Input type="date" className="h-8 w-36" value={end} max={istDay()} onChange={(e) => setEnd(e.target.value)} /></label>
        <label className="flex flex-col gap-1.5"><span>Test period from (optional)</span>
          <Input type="date" className="h-8 w-36" value={split} onChange={(e) => setSplit(e.target.value)} /></label>
        <label className="flex flex-col gap-1.5"><span>
          {strategy.scanner_id ? 'Frozen universe for the scanner' : "Symbols (blank = strategy's)"}</span>
          <Input className="h-8 w-48 es-mono" value={syms} placeholder={strategy.symbols.join(', ')}
            onChange={(e) => setSyms(e.target.value.toUpperCase())} /></label>
        <Button disabled={busy} onClick={() => void run()}>{busy ? 'Running…' : 'Run backtest'}</Button>
      </div>
      {error && <p className="text-[var(--err)]">{error}</p>}

      {runs.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Compare</TableHead><TableHead>Run</TableHead><TableHead>Version</TableHead>
              <TableHead>Period</TableHead><TableHead>Result (net, estimated charges)</TableHead><TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((r) => (
              <TableRow key={r.id}>
                <TableCell>
                  <input type="checkbox" aria-label={`compare ${r.id}`} checked={compare.includes(r.id)}
                    onChange={(e) => setCompare(e.target.checked ? [...compare, r.id].slice(-3) : compare.filter((x) => x !== r.id))} />
                </TableCell>
                <TableCell className="es-mono">{r.created_at.slice(0, 16).replace('T', ' ')}</TableCell>
                <TableCell>v{r.strategy_version}</TableCell>
                <TableCell className="es-mono">{r.config.start_date} → {r.config.end_date}</TableCell>
                <TableCell><Stats s={r.summary.all} /></TableCell>
                <TableCell>
                  <Button size="sm" variant="ghost" onClick={() => void kite.backtest(r.id).then(setOpen, (e) => setError(kiteError(e)))}>
                    Open
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {compared.length > 1 && (
        <div className="space-y-1">
          <p className="font-semibold">Comparison</p>
          {compared.map((r) => (
            <div key={r.id} className="flex flex-wrap gap-2">
              <span>v{r.strategy_version} {r.config.start_date}→{r.config.end_date}</span>
              <span>all: <Stats s={r.summary.all} /></span>
              {r.summary.test && <span>test: <Stats s={r.summary.test} /></span>}
            </div>
          ))}
        </div>
      )}

      {open && <BacktestResult b={open} />}
    </div>
  )
}

export function BacktestResult({ b }: { b: Backtest }) {
  return (
    <div className="space-y-2" data-testid="backtest-result">
      <p className="text-[var(--warn)]">{b.disclaimer}</p>
      <div className="space-y-0.5">
        <div>All: <Stats s={b.summary.all} /></div>
        {b.summary.train && <div>Train (before {b.config.split_date}): <Stats s={b.summary.train} /></div>}
        {b.summary.test && <div>Test (from {b.config.split_date}): <Stats s={b.summary.test} /></div>}
        <div>Estimated charges {inr(b.costs.charges)}</div>
      </div>
      {b.risk?.warnings.length > 0 && (
        <ul className="list-disc pl-4 text-[var(--text-3)]">{b.risk.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
      )}
      {b.scanner && (
        <details>
          <summary className="cursor-pointer">Scanner replay over {b.scanner.universe.join(', ') || 'no universe'}</summary>
          <ul>{Object.entries(b.scanner.matches).map(([d, m]) => (
            <li key={d} className="es-mono">{d}: {m.length ? m.join(', ') : 'no matches'}</li>
          ))}</ul>
        </details>
      )}
      <div className="flex flex-wrap gap-1">
        {Object.entries(b.coverage).map(([sym, c]) => {
          const bad = Object.entries(c.days).filter(([, v]) => v !== 'complete')
          return (
            <Pill key={sym} tone={c.status === 'ok' ? 'ok' : c.status === 'partial' ? 'warn' : 'err'}>
              {sym} {c.status.replaceAll('_', ' ')}{bad.length ? ` · ${bad.length} day(s) skipped` : ''}
              {b.sources[sym] ? ` · ${b.sources[sym]}` : ''}
            </Pill>
          )
        })}
      </div>
      {b.trades.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Date</TableHead><TableHead>Symbol</TableHead><TableHead>Signal</TableHead>
              <TableHead>Entry</TableHead><TableHead>Exit</TableHead><TableHead>Net</TableHead><TableHead>Split</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {b.trades.map((t, i) => (
              <TableRow key={i}>
                <TableCell className="es-mono">{t.trade_date}</TableCell>
                <TableCell className="es-mono">{t.symbol}</TableCell>
                <TableCell className="es-mono">{t.signal_time}</TableCell>
                <TableCell className="es-mono">{t.entry_fill ? `${t.qty} @ ${t.entry_fill.price} ${t.entry_fill.time}` : t.status}</TableCell>
                <TableCell className="es-mono">{t.exit_fill ? `${t.exit_fill.price} ${t.exit_fill.reason}` : '—'}</TableCell>
                <TableCell className="es-mono">{t.pnl_net != null ? `${inr(t.pnl_net)} · ${rMult(t.r_net)}` : '—'}</TableCell>
                <TableCell>{t.split ?? '—'}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {b.skipped.length > 0 && (
        <details>
          <summary className="cursor-pointer">{b.skipped.length} signal(s) not traded</summary>
          <ul>{b.skipped.map((x, i) => (
            <li key={i} className="es-mono">{x.date} {x.signal_time ?? ''} {x.symbol}: {(x.reasons ?? [x.reason]).join(' · ')}</li>
          ))}</ul>
        </details>
      )}
      <details>
        <summary className="cursor-pointer">Assumptions</summary>
        <ul className="list-disc pl-4">{b.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
      </details>
    </div>
  )
}
