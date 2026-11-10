-- Reference schema: Supabase project global-signal-scanner-test only.
-- The hosted TEST migration create_mt5_market_candles was applied via Supabase MCP.
-- Do not apply this file to the production project.
create table public.mt5_market_candles (
    symbol text not null check (symbol in ('EUR/USD', 'XAU/USD', 'BTC/USD')),
    timeframe text not null check (timeframe in ('5min', '15min', '30min')),
    broker_symbol text not null,
    open_time timestamptz not null,
    server_time timestamp without time zone not null,
    server_utc_offset_minutes smallint not null check (server_utc_offset_minutes in (120, 180)),
    open numeric(24, 10) not null check (open > 0),
    high numeric(24, 10) not null check (high > 0),
    low numeric(24, 10) not null check (low > 0),
    close numeric(24, 10) not null check (close > 0),
    tick_volume bigint not null default 0 check (tick_volume >= 0),
    spread_points integer not null default 0 check (spread_points >= 0),
    source text not null default 'VANTAGE_MT5' check (source = 'VANTAGE_MT5'),
    received_at timestamptz not null default now(),
    constraint mt5_market_candles_ohlc_valid check (
      high >= greatest(open, low, close)
      and low <= least(open, high, close)
    ),
    primary key (symbol, timeframe, open_time)
);

create index mt5_market_candles_latest_idx
  on public.mt5_market_candles (symbol, timeframe, open_time desc);

alter table public.mt5_market_candles enable row level security;
revoke all on table public.mt5_market_candles from public, anon, authenticated;
grant select, insert, update on table public.mt5_market_candles to service_role;

create policy "service_role_can_manage_vantage_mt5_shadow_candles"
  on public.mt5_market_candles
  for all
  to service_role
  using (true)
  with check (true);
