// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { PortfolioProvider, usePortfolios, useSelectedPortfolioData } from './Portfolios'
import { TicketCard } from './TicketCard'
import { api } from '#/lib/api'
import type { PaperLedger, Portfolio, Ticket } from '#/lib/desk-types'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))
vi.mock('#/lib/api', () => ({
  api: { desk: { portfolios: vi.fn(), paper: vi.fn(), confirmTicket: vi.fn(), tickets: vi.fn().mockResolvedValue([]) } },
}))
vi.mock('#/lib/messaging-api', () => ({ messagingApi: { propose: vi.fn() } }))

const pf = (id: string, name: string, capital = 20000): Portfolio => ({
  id, name, capital, is_default: id === 'default', trades: 0, capital_locked: id === 'default', capital_history: [],
})
const A = [pf('default', 'Default', 100000), pf('pa', 'A small')]
const B = [pf('default', 'Default', 50000), pf('pb', 'B test')]

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

function Probe() {
  const ctx = usePortfolios()!
  return (
    <div>
      <span data-testid="sel">{ctx.selectedId}</span>
      <span data-testid="names">{ctx.portfolios.map((p) => p.name).join(',')}</span>
      {ctx.portfolios.map((p) => <button key={p.id} onClick={() => ctx.select(p.id)}>{`pick ${p.id}`}</button>)}
    </div>
  )
}

beforeEach(() => localStorage.clear())
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('isolates the list and remembered selection per account and drops a previous account\'s late list', async () => {
  const slowA = deferred<Portfolio[]>()
  vi.mocked(api.desk.portfolios).mockReturnValueOnce(slowA.promise).mockResolvedValueOnce(B)
  const { rerender } = render(<PortfolioProvider key="ua" userId="ua"><Probe /></PortfolioProvider>)
  // Account switch before A's list arrives: the root keys the provider by user id.
  rerender(<PortfolioProvider key="ub" userId="ub"><Probe /></PortfolioProvider>)
  expect(await screen.findByText('pick pb')).toBeTruthy()
  await act(async () => { slowA.resolve(A) })
  expect(screen.getByTestId('names').textContent).toBe('Default,B test')
  fireEvent.click(screen.getByText('pick pb'))
  expect(screen.getByTestId('sel').textContent).toBe('pb')
  expect(localStorage.getItem('drishti-desk-portfolio:ub')).toBe('pb')
  expect(localStorage.getItem('drishti-desk-portfolio:ua')).toBeNull()
})

it('falls back to default when the remembered portfolio is not in this account\'s list', async () => {
  localStorage.setItem('drishti-desk-portfolio:ua', 'pb')
  vi.mocked(api.desk.portfolios).mockResolvedValue(A)
  render(<PortfolioProvider userId="ua"><Probe /></PortfolioProvider>)
  expect(await screen.findByText('pick pa')).toBeTruthy()
  expect(screen.getByTestId('sel').textContent).toBe('default')
})

it('confirms a ticket in its bound portfolio, labels it, and offers no broker path for a named portfolio', async () => {
  localStorage.setItem('drishti-desk-portfolio:ua', 'default')   // the page shows a different portfolio
  vi.mocked(api.desk.portfolios).mockResolvedValue(A)
  vi.mocked(api.desk.confirmTicket).mockResolvedValue({} as Ticket)
  const tk = { id: 't1', status: 'ready', words: 'Buy 12 SBIN', sizing: null, verdict: null, order: { why: '' },
    verdict_warnings: [], portfolio_id: 'pa' } as unknown as Ticket
  render(<PortfolioProvider userId="ua"><TicketCard ticket={tk} publisherKey="k" onChange={vi.fn()} /></PortfolioProvider>)
  expect(await screen.findByText('Portfolio: A small')).toBeTruthy()
  expect(screen.queryByText('Place on Zerodha ↗')).toBeNull()
  expect(screen.queryByText('I placed it at my broker')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Confirm — paper trade in A small' }))
  expect(api.desk.confirmTicket).toHaveBeenCalledWith('t1', 'paper', 'pa')
})

it('a previous portfolio\'s late ledger answer never lands after a switch', async () => {
  vi.mocked(api.desk.portfolios).mockResolvedValue(A)
  const slowDefault = deferred<PaperLedger>()
  const ledger = (id: string, name: string, capital: number): PaperLedger =>
    ({ trades: [], stats: { trades: 0 }, cash: capital, realized: 0, capital, portfolio_id: id, portfolio_name: name })
  vi.mocked(api.desk.paper).mockImplementation((id = 'default') =>
    id === 'default' ? slowDefault.promise : Promise.resolve(ledger('pa', 'A small', 20000)))
  function Ledger() {
    const { data } = useSelectedPortfolioData<PaperLedger>(api.desk.paper)
    return <span data-testid="ledger">{data ? `${data.portfolio_name} ${data.capital}` : 'loading'}</span>
  }
  render(<PortfolioProvider userId="ua"><Probe /><Ledger /></PortfolioProvider>)
  fireEvent.click(await screen.findByText('pick pa'))
  expect((await screen.findByText('A small 20000')).textContent).toBe('A small 20000')
  await act(async () => { slowDefault.resolve(ledger('default', 'Default', 100000)) })
  expect(screen.getByTestId('ledger').textContent).toBe('A small 20000')
  expect(vi.mocked(api.desk.paper).mock.calls.map((c) => c[0])).toEqual(['default', 'pa'])
})
