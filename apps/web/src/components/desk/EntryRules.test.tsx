// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { EntryRules } from './EntryRules'
afterEach(cleanup)
it('requires a positive custom level and applies the selected interval and volume filter', async () => {
  const apply=vi.fn().mockResolvedValue(undefined)
  render(<EntryRules onApply={apply} />)
  fireEvent.click(screen.getByText(/Entry rules/))
  fireEvent.change(screen.getByLabelText('Setup'),{target:{value:'level'}})
  expect((screen.getByRole('button',{name:'Apply entry rules'}) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('Level ₹'),{target:{value:'1200'}})
  fireEvent.change(screen.getByLabelText('Bar interval'),{target:{value:'1m'}})
  fireEvent.change(screen.getByLabelText('Minimum volume × opening range (optional)'),{target:{value:'1.5'}})
  fireEvent.click(screen.getByRole('button',{name:'Apply entry rules'}))
  await waitFor(()=>expect(apply).toHaveBeenCalledWith({setup:'level',interval:'1m',level:1200,min_volume_x:1.5}))
})
