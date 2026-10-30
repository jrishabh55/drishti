import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useMemo, useState } from 'react'
import { DeskFooter, DeskNav } from '#/components/desk/DeskNav'
import { ReplayOdds } from '#/components/desk/ReplayOdds'
import { SourceBadge } from '#/components/desk/SourceBadge'
import { TicketCard } from '#/components/desk/TicketCard'
import { portfolioName, usePortfolios } from '#/components/desk/Portfolios'
import { Topbar } from '#/components/shared/Topbar'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '#/components/ui/select'
import { api } from '#/lib/api'
import type { DeskConfig, Plan, PlanRequest, Ticket, TimeMachineRequest } from '#/lib/desk-types'
import { inr, targetRuleSwitch } from '#/lib/desk-format'

export const Route = createFileRoute('/desk/plans/$id')({
  ssr: false,
  loader: async ({ params }) => ({ initial: await api.desk.plan(params.id) }),
  component: PlanPage,
})

const STOP_RULES: { value: Plan['stop_rule']; label: string; param?: string }[] = [
  { value: 'or_mid', label: 'Opening-range midpoint' },
  { value: 'or_low', label: 'Opening-range low' },
  { value: 'pct', label: '% under entry', param: '%' },
  { value: 'atr', label: 'ATR multiple', param: '× ATR' },
  { value: 'price', label: 'Price', param: '₹' },
]

function Row({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="grid grid-cols-[110px_1fr] items-baseline gap-3 border-b border-[var(--line-1)] py-2">
      <div className="text-xs text-[var(--text-3)]">{label}</div>
      <div>
        <div className="es-mono text-[15px]" style={{ color: tone ?? 'var(--text-1)' }}>{value}</div>
        {sub && <div className="text-[11px] text-[var(--text-4)]">{sub}</div>}
      </div>
    </div>
  )
}

function PlanPage() {
  const { initial } = Route.useLoaderData()
  const navigate = useNavigate()
  const [plan, setPlan] = useState<Plan>(initial)
  const [edit, setEdit] = useState<PlanRequest>({
    symbol: initial.symbol, interval: initial.interval, date: initial.date, setup: initial.setup, level: initial.level,
    entry: initial.entry, entry_time: initial.entry_time,
    or_high: initial.opening_range?.high ?? null, or_low: initial.opening_range?.low ?? null,
    or_minutes: initial.or_minutes, stop_rule: initial.stop_rule, stop_param: initial.stop_param,
    target_rule: initial.target_rule, target_param: initial.target_param, flat_by: initial.flat_by,
    notes: initial.notes,
  })
  const [error, setError] = useState<string | null>(null)
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [config, setConfig] = useState<DeskConfig | null>(null)
  const [orderType, setOrderType] = useState<'LIMIT' | 'MARKET'>('LIMIT')
  const [busy, setBusy] = useState(false)
  const [alertNote, setAlertNote] = useState<string | null>(null)
  const pf = usePortfolios()
  const portfolioId = pf?.selectedId ?? 'default'

  useEffect(() => {
    api.desk.tickets().then((all) => setTickets(all.filter((t) => t.plan_id === plan.id))).catch(() => {})
    api.desk.config().then(setConfig).catch(() => {})
  }, [plan.id])

  const editable = plan.status === 'draft'
  const tmRequest: TimeMachineRequest | null = useMemo(() => plan.entry_time ? {
    mode: 'intraday', interval: plan.interval, symbol: plan.symbol, date: plan.date, entry: plan.entry, stop: plan.stop,
    target: plan.target, entry_time: plan.entry_time, setup: plan.setup,
    or_minutes: plan.or_minutes, stop_rule: plan.stop_rule, until: plan.flat_by,
  } : null, [plan])

  async function save() {
    setBusy(true); setError(null)
    try {
      setPlan(await api.desk.updatePlan(plan.id, edit))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function makeTicket() {
    setBusy(true); setError(null)
    try {
      // Bound for life to the portfolio selected NOW; a later switch never moves it.
      const t = await api.desk.createTicket(plan.id, orderType, portfolioId)
      setTickets((prev) => [t, ...prev])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    await api.desk.deletePlan(plan.id)
    navigate({ to: '/desk/plans' })
  }

  const rule = STOP_RULES.find((r) => r.value === edit.stop_rule)

  return (
    <div className="min-h-screen">
      <Topbar state="idle" />
      <main data-guide="guide-plans" className="mx-auto w-full max-w-[1400px] space-y-6 px-6 py-4">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold">{plan.symbol}</h1>
          <span className="es-pill">{plan.setup === 'orb' ? `${plan.or_minutes}-min ORB` : 'level'} · {plan.date}{plan.entry_time ? ` ${plan.entry_time}` : ''}</span>
          <span className="es-pill">{plan.status}</span>
          {plan.source && <SourceBadge source={plan.source} />}
        </div>
        <DeskNav />
        {error && <p className="text-sm text-[var(--err)]">{error}</p>}

        <div className="grid gap-6 lg:grid-cols-[380px_1fr]">
          <section className="space-y-3">
            <div className="rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)] p-4">
              <Row label="Target" value={inr(plan.target)} tone="var(--ok)"
                sub={`${plan.target_label} · ${plan.target_r}R`} />
              <Row label="Entry" value={inr(plan.entry)}
                sub={plan.entry_time ? `close of the bar ending ${plan.entry_time}` : 'planned ahead'} />
              <Row label="Stop" value={inr(plan.stop)} tone="var(--err)"
                sub={`${plan.stop_label}${plan.stop_detail ? ` — ${plan.stop_detail}` : ''}`} />
              <Row label="Risk / share" value={inr(plan.risk_per_share)} sub={`tick ₹${plan.tick}`} />
              <Row label="Flat by" value={plan.flat_by} sub="out by then, win or lose" />
              {plan.opening_range && (
                <Row label="Opening range" value={`${inr(plan.opening_range.low)} – ${inr(plan.opening_range.high)}`} />
              )}
              {plan.warnings.map((w) => <p key={w} className="mt-2 text-xs text-[var(--run)]">{w}</p>)}
            </div>

            {editable && (
              <div className="space-y-2 rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)] p-4">
                <div className="es-team-label">Adjust the exit plan</div>
                <label className="block text-xs text-[var(--text-3)]">Stop rule
                  <Select value={edit.stop_rule ?? 'or_mid'}
                    onValueChange={(v) => setEdit({ ...edit, stop_rule: v as Plan['stop_rule'] })}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {STOP_RULES.filter((r) => edit.setup === 'orb' || !r.value.startsWith('or_'))
                        .map((r) => <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </label>
                {rule?.param && (
                  <label className="block text-xs text-[var(--text-3)]">Stop ({rule.param})
                    <Input type="number" step="0.1" value={edit.stop_param ?? ''}
                      onChange={(e) => setEdit({ ...edit, stop_param: e.target.value ? Number(e.target.value) : null })} />
                  </label>
                )}
                <label className="block text-xs text-[var(--text-3)]">Target rule
                  <Select value={edit.target_rule ?? 'r'} onValueChange={v => setEdit({...edit, ...targetRuleSwitch(v as 'r' | 'price', plan)})}>
                    <SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="r">R multiple</SelectItem><SelectItem value="price">Fixed price ₹</SelectItem></SelectContent>
                  </Select>
                </label>
                <label className="block text-xs text-[var(--text-3)]">{edit.target_rule === 'price' ? 'Target price ₹' : 'Target (R multiple)'}
                  <Input type="number" step="0.01" min="0.01" value={edit.target_param ?? 2}
                    onChange={(e) => setEdit({ ...edit, target_param: Number(e.target.value) })} />
                </label>
                <label className="block text-xs text-[var(--text-3)]">Flat by (IST)
                  <Input value={edit.flat_by ?? ''} placeholder="15:15"
                    onChange={(e) => setEdit({ ...edit, flat_by: e.target.value })} />
                </label>
                <div className="flex gap-2">
                  <Button size="sm" onClick={save} disabled={busy}>Recompute</Button>
                  <Button size="sm" variant="ghost" onClick={remove}>Delete plan</Button>
                </div>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="outline" onClick={() => api.desk.alertsFromPlan(plan.id)
                .then((a) => setAlertNote(`${a.length} alerts set: stop, target and flat-by.`))
                .catch((e) => setAlertNote(String(e)))}>
                Add stop / target / flat-by alerts
              </Button>
              {alertNote && <span className="text-xs text-[var(--text-3)]">{alertNote} <Link to="/desk/alerts">View</Link></span>}
            </div>
          </section>

          <section className="space-y-6">
            <div data-guide="guide-time-machine" className="space-y-2 rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)] p-4">
              <h2 className="text-lg font-semibold">Time Machine</h2>
              {tmRequest ? <ReplayOdds request={tmRequest} /> : (
                <p className="text-sm text-[var(--text-3)]">
                  The intraday Time Machine needs the entry bar's time. Plan from a triggered entry
                  (or give an entry time) to replay it.
                </p>
              )}
            </div>

            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-lg font-semibold">Size it &amp; check it</h2>
                <div className="seg">
                  {(['LIMIT', 'MARKET'] as const).map((t) => (
                    <button key={t} className={`seg-btn${orderType === t ? ' active' : ''}`}
                      onClick={() => setOrderType(t)}>{t.toLowerCase()}</button>
                  ))}
                </div>
                <Button data-guide="guide-size-ticket" size="sm" onClick={makeTicket} disabled={busy || pf?.ready === false}>Make a ticket</Button>
                <span className="text-xs text-[var(--text-3)]">for paper portfolio <b>{portfolioName(pf?.portfolios, portfolioId)}</b></span>
              </div>
              <p className="text-xs text-[var(--text-4)]">
                Sizes the trade to your limits, then runs every risk check. Nothing is placed until
                you confirm that exact ticket.
              </p>
              {tickets.map((t) => (
                <TicketCard key={t.id} ticket={t} publisherKey={config?.kite_publisher_api_key ?? null}
                  onChange={(nt) => setTickets((prev) => prev.map((x) => (x.id === nt.id ? nt : x)))} />
              ))}
            </div>
          </section>
        </div>
        <DeskFooter />
      </main>
    </div>
  )
}
