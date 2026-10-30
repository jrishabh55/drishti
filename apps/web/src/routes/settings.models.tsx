import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { Topbar } from '#/components/shared/Topbar'
import { ProviderKeySetup } from '#/components/flow/ProviderKeySetup'
import { jsonFetch } from '#/lib/api'
export const Route = createFileRoute('/settings/models')({ssr:false, component: ModelConnections})
function ModelConnections() {
  const [providers, setProviders] = useState<{provider:string; label:string}[]>([])
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { jsonFetch<{providers:{provider:string;label:string}[];administrator_providers:{provider:string;label:string}[]}>('/keys/providers')
    .then(d => setProviders([...d.providers, ...d.administrator_providers])).catch(e => setError(String(e))) }, [])
  return <><Topbar /><main data-guide="guide-models" className="mx-auto max-w-4xl space-y-5 p-6">
    <h1 className="text-2xl font-semibold">Model connections</h1>
    <p className="text-sm text-[var(--text-3)]">Add personal provider keys, then select the quick and deep model providers in Advanced analysis settings. OpenAI credits, Gemini keys and the ChatGPT helper remain available in the analysis form.</p>
    {error && <p role="alert">{error}</p>}
    <div className="grid gap-4 md:grid-cols-2">{providers.map(p => <section key={p.provider} className="space-y-2"><h2 className="font-medium">{p.label}</h2><ProviderKeySetup provider={p.provider} /></section>)}</div>
  </main></>
}
