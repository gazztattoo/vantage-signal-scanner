# XAU/USD strategy validation lab

Status: research-only. This directory is not imported by the production API and cannot activate the scanner, send notifications, or place orders.

## Objective

Test whether simple, explicit XAU/USD rules have positive expectancy after realistic execution costs. Compare components separately before testing the combined setup:

1. EMA 50/200 trend regime only.
2. Previous-day / Asia-range liquidity sweep only.
3. Sweep + confirmed BOS/CHoCH + retest.
4. The same setups with ATR-based stop sizing.
5. Combined SMC + EMA regime + ATR risk management.

No indicator or setup is assumed effective in advance. The LuxAlgo SMC name describes a discretionary framework; this lab tests transparent, independently implemented rules, not LuxAlgo source code.

## Frozen rule definitions (version 1 — candidates to test, not approved trading rules)

- EMA regime: close above EMA200 and EMA50 above EMA200 = bullish; inverse = bearish. Test EMA50/200 as a filter and as a standalone regime separately.
- Liquidity references: prior completed UTC trading-day high/low and prior completed Asia window high/low. Do not use the current day's final high/low before the day/window closes.
- Sweep: price exceeds a known reference by a configurable buffer, then a completed candle closes back inside the reference. A wick alone is not a signal.
- Structure shift: after the sweep, a completed candle closes beyond the most recent confirmed swing pivot in the opposite direction. Swing pivots must be confirmed only after the required right-hand bars have closed.
- Retest: after the structure break, price revisits the broken level/zone within a fixed number of bars and closes in the trade direction. Entry is at the next bar open, never the signal candle close.
- ATR: Wilder ATR(14) calculated only from completed bars. Stop beyond the sweep extreme plus a pre-registered ATR buffer; no stop distance may be selected using future bars.
- Risk/exit: compare fixed 1R, 1.5R and 2R targets and a time stop as separate variants. Do not select the best after seeing the holdout period.
- News: no news event is required to create a signal. News windows are a separate risk-context cohort and may be analysed independently.

## Data requirements and execution realism

Preferred source for initial research: Dukascopy XAU/USD historical OHLC, UTC timestamps, with BID and ASK where available. Cross-check the same period against another feed or the user's Vantage export before any live use. Dukascopy is not Vantage; feed differences must be reported.

Minimum dataset fields: timestamp_utc, open, high, low, close. Prefer bid/ask OHLC or spread observations. Tick volume is not centralized traded volume for OTC spot gold and must not be described as institutional volume.

- Use M5 as execution timeframe and M15 for context; M30 is a separate optional context cohort.
- Preserve gaps and market closures; never synthesize candles.
- Exclude incomplete candles and duplicate/out-of-order timestamps.
- Entry at next-bar open with spread, slippage and commission applied. If bid/ask history is absent, run conservative spread/slippage scenarios and label results provisional.
- If stop and target are both touched in one candle and tick sequence is unavailable, assume the adverse outcome first.
- No look-ahead: daily/session levels, pivots, EMAs, ATR and structure are available only after their source candles close.

## Statistical protocol

- Chronological split: development 60%, validation 20%, final untouched holdout 20%.
- Walk-forward checks by year and by Asia/London/New York liquidity windows.
- Compare every strategy to buy-and-hold, a simple EMA baseline, and a randomized-entry control with matched trade count, direction, stop and holding horizon.
- Report: number of trades, win rate, mean/median R, expectancy R/trade, profit factor, max drawdown R and %, Sharpe/Sortino where meaningful, exposure, average spread/slippage, and results by year/session/direction.
- Bootstrap confidence intervals for expectancy; permutation/placebo test; sensitivity to doubled costs and nearby parameter values.
- Minimum evidence gate before considering promotion: at least 200 completed trades overall and 50 in the final holdout, positive net expectancy in holdout, profit factor > 1.10 after base costs and still > 1.0 at 2x costs, and no single year/session responsible for nearly all profits. These are project screening thresholds, not a guarantee.
- If a variant fails, record the failure. Do not optimize repeatedly on the final holdout.

## Current state

No verified historical dataset is committed yet. Therefore there are no project-specific backtest statistics and no strategy is approved. The next step is to acquire/cache a multi-year M5 dataset plus M15/M30 context, record source/license/checksum and coverage, then run the frozen tests. No Twelve Data or production-provider credits are used.
