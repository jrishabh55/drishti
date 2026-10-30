import React from 'react';
import {CREDITS, FREE_CASH, LIMITS, fmt} from '../lib/demo.mjs';
import {C, R} from '../theme';

// Mirrors the real navigation: apps/web/src/components/shared/Topbar.tsx and
// components/desk/DeskNav.tsx. The product has no left sidebar and no Home
// dashboard — `/` redirects to /scanners (apps/web/src/routes/index.tsx).
const TOPNAV = [
  {id: 'analysis', label: 'Analysis'},
  {id: 'scanners', label: 'Scanners'},
  {id: 'desk', label: 'Trade desk'},
  {id: 'backtests', label: 'Backtests'},
  {id: 'models', label: 'Models'},
];

const DESKNAV = [
  {id: 'today', label: 'Today'},
  {id: 'research', label: 'Research'},
  {id: 'plans', label: 'Plans'},
  {id: 'paper', label: 'Paper ledger'},
  {id: 'performance', label: 'Performance'},
  {id: 'connection', label: 'Zerodha'},
  {id: 'strategies', label: 'Strategies & live'},
  {id: 'alerts', label: 'Alerts'},
  {id: 'messages', label: 'Messages'},
  {id: 'settings', label: 'Limits & settings'},
];

export const DEMO = {
  desk: 'Demo desk',
  paperCapital: fmt.INR(LIMITS.capital),
  freeCash: fmt.INR2(FREE_CASH),
} as const;

export const AppShell: React.FC<{
  /** Kept for fixture bookkeeping; deliberately not rendered — see above. */
  route: string;
  nav: string;
  desk?: string;
  crumb?: string;
  children: React.ReactNode;
}> = ({nav, desk, crumb, children}) => (
  <div style={{position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', background: C.bg0}}>
    <div style={{height: 68, borderBottom: `1px solid ${C.line1}`, background: C.bg1, display: 'flex', alignItems: 'center', padding: '0 32px', gap: 30, flexShrink: 0}}>
      <div style={{display: 'flex', alignItems: 'center', gap: 11}}>
        <div style={{width: 32, height: 32, borderRadius: 9, background: C.accent, color: '#fff', fontSize: 20, fontWeight: 800, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
          D
        </div>
        <div style={{fontSize: 25, fontWeight: 700, color: C.text1, letterSpacing: -0.3}}>Drishti</div>
      </div>
      <nav style={{display: 'flex', gap: 26, alignItems: 'center'}}>
        {TOPNAV.map((n) => (
          <div
            key={n.id}
            style={{
              fontSize: 20,
              fontWeight: n.id === nav ? 600 : 500,
              color: n.id === nav ? C.text1 : C.text3,
              borderBottom: `2px solid ${n.id === nav ? C.accent : 'transparent'}`,
              paddingBottom: 4,
            }}
          >
            {n.label}
          </div>
        ))}
      </nav>
      {crumb ? <div style={{fontSize: 19, color: C.text3}}>{crumb}</div> : null}
      <div style={{flex: 1}} />
      {/* The spec asks simplified views to be labelled; this never leaves the screen. */}
      <div style={{background: C.runBg, color: C.run, border: `1px solid ${C.run}55`, borderRadius: 999, padding: '5px 14px', fontSize: 16, fontWeight: 600}}>
        Illustrative demo account
      </div>
      <div style={{background: C.accent, color: '#fff', borderRadius: R.sm, padding: '7px 16px', fontSize: 18, fontWeight: 600}}>
        New analysis
      </div>
      <div style={{background: C.okBg, color: C.ok, border: `1px solid ${C.ok}44`, borderRadius: 999, padding: '5px 14px', fontSize: 17, fontWeight: 600}}>
        {CREDITS} credits
      </div>
      <div style={{border: `1px solid ${C.accentLine}`, background: C.accentBg, color: C.accentHi, borderRadius: R.sm, padding: '7px 16px', fontSize: 18, fontWeight: 600}}>
        Guide
      </div>
      <div style={{width: 34, height: 34, borderRadius: 999, background: C.bg3, border: `1px solid ${C.line2}`}} />
    </div>
    {desk ? (
      <div style={{height: 54, borderBottom: `1px solid ${C.line1}`, background: C.bg1, display: 'flex', alignItems: 'center', padding: '0 32px', gap: 22, flexShrink: 0}}>
        {DESKNAV.map((t) => (
          <div
            key={t.id}
            style={{
              fontSize: 18,
              fontWeight: t.id === desk ? 600 : 500,
              color: t.id === desk ? C.accentHi : C.text3,
              background: t.id === desk ? C.accentBg : 'transparent',
              border: `1px solid ${t.id === desk ? C.accentLine : 'transparent'}`,
              borderRadius: R.sm,
              padding: '6px 13px',
            }}
          >
            {t.label}
          </div>
        ))}
      </div>
    ) : null}
    <div style={{flex: 1, padding: '22px 34px 258px', minHeight: 0, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 18}}>
      {children}
    </div>
  </div>
);
