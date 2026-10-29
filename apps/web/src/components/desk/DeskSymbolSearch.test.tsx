// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { DeskSymbolSearch } from './DeskSymbolSearch'
import { api } from '#/lib/api'

vi.mock('#/lib/api', () => ({ api: { searchTickers: vi.fn() } }))
const hits = [
  { symbol: 'RELIANCE.NS', name: 'Reliance Industries Limited', exchange: 'NSE', type: 'EQUITY' },
  { symbol: 'RELIANCE.BO', name: 'Reliance Industries Limited', exchange: 'BSE', type: 'EQUITY' },
]
beforeEach(() => {
  vi.mocked(api.searchTickers).mockReset().mockResolvedValue({ results: hits })
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function search(text = 'Reliance') {
  fireEvent.change(screen.getByRole('combobox'), { target: { value: text } })
}

describe('Desk symbol search', () => {
  it('searches company names, selects NSE listings and prefills analysis without running it', async () => {
    const add = vi.fn().mockResolvedValue(undefined)
    render(<DeskSymbolSearch onAdd={add} />)
    search()
    const result = await screen.findByRole('option', { name: /Reliance Industries/ })
    expect(screen.getAllByRole('option')).toHaveLength(1)
    expect(vi.mocked(api.searchTickers).mock.calls[0][0]).toBe('Reliance')
    expect(screen.getByRole('button', { name: 'Add' }).hasAttribute('disabled')).toBe(true)
    fireEvent.click(result)
    expect(screen.getByRole('link', { name: 'Analyse' }).getAttribute('href')).toBe('/analyse?ticker=RELIANCE.NS')
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(add).toHaveBeenCalledWith('RELIANCE'))
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLInputElement).value).toBe(''))
  })

  it('supports keyboard selection', async () => {
    render(<DeskSymbolSearch onAdd={vi.fn()} />)
    search()
    await screen.findByRole('option')
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'ArrowDown' })
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' })
    expect(screen.getByRole('link', { name: 'Analyse' }).getAttribute('href')).toContain('RELIANCE.NS')
  })

  it('keeps an exact-symbol fallback when search fails, and preserves selection on add failure', async () => {
    vi.mocked(api.searchTickers).mockRejectedValue(new Error('offline'))
    render(<DeskSymbolSearch onAdd={vi.fn().mockRejectedValue(new Error('Could not add symbol'))} />)
    search('M&M')
    expect(await screen.findByText(/Search is unavailable/)).toBeTruthy()
    fireEvent.click(screen.getByRole('option', { name: /Use NSE symbol M&M/ }))
    expect(screen.getByRole('link', { name: 'Analyse' }).getAttribute('href')).toBe('/analyse?ticker=M%26M.NS')
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByText('Could not add symbol')).toBeTruthy()
    expect((screen.getByRole('combobox') as HTMLInputElement).value).toBe('M&M')
  })

  it('does not let a slower old response replace the current search', async () => {
    let old!: (value: { results: typeof hits }) => void
    vi.mocked(api.searchTickers).mockImplementationOnce(() => new Promise(resolve => { old = resolve }))
      .mockResolvedValue({ results: [{ ...hits[0], symbol: 'TCS.NS', name: 'Tata Consultancy Services' }] })
    render(<DeskSymbolSearch onAdd={vi.fn()} />)
    search('Reliance')
    await waitFor(() => expect(api.searchTickers).toHaveBeenCalledTimes(1))
    search('Tata')
    expect(await screen.findByRole('option', { name: /Tata Consultancy/ })).toBeTruthy()
    old({ results: hits })
    await waitFor(() => expect(screen.queryByRole('option', { name: /Reliance Industries/ })).toBeNull())
  })
})

it('does not turn an unmatched company name into a watchlist symbol', async () => {
  vi.mocked(api.searchTickers).mockResolvedValue({ results: [] })
  render(<DeskSymbolSearch onAdd={vi.fn()} />)
  search('Unknown Company')
  expect(await screen.findByText(/No NSE stocks found/)).toBeTruthy()
  expect(screen.queryByRole('option')).toBeNull()
  expect(screen.queryByRole('link', { name: 'Analyse' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Add' }).hasAttribute('disabled')).toBe(true)
})

it('waits for a symbol after an NSE prefix and searches without the prefix', async () => {
  render(<DeskSymbolSearch onAdd={vi.fn()} />)
  search('NSE:')
  expect(screen.queryByText('Searching NSE stocks…')).toBeNull()
  search('NSE:RELIANCE')
  await screen.findByRole('option', { name: /Reliance Industries/ })
  expect(vi.mocked(api.searchTickers).mock.calls[0][0]).toBe('RELIANCE')
})
