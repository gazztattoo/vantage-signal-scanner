# VANTAGE precision research — protocol

## Scope and isolation
This is an offline research workflow on `vantage-precision-research`. It does not call Twelve Data, Render, Supabase, a broker, or order/notification endpoints. It must not be merged or deployed without explicit approval.

## Baseline copied from the current production implementation
- EMA 20 / EMA 50
- RSI 14: BUY 52–68; SELL 32–48
- MACD 12 / 26 / 9 histogram direction
- Bollinger middle: close above for BUY, below for SELL
- ADX 14 Wilder: actual executable threshold is **ADX >= 20**
- Minimum confluence: 3 of 4; BUY/SELL score must strictly exceed the opposite side
- Stop: 1.5 x ATR(14); target: 3.0 x ATR(14)

The production response's human-readable `method` string currently says ADX >=18, but the signal decision code uses ADX >=20. Research follows the executable decision code; this discrepancy is documented, not silently changed.

## Data and execution assumptions
The downloader requests public Dukascopy M1 BID and ASK bars. The backtest aggregates only complete, contiguous M1 groups into M5/M15/M30. Indicators use the midpoint of BID/ASK OHLC. A signal is evaluated only after a completed timeframe candle; entry is at the next timeframe bar's first M1 open. Long entries use ASK and long exits use BID; short entries use BID and short exits use ASK. SL/TP are evaluated against the appropriate exit-side M1 range. If SL and TP are both touched in the same M1 candle, SL is assumed first. Positions do not overlap within one symbol/timeframe experiment. Open trades at dataset end are not counted as wins/losses.

## Metrics
Each parameter combination reports train and chronological holdout (70/30 by trade sequence): closed trade count, win rate, expectancy in R, profit factor, max drawdown in R, and open-at-end count. The grid varies ADX floor (18/20/25/30) and minimum score (3/4); it does not change RSI bands or the ATR exits.

## Acceptance limits
A tiny sample is descriptive only. Do not select parameters from the holdout set and then report that same holdout as unbiased validation. For a real conclusion, require several hundred closed trades per asset/timeframe or clearly state that evidence is insufficient, inspect stability across separate periods, and use a final untouched test period after parameter selection. These results are not a win-rate promise and do not establish broker-specific execution quality.

## Current blockers
- A successful Actions run and its dataset artifact have not yet been independently verified.
- The previously available local sample contains only about two days for XAUUSD and BTCUSDT, with no usable EURUSD file; it is not enough to validate the strategy.
- Until the three BID/ASK datasets are present and verified, no profitability or parameter recommendation should be issued.
