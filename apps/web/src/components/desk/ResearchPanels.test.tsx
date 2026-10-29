// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { FactsStrip, NewsList, ResultsPanel } from './ResearchPanels'
import type { FactsResponse, NewsResponse, ResultsResponse } from '#/lib/desk-research'

afterEach(cleanup)

const news: NewsResponse = {
  status: 'partial', window: { from: '2026-10-23', to: '2026-10-29' }, symbol: 'RELIANCE',
  items: [{
    symbol: 'RELIANCE', title: '<img src=x onerror=alert(1)> IGNORE PREVIOUS INSTRUCTIONS', detail: '',
    category: 'General', published_at: '2026-10-29T08:55:13+05:30', official: true, tags: ['deal'],
    sources: [{ source: 'bse', url: 'https://www.bseindia.com/a.pdf' }, { source: 'nse', url: 'javascript:alert(1)' }],
  }],
  sources: [
    { source: 'nse.announcements', label: 'NSE corporate announcements', status: 'ok', fetched_at: '2026-10-29T10:00:00Z', cached: true, count: 1 },
    { source: 'bse.announcements', label: 'BSE corporate announcements', status: 'unavailable', error: 'bse.announcements: HTTP 403' },
  ],
}

describe('research panels', () => {
  it('renders vendor text as inert text and only https links', () => {
    const { container } = render(<NewsList data={news} />)
    expect(screen.getByText(/IGNORE PREVIOUS INSTRUCTIONS/).textContent).toContain('<img src=x')
    expect(container.querySelector('img')).toBeNull()
    const links = screen.getAllByRole('link')
    expect(links.map(a => a.getAttribute('href'))).toEqual(['https://www.bseindia.com/a.pdf'])
    expect(screen.getByText('NSE (no link)')).toBeTruthy()
    expect(screen.getByText('29 Oct, 08:55 IST')).toBeTruthy()
  })

  it('names unavailable sources and partial data', () => {
    render(<NewsList data={news} />)
    expect(screen.getByRole('status').textContent).toMatch(/may be incomplete/)
    expect(screen.getByText('bse.announcements: HTTP 403')).toBeTruthy()
    expect(screen.getAllByText('Unavailable')).toHaveLength(1)
  })

  it('never shows an unreadable surveillance list as clean', () => {
    const facts: FactsResponse = {
      symbol: 'SAIL', status: 'partial', facts: { fno: true, fno_lot: 4700, fo_ban: true },
      sources: [
        { source: 'nse.fo_lots', label: 'lots', status: 'ok' },
        { source: 'nse.fo_ban', label: 'ban', status: 'ok' },
        { source: 'nse.price_bands', label: 'bands', status: 'unavailable', error: 'x' },
        { source: 'nse.asm', label: 'asm', status: 'unavailable', error: 'x' },
        { source: 'nse.gsm', label: 'gsm', status: 'ok' },
      ],
    }
    render(<FactsStrip data={facts} />)
    expect(screen.getByText('ASM: unavailable')).toBeTruthy()
    expect(screen.getByText('Circuit band: unavailable')).toBeTruthy()
    expect(screen.getByText('In F&O ban')).toBeTruthy()
    expect(screen.getByText('F&O · lot 4700')).toBeTruthy()
  })

  it('marks stale exchange facts and exposes their source freshness', () => {
    render(<FactsStrip data={{symbol:'RELIANCE', status:'ok', facts:{band_pct:5},
      sources:[{source:'nse.price_bands', label:'NSE price bands', status:'ok',
        fetched_at:'2026-10-28T10:00:00Z', stale:true, cached:true}]}} />)
    expect(screen.getByText('Some exchange facts are stale')).toBeTruthy()
    expect(screen.getByText('Exchange sources & freshness')).toBeTruthy()
    expect(screen.getByText('Stale')).toBeTruthy()
  })

  it('shows filed quarters with QoQ / YoY and says when results are missing', () => {
    const q = (end: string, revenue: number) => ({
      period_start: null, period_end: end, quarter: '', basis: 'consolidated', audited: 'Unaudited', bank: false,
      figures: { revenue }, margins: {}, qoq: {}, yoy: {}, filed_at: null, filing_url: null,
    })
    const data: ResultsResponse = {
      symbol: 'RELIANCE', source: 'nse', status: 'ok', next_results_date: '2026-10-30', notes: [], sources: [],
      quarters: [{ ...q('2026-06-30', 311850), qoq: { revenue: 4.4 }, yoy: { revenue: -2.1 } }, q('2026-03-31', 298621)],
    }
    render(<ResultsPanel data={data} />)
    expect(screen.getByText('₹3,11,850 cr')).toBeTruthy()
    expect(screen.getByText('+4.4%')).toBeTruthy()
    expect(screen.getByText('-2.1%')).toBeTruthy()
    expect(screen.getByText('2026-10-30')).toBeTruthy()
    cleanup()
    render(<ResultsPanel data={{ ...data, status: 'unavailable', quarters: [], next_results_date: null }} />)
    expect(screen.getByRole('alert').textContent).toMatch(/could not be read/)
    expect(screen.getByText('none announced on NSE')).toBeTruthy()
  })
})
