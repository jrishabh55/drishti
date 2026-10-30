// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { GuideProvider, GuideButton, useGuide } from './GuideProvider'
const router = vi.hoisted(() => ({ path: '/desk/paper', navigate: vi.fn() }))
vi.mock('@tanstack/react-router', () => ({ useRouterState: ({ select }: { select: (s: unknown) => unknown }) => select({ location: { pathname: router.path } }), useNavigate: () => router.navigate }))
afterEach(() => { cleanup(); localStorage.clear(); router.path = '/desk/paper'; vi.restoreAllMocks(); vi.unstubAllGlobals() })

function App({ user = 'alice' }: { user?: string }) {
  return <GuideProvider userId={user}><GuideButton /><main><h1 data-guide="guide-paper">Paper ledger</h1><section data-guide="guide-trading-workflow"><h2>Trading workflow</h2></section><button onClick={() => { throw Error('must not click') }}>Confirm trade</button></main></GuideProvider>
}
describe('contextual guide', () => {
  it('explains a reset after the lesson catalog changes', () => {
    localStorage.setItem('drishti-guide:alice', JSON.stringify({ catalogVersion: 'old', tourId: 'paper', stepIndex: 0, completedLessonIds: [] }))
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Drishti Guide' }))
    expect(screen.getByText(/previous tour progress has been reset/)).toBeTruthy()
  })
  it('uses a neutral fallback for a screen without specific help', () => {
    router.path = '/unlisted'
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Drishti Guide' }))
    expect(screen.getByRole('heading', { name: 'Explore Drishti' })).toBeTruthy()
    router.path = '/desk/paper'
  })
  it.each([true, false])('reveals the page while retaining the tour highlight (mobile=%s)', mobile => {
    router.path = '/desk/start'
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: mobile, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 100, height: 30, x: 0, y: 100, top: 100, left: 0, right: 100, bottom: 130, toJSON() {} })
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Drishti Guide' }))
    fireEvent.click(screen.getByRole('tab', { name: 'Tours' }))
    fireEvent.click(screen.getByRole('button', { name: 'Start first paper trade tour' }))
    const showPage = screen.getByRole('button', { name: 'Show page' })
    showPage.focus()
    fireEvent.click(showPage)
    expect(screen.getByRole('button', { name: 'Resume guide' })).toBeTruthy()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Resume guide' }))
    expect(screen.getByRole('heading', { name: 'Trading workflow' }).parentElement?.hasAttribute('data-guide-highlight')).toBe(true)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.queryByRole('complementary', { name: 'Drishti Guide' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Resume guide' }))
    expect(screen.getByRole('heading', { name: 'Start here' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Resume guide' })).toBeNull()
  })
  it('falls back to a real screen and explains a missing dynamic object', () => {
    function MissingReport() { const guide = useGuide()!; return <button onClick={() => guide.navigate('/runs/$id')}>Open report lesson</button> }
    render(<GuideProvider userId="alice"><GuideButton /><MissingReport /></GuideProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Open Drishti Guide' }))
    fireEvent.click(screen.getByRole('button', { name: 'Open report lesson' }))
    expect(router.navigate).toHaveBeenCalledWith({ to: '/analyse' })
    expect(screen.getByText(/Choose an existing report/)).toBeTruthy()
  })
  it('opens relevant help, answers written questions and closes with Escape', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Drishti Guide' }))
    expect(screen.getByRole('heading', { name: 'Paper ledger', level: 3 })).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: 'Ask' }))
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'paper capital analysis credits' } })
    fireEvent.click(screen.getByRole('button', { name: 'Find help' }))
    expect(screen.getByText('Built-in help')).toBeTruthy()
    expect(screen.getByText(/Paper capital funds simulated trades/)).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('heading', { name: 'Drishti Guide' })).toBeNull()
  })
  it('advances lessons with navigation only and tolerates missing anchors', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Drishti Guide' }))
    fireEvent.click(screen.getByRole('tab', { name: 'Tours' }))
    fireEvent.click(screen.getByRole('button', { name: 'Start first paper trade tour' }))
    expect(router.navigate).toHaveBeenCalledWith({ to: '/desk/start' })
    fireEvent.click(screen.getByRole('button', { name: 'Next step' }))
    expect(router.navigate).toHaveBeenLastCalledWith({ to: '/desk/settings' })
    expect(screen.getByText(/target is not visible/i)).toBeTruthy()
  })
  it('clears question state and progress when identity changes', () => {
    const { rerender } = render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Drishti Guide' }))
    fireEvent.click(screen.getByRole('tab', { name: 'Ask' }))
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'alice private question' } })
    rerender(<App user="bob" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Drishti Guide' }))
    fireEvent.click(screen.getByRole('tab', { name: 'Ask' }))
    expect((screen.getByLabelText('Your question') as HTMLInputElement).value).toBe('')
  })
})
