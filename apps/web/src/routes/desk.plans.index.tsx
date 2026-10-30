import { createFileRoute, Link } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { DeskFooter, DeskNav } from '#/components/desk/DeskNav'
import { Topbar } from '#/components/shared/Topbar'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '#/components/ui/table'
import { api } from '#/lib/api'
import type { Plan } from '#/lib/desk-types'
import { inr } from '#/lib/desk-format'
import { useNavigate } from '@tanstack/react-router'

export const Route = createFileRoute('/desk/plans/')({
  ssr: false,
  component: PlansPage,
})

function PlansPage() {
  const navigate = useNavigate()
  const [plans, setPlans] = useState<Plan[]>([])
  const [error, setError] = useState<string | null>(null)
  const [sym, setSym] = useState('')
  const [entry, setEntry] = useState('')
  const [stopPct, setStopPct] = useState('1')

  useEffect(() => { api.desk.plans().then(setPlans).catch((e) => setError(String(e))) }, [])

  async function create() {
    setError(null)
    try {
      const p = await api.desk.createPlan(entry
        ? { symbol: sym, entry: Number(entry), setup: 'level', stop_rule: 'pct', stop_param: Number(stopPct) }
        : { symbol: sym })
      navigate({ to: '/desk/plans/$id', params: { id: p.id } })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="min-h-screen">
      <Topbar state="idle" />
      <main data-guide="guide-plans" className="mx-auto w-full max-w-[1400px] space-y-6 px-6 py-4">
        <h1 className="text-2xl font-bold">Plans</h1>
        <DeskNav />
        <div className="flex flex-wrap items-end gap-2 rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)] p-3">
          <label className="flex flex-col gap-1.5 text-xs text-[var(--text-3)]">Symbol
            <Input className="w-36" value={sym} onChange={(e) => setSym(e.target.value.toUpperCase())} />
          </label>
          <label className="flex flex-col gap-1.5 text-xs text-[var(--text-3)]">Entry ₹ (blank = from the entry timer)
            <Input className="w-40" type="number" value={entry} onChange={(e) => setEntry(e.target.value)} />
          </label>
          {entry && (
            <label className="flex flex-col gap-1.5 text-xs text-[var(--text-3)]">Stop % under entry
              <Input className="w-28" type="number" step="0.1" value={stopPct}
                onChange={(e) => setStopPct(e.target.value)} />
            </label>
          )}
          <Button onClick={create} disabled={!sym}>New plan</Button>
        </div>
        {error && <p className="text-sm text-[var(--err)]">{error}</p>}
        <div className="overflow-auto rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)]">
          <Table>
            <TableHeader>
              <TableRow>
                {['Date', 'Symbol', 'Setup', 'Entry', 'Stop', 'Target', 'R', 'Status', ''].map((h) => (
                  <TableHead key={h} className="es-team-label">{h}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {plans.map((p) => (
                <TableRow key={p.id}>
                  <TableCell className="es-mono">{p.date} {p.entry_time ?? ''}</TableCell>
                  <TableCell className="font-medium">{p.symbol}</TableCell>
                  <TableCell>{p.setup}</TableCell>
                  <TableCell className="es-mono">{inr(p.entry)}</TableCell>
                  <TableCell className="es-mono">{inr(p.stop)}</TableCell>
                  <TableCell className="es-mono">{inr(p.target)}</TableCell>
                  <TableCell className="es-mono">{p.target_r}</TableCell>
                  <TableCell><span className="es-pill">{p.status}</span></TableCell>
                  <TableCell className="text-right">
                    <Link to="/desk/plans/$id" params={{ id: p.id }}>Open →</Link>
                  </TableCell>
                </TableRow>
              ))}
              {!plans.length && (
                <TableRow><TableCell colSpan={9} className="text-sm text-[var(--text-3)]">
                  No plans yet — plan a breakout from the Today tab.
                </TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </div>
        <DeskFooter />
      </main>
    </div>
  )
}
