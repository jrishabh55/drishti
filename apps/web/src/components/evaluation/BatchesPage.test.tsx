// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { BatchesPage } from '#/routes/evaluation.index'
import { api } from '#/lib/api'
import { evaluationApi } from '#/lib/evaluation-api'

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (opts: unknown) => opts,
  useNavigate: () => vi.fn(),
  Link: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}))
vi.mock('#/components/shared/Topbar', () => ({ Topbar: () => null }))
vi.mock('#/lib/api', () => ({ api: { config: vi.fn() } }))
vi.mock('#/lib/evaluation-api', () => ({ evaluationApi: {
  listBatches: vi.fn(), quote: vi.fn(), startBatch: vi.fn(),
} }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('uses the actual config value fields for each independent analyst and quotes before starting', async () => {
  // Exact /api/config analyst shape (value, label), rather than a fabricated key field.
  vi.mocked(api.config).mockResolvedValue({
    analysts: [
      { value: 'market', label: 'Market Analyst' }, { value: 'social', label: 'Social Media Analyst' },
      { value: 'news', label: 'News Analyst' }, { value: 'fundamentals', label: 'Fundamentals Analyst' },
    ],
    providers: [{ key: 'openai', label: 'OpenAI' }],
    models_by_provider: { openai: [{ id:'gpt-test', label:'Test model' }] },
    research_depths: [{value:1,label:'Shallow'}], output_languages: [], default_ticker:'SPY',
  })
  vi.mocked(evaluationApi.listBatches).mockResolvedValue([])
  vi.mocked(evaluationApi.quote).mockResolvedValue({
    cells:1, max_credits:1, credits_per_cell:1, credits_available:5,
    grid:[{ticker:'RELIANCE.NS',date:'2026-01-05'}],
  })
  render(<BatchesPage />)
  const market = await screen.findByRole('checkbox', { name:'Market Analyst' }) as HTMLInputElement
  const social = screen.getByRole('checkbox', { name:'Social Media Analyst' }) as HTMLInputElement
  expect(market.checked).toBe(true)
  expect(social.checked).toBe(false)
  fireEvent.click(social)
  expect(market.checked).toBe(true)
  expect(social.checked).toBe(true)
  fireEvent.click(screen.getByRole('button', {name:'Check cost'}))
  await waitFor(() => expect(evaluationApi.quote).toHaveBeenCalledTimes(1))
  const body = vi.mocked(evaluationApi.quote).mock.calls[0][0]
  expect(body.settings.analysts).toEqual(['market','news','fundamentals','social'])
  expect(evaluationApi.startBatch).not.toHaveBeenCalled()
  expect((await screen.findByRole('button', {name:'Start batch'})) as HTMLButtonElement).toHaveProperty('disabled', true)
})
