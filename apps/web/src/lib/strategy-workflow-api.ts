import { jsonFetch } from './api'
import type { Strategy, StrategyInput, KiteStatus } from './kite-api'

export type DraftSource = { run_id?: string; r_multiple?: number; scanner_id?: string; symbols?: string[]; portfolio_id?: string }
export interface StrategyDraft {
  id: string
  state: string
  expires_at: string
  source: Record<string, unknown>
  strategy: StrategyInput & { portfolio_id: string; interval: '1m' | '5m' }
  warnings: string[]
}
export interface TradingReadiness {
  kite: KiteStatus
  loops: Record<string, string>
  strategies: { id: string; name: string; mode: string; portfolio_id: string; last_scan: unknown }[]
  paper_blockers: string[]
  live_orders_enabled: boolean
  market_open: boolean
  data_coverage: { subscribed: number; ready: number }
  validation_note: string
}
export const workflowApi = {
  preview: (source: DraftSource) => jsonFetch<StrategyDraft>('/desk/strategy-drafts', {
    method: 'POST', body: JSON.stringify(source),
  }),
  save: (id: string, strategy: StrategyInput) => jsonFetch<{ strategy: Strategy }>(`/desk/strategy-drafts/${encodeURIComponent(id)}/save`, {
    method: 'POST', body: JSON.stringify({ strategy: { ...strategy, mode: 'off' } }),
  }),
  readiness: () => jsonFetch<TradingReadiness>('/desk/readiness'),
}
