import { jsonFetch } from './api'

export type Channel = 'whatsapp' | 'telegram'
export type MessagingStatus = {
  id?: string; configured: boolean; connected?: boolean; verified?: boolean; inbound_verified?: boolean
  alerts?: boolean; approvals?: boolean; encryption_available: boolean; last_error?: string | null
  server_url?: string; server_configured?: boolean; credential_source?: string
  phone?: string; recipient?: string; bot_username?: string; pending_identity?: string
  qr?: string; pairing_code?: string; pairing_expires_at?: number; pair_url?: string; cleanup_pending?: boolean
}
const post = <T,>(path: string, body: object = {}) => jsonFetch<T>(path, {method:'POST', body:JSON.stringify(body)})
export const messagingApi = {
  status: (ch: Channel) => jsonFetch<MessagingStatus>(`/messaging/${ch}`),
  setup: (ch: Channel, body: object) => jsonFetch<MessagingStatus>(`/messaging/${ch}`, {method:'PUT',body:JSON.stringify(body)}),
  pair: (ch: Channel, phone = '') => post<MessagingStatus>(`/messaging/${ch}/pair`, {phone}),
  confirm: (ch: Channel, code = '') => post<MessagingStatus>(`/messaging/${ch}/confirm`, {code}),
  refresh: (ch: Channel) => post<MessagingStatus>(`/messaging/${ch}/refresh`),
  recipient: (phone: string) => post<{sent:boolean}>('/messaging/whatsapp/recipient', {phone}),
  preferences: (ch: Channel, alerts: boolean, approvals: boolean) => jsonFetch<MessagingStatus>(`/messaging/${ch}/preferences`, {method:'PUT',body:JSON.stringify({alerts,approvals})}),
  test: (ch: Channel) => post<MessagingStatus>(`/messaging/${ch}/test`),
  disconnect: (ch: Channel) => jsonFetch<MessagingStatus>(`/messaging/${ch}`, {method:'DELETE'}),
  propose: (ticket_id: string) => post<{state:string; message:string; expires_at:number}>('/messaging/proposals/paper', {ticket_id}),
}
