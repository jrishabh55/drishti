import { Badge } from '#/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '#/components/ui/table'
import { inr } from '#/lib/desk-format'
import {
  age, crore, istStamp, safeHref,
  type Deal, type FactsResponse, type Figures, type InsidersResponse, type NewsItem,
  type NewsResponse, type ResultsResponse, type SourceStatus,
} from '#/lib/desk-research'

const SOURCE_NAME = { nse: 'NSE', bse: 'BSE', yahoo: 'Yahoo' } as const

/** Where every number on the page came from, and how fresh it is. A source
 *  that couldn't be read says so; its absence is never shown as "nothing". */
export function SourceList({ sources }: { sources: SourceStatus[] }) {
  if (!sources.length) return null
  return (
    <ul aria-label="Sources" className="flex flex-col gap-1 text-xs text-[var(--text-3)]">
      {sources.map((s, i) => (
        <li key={`${s.source}-${s.symbol ?? ''}-${i}`} className="flex flex-wrap items-center gap-2">
          <span className={`es-pill ${s.status === 'unavailable' ? 'err' : s.stale ? 'warn' : 'ok'}`}>
            {s.status === 'unavailable' ? 'Unavailable' : s.stale ? 'Stale' : s.status === 'empty' ? 'No rows' : 'OK'}
          </span>
          <span>{s.symbol ? `${s.symbol} · ` : ''}{s.label}</span>
          {s.status === 'unavailable'
            ? <span className="text-[var(--err)]">{s.error}</span>
            : <span>fetched {age(s.fetched_at)}{s.cached ? ' (cached)' : ''}{s.stale ? ' — source is failing, showing the last good copy' : ''}</span>}
        </li>
      ))}
    </ul>
  )
}

export function StatusNote({ status, what }: { status: string; what: string }) {
  if (status === 'unavailable') {
    return <p role="alert" className="text-sm text-[var(--err)]">{what} could not be read from any source right now. See the source list for why; nothing below is a substitute for it.</p>
  }
  if (status === 'partial') {
    return <p role="status" className="text-sm text-[var(--run)]">Some sources could not be read, so this may be incomplete.</p>
  }
  return null
}

function NewsRow({ item, showSymbol }: { item: NewsItem; showSymbol: boolean }) {
  return (
    <li className="border-b border-[var(--line-1)] py-2 last:border-0">
      <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--text-3)]">
        <time dateTime={item.published_at}>{istStamp(item.published_at)} IST</time>
        {showSymbol && <span className="es-mono font-medium text-[var(--text-1)]">{item.symbol}</span>}
        <Badge variant={item.official ? 'secondary' : 'outline'}>{item.official ? 'Exchange filing' : 'Headline (unofficial)'}</Badge>
        {item.category && <span>{item.category}</span>}
        {item.tags.map(t => <Badge key={t} variant="outline">{t.replace('_', ' ')}</Badge>)}
      </div>
      <p className="mt-1 text-sm">{item.title}</p>
      {item.detail && <p className="mt-0.5 line-clamp-2 text-xs text-[var(--text-3)]">{item.detail}</p>}
      <div className="mt-1 flex gap-3 text-xs">
        {item.sources.map(s => safeHref(s.url)
          ? <a key={s.source} href={safeHref(s.url)} target="_blank" rel="noreferrer noopener">{SOURCE_NAME[s.source]} ↗</a>
          : <span key={s.source} className="text-[var(--text-4)]">{SOURCE_NAME[s.source]} (no link)</span>)}
      </div>
    </li>
  )
}

export function NewsList({ data, showSymbol = false }: { data: NewsResponse; showSymbol?: boolean }) {
  return (
    <section className="space-y-3">
      <StatusNote status={data.status} what="News" />
      {data.notes?.map(n => <p key={n} className="text-xs text-[var(--text-3)]">{n}</p>)}
      {data.window && <p className="text-xs text-[var(--text-3)]">{data.window.from} to {data.window.to} (IST days), newest first. Filings on both exchanges are shown once with both links.</p>}
      {data.items.length
        ? <ul>{data.items.map((it, i) => <NewsRow key={`${it.published_at}-${i}`} item={it} showSymbol={showSymbol} />)}</ul>
        : data.status !== 'unavailable' && <p className="text-sm text-[var(--text-3)]">No announcements or headlines in this window.</p>}
      <SourceList sources={data.sources} />
    </section>
  )
}

const ROWS: { key: keyof Figures; label: string; money: boolean }[] = [
  { key: 'revenue', label: 'Revenue from operations', money: true },
  { key: 'total_income', label: 'Total income', money: true },
  { key: 'pbt', label: 'Profit before tax', money: true },
  { key: 'pat', label: 'Net profit', money: true },
  { key: 'pat_owners', label: 'Profit to shareholders', money: true },
  { key: 'eps_diluted', label: 'Diluted EPS (₹)', money: false },
  { key: 'gross_npa_pct', label: 'Gross NPA %', money: false },
]

function change(v: number | undefined) {
  if (v == null) return <span className="text-[var(--text-4)]">—</span>
  return <span className={v >= 0 ? 'text-[var(--ok)]' : 'text-[var(--err)]'}>{v >= 0 ? '+' : ''}{v.toFixed(1)}%</span>
}

export function ResultsPanel({ data }: { data: ResultsResponse }) {
  const latest = data.quarters[0]
  const rows = latest ? ROWS.filter(r => data.quarters.some(q => q.figures[r.key] != null)) : []
  return (
    <section className="space-y-3">
      <StatusNote status={data.status} what="Quarterly results" />
      <p className="text-sm">
        Next results board meeting:{' '}
        <strong>{data.next_results_date ?? 'none announced on NSE'}</strong>
      </p>
      {data.notes.map(n => <p key={n} className="text-xs text-[var(--text-3)]">{n}</p>)}
      {latest && (
        <>
          <p className="text-xs text-[var(--text-3)]">
            {data.source === 'nse'
              ? `As filed with NSE (SEBI integrated filing), ${latest.basis}, ₹ crore. QoQ is against ${latest.qoq_period_end ?? '—'}, YoY against ${latest.yoy_period_end ?? '—'}.`
              : 'Yahoo Finance quarterly statements — unofficial, ₹ crore.'}
          </p>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Quarter ended</TableHead>
                  {data.quarters.map(q => <TableHead key={q.period_end} className="text-right">{q.period_end}</TableHead>)}
                  <TableHead className="text-right">QoQ</TableHead>
                  <TableHead className="text-right">YoY</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map(r => (
                  <TableRow key={r.key}>
                    <TableCell>{r.label}</TableCell>
                    {data.quarters.map(q => (
                      <TableCell key={q.period_end} className="es-mono text-right">
                        {q.figures[r.key] == null ? '—' : r.money ? crore(q.figures[r.key]) : q.figures[r.key]!.toFixed(2)}
                      </TableCell>
                    ))}
                    <TableCell className="text-right">{r.key === 'gross_npa_pct' ? '' : change(latest.qoq[r.key])}</TableCell>
                    <TableCell className="text-right">{r.key === 'gross_npa_pct' ? '' : change(latest.yoy[r.key])}</TableCell>
                  </TableRow>
                ))}
                <TableRow>
                  <TableCell>Net margin</TableCell>
                  {data.quarters.map(q => <TableCell key={q.period_end} className="es-mono text-right">{q.margins.pat_margin_pct != null ? `${q.margins.pat_margin_pct}%` : '—'}</TableCell>)}
                  <TableCell /><TableCell />
                </TableRow>
                <TableRow>
                  <TableCell>Filing</TableCell>
                  {data.quarters.map(q => (
                    <TableCell key={q.period_end} className="text-right text-xs">
                      {safeHref(q.filing_url) ? <a href={safeHref(q.filing_url)} target="_blank" rel="noreferrer noopener">{q.audited || 'filed'} ↗</a> : '—'}
                    </TableCell>
                  ))}
                  <TableCell /><TableCell />
                </TableRow>
              </TableBody>
            </Table>
          </div>
        </>
      )}
      <SourceList sources={data.sources} />
    </section>
  )
}

export function DealsTable({ deals, showSymbol }: { deals: Deal[]; showSymbol: boolean }) {
  if (!deals.length) return <p className="text-sm text-[var(--text-3)]">No bulk or block deals.</p>
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Date</TableHead>
            {showSymbol && <TableHead>Symbol</TableHead>}
            <TableHead>Type</TableHead>
            <TableHead>Client</TableHead>
            <TableHead>Side</TableHead>
            <TableHead className="text-right">Qty</TableHead>
            <TableHead className="text-right">Price</TableHead>
            <TableHead className="text-right">Value</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {deals.map((d, i) => (
            <TableRow key={i}>
              <TableCell>{d.date ?? '—'}</TableCell>
              {showSymbol && <TableCell className="es-mono">{d.symbol}</TableCell>}
              <TableCell>{d.kind}</TableCell>
              <TableCell>{d.client}</TableCell>
              <TableCell className={d.side === 'BUY' ? 'text-[var(--ok)]' : 'text-[var(--err)]'}>{d.side}</TableCell>
              <TableCell className="es-mono text-right">{d.qty.toLocaleString('en-IN')}</TableCell>
              <TableCell className="es-mono text-right">{inr(d.price)}</TableCell>
              <TableCell className="es-mono text-right">{crore(d.value_cr)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

export function InsidersPanel({ data }: { data: InsidersResponse }) {
  const s = data.summary
  return (
    <section className="space-y-3">
      <StatusNote status={data.status} what="Insider disclosures" />
      <p className="text-xs text-[var(--text-3)]">
        SEBI insider-trading (PIT) disclosures filed with NSE, {data.window.from} to {data.window.to}, by disclosure date.
        Promoters, directors, key staff and their relatives must disclose trades within two trading days.
      </p>
      <div className="flex flex-wrap gap-4 text-sm">
        <span>Insider market buys {inr(s.market_trades.buy_inr, true)} · sells {inr(s.market_trades.sell_inr, true)}</span>
        <span>Promoter market net {inr(s.promoter_market_trades.net_inr, true)}</span>
        <span>Pledge events {s.pledge_events}</span>
      </div>
      <p className="text-xs text-[var(--text-3)]">Gifts, off-market transfers and pledges are listed but not counted as buying or selling.</p>
      {data.pit.length ? (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Disclosed</TableHead>
                <TableHead>Person</TableHead>
                <TableHead>Category</TableHead>
                <TableHead>Transaction</TableHead>
                <TableHead className="text-right">Qty</TableHead>
                <TableHead className="text-right">Value</TableHead>
                <TableHead className="text-right">Holding after</TableHead>
                <TableHead>Traded</TableHead>
                <TableHead>Filing</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.pit.map((r, i) => (
                <TableRow key={i}>
                  <TableCell>{r.disclosed_on ?? '—'}{r.disclosure_lag_days != null ? ` (+${r.disclosure_lag_days}d)` : ''}</TableCell>
                  <TableCell>{r.person}</TableCell>
                  <TableCell>{r.promoter ? <Badge>{r.category}</Badge> : r.category}</TableCell>
                  <TableCell>{r.transaction} · {r.mode}</TableCell>
                  <TableCell className="es-mono text-right">{r.qty?.toLocaleString('en-IN') ?? '—'}</TableCell>
                  <TableCell className="es-mono text-right">{inr(r.value_inr, true)}</TableCell>
                  <TableCell className="es-mono text-right">{r.holding_after_pct != null ? `${r.holding_after_pct}%` : '—'}</TableCell>
                  <TableCell>{r.traded_from === r.traded_to ? r.traded_to : `${r.traded_from} – ${r.traded_to}`}</TableCell>
                  <TableCell>{safeHref(r.filing_url) ? <a href={safeHref(r.filing_url)} target="_blank" rel="noreferrer noopener">XBRL ↗</a> : '—'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : data.status !== 'unavailable' && <p className="text-sm text-[var(--text-3)]">No insider disclosures in this window.</p>}
      <h3 className="pt-2 text-sm font-semibold">Bulk and block deals</h3>
      <DealsTable deals={data.deals} showSymbol={false} />
      <SourceList sources={data.sources} />
    </section>
  )
}

export function FactsStrip({ data }: { data: FactsResponse }) {
  const f = data.facts
  const pill = (cls: string, text: string) => <span className={`es-pill ${cls}`}>{text}</span>
  const down = (name: string) => data.sources.find(s => s.source === name)?.status === 'unavailable'
  return (
    <div className="space-y-2" aria-label="Exchange facts">
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {down('nse.price_bands') ? pill('warn', 'Circuit band: unavailable')
        : pill('', f.band_pct != null ? `Circuit band ${f.band_pct}%` : f.listed_in_band_file ? 'No price band' : 'Not in NSE band file')}
      {down('nse.fo_lots') ? pill('warn', 'F&O: unavailable') : pill('', f.fno ? `F&O · lot ${f.fno_lot}` : 'Not in F&O')}
      {down('nse.fo_ban') ? pill('warn', 'F&O ban list: unavailable') : f.fo_ban ? pill('err', 'In F&O ban') : null}
      {down('nse.asm') ? pill('warn', 'ASM: unavailable') : f.asm ? pill('err', `ASM ${f.asm}`) : null}
      {down('nse.gsm') ? pill('warn', 'GSM: unavailable') : f.gsm ? pill('err', `GSM ${f.gsm}`) : null}
      {data.sources.some(s => s.stale) && pill('warn', 'Some exchange facts are stale')}
    </div>
    <details className="text-xs"><summary className="cursor-pointer text-[var(--text-3)]">Exchange sources & freshness</summary><SourceList sources={data.sources} /></details>
    </div>
  )
}
