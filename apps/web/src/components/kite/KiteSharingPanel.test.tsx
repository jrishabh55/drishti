// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { KiteSharingPanel } from './KiteSharingPanel'
const calls = vi.hoisted(() => ({ sharing: vi.fn(), grantData: vi.fn(), revokeData: vi.fn(), selectDataSource: vi.fn() }))
vi.mock('#/lib/kite-api', () => ({ kite: calls, kiteError: (e: Error) => e.message }))
const base = { user_id: 'user_a', enabled: true, grants: [], received: [], selected_owner_id: null }
beforeEach(() => { vi.resetAllMocks(); calls.sharing.mockResolvedValue(base) })
afterEach(cleanup)

describe('Kite data sharing', () => {
  it('requires an explicit account grant and lets the owner revoke it', async () => {
    render(<KiteSharingPanel configured />)
    expect((await screen.findByLabelText('Your Drishti account ID') as HTMLInputElement).value).toBe('user_a')
    fireEvent.change(screen.getByLabelText('Recipient account ID'), { target: { value: 'user_b' } })
    fireEvent.change(screen.getByLabelText('Account label'), { target: { value: 'Experiment B' } })
    calls.sharing.mockResolvedValue({ ...base, grants: [{ owner_id: 'user_a', recipient_id: 'user_b', label: 'Experiment B', created_at: '' }] })
    fireEvent.click(screen.getByRole('button', { name: 'Grant data access' }))
    expect(await screen.findByText('Experiment B')).toBeTruthy()
    expect(calls.grantData).toHaveBeenCalledWith('user_b', 'Experiment B')
    calls.sharing.mockResolvedValue(base)
    fireEvent.click(screen.getByRole('button', { name: 'Revoke access for user_b' }))
    await waitFor(() => expect(screen.queryByText('Experiment B')).toBeNull())
    expect(calls.revokeData).toHaveBeenCalledWith('user_b')
  })

  it('lets a recipient choose a granted source without needing broker credentials', async () => {
    calls.sharing.mockResolvedValue({ ...base, user_id: 'user_b', received: [{ owner_id: 'user_a', recipient_id: 'user_b', label: '', created_at: '' }] })
    render(<KiteSharingPanel configured={false} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Use connection from user_a' }))
    await waitFor(() => expect(calls.selectDataSource).toHaveBeenCalledWith('user_a'))
    expect((screen.getByRole('button', { name: 'Grant data access' }) as HTMLButtonElement).disabled).toBe(true)
  })
})
