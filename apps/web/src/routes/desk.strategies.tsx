import { createFileRoute, Link } from '@tanstack/react-router'
import { useCallback, useEffect, useState } from 'react'
import { DeskFooter, DeskNav } from '#/components/desk/DeskNav'
import { Card, Pill, tradeTone } from '#/components/kite/KiteBits'
import { BLANK_STRATEGY, MODES, StrategyForm, toInput } from '#/components/kite/StrategyForm'
import { Topbar } from '#/components/shared/Topbar'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { api } from '#/lib/api'
import { inr, rMult } from '#/lib/desk-format'
import type { Ticket } from '#/lib/desk-types'
import {
  confirmWords, kite, kiteError,
  type KitePerformance, type KiteStatus, type KiteTrade, type Strategy, type StrategyInput,
} from '#/lib/kite-api'

export const Route = createFileRoute('/desk/strategies')({
  ssr: false,
  component: StrategiesPage,
})

const istToday = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date())

function StrategiesPage() {
  const [st, setSt] = useState<KiteStatus | null>(null)
  const [trades, setTrades] = useState<KiteTrade[]>([])
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [strats, setStrats] = useState<Strategy[]>([])
  const [perf, setPerf] = useState<KitePerformance | null>(null)
  const [editing, setEditing] = useState<{ id: string | null; init: StrategyInput } | null>(null)
  const [typed, setTyped] = useState<Record<string, string>>({})
  const [ack, setAck] = useState('')
  const [killArmed, setKillArmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [s, t, tk, ss, p] = await Promise.all([kite.status(), kite.trades(), api.desk.tickets(),
        kite.strategies(), kite.performance()])
      setSt(s); setTrades(t); setTickets(tk.filter((x) => x.status === 'ready')); setStrats(ss); setPerf(p)
    } catch (e) {
      setError(kiteError(e))
    }
  }, [])

  useEffect(() => {
    void load()
    const t = window.setInterval(() => void load(), 15_000)
    return () => window.clearInterval(t)
  }, [load])

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError(null)
    try { await fn() } catch (e) { setError(kiteError(e)) } finally { setBusy(false); void load() }
  }

  const ctl = st?.control
  const canOrder = !!st && st.orders.blocked_by.length === 0

  return (
    <div className="min-h-screen">
      <Topbar state="idle" />
      <main className="mx-auto w-full max-w-[1100px] space-y-6 px-6 py-4">
        <h1 className="text-2xl font-bold">Strategies &amp; live orders</h1>
        <DeskNav />
        {error && <p className="text-sm text-[var(--err)]">{error}</p>}

        {st && ctl && (
          <Card title="Today's arm state" hint="Nothing is sent to Zerodha unless every check on the connection page passes and you type the order's confirmation.">
            <div className="flex flex-wrap items-center gap-2">
              <Pill tone={ctl.killed ? 'err' : ctl.armed ? 'ok' : 'warn'}>
                {ctl.killed ? 'KILLED for today' : ctl.armed ? 'ARMED' : 'DISARMED'}
              </Pill>
              {st.session !== 'active' && <Link to="/desk/connection" className="text-sm">Log in to Zerodha →</Link>}
              {!ctl.armed && !ctl.killed && (
                <>
                  <Input className="w-48 es-mono" placeholder={`ARM ${istToday()}`} value={ack}
                    onChange={(e) => setAck(e.target.value.toUpperCase())} />
                  <Button disabled={busy || !ack} onClick={() => void run(() => kite.arm(ack).then(() => setAck('')))}>Arm today</Button>
                </>
              )}
              {ctl.armed && <Button variant="outline" disabled={busy} onClick={() => void run(() => kite.disarm())}>Disarm</Button>}
              <Button variant="destructive" disabled={busy}
                onClick={() => {
                  if (!killArmed) { setKillArmed(true); return }
                  setKillArmed(false)
                  void run(() => kite.kill())
                }}>
                {killArmed ? 'Really kill? Exits all live trades' : 'Kill switch'}
              </Button>
            </div>
            {st.orders.blocked_by.length > 0 && (
              <p className="text-xs text-[var(--text-3)]">Live orders blocked: {st.orders.blocked_by.join(' · ')}</p>
            )}
            {ctl.kill_summary && ctl.killed && (
              <p className="text-xs text-[var(--text-3)]">Kill: {ctl.kill_summary.exits_sent} exits sent,
                {' '}{ctl.kill_summary.needs_attention} need attention{ctl.kill_summary.error ? ` · ${ctl.kill_summary.error}` : ''}</p>
            )}
          </Card>
        )}

        <Card title="Ready desk tickets" hint="Send a risk-checked desk ticket to Zerodha as entry + broker-side stop and target. Type the words shown to confirm.">
          {tickets.length === 0 && <p className="text-sm text-[var(--text-4)]">No ready tickets. Make one from a plan.</p>}
          {tickets.map((tk) => tk.order && (
            <div key={tk.id} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="flex-1">{tk.words}</span>
              <Input className="w-48 es-mono" placeholder={confirmWords(tk.order.qty, tk.order.symbol)}
                value={typed[tk.id] ?? ''} onChange={(e) => setTyped({ ...typed, [tk.id]: e.target.value.toUpperCase() })} />
              <Button disabled={busy || !canOrder || typed[tk.id] !== confirmWords(tk.order.qty, tk.order.symbol)}
                onClick={() => void run(() => kite.sendTicket(tk.id, typed[tk.id]))}>Send to Zerodha</Button>
            </div>
          ))}
        </Card>

        <Card title="Live trades" hint="Kite's order book is the source of truth; trades reconcile every 15 s while the market is open.">
          {trades.length === 0 && <p className="text-sm text-[var(--text-4)]">No live trades yet.</p>}
          {trades.map((t) => (
            <details key={t.id} className="rounded-[var(--r-sm)] border border-[var(--line-1)] p-2 text-sm">
              <summary className="flex cursor-pointer flex-wrap items-center gap-2">
                <span className="es-mono font-semibold">{t.symbol}</span>
                <span className="es-mono">{t.filled_qty || t.qty} @ {t.entry_avg ?? t.limit ?? 'MKT'}</span>
                <span className="text-[var(--text-3)]">stop {t.stop} · target {t.target} · flat by {t.flat_by}</span>
                <Pill tone={tradeTone(t.status)}>{t.status.replace('_', ' ')}{t.exit_reason ? ` · ${t.exit_reason}` : ''}</Pill>
                {t.pnl_net != null && <span className="es-mono">{inr(t.pnl_net)} · {rMult(t.r_net)}</span>}
              </summary>
              {t.attention && <p className="mt-1 text-[var(--err)]">{t.attention}</p>}
              <ul className="mt-1 space-y-0.5 text-xs text-[var(--text-3)]">
                {t.events.map((e, i) => <li key={i}><span className="es-mono">{e.at}</span> {e.what}</li>)}
              </ul>
              {(t.status === 'entry_sent' || t.status === 'protected' || t.status === 'exit_sent') && (
                <div className="mt-2 flex gap-2">
                  <Button size="sm" variant="outline" disabled={busy} onClick={() => void run(() => kite.refreshTrade(t.id))}>Refresh</Button>
                  {t.status !== 'exit_sent' && (
                    <Button size="sm" variant="destructive" disabled={busy} onClick={() => void run(() => kite.exitTrade(t.id))}>Exit now</Button>
                  )}
                </div>
              )}
            </details>
          ))}
          {perf && perf.trades > 0 && (
            <p className="text-xs text-[var(--text-3)]">
              {perf.trades} closed · win rate {Math.round((perf.win_rate ?? 0) * 100)}% · avg {rMult(perf.avg_r_net)} ·
              net {inr(perf.pnl_net)} · charges {inr(perf.charges)} · max drawdown {inr(perf.max_drawdown)}
            </p>
          )}
        </Card>

        <Card title="Strategies" hint="Bounded: up to 20 symbols, a few signals a day, Off / Paper / Confirm. Signals use LIVE bars when you're streaming, otherwise DELAYED, and say which.">
          {editing ? (
            <StrategyForm initial={editing.init} busy={busy} onCancel={() => setEditing(null)}
              onSave={(body) => void run(async () => {
                if (editing.id) await kite.updateStrategy(editing.id, body)
                else await kite.createStrategy(body)
                setEditing(null)
              })} />
          ) : (
            <Button variant="outline" onClick={() => setEditing({ id: null, init: BLANK_STRATEGY })}>New strategy</Button>
          )}
          {strats.map((s) => (
            <div key={s.id} className="space-y-2 rounded-[var(--r-sm)] border border-[var(--line-1)] p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{s.name}</span>
                <Pill tone={s.mode === 'off' ? 'warn' : s.mode === 'paper' ? 'info' : 'ok'}>
                  {MODES.find((m) => m.value === s.mode)?.label}
                </Pill>
                <span className="es-mono text-[var(--text-3)]">{s.symbols.join(' ')}</span>
                {s.paused_reason && <span className="text-xs text-[var(--err)]">stopped by {s.paused_reason}</span>}
                <span className="flex-1" />
                <Button size="sm" variant="outline" disabled={busy || s.mode === 'off'}
                  onClick={() => void run(() => kite.scanStrategy(s.id))}>Scan now</Button>
                <Button size="sm" variant="outline" onClick={() => setEditing({ id: s.id, init: toInput(s) })}>Edit</Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => void run(() => kite.deleteStrategy(s.id))}>Delete</Button>
              </div>
              {s.signals.slice().reverse().slice(0, 8).map((g) => {
                const words = g.order ? confirmWords(g.order.qty, g.symbol) : ''
                const key = `${s.id}:${g.id}`
                return (
                  <div key={g.id} className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="es-mono">{g.date} {g.symbol}</span>
                    <Pill tone={g.status === 'await_confirm' ? 'run' : g.status === 'blocked' ? 'err' : 'info'}>{g.status.replace('_', ' ')}</Pill>
                    <Pill tone={g.source.startsWith('live') ? 'ok' : 'warn'}>{g.source.startsWith('live') ? 'LIVE' : 'DELAYED'}</Pill>
                    {g.reason && <span className="text-[var(--text-3)]">{g.reason}</span>}
                    {g.status === 'await_confirm' && g.order && (
                      <>
                        <span>{words} @ {g.order.price ?? 'MKT'} · stop {g.order.stop} · target {g.order.target}</span>
                        <Input className="h-7 w-40 es-mono" placeholder={words} value={typed[key] ?? ''}
                          onChange={(e) => setTyped({ ...typed, [key]: e.target.value.toUpperCase() })} />
                        <Button size="sm" disabled={busy || !canOrder || typed[key] !== words}
                          onClick={() => void run(() => kite.confirmSignal(s.id, g.id, typed[key]))}>Send</Button>
                        <Button size="sm" variant="ghost" disabled={busy}
                          onClick={() => void run(() => kite.skipSignal(s.id, g.id))}>Skip</Button>
                      </>
                    )}
                  </div>
                )
              })}
            </div>
          ))}
        </Card>
        <DeskFooter />
      </main>
    </div>
  )
}
