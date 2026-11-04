-- Global, atomic UTC-day request budget for all production Biquote calls.
create schema if not exists private;

create table if not exists private.biquote_usage_daily (
    usage_date date primary key,
    request_count integer not null default 0 check (request_count >= 0),
    updated_at timestamptz not null default now()
);

alter table private.biquote_usage_daily enable row level security;
revoke all on schema private from public, anon, authenticated;
revoke all on table private.biquote_usage_daily from public, anon, authenticated;
grant usage on schema private to service_role;
grant select, insert, update on table private.biquote_usage_daily to service_role;

create or replace function public.consume_biquote_request(p_daily_limit integer default 100)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_day date := (now() at time zone 'utc')::date;
    v_count integer;
    v_reset timestamptz;
begin
    if p_daily_limit < 1 then
        raise exception 'p_daily_limit must be positive';
    end if;

    insert into private.biquote_usage_daily (usage_date, request_count, updated_at)
    values (v_day, 1, now())
    on conflict (usage_date) do update
       set request_count = private.biquote_usage_daily.request_count + 1,
           updated_at = now()
     where private.biquote_usage_daily.request_count < p_daily_limit
    returning request_count into v_count;

    if v_count is null then
        select request_count into v_count
          from private.biquote_usage_daily
         where usage_date = v_day;
    end if;

    v_reset := ((v_day + 1)::timestamp at time zone 'utc');
    return jsonb_build_object(
        'allowed', v_count <= p_daily_limit,
        'used', v_count,
        'daily_limit', p_daily_limit,
        'remaining', greatest(p_daily_limit - v_count, 0),
        'reset_at_utc', v_reset
    );
end;
$$;

create or replace function public.get_biquote_usage(p_daily_limit integer default 100)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_day date := (now() at time zone 'utc')::date;
    v_count integer := 0;
    v_reset timestamptz;
begin
    if p_daily_limit < 1 then
        raise exception 'p_daily_limit must be positive';
    end if;
    select request_count into v_count
      from private.biquote_usage_daily
     where usage_date = v_day;
    v_count := coalesce(v_count, 0);
    v_reset := ((v_day + 1)::timestamp at time zone 'utc');
    return jsonb_build_object(
        'allowed', v_count < p_daily_limit,
        'used', v_count,
        'daily_limit', p_daily_limit,
        'remaining', greatest(p_daily_limit - v_count, 0),
        'reset_at_utc', v_reset
    );
end;
$$;

revoke all on function public.consume_biquote_request(integer) from public, anon, authenticated;
revoke all on function public.get_biquote_usage(integer) from public, anon, authenticated;
grant execute on function public.consume_biquote_request(integer) to service_role;
grant execute on function public.get_biquote_usage(integer) to service_role;
