import { useState } from 'react'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { features, featureForRoute, tourSteps } from './content'
import { useGuide } from './GuideProvider'
import { GuideAsk } from './GuideAsk'
import { GuideVideo } from './GuideVideo'

export function GuidePanel() {
  const guide = useGuide()!
  const [tab, setTab] = useState<'screen' | 'tours' | 'ask'>('screen')
  const [filter, setFilter] = useState('')
  const feature = featureForRoute(guide.path) ?? features[0]
  const steps = guide.progress.tourId ? tourSteps(guide.progress.tourId) : []
  const step = steps[guide.progress.stepIndex]
  return <>
    <header className="drishti-guide-header"><div><h2>Drishti Guide</h2><p>Your next step, explained</p></div><Button variant="ghost" size="sm" onClick={guide.closeGuide} aria-label="Close guide">×</Button></header>
    <div role="tablist" aria-label="Guide sections" className="drishti-guide-tabs">{(['screen', 'tours', 'ask'] as const).map(id => <button key={id} role="tab" id={`guide-tab-${id}`} aria-controls="guide-tab-content" aria-selected={tab === id} onClick={() => setTab(id)}>{id === 'screen' ? 'This screen' : id === 'tours' ? 'Tours' : 'Ask'}</button>)}</div>
    <div id="guide-tab-content" role="tabpanel" aria-labelledby={`guide-tab-${tab}`} className="drishti-guide-body">
      {tab === 'screen' && <><span className="drishti-guide-kicker">YOU ARE HERE</span><h3>{feature.title}</h3><p>{feature.summary}</p><p>{feature.body}</p><Button onClick={() => { guide.startTour(feature.id); setTab('tours') }}>Take this lesson</Button><div className="drishti-guide-callout">Paper capital and analysis credits are separate. The guide explains controls; you decide when to run analysis or confirm a ticket.</div>{feature.faqs.map(faq => <details key={faq.question}><summary>{faq.question}</summary><p>{faq.answer}</p></details>)}<GuideVideo chapterId={feature.chapter} /></>}
      {tab === 'tours' && <>
        {step ? <div aria-live="polite"><span className="drishti-guide-kicker">{guide.progress.stepIndex + 1} / {steps.length} · {guide.progress.tourId === 'first-paper' ? 'FIRST PAPER TRADE' : 'FEATURE LESSON'}</span><progress value={guide.progress.stepIndex + 1} max={steps.length} aria-label="Tour progress" /><h3>{step.title}</h3><p>{step.body}</p>{!guide.targetVisible && <div className="drishti-guide-callout">The target is not visible yet. {step.fallback}<div className="drishti-guide-actions"><Button variant="ghost" size="sm" onClick={guide.retryTarget}>Retry target</Button><Button variant="ghost" size="sm" onClick={() => guide.navigate(step.route)}>Go to screen</Button></div></div>}<p className="drishti-guide-note">Follow the explanation, then use the actual controls yourself. On a small screen, close the guide to reveal the page; your place is saved.</p><div className="drishti-guide-actions"><Button variant="outline" size="sm" disabled={guide.progress.stepIndex === 0} onClick={() => guide.advance(-1)}>Back</Button><Button size="sm" aria-label="Next step" onClick={() => guide.advance(1)}>{guide.progress.stepIndex === steps.length - 1 ? 'Complete lesson' : 'Next step'}</Button></div><div className="drishti-guide-actions"><Button variant="ghost" size="sm" onClick={() => guide.advance(1)}>Skip step</Button><Button variant="ghost" size="sm" onClick={guide.closeGuide}>Pause</Button><Button variant="ghost" size="sm" onClick={guide.restart}>Restart tour</Button><Button variant="ghost" size="sm" onClick={guide.stopTour}>All lessons</Button></div></div> : <><h3>Your first paper trade</h3><p>Learn limits, research, planning, risk checks, Telegram approval and reviewing results. Completing a lesson records learning progress, not a trading action.</p><Button aria-label="Start first paper trade tour" onClick={() => guide.startTour('first-paper')}>Start first paper trade tour</Button></>}
        <h3 className="drishti-guide-library-title">Feature library <small>{guide.progress.completedLessonIds.length} completed</small></h3><Input aria-label="Search guide lessons" placeholder="Search lessons…" value={filter} onChange={e => setFilter(e.target.value)} />
        <div className="drishti-guide-lessons">{features.filter(f => `${f.title} ${f.summary} ${f.keywords.join(' ')}`.toLowerCase().includes(filter.toLowerCase())).map(f => <button key={f.id} onClick={() => guide.startTour(f.id)}><span>{guide.progress.completedLessonIds.includes(f.id) ? '✓ ' : ''}{f.title}</span><small>{f.summary}</small></button>)}</div>
      </>}
      {tab === 'ask' && <GuideAsk />}
    </div>
    <footer className="drishti-guide-footer">Operational help · no automatic trades</footer>
  </>
}
