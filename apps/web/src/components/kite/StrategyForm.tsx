import { useState } from 'react'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '#/components/ui/select'
import type { Portfolio } from '#/lib/desk-types'
import type { ExitPolicy, Strategy, StrategyInput, StrategyMode } from '#/lib/kite-api'
import type { ScannerSummary } from '#/lib/scanner-types'

export const BLANK_STRATEGY: StrategyInput = {
  name: 'ORB 15m', mode: 'off', symbols: [], setup: 'orb', level: null, or_minutes: 15,
  stop_rule: 'or_mid', stop_param: null, target_r: 2, target_rule: 'r', target_param: null, order_type: 'LIMIT',
  max_signals_per_day: 1, confirm_ttl_s: 120, portfolio_id: 'default', interval: '5m',
  scanner_id: null, min_volume_x: null, exit_policy: null,
}

/** Editable fields only; legacy strategies get the new fields' defaults. */
export function toInput(s: Strategy): StrategyInput {
  const {
    id: _id, signals: _sig, version: _v, paused_reason: _p, paused_from: _pa, auto_opt_in: _a, last_scan: _l, ...rest
  } = s
  return { ...BLANK_STRATEGY, ...rest }
}

export const MODES: { value: StrategyMode; label: string; hint: string }[] = [
  { value: 'off', label: 'Off', hint: 'does nothing' },
  { value: 'paper', label: 'Paper', hint: 'signals go to the selected paper portfolio' },
  { value: 'confirm', label: 'Confirm', hint: 'signals wait for your typed confirmation to send live' },
  { value: 'auto', label: 'Auto', hint: 'sends live only after a separate typed opt-in, with every live gate still applied' },
]

const LIVE: StrategyMode[] = ['confirm', 'auto']
const NONE = '__none__'

/** Drops blank inputs; an empty policy means the legacy fixed stop/target. */
export function cleanPolicy(p: ExitPolicy | null): ExitPolicy | null {
  const out = Object.fromEntries(Object.entries(p ?? {}).filter(([, v]) => v != null && Number.isFinite(v)))
  return Object.keys(out).length ? out as ExitPolicy : null
}

export function Seg<T extends string>({ value, options, onChange }: {
  value: T; options: readonly { value: T; label: string }[]; onChange: (v: T) => void
}) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button key={o.value} type="button" className={`seg-btn${value === o.value ? ' active' : ''}`}
          onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  )
}

/** ``draft``: a research/scanner draft under review — always saved Off; activation happens later. */
export function StrategyForm({ initial, onSave, onCancel, busy, portfolios = [], scanners = [], draft = false }: {
  initial: StrategyInput; onSave: (s: StrategyInput) => void; onCancel: () => void; busy: boolean
  portfolios?: Pick<Portfolio, 'id' | 'name'>[]; scanners?: Pick<ScannerSummary, 'id' | 'name' | 'prebuilt'>[]
  draft?: boolean
}) {
  const [s, setS] = useState(draft ? { ...initial, mode: 'off' as const } : initial)
  const [syms, setSyms] = useState(initial.symbols.join(', '))
  const num = (v: string) => (v === '' ? null : Number(v))
  const row = 'flex items-center justify-between gap-3 text-sm'
  const live = LIVE.includes(s.mode)
  const pol = s.exit_policy ?? {}
  const setPol = (k: keyof ExitPolicy, v: string) => setS({ ...s, exit_policy: { ...pol, [k]: num(v) ?? undefined } })
  const pfs = portfolios.length ? portfolios : [{ id: 'default', name: 'Default' }]
  return (
    <form className="space-y-2" onSubmit={(e) => {
      e.preventDefault()
      onSave({ ...s, ...(draft ? { mode: 'off' as const } : {}), symbols: syms.split(',').map((x) => x.trim()).filter(Boolean), exit_policy: cleanPolicy(s.exit_policy) })
    }}>
      <label className={row}><span>Name</span>
        <Input className="w-64" value={s.name} onChange={(e) => setS({ ...s, name: e.target.value })} /></label>
      <label className={row}><span>Symbols (≤ 20 with scanner matches)</span>
        <Input className="w-64 es-mono" value={syms} onChange={(e) => setSyms(e.target.value.toUpperCase())} /></label>
      <div className={row}><span>Scanner candidates</span>
        <Select value={s.scanner_id ?? NONE} onValueChange={(v) => setS({ ...s, scanner_id: v === NONE ? null : v })}>
          <SelectTrigger size="sm" aria-label="Scanner" className="w-64"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={NONE}>None — symbols only</SelectItem>
            {scanners.map((x) => <SelectItem key={x.id} value={x.id}>{x.name}{x.prebuilt ? ' (prebuilt)' : ''}</SelectItem>)}
          </SelectContent>
        </Select></div>
      {draft ? (
        <div className={row}><span>Mode</span>
          <span className="text-xs text-[var(--text-3)]">Off — saved as a draft; turn on Paper after review</span></div>
      ) : (
        <>
          <div className={row}><span>Mode</span>
            <Seg value={s.mode} options={MODES} onChange={(mode) => setS({
              ...s, mode, ...(LIVE.includes(mode) ? { portfolio_id: 'default', exit_policy: null } : {}),
            })} /></div>
          <p className="text-xs text-[var(--text-4)]">{MODES.find((m) => m.value === s.mode)?.hint}</p>
        </>
      )}
      <div className={row}><span>Portfolio</span>
        <Select value={s.portfolio_id} disabled={live} onValueChange={(portfolio_id) => setS({ ...s, portfolio_id })}>
          <SelectTrigger size="sm" aria-label="Portfolio" className="w-64"><SelectValue /></SelectTrigger>
          <SelectContent>
            {pfs.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
          </SelectContent>
        </Select></div>
      {live && <p className="text-xs text-[var(--text-4)]">Confirm and Auto trade the real account, so they use Default.</p>}
      <div className={row}><span>Bars</span>
        <Seg value={s.interval} options={[{ value: '5m', label: '5 min' }, { value: '1m', label: '1 min' }] as const}
          onChange={(interval) => setS({ ...s, interval })} /></div>
      <div className={row}><span>Setup</span>
        <Seg value={s.setup} options={[{ value: 'orb', label: 'Opening range' }, { value: 'level', label: 'Level' }] as const}
          onChange={(setup) => setS({ ...s, setup })} /></div>
      {s.setup === 'level' ? (
        <label className={row}><span>Close above ₹</span>
          <Input className="w-36 es-mono" type="number" value={s.level ?? ''}
            onChange={(e) => setS({ ...s, level: num(e.target.value) })} /></label>
      ) : (
        <label className={row}><span>Range minutes</span>
          <Input className="w-36 es-mono" type="number" value={s.or_minutes}
            onChange={(e) => setS({ ...s, or_minutes: Number(e.target.value) })} /></label>
      )}
      <div className={row}><span>Stop</span>
        <Seg value={s.stop_rule} options={[{ value: 'or_mid', label: 'Range mid' }, { value: 'or_low', label: 'Range low' },
          { value: 'pct', label: '%' }, { value: 'atr', label: 'ATR' }, { value: 'price', label: 'Price' }] as const}
          onChange={(stop_rule) => setS({ ...s, stop_rule })} /></div>
      {(s.stop_rule === 'pct' || s.stop_rule === 'atr' || s.stop_rule === 'price') && (
        <label className={row}><span>{s.stop_rule === 'pct' ? 'Stop %' : s.stop_rule === 'atr' ? 'ATR multiple' : 'Stop ₹'}</span>
          <Input className="w-36 es-mono" type="number" value={s.stop_param ?? ''}
            onChange={(e) => setS({ ...s, stop_param: num(e.target.value) })} /></label>
      )}
      <label className={row}><span>Min trigger volume (× range avg, optional)</span>
        <Input className="w-36 es-mono" type="number" step="0.1" value={s.min_volume_x ?? ''}
          onChange={(e) => setS({ ...s, min_volume_x: num(e.target.value) })} /></label>
      <div className={row}><span>Target</span>
        <Seg value={s.target_rule ?? 'r'} options={[{ value: 'r', label: 'R multiple' }, { value: 'price', label: 'Fixed price' }] as const}
          onChange={(target_rule) => setS({ ...s, target_rule })} /></div>
      {s.target_rule === 'price' ? (
        <label className={row}><span>Target ₹</span>
          <Input className="w-36 es-mono" type="number" step="0.05" value={s.target_param ?? ''}
            onChange={(e) => setS({ ...s, target_param: num(e.target.value) })} /></label>
      ) : (
        <label className={row}><span>Target (R)</span>
          <Input className="w-36 es-mono" type="number" step="0.1" value={s.target_r}
            onChange={(e) => setS({ ...s, target_r: Number(e.target.value) })} /></label>
      )}
      <div className={row}><span>Entry order</span>
        <Seg value={s.order_type} options={[{ value: 'LIMIT', label: 'Limit' }, { value: 'MARKET', label: 'Market' }] as const}
          onChange={(order_type) => setS({ ...s, order_type })} /></div>
      <label className={row}><span>Max signals per day</span>
        <Input className="w-36 es-mono" type="number" value={s.max_signals_per_day}
          onChange={(e) => setS({ ...s, max_signals_per_day: Number(e.target.value) })} /></label>
      <label className={row}><span>Confirm window (seconds)</span>
        <Input className="w-36 es-mono" type="number" value={s.confirm_ttl_s}
          onChange={(e) => setS({ ...s, confirm_ttl_s: Number(e.target.value) })} /></label>
      {!live && (
        <fieldset className="space-y-2 rounded-[var(--r-sm)] border border-[var(--line-1)] p-2">
          <legend className="px-1 text-xs text-[var(--text-3)]">Advanced exits (paper &amp; backtests; blank = fixed stop/target)</legend>
          {([
            ['breakeven_after_r', 'Stop to breakeven after (R)'],
            ['trail_after_r', 'Trail after (R)'],
            ['trail_distance_r', 'Trail distance (R)'],
            ['scale_out_at_r', 'Scale out at (R)'],
            ['scale_out_pct', 'Scale out % (0–100)'],
          ] as const).map(([k, label]) => (
            <label key={k} className={row}><span>{label}</span>
              <Input className="w-36 es-mono" type="number" step="0.1" aria-label={label} value={pol[k] ?? ''}
                onChange={(e) => setPol(k, e.target.value)} /></label>
          ))}
        </fieldset>
      )}
      <p className="text-xs text-[var(--text-4)]">
        Sizing, loss limits and entry window come from Limits &amp; settings. Every edit is a new version;
        trades keep the version that made them.
      </p>
      <div className="flex gap-2">
        <Button type="submit" disabled={busy}>Save strategy</Button>
        <Button type="button" variant="outline" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  )
}
