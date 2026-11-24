# FXCM XAU/USD research gate

Status: isolated research only. This work is on `fxcm-gold-research`, based on `push-test`.
It is not merged, deployed, or connected to the TEST or production scanner.

## What FXCM documents

- FXCM lists free Java API and ForexConnect API access for demo or live accounts.
- FXCM lists a per-symbol quote limit of up to 2–3 updates/second for those APIs.
- FXCM's Socket REST guide documents a market-data instrument discovery request
  (`/trading/get_instruments`), streaming market-data subscriptions, and historical
  candles at `/candles/{offer_id}/{period_id}`.
- The documented candle periods include `m1`, `m5`, and `m15`.
- The documented candle tuple is:
  `[epoch, BidOpen, BidClose, BidHigh, BidLow, AskOpen, AskClose, AskHigh, AskLow, TickQty]`.
- The API documentation requires an access token generated from Trading Station Web.

Official references:
- https://www.fxcm.com/eu/algorithmic-trading/market-data/
- https://www.fxcm.com/eu/algorithmic-trading/compare-api/
- https://fxcm-rest.readthedocs.io/en/latest/socketrestapispecs.html
- https://www.fxcm.com/public-docs/fclite/gettingstarted/authentication/getting_access.html

## Important limitations and checks

1. FXCM's public Socket REST specification is old (the published revision history ends
   in 2019). Confirm with FXCM support that the documented REST/socket endpoints remain
   supported for a new EU demo account before implementing a live connector.
2. A demo account is simulated. FXCM's application disclosure says demo prices may be
   delayed and throttled (up to two quotes/second in the cited app disclosure). A demo
   feed is suitable for connectivity/parser tests, not proof of executable live pricing.
3. Confirm the XAU/USD symbol and offer ID dynamically using the account's instrument
   list. Never hardcode an assumed offer ID or silently substitute a gold futures symbol.
4. Keep Bid and Ask candle series separate. Do not merge or average them into a synthetic
   OHLC series for strategy decisions.
5. Do not treat FXCM prices as Vantage execution prices. Measure basis, spread, timestamp
   alignment, missing bars, and candle differences against Vantage MT5.
6. No strategy changes, push notifications, orders, or production changes are permitted
   during this research phase.

## Parser implementation

`fxcm_research_adapter.py` is a pure offline parser for the documented historical candle
array. It performs no network calls, authentication, persistence, or trading. It rejects
invalid OHLC, duplicate/misaligned timestamps, non-completed candles, and stale latest
closed bars when a freshness threshold is supplied.

Run the isolated tests:

```bash
python -m unittest discover -s tests -p "test_fxcm_research_adapter.py" -v
```

## Required evidence before integration

- EU demo account credentials and confirmation that API access is enabled.
- Written confirmation from FXCM support of the current API endpoints and the intended
  internal algorithmic-analysis use.
- Verified XAU/USD instrument identifier from the demo account.
- At least one complete market-session capture with source timestamps, Bid/Ask ticks,
  and closed M5/M15 candles.
- Side-by-side comparison against Vantage MT5 candles, with no provider stitching.
- Separate review of demo-vs-live price differences before any live feed is considered.

No credentials belong in source code, GitHub commits, or chat messages. If a connection is
approved later, secrets must be stored only in the TEST service's secret environment.
