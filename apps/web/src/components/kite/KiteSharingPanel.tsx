import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { kite, kiteError, type KiteSharing } from '#/lib/kite-api'
import { Card } from './KiteBits'

export function KiteSharingPanel({ configured, onChange }: { configured: boolean; onChange?: () => void }) {
  const [info, setInfo] = useState<KiteSharing | null>(null)
  const [recipient, setRecipient] = useState('')
  const [label, setLabel] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const sequence = useRef(0)

  const refresh = useCallback(async () => {
    const id = ++sequence.current
    try {
      const next = await kite.sharing()
      if (id === sequence.current) { setInfo(next); setError(null) }
    } catch (e) { if (id === sequence.current) setError(kiteError(e)) }
  }, [])

  useEffect(() => {
    void refresh()
    return () => { sequence.current++ }
  }, [refresh])

  async function perform(action: () => Promise<unknown>) {
    const id = ++sequence.current
    setBusy(true); setError(null); setNotice(null)
    try {
      await action()
      const next = await kite.sharing()
      if (id !== sequence.current) return
      setInfo(next); setRecipient(''); setLabel('')
      onChange?.()
    } catch (e) { if (id === sequence.current) setError(kiteError(e)) }
    finally { if (id === sequence.current) setBusy(false) }
  }

  return <Card title="Shared market-data connections"
    hint="Grant selected Drishti accounts access to your market-data feed. Keys, broker orders, positions and trading controls stay with you.">
    {error && <p role="alert" className="text-sm text-[var(--err)]">{error}</p>}
    {error && !info && <Button size="sm" variant="outline" onClick={() => void refresh()}>Retry loading connections</Button>}
    {notice && <p role="status" className="text-xs text-[var(--text-3)]">{notice}</p>}
    {!info ? <p className="text-sm text-[var(--text-3)]">Loading sharing settings…</p> : !info.enabled ?
      <p className="text-sm">Sharing requires separate signed-in Drishti accounts.</p> : <>
      <div className="grid gap-1.5">
        <label htmlFor="kite-account-id" className="text-xs text-[var(--text-3)]">Your Drishti account ID</label>
        <div className="flex items-center gap-2">
          <Input id="kite-account-id" readOnly value={info.user_id} className="es-mono" />
          <Button variant="outline" onClick={async () => {
            try { await navigator.clipboard.writeText(info.user_id); setNotice('Account ID copied.') }
            catch { setNotice('Select and copy the account ID above.') }
          }}>Copy ID</Button>
        </div>
        <p className="text-xs text-[var(--text-4)]">Copy the recipient account's ID from this page while signed in to that account.</p>
      </div>
      <form className="space-y-3" onSubmit={e => {
        e.preventDefault(); void perform(() => kite.grantData(recipient.trim(), label.trim()))
      }}>
        <div className="grid gap-1.5">
          <label htmlFor="kite-share-recipient" className="text-xs text-[var(--text-3)]">Recipient account ID</label>
          <Input id="kite-share-recipient" value={recipient} onChange={e => setRecipient(e.target.value)} maxLength={128} placeholder="Recipient's Drishti account ID" />
        </div>
        <div className="grid gap-1.5">
          <label htmlFor="kite-share-label" className="text-xs text-[var(--text-3)]">Account label</label>
          <Input id="kite-share-label" value={label} onChange={e => setLabel(e.target.value)} maxLength={60} placeholder="Optional, e.g. Momentum tests" />
        </div>
        <Button type="submit" size="sm" disabled={busy || !configured || !recipient.trim()}>Grant data access</Button>
        {!configured && <p className="text-xs text-[var(--text-4)]">Save your own Kite app to grant access. You can use an incoming shared connection without saving keys.</p>}
        <p className="text-xs text-[var(--text-4)]">Replacing or forgetting your Kite keys removes your outgoing grants.</p>
      </form>
      {info.grants.length > 0 && <div className="space-y-2">
        <h3 className="text-sm font-medium">Accounts you allow</h3>
        {info.grants.map(g => <div key={g.recipient_id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-[var(--line-1)] p-2 text-sm">
          <div>{g.label && <p>{g.label}</p>}<code className="break-all text-xs">{g.recipient_id}</code></div>
          <Button size="sm" variant="outline" disabled={busy} aria-label={`Revoke access for ${g.recipient_id}`}
            onClick={() => void perform(() => kite.revokeData(g.recipient_id))}>Revoke</Button>
        </div>)}
      </div>}
      <div className="space-y-2 border-t border-[var(--line-1)] pt-3">
        <h3 className="text-sm font-medium">Market-data source</h3>
        <Button size="sm" variant="outline" disabled={busy || !info.selected_owner_id}
          onClick={() => void perform(() => kite.selectDataSource(null))}>
          {info.selected_owner_id ? 'Use my own connection' : 'Using my own connection'}
        </Button>
        {info.received.map(g => <div key={g.owner_id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <code className="break-all text-xs">{g.owner_id}</code>
          <Button size="sm" variant="outline" disabled={busy || info.selected_owner_id === g.owner_id}
            aria-label={`Use connection from ${g.owner_id}`} onClick={() => void perform(() => kite.selectDataSource(g.owner_id))}>
            {info.selected_owner_id === g.owner_id ? 'Selected' : 'Use connection'}
          </Button>
        </div>)}
        {!info.received.length && <p className="text-xs text-[var(--text-4)]">No incoming connections. Ask the owner to grant your account ID access.</p>}
        <p className="text-xs text-[var(--text-4)]">The connection owner logs in daily and controls streaming symbols. Your paper portfolios and results stay separate.</p>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => void refresh()}>Refresh connections</Button>
      </div>
    </>}
  </Card>
}
