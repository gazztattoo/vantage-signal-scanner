# Market-data provider research and safe router plan

**Prepared:** 2026-11-09  
**Branch:** `push-test`  
**Decision status:** research complete; no live provider is approved or activated.

## Objective

Find a low-cost source for closed OHLC candles for EUR/USD, XAU/USD and BTC/USD on M5, M15 and M30, while preserving the scanner's strict data-quality and quota controls.

A provider that exposes the same ticker name is not automatically equivalent to Vantage. FX and gold are OTC/CFD instruments; prices, bid/ask, spread, rollover and candle construction can differ by broker. Never combine candles from different sources inside one analysis series.

## Candidate assessment

| Provider | Relevant coverage | Cost / limits stated in docs | Assessment |
|---|---|---|---|
| **Vantage MT5 terminal feed** | Exact broker symbols and broker OHLC, if exported from a logged-in MT5 terminal | No public, self-service market-data REST API found. Vantage Connect advertises institutional market-data / execution connectivity over FIX and MT bridges. | **Best source of truth.** A small MQL5 exporter running in desktop MT5 or a VPS can push closed M5/M15/M30 candles to the scanner. The iPhone MT5 app alone cannot host an Expert Advisor. |
| **biquote** | EURUSD, XAUUSD, BTCUSD; documented M1/M5/M15/M30 and up to 1,000 bars per request | Public read endpoints, no key; documentation claims 15,000 requests/minute per IP | **Best all-three candidate for a controlled shadow evaluation, not approved for trading.** Its docs say candles combine Yahoo Finance bootstrap, live tick aggregation and MT5 historical feeds; the symbol catalogue says only around 280 of roughly 1,700 instruments quote at a time. Feed lineage and broker identity need validation. Always discard `isOpen=true` bars and inspect source/freshness. |
| **AlgoSpecial preview API** | EURUSD, XAUUSD and BTCUSDT datasets; M5/M15/M30 | 60 requests/minute, 1,000/day per IP, 2,000 rows and 31-day range per request | **Historical/research candidate.** Documentation says timestamps use broker-server time, some legacy FX data has zero tick-volume/spread, and data is supplied as-is. Good for cross-checking and short backtests after timezone/quality reconciliation; not documented as a live feed. |
| **Dukascopy historical data** | EURUSD and XAUUSD history at M1/M5/M15/M30; BTCUSD historical candles are also exposed by the community dukascopy-node client | Historical downloads are free through the documented open-source client; large tick downloads involve many hourly batches | **Strong offline validation source.** Use bid/ask-aware history for backtests, not as a guaranteed live provider. The wrapper labels its real-time retrieval experimental, so do not use that path for live signals. |
| **Coinbase Exchange public candles** | BTC-USD spot; 5-minute and 15-minute granularity are documented. M30 can be derived from complete M15 bars. | Public endpoint; historic-rate guidance says not to poll it frequently; historical response is capped at 300 candles per request in the published API spec | **Crypto-only comparator.** More transparent exchange provenance than a generic aggregator, but BTC-USD spot is not Vantage BTC/USD CFD. Do not use it to set Vantage entry/SL/TP levels. |
| **Alpha Vantage** | FX intraday intervals include 5/15/30 minutes in the API schema | The official docs mark FX_INTRADAY as a premium function | **Not a free-tier replacement.** |
| **TraderMade free tier** | Forex/CFD/crypto live mid-prices, but no minute/hour historical endpoints on the free plan | Free trial live-price endpoints; minute/hour historical endpoints are not available on free tier | **Not suitable for M5/M15/M30 candle history.** |
| **OHLC.io Free** | The free plan is restricted to a small US-stock list; intraday cross-asset data requires a paid plan | Paid plan required for intraday bars; Free does not cover the target FX/gold/crypto symbols | **Not suitable for this requirement.** |

### Sources checked

- Vantage Connect API solutions: https://connect.vantagemarkets.co.uk/trading-solutions/api-solutions/
- biquote API reference (OHLC, symbol catalogue, provenance and limits): https://biquote.io/docs/
- AlgoSpecial free preview API (coverage, rate limits, timezone and usage terms): https://www.algospecial.com/data-api/
- Dukascopy historical instrument example: https://www.dukascopy-node.app/instrument/eurusd
- Dukascopy-node real-time method (explicitly experimental): https://www.dukascopy-node.app/downloading-real-time-data
- Coinbase public API FAQ: https://help.coinbase.com/en/pro/other-topics/api/faq-on-api
- Alpha Vantage API docs: https://www.alphavantage.co/documentation/
- TraderMade free-tier guide: https://marketdata.tradermade.com/docs/restful-api/free-tier.pdf
- OHLC plan limits: https://ohlc.io/docs/limits and https://ohlc.io/pricing

## Recommended architecture

1. **Broker-first source of truth:** ingest closed candles from the actual Vantage MT5 terminal using a small MQL5 exporter. The exporter posts only completed bars and includes broker symbol, timeframe, bar-open timestamp, bid/ask basis, server time/offset and terminal connection state.
2. **Shadow providers:** biquote, AlgoSpecial, Dukascopy and Coinbase stay in an isolated comparison pipeline. They must not silently become the trade-signal source. Each series retains its own provider/source identity.
3. **Single normalized candle contract:** `symbol`, `timeframe`, `open_time_utc`, `open`, `high`, `low`, `close`, optional `tick_volume`, `provider`, `source_symbol`, `received_at_utc`, `is_closed`, `quality_flags`.
4. **Cache before quota:** the scanner scheduler fetches once per new closed candle, stores the normalized series, and serves all browser clients from cache. Browser refreshes must never trigger provider calls. Proposed initial cache TTLs: M5 20 seconds, M15 45 seconds, M30 90 seconds; these are configuration starting points, not claims about provider latency.
5. **Global hard quotas:** count requests per provider in a shared atomic store (Supabase/Postgres RPC or Redis), not process memory. Reserve a safety margin below each provider's published limit. Each request must reserve budget before calling; no retry storm; provider 429 responses open a circuit breaker until Retry-After/reset.
6. **Freshness gate:** keep only closed bars. Reject future timestamps, duplicates, out-of-order rows, invalid OHLC ranges, missing intervals and stale latest bars. A stale feed returns a visible data-unavailable state and **never** generates a signal.
7. **No cross-provider stitching:** one analysis uses one provider's complete contiguous candle series. Do not fill gaps from another vendor or combine an exchange spot close with a Vantage CFD price.
8. **Fail closed:** if the primary source is unavailable or quota is exhausted, do not switch automatically to an unvalidated source. Show provider, last candle time, age and reason; mark the instrument as not eligible for signals.
9. **Mode isolation:** `push-test` remains `DATA_MODE="mock"` with `API_KEY=None`. Its analysis endpoints must stay deterministic/local and must not call any provider. Production stays paused until Carlos explicitly approves a named provider, budget and validation window.

## Proposed rollout gates

- **Gate 0 — code only:** normalized contract, cache, shared quota accounting, metrics and mock fixtures; all tests offline.
- **Gate 1 — historical shadow validation:** compare AlgoSpecial/Dukascopy against saved Vantage candles for identical timestamps and bid/ask conventions. No live endpoints and no signals.
- **Gate 2 — live shadow trial:** only after explicit approval, collect a time-boxed sample from one named feed. Compare at least 500 closed candles per symbol/timeframe, timestamps, candle gaps, OHLC deviation, spread and freshness. Do not publish entry levels from the shadow source.
- **Gate 3 — source eligibility:** choose per instrument only when divergence is within a pre-agreed tolerance and the feed's availability, rate limit and usage rights are confirmed.
- **Gate 4 — production proposal:** separate review and explicit activation; no automatic switch-on after quota reset.

## Immediate conclusion

No free public API has been verified as a broker-equivalent, reliable live source for all three Vantage instruments. The safest practical route is the Vantage MT5 terminal feed as primary, with biquote as a possible all-three shadow comparator, AlgoSpecial/Dukascopy for historical cross-checks and Coinbase for BTC-USD spot comparison. This research did not call any market-data endpoint and did not consume Twelve Data credits.
