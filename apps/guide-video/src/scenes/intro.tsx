import React from 'react';
import {AbsoluteFill} from 'remotion';
import {C, R} from '../theme';
import {LIMITS, PLAN, fmt} from '../lib/demo.mjs';
import {Panel, Pill} from '../ui';
import {AppShell, DEMO} from './AppShell';

const Stage: React.FC<{children: React.ReactNode}> = ({children}) => (
  <AbsoluteFill style={{background: C.bg0, padding: '70px 120px 268px', display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 34}}>
    {children}
  </AbsoluteFill>
);

const Tile: React.FC<{k: string; title: string; body: string; tone?: string}> = ({k, title, body, tone}) => (
  <div style={{background: C.bg1, border: `1px solid ${tone ? `${tone}55` : C.line1}`, borderRadius: R.lg, padding: 28, display: 'flex', flexDirection: 'column', gap: 12}}>
    <div style={{fontSize: 19, color: tone ?? C.text4, fontFamily: 'ui-monospace, Menlo, monospace', letterSpacing: 1}}>{k}</div>
    <div style={{fontSize: 32, fontWeight: 700, color: C.text1, letterSpacing: -0.4}}>{title}</div>
    <div style={{fontSize: 22, color: C.text2, lineHeight: 1.45}}>{body}</div>
  </div>
);

const INTRO: Record<string, React.ReactNode> = {
  what: (
    <Stage>
      <div style={{display: 'flex', alignItems: 'center', gap: 20}}>
        <div style={{width: 56, height: 56, borderRadius: 16, background: C.accent}} />
        <div style={{fontSize: 92, fontWeight: 800, color: C.text1, letterSpacing: -3}}>Drishti</div>
        <Pill tone="accent">research &amp; paper trading desk</Pill>
      </div>
      <div style={{fontSize: 34, color: C.text2, maxWidth: 1180, lineHeight: 1.4}}>
        Analysis, scanners, plans, risk checks and a simulated ledger — one workflow for Indian equities.
      </div>
      <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 24}}>
        <Tile k="SIMULATED" title="Paper capital" body="Simulated trading money that funds the paper ledger." tone={C.accent} />
        <Tile k="SEPARATE" title="Analysis credits" body="A different balance that may pay for model analysis." tone={C.info} />
        <Tile k="GUARDED" title="Live execution" body="Disabled in this deployment. Every control you see is guarded or simulated." tone={C.err} />
      </div>
    </Stage>
  ),
  loop: (
    <Stage>
      <div style={{fontSize: 58, fontWeight: 700, color: C.text1, letterSpacing: -1.2}}>One loop, every session</div>
      <div style={{display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 20, alignItems: 'stretch'}}>
        {[
          ['Limits', 'Set what you can lose before you look at a chart.'],
          ['Research', 'Read the symbol, the news and the analysis.'],
          ['Plan', 'Entry, stop, target and a flat-by time.'],
          ['Sizing', 'Risk checks produce a ticket — or block it.'],
          ['Paper ticket', 'You confirm it. Then the ledger records what happened.'],
        ].map(([t, b], i) => (
          <div key={t} style={{background: C.bg1, border: `1px solid ${i === 4 ? C.accentLine : C.line1}`, borderRadius: R.lg, padding: 24, display: 'flex', flexDirection: 'column', gap: 10}}>
            <div style={{fontSize: 44, fontWeight: 800, color: i === 4 ? C.accentHi : C.text4, fontFamily: 'ui-monospace, Menlo, monospace'}}>{i + 1}</div>
            <div style={{fontSize: 30, fontWeight: 700, color: C.text1}}>{t}</div>
            <div style={{fontSize: 21, color: C.text2, lineHeight: 1.4}}>{b}</div>
          </div>
        ))}
      </div>
      <Panel title="Then read what happened" note={`${DEMO.desk} · paper ${fmt.INR(LIMITS.capital)}`}>
        <div style={{fontSize: 24, color: C.text2}}>
          The paper ledger and the performance screen show fills, charges and losses — not a promise about live trading.
        </div>
      </Panel>
    </Stage>
  ),
  guards: (
    <Stage>
      <div style={{fontSize: 58, fontWeight: 700, color: C.text1, letterSpacing: -1.2}}>What stays guarded</div>
      <div style={{display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 22}}>
        <Tile k="GATE 1" title="Server enablement" body="Live order execution is disabled on this deployment." tone={C.err} />
        <Tile k="GATE 2" title="Broker credentials" body="Your own Kite application, stored encrypted." />
        <Tile k="GATE 3" title="Daily arming" body="A separate, deliberate action each trading day." />
        <Tile k="GATE 4" title="Order confirmation" body="Every order is confirmed by you, one at a time." />
      </div>
      <div style={{background: C.errBg, border: `1px solid ${C.err}55`, borderRadius: R.md, padding: '20px 26px', color: C.err, fontSize: 28, lineHeight: 1.4}}>
        Paper results do not prove live execution quality, and nothing here promises a profit.
      </div>
    </Stage>
  ),
  guide: (
    <AbsoluteFill>
      <AppShell route="/desk/plans/$id" nav="desk" desk="plans">
        <Panel title={`Plan · ${PLAN.symbol} long`} note="Draft saved">
          <div style={{fontSize: 24, color: C.text2}}>
            Entry {fmt.PRICE(PLAN.entry)} · stop {fmt.PRICE(PLAN.stop)} · target {fmt.PRICE(PLAN.target)} · flat by{' '}
            {PLAN.flatBy}
          </div>
        </Panel>
      </AppShell>
      <AbsoluteFill style={{background: 'rgba(6,8,12,0.55)'}} />
      <div style={{position: 'absolute', right: 40, top: 92, bottom: 230, width: 560, background: C.bg1, border: `1px solid ${C.accentLine}`, borderRadius: R.xl, padding: 30, display: 'flex', flexDirection: 'column', gap: 18, boxShadow: '0 24px 60px rgba(0,0,0,0.55)'}}>
        <div style={{display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12}}>
          <div>
            <div style={{fontSize: 30, fontWeight: 700, color: C.text1}}>Drishti Guide</div>
            <div style={{fontSize: 20, color: C.text3}}>Your next step, explained</div>
          </div>
          <Pill>Show page</Pill>
        </div>
        <div style={{display: 'flex', gap: 10}}>
          <Pill tone="accent">This screen</Pill>
          <Pill>Tours</Pill>
          <Pill>Ask</Pill>
        </div>
        <div style={{fontSize: 17, color: C.text4, letterSpacing: 1}}>YOU ARE HERE</div>
        <div style={{fontSize: 24, fontWeight: 700, color: C.text1}}>Sizing &amp; risk checks</div>
        <div style={{fontSize: 21, color: C.text2, lineHeight: 1.5}}>
          Use the sizing and risk check control on a saved plan to create a reviewable ticket. Inspect quantity,
          per-share risk, total risk and failed checks.
        </div>
        <div style={{height: 1, background: C.line1}} />
        <div style={{fontSize: 19, color: C.text4, textTransform: 'uppercase', letterSpacing: 1}}>Common question</div>
        <div style={{fontSize: 21, color: C.text2, lineHeight: 1.5}}>
          <b style={{color: C.text1}}>Does creating a ticket confirm a trade?</b> No. Review the ticket and confirm it
          deliberately. The guide never confirms tickets or overrides failed risk checks.
        </div>
      </div>
    </AbsoluteFill>
  ),
};

export const IntroScene: React.FC<{chapter: string}> = ({chapter}) => {
  const node = INTRO[chapter];
  if (!node) throw new Error(`No intro scene for chapter "${chapter}"`);
  return <>{node}</>;
};
