# GEDEÓN 2.0 — release preparation

Release status: PREPARATION / NOT ACTIVATED
Product scope: XAU/USD only
Production scanner: must remain paused until explicit authorization
Notifications: unchanged; do not enable
Order execution: disabled
Paid/limited market data: no new consumption for research

## Product definition

Gedeón 2.0 is the gold-only research and signal-analysis upgrade. Its goal is signal quality and measured expectancy, not a higher signal count.

### Intended analysis stack

- EMA 50/200: trend regime/context, not an automatic entry trigger.
- Liquidity map: prior completed day high/low, completed Asia range high/low, confirmed equal highs/lows and confirmed swing levels.
- Liquidity sweep: wick beyond a known level followed by a completed close back inside.
- BOS/CHoCH: confirmed close beyond a previously confirmed swing pivot.
- Retest: revisit of the broken level/zone followed by a directional close; entry simulation at next bar open.
- ATR(14): stop-distance and volatility normalization only.
- News: contextual risk layer. A missing/unavailable calendar must never block a technical setup. A verified high-impact event may temporarily block fresh entries around the release; no news direction is inferred from the event label alone.

### Timeframes

- M5: simulated execution/entry timing.
- M15: primary structure and directional context.
- M30: optional manual context only; never silently required for automatic M5/M15 evaluation.

### Research gates before release

1. Verify historical XAU/USD M5/M15/M30 data provenance, timezone, gaps, duplicates and coverage.
2. Include bid/ask spread where possible; otherwise report conservative cost scenarios and label results provisional.
3. Prevent look-ahead in prior-day/session ranges, swing confirmation, BOS/CHoCH, retests and indicator values.
4. Run chronological development/validation/untouched holdout splits and walk-forward analysis.
5. Compare EMA-only, liquidity-only, sweep+structure+retest, ATR risk variants and combined model.
6. Report trade count, win rate, expectancy R, profit factor, drawdown, costs and breakdowns by year/session.
7. Require the evidence gate in GOLD_STRATEGY_VALIDATION_PROTOCOL.md before proposing promotion.
8. Run full unit tests, integration tests, API smoke checks and PWA checks.
9. Confirm news unavailable does not veto signals; confirm a verified event window is only a temporary risk filter.
10. Verify fresh price feed health independently of HTTP status or UI connection labels.
11. Verify the active Render deploy and confirm SCANNER_ENABLED remains false.
12. Only after Carlos explicitly authorizes activation may production scanning or notifications be enabled.

## Known work still required

- The historical dataset has not yet been acquired and audited.
- The new strategy rules have not yet been backtested.
- Existing production analysis still contains legacy EMA20/50, RSI, MACD, ADX and Bollinger logic. It must not be represented as the Gedeón 2.0 SMC engine.
- BiQuote feed is an external MT5 observation, not a Vantage execution quote. Validate timestamp freshness, Bid/Ask spread and symbol mapping before any signal is treated as actionable.
- The existing broker session schedule is indicative and not verified against the current Vantage contract specification.
- Calendar source is third-party. When it is unavailable, the technical scanner must continue to assess setups without a news veto.

## Rollback and safety

Gedeón 2.0 is research-only until explicitly promoted. Do not alter SCANNER_ENABLED, notification subscriptions, cron schedules, Twelve Data limits or provider quotas as part of research. Do not execute broker orders. If any data-quality, test, deploy or feed-health check fails, keep production paused.
