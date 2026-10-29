import { createFileRoute } from '@tanstack/react-router'
import { Fragment, useEffect, useState } from 'react'
import { DeskFooter, DeskNav } from '#/components/desk/DeskNav'
import { Topbar } from '#/components/shared/Topbar'
import { Button } from '#/components/ui/button'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '#/components/ui/table'
import { api } from '#/lib/api'
import type { PaperLedger, PaperTrade } from '#/lib/desk-types'
import { inr, rMult } from '#/lib/desk-format'

export const Route = createFileRoute('/desk/paper')({
  ssr: false,
  component: PaperPage,
})

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-[var(--r-md)] border border-[var(--line-1)] bg-[var(--bg-1)] px-3 py-2">
      <div className="text-[10.5px] uppercase tracking-wider text-[var(--text-4)]">{label}</div>
      <div className="es-mono mt-0.5 text-lg" style={{ color: tone }}>{value}</div>
    </div>
  )
}

function PaperPage() {
  const [ledger, setLedger] = useState<PaperLedger | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = () => api.desk.paper().then(setLedger).catch((e) => setError(String(e)))
  useEffect(() => {
    load()
    const t = setInterval(load, 60_000)
    return () => clearInterval(t)
  }, [])

  async function close(t: PaperTrade) {
    let px: string | null = ''
    if (t.status === 'open') {
      px = window.prompt(`Close ${t.symbol} at what price? (blank = last delayed close)`, '')
      if (px === null) return
    }
    try {
      await api.desk.closePaper(t.id, px ? Number(px) : undefined)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const s = ledger?.stats
  return (
    <div className="min-h-screen">
      <Topbar state="idle" />
      <main className="mx-auto w-full max-w-[1400px] space-y-6 px-6 py-4">
        <h1 className="text-2xl font-bold">Paper ledger</h1>
        <DeskNav />
        {error && <p className="text-sm text-[var(--err)]">{error}</p>}
        {ledger && (
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
            <Stat label="Capital" value={inr(ledger.capital, true)} />
            <Stat label="Free cash" value={inr(ledger.cash, true)} />
            <Stat label="Net P&L" value={inr(s?.pnl_net ?? 0)}
              tone={(s?.pnl_net ?? 0) >= 0 ? 'var(--ok)' : 'var(--err)'} />
            <Stat label="Trades" value={String(s?.trades ?? 0)} />
            <Stat label="Win rate" value={s?.win_rate != null ? `${(s.win_rate * 100).toFixed(0)}%` : '—'} />
            <Stat label="Avg R (net)" value={rMult(s?.avg_r_net)} />
            <Stat label="Charges paid" value={inr(s?.charges ?? 0)} />
          </div>
        )}
        <p className="text-xs text-[var(--text-4)]">
          Fills use each plan's 1-minute or 5-minute bars from the available feed: entries only on bars that start after you
          confirm, the stop is checked before the target in every bar, a gap through the stop
          fills at the open (worse than planned), and anything open is closed at your flat-by.
        </p>
        <div className="overflow-auto rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)]">
          <Table>
            <TableHeader>
              <TableRow>
                {['Date', 'Symbol', 'Qty', 'Status', 'Entry', 'Exit', 'Gross', 'Charges', 'Net', 'R net', ''].map((h) => (
                  <TableHead key={h} className="es-team-label whitespace-nowrap">{h}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {ledger?.trades.map((t) => (
                <Fragment key={t.id}>
                  <TableRow className="cursor-pointer" onClick={() => setOpen(open === t.id ? null : t.id)}>
                    <TableCell className="es-mono">{t.trade_date}</TableCell>
                    <TableCell className="font-medium">{t.symbol}{t.data_warning && <div role="status" className="text-xs text-[var(--warn)]">{t.data_warning}</div>}<div className="text-xs text-[var(--text-4)]">{t.interval ?? '5m'} bars</div></TableCell>
                    <TableCell className="es-mono">{t.qty}</TableCell>
                    <TableCell><span className={`es-pill ${t.status === 'open' ? 'run' : t.status === 'closed' ? '' : 'info'}`}>{t.status}</span></TableCell>
                    <TableCell className="es-mono">{t.entry_fill ? `${inr(t.entry_fill.price)} ${t.entry_fill.time}` : '—'}</TableCell>
                    <TableCell className="es-mono">{t.exit_fill ? `${inr(t.exit_fill.price)} ${t.exit_fill.reason}` : '—'}</TableCell>
                    <TableCell className="es-mono">{t.pnl_gross != null ? inr(t.pnl_gross) : '—'}</TableCell>
                    <TableCell className="es-mono">{t.charges != null ? inr(t.charges) : '—'}</TableCell>
                    <TableCell className="es-mono" style={{ color: (t.pnl_net ?? 0) >= 0 ? 'var(--ok)' : 'var(--err)' }}>
                      {t.pnl_net != null ? inr(t.pnl_net) : '—'}
                    </TableCell>
                    <TableCell className="es-mono">{rMult(t.r_net)}</TableCell>
                    <TableCell className="text-right">
                      {(t.status === 'open' || t.status === 'pending') && (
                        <Button size="sm" variant="outline" onClick={(e) => { e.stopPropagation(); close(t) }}>
                          {t.status === 'open' ? 'Close' : 'Cancel'}
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                  {open === t.id && (
                    <TableRow>
                      <TableCell colSpan={11} className="space-y-0.5 bg-[var(--bg-2)] text-xs">
                        {t.events.map((e, i) => <div key={i}><span className="es-mono">{e.at}</span> {e.what}</div>)}
                        {t.mae != null && <div className="text-[var(--text-4)]">Worst {rMult(t.mae)} · best {rMult(t.mfe)} while open</div>}
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              ))}
              {!ledger?.trades.length && (
                <TableRow><TableCell colSpan={11} className="text-sm text-[var(--text-3)]">
                  No paper trades yet. Confirm a ticket as a paper trade from a plan.
                </TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </div>
        <DeskFooter />
      </main>
    </div>
  )
}
