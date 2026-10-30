import { Link } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '#/components/ui/button'
import { workflowApi, type TradingReadiness as Readiness } from '#/lib/strategy-workflow-api'
import { kiteError } from '#/lib/kite-api'

export function TradingReadiness() {
  const [data, setData] = useState<Readiness | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const seq = useRef(0)
  const refresh = useCallback(async () => {
    const n = ++seq.current
    setBusy(true)
    try { const d = await workflowApi.readiness(); if (n === seq.current) { setData(d); setError(null) } }
    catch (e) { if (n === seq.current) setError(kiteError(e)) }
    finally { if (n === seq.current) setBusy(false) }
  }, [])
  useEffect(() => { void refresh(); return () => { seq.current++ } }, [refresh])
  return (
    <section className="es-card space-y-3 p-4" data-guide="guide-trading-readiness">
      <div className="flex items-center justify-between gap-2"><h2 className="font-semibold">Trading readiness</h2><Button size="sm" variant="outline" disabled={busy} onClick={() => void refresh()}>{busy ? 'Checking…' : 'Refresh status'}</Button></div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {data && <>
        <p className="text-sm">Live order sending: <strong>{data.live_orders_enabled ? 'enabled on server — account and daily strategy gates still apply' : 'disabled on server'}</strong></p>
        <p className="text-sm text-muted-foreground">{data.market_open ? 'NSE session is open.' : 'NSE session is closed; strategies wait for the next trading session.'} {data.data_coverage.ready} of {data.data_coverage.subscribed} subscribed symbols have fresh live bars.</p>
        <div className="flex flex-wrap gap-2">{Object.entries(data.loops).map(([name,state]) => <span key={name} className="es-pill">{name}: {state}</span>)}</div>
        {data.paper_blockers.length ? <ul className="space-y-1 text-sm text-[var(--run)]">{data.paper_blockers.map(b => <li key={b}>{b}</li>)}</ul> : <p className="text-sm text-[var(--ok)]">Paper strategy prerequisites are present. Inspect each strategy’s scan diagnostics and actual trade results.</p>}
        <p className="text-xs text-muted-foreground">{data.validation_note}</p>
        <div className="flex flex-wrap gap-3 text-sm"><Link to="/desk/connection">Connect market data →</Link><Link to="/desk/strategies">Review strategies →</Link></div>
      </>}
    </section>
  )
}
