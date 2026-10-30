import catalog from './catalog.json'

export type GuideStep = { route: string; anchor: string; title: string; body: string; fallback: string }
export type Feature = { id: string; title: string; route: string; summary: string; body: string; keywords: string[]; faqs: { question: string; answer: string }[]; chapter: string; steps: GuideStep[] }
export const catalogVersion = catalog.version
export const features: Feature[] = catalog.features
export const firstPaperIds = ['orientation', 'limits', 'research', 'today', 'plans', 'timemachine', 'sizing', 'messages', 'paper', 'performance']
export function tourSteps(id: string) {
  return id === 'first-paper' ? firstPaperIds.flatMap(key => features.find(f => f.id === key)?.steps ?? []) : features.find(f => f.id === id)?.steps ?? []
}
export const normalizePath = (path: string) => path.split(/[?#]/)[0].replace(/\/$/, '') || '/'
export function matchesRoute(pattern: string, path: string) {
  const p = normalizePath(pattern).split('/')
  const a = normalizePath(path).split('/')
  return p.length <= a.length && p.every((v, i) => v.startsWith('$') ? Boolean(a[i]) : v === a[i]) && (pattern !== '/' || a.length === p.length)
}
export function featureForRoute(path: string) {
  // Plan details have their own feature help; Time Machine/sizing remain library lessons.
  const candidates = features.filter(f => !['timemachine', 'sizing'].includes(f.id) && matchesRoute(f.route, path))
  return candidates.sort((a, b) => b.route.length - a.route.length)[0]
}
const stop = new Set('a an the what why how to do does is are can i my we it for of in on this that and with have use'.split(' '))
export function searchHelp(question: string, path: string): Feature[] {
  const terms = (question.toLowerCase().match(/[a-z0-9]+/g) ?? []).filter(t => t.length > 1 && !stop.has(t))
  if (!terms.length) return []
  const current = featureForRoute(path)?.id
  return features.map(f => {
    const text = [f.title, f.summary, f.body, ...f.keywords, ...f.faqs.flatMap(q => [q.question, q.answer])].join(' ').toLowerCase()
    const hits = terms.filter(t => new RegExp(`\\b${t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`).test(text)).length
    return { f, score: hits ? hits + (f.id === current ? 0.5 : 0) : 0 }
  }).filter(x => x.score > 0).sort((a, b) => b.score - a.score).slice(0, 6).map(x => x.f)
}
