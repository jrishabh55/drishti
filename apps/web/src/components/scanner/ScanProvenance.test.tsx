// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { ScanProvenance } from './ScanProvenance'

afterEach(cleanup)
it('distinguishes mixed candles, coverage, daily prices and public fundamentals', () => {
  render(<ScanProvenance result={{ universe: 500, data_as_of: '', matches: [], source: 'mixed',
    coverage: { requested: 500, live: 217, delayed: 280, mixed: 3,
      by_timeframe: { '5m': { kite: 217, delayed: 280, missing: 3 } } } }} />)
  expect(screen.getByText(/Mixed Kite\/delayed/)).toBeTruthy()
  expect(screen.getByText(/217 Kite.*280 delayed.*3 partial/)).toBeTruthy()
  expect(screen.getByText(/5m: 217 Kite, 280 delayed, 3 missing/)).toBeTruthy()
  expect(screen.getByText(/closed daily candles/)).toBeTruthy()
  expect(screen.getByText(/Fundamentals.*public snapshot/)).toBeTruthy()
})
it('labels legacy results as delayed when provenance is absent', () => {
  render(<ScanProvenance result={{ universe: 1, data_as_of: '', matches: [] }} />)
  expect(screen.getByText(/Delayed Yahoo/)).toBeTruthy()
})
