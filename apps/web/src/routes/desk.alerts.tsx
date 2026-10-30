import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { DeskFooter, DeskNav } from '#/components/desk/DeskNav'
import { TelegramSettings } from '#/components/desk/TelegramSettings'
import { Topbar } from '#/components/shared/Topbar'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '#/components/ui/select'
import { api } from '#/lib/api'
import type { Alert, AlertEvent, AlertType } from '#/lib/desk-types'
import { inr } from '#/lib/desk-format'

export const Route = createFileRoute('/desk/alerts')({
  ssr: false,
  component: AlertsPage,
})

const TYPES: { value: AlertType; label: string }[] = [
  { value: 'above', label: 'Price at or above' },
  { value: 'below', label: 'Price at or below' },
  { value: 'pct_move', label: '% move from previous close' },
  { value: 'time', label: 'Time (IST)' },
  { value: 'opening_bell', label: 'Opening bell (09:15)' },
]

function describe(a: Alert): string {
  switch (a.type) {
    case 'above': case 'target': return `${a.symbol} ≥ ${inr(a.price)}${a.type === 'target' ? ' (target)' : ''}`
    case 'below': case 'stop': return `${a.symbol} ≤ ${inr(a.price)}${a.type === 'stop' ? ' (stop)' : ''}`
    case 'pct_move': return `${a.symbol} moves ${a.direction ?? 'either way'} ${a.pct}%`
    case 'time': return `${a.at} IST${a.label ? ` — ${a.label}` : ''}`
    case 'opening_bell': return 'NSE opening bell'
  }
}

function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [events, setEvents] = useState<AlertEvent[]>([])
  const [type, setType] = useState<AlertType>('above')
  const [symbol, setSymbol] = useState('')
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    api.desk.alerts().then(setAlerts).catch((e) => setError(String(e)))
    api.desk.alertEvents().then(setEvents).catch(() => {})
  }
  useEffect(() => {
    load()
    const t = setInterval(load, 60_000)
    return () => clearInterval(t)
  }, [])

  async function add() {
    setError(null)
    const body: Partial<Alert> = { type }
    if (type !== 'time' && type !== 'opening_bell') body.symbol = symbol
    if (type === 'above' || type === 'below') body.price = Number(value)
    if (type === 'pct_move') body.pct = Number(value)
    if (type === 'time') body.at = value
    try {
      await api.desk.createAlert(body)
      setValue('')
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const needsSymbol = type !== 'time' && type !== 'opening_bell'
  const valueLabel = type === 'pct_move' ? '%' : type === 'time' ? 'HH:MM' : '₹ price'
  return (
    <div className="min-h-screen">
      <Topbar state="idle" />
      <main data-guide="guide-alerts" className="mx-auto w-full max-w-[1400px] space-y-6 px-6 py-4">
        <h1 className="text-2xl font-bold">Alerts</h1>
        <DeskNav />
        <div className="flex flex-wrap items-end gap-2 rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)] p-3">
          <label className="flex flex-col gap-1.5 text-xs text-[var(--text-3)]">Rings when
            <Select value={type} onValueChange={(v) => setType(v as AlertType)}>
              <SelectTrigger className="w-56"><SelectValue /></SelectTrigger>
              <SelectContent>{TYPES.map((t) => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}</SelectContent>
            </Select>
          </label>
          {needsSymbol && (
            <label className="flex flex-col gap-1.5 text-xs text-[var(--text-3)]">Symbol
              <Input className="w-36" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
            </label>
          )}
          {type !== 'opening_bell' && (
            <label className="flex flex-col gap-1.5 text-xs text-[var(--text-3)]">{valueLabel}
              <Input className="w-32" value={value} onChange={(e) => setValue(e.target.value)} />
            </label>
          )}
          <Button onClick={add}>Add alert</Button>
        </div>
        {error && <p className="text-sm text-[var(--err)]">{error}</p>}
        <p className="text-xs text-[var(--text-4)]">
          Checked every minute while NSE is open against the newest delayed 5-minute bar (its high
          and low, so a touch that reversed isn't missed). Each alert rings once. Alerts appear here
          and on the Today tab.
        </p>
        <div className="grid gap-6 lg:grid-cols-2">
          <section className="space-y-2">
            <h2 className="text-lg font-semibold">Rules</h2>
            {alerts.map((a) => (
              <div key={a.id} className="flex items-center justify-between gap-2 rounded-[var(--r-md)] border border-[var(--line-1)] bg-[var(--bg-1)] px-3 py-2">
                <div>
                  <div className="text-sm">{describe(a)}</div>
                  <div className="text-xs text-[var(--text-4)]">
                    {a.date ? `on ${a.date} · ` : ''}{a.rang_at ? `rang ${new Date(a.rang_at).toLocaleTimeString()}` : 'watching'}
                  </div>
                </div>
                <Button size="sm" variant="ghost" onClick={() => api.desk.deleteAlert(a.id).then(load)}>Delete</Button>
              </div>
            ))}
            {!alerts.length && <p className="text-sm text-[var(--text-3)]">No alerts.</p>}
          </section>
          <section className="space-y-2">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">Rang</h2>
              <Button size="sm" variant="ghost" onClick={() => api.desk.markEventsSeen().then(load)}>Mark all seen</Button>
            </div>
            {events.map((e) => (
              <div key={e.id} className={`rounded-[var(--r-md)] border px-3 py-2 text-sm ${e.seen ? 'border-[var(--line-1)]' : 'border-[var(--run)] bg-[var(--run-bg)]'}`}>
                <div className="es-mono text-xs text-[var(--text-4)]">{new Date(e.at).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' })}</div>
                {e.message}
              </div>
            ))}
            {!events.length && <p className="text-sm text-[var(--text-3)]">Nothing has rung yet.</p>}
          </section>
        </div>
        <TelegramSettings />
        <a href="/desk/messages" className="block text-sm text-[var(--accent)] underline">Set up WhatsApp, guided Telegram linking and paper approvals →</a>
        <DeskFooter />
      </main>
    </div>
  )
}
