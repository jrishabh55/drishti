import { useRef, useState } from 'react'
import { Button } from '#/components/ui/button'
import { api } from '#/lib/api'
import type { Ticket } from '#/lib/desk-types'
import { inr } from '#/lib/desk-format'

/* The order in plain words, its risk verdict, and the user's explicit yes.
   Nothing goes anywhere until "Confirm" on THIS ticket; confirming re-runs
   the risk check server-side (the clock and today's trades may have moved). */

function List({ items, tone }: { items: string[]; tone: string }) {
  if (!items.length) return null
  return (
    <ul className="list-disc space-y-0.5 pl-5 text-xs" style={{ color: tone }}>
      {items.map((x) => <li key={x}>{x}</li>)}
    </ul>
  )
}

export function TicketCard({ ticket, publisherKey, onChange }: {
  ticket: Ticket
  publisherKey: string | null
  onChange: (t: Ticket) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)
  const s = ticket.sizing
  const v = ticket.verdict

  async function act(fn: () => Promise<Ticket>) {
    setBusy(true); setError(null)
    try {
      onChange(await fn())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      api.desk.tickets().then((all) => {
        const fresh = all.find((t) => t.id === ticket.id)
        if (fresh) onChange(fresh)
      }).catch(() => {})
    } finally {
      setBusy(false)
    }
  }

  function copy() {
    navigator.clipboard?.writeText(`${ticket.words}\n${ticket.order?.why ?? ''}`).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }).catch(() => {})
  }

  const statusPill: Record<Ticket['status'], string> = {
    ready: 'ok', blocked: 'err', refused: 'err', paper: 'accent', sent: 'info', cancelled: '',
  }

  return (
    <div className="space-y-3 rounded-[var(--r-lg)] border border-[var(--line-1)] bg-[var(--bg-1)] p-4 shadow-[var(--shadow-1)]">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`es-pill ${statusPill[ticket.status]}`}>
          <span className="es-dot" />
          {ticket.status === 'ready' ? 'PASS — ready for your yes'
            : ticket.status === 'paper' ? 'Paper trade placed'
              : ticket.status === 'sent' ? 'Handed to your broker'
                : ticket.status.toUpperCase()}
        </span>
        {v && <span className="es-pill">risk check {v.verdict}</span>}
      </div>

      <p className="text-base font-medium">{ticket.words}</p>
      {ticket.order && <p className="text-xs text-[var(--text-3)]">{ticket.order.why}</p>}

      {s && s.qty > 0 && (
        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          <div><div className="text-[var(--text-4)]">Position</div><div className="es-mono">{inr(s.cost)}</div></div>
          <div><div className="text-[var(--text-4)]">Loss at stop</div>
            <div className="es-mono text-[var(--err)]">{inr(s.max_loss)} <span className="text-[var(--text-4)]">≈{inr(s.max_loss_net)} w/ charges</span></div></div>
          <div><div className="text-[var(--text-4)]">Gain at target</div>
            <div className="es-mono text-[var(--ok)]">{inr(s.reward_at_target)} <span className="text-[var(--text-4)]">≈{inr(s.reward_at_target_net)} net</span></div></div>
          <div><div className="text-[var(--text-4)]">Reward:risk</div>
            <div className="es-mono">{s.reward_risk.toFixed(2)} <span className="text-[var(--text-4)]">({s.reward_risk_net?.toFixed(2) ?? '—'} net)</span></div></div>
        </div>
      )}
      {s?.limits && (
        <details className="text-xs text-[var(--text-3)]">
          <summary className="cursor-pointer">How the size was chosen (bound by {s.bound_by})</summary>
          <ul className="mt-1 space-y-0.5">
            {s.limits.map((l) => (
              <li key={l.name} className={l.binds ? 'text-[var(--text-1)]' : ''}>
                {l.name}: {l.shares} shares — {l.how}
              </li>
            ))}
          </ul>
        </details>
      )}
      {s?.refusal && <p className="text-sm text-[var(--err)]">{s.refusal}</p>}
      <List items={s?.notes ?? []} tone="var(--run)" />
      {v && <List items={v.reasons} tone="var(--err)" />}
      {v && <List items={v.warnings} tone="var(--run)" />}
      <List items={ticket.verdict_warnings ?? []} tone="var(--text-3)" />
      {v && <List items={v.info} tone="var(--text-3)" />}
      {error && <p className="text-sm text-[var(--err)]">{error}</p>}

      {ticket.status === 'ready' && (
        <div className="flex flex-wrap items-center gap-2 border-t border-[var(--line-1)] pt-3">
          <Button size="sm" disabled={busy}
            onClick={() => act(() => api.desk.confirmTicket(ticket.id, 'paper'))}>
            Confirm — paper trade
          </Button>
          {publisherKey ? (
            <>
              {/* Kite Publisher: the user confirms on Zerodha's own screen.
                  Static-IP/algo rules don't apply to Publisher orders. */}
              <form ref={formRef} method="post" target="_blank"
                action="https://kite.zerodha.com/connect/basket">
                <input type="hidden" name="api_key" value={publisherKey} />
                <input type="hidden" name="data" value={JSON.stringify(ticket.kite_basket)} />
              </form>
              <Button size="sm" variant="outline" disabled={busy}
                onClick={() => {
                  formRef.current?.submit()
                  act(() => api.desk.confirmTicket(ticket.id, 'broker'))
                }}>
                Place on Zerodha ↗
              </Button>
            </>
          ) : (
            <Button size="sm" variant="outline" disabled={busy}
              onClick={() => act(() => api.desk.confirmTicket(ticket.id, 'broker'))}
              title="Records the trade against today's limits; place it yourself at your broker">
              I placed it at my broker
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={copy}>{copied ? 'Copied' : 'Copy ticket'}</Button>
          <Button size="sm" variant="ghost" disabled={busy}
            onClick={() => act(() => api.desk.cancelTicket(ticket.id))}>
            Cancel
          </Button>
        </div>
      )}
      {ticket.status === 'ready' && (
        <p className="text-[11px] text-[var(--text-4)]">
          The stop isn't sent with the entry: place it (SL-M, or a GTT for delivery) right after
          the entry fills, so it can't trigger first and open a short.
        </p>
      )}
    </div>
  )
}
