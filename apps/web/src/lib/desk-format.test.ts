import { describe, expect, it } from 'vitest'
import { inr, pct01, rMult, signedPct, sourceLabel, istTime, orbEmptyMessage, targetRuleSwitch } from './desk-format'

describe('desk-format', () => {
  it('formats rupees with Indian grouping', () => {
    expect(inr(123456.7)).toBe('₹1,23,456.70')
    expect(inr(1500, true)).toBe('₹1,500')
    expect(inr(null)).toBe('—')
  })
  it('formats odds, moves and R', () => {
    expect(pct01(0.2938)).toBe('29.4%')
    expect(signedPct(0.55)).toBe('+0.55%')
    expect(signedPct(-1.2)).toBe('-1.20%')
    expect(rMult(-1)).toBe('−1.00R')
    expect(rMult(0.5)).toBe('+0.50R')
    expect(rMult(0)).toBe('0.00R')
  })
  it('labels the data source honestly', () => {
    expect(sourceLabel('delayed:yahoo')).toEqual({ live: false, label: 'DELAYED' })
    expect(sourceLabel('live:kite')).toEqual({ live: true, label: 'LIVE' })
    expect(sourceLabel(undefined).label).toBe('DELAYED')
  })
  it('shows exchange (IST) time', () => {
    expect(istTime('2026-10-28T04:10:00+00:00')).toBe('09:40')
  })
})


describe('ORB empty state', () => {
  it('distinguishes missing session data from a scan without breakouts', () => {
    expect(orbEmptyMessage({ with_data: 0, scanned: 0 })).toContain('No bars')
    expect(orbEmptyMessage({ with_data: 10, scanned: 0, unusable: 10 })).toContain('incomplete')
    expect(orbEmptyMessage({ with_data: 10, scanned: 0, unusable: 0 })).toContain('liquidity')
    expect(orbEmptyMessage({ with_data: 10, scanned: 10 })).toContain('No chart has closed above')
  })
})

describe('target rule switch', () => {
  it('keeps the planned target instead of reading the R multiple as a price', () => {
    const plan = { target: 1532.5, target_r: 2 }
    expect(targetRuleSwitch('price', plan)).toEqual({ target_rule: 'price', target_param: 1532.5 })
    expect(targetRuleSwitch('r', plan)).toEqual({ target_rule: 'r', target_param: 2 })
  })
})
