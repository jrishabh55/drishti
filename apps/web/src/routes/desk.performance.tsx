import { createFileRoute } from '@tanstack/react-router'
import { DeskFooter, DeskNav } from '#/components/desk/DeskNav'
import { portfolioName, usePortfolios, useSelectedPortfolioData } from '#/components/desk/Portfolios'
import { Topbar } from '#/components/shared/Topbar'
import { TradingAnalytics } from '#/components/desk/TradingAnalytics'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '#/components/ui/table'
import { evaluationApi, type PaperPerformance } from '#/lib/evaluation-api'
import { linePoints } from '#/lib/evaluation-format'
import { inr, pct01, rMult } from '#/lib/desk-format'

export const Route = createFileRoute('/desk/performance')({
  ssr: false,
  component: PerformancePage,
})

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-[var(--r-md)] border border-[var(--line-1)] bg-[var(--bg-1)] px-3 py-2">
      <div className="text-[10.5px] uppercase tracking-wider text-[var(--text-4)]">{label}</div>
      <div className="es-mono mt-0.5 text-lg" style={{ color: tone }}>{value}</div>
    </div>
  )
}

function Spark({ values, color, label, fill = false }: { values: number[]; color: string; label: string; fill?: boolean }) {
  const w = 600
  const h = 120
  const pts = linePoints(values, w, h)
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-32 w-full" role="img" aria-label={label} preserveAspectRatio="none">
      {fill && pts && <polygon points={`2,2 ${pts} ${w - 2},2`} fill={color} opacity={0.15} />}
      <polyline points={pts} fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

function PerformancePage() {
  const pf = usePortfolios()
  // The selected portfolio's performance; a switch drops the previous one's late answer.
  const { data: perf, error } = useSelectedPortfolioData<PaperPerformance>(evaluationApi.paperPerformance)

  const s = perf?.stats
  const c = perf?.calibration
  return (
    <div className="min-h-screen">
      <Topbar state="idle" />
      <main data-guide="guide-performance" className="mx-auto w-full max-w-[1400px] space-y-6 px-6 py-4">
        <h1 className="text-2xl font-bold">
          Paper performance{perf && <span className="text-[var(--text-3)]"> — {portfolioName(pf?.portfolios, perf.portfolio_id)}</span>}
        </h1>
        <DeskNav />
        {error && <p className="text-sm text-[var(--err)]">{error}</p>}
        {perf && s && (
          <>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
              <Stat label="Equity" value={inr(perf.equity, true)} tone={perf.equity >= perf.capital ? 'var(--ok)' : 'var(--err)'} />
              <Stat label="Net P&L" value={inr(s.pnl_net ?? 0)} />
              <Stat label="Closed trades" value={String(s.trades)} />
              <Stat label="Win rate" value={pct01(s.win_rate, 0)} />
              <Stat label="Profit factor" value={s.profit_factor != null ? s.profit_factor.toFixed(2) : '—'} />
              <Stat label="Avg R (net)" value={rMult(s.avg_r_net)} />
              <Stat label="Expectancy / trade" value={s.expectancy_net != null ? inr(s.expectancy_net) : '—'} />
              <Stat label="Max drawdown" value={s.max_drawdown != null ? `${inr(s.max_drawdown, true)} (${(s.max_drawdown_pct ?? 0).toFixed(2)}%)` : '—'} />
            </div>

            {perf.curve.length > 1 ? (
              <div className="grid gap-4 md:grid-cols-2">
                <div className="es-card p-3">
                  <div className="text-sm font-medium">Equity (net of charges)</div>
                  <Spark values={perf.curve.map((p) => p.equity)} color="var(--accent)" label="Equity curve" />
                </div>
                <div className="es-card p-3">
                  <div className="text-sm font-medium">Drawdown from peak</div>
                  <Spark values={perf.curve.map((p) => -p.drawdown)} color="var(--err)" label="Drawdown" fill />
                </div>
              </div>
            ) : (
              <p className="text-sm text-[var(--text-3)]">No closed paper trades yet: equity and drawdown appear after the first one.</p>
            )}

            <section className="space-y-2">
              <h2 className="text-lg font-semibold">By day</h2>
              <div className="overflow-auto rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)]">
                <Table>
                  <TableHeader><TableRow>
                    {['Date', 'Trades', 'Wins', 'Net P&L', 'Charges', 'R (net)'].map((h) =>
                      <TableHead key={h} className="es-team-label">{h}</TableHead>)}
                  </TableRow></TableHeader>
                  <TableBody>
                    {perf.days.map((d) => (
                      <TableRow key={d.date}>
                        <TableCell className="es-mono">{d.date}</TableCell>
                        <TableCell className="es-mono">{d.trades}</TableCell>
                        <TableCell className="es-mono">{d.wins}</TableCell>
                        <TableCell className="es-mono" style={{ color: d.pnl_net >= 0 ? 'var(--ok)' : 'var(--err)' }}>{inr(d.pnl_net)}</TableCell>
                        <TableCell className="es-mono">{inr(d.charges)}</TableCell>
                        <TableCell className="es-mono">{rMult(d.r_net)}</TableCell>
                      </TableRow>
                    ))}
                    {!perf.days.length && (
                      <TableRow><TableCell colSpan={6} className="text-sm text-[var(--text-3)]">No closed trades.</TableCell></TableRow>
                    )}
                  </TableBody>
                </Table>
              </div>
            </section>

            <section className="space-y-2">
              <h2 className="text-lg font-semibold">Time Machine: expected vs what happened</h2>
              {c ? (
                <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
                  <Stat label={`Target hit (${c.trades} replayed)`} value={`${pct01(c.expected_target_rate, 0)} → ${pct01(c.actual_target_rate, 0)}`} />
                  <Stat label="Stop hit" value={`${pct01(c.expected_stop_rate, 0)} → ${pct01(c.actual_stop_rate, 0)}`} />
                  <Stat label="Avg R (gross)" value={`${rMult(c.expected_avg_r)} → ${rMult(c.actual_avg_r)}`} />
                </div>
              ) : (
                <p className="text-sm text-[var(--text-3)]">
                  None of your closed trades has a Time Machine replay of the same plan on record, so there
                  is nothing to compare. Run the Time Machine on a plan before trading it.
                </p>
              )}
              {perf.comparisons.length > 0 && (
                <div className="overflow-auto rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)]">
                  <Table>
                    <TableHeader><TableRow>
                      {['Date', 'Symbol', 'Expected target / stop', 'Expected avg R', 'Actual', 'R (gross)'].map((h) =>
                        <TableHead key={h} className="es-team-label">{h}</TableHead>)}
                    </TableRow></TableHeader>
                    <TableBody>
                      {perf.comparisons.map((x) => (
                        <TableRow key={x.trade_id}>
                          <TableCell className="es-mono">{x.trade_date}</TableCell>
                          <TableCell className="font-medium">{x.symbol}</TableCell>
                          <TableCell className="es-mono">
                            {x.expected ? `${pct01(x.expected.p_target, 0)} / ${pct01(x.expected.p_stop, 0)}` : 'no replay on record'}
                          </TableCell>
                          <TableCell className="es-mono">{x.expected ? rMult(x.expected.avg_r) : '—'}</TableCell>
                          <TableCell>{x.actual.outcome} <span className="text-xs text-[var(--text-4)]">({x.actual.exit_reason})</span></TableCell>
                          <TableCell className="es-mono">{rMult(x.actual.r_gross)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
              <p className="text-xs text-[var(--text-4)]">
                Expected figures are the stored replay of the exact plan (same symbol, date, entry, stop
                and target); actual fills can differ from the planned entry. A handful of trades says
                little about calibration.
              </p>
            </section>
          </>
        )}
        <TradingAnalytics />
        <DeskFooter />
      </main>
    </div>
  )
}
