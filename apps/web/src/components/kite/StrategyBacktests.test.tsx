// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { BacktestResult } from './StrategyBacktests'
import { BLANK_STRATEGY, cleanPolicy, StrategyForm, toInput } from './StrategyForm'
import type { Backtest, Strategy } from '#/lib/kite-api'

afterEach(cleanup)

const result: Backtest = {
  id: 'b1', created_at: '2026-10-30T10:00:00Z', strategy_id: 's1', strategy_name: 'orb', strategy_version: 2,
  config: { strategy_id: 's1', start_date: '2026-10-01', end_date: '2026-10-27', split_date: '2026-10-15', symbols: ['SBIN', 'INFY'], interval: '5m' },
  summary: { all: { trades: 2, win_rate: 0.5, avg_r_net: 0.4, pnl_net: 120, max_drawdown: 80 },
    train: { trades: 1, win_rate: 1, avg_r_net: 1.8, pnl_net: 200, max_drawdown: 0 }, test: { trades: 1, win_rate: 0, avg_r_net: -1, pnl_net: -80, max_drawdown: 80 } },
  costs: { charges: 41.2, estimated: true }, sources: { SBIN: 'live:kite' }, generated_at: '2026-10-30T10:00:00Z',
  strategy_snapshot: { version: 2 }, profile_snapshot: {},
  coverage: {
    SBIN: { status: 'partial', days: { '2026-10-01': 'complete', '2026-10-02': 'incomplete' } },
    INFY: { status: 'insufficient_history', days: {} },
  },
  trades: [{ symbol: 'SBIN', trade_date: '2026-10-01', signal_time: '09:35', qty: 10, status: 'closed', split: 'train',
    entry_fill: { time: '09:35', price: 802.5 }, exit_fill: { time: '11:00', price: 807, reason: 'target' }, pnl_net: 200, r_net: 1.8 }],
  skipped: [{ date: '2026-10-03', symbol: 'SBIN', reason: 'sized to 0 shares' }],
  disclaimer: 'Historical simulation … It is not a prediction.', assumptions: ['Charges: estimated'],
}

it('shows the disclaimer, split periods, honest coverage and skipped signals', () => {
  render(<BacktestResult b={result} />)
  expect(screen.getByText(/not a prediction/)).toBeTruthy()
  expect(screen.getByText(/Train \(before 2026-10-15\)/)).toBeTruthy()
  expect(screen.getByText(/Test \(from 2026-10-15\)/)).toBeTruthy()
  expect(screen.getByText(/SBIN partial · 1 day\(s\) skipped · live:kite/)).toBeTruthy()
  expect(screen.getByText(/INFY insufficient history/)).toBeTruthy()
  expect(screen.getByText(/1 signal\(s\) not traded/)).toBeTruthy()
})

it('drops blank exit inputs so an empty policy keeps the fixed stop/target', () => {
  expect(cleanPolicy({ breakeven_after_r: undefined, scale_out_pct: Number.NaN })).toBeNull()
  expect(cleanPolicy({ trail_after_r: 1, trail_distance_r: 0.5 })).toEqual({ trail_after_r: 1, trail_distance_r: 0.5 })
})

it('gives legacy strategies the new defaults and strips server-owned fields', () => {
  const legacy = { ...BLANK_STRATEGY, id: 's', signals: [], version: 3, paused: true, auto_opt_in: null, last_scan: null } as Strategy
  const { portfolio_id, interval, exit_policy, ...rest } = toInput(legacy)
  expect([portfolio_id, interval, exit_policy]).toEqual(['default', '5m', null])
  expect(rest).not.toHaveProperty('version')
  expect(rest).not.toHaveProperty('paused')
})

it('live modes hide paper-only exits and save on the Default portfolio', () => {
  const onSave = vi.fn()
  render(<StrategyForm initial={{ ...BLANK_STRATEGY, symbols: ['SBIN'], portfolio_id: 'lab', exit_policy: { breakeven_after_r: 1 } }}
    busy={false} onCancel={() => {}} onSave={onSave} portfolios={[{ id: 'default', name: 'Default' }, { id: 'lab', name: 'Lab' }]} />)
  expect(screen.getByLabelText('Stop to breakeven after (R)')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Auto' }))
  expect(screen.queryByLabelText('Stop to breakeven after (R)')).toBeNull()
  expect(screen.getByText(/separate typed opt-in/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Save strategy' }))
  expect(onSave.mock.calls[0][0]).toMatchObject({ mode: 'auto', portfolio_id: 'default', exit_policy: null, symbols: ['SBIN'] })
})

it('draft mode hides activation and always saves Off, keeping research price levels', () => {
  const onSave = vi.fn()
  render(<StrategyForm draft busy={false} onCancel={() => {}} onSave={onSave}
    initial={{ ...BLANK_STRATEGY, symbols: ['SBIN'], mode: 'paper', portfolio_id: 'lab', setup: 'level', level: 802,
      stop_rule: 'price', stop_param: 797.5, target_rule: 'price', target_param: 812 }}
    portfolios={[{ id: 'default', name: 'Default' }, { id: 'lab', name: 'Lab' }]} />)
  expect(screen.queryByRole('button', { name: 'Auto' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull()
  expect((screen.getByLabelText('Target ₹', { selector: 'input' }) as HTMLInputElement).value).toBe('812')
  fireEvent.click(screen.getByRole('button', { name: 'Save strategy' }))
  expect(onSave.mock.calls[0][0]).toMatchObject({
    mode: 'off', portfolio_id: 'lab', stop_rule: 'price', stop_param: 797.5, target_rule: 'price', target_param: 812,
  })
})
