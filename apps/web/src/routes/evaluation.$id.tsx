import { Link, createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { Topbar } from '#/components/shared/Topbar'
import { Button } from '#/components/ui/button'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '#/components/ui/table'
import { evaluationApi, type BatchView } from '#/lib/evaluation-api'
import { cellLabel, signedFrac } from '#/lib/evaluation-format'
import { pct01 } from '#/lib/desk-format'

export const Route = createFileRoute('/evaluation/$id')({
  ssr: false,
  component: BatchPage,
})

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-[var(--r-md)] border border-[var(--line-1)] bg-[var(--bg-1)] px-3 py-2">
      <div className="text-[10.5px] uppercase tracking-wider text-[var(--text-4)]">{label}</div>
      <div className="es-mono mt-0.5 text-lg">{value}</div>
    </div>
  )
}

function BatchPage() {
  const { id } = Route.useParams()
  const [view, setView] = useState<BatchView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = () => evaluationApi.getBatch(id).then(setView).catch((e) => setError(e instanceof Error ? e.message : String(e)))
  useEffect(() => {
    load()
    const t = setInterval(load, 15_000)
    return () => clearInterval(t)
  }, [id])

  async function stop() {
    try {
      const next = await evaluationApi.cancelBatch(id)
      setView(next)
      const r = next.cancel_result
      if (r) setNotice(`${r.cleared} queued ${r.cleared === 1 ? 'run' : 'runs'} cleared.` +
        (r.starting ? ` ${r.starting} already starting will finish and may use a credit.` : ''))
    } catch (e) { setError(String(e)) }
  }

  const s = view?.summary
  const ratings = Object.entries(s?.by_rating ?? {})
  return (
    <div className="min-h-screen">
      <Topbar state="idle" />
      <main data-guide="guide-backtests" className="mx-auto w-full max-w-[1100px] space-y-6 px-6 py-4">
        <div className="flex items-center gap-3">
          <Link to="/evaluation" className="text-sm">← Batches</Link>
          <h1 className="text-2xl font-bold">Backtest batch</h1>
          <div className="flex-1" />
          {view && !view.cancelled && view.cells.some((c) => c.phase === 'queued') && (
            <Button variant="outline" size="sm" onClick={stop}>Stop queued runs</Button>
          )}
        </div>
        {error && <p className="text-sm text-[var(--err)]">{error}</p>}
        {notice && <p role="status" className="text-sm">{notice}</p>}
        {view && s && (
          <>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Stat label="Scored" value={s.scored} />
              <Stat label="Pending outcome" value={s.pending} />
              <Stat label="Queued / running" value={s.running} />
              <Stat label="Failed / stopped" value={s.failed} />
            </div>

            <section className="space-y-2">
              <h2 className="text-lg font-semibold">By rating</h2>
              {ratings.length ? (
                <div className="overflow-auto rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)]">
                  <Table>
                    <TableHeader><TableRow>
                      {['Rating', 'Scored calls', 'Direction right', 'Mean alpha'].map((h) =>
                        <TableHead key={h} className="es-team-label">{h}</TableHead>)}
                    </TableRow></TableHeader>
                    <TableBody>
                      {ratings.map(([rating, r]) => (
                        <TableRow key={rating}>
                          <TableCell className="font-medium">{rating}</TableCell>
                          <TableCell className="es-mono">{r.count}</TableCell>
                          <TableCell className="es-mono">{r.hit_rate == null ? 'no direction claimed' : pct01(r.hit_rate, 0)}</TableCell>
                          <TableCell className="es-mono" style={{ color: r.mean_alpha >= 0 ? 'var(--ok)' : 'var(--err)' }}>
                            {signedFrac(r.mean_alpha)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              ) : (
                <p className="text-sm text-[var(--text-3)]">Nothing scored yet.</p>
              )}
              <p className="text-xs text-[var(--text-4)]">
                Alpha is the return over {view.holding_days} trading days from the first close after
                each analysis date, minus the regional benchmark over the same dates. A call is scored
                only once that whole window has real closes; until then it is pending. One model
                sampling per date, so treat these as indicative, not repeatable.
              </p>
            </section>

            <section className="overflow-auto rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)]">
              <Table>
                <TableHeader><TableRow>
                  {['Ticker', 'Date', 'Rating', 'Outcome', 'Raw', ''].map((h) =>
                    <TableHead key={h} className="es-team-label">{h}</TableHead>)}
                </TableRow></TableHeader>
                <TableBody>
                  {view.cells.map((c) => (
                    <TableRow key={`${c.ticker}-${c.date}`}>
                      <TableCell className="font-medium">{c.ticker}</TableCell>
                      <TableCell className="es-mono">{c.date}</TableCell>
                      <TableCell>{c.rating ?? '—'}</TableCell>
                      <TableCell className="text-sm">
                        <span className={`es-pill ${c.phase === 'scored' ? '' : c.phase === 'running' || c.phase === 'queued' || c.phase === 'submitting' ? 'run' : 'info'}`}>{c.phase}</span>{' '}
                        {cellLabel(c)}
                      </TableCell>
                      <TableCell className="es-mono">{c.score?.raw_return != null ? signedFrac(c.score.raw_return) : '—'}</TableCell>
                      <TableCell className="text-right">
                        {c.run_id && <Link to="/runs/$id" params={{ id: c.run_id }}>Report</Link>}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </section>
          </>
        )}
      </main>
    </div>
  )
}
