import assert from 'node:assert/strict';
import test from 'node:test';
import {
  ALL_DATES, AVG_R_NET, BLOCK_REASON, BOUND_BY, CHARGES, CLOSED, CREDITS, DAILY_PNL,
  FAILED_CHECKS, FREE_CASH, GROSS_PNL, HIDDEN_CLOSED, LEDGER_ROWS, LIMITS, LIMIT_ROWS, NET_PNL,
  NEWS, OPEN_VALUE, PLAN, QTY, RISK_CHECKS, RISK_PER_SHARE, SIZE_LIMITS, TICKET, VISIBLE_CLOSED,
  WINS, WIN_RATE, isWeekday, roundTripCharges,
} from './demo.mjs';

test('sizing takes the minimum across every limit, as sizing.py does', () => {
  // sizing.py:83-97 builds max loss / position cap / max order / cash and takes min().
  assert.deepEqual(SIZE_LIMITS.map((l) => l.name), ['max loss', 'position cap', 'max order', 'cash']);
  assert.equal(QTY, Math.min(...SIZE_LIMITS.map((l) => l.shares)));
  assert.equal(BOUND_BY, SIZE_LIMITS.find((l) => l.shares === QTY).name);
  assert.equal(SIZE_LIMITS.filter((l) => l.binds).length >= 1, true);
  // The proposed quantity must satisfy every limit — the sizer never proposes then blocks.
  assert.ok(QTY * RISK_PER_SHARE <= LIMITS.maxLossPerTradeInr);
  assert.ok(QTY * PLAN.entry <= LIMITS.maxOrderInr);
  assert.ok(QTY * PLAN.entry <= (LIMITS.capital * LIMITS.maxPositionPct) / 100);
  for (const l of SIZE_LIMITS) assert.ok(l.shares >= QTY, `${l.name} would propose fewer than the bound`);
});

test('the ticket reports position, loss at stop, gain at target and reward:risk', () => {
  assert.equal(TICKET.position, Math.round(QTY * PLAN.entry * 100) / 100);
  assert.equal(TICKET.lossAtStop, Math.round(QTY * RISK_PER_SHARE * 100) / 100);
  assert.equal(TICKET.gainAtTarget, Math.round(QTY * (PLAN.target - PLAN.entry) * 100) / 100);
  assert.equal(TICKET.rewardRisk, 2, 'the demo plan is a clean 2R gross');
  // Net figures must be worse than gross, because charges only ever cost money.
  assert.ok(TICKET.lossAtStopNet > TICKET.lossAtStop);
  assert.ok(TICKET.gainAtTargetNet < TICKET.gainAtTarget);
  assert.ok(TICKET.rewardRiskNet < TICKET.rewardRisk);
});

test('the demo ticket passes every risk check, so approval and the ledger agree', () => {
  // The same 27-share ticket is approved over Telegram and shown open in the ledger.
  // A staged block would contradict both, so the demo plan must clear every check.
  assert.equal(FAILED_CHECKS.length, 0, `demo ticket is blocked by ${FAILED_CHECKS.map((c) => c.label)}`);
  assert.equal(BLOCK_REASON, null);
  assert.ok(TICKET.rewardRiskNet >= LIMITS.minRewardRisk);
  for (const c of RISK_CHECKS) assert.ok(typeof c.detail === 'string' && c.detail.length);
  // The target is not tuned to clear the check — it is a plain 2R.
  assert.equal(TICKET.rewardRisk, 2);
});

test('charges follow the intraday formula, not a flat fee', () => {
  // min(₹20, 0.03%) per order + STT + exchange + SEBI + stamp + GST (charges.py:88-117).
  const big = roundTripCharges(1000, 1010, 100);
  const small = roundTripCharges(1000, 1010, 2);
  assert.ok(big > small, 'charges must scale with turnover');
  assert.ok(new Set(CLOSED.map((t) => t.charges)).size > 1, 'every trade charged the same — flat fee leaked back in');
  for (const t of CLOSED) assert.ok(t.charges > 0 && t.charges < Math.abs(t.entry * t.qty) * 0.01);
  // Brokerage is capped at ₹20 an order, so a huge order cannot charge proportionally.
  assert.ok(roundTripCharges(10000, 10000, 1000) < 0.01 * 10000 * 1000);
});

test('ledger, charges and performance agree with the trade list', () => {
  assert.equal(GROSS_PNL, Math.round(CLOSED.reduce((n, t) => n + (t.exit - t.entry) * t.qty, 0) * 100) / 100);
  assert.equal(CHARGES, Math.round(CLOSED.reduce((n, t) => n + t.charges, 0) * 100) / 100);
  assert.equal(NET_PNL, Math.round((GROSS_PNL - CHARGES) * 100) / 100);
  assert.ok(NET_PNL < 0, 'the demo desk must not imply a profitable track record');
  assert.equal(WINS, CLOSED.filter((t) => t.exit > t.entry).length);
  assert.equal(WIN_RATE, Math.round((WINS / CLOSED.length) * 100));
  assert.ok(AVG_R_NET < 0);
  assert.equal(
    Math.round(DAILY_PNL.reduce((n, d) => n + d.net, 0) * 100) / 100,
    Math.round(CLOSED.reduce((n, t) => n + t.net, 0) * 100) / 100,
  );
  for (const t of CLOSED) assert.equal(t.exitReason === 'target', t.gross > 0, `${t.symbol} ${t.day}: exit reason disagrees with P&L`);
  for (const [, day] of Object.entries(DAILY_PNL))
    assert.ok(CLOSED.filter((t) => t.day === day.day).length <= LIMITS.maxTradesPerDay);
});

test('cash never exceeds the capital and the open position is funded', () => {
  assert.equal(OPEN_VALUE, TICKET.position);
  assert.equal(FREE_CASH, Math.round((LIMITS.capital + NET_PNL - OPEN_VALUE) * 100) / 100);
  assert.ok(FREE_CASH > 0 && FREE_CASH < LIMITS.capital);
  assert.ok(FREE_CASH + OPEN_VALUE <= LIMITS.capital);
  for (const t of CLOSED) assert.ok(t.qty * t.entry <= LIMITS.maxOrderInr, `${t.symbol} order value breaches the limit`);
});

test('limit rows use the field names the settings screen actually shows', () => {
  const labels = LIMIT_ROWS.map(([l]) => l);
  // desk.settings.tsx:22-35,111
  for (const real of ['Capital ₹', 'Max position % of capital', 'Max loss per trade ₹', 'Max daily loss ₹',
    'Max entries per day', 'No entries after', 'Every entry needs a stop'])
    assert.ok(labels.includes(real), `missing real label: ${real}`);
  for (const invented of ['Concentration', 'Per-trade loss', 'Max trades / day', 'Stop required', 'Paper capital'])
    assert.ok(!labels.includes(invented), `invented label still shown: ${invented}`);
});

test('every date shown on screen is a weekday (not an NSE holiday calendar)', () => {
  for (const d of ALL_DATES) assert.ok(isWeekday(d), `${d} is a weekend`);
  assert.ok(!isWeekday('2026-10-24'), 'guard must actually reject a Saturday');
  assert.ok(ALL_DATES.length >= 9);
});

test('news rows are conspicuously illustrative, never presented as real filings', () => {
  for (const n of NEWS) assert.match(n.headline, /^Illustrative:/, `${n.headline} reads as a real filing`);
  assert.ok(NEWS.length >= 2);
});

test('analysis credits are a separate, non-rupee balance', () => {
  assert.equal(typeof CREDITS, 'number');
  assert.ok(CREDITS > 0 && !String(CREDITS).includes('₹'));
});

test('the ledger on screen can be reconciled: the open position is shown, not just deducted', () => {
  // Free cash deducts the open position, so the screen must show it or a viewer sees
  // ~50k of capital vanish. This is the approval -> ledger link the films teach.
  const open = LEDGER_ROWS.filter((r) => r.status === 'open');
  assert.equal(open.length, 1, 'exactly one open row is expected');
  assert.equal(open[0].qty, QTY, 'the open row must be the ticket that was approved');
  assert.equal(open[0].symbol, PLAN.symbol);
  assert.equal(LEDGER_ROWS[0].status, 'open', 'the open position must be the first row');
  assert.equal(Math.round((LIMITS.capital + NET_PNL - open[0].qty * PLAN.entry) * 100) / 100, FREE_CASH);
  // Any closed rows that are not shown must be announced, not silently dropped.
  const shown = LEDGER_ROWS.filter((r) => r.status === 'closed');
  assert.equal(shown.length, VISIBLE_CLOSED);
  assert.equal(HIDDEN_CLOSED, CLOSED.length - shown.length);
  assert.ok(HIDDEN_CLOSED >= 0);
  // Newest first, so the shown subset is the most recent.
  assert.deepEqual(shown.map((r) => r.day), [...CLOSED].reverse().slice(0, VISIBLE_CLOSED).map((t) => t.day));
});

test('R is the paper engine R: net over PLANNED risk, not the realised move', () => {
  // paper.py:170-175 divides by (entry - stop) * qty.
  for (const t of CLOSED) {
    assert.ok(t.stop < t.entry, `${t.symbol}: stop must sit below entry`);
    const risk = (t.entry - t.stop) * t.qty;
    assert.equal(t.rNet, Math.round(((t.gross - t.charges) / risk) * 100) / 100);
    assert.equal(t.rGross, Math.round((t.gross / risk) * 100) / 100);
  }
  // A 2R plan that hits its target must read near 2R, not below 1R.
  const winners = CLOSED.filter((t) => t.gross > 0);
  assert.ok(winners.length > 0);
  for (const t of winners) assert.ok(t.rGross >= 1.9, `${t.symbol} target trade shows only ${t.rGross}R gross`);
  for (const t of CLOSED.filter((x) => x.gross < 0)) assert.ok(t.rGross <= -0.9, `${t.symbol} stop shows ${t.rGross}R`);
  assert.ok(AVG_R_NET < 0, 'charges must drag the average R below zero');
});
