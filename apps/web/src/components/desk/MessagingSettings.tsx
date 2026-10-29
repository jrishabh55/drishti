import { useEffect, useState } from 'react'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { Switch } from '#/components/ui/switch'
import { messagingApi as api, type Channel, type MessagingStatus } from '#/lib/messaging-api'

function ConnectionCard({ channel }: { channel: Channel }) {
  const label = channel === 'whatsapp' ? 'WhatsApp' : 'Telegram'
  const [status, setStatus] = useState<MessagingStatus | null>(null)
  const [url, setUrl] = useState('')
  const [key, setKey] = useState('')
  const [phone, setPhone] = useState('')
  const [recipient, setRecipient] = useState('')
  const [code, setCode] = useState('')
  const [replaceHook, setReplaceHook] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    let disposed = false
    setError('')
    api.status(channel).then(s => {
      if (!disposed) { setStatus(s); setUrl(s.server_url || ''); setRecipient(s.recipient || '') }
    }).catch(e => { if (!disposed) setError(String(e.message || e)) })
    return () => { disposed = true }
  }, [channel, refreshKey])

  useEffect(() => {
    if (!status?.configured) return
    let disposed = false
    let fetching = false
    const timer = setInterval(() => {
      if (busy || fetching) return
      fetching = true
      const fn = channel === 'whatsapp' && !status.connected ? api.refresh : api.status
      fn(channel).then(s => {
        if (!disposed) { setStatus(s); if (s.recipient) setRecipient(s.recipient) }
      }).catch(() => {}).finally(() => { fetching = false })
    }, 5000)
    return () => { disposed = true; clearInterval(timer) }
  }, [channel, status?.id, status?.configured, status?.connected, busy])

  async function act(fn: () => Promise<MessagingStatus>, success = '') {
    setBusy(true); setMessage(''); setError('')
    try { const s = await fn(); setStatus(s); if (s.recipient) setRecipient(s.recipient); setMessage(success) }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  async function save() {
    await act(async () => {
      const s = await api.setup(channel, channel === 'whatsapp' ? {server_url:url,api_key:key} : {bot_token:key,replace_webhook:replaceHook})
      setKey(''); return s
    }, 'Connection settings saved.')
  }
  async function sendCode() {
    setBusy(true); setError(''); setMessage('')
    try { await api.recipient(recipient); setMessage('Verification code sent to that WhatsApp number. Enter it below.') }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  const disabled = busy || !status || status.encryption_available === false || !!status.cleanup_pending
  const verified = !!(status?.verified && status?.connected)
  const qr = status?.qr?.startsWith('data:image/png;base64,') ? status.qr : undefined

  return <section className="es-card space-y-4 p-5" aria-label={`${label} connection`}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-lg font-semibold">{label}</h2>
      <span className={`es-pill ${verified ? 'ok' : status?.configured ? 'info' : ''}`}>
        {verified ? 'Verified' : status?.connected ? 'Connected · verify account' : status?.configured ? 'Setup in progress' : 'Not connected'}
      </span>
    </div>
    {!status && !error && <p role="status">Loading connection…</p>}
    {channel === 'whatsapp' ? <>
      <p className="text-sm text-[var(--text-3)]">Connect your number through your self-hosted Evolution server. Open WhatsApp → Linked devices → Link a device to scan the QR shown here.</p>
      <label className="block text-sm">Evolution server URL<Input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://evolution.example.com" autoComplete="url" /></label>
      <label className="block text-sm">Evolution API key<Input type="password" autoComplete="off" value={key} onChange={e => setKey(e.target.value)} placeholder={status?.server_configured ? 'Server key configured; leave blank to use it' : 'Enter API key'} /></label>
      <p className="text-xs text-[var(--text-4)]">The URL must be approved by your administrator. Saved keys stay encrypted and are never shown again.</p>
      <Button disabled={disabled} onClick={save}>Save WhatsApp server</Button>
      {status?.configured && <div className="space-y-3 border-t border-[var(--line-1)] pt-4">
        <h3 className="font-medium">Link your phone</h3>
        <label className="block text-sm">WhatsApp number with country code<Input value={phone} onChange={e => setPhone(e.target.value)} placeholder="919876543210" inputMode="tel" /></label>
        <div className="flex flex-wrap gap-2">
          <Button disabled={disabled || !!status.connected} onClick={() => act(() => api.pair(channel,''))}>Generate WhatsApp QR</Button>
          <Button variant="outline" disabled={disabled || !phone || !!status.connected} onClick={() => act(() => api.pair(channel,phone))}>Get WhatsApp pairing code</Button>
          <Button variant="outline" disabled={disabled} onClick={() => act(() => api.refresh(channel))}>Refresh WhatsApp connection</Button>
        </div>
        {qr && <div className="space-y-2"><img className="h-60 w-60 rounded-md bg-white p-2" src={qr} alt="Scan in WhatsApp Linked devices" /><p className="text-xs text-[var(--text-3)]">QR codes expire quickly. Generate a fresh code after expiry if needed.</p></div>}
        {status.pairing_code && <div><p className="text-xs text-[var(--text-3)]">Choose “Link with phone number instead” in WhatsApp:</p><code className="es-mono text-xl">{status.pairing_code}</code></div>}
        {status.phone && <p>Linked number: <strong className="es-mono">+{status.phone}</strong></p>}
        {status.connected && <div className="space-y-2">
          <h3 className="font-medium">Verify notification recipient</h3>
          <p className="text-xs text-[var(--text-3)]">Use your own number for WhatsApp self-chat, or another private number you control. Verification confirms where messages go.</p>
          <label className="block text-sm">WhatsApp recipient<Input value={recipient} onChange={e => setRecipient(e.target.value)} inputMode="tel" placeholder="Number with country code" /></label>
          <Button variant="outline" disabled={disabled || !recipient} onClick={sendCode}>Send verification code</Button>
          <label className="block text-sm">WhatsApp verification code<Input value={code} onChange={e => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" /></label>
          <Button disabled={disabled || !code} onClick={() => act(() => api.confirm(channel,code),'Recipient verified. Reply to the LINK message in WhatsApp to enable paper approvals.')}>Verify WhatsApp code</Button>
        </div>}
        {verified && !status.inbound_verified && <p className="text-sm text-[var(--run)]">Reply to the LINK message as a new WhatsApp message to verify incoming replies. If self-chat replies do not reach this server, use another verified number or confirm trades in Drishti.</p>}
      </div>}
    </> : <>
      <p className="text-sm text-[var(--text-3)]">Create a bot in Telegram’s BotFather and paste its token here. Then open the connection link and start a private chat. Drishti finds your chat ID automatically.</p>
      <a href="https://t.me/BotFather" target="_blank" rel="noreferrer" className="text-sm text-[var(--accent)] underline">Open BotFather</a>
      <label className="block text-sm">Telegram bot token<Input type="password" autoComplete="off" value={key} onChange={e => setKey(e.target.value)} placeholder={status?.configured ? 'Saved; leave blank to keep' : '123456:bot-token'} /></label>
      <label className="flex items-start gap-2 text-xs text-[var(--text-3)]"><input type="checkbox" checked={replaceHook} onChange={e => setReplaceHook(e.target.checked)} />Replace an existing Telegram webhook. This stops that bot’s integration with its previous app.</label>
      <Button disabled={disabled || !key && !status?.configured} onClick={save}>Save Telegram bot</Button>
      {status?.configured && <div className="space-y-3 border-t border-[var(--line-1)] pt-4">
        {status.bot_username && <p>Bot: <strong>@{status.bot_username}</strong></p>}
        <Button disabled={disabled} onClick={() => act(() => api.pair(channel,''))}>Create Telegram connection link</Button>
        {status.pair_url?.startsWith('https://t.me/') && <a className="block text-[var(--accent)] underline" href={status.pair_url} target="_blank" rel="noreferrer">Open Telegram to connect</a>}
        {status.pending_identity && <div className="space-y-2"><p>Telegram account waiting for your confirmation:</p><p className="font-medium">{status.pending_identity}</p><Button disabled={disabled} onClick={() => act(() => api.confirm(channel),'Telegram account verified.')}>Confirm Telegram account</Button></div>}
        <Button variant="outline" disabled={disabled} onClick={() => act(() => api.refresh(channel))}>Refresh Telegram connection</Button>
      </div>}
    </>}
    <div className="space-y-3 border-t border-[var(--line-1)] pt-4">
      <label className="flex items-center justify-between gap-3 text-sm">{label} alerts<Switch aria-label={`${label} alerts`} disabled={disabled || !verified} checked={!!status?.alerts} onCheckedChange={v => act(() => api.preferences(channel,v,!!status?.approvals))} /></label>
      <label className="flex items-center justify-between gap-3 text-sm">{label} paper approvals<Switch aria-label={`${label} paper approvals`} disabled={disabled || !verified || !status?.inbound_verified} checked={!!status?.approvals} onCheckedChange={v => act(() => api.preferences(channel,!!status?.alerts,v))} /></label>
      <p className="text-xs text-[var(--text-4)]">Paper approvals expire after five minutes or the entry cutoff. WhatsApp uses explicit reply codes; Telegram offers buttons. Real broker orders stay disabled.</p>
      <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={disabled || !verified} onClick={() => act(() => api.test(channel),'Test requested. Check this connection for delivery errors.')}>Test {label}</Button>
        {(status?.configured || status?.cleanup_pending) && <Button variant="ghost" disabled={busy} onClick={() => act(() => api.disconnect(channel),`${label} disconnected. Pending approvals revoked.`)}>{status.cleanup_pending ? `Retry ${label} cleanup` : `Disconnect ${label}`}</Button>}</div>
    </div>
    {status?.encryption_available === false && <p className="text-sm text-[var(--run)]">Encrypted credential storage needs administrator setup.</p>}
    {(error || status?.last_error) && <div role="alert" className="text-sm text-[var(--err)]">{error || status?.last_error}</div>}
    {!status && error && <Button variant="outline" onClick={() => setRefreshKey(v => v+1)}>Retry {label} setup</Button>}
    {message && <p role="status" className="text-sm text-[var(--text-2)]">{message}</p>}
  </section>
}

export function MessagingSettings() {
  return <div className="grid items-start gap-5 lg:grid-cols-2"><ConnectionCard channel="whatsapp" /><ConnectionCard channel="telegram" /></div>
}
