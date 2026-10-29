import { useEffect, useState } from 'react'
import { Switch } from '#/components/ui/switch'
import { evaluationApi, type PortfolioSnapshot } from '#/lib/evaluation-api'
import { inr } from '#/lib/desk-format'

/* Opt-in for "give the agents my paper book". The server snapshots the book at
   submit time and freezes it with the run; this only previews what it holds.
   Wire: <PaperPortfolioOptIn checked={x} onCheckedChange={setX} /> and send
   `use_paper_portfolio: x` in the run request. */
export function PaperPortfolioOptIn({ checked, onCheckedChange }: {
  checked: boolean
  onCheckedChange: (value: boolean) => void
}) {
  const [book, setBook] = useState<PortfolioSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!checked || book) return
    evaluationApi.portfolioSnapshot().then(setBook).catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [checked, book])

  return (
    <div className="space-y-1.5">
      <label className="flex items-center gap-2 text-sm">
        <Switch checked={checked} onCheckedChange={onCheckedChange} aria-label="Use my paper portfolio" />
        Use my paper portfolio
      </label>
      <p className="text-xs text-[var(--text-4)]">
        The decision agents see your paper cash and open positions as of submit. Pending orders
        reserve cash but are not holdings. The book is frozen with the run.
      </p>
      {checked && error && <p className="text-xs text-[var(--err)]">Couldn't read your paper book: {error}</p>}
      {checked && book && (
        <div className="es-mono text-xs text-[var(--text-3)]" data-testid="paper-book">
          Cash {inr(book.cash, true)}
          {book.positions.length
            ? ` · ${book.positions.map((p) => `${p.ticker} ${p.quantity}${p.average_price != null ? ` @ ${inr(p.average_price)}` : ''}`).join(', ')}`
            : ' · no open positions'}
        </div>
      )}
    </div>
  )
}
