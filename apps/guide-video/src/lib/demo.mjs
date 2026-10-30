// One fictional demo desk. Every figure shown in the films is derived here, so the
// sizing ticket, the paper ledger and the performance screen cannot disagree.
//
// Nothing is captured from a real account and no price is a quote: symbols are real
// NSE tickers but every price, date and headline below is an invented DEMO value.
// The sizing and charges maths mirror apps/api/desk/sizing.py and
// apps/api/desk/market/charges.py so the films teach the real behaviour.

const INR = (n) => `₹${Math.round(n).toLocaleString('en-IN')}`;
const INR2 = (n) => `₹${n.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
const PRICE = (n) => n.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const r2 = (n) => Math.round(n * 100) / 100;

/** Field names as desk.settings.tsx actually labels them. */
export const LIMITS = {
  capital: 200000,
  cashBufferPct: 10,
  maxPositionPct: 25,
  maxOrderInr: 50000,
  maxLossPerTradeInr: 2000,
  maxDailyLossInr: 5000,
  maxTradesPerDay: 3,
  minRewardRisk: 1.5,
  noEntriesAfter: '14:30',
  stopRequired: true,
};
export const LIMIT_ROWS = [
  ['Capital ₹', INR(LIMITS.capital)],
  ['Cash buffer %', `${LIMITS.cashBufferPct}%`],
  ['Max position % of capital', `${LIMITS.maxPositionPct}%`],
  ['Max order ₹', INR(LIMITS.maxOrderInr)],
  ['Max loss per trade ₹', INR(LIMITS.maxLossPerTradeInr)],
  ['Max daily loss ₹', INR(LIMITS.maxDailyLossInr)],
  ['Max entries per day', String(LIMITS.maxTradesPerDay)],
  ['Min reward:risk (after charges)', LIMITS.minRewardRisk.toFixed(2)],
  ['No entries after', LIMITS.noEntriesAfter],
  ['Every entry needs a stop', 'On'],
];
export const CREDITS = 12;

// ---------------------------------------------------------------- charges
// Port of apps/api/desk/market/charges.py for the intraday (MIS) product.
const RATES = {sttIntradaySell: 0.00025, exchangeTxn: 0.0000297, sebiFee: 10 / 1e7, stampIntradayBuy: 0.00003, gst: 0.18};
const perOrder = (value) => Math.min(20, 0.0003 * value);

/** Total statutory + brokerage charges for one intraday round trip. */
export function roundTripCharges(entry, exit, qty) {
  const buy = entry * qty;
  const sell = exit * qty;
  const turnover = buy + sell;
  const brk = perOrder(buy) + perOrder(sell);
  const txn = RATES.exchangeTxn * turnover;
  const sebi = RATES.sebiFee * turnover;
  const gst = RATES.gst * (brk + txn + sebi);
  return r2(brk + RATES.sttIntradaySell * sell + txn + sebi + RATES.stampIntradayBuy * buy + gst);
}

// ---------------------------------------------------------------- the plan
export const PLAN = {
  symbol: 'INFY',
  name: 'Infosys',
  entry: 1842.5,
  stop: 1828.0,
  target: 1871.5,
  flatBy: '15:10',
  entryTime: '09:35',
  date: '2026-10-29',
};
export const RISK_PER_SHARE = r2(PLAN.entry - PLAN.stop);

// ---------------------------------------------------------------- sizing
// sizing.py takes the MINIMUM across every limit and proposes that quantity. It never
// proposes a quantity it then blocks, so the film must not show one either.
const shares = (budget, per) => Math.floor(budget / per);
const cashAvailable = LIMITS.capital - (LIMITS.capital * LIMITS.cashBufferPct) / 100;

const RAW_LIMITS = [
  {name: 'max loss', shares: shares(LIMITS.maxLossPerTradeInr, RISK_PER_SHARE),
   how: `${INR(LIMITS.maxLossPerTradeInr)} max loss / ${INR2(RISK_PER_SHARE)} risk a share`},
  {name: 'position cap', shares: shares((LIMITS.capital * LIMITS.maxPositionPct) / 100, PLAN.entry),
   how: `${LIMITS.maxPositionPct}% of ${INR(LIMITS.capital)} / ${INR2(PLAN.entry)}`},
  {name: 'max order', shares: shares(LIMITS.maxOrderInr, PLAN.entry),
   how: `${INR(LIMITS.maxOrderInr)} / ${INR2(PLAN.entry)}`},
  {name: 'cash', shares: shares(cashAvailable, PLAN.entry),
   how: `capital ${INR(LIMITS.capital)} minus the ${INR((LIMITS.capital * LIMITS.cashBufferPct) / 100)} buffer = ${INR(cashAvailable)} / ${INR2(PLAN.entry)}`},
];
export const QTY = Math.min(...RAW_LIMITS.map((l) => l.shares));
export const BOUND_BY = RAW_LIMITS.find((l) => l.shares === QTY).name;
export const SIZE_LIMITS = RAW_LIMITS.map((l) => ({...l, binds: l.name === BOUND_BY}));

const lossAtStop = r2(QTY * RISK_PER_SHARE);
const gainAtTarget = r2(QTY * (PLAN.target - PLAN.entry));
const chargesAtStop = roundTripCharges(PLAN.entry, PLAN.stop, QTY);
const chargesAtTarget = roundTripCharges(PLAN.entry, PLAN.target, QTY);
const lossAtStopNet = r2(lossAtStop + chargesAtStop);
const gainAtTargetNet = r2(gainAtTarget - chargesAtTarget);

export const TICKET = {
  position: r2(QTY * PLAN.entry),
  lossAtStop,
  gainAtTarget,
  rewardRisk: r2((PLAN.target - PLAN.entry) / RISK_PER_SHARE),
  chargesAtStop,
  chargesAtTarget,
  lossAtStopNet,
  gainAtTargetNet,
  rewardRiskNet: r2(gainAtTargetNet / lossAtStopNet),
};

// risk.py:242-246 compares the NET reward:risk against min_reward_risk. These are the
// checks the real ticket runs. This plan passes all of them, which is what lets the
// same 27-share ticket honestly appear approved in Telegram and open in the ledger.
export const RISK_CHECKS = [
  {label: 'Stop present', pass: true, detail: INR2(PLAN.stop)},
  {label: `Loss at stop ≤ ${INR(LIMITS.maxLossPerTradeInr)}`, pass: TICKET.lossAtStop <= LIMITS.maxLossPerTradeInr, detail: INR2(TICKET.lossAtStop)},
  {label: `Position ≤ max order ${INR(LIMITS.maxOrderInr)}`, pass: TICKET.position <= LIMITS.maxOrderInr, detail: INR2(TICKET.position)},
  {label: `Reward:risk (net) ≥ ${LIMITS.minRewardRisk.toFixed(2)}`, pass: TICKET.rewardRiskNet >= LIMITS.minRewardRisk, detail: TICKET.rewardRiskNet.toFixed(2)},
  {label: `Daily loss ≤ ${INR(LIMITS.maxDailyLossInr)}`, pass: true, detail: INR(0)},
  {label: `Entries today ≤ ${LIMITS.maxTradesPerDay}`, pass: true, detail: '0'},
  {label: `No entries after ${LIMITS.noEntriesAfter}`, pass: true, detail: PLAN.entryTime},
];
export const FAILED_CHECKS = RISK_CHECKS.filter((c) => !c.pass);
// Null while every check passes. Blocked tickets are explained in the guide callout
// rather than staged, so the demo keeps one consistent plan/approval/ledger state.
// Wording mirrors risk.py:244-246 if a future demo plan ever does fail.
export const BLOCK_REASON = FAILED_CHECKS.length
  ? `reward:risk: ${TICKET.rewardRiskNet.toFixed(2)} after charges (gross ${TICKET.rewardRisk.toFixed(2)}) is under your minimum ${LIMITS.minRewardRisk}`
  : null;

// ---------------------------------------------------------------- ledger
// Each row carries the PLANNED stop, because paper.py:170-175 divides R by the planned
// risk (entry - stop) x qty, not by the realised move. Stops sit a plain 1R below entry
// and targets 2R above, matching the plans the films teach.
export const CLOSED = [
  {day: '2026-10-22', symbol: 'TATAMOTORS', qty: 70, entry: 705.1, stop: 699.4, exit: 699.4, status: 'closed'},
  {day: '2026-10-23', symbol: 'HDFCBANK', qty: 30, entry: 1610.0, stop: 1600.9, exit: 1628.2, status: 'closed'},
  {day: '2026-10-26', symbol: 'SBIN', qty: 60, entry: 812.4, stop: 806.9, exit: 806.9, status: 'closed'},
  {day: '2026-10-27', symbol: 'INFY', qty: 26, entry: 1822.0, stop: 1813.7, exit: 1838.6, status: 'closed'},
  {day: '2026-10-28', symbol: 'TATAMOTORS', qty: 65, entry: 712.3, stop: 707.1, exit: 707.1, status: 'closed'},
  {day: '2026-10-28', symbol: 'HDFCBANK', qty: 28, entry: 1598.5, stop: 1594.2, exit: 1594.2, status: 'closed'},
].map((t) => {
  const gross = r2((t.exit - t.entry) * t.qty);
  const charges = roundTripCharges(t.entry, t.exit, t.qty);
  const risk = (t.entry - t.stop) * t.qty; // paper.py:170
  return {
    ...t,
    gross,
    charges,
    net: r2(gross - charges),
    exitReason: t.exit > t.entry ? 'target' : 'stop',
    rGross: risk > 0 ? r2(gross / risk) : null,
    rNet: risk > 0 ? r2((gross - charges) / risk) : null,
  };
});

export const CHARGES = r2(CLOSED.reduce((n, t) => n + t.charges, 0));
export const GROSS_PNL = r2(CLOSED.reduce((n, t) => n + t.gross, 0));
export const NET_PNL = r2(GROSS_PNL - CHARGES);
export const WINS = CLOSED.filter((t) => t.gross > 0).length;
export const WIN_RATE = Math.round((WINS / CLOSED.length) * 100);
export const AVG_R_NET = r2(CLOSED.reduce((n, t) => n + t.rNet, 0) / CLOSED.length);
export const AVG_R_GROSS = r2(CLOSED.reduce((n, t) => n + t.rGross, 0) / CLOSED.length);
export const OPEN_VALUE = TICKET.position;
export const FREE_CASH = r2(LIMITS.capital + NET_PNL - OPEN_VALUE);

/** Exactly the rows the paper ledger screen renders, newest closed first after the
 *  open position. Exported so a check can fail if free cash deducts an open position
 *  the screen never shows. */
export const VISIBLE_CLOSED = 4;
export const LEDGER_ROWS = [
  {day: PLAN.date, symbol: PLAN.symbol, qty: QTY, status: 'open', entry: PLAN.entry,
   entryTime: PLAN.entryTime, exit: null, gross: null, charges: null, net: null},
  ...[...CLOSED].reverse().slice(0, VISIBLE_CLOSED),
];
export const HIDDEN_CLOSED = CLOSED.length - VISIBLE_CLOSED;

/** Net paper P&L per trading day, for the performance chart. */
export const DAILY_PNL = [...new Set(CLOSED.map((t) => t.day))].map((day) => ({
  day,
  net: r2(CLOSED.filter((t) => t.day === day).reduce((n, t) => n + t.net, 0)),
}));

// ---------------------------------------------------------------- context
/** Invented, clearly-labelled disclosure examples. Not real filings. */
export const NEWS = [
  {symbol: 'INFY', headline: 'Illustrative: quarterly results filing', date: '2026-10-23'},
  {symbol: 'TATAMOTORS', headline: 'Illustrative: board meeting intimation', date: '2026-10-22'},
];

/** Weekday check only — this is not an NSE holiday calendar. */
export const isWeekday = (iso) => {
  const d = new Date(`${iso}T00:00:00Z`).getUTCDay();
  return d >= 1 && d <= 5;
};

export const ALL_DATES = [PLAN.date, ...CLOSED.map((t) => t.day), ...NEWS.map((n) => n.date)];
export const fmt = {INR, INR2, PRICE};
