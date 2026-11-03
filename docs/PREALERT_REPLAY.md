# Offline historical replay input

The replay runner `scripts/replay_prealerts.py` consumes a local CSV. It does not connect to Twelve Data, another market provider, Supabase, or Render.

## CSV schema

Required columns:

`timestamp,symbol,direction,price,zone_low,zone_high,setup_valid,confirmation_valid,invalidated`

Optional `data_timestamp` records the actual quote timestamp separately from the replay timestamp.

- Timestamps: ISO 8601, preferably UTC.
- Symbols: EUR/USD, XAU/USD, BTC/USD.
- direction: BUY or SELL.
- setup_valid / confirmation_valid / invalidated: true or false.
- Prices and zones must use the same quote convention and precision as the intended execution venue.
- Rows may be unordered; replay sorts them chronologically.

## Example

```csv
timestamp,symbol,direction,price,zone_low,zone_high,setup_valid,confirmation_valid,invalidated
2026-11-03T12:00:00Z,EUR/USD,BUY,1.1010,1.1000,1.1005,true,false,false
2026-11-03T12:00:20Z,EUR/USD,BUY,1.1008,1.1000,1.1005,true,false,false
2026-11-03T12:00:40Z,EUR/USD,BUY,1.1004,1.1000,1.1005,true,true,false
```

## Run

```bash
python scripts/replay_prealerts.py market_snapshots.csv --max-data-age 90 --ttl 180 --output replay_report.json
```

The report includes lifecycle counts, unconfirmed rate and time between first prealert and confirmation. The median is the middle sorted observation (for even counts, the upper middle observation); use a statistical analysis package for a conventional interpolated median if required.

## Important limitation

This replay evaluates supplied strategy snapshots; it does not calculate EMA/RSI/MACD/ADX or derive trade zones from raw OHLC candles. To run a valid historical market replay, supply a dataset with the strategy context already computed from candles, plus timestamped prices. Do not label synthetic examples as historical market results.
