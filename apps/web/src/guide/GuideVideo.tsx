import { useEffect, useRef, useState } from 'react'
import { Button } from '#/components/ui/button'
type Clip = { src: string; poster: string; duration: number }
type Manifest = { version: string; intro: Clip; walkthrough: Clip; chapters: { id: string; title: string; start: number }[] }
const mediaPath = (value: unknown) => typeof value === 'string' && /^\/guide-media\/[a-zA-Z0-9_-]+\/[a-zA-Z0-9_.-]+$/.test(value) && !value.includes('..')
export function GuideVideo({ chapterId }: { chapterId: string }) {
  const [manifest, setManifest] = useState<Manifest | null>(null)
  const [error, setError] = useState(false)
  const [mode, setMode] = useState<'intro' | 'walkthrough'>('intro')
  const [expanded, setExpanded] = useState(false)
  const player = useRef<HTMLVideoElement>(null)
  const pendingSeek = useRef(0)
  useEffect(() => {
    const controller = new AbortController()
    fetch('/guide-media/manifest.json', { signal: controller.signal }).then(async res => {
      if (!res.ok) throw Error('not published')
      const m = await res.json() as Manifest
      if (![m.intro, m.walkthrough].every(c => c && mediaPath(c.src) && mediaPath(c.poster) && Number.isFinite(c.duration) && c.duration > 0) || !Array.isArray(m.chapters) || !m.chapters.every(c => typeof c.id === 'string' && typeof c.title === 'string' && Number.isFinite(c.start) && c.start >= 0 && c.start < m.walkthrough.duration)) throw Error('invalid manifest')
      if (!controller.signal.aborted) setManifest(m)
    }).catch(() => { if (!controller.signal.aborted) setError(true) })
    return () => controller.abort()
  }, [])
  function choose(next: 'intro' | 'walkthrough', start = 0) {
    pendingSeek.current = start; setMode(next); setExpanded(true)
    if (mode === next && player.current?.readyState) player.current.currentTime = start
  }
  const chapter = manifest?.chapters.find(c => c.id === chapterId)
  return <section className="drishti-guide-video"><h4>Watch the walkthrough</h4>{manifest && !error ? <>
    <div className="drishti-guide-actions"><Button variant="outline" size="sm" onClick={() => choose('intro')}>Introduction</Button><Button variant="outline" size="sm" onClick={() => choose('walkthrough', chapter?.start ?? 0)}>{chapter ? 'Watch this chapter' : 'Full walkthrough'}</Button></div>
    {expanded && <><video key={mode} ref={player} controls preload="metadata" playsInline poster={manifest[mode].poster} src={manifest[mode].src} onError={() => setError(true)} onLoadedMetadata={() => { if (player.current) player.current.currentTime = pendingSeek.current }}><track kind="captions" src={manifest[mode].src.replace(/\.mp4$/, '.vtt')} srcLang="en" label="English" /></video><p className="drishti-guide-note">English narration and captions · illustrative demo account</p>{mode === 'walkthrough' && <div className="drishti-guide-chapters">{manifest.chapters.map(c => <button key={c.id} onClick={() => choose('walkthrough', c.start)}>{c.title}</button>)}</div>}</>}
  </> : <p className="drishti-guide-note">{error ? 'Videos are not available in this deployment yet. Written help and tours work independently.' : 'Checking video availability…'}</p>}</section>
}
