-- Private cache of fully closed BiQuote OHLC bars by timeframe.
create table if not exists public.market_ohlc_candles (
  symbol text not null check (symbol in ('EUR/USD','XAU/USD','BTC/USD')),
  timeframe text not null check (timeframe in ('5min','15min','30min')),
  candle_time timestamptz not null,
  open double precision not null check (open > 0),
  high double precision not null check (high > 0),
  low double precision not null check (low > 0),
  close double precision not null check (close > 0),
  source text not null default 'biquote_ohlc',
  created_at timestamptz not null default now(),
  primary key (symbol, timeframe, candle_time),
  check (high >= greatest(open, close, low)),
  check (low <= least(open, close, high))
);
create index if not exists market_ohlc_candles_lookup_idx
  on public.market_ohlc_candles (symbol, timeframe, candle_time desc);
alter table public.market_ohlc_candles enable row level security;
