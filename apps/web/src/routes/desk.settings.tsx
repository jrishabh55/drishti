import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { DeskFooter, DeskNav } from '#/components/desk/DeskNav'
import { PortfolioManager, usePortfolios } from '#/components/desk/Portfolios'
import { Topbar } from '#/components/shared/Topbar'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { Switch } from '#/components/ui/switch'
import { ApiError, api } from '#/lib/api'
import type { DeskProfile } from '#/lib/desk-types'

export const Route = createFileRoute('/desk/settings')({
  ssr: false,
  component: SettingsPage,
})

type NumKey = { [K in keyof DeskProfile]: DeskProfile[K] extends number ? K : never }[keyof DeskProfile]
type StrKey = 'no_entries_before' | 'no_entries_after' | 'flat_by' | 'broker_square_off'

const GROUPS: { title: string; hint: string; nums: [NumKey, string][]; times?: [StrKey, string][] }[] = [
  {
    title: 'Money', hint: 'Sizing never uses more than these.',
    nums: [['capital', 'Default portfolio capital ₹'], ['cash_buffer_pct', 'Cash buffer %'],
      ['max_position_pct', 'Max position % of capital'], ['min_order_inr', 'Min order ₹'],
      ['max_order_inr', 'Max order ₹']],
  },
  {
    title: 'Losses', hint: 'The risk check blocks any entry that would break these.',
    nums: [['max_loss_per_trade_inr', 'Max loss per trade ₹'], ['max_daily_loss_inr', 'Max daily loss ₹'],
      ['max_trades_per_day', 'Max entries per day'], ['max_orders_per_day', 'Max orders per day'],
      ['min_reward_risk', 'Min reward:risk (after charges)']],
  },
  {
    title: 'Clock (IST)', hint: 'Flat-by must be before your broker auto-squares off intraday positions.',
    nums: [['or_minutes', 'Opening range minutes']],
    times: [['no_entries_before', 'No entries before'], ['no_entries_after', 'No entries after'],
      ['flat_by', 'Flat by'], ['broker_square_off', 'Broker MIS square-off']],
  },
  {
    title: 'Broker plan (charges estimate)', hint: 'Statutory charges (STT, stamp duty, GST…) are added automatically.',
    nums: [['brokerage_intraday_flat', 'Intraday ₹ per order (cap)'],
      ['brokerage_intraday_pct', 'Intraday % per order'], ['brokerage_delivery_flat', 'Delivery ₹ per order'],
      ['dp_charge_per_sell', 'DP charge per delivery sell ₹'], ['slippage_ticks', 'Paper slippage (ticks)']],
  },
  {
    title: 'ORB universe', hint: 'Which stocks the opening-range scan looks at.',
    nums: [['min_price', 'Min price ₹'], ['min_avg_value_cr', 'Min 20-day avg traded value ₹ cr']],
  },
]

function SettingsPage() {
  const [p, setP] = useState<DeskProfile | null>(null)
  const [blocked, setBlocked] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const portfolios = usePortfolios()

  useEffect(() => {
    api.desk.profile().then((x) => { setP(x); setBlocked(x.blocked.join(', ')) }).catch((e) => setError(String(e)))
  }, [])

  async function save() {
    if (!p) return
    setError(null); setMsg(null)
    try {
      const saved = await api.desk.saveProfile({
        ...p, blocked: blocked.split(',').map((s) => s.trim()).filter(Boolean),
      })
      setP(saved); setBlocked(saved.blocked.join(', '))
      setMsg('Saved.')
      portfolios?.reload()   // the default portfolio's capital is the profile's
    } catch (e) {
      if (e instanceof ApiError && Array.isArray(e.detail)) {
        setError((e.detail as { loc: string[]; msg: string }[])
          .map((d) => `${d.loc.slice(1).join('.') || 'profile'}: ${d.msg}`).join('; '))
      } else {
        setError(e instanceof Error ? e.message : String(e))
      }
    }
  }

  return (
    <div className="min-h-screen">
      <Topbar state="idle" />
      <main data-guide="guide-limits" className="mx-auto w-full max-w-[1100px] space-y-6 px-6 py-4">
        <h1 className="text-2xl font-bold">Limits &amp; settings</h1>
        <DeskNav />
        <a href="/desk/messages" className="block text-sm text-[var(--accent)] underline">Manage WhatsApp and Telegram connections →</a>
        {p && (
          <div className="grid gap-4 md:grid-cols-2">
            {GROUPS.map((g) => (
              <fieldset key={g.title} className="space-y-2 rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)] p-4">
                <legend className="px-1 text-sm font-semibold">{g.title}</legend>
                <p className="text-xs text-[var(--text-4)]">{g.hint}</p>
                {g.nums.map(([k, label]) => (
                  <label key={k} className="flex items-center justify-between gap-3 text-sm">
                    <span className="text-[var(--text-2)]">{label}</span>
                    <Input className="es-mono w-36" type="number" value={String(p[k])}
                      onChange={(e) => setP({ ...p, [k]: Number(e.target.value) })} />
                  </label>
                ))}
                {g.times?.map(([k, label]) => (
                  <label key={k} className="flex items-center justify-between gap-3 text-sm">
                    <span className="text-[var(--text-2)]">{label}</span>
                    <Input className="es-mono w-36" value={p[k] ?? ''} placeholder="off"
                      onChange={(e) => setP({ ...p, [k]: e.target.value || null })} />
                  </label>
                ))}
              </fieldset>
            ))}
            <fieldset className="space-y-2 rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)] p-4">
              <legend className="px-1 text-sm font-semibold">Rules</legend>
              <label className="flex items-center justify-between text-sm">
                <span className="text-[var(--text-2)]">Every entry needs a stop</span>
                <Switch checked={p.require_stop} onCheckedChange={(v) => setP({ ...p, require_stop: v })} />
              </label>
              <label className="flex items-center justify-between gap-3 text-sm">
                <span className="text-[var(--text-2)]">Product</span>
                <div className="seg">
                  {(['MIS', 'CNC'] as const).map((x) => (
                    <button key={x} className={`seg-btn${p.product === x ? ' active' : ''}`}
                      onClick={() => setP({ ...p, product: x })}>{x}</button>
                  ))}
                </div>
              </label>
              <label className="flex items-center justify-between gap-3 text-sm">
                <span className="text-[var(--text-2)]">ORB universe</span>
                <div className="seg">
                  {(['fno', 'nifty500', 'all'] as const).map((x) => (
                    <button key={x} className={`seg-btn${p.universe === x ? ' active' : ''}`}
                      onClick={() => setP({ ...p, universe: x })}>{x}</button>
                  ))}
                </div>
              </label>
              <label className="block text-sm text-[var(--text-2)]">Blocked symbols (comma separated)
                <Input value={blocked} onChange={(e) => setBlocked(e.target.value.toUpperCase())} />
              </label>
            </fieldset>
          </div>
        )}
        <div className="flex items-center gap-3">
          <Button onClick={save} disabled={!p}>Save</Button>
          {msg && <span className="text-sm text-[var(--ok)]">{msg}</span>}
          {error && <span className="text-sm text-[var(--err)]">{error}</span>}
        </div>
        <PortfolioManager />
        <DeskFooter />
      </main>
    </div>
  )
}
