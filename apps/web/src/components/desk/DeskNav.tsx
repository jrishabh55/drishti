import { Link } from '@tanstack/react-router'

const TABS = [
  { to: '/desk', label: 'Today', exact: true },
  { to: '/desk/research', label: 'Research', exact: false },
  { to: '/desk/plans', label: 'Plans', exact: false },
  { to: '/desk/paper', label: 'Paper ledger', exact: false },
  { to: '/desk/performance', label: 'Performance', exact: false },
  { to: '/desk/connection', label: 'Zerodha', exact: false },
  { to: '/desk/strategies', label: 'Strategies & live', exact: false },
  { to: '/desk/alerts', label: 'Alerts', exact: false },
  { to: '/desk/messages', label: 'Messages', exact: false },
  { to: '/desk/settings', label: 'Limits & settings', exact: false },
] as const

export function DeskNav() {
  return (
    <nav className="flex flex-wrap gap-1 border-b border-[var(--line-1)] pb-2">
      {TABS.map((t) => (
        <Link
          key={t.to}
          to={t.to}
          activeOptions={{ exact: t.exact }}
          className="rounded-[var(--r-sm)] px-3 py-1.5 text-sm text-[var(--text-3)] no-underline hover:bg-[var(--bg-2)] hover:text-[var(--text-1)]"
          activeProps={{ className: 'bg-[var(--bg-2)] !text-[var(--text-1)]' }}
        >
          {t.label}
        </Link>
      ))}
    </nav>
  )
}

export function DeskFooter() {
  return (
    <footer className="mt-8 border-t pt-4 text-xs text-muted-foreground">
      The Trade Desk is tooling, not advice: setups, replays and rules checks are arithmetic over
      delayed public bars or your connected broker feed. Most people who day trade lose money. Simulated odds are not
      predictions; a stop is a plan, not a guarantee. Paper trades stay in the app.
      Connected Zerodha orders require live trading to be enabled, daily arming and your explicit confirmation.
      Logic adapted from{' '}
      <a href="https://github.com/Jakeschincariol/day-trader-skill" target="_blank" rel="noreferrer">
        day-trader-skill
      </a>{' '}
      (MIT).
    </footer>
  )
}
