// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MessagingSettings } from './MessagingSettings'
import { messagingApi } from '#/lib/messaging-api'

vi.mock('#/lib/messaging-api', () => ({ messagingApi: {
  status:vi.fn(), setup:vi.fn(), pair:vi.fn(), confirm:vi.fn(), refresh:vi.fn(),
  preferences:vi.fn(), recipient:vi.fn(), test:vi.fn(), disconnect:vi.fn(), propose:vi.fn(),
} }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
const base = {configured:false,connected:false,verified:false,alerts:false,approvals:false,encryption_available:true}

it('shows the server default and provides guided Telegram setup without a manual chat id', async () => {
  vi.mocked(messagingApi.status).mockImplementation(async ch => ({...base,server_url:ch==='whatsapp'?'https://evo.example':''}))
  render(<MessagingSettings />)
  expect(await screen.findByDisplayValue('https://evo.example')).toBeTruthy()
  expect(screen.queryByLabelText('Chat ID')).toBeNull()
  expect(screen.getByRole('link',{name:'Open BotFather'})).toBeTruthy()
  expect((screen.getByRole('button',{name:'Test WhatsApp'}) as HTMLButtonElement).disabled).toBe(true)
})

it('renders private QR and phone pairing states then verifies the recipient', async () => {
  vi.mocked(messagingApi.status).mockImplementation(async ch => ({...base,configured:ch==='whatsapp',server_url:'https://evo.example'}))
  vi.mocked(messagingApi.pair).mockResolvedValue({...base,configured:true,qr:'data:image/png;base64,AA==',pairing_code:'ABCD1234',pairing_expires_at:Date.now()/1000+90})
  render(<MessagingSettings />)
  fireEvent.click(await screen.findByRole('button',{name:'Generate WhatsApp QR'}))
  expect(await screen.findByAltText('Scan in WhatsApp Linked devices')).toHaveProperty('src','data:image/png;base64,AA==')
  expect(screen.getByText('ABCD1234')).toBeTruthy()
  expect(messagingApi.pair).toHaveBeenCalledWith('whatsapp','')
})

it('clears bot credentials and offers explicit confirmation of the linked Telegram identity', async () => {
  vi.mocked(messagingApi.status).mockResolvedValue(base)
  vi.mocked(messagingApi.setup).mockResolvedValue({...base,configured:true,connected:true,bot_username:'my_bot'})
  vi.mocked(messagingApi.pair).mockResolvedValue({...base,configured:true,connected:true,pair_url:'https://t.me/my_bot?start=abc',pending_identity:'Rishabh (42)'})
  render(<MessagingSettings />)
  const token=await screen.findByLabelText('Telegram bot token')
  fireEvent.change(token,{target:{value:'123:secret'}})
  fireEvent.click(screen.getByRole('button',{name:'Save Telegram bot'}))
  await waitFor(()=>expect(token).toHaveProperty('value',''))
  fireEvent.click(screen.getByRole('button',{name:'Create Telegram connection link'}))
  expect(await screen.findByRole('link',{name:'Open Telegram to connect'})).toHaveProperty('href','https://t.me/my_bot?start=abc')
  expect(screen.getByText('Rishabh (42)')).toBeTruthy()
  expect(screen.getByRole('button',{name:'Confirm Telegram account'})).toBeTruthy()
})

it('explains setup failure and leaves approvals disabled until inbound verification', async () => {
  vi.mocked(messagingApi.status).mockResolvedValue({...base,configured:true,connected:true,verified:true,inbound_verified:false})
  vi.mocked(messagingApi.refresh).mockRejectedValue(new Error('Evolution is unavailable'))
  render(<MessagingSettings />)
  fireEvent.click(await screen.findByRole('button',{name:'Refresh WhatsApp connection'}))
  expect(await screen.findByText('Evolution is unavailable')).toBeTruthy()
  expect((screen.getByRole('switch',{name:'WhatsApp paper approvals'}) as HTMLButtonElement).disabled).toBe(true)
})

it('retains a visible cleanup retry after disconnect failed at the provider', async () => {
  vi.mocked(messagingApi.status).mockImplementation(async ch=>({...base,cleanup_pending:ch==='whatsapp',last_error:ch==='whatsapp'?'Remote cleanup failed':null}))
  render(<MessagingSettings />)
  expect(await screen.findByRole('button',{name:'Retry WhatsApp cleanup'})).toHaveProperty('disabled',false)
  expect(screen.getByRole('button',{name:'Save WhatsApp server'})).toHaveProperty('disabled',true)
})
