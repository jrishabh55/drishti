import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import { DeskSymbolSearch } from '#/components/desk/DeskSymbolSearch'
import { DeskFooter, DeskNav } from '#/components/desk/DeskNav'
import { SourceBadge } from '#/components/desk/SourceBadge'
import { Topbar } from '#/components/shared/Topbar'
import { Button } from '#/components/ui/button'
import { Switch } from '#/components/ui/switch'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '#/components/ui/table'
import { api } from '#/lib/api'
import type { AlertEvent, EntryStatus, OrbScan, PaperLedger } from '#/lib/desk-types'
import { inr, orbEmptyMessage, rMult, signedPct } from '#/lib/desk-format'

export const Route = createFileRoute('/desk/')({
  ssr: false,
  component: DeskToday,
})

const REFRESH_MS = 120_000
const STATUS_PILL: Record<EntryStatus['status'], string> = {
  triggered: 'ok', waiting: 'run', expired: '', 'no-data': 'warn',
}

function DeskToday() {
  const navigate = useNavigate()
  const [scan, setScan] = useState<OrbScan | null>(null)
  const [scanError, setScanError] = useState<string | null>(null)
  const [scanning, setScanning] = useState(false)
  const [auto, setAuto] = useState(true)
  const [watch, setWatch] = useState<string[]>([])
  const [statuses, setStatuses] = useState<Record<string, EntryStatus | string>>({})
  const [ledger, setLedger] = useState<PaperLedger | null>(null)
  const [events, setEvents] = useState<AlertEvent[]>([])
  const [planning, setPlanning] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const scanInFlight = useRef(false)
  const runScan = useCallback(async () => {
    if (scanInFlight.current) return
    scanInFlight.current = true
    setScanning(true)
    try {
      setScan(await api.desk.orb({}, AbortSignal.timeout(20_000)))
      setScanError(null)
    } catch (e) {
      setScanError(e instanceof Error ? e.message : String(e))
    } finally {
      scanInFlight.current = false
      setScanning(false)
    }
  }, [])

  const refreshWatch = useCallback(async (symbols: string[]) => {
    const pairs = await Promise.all(symbols.map(async (s) => {
      try {
        return [s, await api.desk.entry(s)] as const
      } catch (e) {
        return [s, e instanceof Error ? e.message : String(e)] as const
      }
    }))
    setStatuses(Object.fromEntries(pairs))
  }, [])

  const refreshAll = useCallback(async () => {
    runScan()
    api.desk.paper().then(setLedger).catch(() => {})
    api.desk.alertEvents(true).then(setEvents).catch(() => {})
    const w = await api.desk.watchlist().catch(() => [] as string[])
    setWatch(w)
    refreshWatch(w)
  }, [runScan, refreshWatch])

  useEffect(() => { refreshAll() }, [refreshAll])
  useEffect(() => {
    if (!auto) return
    const t = setInterval(refreshAll, REFRESH_MS)
    return () => clearInterval(t)
  }, [auto, refreshAll])

  async function plan(symbol: string) {
    setPlanning(symbol); setError(null)
    try {
      const p = await api.desk.createPlan({ symbol })
      navigate({ to: '/desk/plans/$id', params: { id: p.id } })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setPlanning(null)
    }
  }

  async function addWatch(symbol: string) {
    const w = await api.desk.watch(symbol)
    setWatch(w)
    refreshWatch(w)
  }

  async function removeWatch(s: string) {
    await api.desk.unwatch(s).catch(() => {})
    const w = watch.filter((x) => x !== s)
    setWatch(w)
  }

  const today = ledger?.trades.filter((t) => t.trade_date === scan?.date) ?? []
  const openTrades = ledger?.trades.filter((t) => t.status === 'open' || t.status === 'pending') ?? []
  const todayNet = today.reduce((a, t) => a + (t.pnl_net ?? 0), 0)

  return (
    <div className="min-h-screen">
      <Topbar state="idle" />
      <main className="mx-auto w-full max-w-[1400px] space-y-6 px-6 py-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold">Trade Desk</h1>
            <p className="text-sm text-[var(--text-3)]">
              Scan → entry → exit plan → Time Machine → size → risk check → your yes.
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs text-[var(--text-3)]">
            <Switch checked={auto} onCheckedChange={setAuto} id="auto" />
            <label htmlFor="auto">Refresh every 2 min</label>
          </div>
        </div>
        <DeskNav />
        {error && <p className="text-sm text-[var(--err)]">{error}</p>}

        {(events.length > 0 || openTrades.length > 0) && (
          <section className="grid gap-3 md:grid-cols-2">
            {events.length > 0 && (
              <div className="space-y-1 rounded-[var(--r-lg)] border border-[var(--run)] bg-[var(--run-bg)] p-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">Alerts ({events.length} new)</span>
                  <Button size="sm" variant="ghost" onClick={() =>
                    api.desk.markEventsSeen().then(() => setEvents([]))}>Dismiss</Button>
                </div>
                {events.slice(0, 5).map((e) => (
                  <p key={e.id} className="text-xs">{e.message}</p>
                ))}
              </div>
            )}
            {openTrades.length > 0 && (
              <div className="space-y-1 rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)] p-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">Open paper trades</span>
                  <Link to="/desk/paper" className="text-xs">Ledger →</Link>
                </div>
                {openTrades.map((t) => (
                  <p key={t.id} className="es-mono text-xs">
                    {t.symbol} {t.qty} · {t.status}
                    {t.entry_fill ? ` @ ${inr(t.entry_fill.price)}` : ''} · stop {inr(t.stop)} · target {inr(t.target)}
                  </p>
                ))}
                <p className="text-xs text-[var(--text-3)]">Today net: {inr(todayNet)}</p>
              </div>
            )}
          </section>
        )}

        <section className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="text-lg font-semibold">Opening-range breakouts</h2>
            {scan && <SourceBadge source={scan.source} asOf={scan.data_as_of} />}
            {scan && <span className="es-pill">range {scan.range} · {scan.universe.toUpperCase()}</span>}
            <Button size="sm" variant="outline" onClick={runScan} disabled={scanning}>
              {scanning ? 'Scanning…' : 'Scan now'}
            </Button>
          </div>
          {scanError && <p className="text-sm text-[var(--text-3)]">{scanError}</p>}
          {scan && (
            <>
              <p className="es-mono text-xs text-[var(--text-4)]">
                {scan.counts.scanned} charts usable of {scan.counts.universe}
                {scan.counts.illiquid ? ` · ${scan.counts.illiquid} under your liquidity floor` : ''}
                {scan.counts.unusable ? ` · ${scan.counts.unusable} with missing bars` : ''}
              </p>
              {scan.notes.map((n) => <p key={n} className="text-xs text-[var(--run)]">{n}</p>)}
              <div className="overflow-auto rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)] shadow-[var(--shadow-1)]">
                <Table>
                  <TableHeader>
                    <TableRow>
                      {['Symbol', 'Closed above', 'Trigger', 'Range', '% over', 'Last', 'Vol ×', '₹ cr/day', ''].map((h) => (
                        <TableHead key={h} className="es-team-label whitespace-nowrap">{h}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {scan.hits.map((h) => (
                      <TableRow key={h.symbol}>
                        <TableCell>
                          <div className="font-medium">{h.symbol}</div>
                          <div className="max-w-48 truncate text-xs text-[var(--text-4)]">{h.name}</div>
                        </TableCell>
                        <TableCell className="es-mono">{h.at}</TableCell>
                        <TableCell className="es-mono">{inr(h.price)}</TableCell>
                        <TableCell className="es-mono text-xs">{inr(h.or_low)} – {inr(h.or_high)}</TableCell>
                        <TableCell className="es-mono">{signedPct(h.pct_over)}</TableCell>
                        <TableCell className="es-mono">{inr(h.last)} <span className="text-xs text-[var(--text-4)]">{signedPct(h.last_vs_or_high_pct)}</span></TableCell>
                        <TableCell className="es-mono">{h.volume_x?.toFixed(2) ?? '—'}</TableCell>
                        <TableCell className="es-mono">{h.avg_value_cr ?? '—'}</TableCell>
                        <TableCell className="whitespace-nowrap text-right">
                          <Link to="/analyse" search={{ ticker: `${h.symbol}.NS` }} className="es-btn sm no-underline">Analyse</Link>
                          <Button size="sm" variant="ghost" onClick={() => api.desk.watch(h.symbol)
                            .then((w) => { setWatch(w); refreshWatch(w) })}>Watch</Button>
                          <Button size="sm" onClick={() => plan(h.symbol)} disabled={planning === h.symbol}>
                            {planning === h.symbol ? 'Planning…' : 'Plan →'}
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                    {!scan.hits.length && (
                      <TableRow><TableCell colSpan={9} className="text-sm text-[var(--text-3)]">
                        {orbEmptyMessage(scan.counts)}
                      </TableCell></TableRow>
                    )}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </section>

        <section className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-lg font-semibold">Watchlist entry timer</h2>
            <Button size="sm" variant="ghost" onClick={() => refreshWatch(watch)}>Refresh</Button>
          </div>
          <DeskSymbolSearch onAdd={addWatch} />
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {watch.map((s) => {
              const st = statuses[s]
              return (
                <div key={s} className="space-y-1 rounded-[var(--r-md)] border border-[var(--line-1)] bg-[var(--bg-1)] p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{s}</span>
                    {typeof st === 'object' && (
                      <span className={`es-pill ${STATUS_PILL[st.status]}`}>
                        <span className="es-dot" />{st.status.toUpperCase()}
                        {st.trigger ? ` ${st.trigger.time} @ ${inr(st.trigger.price)}` : ''}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-[var(--text-3)]">
                    {typeof st === 'string' ? st : st?.reason ?? 'checking…'}
                  </p>
                  {typeof st === 'object' && st.data_as_of && (
                    <SourceBadge source={st.source} asOf={st.data_as_of} />
                  )}
                  <div className="flex gap-1">
                    <Link to="/analyse" search={{ ticker: `${s}.NS` }} className="es-btn sm no-underline">Analyse</Link>
                    {typeof st === 'object' && st.status === 'triggered' && (
                      <Button size="sm" onClick={() => plan(s)} disabled={planning === s}>Plan →</Button>
                    )}
                    <Button size="sm" variant="ghost" onClick={() => removeWatch(s)}>Remove</Button>
                  </div>
                </div>
              )
            })}
            {!watch.length && (
              <p className="text-sm text-[var(--text-3)]">
                Add symbols to watch them bar by bar for an opening-range breakout.
              </p>
            )}
          </div>
        </section>

        {ledger && ledger.stats.trades > 0 && (
          <p className="text-xs text-[var(--text-3)]">
            Paper record: {ledger.stats.trades} trades, win rate{' '}
            {((ledger.stats.win_rate ?? 0) * 100).toFixed(0)}%, average {rMult(ledger.stats.avg_r_net)} net,
            P&amp;L {inr(ledger.stats.pnl_net)} after {inr(ledger.stats.charges)} charges.
          </p>
        )}
        <DeskFooter />
      </main>
    </div>
  )
}
