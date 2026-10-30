import { createFileRoute } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import { DeskFooter, DeskNav } from '#/components/desk/DeskNav'
import { Topbar } from '#/components/shared/Topbar'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { istTime } from '#/lib/desk-format'
import { kite, kiteError, kiteRedirectParams, type KiteStatus } from '#/lib/kite-api'
import { Card, Pill, sessionPill } from '#/components/kite/KiteBits'

export const Route = createFileRoute('/desk/connection')({
  ssr: false,
  component: ConnectionPage,
})

function ConnectionPage() {
  const [st, setSt] = useState<KiteStatus | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [symbols, setSymbols] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const handled = useRef(false)

  const load = useCallback(() => kite.status().then(setSt).catch((e) => setError(kiteError(e))), [])

  const run = useCallback(async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true); setError(null); setMsg(null)
    try {
      await fn()
      if (ok) setMsg(ok)
    } catch (e) {
      setError(kiteError(e))
    } finally {
      setBusy(false)
      void load()
    }
  }, [load])

  useEffect(() => {
    // Kite redirects here with request_token + state after the daily login.
    const back = kiteRedirectParams(window.location.search)
    if (back && !handled.current) {
      handled.current = true
      window.history.replaceState(null, '', window.location.pathname)
      void run(() => kite.callback(back.request_token, back.state, back.status),
        'Connected to Zerodha for today.')
    } else {
      void load()
    }
    const t = window.setInterval(() => void load(), 10_000)
    return () => window.clearInterval(t)
  }, [load, run])

  const redirectUrl = st ? `${window.location.origin}${st.redirect_path}` : ''
  const sp = st ? sessionPill(st.session) : null

  return (
    <div className="min-h-screen">
      <Topbar state="idle" />
      <main data-guide="guide-broker" className="mx-auto w-full max-w-[1100px] space-y-6 px-6 py-4">
        <h1 className="text-2xl font-bold">Zerodha connection</h1>
        <DeskNav />
        {error && <p className="text-sm text-[var(--err)]">{error}</p>}
        {msg && <p className="text-sm text-[var(--ok)]">{msg}</p>}
        {st && (
          <div className="grid gap-4 md:grid-cols-2">
            <Card title="1 · Your Kite Connect app"
              hint="Each user connects their own Kite Connect app (developers.kite.trade). Keys are encrypted on the server and never shown again.">
              {!st.encryption_configured ? (
                <p className="text-sm text-[var(--err)]">
                  Credential storage is not configured on this server
                  (WEBAPP_KEY_ENCRYPTION_SECRET is unset). Ask the admin to set it; nothing can be
                  saved until then.
                </p>
              ) : (
                <>
                  <p className="text-sm text-[var(--text-2)]">
                    Set your app&apos;s redirect URL to{' '}
                    <code className="es-mono break-all text-[var(--text-1)]">{redirectUrl}</code>
                  </p>
                  {st.app_unreadable && (
                    <p className="text-sm text-[var(--err)]">
                      Your saved keys can&apos;t be decrypted (the server&apos;s secret changed). Save them again.
                    </p>
                  )}
                  {st.app_configured && (
                    <p className="text-sm">App saved · api_key ends in{' '}
                      <span className="es-mono">{st.api_key_last4}</span></p>
                  )}
                  <form className="space-y-2" onSubmit={(e) => {
                    e.preventDefault()
                    void run(() => kite.saveApp(apiKey, apiSecret).then(() => { setApiKey(''); setApiSecret('') }),
                      'App credentials saved.')
                  }}>
                    <Input placeholder="api_key" autoComplete="off" value={apiKey}
                      onChange={(e) => setApiKey(e.target.value.trim())} />
                    <Input placeholder="api_secret" type="password" autoComplete="new-password"
                      value={apiSecret} onChange={(e) => setApiSecret(e.target.value.trim())} />
                    <div className="flex gap-2">
                      <Button type="submit" disabled={busy || !apiKey || !apiSecret}>
                        {st.app_configured ? 'Replace keys' : 'Save keys'}
                      </Button>
                      {st.app_configured && (
                        <Button type="button" variant="outline" disabled={busy}
                          onClick={() => void run(() => kite.forgetApp(), 'Keys removed and logged out.')}>
                          Forget keys
                        </Button>
                      )}
                    </div>
                  </form>
                </>
              )}
            </Card>

            <Card title="2 · Today's session"
              hint="Kite sessions expire at 06:00 IST every day — log in each trading morning.">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                {sp && <Pill tone={sp.tone}>{sp.label}</Pill>}
                {st.kite_user_id && <span className="es-mono">{st.kite_user_id}</span>}
                {st.user_name && <span className="text-[var(--text-3)]">{st.user_name}</span>}
              </div>
              {st.login_at && (
                <p className="text-xs text-[var(--text-3)]">
                  Logged in {istTime(st.login_at)} IST · expires {new Date(st.expires_at ?? '').toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' })} IST
                </p>
              )}
              <div className="flex gap-2">
                <Button disabled={busy || !st.app_configured} onClick={() => void run(async () => {
                  const { login_url } = await kite.login()
                  window.location.assign(login_url)
                })}>
                  {st.session === 'active' ? 'Log in again' : 'Log in to Zerodha'}
                </Button>
                {st.session !== 'none' && (
                  <Button variant="outline" disabled={busy}
                    onClick={() => void run(() => kite.disconnect(), 'Disconnected; today\'s session is gone.')}>
                    Disconnect
                  </Button>
                )}
              </div>
              {!st.app_configured && <p className="text-xs text-[var(--text-4)]">Save your app keys first.</p>}
            </Card>

            <Card title="3 · Live data"
              hint="Stream ticks for your watchlist (or the symbols below) into private 1m/5m bars. Only you see them; the desk uses them instead of delayed bars while they're fresh.">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Pill tone={st.live.state === 'live' ? 'ok' : st.live.state === 'off' ? 'warn' : 'run'}>
                  {st.live.state === 'off' ? 'Not streaming · desk uses DELAYED bars' : st.live.state}
                </Pill>
                {st.live.reconnects > 0 && <span className="text-xs text-[var(--text-3)]">{st.live.reconnects} reconnects</span>}
              </div>
              {st.live.detail && <p className="text-xs text-[var(--text-3)]">{st.live.detail}</p>}
              <div className="flex gap-2">
                <Input placeholder="SBIN, RELIANCE (blank = watchlist)" value={symbols}
                  onChange={(e) => setSymbols(e.target.value.toUpperCase())} />
                <Button disabled={busy || st.session !== 'active'} onClick={() => void run(() => kite.liveStart(
                  symbols.split(',').map((s) => s.trim()).filter(Boolean)), 'Streaming started.')}>
                  Start
                </Button>
                {st.live.state !== 'off' && (
                  <Button variant="outline" disabled={busy} onClick={() => void run(() => kite.liveStop())}>Stop</Button>
                )}
              </div>
              {st.live.symbols.length > 0 && (
                <table className="w-full text-sm">
                  <thead><tr className="text-left text-xs text-[var(--text-4)]">
                    <th>Symbol</th><th>Last tick</th><th>1m bars</th><th>Desk source</th></tr></thead>
                  <tbody>
                    {st.live.symbols.map((s) => (
                      <tr key={s.symbol}>
                        <td className="es-mono">{s.symbol}</td>
                        <td className="es-mono">{s.last_tick_at ? `${istTime(s.last_tick_at)} IST` : '—'}</td>
                        <td className="es-mono">{s.bars_1m}</td>
                        <td><Pill tone={s.source.startsWith('live') ? 'ok' : 'warn'}>
                          {s.source.startsWith('live') ? 'LIVE'
                            : s.backfill_error ? 'DELAYED (session history unavailable)'
                              : s.backfilled === false ? 'DELAYED (filling today’s session)' : 'DELAYED (stale)'}</Pill>
                          {s.backfill_error && <span className="block text-xs text-[var(--text-4)]">{s.backfill_error}</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>

            <Card title="4 · Live orders (owner only)"
              hint="Orders are never automatic. Each one needs: server switch on, owner account, today's login, today's typed arm, and your typed confirmation.">
              <ul className="space-y-1 text-sm">
                <Check ok={st.orders.server_enabled} text="Server allows live orders (KITE_ORDERS_ENABLED=1)" />
                <Check ok={st.orders.owner} text="Your account is an authorised owner (AUTOTRADE_USER_IDS)" />
                <Check ok={!!st.orders.owner_client_id && st.kite_user_id?.toUpperCase() === st.orders.owner_client_id}
                  text={st.orders.owner_client_id
                    ? `Orders bound to Zerodha client ${st.orders.owner_client_id}${st.kite_user_id ? ` (logged in as ${st.kite_user_id})` : ''}`
                    : 'A Zerodha client id is bound to your account (AUTOTRADE_USER_IDS=user:CLIENTID)'} />
                <Check ok={st.session === 'active'} text="Logged in to Zerodha today" />
                <Check ok={st.control.armed} text="Armed for today" />
                <Check ok={!st.control.killed} text="Kill switch not used today" />
                {st.cas?.active && (
                  <Check ok={st.cas.profile_ok}
                    text={`Flat-by at or before ${st.cas.latest_flat_by} for ${st.cas.applies_to}: NSE's closing auction runs until ${st.cas.until} (continuous trading ends ${st.cas.continuous_end}, Zerodha squares off MIS at ${st.cas.broker_square_off})${st.cas.profile_ok ? '' : ' — action required: change flat-by in Desk settings'}`} />
                )}
              </ul>
              <p className="text-xs text-[var(--text-4)]">
                Kite only accepts API orders from a static IP registered in your Kite developer
                profile: register this server&apos;s outbound IP before arming.
              </p>
              {st.orders.blocked_by.length > 0 && (
                <p className="text-xs text-[var(--text-3)]">Blocked: {st.orders.blocked_by.join(' · ')}</p>
              )}
            </Card>
          </div>
        )}
        <DeskFooter />
      </main>
    </div>
  )
}

function Check({ ok, text }: { ok: boolean; text: string }) {
  return (
    <li className="flex items-center gap-2">
      <span className={ok ? 'text-[var(--ok)]' : 'text-[var(--text-4)]'}>{ok ? '✓' : '○'}</span>
      <span className={ok ? '' : 'text-[var(--text-3)]'}>{text}</span>
    </li>
  )
}
