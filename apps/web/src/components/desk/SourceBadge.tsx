import { istTime, sourceLabel } from '#/lib/desk-format'

/** LIVE / DELAYED + the newest bar's time. Every desk number sits next to one
 *  of these, so nobody mistakes 15-minute-old bars for the market now. */
export function SourceBadge({ source, asOf }: { source?: string | null; asOf?: string | null }) {
  const s = sourceLabel(source)
  return (
    <span className={`es-pill ${s.live ? 'ok' : 'warn'}`} title={source ?? 'delayed:yahoo'}>
      <span className={`es-dot${s.live ? ' pulse' : ''}`} />
      {s.label}
      {asOf ? ` · ${istTime(asOf)} IST` : ''}
    </span>
  )
}
