import { describe, expect, it } from 'vitest'
import { filterFromScanner, filterUnchanged } from './FilterPanel'
import type { ScannerSummary } from '#/lib/scanner-types'

const scanner = { id: 'prebuilt:test', name: 'Breakout', description: 'test', prebuilt: true,
  definition: { logic: 'AND', children: [
    { timeframe: '1d', left: { field: 'close' }, op: '>', right: { const: 100 } },
  ] },
} as ScannerSummary

describe('saved scanner strategy eligibility', () => {
  it('recognizes a freshly loaded saved definition despite display formatting', () => {
    const filter = filterFromScanner(scanner, { collapsed: true })
    expect(filter.json).not.toBe(filter.originalJson)
    expect(filterUnchanged(filter)).toBe(true)
  })
  it('does not use the saved identity when the current builder was edited', () => {
    const filter = filterFromScanner(scanner)
    filter.state!.groups[0].rows[0].right = { kind: 'const', value: 200 }
    expect(filterUnchanged(filter)).toBe(false)
  })
  it('refuses invalid advanced JSON and an absent filter', () => {
    const filter = filterFromScanner(scanner)
    filter.state = null; filter.json = '{'
    expect(filterUnchanged(filter)).toBe(false)
    expect(filterUnchanged(null)).toBe(false)
  })
})
