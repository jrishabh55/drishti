import { jsonFetch } from './api'

export type Channel = 'whatsapp' | 'telegram'
export type MessagingStatus = {
  id?: string; configured: boolean; connected?: boolean; verified?: boolean; inbound_verified?: boolean
  alerts?: boolean; approvals?: boolean
  /** STATUS / PERFORMANCE / confirmed pause-resume from this chat. */
  supervision?: boolean; daily_summary?: boolean
  /** Cards for live signals that link to the app's typed confirmation (no approve). */
  live_links?: boolean
  encryption_available: boolean; last_error?: string | null
  server_url?: string; server_configured?: boolean; credential_source?: string
  phone?: string; recipient?: string; bot_username?: string; pending_identity?: string
  qr?: string; pairing_code?: string; pairing_expires_at?: number; pair_url?: string; cleanup_pending?: boolean
}
const post = <T,>(path: string, body: object = {}) => jsonFetch<T>(path, {method:'POST', body:JSON.stringify(body)})
export type SupervisionPrefs = { supervision?: boolean; daily_summary?: boolean; live_links?: boolean }
export const messagingApi = {
  status: (ch: Channel) => jsonFetch<MessagingStatus>(`/messaging/${ch}`),
  setup: (ch: Channel, body: object) => jsonFetch<MessagingStatus>(`/messaging/${ch}`, {method:'PUT',body:JSON.stringify(body)}),
  pair: (ch: Channel, phone = '') => post<MessagingStatus>(`/messaging/${ch}/pair`, {phone}),
  confirm: (ch: Channel, code = '') => post<MessagingStatus>(`/messaging/${ch}/confirm`, {code}),
  refresh: (ch: Channel) => post<MessagingStatus>(`/messaging/${ch}/refresh`),
  recipient: (phone: string) => post<{sent:boolean}>('/messaging/whatsapp/recipient', {phone}),
  /** Omitted supervision prefs stay unchanged on the server. */
  preferences: (ch: Channel, alerts: boolean, approvals: boolean, extra: SupervisionPrefs = {}) => jsonFetch<MessagingStatus>(`/messaging/${ch}/preferences`, {method:'PUT',body:JSON.stringify({alerts,approvals,...extra})}),
  test: (ch: Channel) => post<MessagingStatus>(`/messaging/${ch}/test`),
  disconnect: (ch: Channel) => jsonFetch<MessagingStatus>(`/messaging/${ch}`, {method:'DELETE'}),
  propose: (ticket_id: string) => post<{state:string; message:string; expires_at:number}>('/messaging/proposals/paper', {ticket_id}),
}
