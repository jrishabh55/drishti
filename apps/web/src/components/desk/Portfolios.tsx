import { Link } from '@tanstack/react-router'
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '#/components/ui/select'
import { api } from '#/lib/api'
import type { Portfolio } from '#/lib/desk-types'
import { inr } from '#/lib/desk-format'

/* Paper portfolios: the selected one drives the ledger, performance and new
   tickets. Mounted once per signed-in account (keyed by user id at the root),
   so nothing — list, selection or an in-flight response — survives a sign-out
   or an account switch. The selection is remembered per account. */

export const DEFAULT_PORTFOLIO = 'default'

interface PortfolioCtx {
  portfolios: Portfolio[]
  /** The selected portfolio's id; 'default' until the list says otherwise. */
  selectedId: string
  selected: Portfolio | undefined
  /** False until the first list load settles: wait before fetching by selection. */
  ready: boolean
  error: string | null
  select: (id: string) => void
  reload: () => Promise<Portfolio[] | undefined>
}

const Ctx = createContext<PortfolioCtx | null>(null)

const storageKey = (userId: string) => `drishti-desk-portfolio:${userId}`

function readStored(userId: string): string | null {
  try { return localStorage.getItem(storageKey(userId)) } catch { return null }
}

function message(e: unknown) {
  return e instanceof Error ? e.message : String(e)
}

export function PortfolioProvider({ userId, children }: { userId: string; children: React.ReactNode }) {
  const [portfolios, setPortfolios] = useState<Portfolio[]>([])
  const [chosen, setChosen] = useState(() => readStored(userId) ?? DEFAULT_PORTFOLIO)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const seq = useRef(0)

  const reload = useCallback(async () => {
    const n = ++seq.current
    try {
      const list = await api.desk.portfolios()
      if (n !== seq.current) return undefined   // a newer load (or unmount) won
      setPortfolios(list); setError(null)
      return list
    } catch (e) {
      if (n === seq.current) setError(message(e))
      return undefined
    } finally {
      if (n === seq.current) setReady(true)
    }
  }, [])

  useEffect(() => {
    reload()
    return () => { seq.current++ }
  }, [reload])

  const select = useCallback((id: string) => {
    setChosen(id)
    try { localStorage.setItem(storageKey(userId), id) } catch { /* private mode: not remembered */ }
  }, [userId])

  // A remembered id that no longer lists (or never did) falls back to default.
  const selectedId = portfolios.length && !portfolios.some((p) => p.id === chosen) ? DEFAULT_PORTFOLIO : chosen
  const selected = portfolios.find((p) => p.id === selectedId)
  return (
    <Ctx.Provider value={{ portfolios, selectedId, selected, ready, error, select, reload }}>
      {children}
    </Ctx.Provider>
  )
}

/** Null outside the provider (e.g. an isolated component test). */
export function usePortfolios() {
  return useContext(Ctx)
}

/** `fetch(selectedId)` for the selected portfolio, re-run on a switch, every
 *  `refreshMs` and whenever `tick` changes. An answer for a previous
 *  selection (or after unmount) is dropped, never shown. */
export function useSelectedPortfolioData<T>(fetch: (portfolioId: string) => Promise<T>,
  { refreshMs, tick = 0 }: { refreshMs?: number; tick?: number } = {}) {
  const pf = usePortfolios()
  const portfolioId = pf?.selectedId ?? DEFAULT_PORTFOLIO
  const ready = pf?.ready ?? true
  const [state, setState] = useState<{ data: T | null; error: string | null }>({ data: null, error: null })
  const fetchRef = useRef(fetch)
  fetchRef.current = fetch

  useEffect(() => {
    if (!ready) return
    let live = true
    setState({ data: null, error: null })
    const load = () => fetchRef.current(portfolioId).then(
      (data) => { if (live) setState({ data, error: null }) },
      (e) => { if (live) setState((s) => ({ ...s, error: message(e) })) })
    load()
    const t = refreshMs ? setInterval(load, refreshMs) : undefined
    return () => { live = false; if (t) clearInterval(t) }
  }, [portfolioId, ready, refreshMs, tick])
  return { ...state, portfolioId }
}

export function portfolioName(portfolios: Portfolio[] | undefined, id: string | undefined) {
  const pid = id ?? DEFAULT_PORTFOLIO
  return portfolios?.find((p) => p.id === pid)?.name ?? (pid === DEFAULT_PORTFOLIO ? 'Default' : 'Unknown portfolio')
}

export function PortfolioPicker() {
  const ctx = usePortfolios()
  if (!ctx) return null
  const { portfolios, selectedId, select, ready } = ctx
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-[var(--text-4)]">Paper portfolio</span>
      <Select value={selectedId} onValueChange={select} disabled={!ready}>
        <SelectTrigger size="sm" aria-label="Paper portfolio" className="min-w-[180px]">
          <SelectValue placeholder="Default" />
        </SelectTrigger>
        <SelectContent>
          {(portfolios.length ? portfolios : [{ id: DEFAULT_PORTFOLIO, name: 'Default', capital: 0 } as Portfolio]).map((p) => (
            <SelectItem key={p.id} value={p.id}>
              {p.name}{p.capital ? ` · ${inr(p.capital, true)}` : ''}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Link to="/desk/settings" hash="portfolios" className="text-xs text-[var(--text-3)]">Manage</Link>
    </div>
  )
}

function PortfolioRow({ p, onSaved }: { p: Portfolio; onSaved: () => void }) {
  const [name, setName] = useState(p.name)
  const [capital, setCapital] = useState(String(p.capital))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    const body: { name?: string; capital?: number } = {}
    if (name.trim() !== p.name) body.name = name.trim()
    if (!p.capital_locked && Number(capital) !== p.capital) body.capital = Number(capital)
    if (!Object.keys(body).length) return
    setBusy(true); setError(null)
    try { await api.desk.updatePortfolio(p.id, body); onSaved() }
    catch (e) { setError(message(e)) }
    finally { setBusy(false) }
  }

  if (p.is_default) {
    return (
      <div className="rounded-[var(--r-md)] border border-[var(--line-1)] p-3 text-sm">
        <div className="font-medium">Default <span className="es-mono text-[var(--text-3)]">{inr(p.capital, true)}</span></div>
        <p className="text-xs text-[var(--text-4)]">Your original ledger ({p.trades} trades). Its capital is “Default portfolio capital ₹” above;
          it is the only portfolio that can hand tickets to your broker.</p>
      </div>
    )
  }
  return (
    <div className="space-y-2 rounded-[var(--r-md)] border border-[var(--line-1)] p-3 text-sm">
      <div className="flex flex-wrap items-end gap-2">
        <div className="grid gap-1.5">
          <Label htmlFor={`pf-name-${p.id}`}>Name</Label>
          <Input id={`pf-name-${p.id}`} value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`pf-cap-${p.id}`}>Starting capital ₹</Label>
          <Input id={`pf-cap-${p.id}`} type="number" min={1} value={capital} disabled={p.capital_locked}
            onChange={(e) => setCapital(e.target.value)} />
        </div>
        <Button variant="outline" disabled={busy} onClick={save}>Save</Button>
      </div>
      {p.capital_locked && (
        <p className="text-xs text-[var(--text-4)]">
          Capital is fixed: this portfolio has {p.trades} paper trade{p.trades === 1 ? '' : 's'}, and changing it would
          rewrite its cash and performance. Create a new portfolio for a different size.
        </p>
      )}
      {p.capital_history.length > 0 && (
        <ul className="text-xs text-[var(--text-4)]">
          {p.capital_history.map((h) => (
            <li key={h.at}>{h.at.slice(0, 16).replace('T', ' ')} UTC: {inr(h.from, true)} → {inr(h.to, true)}</li>
          ))}
        </ul>
      )}
      {error && <p className="text-xs text-[var(--err)]">{error}</p>}
    </div>
  )
}

/** Create, rename and size paper portfolios (Settings page). */
export function PortfolioManager() {
  const ctx = usePortfolios()
  const [name, setName] = useState('')
  const [capital, setCapital] = useState('100000')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!ctx) return null
  const { portfolios, reload, select, error: listError } = ctx

  async function create() {
    setBusy(true); setError(null)
    try {
      const p = await api.desk.createPortfolio({ name: name.trim(), capital: Number(capital) })
      await reload()
      select(p.id)
      setName('')
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section id="portfolios" className="space-y-3 rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)] p-4">
      <h2 className="text-lg font-semibold">Paper portfolios</h2>
      <p className="text-xs text-[var(--text-3)]">
        Each portfolio keeps its own starting capital, cash, trades, daily risk counts and performance. The limits on this
        page, your plans and your watchlist are shared by all of them. Named portfolios are paper only.
      </p>
      {listError && <p className="text-xs text-[var(--err)]">{listError}</p>}
      <div className="space-y-2">
        {portfolios.map((p) => <PortfolioRow key={`${p.id}:${p.name}:${p.capital}`} p={p} onSaved={() => { reload() }} />)}
      </div>
      <div className="flex flex-wrap items-end gap-2 border-t border-[var(--line-1)] pt-3">
        <div className="grid gap-1.5">
          <Label htmlFor="pf-new-name">New portfolio</Label>
          <Input id="pf-new-name" placeholder="e.g. Small ORB test" value={name} maxLength={60}
            onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="pf-new-cap">Starting capital ₹</Label>
          <Input id="pf-new-cap" type="number" min={1} value={capital} onChange={(e) => setCapital(e.target.value)} />
        </div>
        <Button disabled={busy || !name.trim() || !(Number(capital) > 0)} onClick={create}>Create</Button>
      </div>
      {error && <p className="text-xs text-[var(--err)]">{error}</p>}
    </section>
  )
}
