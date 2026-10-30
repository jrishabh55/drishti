// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { GuideProvider, GuideButton } from './GuideProvider'
const router = vi.hoisted(() => ({ path: '/desk/paper', navigate: vi.fn() }))
vi.mock('@tanstack/react-router', () => ({ useRouterState: ({ select }: { select: (s: unknown) => unknown }) => select({ location: { pathname: router.path } }), useNavigate: () => router.navigate }))
afterEach(() => { cleanup(); localStorage.clear(); vi.clearAllMocks() })

function App({ user = 'alice' }: { user?: string }) {
  return <GuideProvider userId={user}><GuideButton /><main><h1 data-guide="guide-paper">Paper ledger</h1><button onClick={() => { throw Error('must not click') }}>Confirm trade</button></main></GuideProvider>
}
describe('contextual guide', () => {
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
    expect(router.navigate).toHaveBeenCalledWith({ to: '/desk/paper' })
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
