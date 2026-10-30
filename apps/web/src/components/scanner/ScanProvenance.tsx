import type { ScanResult } from '#/lib/scanner-types'

export function ScanProvenance({ result }: { result: ScanResult }) {
  const c = result.coverage
  const label = result.source === 'live:kite' ? 'Kite candles'
    : result.source === 'mixed' ? 'Mixed Kite/delayed candles' : 'Delayed Yahoo candles'
  return <div className="space-y-1 text-xs text-muted-foreground" aria-label="Scanner data sources">
    <p>{label}{c ? ` · ${c.live} Kite · ${c.delayed} delayed · ${c.mixed} partial sources` : ''}</p>
    {c && <p>{Object.entries(c.by_timeframe).map(([tf, v]) => `${tf}: ${v.kite} Kite, ${v.delayed} delayed, ${v.missing} missing`).join(' · ')}</p>}
    <p>Conditions use completed candles. Price and volume use closed daily candles. Fundamentals and liquidity metadata use the public snapshot.</p>
  </div>
}
