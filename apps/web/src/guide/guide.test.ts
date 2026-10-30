import { describe, expect, it } from 'vitest'
import { featureForRoute, searchHelp, features, tourSteps } from './content'
import { readProgress, writeProgress, emptyProgress } from './state'

describe('guide content', () => {
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
