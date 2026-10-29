/* Trade Desk research: types + client for /desk/research/*. Kept apart from
   lib/api.ts so the research screen owns its own contract. Every vendor
   string in here is untrusted text: render it as text, never as HTML. */
import { API_BASE, ApiError } from './api'

export type SourceState = 'ok' | 'empty' | 'unavailable'

export interface SourceStatus {
  source: string
  label: string
  status: SourceState
  fetched_at?: string | null
  cached?: boolean
  stale?: boolean
  count?: number
  error?: string
  symbol?: string
}

interface Sourced {
  sources: SourceStatus[]
  status: 'ok' | 'partial' | 'unavailable' | 'empty'
}

export interface NewsLink { source: 'nse' | 'bse' | 'yahoo'; url: string | null }

export interface NewsItem {
  symbol: string
  title: string
  detail: string
  category: string
  published_at: string
  official: boolean
  tags: string[]
  sources: NewsLink[]
}

export interface NewsResponse extends Sourced {
  window: { from: string; to: string } | null
  items: NewsItem[]
  symbol?: string
  symbols?: string[]
  notes?: string[]
}

export type Figures = Partial<Record<
  'revenue' | 'total_income' | 'expenses' | 'pbt' | 'pat' | 'pat_owners' | 'eps_diluted' | 'gross_npa_pct',
  number>>

export interface Quarter {
  period_start: string | null
  period_end: string
  quarter: string
  basis: string
  audited: string
  bank: boolean
  figures: Figures
  margins: Partial<Record<'pbt_margin_pct' | 'pat_margin_pct', number>>
  qoq: Figures
  yoy: Figures
  qoq_period_end?: string
  yoy_period_end?: string
  filed_at: string | null
  filing_url: string | null
}

export interface ResultsResponse extends Sourced {
  symbol: string
  source: 'nse' | 'yahoo'
  quarters: Quarter[]
  next_results_date: string | null
  notes: string[]
}

export interface PitRow {
  person: string
  category: string
  promoter: boolean
  transaction: string
  mode: string
  security: string
  qty: number | null
  value_inr: number | null
  avg_price: number | null
  holding_before_pct: number | null
  holding_after_pct: number | null
  traded_from: string | null
  traded_to: string | null
  disclosed_on: string | null
  disclosure_lag_days: number | null
  filing_url: string | null
}

export interface Deal {
  kind: 'bulk' | 'block'
  date: string | null
  symbol: string
  client: string
  side: string
  qty: number
  price: number
  value_cr: number
  name?: string
  on_watchlist?: boolean
}

interface Net { buy_inr: number; sell_inr: number; net_inr: number }

export interface InsidersResponse extends Sourced {
  symbol: string
  window: { from: string; to: string }
  pit: PitRow[]
  deals: Deal[]
  summary: { market_trades: Net; promoter_market_trades: Net; pledge_events: number; disclosures: number }
}

export interface DealsResponse extends Sourced { deals: Deal[]; only_watchlist: boolean }

export interface FactsResponse extends Sourced {
  symbol: string
  facts: {
    band_pct?: number | null
    series?: string | null
    listed_in_band_file?: boolean
    fno?: boolean
    fno_lot?: number | null
    fo_ban?: boolean
    asm?: string | null
    gsm?: string | null
  }
}

const LEGACY_TOKEN = (import.meta as unknown as { env?: { VITE_API_TOKEN?: string } }).env?.VITE_API_TOKEN

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    signal,
    headers: LEGACY_TOKEN ? { Authorization: `Bearer ${LEGACY_TOKEN}` } : {},
  })
  if (!res.ok) {
    const raw = await res.text().catch(() => '')
    let detail: unknown = raw
    try { detail = (JSON.parse(raw) as { detail?: unknown })?.detail ?? raw } catch { /* keep raw */ }
    throw new ApiError(res.status, res.statusText, detail, raw)
  }
  return res.json() as Promise<T>
}

const sym = (s: string) => encodeURIComponent(s)

export const research = {
  facts: (s: string, signal?: AbortSignal) => get<FactsResponse>(`/desk/research/${sym(s)}/facts`, signal),
  news: (s: string, days = 7, signal?: AbortSignal) =>
    get<NewsResponse>(`/desk/research/${sym(s)}/news?days=${days}`, signal),
  results: (s: string, signal?: AbortSignal) => get<ResultsResponse>(`/desk/research/${sym(s)}/results`, signal),
  insiders: (s: string, days = 90, signal?: AbortSignal) =>
    get<InsidersResponse>(`/desk/research/${sym(s)}/insiders?days=${days}`, signal),
  watchlistNews: (days = 3, signal?: AbortSignal) =>
    get<NewsResponse>(`/desk/research/watchlist/news?days=${days}`, signal),
  deals: (scope: 'watchlist' | 'all' = 'watchlist', signal?: AbortSignal) =>
    get<DealsResponse>(`/desk/research/deals?scope=${scope}`, signal),
}

/** ₹ crore with Indian grouping: 311850 -> "₹3,11,850 cr" */
export function crore(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—'
  return `₹${new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(n)} cr`
}

/** "2026-10-29T08:55:13+05:30" -> "29 Oct, 08:55" in IST */
export function istStamp(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
    timeZone: 'Asia/Kolkata',
  })
}

/** How old a fetch is, for the freshness line: "just now", "12 min ago", "3 h ago". */
export function age(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return 'unknown'
  const min = Math.round((now - new Date(iso).getTime()) / 60_000)
  if (min < 1) return 'just now'
  if (min < 60) return `${min} min ago`
  if (min < 48 * 60) return `${Math.round(min / 60)} h ago`
  return `${Math.round(min / 1440)} d ago`
}

/** Links are rendered only when https; the server already allowlists hosts. */
export function safeHref(url: string | null | undefined): string | undefined {
  return url && url.startsWith('https://') ? url : undefined
}
