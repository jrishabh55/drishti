/* Number formatting for the Trade Desk (₹, %, R). Pure — unit-tested. */

const INR = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const INR0 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })

/** ₹1,23,456.70 — Indian digit grouping. */
export function inr(n: number | null | undefined, whole = false): string {
  if (n == null || Number.isNaN(n)) return '—'
  return `₹${(whole ? INR0 : INR).format(n)}`
}

/** 0.2938 -> "29.4%" */
export function pct01(p: number | null | undefined, digits = 1): string {
  if (p == null || Number.isNaN(p)) return '—'
  return `${(p * 100).toFixed(digits)}%`
}

/** 1.234 -> "+1.23%" (already a percent) */
export function signedPct(p: number | null | undefined, digits = 2): string {
  if (p == null || Number.isNaN(p)) return '—'
  return `${p >= 0 ? '+' : ''}${p.toFixed(digits)}%`
}

/** -1 -> "−1.00R", 0.5 -> "+0.50R" */
export function rMult(r: number | null | undefined, digits = 2): string {
  if (r == null || Number.isNaN(r)) return '—'
  const s = Math.abs(r).toFixed(digits)
  return `${r > 0 ? '+' : r < 0 ? '−' : ''}${s}R`
}

/** "delayed:yahoo" -> { live: false, label: 'DELAYED' } */
export function sourceLabel(source: string | null | undefined): { live: boolean; label: string } {
  if (source?.startsWith('live:')) return { live: true, label: 'LIVE' }
  return { live: false, label: 'DELAYED' }
}

/** ISO time -> "10:35" in IST, the exchange clock. */
export function istTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString('en-IN', {
    hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata',
  })
}

/** An empty result only means “no breakouts” when charts were evaluated. */
export function orbEmptyMessage(counts: Record<string, number>): string {
  if (!counts.with_data) return 'No bars are available for this session yet. The data feed may be catching up; try again shortly.'
  if (!counts.scanned && counts.unusable) return 'Session data is incomplete. No charts could be evaluated yet.'
  if (!counts.scanned) return 'No charts passed your price and liquidity filters.'
  return 'No chart has closed above its opening-range high yet.'
}
