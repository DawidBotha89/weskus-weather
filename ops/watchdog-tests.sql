-- Run after applying watchdog.sql. Every case prevents authenticated dispatch:
-- disabled, unchecked startup, fresh success, or attempts already at the cap.
-- Every fixture and any public-only HTTP request are rolled back together.
begin;
do $$
declare
  s weather_ops.updater%rowtype;
  fixture jsonb;
  expected_hour timestamptz := date_trunc('hour',now() at time zone 'UTC') at time zone 'UTC';
begin
  if has_schema_privilege('anon','weather_ops','usage')
    or has_schema_privilege('authenticated','weather_http','usage')
    or has_function_privilege('anon','weather_ops.tick()','execute') then
    raise exception 'Private scheduler is accessible to a public role';
  end if;

  update weather_ops.updater set enabled=false,attempts=0,last_fetch_at=null;
  perform weather_ops.tick();
  select * into s from weather_ops.updater where id;
  if s.last_fetch_at is not null or s.attempts<>0 then raise exception 'Disabled scheduler ran'; end if;

  update weather_ops.updater set enabled=true,last_checked_at=null,fetch_request_id=null,
    last_fetch_at=now(),success_utc=null,missed_updates=0,last_missed_hour=null;
  perform weather_ops.tick();
  select * into s from weather_ops.updater where id;
  if s.missed_updates<>0 or s.attempts<>0 then
    raise exception 'Unchecked startup falsely counted a missed hour or dispatched';
  end if;

  fixture := jsonb_build_object('generated_utc',now(),
    'atmosphere',jsonb_build_object('towns',(select jsonb_object_agg(n::text,'[]'::jsonb) from generate_series(1,15)n)),
    'health',jsonb_build_object('last_success_utc',now()),'refresh_errors','[]'::jsonb);
  insert into net._http_response(id,status_code,content,timed_out,created)
    values(-910201,200,fixture::text,false,now());
  update weather_ops.updater set enabled=true,fetch_request_id=-910201,last_fetch_at=now(),
    published_utc=null,success_utc=null,last_checked_at=null,attempts=0,dispatch_hour=expected_hour;
  perform weather_ops.tick();
  select * into s from weather_ops.updater where id;
  if s.success_utc is distinct from now() or s.attempts<>0 or s.status<>'checked' then
    raise exception 'Fresh successful publication did not suppress dispatch';
  end if;
  if not (public.weather_update_status()->>'healthy')::boolean then
    raise exception 'Fresh publication marked unhealthy';
  end if;

  fixture := jsonb_set(fixture,'{health,last_success_utc}',to_jsonb(now()-interval '3 hours'));
  fixture := jsonb_set(fixture,'{refresh_errors}','["baseline"]'::jsonb);
  update net._http_response set content=fixture::text where id=-910201;
  update weather_ops.updater set fetch_request_id=-910201,published_utc=null,success_utc=null,
    last_dispatch_at=now(),attempts=3,dispatch_hour=expected_hour;
  perform weather_ops.tick();
  select * into s from weather_ops.updater where id;
  if s.success_utc is distinct from now()-interval '3 hours' or s.attempts<>3 then
    raise exception 'Failed refresh falsely advanced success or exceeded retry cap';
  end if;
  if (public.weather_update_status()->>'healthy')::boolean then
    raise exception 'Fresh attempted time concealed failed refresh';
  end if;

  update net._http_response set status_code=503,content='unavailable' where id=-910201;
  update weather_ops.updater set fetch_request_id=-910201;
  perform weather_ops.tick();
  select * into s from weather_ops.updater where id;
  if s.status<>'feed_unavailable' or s.success_utc is distinct from now()-interval '3 hours' then
    raise exception 'Failed HTTP read concealed last successful publication';
  end if;
end;
$$;
rollback;
