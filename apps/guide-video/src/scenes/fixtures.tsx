import React from 'react';
import {
  AVG_R_NET, BLOCK_REASON, BOUND_BY, CHARGES, CLOSED, CREDITS, DAILY_PNL, FAILED_CHECKS, FREE_CASH,
  HIDDEN_CLOSED, LEDGER_ROWS, LIMITS, LIMIT_ROWS, NET_PNL, NEWS, PLAN, QTY, RISK_CHECKS,
  RISK_PER_SHARE, SIZE_LIMITS, TICKET, WIN_RATE, fmt,
} from '../lib/demo.mjs';
import {C, R} from '../theme';
import {Bars, Candles, Field, Panel, Pill, Row} from '../ui';
import {AppShell, DEMO} from './AppShell';

const {INR, INR2, PRICE} = fmt;

const Grid: React.FC<{cols: string; children: React.ReactNode}> = ({cols, children}) => (
  <div style={{display: 'grid', gridTemplateColumns: cols, gap: 18}}>{children}</div>
);

const Note: React.FC<{tone?: 'warn' | 'err' | 'info' | 'ok'; children: React.ReactNode}> = ({tone = 'info', children}) => {
  const fg = {warn: C.run, err: C.err, info: C.info, ok: C.ok}[tone];
  const bg = {warn: C.runBg, err: C.errBg, info: C.infoBg, ok: C.okBg}[tone];
  return (
    <div style={{background: bg, border: `1px solid ${fg}55`, borderRadius: R.md, padding: '13px 18px', color: fg, fontSize: 20, lineHeight: 1.45}}>
      {children}
    </div>
  );
};

const Input: React.FC<{label: string; value: string; mono?: boolean; muted?: boolean}> = ({label, value, mono, muted}) => (
  <div style={{display: 'flex', flexDirection: 'column', gap: 6}}>
    <div style={{fontSize: 15, color: C.text4, textTransform: 'uppercase', letterSpacing: 1}}>{label}</div>
    <div style={{background: C.bg2, border: `1px solid ${C.line2}`, borderRadius: R.sm, padding: '10px 14px', fontSize: 20, color: muted ? C.text3 : C.text1, fontFamily: mono ? 'ui-monospace, Menlo, monospace' : undefined}}>
      {value}
    </div>
  </div>
);

const Btn: React.FC<{children: React.ReactNode; tone?: 'primary' | 'ghost' | 'danger'}> = ({children, tone = 'ghost'}) => {
  const s = tone === 'primary' ? {bg: C.accent, fg: '#fff', line: C.accent}
    : tone === 'danger' ? {bg: C.errBg, fg: C.err, line: `${C.err}66`}
    : {bg: C.bg3, fg: C.text2, line: C.line2};
  return (
    <span style={{background: s.bg, color: s.fg, border: `1px solid ${s.line}`, borderRadius: R.sm, padding: '9px 19px', fontSize: 19, fontWeight: 600, alignSelf: 'flex-start'}}>
      {children}
    </span>
  );
};

const check = (c: {pass: boolean; label: string; detail: string}) => (
  <Row key={c.label} widths={[7, 3]} cells={[c.label, c.pass ? <Pill tone="ok">Pass · {c.detail}</Pill> : <Pill tone="err">Fail · {c.detail}</Pill>]} />
);

/** One synthetic screen per catalog chapter. Routes and navigation mirror the real app. */
export const FIXTURES: Record<string, {route: string; nav: string; desk?: string; crumb?: string; node: React.ReactNode}> = {
  // `/` redirects to /scanners, so "Start here" is the scanner workbench with the Guide open.
  orientation: {
    route: '/ → /scanners',
    nav: 'scanners',
    node: (
      <>
        <Panel title="Scanner workbench" note="Drishti opens here" accent>
          <Grid cols="repeat(3, 1fr)">
            <Field label="Paper capital" value={INR(LIMITS.capital)} />
            <Field label="Analysis credits" value={`${CREDITS} credits`} tone={C.info} />
            <Field label="Live orders" value="Disabled on this server" tone={C.err} />
          </Grid>
          <Note>Paper capital is simulated money. Analysis credits are a separate balance.</Note>
        </Panel>
        <Panel title="Guide · suggested order">
          <Grid cols="repeat(5, 1fr)">
            {['1 · Limits', '2 · Research', '3 · Plan', '4 · Sizing', '5 · Paper ticket'].map((s, i) => (
              <div key={s} style={{background: C.bg2, border: `1px solid ${i === 0 ? C.accentLine : C.line1}`, borderRadius: R.md, padding: 16, fontSize: 21, color: i === 0 ? C.accentHi : C.text2, fontWeight: 600}}>
                {s}
              </div>
            ))}
          </Grid>
        </Panel>
      </>
    ),
  },
  models: {
    route: '/settings/models',
    nav: 'models',
    node: (
      <>
        <Panel title="Model connections" note="Extra provider keys · encrypted on the server">
          <Grid cols="1fr 1fr">
            <Input label="Extra provider key" value="•••••••••••••••••••••  saved" mono muted />
            <Input label="OpenAI API key" value="not connected" muted />
          </Grid>
          <div style={{display: 'flex', gap: 12}}>
            <Pill tone="neutral">ChatGPT helper · separate connection</Pill>
            <Pill tone="info">Gemini key is set up in the Analysis form</Pill>
          </div>
        </Panel>
        <Panel title="Analysis form · models" accent>
          <Grid cols="repeat(3, 1fr)">
            <Input label="Gemini API key" value="•••••••••••••••••••••  saved" mono muted />
            <Input label="Quick model" value="gemini · personal credentials" />
            <Input label="Deep model" value="gemini · personal credentials" />
          </Grid>
          <Note tone="warn">OpenAI analysis may use credits. Personal providers bill your own account.</Note>
        </Panel>
      </>
    ),
  },
  analysis: {
    route: '/analyse',
    nav: 'analysis',
    node: (
      <>
        <Panel title="New analysis">
          <Grid cols="repeat(4, 1fr)">
            <Input label="Ticker or company name" value={`${PLAN.symbol} · ${PLAN.name}`} />
            <Input label="Analysis date" value={PLAN.date} mono />
            <Input label="Analysts" value="Market, News, Fundamentals" />
            <Input label="Depth" value="Standard (2 rounds)" />
          </Grid>
          <div style={{display: 'flex', alignItems: 'center', gap: 16}}>
            <Btn tone="primary">Start analysis →</Btn>
            <div style={{fontSize: 19, color: C.text3}}>Quick + deep model · personal provider · you confirm the spend</div>
          </div>
        </Panel>
        <Panel title="Advanced" note="Standard · 2 rounds">
          <div style={{background: C.bg2, border: `1px solid ${C.line1}`, borderRadius: R.md, padding: '14px 18px', fontSize: 20, color: C.text3}}>
            Run estimate: <b style={{color: C.text1}}>~12 min</b> <span style={{color: C.text4}}>·</span>{' '}
            <b style={{color: C.text1}}>$2.40</b>
          </div>
          <Note tone="warn">A failed or interrupted run is not a completed recommendation.</Note>
        </Panel>
      </>
    ),
  },
  reports: {
    route: '/runs/$id',
    nav: 'analysis',
    crumb: `Analyses / ${PLAN.symbol}`,
    node: (
      <>
        <Panel title={`${PLAN.symbol} · ${PLAN.date}`} note="Completed" accent>
          <div style={{display: 'flex', gap: 12}}>
            <Pill tone="accent">Decision</Pill>
            <Pill tone="neutral">Levels</Pill>
            <Pill tone="neutral">Bull case</Pill>
            <Pill tone="neutral">Bear case</Pill>
            <Pill tone="neutral">Risk</Pill>
            <Pill tone="neutral">Trade plan</Pill>
            <Pill tone="neutral">Transcript</Pill>
          </div>
          <Grid cols="1fr 1fr">
            <Field label="Decision" value="Hold — thesis not confirmed" tone={C.run} />
            <Field label="Sources" value="5 documents, dated" />
          </Grid>
        </Panel>
        <Panel title="Other runs">
          <Row head widths={[2, 3, 2, 2]} cells={['Status', 'Symbol / date', 'Depth', 'Result']} />
          <Row widths={[2, 3, 2, 2]} cells={[<Pill tone="warn">Running</Pill>, '/runs/… · TATAMOTORS · 2026-10-29', 'Standard', '—']} />
          <Row widths={[2, 3, 2, 2]} cells={[<Pill tone="err">Failed</Pill>, '/runs/… · HDFCBANK · 2026-10-28', 'Deep', 'No recommendation']} />
        </Panel>
      </>
    ),
  },
  scanners: {
    route: '/scanners',
    nav: 'scanners',
    node: (
      <>
        <Panel title="Opening range breakout" note="3 rules">
          <Grid cols="repeat(3, 1fr)">
            <Field label="Rule 1" value="Price > 20-day high" />
            <Field label="Rule 2" value="Volume > 1.5× average" />
            <Field label="Rule 3" value="ATR between 1.2% and 3%" />
          </Grid>
        </Panel>
        <Panel title="Matches" note={`data as of ${PLAN.date} 15:30 (delayed)`}>
          <Row head widths={[3, 3, 4]} cells={['Symbol', 'Close', 'Why it matched']} />
          <Row widths={[3, 3, 4]} cells={[PLAN.symbol, '1,844.20', 'above 20-day high, 1.8× volume']} />
          <Row widths={[3, 3, 4]} cells={['TATAMOTORS', '712.80', 'above 20-day high, 1.6× volume']} />
          <Note tone="warn">Symbols without usable bars are not matched at all — they do not appear as a failed row.</Note>
        </Panel>
      </>
    ),
  },
  research: {
    route: '/desk/research',
    nav: 'desk',
    desk: 'research',
    node: (
      <Grid cols="1.25fr 1fr">
        <Panel title="Watchlist news" note="dated sources">
          <Row head widths={[3, 6, 2]} cells={['Symbol', 'Headline', 'Date']} />
          {NEWS.map((n) => (
            <Row key={n.symbol} widths={[3, 6, 2]} cells={[n.symbol, n.headline, n.date]} />
          ))}
          <Note>Context only. News does not establish an entry or a fill.</Note>
        </Panel>
        <Panel title="Company results">
          <Note tone="warn">No matching disclosures returned for BAJFINANCE. An empty panel can also mean the data could not be fetched.</Note>
        </Panel>
      </Grid>
    ),
  },
  today: {
    route: '/desk',
    nav: 'desk',
    desk: 'today',
    node: (
      <>
        <Panel title="Watchlist" note={`In session · 11:42 IST · flat by ${PLAN.flatBy}`}>
          <Grid cols="repeat(3, 1fr)">
            <div style={{background: C.bg2, border: `1px solid ${C.accentLine}`, borderRadius: R.md, padding: 16, display: 'flex', flexDirection: 'column', gap: 9}}>
              <div style={{display: 'flex', justifyContent: 'space-between'}}>
                <span style={{fontSize: 23, fontWeight: 700, color: C.text1}}>{PLAN.symbol}</span>
                <Pill tone="ok">Triggered</Pill>
              </div>
              <Field label="Opening range" value={`${PRICE(PLAN.stop)} – ${PRICE(PLAN.entry)}`} mono />
              <Field label="Entry timer" value={`Triggered ${PLAN.entryTime}`} />
            </div>
            <div style={{background: C.bg2, border: `1px solid ${C.line1}`, borderRadius: R.md, padding: 16, display: 'flex', flexDirection: 'column', gap: 9}}>
              <div style={{display: 'flex', justifyContent: 'space-between'}}>
                <span style={{fontSize: 23, fontWeight: 700, color: C.text1}}>TATAMOTORS</span>
                <Pill tone="warn">Waiting</Pill>
              </div>
              <Field label="Reason" value="Waiting for a break of the opening range" />
            </div>
            <div style={{background: C.bg2, border: `1px solid ${C.line1}`, borderRadius: R.md, padding: 16, display: 'flex', flexDirection: 'column', gap: 9}}>
              <div style={{display: 'flex', justifyContent: 'space-between'}}>
                <span style={{fontSize: 23, fontWeight: 700, color: C.text1}}>BAJFINANCE</span>
                <Pill tone="err">NO-DATA</Pill>
              </div>
              <Field label="Reason" value="No opening-range bars for this date" />
            </div>
          </Grid>
        </Panel>
        <Panel title={`${PLAN.symbol} · illustrative bars`} note="shape only, not a quoted series">
          <Candles width={1420} height={190} bars={40} />
        </Panel>
      </>
    ),
  },
  plans: {
    route: '/desk/plans',
    nav: 'desk',
    desk: 'plans',
    node: (
      <>
        <Panel title={`Plan · ${PLAN.symbol} long`} note="Draft saved" accent>
          <Grid cols="repeat(4, 1fr)">
            <Input label="Entry" value={PRICE(PLAN.entry)} mono />
            <Input label="Stop rule" value={`${PRICE(PLAN.stop)} · opening-range low`} mono />
            <Input label="Target rule" value={`${PRICE(PLAN.target)} · 2R`} mono />
            <Input label="Flat by" value={PLAN.flatBy} mono />
          </Grid>
          <div style={{display: 'flex', gap: 12}}>
            <Btn>Recompute</Btn>
            <Btn>Size ticket</Btn>
          </div>
        </Panel>
        <Note tone="warn">Creating a plan does not place an order. A manual plan with no entry time cannot run the intraday replay.</Note>
      </>
    ),
  },
  timemachine: {
    route: '/desk/plans/$id',
    nav: 'desk',
    desk: 'plans',
    node: (
      <>
        <Panel title="Time Machine" note={`${PLAN.symbol} · ${PLAN.date}`}>
          <div style={{display: 'flex', gap: 12}}>
            <Pill tone="accent">Intraday</Pill>
            <Pill tone="neutral">Swing (daily bars)</Pill>
          </div>
          <Grid cols="1fr 1fr">
            <Field label="Entry bar time" value={PLAN.entryTime} mono />
            <Field label="Complete prior sessions found" value="0 of 20 required" tone={C.err} />
          </Grid>
          <Note tone="err">
            Not enough history: 0 complete prior session windows. No intraday replay was run — this is a missing result,
            not a passing one.
          </Note>
        </Panel>
        <Panel title="What the replay would compare">
          <Grid cols="repeat(3, 1fr)">
            <Field label="Mode" value="Historical bars + simulated paths" />
            <Field label="Requirement" value="Entry bar time + ≥ 20 sessions" />
            <Field label="Output" value="Simulated odds — not a prediction" tone={C.run} />
          </Grid>
        </Panel>
      </>
    ),
  },
  sizing: {
    route: '/desk/plans/$id',
    nav: 'desk',
    desk: 'plans',
    node: (
      <Grid cols="1.05fr 1fr">
        <Panel title="Ticket" note={`bound by ${BOUND_BY}`} accent>
          <Grid cols="repeat(4, 1fr)">
            <Field label="Position" value={INR2(TICKET.position)} mono />
            <Field label="Loss at stop" value={`${INR2(TICKET.lossAtStop)} · ≈${INR2(TICKET.lossAtStopNet)} w/ charges`} mono tone={C.err} />
            <Field label="Gain at target" value={`${INR2(TICKET.gainAtTarget)} · ≈${INR2(TICKET.gainAtTargetNet)} net`} mono tone={C.ok} />
            <Field label="Reward:risk" value={`${TICKET.rewardRisk.toFixed(2)} (${TICKET.rewardRiskNet.toFixed(2)} net)`} mono />
          </Grid>
          <div style={{fontSize: 17, color: C.text4, textTransform: 'uppercase', letterSpacing: 1}}>
            How the size was chosen (bound by {BOUND_BY})
          </div>
          {SIZE_LIMITS.map((l) => (
            <div key={l.name} style={{fontSize: 19, color: l.binds ? C.text1 : C.text3, fontWeight: l.binds ? 600 : 500}}>
              {l.name}: {l.shares} shares — {l.how}
            </div>
          ))}
        </Panel>
        <Panel title="Risk check" note={FAILED_CHECKS.length ? `${FAILED_CHECKS.length} blocked` : 'all passed'}>
          {RISK_CHECKS.map(check)}
          {BLOCK_REASON ? <Note tone="err">{BLOCK_REASON}</Note> : null}
          <Note tone="warn">
            If a check fails the ticket is held, and the fix is a corrected plan or a deliberate limit review — never a
            bypass, and never moving the target just to clear the check.
          </Note>
        </Panel>
      </Grid>
    ),
  },
  messages: {
    route: '/desk/messages',
    nav: 'desk',
    desk: 'messages',
    node: (
      <Grid cols="1fr 1.1fr">
        <Panel title="Telegram">
          <Grid cols="1fr 1fr">
            <Field label="Bot" value="Connected" tone={C.ok} />
            <Field label="Chat" value="Verified" tone={C.ok} />
          </Grid>
          <Row widths={[7, 3]} cells={['Daily updates', <Pill tone="ok">On</Pill>]} />
          <Row widths={[7, 3]} cells={['Paper approvals', <Pill tone="ok">On</Pill>]} />
          <Row widths={[7, 3]} cells={['WhatsApp (optional)', <Pill tone="neutral">Not set up</Pill>]} />
        </Panel>
        <Panel title="Paper proposal" note="expires in 4 min">
          <div style={{background: C.bg2, border: `1px solid ${C.line2}`, borderRadius: R.md, padding: 16, fontSize: 20, color: C.text2, lineHeight: 1.5}}>
            <b style={{color: C.text1}}>{PLAN.symbol} long · paper</b>
            <br />
            Entry {PRICE(PLAN.entry)} · stop {PRICE(PLAN.stop)} · target {PRICE(PLAN.target)}
            <br />
            Qty {QTY} · value {INR2(TICKET.position)} · risk {INR2(TICKET.lossAtStop)} · flat by {PLAN.flatBy}
          </div>
          <div style={{display: 'flex', gap: 10}}>
            <Btn tone="primary">Approve paper trade</Btn>
            <Btn tone="danger">Reject</Btn>
            <Btn>Status</Btn>
            <Btn>View plan</Btn>
          </div>
          <Note tone="warn">Approval re-runs risk checks and applies to the paper ledger only. It never places a live broker order.</Note>
        </Panel>
      </Grid>
    ),
  },
  paper: {
    route: '/desk/paper',
    nav: 'desk',
    desk: 'paper',
    node: (
      <>
        <Panel title="Paper ledger" note={DEMO.desk}>
          <Grid cols="repeat(6, 1fr)">
            <Field label="Capital" value={INR(LIMITS.capital)} />
            <Field label="Free cash" value={INR2(FREE_CASH)} />
            <Field label="Net P&L" value={INR2(NET_PNL)} tone={C.err} />
            <Field label="Trades" value={String(CLOSED.length)} />
            <Field label="Win rate" value={`${WIN_RATE}%`} />
            <Field label="Charges paid" value={INR2(CHARGES)} />
          </Grid>
        </Panel>
        <Panel title="Trades">
          <Row head widths={[3, 3, 2, 3, 4, 4, 3, 3, 3]}
            cells={['Date', 'Symbol', 'Qty', 'Status', 'Entry', 'Exit', 'Gross', 'Charges', 'Net']} />
          {LEDGER_ROWS.map((t) => (
            <Row
              key={`${t.symbol}-${t.day}-${t.status}`}
              widths={[3, 3, 2, 3, 4, 4, 3, 3, 3]}
              tone={t.status === 'open' ? C.text1 : undefined}
              cells={[t.day, t.symbol, String(t.qty),
                t.status === 'open' ? <Pill tone="warn">open</Pill> : <Pill tone="neutral">closed</Pill>,
                t.status === 'open' ? `${PRICE(t.entry)} ${PLAN.entryTime}` : PRICE(t.entry),
                t.exit == null ? '\u2014' : `${PRICE(t.exit)} ${t.exitReason}`,
                t.gross == null ? '\u2014' : INR2(t.gross),
                t.charges == null ? '\u2014' : INR2(t.charges),
                t.net == null ? '\u2014' : INR2(t.net)]}
            />
          ))}
          <div style={{fontSize: 18, color: C.text4, paddingTop: 4}}>
            + {HIDDEN_CLOSED} earlier closed trades · totals above include all {CLOSED.length}
          </div>
          <Note tone="warn">
            The {QTY}-share {PLAN.symbol} position approved over Telegram is still open, which is why free cash is{' '}
            {INR2(FREE_CASH)}. Charges of {INR2(CHARGES)} across all {CLOSED.length} closed trades are in the net figure.
          </Note>
        </Panel>
      </>
    ),
  },
  performance: {
    route: '/desk/performance',
    nav: 'desk',
    desk: 'performance',
    node: (
      <Grid cols="1fr 1fr">
        <Panel title="Daily net P&L" note="simulated">
          <Bars values={DAILY_PNL.map((d) => d.net)} width={640} height={200} />
          <Grid cols="repeat(3, 1fr)">
            <Field label="Trades" value={String(CLOSED.length)} tone={C.run} />
            <Field label="Charges paid" value={INR2(CHARGES)} />
            <Field label="Net P&L" value={INR2(NET_PNL)} tone={C.err} />
            <Field label="Avg R (net)" value={`${AVG_R_NET.toFixed(2)}R`} tone={C.err} />
          </Grid>
        </Panel>
        <Panel title="Time Machine: expected vs what happened">
          <div style={{fontSize: 20, color: C.text3, lineHeight: 1.5}}>
            None of your closed trades has a Time Machine replay of the same plan on record, so there is nothing to
            compare. Run the Time Machine on a plan before trading it.
          </div>
          <Note tone="warn">
            {CLOSED.length} trades is far too small a sample to conclude anything, and simulated odds are not
            predictions. A new account shows zeros, and stays empty.
          </Note>
        </Panel>
      </Grid>
    ),
  },
  alerts: {
    route: '/desk/alerts',
    nav: 'desk',
    desk: 'alerts',
    node: (
      <>
        <Panel title="Alerts">
          <Row head widths={[4, 5]} cells={['Type', 'Condition']} />
          <Row widths={[4, 5]} cells={['Price at or above', `${PLAN.symbol} ${PRICE(PLAN.entry)}`]} />
          <Row widths={[4, 5]} cells={['% move from previous close', `${PLAN.symbol} ≥ 2%`]} />
          <Row widths={[4, 5]} cells={['Time (IST)', '14:25']} />
        </Panel>
        <Panel title="Rang" note="Mark all seen">
          <Row widths={[3, 6, 2]} cells={[PLAN.entryTime, `${PLAN.symbol} crossed the opening-range high`, <Pill tone="accent">Unread</Pill>]} />
          <Row widths={[3, 6, 2]} cells={['11:02', 'TATAMOTORS stop hit', <Pill tone="neutral">Seen</Pill>]} />
          <Note>Marking an event seen changes its read status only. No alert guarantees a price or an executable entry.</Note>
        </Panel>
      </>
    ),
  },
  limits: {
    route: '/desk/settings',
    nav: 'desk',
    desk: 'settings',
    node: (
      <>
        <Panel title="Limits" note="Sizing never uses more than these" accent>
          <Grid cols="repeat(5, 1fr)">
            {LIMIT_ROWS.map(([label, value]) => (
              <Input key={label} label={label} value={value} mono />
            ))}
          </Grid>
        </Panel>
        <Note tone="warn">Raising a limit does not make a setup safer — it only moves the line the risk check draws.</Note>
      </>
    ),
  },
  backtests: {
    route: '/evaluation',
    nav: 'backtests',
    node: (
      <>
        <Panel title="Backtest batches">
          <div style={{fontSize: 20, color: C.text2, lineHeight: 1.45}}>
            Run the same analysis over a small grid of past dates, then score each call against what the closes
            actually did. Each cell is an ordinary analysis run in your history.
          </div>
          <Grid cols="2fr 1fr 1fr 1fr">
            <Input label="Tickers (comma separated, up to 6)" value="INFY, TATAMOTORS, HDFCBANK" mono />
            <Input label="From" value="2026-09-01" mono />
            <Input label="To" value="2026-09-30" mono />
            <Input label="Every N days" value="7" mono />
          </Grid>
          <div style={{display: 'flex', alignItems: 'center', gap: 16}}>
            <Btn>Check cost</Btn>
            <div style={{fontSize: 20, color: C.text2}}>
              15 runs · up to <b style={{color: C.text1}}>15</b> credits · you have {CREDITS}
            </div>
          </div>
          <div style={{display: 'flex', flexDirection: 'column', gap: 10}}>
            <div style={{fontSize: 20, color: C.text4, display: 'flex', alignItems: 'center', gap: 10}}>
              <span style={{display: 'inline-block', width: 20, height: 20, borderRadius: 4, border: `1px solid ${C.line2}`, background: C.bg2}} />
              I understand this starts 15 analyses and can spend up to 15 credits (cached results cost nothing).
            </div>
            <div style={{display: 'flex', alignItems: 'center', gap: 16}}>
              <span style={{background: C.bg2, color: C.text4, border: `1px solid ${C.line1}`, borderRadius: R.sm, padding: '9px 19px', fontSize: 19, fontWeight: 600}}>
                Start batch
              </span>
              <span style={{color: C.err, fontSize: 20}}>Not enough credits for this batch.</span>
            </div>
          </div>
        </Panel>
        <Panel title="By rating" note="illustrative demo results">
          <Row head widths={[3, 3, 3, 3]} cells={['Rating', 'Scored calls', 'Direction right', 'Mean alpha']} />
          <Row widths={[3, 3, 3, 3]} cells={['BUY', '9', '44%', <span style={{color: C.err}}>−0.6%</span>]} />
          <Row widths={[3, 3, 3, 3]} cells={['HOLD', '4', 'no direction claimed', <span style={{color: C.ok}}>+0.2%</span>]} />
          <Row widths={[3, 3, 3, 3]} cells={['SELL', '2', '50%', <span style={{color: C.err}}>−0.1%</span>]} />
          <Note tone="warn">Scores depend on the dates and assumptions chosen. They are not a prediction.</Note>
        </Panel>
      </>
    ),
  },
  broker: {
    route: '/desk/connection',
    nav: 'desk',
    desk: 'connection',
    node: (
      <>
        <Panel title="Zerodha Kite connection">
          <Grid cols="1fr 1fr">
            <Input label="Your Kite API key" value="•••••••••••••••  saved, encrypted" mono muted />
            <Input label="Daily login" value="Required each trading day" />
          </Grid>
          <div style={{display: 'flex', gap: 12}}>
            <Pill tone="ok">Credentials stored</Pill>
            <Pill tone="warn">Daily login pending</Pill>
            <Pill tone="err">Live orders disabled on this server</Pill>
          </div>
        </Panel>
        <Panel title="Separate gates">
          <Grid cols="repeat(4, 1fr)">
            {['Connect credentials', 'Daily login', 'Arm for the day', 'Confirm each order'].map((g, i) => (
              <div key={g} style={{background: C.bg2, border: `1px solid ${C.line1}`, borderRadius: R.md, padding: 16, fontSize: 20, color: C.text2}}>
                <div style={{color: C.text4, fontSize: 15, marginBottom: 6}}>GATE {i + 1}</div>
                {g}
              </div>
            ))}
          </Grid>
          <Note>Connecting a broker does not enable orders. Delayed public data can differ from broker data.</Note>
        </Panel>
      </>
    ),
  },
  live: {
    route: '/desk/strategies',
    nav: 'desk',
    desk: 'strategies',
    node: (
      <>
        <Panel title="Strategy controls" note="production live execution disabled">
          <div style={{display: 'flex', gap: 12, alignItems: 'center'}}>
            <Pill tone="accent">Off</Pill>
            <Pill tone="neutral">Paper</Pill>
            <Pill tone="neutral">Confirm</Pill>
            <div style={{flex: 1}} />
            <Btn tone="danger">Kill switch</Btn>
          </div>
          <Grid cols="repeat(4, 1fr)">
            <Field label="Server enablement" value="Disabled" tone={C.err} />
            <Field label="Armed today" value="No" tone={C.text3} />
            <Field label="Broker connection" value="Credentials stored" />
            <Field label="Data feed" value="Delayed" tone={C.run} />
          </Grid>
        </Panel>
        <Note tone="err">
          Live orders need server enablement, daily arming and explicit per-order confirmation. The Guide has no action
          tools and never changes these controls.
        </Note>
      </>
    ),
  },
};

export const ChapterScreen: React.FC<{chapter: string}> = ({chapter}) => {
  const f = FIXTURES[chapter];
  if (!f) throw new Error(`No fixture screen for chapter "${chapter}"`);
  return (
    <AppShell route={f.route} nav={f.nav} desk={f.desk} crumb={f.crumb}>
      {f.node}
    </AppShell>
  );
};
