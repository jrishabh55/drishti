import { describe, expect, it } from 'vitest'
import { ApiError } from './api'
import { confirmWords, kiteError, kiteRedirectParams } from './kite-api'

describe('kiteRedirectParams', () => {
  it('reads Kite redirect params including our state', () => {
    expect(kiteRedirectParams('?action=login&type=login&status=success&request_token=abc123&state=n0nce'))
      .toEqual({ request_token: 'abc123', state: 'n0nce', status: 'success' })
  })
  it('ignores ordinary page loads and redirects missing state', () => {
    expect(kiteRedirectParams('')).toBeNull()
    expect(kiteRedirectParams('?request_token=abc123')).toBeNull()
  })
})

describe('kiteError', () => {
  it('shows every refusal reason from the order gates', () => {
    const err = new ApiError(409, 'Conflict', { code: 'refused', message: 'x', reasons: ['not armed', 'past flat-by'] }, '')
    expect(kiteError(err)).toBe('not armed · past flat-by')
  })
  it('falls back to the message', () => {
    expect(kiteError(new ApiError(503, 'x', { code: 'encryption_unconfigured', message: 'set the secret' }, ''))).toBe('set the secret')
  })
})

it('confirm words match the server', () => {
  expect(confirmWords(50, 'SBIN')).toBe('BUY 50 SBIN')
})
