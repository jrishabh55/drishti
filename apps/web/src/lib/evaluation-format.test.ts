import { describe, expect, it } from 'vitest'
import { cellLabel, linePoints, signedFrac } from './evaluation-format'
import type { BatchCell } from './evaluation-api'

const cell = (over: Partial<BatchCell>): BatchCell => ({
  ticker: 'RELIANCE.NS', date: '2026-01-05', run_id: 'r', phase: 'running', rating: null,
  run_status: 'running', error: null, score: null, ...over,
})

describe('evaluation format', () => {
  it('scales a series into the box, top = max', () => {
    expect(linePoints([0, 10], 100, 50, 0)).toBe('0.0,50.0 100.0,0.0')
    expect(linePoints([5, 5, 5], 100, 50, 0)).toBe('0.0,50.0 50.0,50.0 100.0,50.0')
    expect(linePoints([], 100, 50)).toBe('')
  })

  it('formats signed fractions', () => {
    expect(signedFrac(0.0123)).toBe('+1.23%')
    expect(signedFrac(-0.5, 0)).toBe('-50%')
    expect(signedFrac(null)).toBe('—')
  })

  it('labels cells without inventing outcomes', () => {
    expect(cellLabel(cell({ phase: 'pending' }))).toMatch(/pending/)
    expect(cellLabel(cell({ phase: 'scored', score: { status: 'scored', alpha: 0.02, benchmark: '^NSEI' } })))
      .toBe('alpha +2.00% vs ^NSEI')
    expect(cellLabel(cell({ phase: 'rejected', error: 'out of credits' }))).toBe('out of credits')
    expect(cellLabel(cell({ phase: 'queued' }))).toBe('waiting its turn')
    expect(cellLabel(cell({ phase: 'queued', retry_at: '2026-10-29T10:00:00Z', error: 'credit check unavailable' })))
      .toMatch(/^retrying after .*: credit check unavailable$/)
    expect(cellLabel(cell({ phase: 'submitting' }))).toMatch(/starting/)
  })
})
