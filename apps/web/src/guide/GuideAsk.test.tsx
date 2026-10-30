// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { GuideAsk } from './GuideAsk'
import { jsonFetch } from '#/lib/api'
vi.mock('#/lib/api', () => ({ jsonFetch: vi.fn() }))
vi.mock('./GuideProvider', () => ({ useGuide: () => ({ path: '/desk/paper', navigate: vi.fn() }) }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('does not display a late AI answer after the user changes the question', async () => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.releasePointerCapture = vi.fn()
  let finish!: (answer: unknown) => void
  vi.mocked(jsonFetch).mockResolvedValueOnce({ providers: [{ id: 'anthropic', label: 'Personal Anthropic', models: [{ id: 'test-model', label: 'Test model' }] }] })
    .mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  render(<GuideAsk />)
  fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'How do paper trades work?' } })
  fireEvent.click(screen.getByText('Optional AI answers'))
  fireEvent.click(screen.getByLabelText('Enable AI using my personal provider'))
  const provider = await screen.findByRole('combobox', { name: 'Guide personal provider' })
  fireEvent.keyDown(provider, { key: 'ArrowDown' })
  fireEvent.click(await screen.findByRole('option', { name: 'Personal Anthropic' }))
  fireEvent.keyDown(screen.getByRole('combobox', { name: 'Guide model' }), { key: 'ArrowDown' })
  fireEvent.click(await screen.findByRole('option', { name: 'Test model' }))
  fireEvent.click(screen.getByRole('button', { name: 'Ask AI · personal provider usage' }))
  await waitFor(() => expect(jsonFetch).toHaveBeenCalledTimes(2))
  fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'A different question' } })
  await act(async () => { finish({ answer: 'Old answer that belongs to the previous question', sources: ['paper'], mode: 'ai' }) })
  await waitFor(() => expect(screen.queryByText('Old answer that belongs to the previous question')).toBeNull())
  expect(screen.queryByText('Asking your provider…')).toBeNull()
})
