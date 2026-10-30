import { Link } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { StrategyForm } from '#/components/kite/StrategyForm'
import { Button } from '#/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '#/components/ui/dialog'
import { api } from '#/lib/api'
import type { ScannerSummary } from '#/lib/scanner-types'
import { kiteError, type Strategy, type StrategyInput } from '#/lib/kite-api'
import { workflowApi, type DraftSource, type StrategyDraft } from '#/lib/strategy-workflow-api'
import { usePortfolios } from './Portfolios'

export function StrategyDraftButton({ source, label = 'Draft strategy' }: { source: DraftSource; label?: string }) {
  const pf = usePortfolios()
  const [draft, setDraft] = useState<StrategyDraft | null>(null)
  const [scanners, setScanners] = useState<ScannerSummary[]>([])
  const [saved, setSaved] = useState<Strategy | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const seq = useRef(0)
  useEffect(() => () => { seq.current++ }, [])
  function close() { seq.current++; setDraft(null); setSaved(null); setBusy(false); setError(null) }
  async function preview() {
    const n = ++seq.current
    setBusy(true); setError(null); setSaved(null)
    try {
      const [d, options] = await Promise.all([
        workflowApi.preview({ ...source, portfolio_id: pf?.selectedId ?? 'default' }),
        api.listScanners().catch(() => [] as ScannerSummary[]),
      ])
      if (n === seq.current) { setDraft(d); setScanners(options) }
    } catch (e) { if (n === seq.current) setError(kiteError(e)) }
    finally { if (n === seq.current) setBusy(false) }
  }
  async function save(strategy: StrategyInput) {
    if (!draft) return
    const n = ++seq.current
    setBusy(true); setError(null)
    try {
      const result = await workflowApi.save(draft.id, strategy)
      if (n === seq.current) setSaved(result.strategy)
    } catch (e) { if (n === seq.current) setError(kiteError(e)) }
    finally { if (n === seq.current) setBusy(false) }
  }
  return (
    <span className="inline-flex flex-col items-start gap-1">
      <Button size="sm" variant="outline" disabled={busy || Boolean(pf && !pf.ready)} onClick={(e) => { e.stopPropagation(); void preview() }}>
        {busy && !draft ? 'Preparing…' : label}
      </Button>
      {error && !draft && <span role="alert" className="text-xs text-destructive">{error}</span>}
      <Dialog open={Boolean(draft)} onOpenChange={(open) => { if (!open) close() }}>
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl" onClick={(e) => e.stopPropagation()}>
          <DialogTitle>{saved ? 'Strategy saved Off' : 'Review your strategy draft'}</DialogTitle>
          <DialogDescription>Review the source, portfolio and rules. Saving this draft does not start trading.</DialogDescription>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          {saved ? (
            <div className="space-y-3">
              <p>{saved.name} is saved. Open Strategies, review its data readiness and deliberately select Paper to begin testing.</p>
              <Link to="/desk/strategies" className="es-btn sm" onClick={close}>Review in Strategies →</Link>
            </div>
          ) : draft && (
            <>
              <ul className="space-y-1 text-xs text-muted-foreground">{draft.warnings.map(w => <li key={w}>{w}</li>)}</ul>
              {draft.source.kind === 'analysis' && <p className="text-xs text-muted-foreground">Research date: {String(draft.source.analysis_date)} · absolute stop and target preserved.</p>}
              {draft.source.kind === 'scanner' && <p className="text-xs text-muted-foreground">Current preview: {Array.isArray(draft.source.preview_symbols) ? draft.source.preview_symbols.join(', ') : 'No preview'} · {draft.source.selection === 'dynamic' ? 'follows saved scanner matches' : 'fixed selected symbols'}</p>}
              <StrategyForm key={draft.id} initial={draft.strategy} portfolios={pf?.portfolios ?? []} scanners={scanners} draft busy={busy} onSave={(s) => void save(s)} onCancel={close} />
            </>
          )}
        </DialogContent>
      </Dialog>
    </span>
  )
}
