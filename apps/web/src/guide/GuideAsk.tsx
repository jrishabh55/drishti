import { useEffect, useRef, useState } from 'react'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '#/components/ui/select'
import { jsonFetch } from '#/lib/api'
import { features, searchHelp } from './content'
import { useGuide } from './GuideProvider'

type Provider = { id: string; label: string; models: { id: string; label: string }[] }
type Answer = { answer: string; sources: string[]; mode: 'ai' | 'help'; notice?: string }
export function GuideAsk() {
  const guide = useGuide()!
  const [question, setQuestion] = useState('')
  const [results, setResults] = useState<string[] | null>(null)
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [error, setError] = useState('')
  const [ai, setAi] = useState(false)
  const [providers, setProviders] = useState<Provider[]>([])
  const [loadingProviders, setLoadingProviders] = useState(false)
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [busy, setBusy] = useState(false)
  const [source, setSource] = useState('')
  const request = useRef<AbortController | null>(null)
  const generation = useRef(0)
  const selected = providers.find(p => p.id === provider)
  useEffect(() => {
    generation.current++; request.current?.abort(); setAnswer(null); setResults(null); setError(''); setBusy(false)
    return () => { generation.current++; request.current?.abort() }
  }, [guide.path, ai, provider, model])
  useEffect(() => {
    if (!ai) return
    const controller = new AbortController()
    setLoadingProviders(true); setError('')
    jsonFetch<{ providers: Provider[] }>('/guide/providers', { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setProviders(result.providers) })
      .catch(() => { if (!controller.signal.aborted) setError('Model connections could not be loaded. Built-in help remains available.') })
      .finally(() => { if (!controller.signal.aborted) setLoadingProviders(false) })
    return () => controller.abort()
  }, [ai])
  function findHelp(text: string) { setQuestion(text); setAnswer(null); setError(''); setSource(''); setResults(searchHelp(text, guide.path).map(f => f.id)) }
  async function ask() {
    if (!question.trim() || busy || !ai || !provider || !model) return
    request.current?.abort(); const controller = new AbortController(); request.current = controller
    const current = ++generation.current
    setBusy(true); setError(''); setAnswer(null); setResults(null); setSource('')
    try {
      const response = await jsonFetch<Answer>('/guide/ask', { method: 'POST', signal: controller.signal, body: JSON.stringify({ question: question.trim(), route: guide.path, provider, model, consent: true }) })
      if (generation.current === current && !controller.signal.aborted) setAnswer({ ...response, sources: response.sources.filter(id => features.some(f => f.id === id)) })
    } catch {
      if (generation.current === current && !controller.signal.aborted) setError('AI help is unavailable or its usage limit was reached. Try built-in help, or check your personal model connection. Your question was not run as an analysis.')
    } finally { if (generation.current === current) setBusy(false) }
  }
  const detail = features.find(f => f.id === source)
  return <>
    <span className="drishti-guide-kicker">ASK HOW TO USE DRISHTI</span><h3>Questions about the app</h3>
    <p>Built-in help searches the written guide for free. It works without a model key.</p>
    <form onSubmit={e => { e.preventDefault(); findHelp(question) }}><Input aria-label="Your question" maxLength={1000} value={question} onChange={e => setQuestion(e.target.value)} placeholder="How do I use paper money?" /><div className="drishti-guide-actions"><Button type="submit" size="sm" disabled={!question.trim()}>Find help</Button></div></form>
    <div className="drishti-guide-suggestions">{['Does paper money pay for analysis?', 'Why is there no data?', 'What does Time Machine need?'].map(q => <button key={q} onClick={() => findHelp(q)}>{q}</button>)}</div>
    {results && <div aria-live="polite"><h4>Built-in help</h4>{!results.length && <p>No matching written help found. Try a feature name or a question about using this screen.</p>}{results.map((id, i) => { const f = features.find(v => v.id === id)!; return <article className="drishti-guide-answer" key={id}><strong>{f.title}</strong><p>{i === 0 ? f.faqs[0]?.answer ?? f.body : f.summary}</p><button className="drishti-guide-source" onClick={() => setSource(id)}>Read source: {f.title}</button></article> })}</div>}
    <details className="drishti-guide-ai-settings"><summary>Optional AI answers</summary><p>Your selected personal provider may charge for each question. Only your question, the screen's route pattern and relevant written help are sent. No portfolio, credentials or page contents are sent automatically. Do not include secrets.</p><label className="drishti-guide-consent"><input type="checkbox" checked={ai} onChange={e => setAi(e.target.checked)} />Enable AI using my personal provider</label>{ai && <>
      {loadingProviders ? <p role="status">Loading your model connections…</p> : !providers.length ? <p>No supported personal key is configured. <button className="drishti-guide-source" onClick={() => guide.navigate('/settings/models')}>Open Model connections</button>. Built-in help still works.</p> : <div className="drishti-guide-models"><label>Personal provider<Select value={provider} onValueChange={id => { setProvider(id); setModel('') }}><SelectTrigger aria-label="Guide personal provider"><SelectValue placeholder="Choose provider" /></SelectTrigger><SelectContent>{providers.map(p => <SelectItem key={p.id} value={p.id}>{p.label}</SelectItem>)}</SelectContent></Select></label>{selected && <label>Model<Select value={model} onValueChange={setModel}><SelectTrigger aria-label="Guide model"><SelectValue placeholder="Choose model" /></SelectTrigger><SelectContent>{selected.models.map(m => <SelectItem key={m.id} value={m.id}>{m.label}</SelectItem>)}</SelectContent></Select></label>}</div>}
      <Button size="sm" disabled={!provider || !model || !question.trim() || busy} onClick={ask}>{busy ? 'Asking your provider…' : 'Ask AI · personal provider usage'}</Button>
    </>}</details>
    {error && <p role="alert">{error}</p>}
    {answer && <article className="drishti-guide-answer" aria-live="polite"><h4>{answer.mode === 'ai' ? 'AI answer' : 'Built-in fallback'}</h4><p className="drishti-guide-preserve">{answer.answer}</p>{answer.notice && <p>{answer.notice}</p>}<div>{answer.sources.map(id => <button key={id} className="drishti-guide-source" onClick={() => setSource(id)}>{features.find(f => f.id === id)?.title}</button>)}</div><p className="drishti-guide-note">Check the sources. AI can make mistakes.</p></article>}
    {detail && <article className="drishti-guide-answer"><h4>{detail.title}</h4><p>{detail.body}</p><Button variant="outline" size="sm" onClick={() => guide.navigate(detail.route)}>Go to this feature</Button></article>}
  </>
}
