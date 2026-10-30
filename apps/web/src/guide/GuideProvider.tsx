import { createContext, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useNavigate, useRouterState } from '@tanstack/react-router'
import { CircleHelp } from 'lucide-react'
import { Button } from '#/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '#/components/ui/dialog'
import { matchesRoute, tourSteps } from './content'
import { emptyProgress, readProgress, writeProgress, progressWasReset } from './state'
import type { Progress } from './state'
import { GuidePanel } from './GuidePanel'
import './guide.css'

type GuideContextValue = {
  openGuide: () => void; closeGuide: () => void; path: string; progress: Progress;
  startTour: (id: string) => void; advance: (delta: number) => void;
  stopTour: () => void; restart: () => void; navigate: (path: string) => void;
  targetVisible: boolean; retryTarget: () => void; userId: string;
  mobile: boolean; peekGuide: () => void; navigationNotice: string;
  progressReset: boolean; dismissReset: () => void;
}
const Context = createContext<GuideContextValue | null>(null)
export function useGuide() { return useContext(Context) }
function storage() { try { return window.localStorage } catch { return undefined } }

export function GuideButton() {
  const guide = useGuide()
  return guide ? <Button variant="ghost" size="sm" aria-label="Open Drishti Guide" onClick={guide.openGuide}><CircleHelp size={15} /><span>Guide</span></Button> : null
}
export function GuideProvider({ children, userId }: { children: ReactNode; userId: string }) {
  return <GuideSession key={userId} userId={userId}>{children}</GuideSession>
}
function GuideSession({ children, userId }: { children: ReactNode; userId: string }) {
  const path = useRouterState({ select: s => s.location.pathname })
  const routerNavigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [mobile, setMobile] = useState(false)
  const [peek, setPeek] = useState(false)
  const [navigationNotice, setNavigationNotice] = useState('')
  const [progressReset, setProgressReset] = useState(() => progressWasReset(storage(), userId))
  const [progress, setProgress] = useState<Progress>(() => readProgress(storage(), userId))
  const [targetVisible, setTargetVisible] = useState(false)
  const [retry, setRetry] = useState(0)
  const returnFocus = useRef<HTMLElement | null>(null)
  const panel = useRef<HTMLDivElement>(null)
  const resume = useRef<HTMLButtonElement>(null)
  const steps = progress.tourId ? tourSteps(progress.tourId) : []
  const step = steps[progress.stepIndex]
  const navigate = (route: string) => {
    // A dynamic destination must already be the current concrete route.
    setNavigationNotice('')
    if (route.includes('$')) {
      if (matchesRoute(route, path)) route = path
      else if (route.startsWith('/runs/')) { route = '/analyse'; setNavigationNotice('Choose an existing report or deliberately run an analysis first. The guide does not create a run for you.') }
      else { route = '/desk/plans'; setNavigationNotice('Open or create a plan first, then return to this lesson. A draft needs an entry time and sufficient history for intraday Time Machine.') }
    }
    void routerNavigate({ to: route as never })
  }
  function openGuide() { if (!open) returnFocus.current = document.activeElement as HTMLElement; setPeek(false); setOpen(true) }
  function closeGuide() { setOpen(false); setPeek(false); requestAnimationFrame(() => returnFocus.current?.focus({ preventScroll: true })) }
  function startTour(id: string) {
    const next = tourSteps(id)
    if (!next.length) return
    setProgress(p => ({ ...p, tourId: id, stepIndex: 0 })); navigate(next[0].route)
  }
  function advance(delta: number) {
    const index = progress.stepIndex + delta
    if (index >= steps.length) {
      setProgress(p => ({ ...p, tourId: null, stepIndex: 0, completedLessonIds: [...new Set([...p.completedLessonIds, p.tourId!])] }))
    } else if (index >= 0) { setProgress(p => ({ ...p, stepIndex: index })); navigate(steps[index].route) }
  }
  useEffect(() => { writeProgress(storage(), userId, progress) }, [progress, userId])
  useEffect(() => {
    const media = window.matchMedia?.('(max-width: 760px)')
    if (!media) return
    const update = () => setMobile(media.matches)
    update(); media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && open) closeGuide()
      const target = event.target as HTMLElement
      if (event.key === '?' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName) && !target?.isContentEditable) { event.preventDefault(); openGuide() }
    }
    document.addEventListener('keydown', key)
    return () => document.removeEventListener('keydown', key)
  }, [open])
  useEffect(() => {
    if (open && peek) resume.current?.focus()
    else if (open && !mobile) panel.current?.focus()
  }, [open, mobile, peek])
  useEffect(() => {
    setTargetVisible(false)
    if (!open || !step) return
    let target: HTMLElement | null = null
    const inspect = () => {
      const next = document.querySelector<HTMLElement>(`[data-guide="${step.anchor}"]`)
      const rect = next?.getBoundingClientRect()
      const style = next && window.getComputedStyle(next)
      const visible = Boolean(next && rect?.width && rect.height && style?.display !== 'none' && style?.visibility !== 'hidden' && matchesRoute(step.route, path))
      setTargetVisible(visible)
      if (visible && next !== target) {
        target?.removeAttribute('data-guide-highlight'); target = next
        target?.setAttribute('data-guide-highlight', '')
        target?.scrollIntoView?.({ block: 'start', behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })
      } else if (!visible) { target?.removeAttribute('data-guide-highlight'); target = null }
    }
    inspect()
    const observer = new MutationObserver(inspect)
    observer.observe(document.body, { childList: true, subtree: true })
    window.addEventListener('resize', inspect)
    const limit = window.setTimeout(() => observer.disconnect(), 8000)
    return () => { observer.disconnect(); clearTimeout(limit); window.removeEventListener('resize', inspect); target?.removeAttribute('data-guide-highlight') }
  }, [open, step, path, retry, peek])

  const value: GuideContextValue = { openGuide, closeGuide, path, progress, startTour, advance,
    stopTour: () => setProgress(p => ({ ...p, tourId: null, stepIndex: 0 })),
    restart: () => { if (progress.tourId) startTour(progress.tourId); else setProgress(emptyProgress()) },
    navigate, targetVisible, retryTarget: () => setRetry(n => n + 1), userId, mobile, peekGuide: () => setPeek(true), navigationNotice, progressReset, dismissReset: () => setProgressReset(false) }
  return <Context.Provider value={value}>
    <div className={`drishti-guide-layout${open && !mobile && !peek ? ' drishti-guide-expanded' : ''}`}><div className="drishti-guide-workspace">{children}</div></div>
    {!open && <button type="button" className="drishti-guide-launch" aria-label="Show guide" onClick={openGuide}><CircleHelp size={18} />Guide</button>}
    {open && peek && <aside className="drishti-guide-peek" aria-label="Tour step"><span>{step?.title ?? 'Drishti Guide'}</span><Button ref={resume} size="sm" onClick={openGuide} aria-label="Resume guide">Resume guide</Button><Button size="sm" variant="ghost" onClick={closeGuide} aria-label="Close guide">×</Button></aside>}
    {mobile ? <Dialog open={open && !peek} onOpenChange={next => next ? openGuide() : closeGuide()}><DialogContent className="drishti-guide-mobile translate-x-0 translate-y-0" showCloseButton={false} onCloseAutoFocus={e => { e.preventDefault(); (peek ? resume.current : returnFocus.current)?.focus({ preventScroll: true }) }}><DialogTitle className="sr-only">Drishti Guide</DialogTitle><DialogDescription className="sr-only">Tours, screen help and operational questions.</DialogDescription><GuidePanel /></DialogContent></Dialog> : open && !peek && <div ref={panel} tabIndex={-1} className="drishti-guide-dock" role="complementary" aria-label="Drishti Guide"><GuidePanel /></div>}
  </Context.Provider>
}
