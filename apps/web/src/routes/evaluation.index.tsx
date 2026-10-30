import { Link, createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { Topbar } from '#/components/shared/Topbar'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '#/components/ui/table'
import { api } from '#/lib/api'
import { evaluationApi, type BatchBody, type BatchQuote, type BatchRow } from '#/lib/evaluation-api'
import type { ConfigResponse } from '#/lib/types'

export const Route = createFileRoute('/evaluation/')({
  ssr: false,
  component: BatchesPage,
})

const iso = (d: Date) => d.toISOString().slice(0, 10)

export function BatchesPage() {
  const navigate = useNavigate()
  const [config, setConfig] = useState<ConfigResponse | null>(null)
  const [rows, setRows] = useState<BatchRow[]>([])
  const [tickers, setTickers] = useState('RELIANCE.NS')
  const [start, setStart] = useState(iso(new Date(Date.now() - 90 * 86_400_000)))
  const [end, setEnd] = useState(iso(new Date(Date.now() - 14 * 86_400_000)))
  const [every, setEvery] = useState(14)
  const [provider, setProvider] = useState('')
  const [quick, setQuick] = useState('')
  const [deep, setDeep] = useState('')
  const [depth, setDepth] = useState(1)
  const [analysts, setAnalysts] = useState<string[]>(['market', 'news', 'fundamentals'])
  const [quote, setQuote] = useState<BatchQuote | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.config().then((c) => {
      setConfig(c)
      const p = c.providers[0]?.key ?? ''
      const ms = c.models_by_provider[p] ?? []
      setProvider(p)
      setQuick(ms[0]?.id ?? '')
      setDeep(ms[1]?.id ?? ms[0]?.id ?? '')
    }).catch((e) => setError(String(e)))
    evaluationApi.listBatches().then(setRows).catch((e) => setError(String(e)))
  }, [])

  // Any change to the grid or settings invalidates the quote and its confirmation.
  useEffect(() => { setQuote(null); setConfirmed(false) }, [tickers, start, end, every, provider, quick, deep, depth, analysts])

  const models = config?.models_by_provider[provider] ?? []
  const body = (): BatchBody => ({
    tickers: tickers.split(/[\s,]+/).filter(Boolean),
    start_date: start, end_date: end, every_n_days: every,
    settings: {
      analysts, research_depth: depth, llm_provider: provider,
      shallow_thinker: quick, deep_thinker: deep, output_language: 'English', checkpoint_enabled: true,
    },
  })
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e))

  async function getQuote() {
    setError(null)
    try { setQuote(await evaluationApi.quote(body())) } catch (e) { fail(e) }
  }

  async function launch() {
    if (!quote) return
    setBusy(true)
    setError(null)
    try {
      const b = await evaluationApi.startBatch({ ...body(), confirmed_credits: quote.max_credits })
      navigate({ to: '/evaluation/$id', params: { id: b.id } })
    } catch (e) { fail(e) } finally { setBusy(false) }
  }

  const short = quote?.credits_available != null && quote.credits_available < quote.max_credits
  return (
    <div className="min-h-screen">
      <Topbar state="idle" />
      <main data-guide="guide-backtests" className="mx-auto w-full max-w-[1100px] space-y-6 px-6 py-4">
        <div>
          <h1 className="text-2xl font-bold">Backtest batches</h1>
          <p className="text-sm text-[var(--text-3)]">
            Run the same analysis over a small grid of past dates, then score each call against what
            the closes actually did. Each cell is an ordinary analysis run in your history.
          </p>
        </div>

        <section className="es-card space-y-4 p-4">
          <div className="grid gap-3 md:grid-cols-4">
            <label className="space-y-1 text-xs md:col-span-2">Tickers (comma separated, up to 6)
              <Input value={tickers} onChange={(e) => setTickers(e.target.value.toUpperCase())} />
            </label>
            <label className="space-y-1 text-xs">From
              <Input type="date" value={start} max={iso(new Date())} onChange={(e) => setStart(e.target.value)} />
            </label>
            <label className="space-y-1 text-xs">To
              <Input type="date" value={end} max={iso(new Date())} onChange={(e) => setEnd(e.target.value)} />
            </label>
            <label className="space-y-1 text-xs">Every N days
              <Input type="number" min={1} max={90} value={every} onChange={(e) => setEvery(Math.max(1, Number(e.target.value) || 1))} />
            </label>
            <label className="space-y-1 text-xs">Provider
              <select className="fld-input w-full" value={provider} onChange={(e) => {
                const ms = config?.models_by_provider[e.target.value] ?? []
                setProvider(e.target.value); setQuick(ms[0]?.id ?? ''); setDeep(ms[1]?.id ?? ms[0]?.id ?? '')
              }}>
                {config?.providers.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
              </select>
            </label>
            <label className="space-y-1 text-xs">Quick model
              <select className="fld-input w-full" value={quick} onChange={(e) => setQuick(e.target.value)}>
                {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
            </label>
            <label className="space-y-1 text-xs">Deep model
              <select className="fld-input w-full" value={deep} onChange={(e) => setDeep(e.target.value)}>
                {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
            </label>
            <label className="space-y-1 text-xs">Depth
              <select className="fld-input w-full" value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
                {(config?.research_depths ?? []).map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
              </select>
            </label>
          </div>
          <div className="flex flex-wrap gap-3 text-sm">
            {config?.analysts.map((a) => (
              <label key={a.value} className="flex items-center gap-1.5">
                <input type="checkbox" checked={analysts.includes(a.value)} onChange={(e) =>
                  setAnalysts(e.target.checked ? [...analysts, a.value] : analysts.filter((x) => x !== a.value))} />
                {a.label}
              </label>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" onClick={getQuote} disabled={!analysts.length || !provider}>Check cost</Button>
            {quote && (
              <span className="text-sm">
                {quote.cells} runs · up to <b>{quote.max_credits}</b> credit{quote.max_credits === 1 ? '' : 's'}
                {quote.credits_available != null && <> · you have {quote.credits_available}</>}
                {quote.credits_per_cell === 0 && <> · this provider is not billed in credits</>}
              </span>
            )}
          </div>
          {quote && (
            <div className="space-y-2">
              <p className="es-mono text-xs text-[var(--text-4)]">
                {quote.grid.map((c) => `${c.ticker} ${c.date}`).join(' · ')}
              </p>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={confirmed} disabled={short} onChange={(e) => setConfirmed(e.target.checked)} />
                I understand this starts {quote.cells} analyses and can spend up to {quote.max_credits} credits
                (cached results cost nothing).
              </label>
              <Button onClick={launch} disabled={!confirmed || busy || short}>
                {busy ? 'Starting…' : 'Start batch'}
              </Button>
              {short && <p className="text-sm text-[var(--err)]">Not enough credits for this batch.</p>}
            </div>
          )}
          {error && <p className="text-sm text-[var(--err)]">{error}</p>}
        </section>

        <section className="overflow-auto rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)]">
          <Table>
            <TableHeader>
              <TableRow>
                {['Started', 'Tickers', 'Dates', 'Runs', 'Quoted credits', ''].map((h) =>
                  <TableHead key={h} className="es-team-label">{h}</TableHead>)}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="es-mono">{r.created_at.slice(0, 16).replace('T', ' ')}</TableCell>
                  <TableCell>{r.tickers.join(', ')}</TableCell>
                  <TableCell className="es-mono">{r.first_date} → {r.last_date}</TableCell>
                  <TableCell className="es-mono">{r.cells}{r.cancelled ? ' (stopped)' : ''}</TableCell>
                  <TableCell className="es-mono">{r.quoted_credits}</TableCell>
                  <TableCell className="text-right">
                    <Link to="/evaluation/$id" params={{ id: r.id }}>Open</Link>
                  </TableCell>
                </TableRow>
              ))}
              {!rows.length && (
                <TableRow><TableCell colSpan={6} className="text-sm text-[var(--text-3)]">No batches yet.</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </section>
      </main>
    </div>
  )
}
