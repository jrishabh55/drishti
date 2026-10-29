// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup,fireEvent,render,screen } from '@testing-library/react'
import { TicketCard } from './TicketCard'
import { messagingApi } from '#/lib/messaging-api'
import { api } from '#/lib/api'
import type { Ticket } from '#/lib/desk-types'
vi.mock('#/lib/api',()=>({api:{desk:{confirmTicket:vi.fn(),tickets:vi.fn().mockResolvedValue([])}}}))
vi.mock('#/lib/messaging-api',()=>({messagingApi:{propose:vi.fn()}}))
afterEach(()=>{cleanup();vi.clearAllMocks()})
it('sends the exact ready ticket for paper approval without confirming it',async()=>{
  vi.mocked(messagingApi.propose).mockResolvedValue({state:'pending',expires_at:1,message:'Waiting for your paper-trade decision.'})
  const tk={id:'ticket1',status:'ready',words:'Buy 1 RELIANCE',sizing:null,verdict:null,order:{why:'ORB'},verdict_warnings:[]} as unknown as Ticket
  render(<TicketCard ticket={tk} publisherKey={null} onChange={vi.fn()} />)
  fireEvent.click(screen.getByRole('button',{name:'Send for paper approval'}))
  expect(await screen.findByText('Waiting for your paper-trade decision.')).toBeTruthy()
  expect(messagingApi.propose).toHaveBeenCalledWith('ticket1')
  expect(api.desk.confirmTicket).not.toHaveBeenCalled()
})
