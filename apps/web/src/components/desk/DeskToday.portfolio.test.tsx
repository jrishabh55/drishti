// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router'
import { PortfolioProvider } from './Portfolios'
import { Route as DeskRoute } from '#/routes/desk.index'
import { api } from '#/lib/api'

vi.mock('#/lib/api', () => ({ api: { desk: {
  portfolios: vi.fn(), paper: vi.fn(), orb: vi.fn(() => new Promise(() => {})),
  alertEvents: vi.fn().mockResolvedValue([]), watchlist: vi.fn().mockResolvedValue([]),
} }, jsonFetch: vi.fn().mockResolvedValue({}) }))
vi.mock('#/components/shared/Topbar', () => ({ Topbar: () => null }))
vi.mock('#/components/desk/DeskNav', () => ({ DeskNav: () => null, DeskFooter: () => null }))
vi.mock('#/components/desk/DeskSymbolSearch', () => ({ DeskSymbolSearch: () => null }))
afterEach(() => { cleanup(); localStorage.clear(); vi.clearAllMocks() })

it('Today loads and labels the selected paper portfolio rather than Default', async () => {
  localStorage.setItem('drishti-desk-portfolio:ua', 'pa')
  vi.mocked(api.desk.portfolios).mockResolvedValue([
    { id: 'default', name: 'Default', capital: 100000, is_default: true, trades: 0, capital_locked: true, capital_history: [] },
    { id: 'pa', name: 'A small', capital: 20000, is_default: false, trades: 1, capital_locked: true, capital_history: [] },
  ])
  vi.mocked(api.desk.paper).mockResolvedValue({ portfolio_id: 'pa', portfolio_name: 'A small', trades: [],
    stats: { trades: 1, win_rate: 1, avg_r_net: 1, pnl_net: 100, charges: 10 }, capital: 20000, cash: 20100, realized: 100 })
  const root = createRootRoute({ component: () => <PortfolioProvider userId="ua"><Outlet /></PortfolioProvider> })
  const route = createRoute({ getParentRoute: () => root, path: '/desk', component: DeskRoute.options.component })
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({ initialEntries: ['/desk'] }) })
  render(<RouterProvider router={router} />)
  expect(await screen.findByText(/Paper record \(A small\)/)).toBeTruthy()
  expect(api.desk.paper).toHaveBeenCalledWith('pa')
  expect(api.desk.paper).not.toHaveBeenCalledWith('default')
})
