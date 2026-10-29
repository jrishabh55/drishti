import { useEffect, useState } from 'react'
import { jsonFetch } from '#/lib/api'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'

type Connection = { provider: string; configured: boolean; last4: string | null; encryption_available?: boolean; label?: string }
export function ProviderKeySetup({ provider }: { provider: string }) {
  const [state, setState] = useState<Connection | null>(null)
  const [key, setKey] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [admin, setAdmin] = useState(false)
  async function load() {
    const doc = await jsonFetch<{providers: Connection[]; administrator_providers: Connection[]}>('/keys/providers')
    const entry = doc.providers.find(p => p.provider === provider)
    setAdmin(!entry)
    setState(entry ?? doc.administrator_providers.find(p => p.provider === provider) ?? null)
  }
  useEffect(() => { setState(null); setError(null); setKey(''); load().catch(e => setError(String(e))) }, [provider])
  async function save() {
    setBusy(true); setError(null)
    try {
      await jsonFetch(`/keys/providers/${encodeURIComponent(provider)}`, {method: 'PUT', body: JSON.stringify({api_key: key})})
      setKey(''); await load()
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  async function remove() {
    setBusy(true)
    try { await jsonFetch(`/keys/providers/${encodeURIComponent(provider)}`, {method:'DELETE'}); await load() }
    catch (e) { setError(String(e)) }
    finally { setBusy(false) }
  }
  return <div className="space-y-2 rounded-lg border border-[var(--line-1)] p-3 text-sm">
    <p>{state?.configured ? `Connected${state.last4 ? ` · ending ${state.last4}` : ''}` : admin ? 'Administrator connection setup required.' : 'Use your own provider API key.'}</p>
    {!admin && <div className="flex flex-wrap gap-2">
      <Input type="password" autoComplete="off" aria-label={`${provider} API key`} placeholder="API key" value={key} onChange={e => setKey(e.target.value)} className="min-w-40 flex-1" />
      <Button size="sm" disabled={busy || key.trim().length < 8 || state?.encryption_available === false} onClick={save}>Save key</Button>
      {state?.configured && <Button size="sm" variant="outline" disabled={busy} onClick={remove}>Remove</Button>}
    </div>}
    {state?.encryption_available === false && <p className="text-[var(--run)]">The administrator must configure encrypted credential storage first.</p>}
    {!admin && <p className="text-xs text-[var(--text-3)]">Stored encrypted. The credential is checked by the provider when you run an analysis; usage is billed by that provider.</p>}
    {error && <p role="alert" className="text-[var(--err)]">{error}</p>}
  </div>
}
