import { useEffect, useState } from 'react'
import { Button } from '#/components/ui/button'
import { Command, CommandInput, CommandItem, CommandList } from '#/components/ui/command'
import { api } from '#/lib/api'
import type { TickerHit } from '#/lib/types'

type SearchState = 'idle' | 'loading' | 'ready' | 'error'

export function DeskSymbolSearch({ onAdd, actionLabel = 'Add', busyLabel = 'Adding…' }: {
  onAdd: (symbol: string) => Promise<void>
  actionLabel?: string
  busyLabel?: string
}) {
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [hits, setHits] = useState<TickerHit[]>([])
  const [state, setState] = useState<SearchState>('idle')
  const [open, setOpen] = useState(false)
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const q = query.trim().replace(/^NSE:/i, '')
    if (!open || selected || q.length < 2) return
    let stale = false
    const timer = setTimeout(() => {
      api.searchTickers(q).then(({ results }) => {
        if (stale) return
        setHits(results.filter(hit => hit.type === 'EQUITY' && hit.symbol.endsWith('.NS')))
        setState('ready')
      }).catch(() => {
        if (!stale) { setHits([]); setState('error') }
      })
    }, 250)
    return () => { stale = true; clearTimeout(timer) }
  }, [query, open, selected])

  function change(value: string) {
    setQuery(value); setSelected(null); setHits([]); setError(null)
    setOpen(true); setState(value.trim().replace(/^NSE:/i, '').length >= 2 ? 'loading' : 'idle')
  }

  function pick(symbol: string) {
    const bare = symbol.replace(/\.NS$/i, '')
    setSelected(bare); setQuery(bare); setOpen(false); setError(null)
  }

  async function add() {
    if (!selected || adding) return
    setAdding(true); setError(null)
    try {
      await onAdd(selected)
      setQuery(''); setSelected(null); setHits([]); setOpen(false); setState('idle')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add symbol')
    } finally {
      setAdding(false)
    }
  }

  const exact = query.trim().toUpperCase().replace(/^NSE:/, '').replace(/\.NS$/, '')
  const manual = /^[A-Z0-9&-]{1,20}$/.test(exact) && !hits.length && (state === 'ready' || state === 'error')
  const showResults = open && !selected && query.trim().replace(/^NSE:/i, '').length >= 2

  return (
    <div className="flex w-full flex-wrap items-start gap-2 sm:w-auto">
      <div className="relative w-full sm:w-80" onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false)
      }}>
        <Command shouldFilter={false} label="Search NSE stocks" className="overflow-visible bg-transparent p-0 [&_[data-slot=command-input-wrapper]]:p-0">
          <CommandInput value={query} onValueChange={change} disabled={adding}
            aria-label="Search company or NSE symbol" placeholder="Search company or NSE symbol"
            onFocus={() => { if (!selected && query.trim().replace(/^NSE:/i, '').length >= 2) { setOpen(true); setState('loading') } }}
            onKeyDown={(event) => { if (event.key === 'Escape') setOpen(false) }} />
          <CommandList className={showResults ? 'absolute left-0 right-0 top-full z-30 mt-1 rounded-lg border border-[var(--line-1)] bg-[var(--bg-1)] p-1 shadow-lg' : 'hidden'}>
            {showResults && <>
              {state === 'loading' && <p role="status" className="p-2 text-xs text-[var(--text-3)]">Searching NSE stocks…</p>}
              {state === 'error' && <p role="status" className="p-2 text-xs text-[var(--text-3)]">Search is unavailable. You can enter an exact NSE symbol.</p>}
              {state === 'ready' && !hits.length && <p role="status" className="p-2 text-xs text-[var(--text-3)]">No NSE stocks found. Try another company name or an exact symbol.</p>}
              {hits.map(hit => <CommandItem key={hit.symbol} value={hit.symbol}
                onMouseDown={(event) => event.preventDefault()} onSelect={() => pick(hit.symbol)}>
                <span className="es-mono font-medium">{hit.symbol.replace(/\.NS$/, '')}</span>
                <span className="min-w-0 flex-1 truncate text-xs text-[var(--text-3)]">{hit.name}</span>
                <span className="text-xs text-[var(--text-4)]">NSE</span>
              </CommandItem>)}
              {manual && <CommandItem value={`exact:${exact}`} onMouseDown={(event) => event.preventDefault()}
                onSelect={() => pick(exact)}>Use NSE symbol {exact}</CommandItem>}
            </>}
          </CommandList>
        </Command>
        {error && <p role="alert" className="mt-1 text-xs text-[var(--err)]">{error}</p>}
      </div>
      <Button variant="outline" onClick={add} disabled={!selected || adding}>{adding ? busyLabel : actionLabel}</Button>
      {selected && <a className="es-btn no-underline" href={`/analyse?ticker=${encodeURIComponent(`${selected}.NS`)}`}>Analyse</a>}
    </div>
  )
}
