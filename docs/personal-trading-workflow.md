# Using the private trading platform

This platform separates research, simulated trading and broker execution. Start in **Trade Desk → Start here**. Its readiness panel reports actual sessions, feed coverage, background loops and active Paper strategies. An operational server is not proof a strategy makes money.

## Connect Kite market data

1. Open **Trade Desk → Zerodha**. Save the API key and secret of your paid Kite Connect app. Use the exact redirect URL shown on that screen in the Kite app settings. Saved keys are encrypted.
2. Log in to Zerodha for the day. Sessions expire each morning, so repeat the login on trading days.
3. Start **F&O stocks** or **NIFTY500**, or choose a small custom list. Universe history loads in the background under a bounded request budget. Hundreds of symbols take several minutes; inspect coverage instead of assuming Start means complete.
4. Check per-symbol sources in scanners and Trade Desk. Fresh Kite data is private to the selected connection; unavailable symbols use an explicitly marked delayed fallback. Scanner indicators and entries use completed candles. A live price does not make an unfinished candle a breakout.
5. If sharing with another internal account, grant and select the market-data connection in the sharing panel. Broker credentials, order permission and paper portfolios stay with their respective owners. The owner controls stream symbols. A server restart currently requires starting the stream again.

## Build a strategy and test it

- From a completed NSE stock analysis, use **Draft strategy** beside viable INR levels. The preview preserves absolute entry, stop and target, shows the research date, and explains the conversion to intraday rules.
- From a saved scanner, draft either a dynamic scanner strategy or fixed selected symbols. A whole-scanner strategy follows its current matches on later scans; the preview list is not a permanent watchlist.
- Select a paper portfolio and review the candle interval, entry rule, stop, target, order type and limits. Saving a draft always saves **Off**. No draft starts trading.
- Open **Strategies & live**, run a historical test, then deliberately choose **Paper**. The strategy loop evaluates rules during eligible sessions and creates simulated tickets/trades using that portfolio's capital and risk limits.
- Inspect diagnostics when nothing trades: missing or delayed candles, no completed trigger, exhausted daily signal limits, cash/risk blocks and expired sessions are different conditions.

Named portfolios let you compare approaches without creating more login accounts. They use separate simulated cash and ledgers. Existing Default remains the optional portfolio supplied to stock analysis.

## Understand historical tests

Saved strategy tests are immutable and retain the strategy version, configuration, costs and source assumptions. The engine replays cross-symbol signals in time order on one simulated account, reserves cash for pending/open positions, releases partial-exit cash and checks sizing and risk at each signal.

Daily technical scanner conditions can be replayed on an explicitly listed frozen universe using only data available before that session. Today's universe creates survivorship bias. Historical fundamental, index-membership and intraday scanner conditions are refused; past exchange risk facts are unavailable and disclosed. These tests do not reconstruct missing data or establish live profitability.

Paper/backtests support partial exits, breakeven and trailing rules. Advanced exit policies are currently refused in live Confirm/Auto modes; the form requires an explicit choice to remove an incompatible policy.

## Track outcomes

Use **Paper ledger** for entries, remaining positions and exits. Use **Performance** to filter by portfolio, strategy and strategy version, distinguish realized from open P&L, inspect source freshness, add journal notes/tags and export CSV. Charges are estimates rather than broker contract-note reconciliation. Paper and live totals stay separate. Unavailable or stale marks are shown rather than counted as fresh profits. NIFTY benchmark availability is explicit.

## Supervise from Telegram

Use the existing verified pairing in **Messages**. Enable supervision for status/performance and the two-step pause/resume controls. Resuming never arms a broker account or enables Auto. Daily summaries are opt-in and deduplicated per trading day. Live cards open app review; they do not approve broker trades in chat. Existing paper approval buttons remain available.

## Real-money rollout

This release keeps `KITE_ORDERS_ENABLED=0`. Paper strategies can run automatically; broker orders cannot be sent while that flag is off. Confirm mode requires typed ticket confirmation. Auto requires a deliberate opt-in bound to the saved strategy version, plus owner binding, daily login/arming, fresh live data, broker funds, risk checks and kill-switch gates. Named paper books do not authorize live orders.

The next external validation is a connected Kite trial during a full NSE trading session: verify universe bootstrap, completed-candle triggers, simulated fills/exits, partial-exit accounting, Telegram supervision and end-of-day performance against the observed market. Only after reviewing that evidence should a separate, deliberate live rollout be considered.
