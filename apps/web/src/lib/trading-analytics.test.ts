import { describe, expect, it } from 'vitest'
import { analyticsQuery, markLabel, strategyLabel } from './evaluation-format'

describe('trading analytics format', () => {
  it('builds the shared filter query, omitting an unset strategy', () => {
    expect(analyticsQuery({ mode: 'paper', portfolio_id: 'all' })).toBe('mode=paper&portfolio_id=all')
    expect(analyticsQuery({ mode: 'live', portfolio_id: 'default', strategy_id: 'manual' }))
      .toBe('mode=live&portfolio_id=default&strategy_id=manual')
  })

  it('adds a strategy version only for a real strategy and a positive version', () => {
    expect(analyticsQuery({ mode: 'paper', portfolio_id: 'all', strategy_id: 's1', strategy_version: 2 }))
      .toBe('mode=paper&portfolio_id=all&strategy_id=s1&strategy_version=2')
    expect(analyticsQuery({ mode: 'paper', portfolio_id: 'all', strategy_id: 'manual', strategy_version: 2 }))
      .toBe('mode=paper&portfolio_id=all&strategy_id=manual')
    expect(analyticsQuery({ mode: 'paper', portfolio_id: 'all', strategy_version: 2 })).toBe('mode=paper&portfolio_id=all')
    expect(analyticsQuery({ mode: 'paper', portfolio_id: 'all', strategy_id: 's1', strategy_version: 0 }))
      .toBe('mode=paper&portfolio_id=all&strategy_id=s1')
    expect(analyticsQuery({ mode: 'paper', portfolio_id: 'all', strategy_id: 's1', strategy_version: null }))
      .toBe('mode=paper&portfolio_id=all&strategy_id=s1')
  })

  it('never invents attribution for legacy rows', () => {
    expect(strategyLabel({ strategy_id: null, strategy_name: null, strategy_version: null })).toBe('Manual/legacy')
    expect(strategyLabel({ strategy_id: 's1', strategy_name: 'ORB 15', strategy_version: 2 })).toBe('ORB 15 v2')
    expect(strategyLabel({ strategy_id: 's1', strategy_name: null, strategy_version: null })).toBe('s1')
  })

  it('says how fresh an open mark is', () => {
    expect(markLabel(null)).toBe('—')
    expect(markLabel({ status: 'unavailable', price: null, source: null, as_of: null })).toBe('no price')
    expect(markLabel({ status: 'stale', price: 490, source: 'live:kite', as_of: '2026-10-29T10:00:00+05:30' }))
      .toMatch(/^stale ₹490\.00 · /)
    expect(markLabel({ status: 'delayed', price: 103, source: 'delayed:yahoo', as_of: '2026-10-30T10:40:00+05:30' }))
      .toBe('DELAYED ₹103.00 @ 10:40')
    expect(markLabel({ status: 'live', price: 102, source: 'live:kite', as_of: '2026-10-30T11:00:00+05:30' }))
      .toBe('LIVE ₹102.00 @ 11:00')
  })
})
