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
  control: KiteControl
  /** NSE closing auction session timing for F&O stocks, when one is in force */
  cas?: { active: false } | {
    active: true; until: string; continuous_end: string; broker_square_off: string
    latest_flat_by: string; profile_ok: boolean; applies_to: string
  }
  orders: { server_enabled: boolean; owner: boolean; owner_client_id?: string | null; blocked_by: string[] }
  note?: string | null
}

export interface KiteLeg {
  tag: string
  order_id: string
  status: string | null
  filled?: number
  avg?: number | null
  message?: string | null
}

export interface KiteTrade {
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
  pnl_gross?: number
  pnl_net?: number
  r_net?: number | null
  legs: Record<string, KiteLeg>
  events: { at: string; what: string }[]
}

export type StrategyMode = 'off' | 'paper' | 'confirm'

export interface StrategySignal {
  id: string
  date: string
  at: string
  symbol: string
  status: 'blocked' | 'paper' | 'await_confirm' | 'expired' | 'skipped' | 'sent'
  source: string
  reason?: string
  ticket_id?: string
  expires_at?: string
  order?: { qty: number; symbol: string; price?: number; stop: number; target: number }
}

export interface Strategy {
  id: string
  name: string
  mode: StrategyMode
  symbols: string[]
  setup: 'orb' | 'level'
  level: number | null
  or_minutes: number
  stop_rule: 'or_mid' | 'or_low' | 'pct' | 'atr'
  stop_param: number | null
  target_r: number
  order_type: 'LIMIT' | 'MARKET'
  max_signals_per_day: number
  confirm_ttl_s: number
  version?: number
  paused_reason?: string
  signals: StrategySignal[]
}

export type StrategyInput = Omit<Strategy, 'id' | 'signals' | 'version' | 'paused_reason'>

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
  status: () => kfetch<KiteStatus>('/kite/status'),
  saveApp: (api_key: string, api_secret: string) =>
    kfetch<KiteStatus>('/kite/app', { method: 'PUT', body: JSON.stringify({ api_key, api_secret }) }),
  forgetApp: () => kfetch<KiteStatus>('/kite/app', { method: 'DELETE' }),
  login: () => post<{ login_url: string }>('/kite/login'),
  callback: (request_token: string, state: string, status?: string) =>
    post<KiteStatus>('/kite/callback', { request_token, state, status }),
  disconnect: () => post<KiteStatus>('/kite/disconnect'),
  liveStart: (symbols: string[]) => post<LiveStatus>('/kite/live/start', { symbols }),
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
}

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
