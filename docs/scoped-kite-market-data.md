# Scoped Kite scanner data

Select your own connection, or one explicitly granted to your account, on
**Desk → Zerodha connection**. The connection owner starts the stream for
custom symbols, F&O stocks, or NIFTY500. Recipients cannot start or alter the
owner's stream. Data access never delegates broker credentials or order authority.

The application accepts at most **500 unique symbols per stream**, covering the
217-stock F&O snapshot and NIFTY500. This application bound is below Kite's
[3,000 instruments per WebSocket connection](https://kite.trade/docs/connect/v3/websocket/).
Missing instruments and larger selections fail explicitly; selections are never
silently truncated. Universe membership comes from the existing scanner metadata.

Starting a stream returns before history bootstrap completes. Current-session
minute history goes first, with the existing bounded retry schedule. Then each
symbol receives at most two scanner-history calls: 730 calendar days of daily
candles and ten calendar days of five-minute candles, retaining up to 320 actual
candles per series in the owner's hub. No synthetic candles fill holes. The
historical request gate spaces starts by 350ms per owner, including failures and
shared readers, below Kite's documented
[three historical calls per second](https://kite.trade/docs/connect/v3/exceptions/).
All of these calls are read-only. At 500 symbols the initial 1,500 requests need
at least about nine minutes, plus network latency and bounded session retries.
Failed scanner history is visible and retried only by restarting the stream.

Private candles remain in process memory, never in the global Yahoo scanner DB,
engine panels, or public history cache. Stopping/restarting drops the hub and
cancels bootstrap. Owner session expiry, revocation, or a changed direct grant
prevents subsequent reads; scans recheck access before returning private output.
The request budget and hub are process-local: use the existing single API process
for a connection; multiple API worker processes would need a shared rate budget
and a common private hub. A canceled blocking HTTP call may finish in its worker
thread, but cannot publish candles to a replacement hub.

General scans use selected-owner completed candles where available and public
delayed candles elsewhere. Five-minute history and session candles feed aligned
15-minute/hourly aggregates; aggregates with missing constituent candles are
excluded. Weekly/monthly aggregates wait until their period's final date has
passed. A short history can leave an indicator unevaluable; that is not a match.
Prices, volume and daily change columns retain their closed-daily meaning;
fundamentals and liquidity metadata remain public snapshots. Fresh tick quotes
are separate timestamped fields and visible on the connection page.

ORB keeps its existing coverage fields and adds per-symbol source coverage.
Kite charts retain strict completeness rules; delayed charts retain conservative
delayed-bar handling. A mixed scan is labeled mixed, and partial/missing history
stays visible. Neither a source label nor a scanner match authorizes an order.

Real 217/500-symbol provider throughput and an uninterrupted full-session trial
still require the user's connection and an actual trading session. Automated
tests use controlled providers and establish isolation/coverage behavior only.
