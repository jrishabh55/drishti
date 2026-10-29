// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ProviderKeySetup } from './ProviderKeySetup'
import { jsonFetch } from '#/lib/api'
vi.mock('#/lib/api',()=>({jsonFetch:vi.fn()}))
afterEach(cleanup)
beforeEach(()=>vi.mocked(jsonFetch).mockReset())
it('clears the write-only key after saving and shows only the connection suffix',async()=>{
  let configured=false
  vi.mocked(jsonFetch).mockImplementation(async (_path,init)=>{
    if(init?.method==='PUT'){configured=true;return {} as never}
    return {providers:[{provider:'anthropic',configured,last4:configured?'1234':null,encryption_available:true}],administrator_providers:[]} as never
  })
  render(<ProviderKeySetup provider="anthropic" />)
  await screen.findByText('Use your own provider API key.')
  const input=screen.getByLabelText('anthropic API key') as HTMLInputElement
  fireEvent.change(input,{target:{value:'private-test-key-1234'}})
  fireEvent.click(screen.getByRole('button',{name:'Save key'}))
  await screen.findByText('Connected · ending 1234')
  expect(input.value).toBe('')
  expect(screen.queryByText('private-test-key-1234')).toBeNull()
  await waitFor(()=>expect(jsonFetch).toHaveBeenCalledWith('/keys/providers/anthropic',expect.objectContaining({method:'PUT'})))
})
