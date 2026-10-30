/* Types for the Trade Desk API (apps/api/routes/desk.py). No codegen —
   mirror the Python shapes by hand when they change. */

export type DataSource = 'delayed:yahoo' | `live:${string}` | string

export interface DeskProfile {
  capital: number
  cash_buffer_pct: number
  max_position_pct: number
  min_order_inr: number
  max_order_inr: number
  max_loss_per_trade_inr: number
  max_daily_loss_inr: number
  max_trades_per_day: number
  max_orders_per_day: number
  min_reward_risk: number
  require_stop: boolean
  no_entries_before: string | null
  no_entries_after: string | null
  flat_by: string
  broker_square_off: string
  product: 'MIS' | 'CNC'
  allow_short: boolean
  blocked: string[]
  slippage_ticks: number
  brokerage_intraday_flat: number
  brokerage_intraday_pct: number
  brokerage_delivery_flat: number
  dp_charge_per_sell: number
  universe: 'fno' | 'nifty500' | 'all'
  min_price: number
  min_avg_value_cr: number
  or_minutes: number
}

export interface OrbHit {
  symbol: string
  name: string
  price: number
  at: string
  bar_start: string
  bar_index: number
  or_high: number
  or_low: number
  pct_over: number
  volume_x: number | null
  last: number
  last_vs_or_high_pct: number
  avg_value_cr: number | null
}

export interface OrbScan {
  date: string
  evaluation_as_of?: string
  coverage?: { complete: number; incomplete: number; missing_times: { start: string; charts: number }[] }
  as_of: string
  data_as_of: string | null
  or_minutes: number
  range: string
  source: DataSource
  universe: string
  counts: Record<string, number>
  hits: OrbHit[]
  notes: string[]
  note: string
}

export interface EntryStatus {
  interval?: '1m' | '5m'
  symbol: string
  setup: 'orb' | 'level'
  date: string
  status: 'triggered' | 'waiting' | 'expired' | 'no-data'
  phase: string
  reason: string
  opening_range: { high: number; low: number; minutes: number; bars: number; of: number; complete: boolean } | null
  level: number | null
  trigger: { time: string; bar_start: string; bar_index: number; price: number; volume_x: number | null; pct_over: number } | null
  last: { bar_start: string; close: number; vs_level_pct: number } | null
  source: DataSource
  data_as_of: string | null
  notes: string[]
}

export interface Plan {
  interval?: '1m' | '5m'
  id: string
  symbol: string
  side: 'buy'
  date: string
  setup: 'orb' | 'level'
  entry_time: string | null
  entry_bar: number | null
  opening_range: { high: number; low: number; minutes: number } | null
  or_minutes: number
  stop_rule: 'or_mid' | 'or_low' | 'price' | 'pct' | 'atr'
  stop_param: number | null
  target_rule: 'r' | 'price'
  target_param: number
  flat_by: string
  entry: number
  stop: number
  stop_label: string
  stop_detail: string
  target: number
  target_label: string
  target_r: number
  reward_risk: number
  risk_per_share: number
  tick: number
  warnings: string[]
  status: 'draft' | 'ticketed' | 'closed'
  notes: string
  level: number | null
  source: DataSource | null
  created_at?: string
  updated_at?: string
}

export interface PlanRequest {
  interval?: '1m' | '5m'
  symbol: string
  date?: string | null
  setup?: 'orb' | 'level'
  level?: number | null
  entry?: number | null
  entry_time?: string | null
  or_high?: number | null
  or_low?: number | null
  or_minutes?: number | null
  stop_rule?: Plan['stop_rule'] | null
  stop_param?: number | null
  target_rule?: 'r' | 'price'
  target_param?: number
  flat_by?: string | null
  notes?: string
}

export interface TimeMachineRequest {
  interval?: '1m' | '5m'
  mode: 'intraday' | 'swing'
  symbol: string
  date: string
  entry: number
  stop: number
  target: number
  entry_time?: string | null
  setup?: string
  or_minutes?: number
  stop_rule?: string
  until?: string | null
  horizon?: number
  replays?: number
  seed?: number
  circuit_pct?: number | null
}

export interface TimeMachineResult {
  mode: 'intraday' | 'swing'
  key: string
  source: DataSource
  data_as_of: string | null
  plan: Record<string, unknown> & { entry: number; stop: number; target: number; target_r: number }
  mc: {
    replays: number
    seed: number
    bars_left: number
    p_target: number
    p_stop: number
    p_neither: number
    p_green: number
    median_close: number
    p5_close: number
    p95_close: number
    avg_R: number
    r_at_target: number
    p_gap_through_stop: number
    p_circuit: number | null
    bands: number[][]
    band_times: string[]
    hist: { lo: number; hi: number; bins: number[] }
    until: string
    pool_sessions: number
    pool_from: string
    pool_to: string
  }
  history: {
    sessions: number
    triggered: number
    target: number
    stop: number
    close: number
    avg_R: number | null
    note?: string
    rule: Record<string, unknown> | null
  }
  verdict: string
  verdict_notes: string[]
  warnings: string[]
  note: string
}

export interface SizeLimit {
  name: string
  inr: number
  per: number
  shares: number
  how: string
  binds: boolean
}

export interface Sizing {
  symbol: string
  qty: number
  entry: number
  entry_from: string
  stop: number
  target: number
  risk_per_share: number
  reward_risk: number
  reward_risk_net: number | null
  cost: number
  max_loss: number
  max_loss_net: number
  reward_at_target: number
  reward_at_target_net: number
  charges_at_stop: number
  charges_at_target: number
  bound_by: string
  limits: SizeLimit[]
  refusal: string | null
  notes: string[]
  product: 'MIS' | 'CNC'
  flat_by: string
}

export interface DeskOrder {
  symbol: string
  exchange: 'NSE'
  side: 'buy' | 'sell'
  qty: number
  order_type: 'LIMIT' | 'MARKET'
  product: 'MIS' | 'CNC'
  price?: number
  stop: number
  target: number
  why: string
}

export interface RiskResult {
  symbol: string
  kind: 'entry' | 'exit'
  verdict: 'PASS' | 'BLOCK'
  reasons: string[]
  warnings: string[]
  info: string[]
  max_loss: number | null
  reward_risk: number | null
  reward_risk_net: number | null
  fingerprint: string
}

export interface Ticket {
  id: string
  plan_id: string | null
  symbol: string
  status: 'ready' | 'blocked' | 'refused' | 'paper' | 'sent' | 'cancelled'
  order: DeskOrder | null
  sizing: Sizing
  verdict: RiskResult | null
  verdict_warnings?: string[]
  fingerprint: string
  words: string
  kite_basket: Record<string, unknown>[]
  paper_trade_id?: string
  created_at?: string
  /** The paper portfolio this ticket is bound to for life (legacy: 'default'). */
  portfolio_id?: string
}

export interface PaperTrade {
  interval?: '1m' | '5m'
  data_warning?: string
  id: string
  ticket_id: string
  symbol: string
  qty: number
  order_type: string
  limit: number | null
  stop: number
  target: number
  flat_by: string
  trade_date: string
  status: 'pending' | 'open' | 'closed' | 'cancelled'
  entry_fill: { time: string; price: number } | null
  exit_fill: { time: string; price: number; reason: string } | null
  pnl_gross?: number
  pnl_net?: number
  charges?: number
  r_gross?: number | null
  r_net?: number | null
  mae?: number
  mfe?: number
  events: { at: string; what: string }[]
  portfolio_id?: string
}

export interface PaperLedger {
  trades: PaperTrade[]
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
  }
  cash: number
  realized: number
  capital: number
  portfolio_id: string
  portfolio_name: string
}

/** A paper portfolio. 'default' is the original ledger; its capital is the
 *  profile's capital (Limits & settings). Named ones keep their own capital,
 *  which locks once they have trades. Risk rules, plans and the watchlist
 *  are shared by every portfolio of the account. */
export interface Portfolio {
  id: string
  name: string
  capital: number
  is_default: boolean
  trades: number
  capital_locked: boolean
  capital_history: { at: string; from: number; to: number }[]
  created_at?: string
}

export type AlertType = 'above' | 'below' | 'target' | 'stop' | 'pct_move' | 'time' | 'opening_bell'

export interface Alert {
  id: string
  type: AlertType
  symbol?: string
  price?: number
  pct?: number
  direction?: 'up' | 'down'
  at?: string
  date?: string
  label?: string
  note?: string
  rearm: boolean
  active: boolean
  rang_at?: string
}

export interface AlertEvent {
  id: string
  alert_id: string
  at: string
  symbol?: string
  type: AlertType
  message: string
  seen: boolean
}

export interface DeskConfig {
  kite_publisher_api_key: string | null
  today: string
}
