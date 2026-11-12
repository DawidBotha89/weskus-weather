-- Applied to Lekker Weskus only. Secrets are provisioned separately in Vault.
create schema if not exists weather_ops;
revoke all on schema weather_ops from public, anon, authenticated;
-- Isolated HTTP client for authenticated dispatch. It does not enqueue credentials.
create schema if not exists weather_http;
revoke all on schema weather_http from public, anon, authenticated;
create extension if not exists http with schema weather_http;

create table if not exists weather_ops.updater (
  id boolean primary key default true check (id),
  enabled boolean not null default false,
  last_fetch_at timestamptz,
  fetch_request_id bigint,
  last_checked_at timestamptz,
  published_utc timestamptz,
  success_utc timestamptz,
  last_dispatch_at timestamptz,
  dispatch_hour timestamptz,
  attempts integer not null default 0,
  last_missed_hour timestamptz,
  missed_updates integer not null default 0,
  status text not null default 'starting',
  feed_status text not null default 'unknown',
  dispatch_status text not null default 'idle',
  last_http_status integer
);
-- Allow the reviewed script to upgrade the first deployed scheduler version.
alter table weather_ops.updater
  add column if not exists feed_status text not null default 'unknown',
  add column if not exists dispatch_status text not null default 'idle';
alter table weather_ops.updater enable row level security;
revoke all on weather_ops.updater from public, anon, authenticated;
insert into weather_ops.updater (id) values (true) on conflict do nothing;

create table if not exists weather_ops.events (
  id bigint generated always as identity primary key,
  occurred_at timestamptz not null default now(),
  event text not null,
  detail jsonb not null default '{}'
);
alter table weather_ops.events enable row level security;
revoke all on weather_ops.events from public, anon, authenticated;

create or replace function weather_ops.tick()
returns void language plpgsql security definer set search_path = '' as $$
declare
  s weather_ops.updater%rowtype;
  reply record;
  payload jsonb;
  published timestamptz;
  successful timestamptz;
  hour_start timestamptz := date_trunc('hour', now() at time zone 'UTC') at time zone 'UTC';
  credential text;
begin
  select * into s from weather_ops.updater where id for update skip locked;
  if not found or not s.enabled then return; end if;

  if s.fetch_request_id is not null then
    select status_code,content,timed_out into reply from net._http_response where id=s.fetch_request_id;
    if found then
      s.fetch_request_id := null;
      s.last_checked_at := now();
      begin
        if reply.status_code is distinct from 200 or reply.timed_out then
          raise exception 'feed unavailable';
        end if;
        payload := reply.content::jsonb;
        if (select count(*) from jsonb_object_keys(payload#>'{atmosphere,towns}')) <> 15 then
          raise exception 'town coverage incomplete';
        end if;
        published := (payload->>'generated_utc')::timestamptz;
        if published is null or published > now()+interval '5 minutes' then
          raise exception 'invalid publication date';
        end if;
        -- Legacy snapshots use the last complete publication as their success date.
        successful := case when payload ? 'health' then
          (payload#>>'{health,last_success_utc}')::timestamptz
          when coalesce(payload->'refresh_errors','[]'::jsonb)='[]'::jsonb then published end;
        if successful > published then raise exception 'invalid success date'; end if;
        if s.published_utc is null or published >= s.published_utc then
          if published is distinct from s.published_utc then
            insert into weather_ops.events(event,detail) values
              ('publication_observed',jsonb_build_object('published_utc',published,'success_utc',successful));
          end if;
          s.published_utc := published;
          s.success_utc := greatest(s.success_utc,successful);
          if successful >= s.last_dispatch_at then s.dispatch_status := 'confirmed'; end if;
        end if;
        s.status := case when coalesce(payload->'refresh_errors','[]'::jsonb)='[]'::jsonb
          then 'checked' else 'source_failure' end;
        s.feed_status := case when s.status='checked' then 'complete' else 'degraded' end;
      exception when others then
        s.status := 'feed_unavailable';
        s.feed_status := 'unavailable';
        insert into weather_ops.events(event,detail)
          values ('feed_unavailable',jsonb_build_object('http_status',reply.status_code));
      end;
    elsif s.last_fetch_at < now()-interval '2 minutes' then
      s.fetch_request_id := null;
      s.last_checked_at := now();
      s.status := 'feed_unavailable';
      s.feed_status := 'unavailable';
      insert into weather_ops.events(event) values ('feed_response_missing');
    end if;
  end if;

  if s.dispatch_hour is distinct from hour_start then
    s.dispatch_hour := hour_start;
    s.attempts := 0;
  end if;
  if s.last_checked_at is not null and coalesce(s.success_utc,'epoch') < hour_start
      and now() >= hour_start+interval '15 minutes'
      and s.last_missed_hour is distinct from hour_start then
    s.last_missed_hour := hour_start;
    s.missed_updates := s.missed_updates+1;
    insert into weather_ops.events(event,detail) values
      ('missed_hour',jsonb_build_object('hour',hour_start,'last_success_utc',s.success_utc));
  end if;

  -- Public feed requests cannot undo an accepted GitHub dispatch.
  -- Queue and housekeeping work happens before the synchronous external side effect.
  if s.fetch_request_id is null and (s.last_fetch_at is null or s.last_fetch_at <= now()-interval '5 minutes') then
    s.last_fetch_at := date_trunc('minute',now());
    begin
      s.fetch_request_id := net.http_get(
        url := 'https://lekker-weskus.vercel.app/weather-shadow/latest.json',
        headers := '{"Accept":"application/json","Cache-Control":"no-cache"}'::jsonb,
        timeout_milliseconds := 15000);
    exception when others then
      s.status := 'feed_unavailable';
      s.feed_status := 'unavailable';
      s.last_checked_at := now();
      insert into weather_ops.events(event) values ('feed_enqueue_failed');
    end;
  end if;
  delete from weather_ops.events where occurred_at < now()-interval '30 days';

  -- Minute 2 is the hourly target; retries are spaced 25 minutes apart.
  -- GitHub concurrency serializes runs, including dispatches delayed in its queue.
  if coalesce(s.success_utc,'epoch') < hour_start and now() >= hour_start+interval '2 minutes'
      and s.last_checked_at is not null and s.attempts < 3
      and (s.last_dispatch_at is null or s.last_dispatch_at < now()-interval '25 minutes'
        or s.success_utc >= s.last_dispatch_at) then
    select decrypted_secret into credential from vault.decrypted_secrets
      where name='weather_github_dispatch_token';
    if credential is null or length(credential)<20 then
      s.status := 'credential_required';
      s.dispatch_status := 'credential_required';
    else
      s.last_dispatch_at := now();
      s.attempts := s.attempts+1;
      insert into weather_ops.events(event,detail) values
        ('refresh_requested',jsonb_build_object('hour',hour_start,'attempt',s.attempts,
          'reason',case when s.attempts=1 then 'hour_due' else 'retry' end));
      begin
        perform set_config('http.curlopt_connecttimeout_ms','3000',true);
        perform set_config('http.curlopt_timeout_ms','8000',true);
        select status into s.last_http_status from weather_http.http((
          'POST',
          'https://api.github.com/repos/DawidBotha89/weskus-weather/actions/workflows/forecast.yml/dispatches',
          array[('Authorization','Bearer '||credential)::weather_http.http_header,
            ('Accept','application/vnd.github+json')::weather_http.http_header,
            ('X-GitHub-Api-Version','2022-11-28')::weather_http.http_header,
            ('User-Agent','Weskus-weather-watchdog')::weather_http.http_header],
          'application/json','{"ref":"main"}')::weather_http.http_request);
        s.status := case when s.last_http_status=204 then 'refresh_requested' else 'dispatch_failed' end;
        s.dispatch_status := case when s.last_http_status=204 then 'accepted' else 'failed' end;
        insert into weather_ops.events(event,detail) values
          (case when s.last_http_status=204 then 'dispatch_accepted' else 'dispatch_failed' end,
           jsonb_build_object('http_status',s.last_http_status,'attempt',s.attempts));
      exception when others then
        s.status := 'dispatch_failed';
        s.dispatch_status := 'failed';
        s.last_http_status := null;
        insert into weather_ops.events(event) values ('dispatch_connection_failed');
      end;
      credential := null;
    end if;
  end if;

  update weather_ops.updater set
    last_fetch_at=s.last_fetch_at,fetch_request_id=s.fetch_request_id,last_checked_at=s.last_checked_at,
    published_utc=s.published_utc,success_utc=s.success_utc,last_dispatch_at=s.last_dispatch_at,
    dispatch_hour=s.dispatch_hour,attempts=s.attempts,
    last_missed_hour=s.last_missed_hour,missed_updates=s.missed_updates,status=s.status,
    last_http_status=s.last_http_status,feed_status=s.feed_status,dispatch_status=s.dispatch_status
    where id;
end;
$$;
revoke all on function weather_ops.tick() from public,anon,authenticated;

-- Only this small weather status object is exposed. No tokens or HTTP response bodies.
create or replace function public.weather_update_status()
returns jsonb language sql security definer set search_path = '' as $$
  select jsonb_build_object(
    'enabled',enabled,'checked_utc',last_checked_at,'published_utc',published_utc,
    'last_success_utc',success_utc,'last_dispatch_utc',last_dispatch_at,
    'attempts_this_hour',attempts,'missed_hours',missed_updates,'last_action',status,
    'feed_status',feed_status,'dispatch_status',dispatch_status,'last_dispatch_http_status',last_http_status,
    'healthy',coalesce(enabled and feed_status='complete' and dispatch_status not in ('failed','credential_required')
      and success_utc >= now()-interval '75 minutes'
      and last_checked_at >= now()-interval '10 minutes',false),
    'hourly_target_minute_utc',2,'retry_after_minutes',25,'maximum_attempts_per_hour',3)
  from weather_ops.updater where id;
$$;
revoke all on function public.weather_update_status() from public;
grant execute on function public.weather_update_status() to anon,authenticated,service_role;

-- Named job replacement is idempotent. The row stays disabled until secret setup passes.
select cron.schedule('weather-hourly-watchdog','* * * * *','select weather_ops.tick()');
