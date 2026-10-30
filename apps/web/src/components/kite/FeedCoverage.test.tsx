// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { FeedCoverage } from './FeedCoverage'

afterEach(cleanup)
it('shows bounded history progress and the fallback reason', () => {
  render(<FeedCoverage stream={{ state: 'live', detail: null, reconnects: 0,
    bootstrap: { total: 1000, completed: 400, failed: 2, running: true, session_ready: 217 },
    symbols: [{ symbol: 'SBIN', source: 'live:kite', last_tick_at: '', bars_1m: 10 },
      { symbol: 'TCS', source: 'delayed:yahoo', last_tick_at: null, bars_1m: 0 }] }} />)
  expect(screen.getByText(/1 Kite.*1 delayed/)).toBeTruthy()
  expect(screen.getByText(/400\/1000.*2 unavailable/)).toBeTruthy()
  expect(screen.getByText(/Delayed fallback.*unsubscribed, stale or still filling/)).toBeTruthy()
})
