-- Apply once to the PRODUCTION Supabase SQL editor only after reviewing this migration.
-- RLS is enabled and no anon/authenticated policies are created. API writes use server secret only.
create table if not exists public.market_minute_candles (
  symbol text not null check (symbol in ('EUR/USD','XAU/USD','BTC/USD')),
  minute_open timestamptz not null,
  open double precision not null check (open > 0),
  high double precision not null check (high > 0),
  low double precision not null check (low > 0),
  close double precision not null check (close > 0),
  tick_count integer not null default 1 check (tick_count > 0),
  source text not null default 'biquote_signalr',
  created_at timestamptz not null default now(),
  primary key (symbol, minute_open),
  check (high >= greatest(open, close, low)),
  check (low <= least(open, close, high))
);
create index if not exists market_minute_candles_symbol_time_idx
  on public.market_minute_candles (symbol, minute_open desc);
alter table public.market_minute_candles enable row level security;
