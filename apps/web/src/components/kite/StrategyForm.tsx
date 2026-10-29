import { useState } from 'react'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import type { Strategy, StrategyInput, StrategyMode } from '#/lib/kite-api'

export const BLANK_STRATEGY: StrategyInput = {
  name: 'ORB 15m', mode: 'off', symbols: [], setup: 'orb', level: null, or_minutes: 15,
  stop_rule: 'or_mid', stop_param: null, target_r: 2, order_type: 'LIMIT',
  max_signals_per_day: 1, confirm_ttl_s: 120,
}

export function toInput(s: Strategy): StrategyInput {
  const { id: _id, signals: _sig, version: _v, paused_reason: _p, ...rest } = s
  return rest
}

export const MODES: { value: StrategyMode; label: string; hint: string }[] = [
  { value: 'off', label: 'Off', hint: 'does nothing' },
  { value: 'paper', label: 'Paper', hint: 'signals go to the paper ledger' },
  { value: 'confirm', label: 'Confirm', hint: 'signals wait for your typed confirmation to send live' },
]

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

export function StrategyForm({ initial, onSave, onCancel, busy }: {
  initial: StrategyInput; onSave: (s: StrategyInput) => void; onCancel: () => void; busy: boolean
}) {
  const [s, setS] = useState(initial)
  const [syms, setSyms] = useState(initial.symbols.join(', '))
  const num = (v: string) => (v === '' ? null : Number(v))
  const row = 'flex items-center justify-between gap-3 text-sm'
  return (
    <form className="space-y-2" onSubmit={(e) => {
      e.preventDefault()
      onSave({ ...s, symbols: syms.split(',').map((x) => x.trim()).filter(Boolean) })
    }}>
      <label className={row}><span>Name</span>
        <Input className="w-64" value={s.name} onChange={(e) => setS({ ...s, name: e.target.value })} /></label>
      <label className={row}><span>Symbols (≤ 20)</span>
        <Input className="w-64 es-mono" value={syms} onChange={(e) => setSyms(e.target.value.toUpperCase())} /></label>
      <div className={row}><span>Mode</span>
        <Seg value={s.mode} options={MODES} onChange={(mode) => setS({ ...s, mode })} /></div>
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
          { value: 'pct', label: '%' }, { value: 'atr', label: 'ATR' }] as const}
          onChange={(stop_rule) => setS({ ...s, stop_rule })} /></div>
      {(s.stop_rule === 'pct' || s.stop_rule === 'atr') && (
        <label className={row}><span>{s.stop_rule === 'pct' ? 'Stop %' : 'ATR multiple'}</span>
          <Input className="w-36 es-mono" type="number" value={s.stop_param ?? ''}
            onChange={(e) => setS({ ...s, stop_param: num(e.target.value) })} /></label>
      )}
      <label className={row}><span>Target (R)</span>
        <Input className="w-36 es-mono" type="number" step="0.1" value={s.target_r}
          onChange={(e) => setS({ ...s, target_r: Number(e.target.value) })} /></label>
      <div className={row}><span>Entry order</span>
        <Seg value={s.order_type} options={[{ value: 'LIMIT', label: 'Limit' }, { value: 'MARKET', label: 'Market' }] as const}
          onChange={(order_type) => setS({ ...s, order_type })} /></div>
      <label className={row}><span>Max signals per day</span>
        <Input className="w-36 es-mono" type="number" value={s.max_signals_per_day}
          onChange={(e) => setS({ ...s, max_signals_per_day: Number(e.target.value) })} /></label>
      <label className={row}><span>Confirm window (seconds)</span>
        <Input className="w-36 es-mono" type="number" value={s.confirm_ttl_s}
          onChange={(e) => setS({ ...s, confirm_ttl_s: Number(e.target.value) })} /></label>
      <p className="text-xs text-[var(--text-4)]">
        Sizing, loss limits and entry window come from Limits &amp; settings. There is no automatic mode.
      </p>
      <div className="flex gap-2">
        <Button type="submit" disabled={busy}>Save strategy</Button>
        <Button type="button" variant="outline" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  )
}
