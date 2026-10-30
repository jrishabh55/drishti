/* Evaluation endpoints (apps/api/routes/evaluation.py). Kept out of lib/api.ts
   on purpose; same base URL, legacy bearer and ApiError shape. */
import { API_BASE, ApiError } from './api'
import { analyticsQuery } from './evaluation-format'
import type { RunRequest } from './types'

export interface PortfolioSnapshot {
  cash: number | null
  currency: string | null
  positions: { ticker: string; quantity: number; average_price: number | null }[]
}

export type BatchSettings = Omit<RunRequest, 'ticker' | 'analysis_date'>

export interface BatchBody {
  tickers: string[]
  start_date: string
  end_date: string
  every_n_days: number
  settings: BatchSettings
  confirmed_credits?: number
}

export interface BatchQuote {
  cells: number
  max_credits: number
  credits_per_cell: number
  credits_available: number | null
  grid: { ticker: string; date: string }[]
}

export type CellPhase =
  | 'queued' | 'submitting' | 'running' | 'rejected' | 'cancelled' | 'failed' | 'interrupted'
  | 'pending' | 'scored' | 'unscored' | 'error'

export interface BatchCell {
  ticker: string
  date: string
  run_id: string | null
  phase: CellPhase
  rating: string | null
  run_status: string | null
  error: string | null
  /** Queued after a refused attempt: when it is next tried (ISO). */
  retry_at?: string | null
  attempts?: number
  score: {
    status: string
    benchmark?: string
    raw_return?: number
    alpha?: number
    holding_days?: number
    resolved?: string
    reason?: string
  } | null
}

export interface RatingScore { count: number; hit_rate: number | null; mean_alpha: number }

export interface BatchView {
  id: string
  created_at: string
  cancelled: boolean
  quoted_credits: number
  holding_days: number
  settings: Record<string, unknown>
  cells: BatchCell[]
  summary: { scored: number; pending: number; running: number; failed: number; by_rating: Record<string, RatingScore> }
  /** Only on the cancel response: queued cells cleared, and cells already mid-submission. */
  cancel_result?: { cleared: number; starting: number }
}

export interface BatchRow {
  id: string
  created_at: string
  quoted_credits: number
  cancelled: boolean
  cells: number
  tickers: string[]
  first_date: string | null
  last_date: string | null
}

export interface CurvePoint { at: string | null; equity: number; drawdown: number; drawdown_pct: number | null }

export interface PaperPerformance {
  portfolio_id: string
  capital: number
  equity: number
  stats: {
    trades: number
    wins?: number
    losses?: number
    win_rate?: number
    avg_r_net?: number
    pnl_net?: number
    charges?: number
    profit_factor?: number | null
    max_drawdown?: number
    max_drawdown_pct?: number
    expectancy_net?: number
  }
  curve: CurvePoint[]
  days: { date: string; trades: number; wins: number; pnl_net: number; charges: number; r_net: number }[]
  comparisons: {
    trade_id: string
    symbol: string
    trade_date: string
    actual: { outcome: 'target' | 'stop' | 'neither'; exit_reason: string; r_gross: number | null; r_net: number | null }
    expected: { plan_id: string; replays: number; p_target: number; p_stop: number; p_neither: number; avg_r: number | null; data_as_of: string | null } | null
  }[]
  calibration: {
    trades: number
    expected_target_rate: number
    actual_target_rate: number
    expected_stop_rate: number
    actual_stop_rate: number
    expected_avg_r: number | null
    actual_avg_r: number | null
  } | null
  unmatched: number
}

export type BookMode = 'paper' | 'live'

/** Filters shared by /evaluation/trading-performance and the journal CSV. */
export interface AnalyticsFilters {
  mode: BookMode
  /** 'all' | 'default' | a portfolio id (paper); live trades belong to Default. */
  portfolio_id: string
  /** A strategy id, or 'manual' for unattributed (legacy/manual) trades. */
  strategy_id?: string | null
}

export interface Attribution { strategy_id: string | null; strategy_name: string | null; strategy_version: number | null }

export interface OpenMark {
  status: 'live' | 'delayed' | 'stale' | 'unavailable'
  price: number | null
  source: string | null
  as_of: string | null
}

export interface Annotation { note: string; tags: string[]; updated_at: string | null }

export interface JournalRow extends Attribution {
  mode: BookMode
  trade_id: string
  trade_date: string
  symbol: string
  portfolio_id: string
  portfolio_name: string
  attribution: 'strategy' | 'manual'
  status: string
  qty: number | null
  entry_price: number | null
  exit_price: number | null
  exit_time: string | null
  exit_reason: string | null
  pnl_gross: number | null
  charges: number | null
  charges_basis: 'estimated'
  pnl_net: number | null
  r_net: number | null
  /** Only for an open position; null otherwise. */
  mark: OpenMark | null
  /** Gross, from a fresh (live/delayed same-session) mark only. */
  open_pnl: number | null
  annotation: Annotation | null
}

export interface BookStats {
  trades: number
  wins?: number
  losses?: number
  win_rate?: number
  avg_r_net?: number | null
  pnl_net?: number
  charges?: number
  profit_factor?: number | null
  expectancy_net?: number
  max_drawdown?: number
}

export interface BookGroup {
  trades: number
  closed: number
  realized: number
  open_pnl: number
  open_positions: number
  unmarked: number
  stats: BookStats
}

export interface TradingPerformance {
  mode: BookMode
  filters: { portfolio_id: string; strategy_id: string | null }
  generated_at: string
  /** Null when no single account applies (e.g. all paper portfolios). */
  capital: number | null
  realized: { pnl_net: number; trades: number; charges: number; return_pct: number | null }
  open: { positions: number; marked: number; unmarked: number; pnl: number; basis: string }
  stats: BookStats
  curve: { at: string | null; pnl: number; drawdown: number }[]
  by_strategy: (BookGroup & Attribution & { label: string })[]
  by_portfolio: (BookGroup & { id: string; name: string; capital: number })[]
  benchmark: {
    symbol: string
    name: string
    available: boolean
    start: string | null
    end: string | null
    return_pct: number | null
    book_return_pct: number | null
    source: string
    as_of: string | null
    reason: string | null
  }
  charges_note: string
  trades: JournalRow[]
}

const LEGACY_TOKEN: string | undefined = (
  import.meta as unknown as { env?: { VITE_API_TOKEN?: string } }
).env?.VITE_API_TOKEN

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(LEGACY_TOKEN ? { Authorization: `Bearer ${LEGACY_TOKEN}` } : {}),
    },
  })
  const raw = await res.text()
  if (!res.ok) {
    let detail: unknown = raw
    try { detail = (JSON.parse(raw) as { detail?: unknown }).detail ?? raw } catch { /* raw text */ }
    throw new ApiError(res.status, res.statusText, detail, raw)
  }
  return JSON.parse(raw) as T
}

const post = <T>(path: string, body: unknown) => call<T>(path, { method: 'POST', body: JSON.stringify(body) })

export const evaluationApi = {
  portfolioSnapshot: () => call<PortfolioSnapshot>('/evaluation/portfolio-snapshot'),
  quote: (body: BatchBody) => post<BatchQuote>('/evaluation/batches/quote', body),
  startBatch: (body: BatchBody & { confirmed_credits: number }) => post<BatchView>('/evaluation/batches', body),
  listBatches: () => call<BatchRow[]>('/evaluation/batches'),
  getBatch: (id: string) => call<BatchView>(`/evaluation/batches/${encodeURIComponent(id)}`),
  cancelBatch: (id: string) => post<BatchView>(`/evaluation/batches/${encodeURIComponent(id)}/cancel`, {}),
  paperPerformance: (portfolioId = 'default') =>
    call<PaperPerformance>(`/evaluation/paper-performance?portfolio_id=${encodeURIComponent(portfolioId)}`),
  tradingPerformance: (f: AnalyticsFilters) =>
    call<TradingPerformance>(`/evaluation/trading-performance?${analyticsQuery(f)}`),
  tradeJournalCsvUrl: (f: AnalyticsFilters) => `${API_BASE}/evaluation/trade-journal.csv?${analyticsQuery(f)}`,
  annotateTrade: (mode: BookMode, tradeId: string, body: { note: string; tags: string[] }) =>
    call<Annotation>(`/evaluation/trade-journal/${mode}/${encodeURIComponent(tradeId)}`,
      { method: 'PUT', body: JSON.stringify(body) }),
  reportHtmlUrl: (runId: string) => `${API_BASE}/runs/${encodeURIComponent(runId)}/report.html`,
}
