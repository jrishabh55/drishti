import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEffect, useState, type ReactNode } from 'react'
import { DeskFooter, DeskNav } from '#/components/desk/DeskNav'
import { DeskSymbolSearch } from '#/components/desk/DeskSymbolSearch'
import { DealsTable, FactsStrip, InsidersPanel, NewsList, ResultsPanel, SourceList, StatusNote } from '#/components/desk/ResearchPanels'
import { Topbar } from '#/components/shared/Topbar'
import { Button } from '#/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '#/components/ui/tabs'
import { api } from '#/lib/api'
import { research } from '#/lib/desk-research'

type Tab = 'news' | 'results' | 'insiders'
const TABS: Tab[] = ['news', 'results', 'insiders']

export const Route = createFileRoute('/desk/research')({
  ssr: false,
  validateSearch: (s: Record<string, unknown>): { symbol?: string; tab?: Tab } => ({
    symbol: typeof s.symbol === 'string' && /^[A-Z0-9&-]{1,20}$/i.test(s.symbol) ? s.symbol.toUpperCase() : undefined,
    tab: TABS.includes(s.tab as Tab) ? (s.tab as Tab) : undefined,
  }),
  component: ResearchPage,
})

type Load<T> = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; data: T }

/** Fetch on mount / when deps change; aborts the previous request. */
function useLoad<T>(fn: ((signal: AbortSignal) => Promise<T>) | null, deps: unknown[]): [Load<T> | null, () => void] {
  const [load, setLoad] = useState<Load<T> | null>(null)
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    if (!fn) { setLoad(null); return }
    const ctl = new AbortController()
    setLoad({ state: 'loading' })
    fn(ctl.signal)
      .then(data => { if (!ctl.signal.aborted) setLoad({ state: 'ready', data }) })
      .catch(e => { if (!ctl.signal.aborted) setLoad({ state: 'error', message: e instanceof Error ? e.message : String(e) }) })
    return () => ctl.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])
  return [load, () => setNonce(n => n + 1)]
}

function Loaded<T>({ load, what, children }: { load: Load<T> | null; what: string; children: (d: T) => ReactNode }) {
  if (!load) return null
  if (load.state === 'loading') return <p role="status" className="text-sm text-[var(--text-3)]">Reading {what} from the exchanges… (each source is asked politely, about a second apart)</p>
  if (load.state === 'error') return <p role="alert" className="text-sm text-[var(--err)]">Could not load {what}: {load.message}</p>
  return <>{children(load.data)}</>
}

function ResearchPage() {
  const { symbol, tab = 'news' } = Route.useSearch()
  const navigate = useNavigate({ from: '/desk/research' })
  const [watch, setWatch] = useState<string[]>([])
  const [days, setDays] = useState(7)
  const [scope, setScope] = useState<'watchlist' | 'all'>('watchlist')

  useEffect(() => { api.desk.watchlist().then(setWatch).catch(() => setWatch([])) }, [])

  const open = (s: string | undefined, t: Tab = tab) => navigate({ search: { symbol: s, tab: t } })

  const [facts] = useLoad(symbol ? sig => research.facts(symbol, sig) : null, [symbol])
  const [news, reloadNews] = useLoad(symbol && tab === 'news' ? sig => research.news(symbol, days, sig) : null, [symbol, tab, days])
  const [results, reloadResults] = useLoad(symbol && tab === 'results' ? sig => research.results(symbol, sig) : null, [symbol, tab])
  const [insiders, reloadInsiders] = useLoad(symbol && tab === 'insiders' ? sig => research.insiders(symbol, 90, sig) : null, [symbol, tab])
  const [wNews, reloadWNews] = useLoad(!symbol ? sig => research.watchlistNews(3, sig) : null, [symbol])
  const [deals] = useLoad(!symbol ? sig => research.deals(scope, sig) : null, [symbol, scope])

  return (
    <div className="min-h-screen">
      <Topbar state="idle" />
      <main data-guide="guide-research" className="mx-auto w-full max-w-[1400px] space-y-6 px-6 py-4">
        <h1 className="text-2xl font-bold">Research</h1>
        <DeskNav />
        <div className="flex flex-wrap items-start gap-3">
          <DeskSymbolSearch actionLabel="Research" busyLabel="Opening…" onAdd={async s => open(s, tab)} />
          <div className="flex flex-wrap gap-1" aria-label="Watchlist">
            {symbol && <Button size="sm" variant="ghost" onClick={() => open(undefined)}>All watchlist news</Button>}
            {watch.map(s => (
              <Button key={s} size="sm" variant={s === symbol ? 'secondary' : 'outline'} onClick={() => open(s)}>{s}</Button>
            ))}
          </div>
        </div>

        {symbol ? (
          <section className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="es-mono text-xl font-semibold">{symbol}</h2>
              <a className="es-btn sm no-underline" href={`/analyse?ticker=${encodeURIComponent(`${symbol}.NS`)}`}>Analyse</a>
            </div>
            <Loaded load={facts} what="exchange facts">{d => <FactsStrip data={d} />}</Loaded>
            <Tabs value={tab} onValueChange={v => open(symbol, v as Tab)}>
              <TabsList>
                <TabsTrigger value="news">News & filings</TabsTrigger>
                <TabsTrigger value="results">Results</TabsTrigger>
                <TabsTrigger value="insiders">Insiders & deals</TabsTrigger>
              </TabsList>
              <TabsContent value="news" className="space-y-3 pt-2">
                <div className="flex items-center gap-2 text-xs">
                  {[1, 7, 14, 30].map(d => (
                    <Button key={d} size="sm" variant={d === days ? 'secondary' : 'ghost'} onClick={() => setDays(d)}>{d === 1 ? 'Today' : `${d} days`}</Button>
                  ))}
                  <Button size="sm" variant="ghost" onClick={reloadNews}>Refresh</Button>
                </div>
                <Loaded load={news} what="news">{d => <NewsList data={d} />}</Loaded>
              </TabsContent>
              <TabsContent value="results" className="space-y-3 pt-2">
                <Button size="sm" variant="ghost" onClick={reloadResults}>Refresh</Button>
                <Loaded load={results} what="results">{d => <ResultsPanel data={d} />}</Loaded>
              </TabsContent>
              <TabsContent value="insiders" className="space-y-3 pt-2">
                <Button size="sm" variant="ghost" onClick={reloadInsiders}>Refresh</Button>
                <Loaded load={insiders} what="insider disclosures">{d => <InsidersPanel data={d} />}</Loaded>
              </TabsContent>
            </Tabs>
          </section>
        ) : (
          <section className="space-y-6">
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold">Watchlist news · last 3 days</h2>
                <Button size="sm" variant="ghost" onClick={reloadWNews}>Refresh</Button>
              </div>
              {!watch.length && <p className="text-sm text-[var(--text-3)]">Your watchlist is empty. Search a stock above, or add symbols on the Today tab.</p>}
              <Loaded load={wNews} what="watchlist news">{d => <NewsList data={d} showSymbol />}</Loaded>
            </div>
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold">Bulk and block deals · latest session</h2>
                <Button size="sm" variant={scope === 'watchlist' ? 'secondary' : 'ghost'} onClick={() => setScope('watchlist')}>Watchlist</Button>
                <Button size="sm" variant={scope === 'all' ? 'secondary' : 'ghost'} onClick={() => setScope('all')}>All NSE</Button>
              </div>
              <Loaded load={deals} what="deals">{d => (
                <>
                  <StatusNote status={d.status} what="Deals" />
                  <DealsTable deals={d.deals} showSymbol />
                  <SourceList sources={d.sources} />
                </>
              )}</Loaded>
            </div>
          </section>
        )}
        <p className="text-xs text-[var(--text-3)]">
          Exchange filings and disclosures are shown as published; headlines are leads, not facts — open the filing before acting on one.
          Nothing here is a recommendation.
        </p>
        <DeskFooter />
      </main>
    </div>
  )
}
