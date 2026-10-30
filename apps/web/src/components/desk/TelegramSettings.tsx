import { useEffect, useState } from 'react'
import { jsonFetch } from '#/lib/api'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { Switch } from '#/components/ui/switch'

type Status = {configured:boolean; enabled:boolean; chat_id:string; last_error:string|null; encryption_available:boolean}
export function TelegramSettings() {
  const [status,setStatus] = useState<Status|null>(null)
  const [token,setToken] = useState('')
  const [chat,setChat] = useState('')
  const [enabled,setEnabled] = useState(false)
  const [busy,setBusy] = useState(false)
  const [message,setMessage] = useState<string|null>(null)
  useEffect(() => { jsonFetch<Status>('/desk/notifications/telegram').then(s => {
    setStatus(s); setChat(s.chat_id); setEnabled(s.enabled)
  }).catch(e => setMessage(String(e))) }, [])
  async function save() {
    setBusy(true); setMessage(null)
    try {
      const s = await jsonFetch<Status>('/desk/notifications/telegram',{method:'PUT',body:JSON.stringify({bot_token:token,chat_id:chat,enabled})})
      setStatus(s); setToken(''); setMessage('Saved. New Desk alerts will use this setting.')
    } catch(e) { setMessage(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  async function remove() {
    setBusy(true)
    try { await jsonFetch('/desk/notifications/telegram',{method:'DELETE'}); setToken('');setChat('');setEnabled(false);setStatus(null);setMessage('Telegram disconnected.') }
    catch(e) { setMessage(String(e)) }
    finally { setBusy(false) }
  }
  return <section className="space-y-3 rounded-lg border border-[var(--line-1)] p-4">
    <h2 className="font-semibold">Telegram alerts</h2>
    <p className="text-xs text-[var(--text-3)]">Create a Telegram bot with BotFather, start a chat with it, and add its token and your chat ID. When enabled, new Trade Desk alert messages are sent to this chat. In-app alerts remain available.</p>
    <label className="grid gap-1.5 text-sm">Bot token<Input type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} placeholder={status?.configured ? 'Saved; leave blank to keep' : '123456:bot-token'} /></label>
    <label className="grid gap-1.5 text-sm">Chat ID<Input value={chat} onChange={e => setChat(e.target.value)} /></label>
    <label className="flex items-center justify-between gap-3 text-sm">Send new alerts to Telegram<Switch checked={enabled} onCheckedChange={setEnabled} /></label>
    <div className="flex gap-2"><Button size="sm" disabled={busy || !chat || status?.encryption_available===false} onClick={save}>Save Telegram settings</Button>{status?.configured && <Button size="sm" variant="outline" disabled={busy} onClick={remove}>Disconnect</Button>}</div>
    {status?.encryption_available===false && <p className="text-xs text-[var(--run)]">Encrypted credential storage needs administrator setup.</p>}
    {(message || status?.last_error) && <p role="status" className="text-xs">{message || status?.last_error}</p>}
  </section>
}
