// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { PaperPortfolioOptIn } from './PaperPortfolioOptIn'
import { evaluationApi } from '#/lib/evaluation-api'

vi.mock('#/lib/evaluation-api', () => ({ evaluationApi: { portfolioSnapshot: vi.fn() } }))
afterEach(() => { cleanup(); vi.mocked(evaluationApi.portfolioSnapshot).mockReset() })

function Host() {
  const [on, setOn] = useState(false)
  return <><PaperPortfolioOptIn checked={on} onCheckedChange={setOn} /><span>opted:{String(on)}</span></>
}

describe('PaperPortfolioOptIn', () => {
  it('is off by default and previews the book only once opted in', async () => {
    vi.mocked(evaluationApi.portfolioSnapshot).mockResolvedValue({
      cash: 70050, currency: 'INR', positions: [{ ticker: 'RELIANCE.NS', quantity: 20, average_price: 1510 }],
    })
    render(<Host />)
    expect(screen.getByText('opted:false')).toBeTruthy()
    expect(evaluationApi.portfolioSnapshot).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('switch', { name: 'Use my Default paper portfolio' }))
    expect(screen.getByText('opted:true')).toBeTruthy()
    const book = await screen.findByTestId('paper-book')
    expect(book.textContent).toContain('RELIANCE.NS 20')
    expect(book.textContent).toContain('70,050')
  })

  it('says so when the book cannot be read', async () => {
    vi.mocked(evaluationApi.portfolioSnapshot).mockRejectedValue(new Error('503'))
    render(<PaperPortfolioOptIn checked onCheckedChange={() => {}} />)
    expect(await screen.findByText(/Couldn't read your paper book: 503/)).toBeTruthy()
  })
})
