/* Strategy/portfolio analytics + trade journal over ONE book (paper or live).
   Paper and live are separate tabs and never summed. Open P&L counts only
   fresh marks; a missing benchmark says why. */
import { useEffect, useState } from 'react'
import { usePortfolios } from '#/components/desk/Portfolios'
import { Badge } from '#/components/ui/badge'
import { Button, buttonVariants } from '#/components/ui/button'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '#/components/ui/dialog'
import { Input } from '#/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '#/components/ui/select'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '#/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '#/components/ui/tabs'
import { Textarea } from '#/components/ui/textarea'
import { inr, rMult, signedPct } from '#/lib/desk-format'
import {
  evaluationApi, type AnalyticsFilters, type BookMode, type JournalRow, type TradingPerformance,
} from '#/lib/evaluation-api'
import { markLabel, strategyLabel } from '#/lib/evaluation-format'

const ANY = '__any__'
const tone = (n: number | null | undefined) => (n == null ? undefined : n >= 0 ? 'var(--ok)' : 'var(--err)')

function Tile({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="rounded-[var(--r-md)] border border-[var(--line-1)] bg-[var(--bg-1)] px-3 py-2">
      <div className="text-[10.5px] uppercase tracking-wider text-[var(--text-4)]">{label}</div>
      <div className="es-mono mt-0.5 text-lg" style={{ color }}>{value}</div>
      {sub && <div className="text-xs text-[var(--text-4)]">{sub}</div>}
    </div>
  )
}

function NoteDialog({ row, mode, onClose, onSaved }: {
  row: JournalRow | null; mode: BookMode; onClose: () => void; onSaved: () => void
}) {
  const [note, setNote] = useState('')
  const [tags, setTags] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setNote(row?.annotation?.note ?? '')
    setTags(row?.annotation?.tags.join(' ') ?? '')
    setError(null)
  }, [row])
  const save = async () => {
    if (!row) return
    setBusy(true)
    try {
      await evaluationApi.annotateTrade(mode, row.trade_id, { note, tags: tags.split(/[\s,]+/).filter(Boolean) })
      onSaved()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog open={row != null} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Journal · {row?.symbol} {row?.trade_date}</DialogTitle>
          <DialogDescription>Private to you. Clear both fields to remove the note.</DialogDescription>
        </DialogHeader>
        <Textarea aria-label="Note" maxLength={2000} rows={5} value={note} onChange={(e) => setNote(e.target.value)}
          placeholder="Why you took it, what you saw, what you'd change" />
        <Input aria-label="Tags" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="tags, space separated (max 10)" />
        {error && <p role="alert" className="text-sm text-[var(--err)]">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save note'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function TradingAnalytics() {
  const pf = usePortfolios()
  const [mode, setMode] = useState<BookMode>('paper')
  const [portfolioId, setPortfolioId] = useState<string>(pf?.selectedId ?? 'default')
  const [strategyId, setStrategyId] = useState<string | null>(null)
  const [version, setVersion] = useState<number | null>(null)
  const [data, setData] = useState<TradingPerformance | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [strategies, setStrategies] = useState<Record<string, string>>({})
  const [versions, setVersions] = useState<Record<string, number[]>>({})
  const [editing, setEditing] = useState<JournalRow | null>(null)
  const [tick, setTick] = useState(0)

  // Live trades belong to the Default account only.
  const filters: AnalyticsFilters = { mode, portfolio_id: mode === 'live' ? 'default' : portfolioId, strategy_id: strategyId, strategy_version: version }
  const key = `${filters.mode}|${filters.portfolio_id}|${filters.strategy_id ?? ''}|${filters.strategy_version ?? ''}`

  useEffect(() => {
    let live = true
    setData(null)
    setError(null)
    evaluationApi.tradingPerformance(filters).then((d) => {
      if (!live) return
      setData(d)
      setStrategies((s) => {
        const next = { ...s }
        for (const g of d.by_strategy) if (g.strategy_id) next[g.strategy_id] = g.strategy_name ?? g.strategy_id
        return next
      })
      setVersions((s) => {
        const next = { ...s }
        for (const g of d.by_strategy) {
          if (!g.strategy_id || g.strategy_version == null) continue
          const v = Number(g.strategy_version)
          if (v > 0 && !(next[g.strategy_id] ?? []).includes(v)) next[g.strategy_id] = [...(next[g.strategy_id] ?? []), v].sort((a, b) => a - b)
        }
        return next
      })
    }, (e) => { if (live) setError(e instanceof Error ? e.message : String(e)) })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick])

  const b = data?.benchmark
  return (
    <section className="space-y-3" aria-labelledby="trading-analytics">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="trading-analytics" className="mr-auto text-lg font-semibold">Strategy &amp; portfolio results</h2>
        <Tabs value={mode} onValueChange={(v) => setMode(v as BookMode)}>
          <TabsList>
            <TabsTrigger value="paper">Paper</TabsTrigger>
            <TabsTrigger value="live">Live</TabsTrigger>
          </TabsList>
        </Tabs>
        <Select value={filters.portfolio_id} onValueChange={setPortfolioId} disabled={mode === 'live'}>
          <SelectTrigger aria-label="Portfolio" className="w-44"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All paper portfolios</SelectItem>
            {(pf?.portfolios ?? [{ id: 'default', name: 'Default' }]).map((p) =>
              <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={strategyId ?? ANY} onValueChange={(v) => { setStrategyId(v === ANY ? null : v); setVersion(null) }}>
          <SelectTrigger aria-label="Strategy" className="w-44"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>All strategies</SelectItem>
            <SelectItem value="manual">Manual/legacy</SelectItem>
            {Object.entries(strategies).map(([id, name]) => <SelectItem key={id} value={id}>{name}</SelectItem>)}
          </SelectContent>
        </Select>
        {strategyId && strategyId !== 'manual' && (
          <Select value={version == null ? ANY : String(version)} onValueChange={(v) => setVersion(v === ANY ? null : Number(v))}>
            <SelectTrigger aria-label="Strategy version" className="w-32"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>All versions</SelectItem>
              {(versions[strategyId] ?? []).map((v) => <SelectItem key={v} value={String(v)}>v{v}</SelectItem>)}
            </SelectContent>
          </Select>
        )}
        <a className={buttonVariants({ variant: 'outline', size: 'sm' })} href={evaluationApi.tradeJournalCsvUrl(filters)} download>
          Export CSV
        </a>
      </div>
      {mode === 'live' && <p className="text-xs text-[var(--text-4)]">Live results are your real broker trades on the Default account, shown apart from paper — paper gains are never added to real money.</p>}
      {error && <p role="alert" className="text-sm text-[var(--err)]">{error}</p>}
      {!data && !error && <p className="text-sm text-[var(--text-3)]">Loading…</p>}
      {data && (
        <>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <Tile label={`Realized (${data.realized.trades} closed)`} value={inr(data.realized.pnl_net)} color={tone(data.realized.pnl_net)}
              sub={data.realized.return_pct != null ? `${signedPct(data.realized.return_pct)} of ${inr(data.capital, true)}` : 'no single account to divide by'} />
            <Tile label="Open P&L (marked)" value={data.open.positions ? inr(data.open.pnl) : '—'} color={tone(data.open.positions ? data.open.pnl : null)}
              sub={data.open.positions ? `${data.open.marked}/${data.open.positions} fresh marks · ${data.open.basis}` : 'no open positions'} />
            <Tile label="Max drawdown" value={data.stats.max_drawdown != null ? inr(data.stats.max_drawdown, true) : '—'}
              sub={data.stats.profit_factor != null ? `profit factor ${data.stats.profit_factor.toFixed(2)}` : undefined} />
            <Tile label={`vs ${b?.name ?? 'benchmark'}`} value={b?.available ? signedPct(b.return_pct) : 'unavailable'}
              sub={b?.available ? `${b.start} → ${b.end}${b.book_return_pct != null ? ` · book ${signedPct(b.book_return_pct)}` : ''}` : b?.reason ?? undefined} />
          </div>
          <p className="text-xs text-[var(--text-4)]">{data.charges_note}</p>

          <div className="overflow-auto rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)]">
            <Table>
              <TableHeader><TableRow>
                {['Strategy / version', 'Closed', 'Win rate', 'Avg R', 'Realized', 'Open P&L'].map((h) =>
                  <TableHead key={h} className="es-team-label">{h}</TableHead>)}
              </TableRow></TableHeader>
              <TableBody>
                {data.by_strategy.map((g) => (
                  <TableRow key={`${g.strategy_id}|${g.strategy_version}`}>
                    <TableCell>{strategyLabel(g)}</TableCell>
                    <TableCell className="es-mono">{g.closed}</TableCell>
                    <TableCell className="es-mono">{g.stats.win_rate != null ? `${Math.round(g.stats.win_rate * 100)}%` : '—'}</TableCell>
                    <TableCell className="es-mono">{rMult(g.stats.avg_r_net)}</TableCell>
                    <TableCell className="es-mono" style={{ color: tone(g.realized) }}>{inr(g.realized)}</TableCell>
                    <TableCell className="es-mono">{g.open_positions ? `${inr(g.open_pnl)}${g.unmarked ? ` (${g.unmarked} unmarked)` : ''}` : '—'}</TableCell>
                  </TableRow>
                ))}
                {!data.by_strategy.length && <TableRow><TableCell colSpan={6} className="text-sm text-[var(--text-3)]">No trades for these filters.</TableCell></TableRow>}
              </TableBody>
            </Table>
          </div>

          {data.by_portfolio.length > 1 && (
            <div className="overflow-auto rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)]">
              <Table>
                <TableHeader><TableRow>
                  {['Paper portfolio', 'Capital', 'Closed', 'Realized', 'Return', 'Open P&L'].map((h) =>
                    <TableHead key={h} className="es-team-label">{h}</TableHead>)}
                </TableRow></TableHeader>
                <TableBody>
                  {data.by_portfolio.map((p) => (
                    <TableRow key={p.id}>
                      <TableCell>{p.name}</TableCell>
                      <TableCell className="es-mono">{inr(p.capital, true)}</TableCell>
                      <TableCell className="es-mono">{p.closed}</TableCell>
                      <TableCell className="es-mono" style={{ color: tone(p.realized) }}>{inr(p.realized)}</TableCell>
                      <TableCell className="es-mono">{signedPct((p.realized / p.capital) * 100)}</TableCell>
                      <TableCell className="es-mono">{p.open_positions ? inr(p.open_pnl) : '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          <h3 className="text-base font-semibold">Trade journal</h3>
          <div className="overflow-auto rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)]">
            <Table>
              <TableHeader><TableRow>
                {['Date', 'Symbol', 'Strategy', 'Portfolio', 'Status', 'Net P&L', 'R', 'Mark', 'Note'].map((h) =>
                  <TableHead key={h} className="es-team-label">{h}</TableHead>)}
              </TableRow></TableHeader>
              <TableBody>
                {[...data.trades].reverse().map((t) => (
                  <TableRow key={t.trade_id}>
                    <TableCell className="es-mono">{t.trade_date}</TableCell>
                    <TableCell className="font-medium">{t.symbol}</TableCell>
                    <TableCell>{strategyLabel(t)}</TableCell>
                    <TableCell>{t.portfolio_name}</TableCell>
                    <TableCell>{t.status}{t.exit_reason && <span className="text-xs text-[var(--text-4)]"> ({t.exit_reason})</span>}</TableCell>
                    <TableCell className="es-mono" style={{ color: tone(t.pnl_net ?? t.open_pnl) }}>
                      {t.pnl_net != null ? inr(t.pnl_net) : t.open_pnl != null ? `${inr(t.open_pnl)} open` : '—'}
                    </TableCell>
                    <TableCell className="es-mono">{rMult(t.r_net)}</TableCell>
                    <TableCell className="es-mono text-xs">
                      {t.mark ? <Badge variant={t.mark.status === 'live' || t.mark.status === 'delayed' ? 'secondary' : 'destructive'}>{markLabel(t.mark)}</Badge> : '—'}
                    </TableCell>
                    <TableCell>
                      <Button variant="ghost" size="sm" onClick={() => setEditing(t)} title={t.annotation?.note}>
                        {t.annotation ? `✎ ${t.annotation.tags.join(' ') || 'note'}` : 'Add note'}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
                {!data.trades.length && <TableRow><TableCell colSpan={9} className="text-sm text-[var(--text-3)]">No trades for these filters.</TableCell></TableRow>}
              </TableBody>
            </Table>
          </div>
        </>
      )}
      <NoteDialog row={editing} mode={mode} onClose={() => setEditing(null)} onSaved={() => setTick((n) => n + 1)} />
    </section>
  )
}
