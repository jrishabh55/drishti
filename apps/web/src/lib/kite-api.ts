/* Kite (Zerodha) connection, live data and guarded execution — client for
   apps/api/routes/kite.py. Kept out of lib/api.ts so the Kite feature ships as
   its own module; it reuses the same base URL, legacy bearer and ApiError. */
import { API_BASE, ApiError } from './api'

const LEGACY_TOKEN: string | undefined = (
  import.meta as unknown as { env?: { VITE_API_TOKEN?: string } }
).env?.VITE_API_TOKEN

async function kfetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(LEGACY_TOKEN ? { Authorization: `Bearer ${LEGACY_TOKEN}` } : {}),
      ...(init?.headers ?? {}),
    },
  })
  if (!res.ok) {
    const raw = await res.text().catch(() => '')
    let detail: unknown = raw
    try {
      const parsed = JSON.parse(raw) as { detail?: unknown }
      detail = parsed?.detail ?? parsed
    } catch {
      /* non-JSON body */
    }
    throw new ApiError(res.status, res.statusText, detail, raw)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

const post = <T>(path: string, body?: unknown) =>
  kfetch<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

/** Plain-language message from an API error, including refusal reasons. */
export function kiteError(e: unknown): string {
  if (e instanceof ApiError) {
    const d = e.detail as { reasons?: string[]; message?: string } | string | null
    if (d && typeof d === 'object' && Array.isArray(d.reasons) && d.reasons.length)
      return d.reasons.join(' · ')
    if (Array.isArray(e.detail))
      return (e.detail as { loc: string[]; msg: string }[])
        .map((x) => `${x.loc.slice(1).join('.')}: ${x.msg}`).join('; ')
  }
  return e instanceof Error ? e.message : String(e)
}

export type SessionState = 'none' | 'active' | 'expired' | 'revoked'

export interface LiveSymbol {
  symbol: string
  last_tick_at: string | null
  source: string
  last_price?: number | null
  /** today's session so far is filled from Kite history; until then: DELAYED */
  backfilled?: boolean
  backfill_error?: string | null
  bars_1m: number
}

export interface LiveStatus {
  state: string
  detail: string | null
  symbols: LiveSymbol[]
  reconnects: number
  started_at?: string | null
  bootstrap?: { total: number; completed: number; failed: number; running: boolean; session_ready: number }
  history_errors?: Record<string, string>
  max_symbols?: number
  selection_notes?: string[]
}

export interface KiteControl {
  armed: boolean
  armed_at: string | null
  killed: boolean
  killed_at: string | null
  kill_summary: { trades: number; exits_sent: number; pending?: number; needs_attention: number; error: string | null } | null
}

export interface KiteStatus {
  encryption_configured: boolean
  app_configured: boolean
  app_unreadable: boolean
  api_key_last4: string | null
  session: SessionState
  kite_user_id: string | null
  user_name: string | null
  login_at: string | null
  expires_at: string | null
  redirect_path: string
  live: LiveStatus
  data_access?: KiteDataAccess
  control: KiteControl
  /** NSE closing auction session timing for F&O stocks, when one is in force */
  cas?: { active: false } | {
    active: true; until: string; continuous_end: string; broker_square_off: string
    latest_flat_by: string; profile_ok: boolean; applies_to: string
  }
  orders: { server_enabled: boolean; owner: boolean; owner_client_id?: string | null; blocked_by: string[] }
  note?: string | null
}

export interface KiteDataAccess {
  owner_id: string
  shared: boolean
  session: SessionState
  live: LiveStatus
}

export interface KiteDataGrant {
  owner_id: string
  recipient_id: string
  label: string
  created_at: string
}

export interface KiteSharing {
  user_id: string
  enabled: boolean
  grants: KiteDataGrant[]
  received: Pick<KiteDataGrant, 'owner_id' | 'created_at'>[]
  selected_owner_id: string | null
  data_access: KiteDataAccess
}

export interface KiteLeg {
  tag: string
  order_id: string
  status: string | null
  filled?: number
  avg?: number | null
  message?: string | null
}

export interface KiteTrade extends StrategyAttribution {
  id: string
  ticket_id: string | null
  source: string
  symbol: string
  qty: number
  order_type: string
  limit: number | null
  stop: number
  target: number
  flat_by: string
  status: string
  trade_date: string
  filled_qty: number
  entry_avg?: number | null
  exit_avg?: number | null
  exit_reason?: string
  attention?: string
  /** status closed_outside: sold elsewhere (e.g. the Kite app); P&L unknown. */
  closed_note?: string
  pnl_gross?: number
  pnl_net?: number
  r_net?: number | null
  legs: Record<string, KiteLeg>
  events: { at: string; what: string }[]
}

export type StrategyMode = 'off' | 'paper' | 'confirm' | 'auto'

/** Server-stamped on plans, tickets, orders, paper/live trades and backtests. */
export interface StrategyAttribution {
  strategy_id?: string
  strategy_name?: string
  strategy_version?: number
}

/** Absent or empty: the legacy full-position fixed stop/target. All in R of the initial risk. */
export interface ExitPolicy {
  breakeven_after_r?: number
  trail_after_r?: number
  trail_distance_r?: number
  scale_out_at_r?: number
  scale_out_pct?: number
}

export interface StrategySignal extends StrategyAttribution {
  id: string
  date: string
  at: string
  symbol: string
  status: 'blocked' | 'paper' | 'await_confirm' | 'expired' | 'skipped' | 'sent'
  source: string
  reason?: string
  ticket_id?: string
  kite_trade_id?: string
  expires_at?: string
  interval?: '1m' | '5m'
  portfolio_id?: string
  order?: { qty: number; symbol: string; price?: number; stop: number; target: number }
}

export type ScanOutcomeStatus =
  'waiting' | 'stale' | 'no_data' | 'blocked' | 'paper' | 'await_confirm' | 'sent' | 'limit_reached'

export interface StrategyLastScan {
  at: string
  version: number
  mode: StrategyMode
  interval: '1m' | '5m'
  source: string | null
  outcomes: Record<string, { status: ScanOutcomeStatus; reason: string; source?: string; data_as_of?: string | null }>
  scanner: { id: string; matches: number; error: string | null; truncated?: number; data_as_of?: string } | null
}

export interface Strategy {
  id: string
  name: string
  mode: StrategyMode
  symbols: string[]
  setup: 'orb' | 'level'
  level: number | null
  or_minutes: number
  stop_rule: 'or_mid' | 'or_low' | 'pct' | 'atr' | 'price'
  stop_param: number | null
  target_r: number
  /** 'price' keeps a research target as an absolute price (target_param). */
  target_rule: 'r' | 'price'
  target_param: number | null
  order_type: 'LIMIT' | 'MARKET'
  max_signals_per_day: number
  confirm_ttl_s: number
  portfolio_id: string
  interval: '1m' | '5m'
  scanner_id: string | null
  min_volume_x: number | null
  exit_policy: ExitPolicy | null
  version?: number
  paused_reason?: string
  /** Set by a pause (app or Telegram): mode is off and resume restores Paper/Confirm, never Auto. */
  paused_from?: StrategyMode | null
  auto_opt_in?: { version: number; at: string } | null
  last_scan?: StrategyLastScan | null
  signals: StrategySignal[]
}

type ServerOwned = 'id' | 'signals' | 'version' | 'paused_reason' | 'paused_from' | 'auto_opt_in' | 'last_scan'
export type StrategyInput = Omit<Strategy, ServerOwned>

export interface BacktestRequest {
  strategy_id: string
  start_date: string
  end_date: string
  split_date?: string | null
  symbols?: string[] | null
}

export interface BacktestStats {
  trades: number
  wins?: number
  losses?: number
  win_rate?: number
  avg_r_net?: number
  pnl_net?: number
  charges?: number
  profit_factor?: number | null
  max_drawdown?: number
}

export interface BacktestTrade extends StrategyAttribution {
  symbol: string
  trade_date: string
  signal_time: string
  qty: number
  status: string
  split: 'train' | 'test' | null
  entry_fill: { time: string; price: number } | null
  exit_fill: { time: string; price: number; reason: string } | null
  pnl_net?: number
  r_net?: number | null
  charges?: number
  limit?: number | null
  sizing_bound_by?: string
  cash_at_signal?: number
  risk_warnings?: string[]
}

export type CoverageDay = 'complete' | 'incomplete' | 'no_data'

export interface BacktestSummary extends StrategyAttribution {
  id: string
  created_at: string
  strategy_id: string
  config: BacktestRequest & { symbols: string[]; interval: '1m' | '5m' }
  summary: { all: BacktestStats; train?: BacktestStats; test?: BacktestStats }
  costs: { charges: number; estimated: boolean }
  sources: Record<string, string>
  generated_at: string
}

export interface Backtest extends BacktestSummary {
  strategy_snapshot: Record<string, unknown> & { version: number }
  profile_snapshot: Record<string, unknown>
  coverage: Record<string, { status: 'ok' | 'partial' | 'insufficient_history'; days: Record<string, CoverageDay>; error?: string }>
  trades: BacktestTrade[]
  skipped: { date: string; symbol: string; reason: string; reasons?: string[]; signal_time?: string; cash?: number }[]
  /** Desk risk.check replayed at each signal; past NSE facts are unavailable and disclosed. */
  risk: { engine: string; market_facts: 'unavailable'; warnings: string[] }
  scanner: { id: string; universe: string[]; matches: Record<string, string[]>; timeframe: '1d'; as_of: string } | null
  disclaimer: string
  assumptions: string[]
}

export interface KitePerformance {
  trades: number
  wins?: number
  win_rate?: number
  avg_r_net?: number
  pnl_net?: number
  charges?: number
  profit_factor?: number | null
  max_drawdown?: number
}

export const kite = {
  sharing: () => kfetch<KiteSharing>('/kite/sharing'),
  grantData: (recipient_id: string, label = '') =>
    post<KiteSharing>('/kite/sharing/grants', { recipient_id, label }),
  revokeData: (recipient_id: string) => kfetch<void>(`/kite/sharing/grants/${encodeURIComponent(recipient_id)}`, { method: 'DELETE' }),
  selectDataSource: (owner_id: string | null) => kfetch<KiteSharing>('/kite/sharing/source', {
    method: 'PUT', body: JSON.stringify({ owner_id }),
  }),
  status: () => kfetch<KiteStatus>('/kite/status'),
  saveApp: (api_key: string, api_secret: string) =>
    kfetch<KiteStatus>('/kite/app', { method: 'PUT', body: JSON.stringify({ api_key, api_secret }) }),
  forgetApp: () => kfetch<KiteStatus>('/kite/app', { method: 'DELETE' }),
  login: () => post<{ login_url: string }>('/kite/login'),
  callback: (request_token: string, state: string, status?: string) =>
    post<KiteStatus>('/kite/callback', { request_token, state, status }),
  disconnect: () => post<KiteStatus>('/kite/disconnect'),
  liveStart: (symbols: string[], universe?: 'fno' | 'nifty500') => post<LiveStatus>('/kite/live/start', { symbols, universe }),
  liveStop: () => post<LiveStatus>('/kite/live/stop'),
  arm: (ack: string) => post<KiteControl>('/kite/arm', { ack }),
  disarm: () => post<KiteControl>('/kite/disarm'),
  kill: () => post<KiteControl & { summary: KiteControl['kill_summary'] }>('/kite/kill'),
  trades: () => kfetch<KiteTrade[]>('/kite/trades'),
  sendTicket: (ticket_id: string, confirm_text: string) =>
    post<KiteTrade>('/kite/trades', { ticket_id, confirm_text }),
  refreshTrade: (id: string) => post<KiteTrade>(`/kite/trades/${id}/refresh`),
  exitTrade: (id: string) => post<KiteTrade>(`/kite/trades/${id}/exit`),
  performance: () => kfetch<KitePerformance>('/kite/performance'),
  strategies: () => kfetch<Strategy[]>('/kite/strategies'),
  createStrategy: (body: StrategyInput) => post<Strategy>('/kite/strategies', body),
  updateStrategy: (id: string, body: StrategyInput) =>
    kfetch<Strategy>(`/kite/strategies/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteStrategy: (id: string) => kfetch<void>(`/kite/strategies/${id}`, { method: 'DELETE' }),
  scanStrategy: (id: string) => post<{ new: StrategySignal[]; strategy: Strategy }>(`/kite/strategies/${id}/scan`),
  confirmSignal: (sid: string, sig: string, confirm_text: string) =>
    post<KiteTrade>(`/kite/strategies/${sid}/signals/${sig}/confirm`, { confirm_text }),
  skipSignal: (sid: string, sig: string) => post<StrategySignal>(`/kite/strategies/${sid}/signals/${sig}/skip`),
  pauseStrategy: (id: string) => post<Strategy>(`/kite/strategies/${id}/pause`),
  resumeStrategy: (id: string, mode: 'paper' | 'confirm' = 'paper') =>
    post<Strategy>(`/kite/strategies/${id}/resume`, { mode }),
  autoOptIn: (id: string, ack: string) => post<Strategy>(`/kite/strategies/${id}/auto-opt-in`, { ack }),
  autoOptOut: (id: string) => kfetch<Strategy>(`/kite/strategies/${id}/auto-opt-in`, { method: 'DELETE' }),
  backtests: (strategy_id?: string) =>
    kfetch<BacktestSummary[]>(`/kite/strategy-backtests${strategy_id ? `?strategy_id=${encodeURIComponent(strategy_id)}` : ''}`),
  backtest: (id: string) => kfetch<Backtest>(`/kite/strategy-backtests/${encodeURIComponent(id)}`),
  runBacktest: (body: BacktestRequest) => post<Backtest>('/kite/strategy-backtests', body),
}

/** Words the server requires typed to opt a strategy version into unattended live sends. */
export const autoWords = (name: string) => `AUTO ${name}`

/** Words the server requires typed to send a live order. */
export const confirmWords = (qty: number, symbol: string) => `BUY ${qty} ${symbol}`

/** The Kite login redirect query, if this page load is Kite's callback. */
export function kiteRedirectParams(search: string): { request_token: string; state: string; status: string } | null {
  const q = new URLSearchParams(search)
  const request_token = q.get('request_token')
  const state = q.get('state')
  if (!request_token || !state) return null
  return { request_token, state, status: q.get('status') ?? 'success' }
}
