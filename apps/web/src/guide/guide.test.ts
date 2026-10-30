import { describe, expect, it } from 'vitest'
import { featureForRoute, searchHelp, features, tourSteps } from './content'
import { readProgress, writeProgress, emptyProgress } from './state'
import { readFileSync, readdirSync } from 'node:fs'

describe('guide content', () => {
  it('targets the real replay and sizing controls when a plan is open', () => {
    expect(tourSteps('timemachine')[0]).toMatchObject({ route: '/desk/plans/$id', anchor: 'guide-time-machine' })
    expect(tourSteps('sizing')[0]).toMatchObject({ route: '/desk/plans/$id', anchor: 'guide-size-ticket' })
  })
  it('keeps every lesson destination and anchor connected to an actual screen', () => {
    const routes = new URL('../routes/', import.meta.url)
    const source = readdirSync(routes).filter(n => n.endsWith('.tsx')).map(n => readFileSync(new URL(n, routes), 'utf8')).join('\n')
    const paths = [...source.matchAll(/createFileRoute\('([^']+)'\)/g)].map(m => m[1].replace(/\/$/, '') || '/')
    for (const feature of features) for (const step of feature.steps) {
      expect(paths, feature.id).toContain(step.route)
      expect(source, feature.id).toContain(`data-guide="${step.anchor}"`)
    }
  })
  it('starts the paper tour on a useful screen rather than the redirect-only home', () => {
    expect(tourSteps('first-paper')[0].route).toBe('/desk/paper')
  })
  it('uses specific feature routes and handles dynamic objects without inventing ids', () => {
    expect(featureForRoute('/desk/plans/abc?tab=test')?.id).toBe('plans')
    expect(featureForRoute('/desk/performance')?.id).toBe('performance')
    expect(featureForRoute('/settings/models')?.id).toBe('models')
    expect(featureForRoute('/unrelated')).toBeUndefined()
  })
  it('retrieves sourced operational help and abstains on unrelated text', () => {
    expect(searchHelp('paper capital analysis credits', '/desk/paper')[0]?.id).toBe('paper')
    expect(searchHelp('how to bake chocolate cake', '/desk/paper')).toEqual([])
    expect(features.length).toBeGreaterThanOrEqual(16)
  })
  it('finds missing-bar guidance even when the question comes from the ledger', () => {
    expect(searchHelp('why is there no data', '/desk/paper')[0]?.id).toBe('today')
  })
})

describe('guide progress', () => {
  function memory() {
    const data = new Map<string, string>()
    return { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value) } }
  }
  it('isolates users and resumes valid progress', () => {
    const storage = memory()
    const progress = { ...emptyProgress(), tourId: 'paper', stepIndex: 0, completedLessonIds: ['models'] }
    writeProgress(storage, 'alice', progress)
    expect(readProgress(storage, 'alice').completedLessonIds).toEqual(['models'])
    expect(readProgress(storage, 'bob').completedLessonIds).toEqual([])
  })
  it('recovers from corrupt data, old versions and unavailable storage', () => {
    const storage = memory()
    writeProgress(storage, 'alice', { ...emptyProgress(), catalogVersion: 'old' })
    expect(readProgress(storage, 'alice')).toEqual(emptyProgress())
    const broken = { getItem() { throw Error('denied') }, setItem() { throw Error('denied') } }
    expect(readProgress(broken, 'alice')).toEqual(emptyProgress())
    expect(() => writeProgress(broken, 'alice', emptyProgress())).not.toThrow()
  })
})
