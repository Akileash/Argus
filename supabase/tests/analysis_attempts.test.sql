-- events.analysis_attempts: defaults to 0, the worker can bump it with a compare-and-set claim, the heartbeat
-- refreshes updated_at, clients cannot write it, and it cannot go negative or null.
begin;
do $$
declare
  eid uuid := gen_random_uuid(); t timestamptz := now() - interval '2 hours';
  n int;
begin
  insert into public.events (id, device_id, type, t0, detected_at, device_class, payload)
    values (eid, md5('device-1')::uuid, 'struck_by_near_miss', t, t, 'near_miss', '{}');
  assert (select analysis_attempts from public.events where id = eid) = 0, 'default is 0';

  -- the worker's claim: compare-and-set from uploaded, counter + 1
  update public.events set status = 'uploaded' where id = eid;
  update public.events set status = 'analyzing', analysis_attempts = 1
    where id = eid and status = 'uploaded' and analysis_attempts = 0;
  get diagnostics n = row_count;
  assert n = 1, 'claim updates one row';
  update public.events set status = 'analyzing', analysis_attempts = 1
    where id = eid and status = 'uploaded' and analysis_attempts = 0;
  get diagnostics n = row_count;
  assert n = 0, 'a second claim of the same event matches nothing';

  -- heartbeat: any update (even a no-op one) makes the trigger set updated_at = now()
  update public.events set updated_at = timestamptz '2000-01-01' where id = eid;
  assert (select updated_at from public.events where id = eid) = now(), 'trigger owns updated_at';
  update public.events set status = 'analyzing' where id = eid and status = 'analyzing';
  get diagnostics n = row_count;
  assert n = 1, 'heartbeat update matches the analyzing event';
  assert (select updated_at from public.events where id = eid) = now(), 'heartbeat refreshes updated_at';

  begin
    update public.events set analysis_attempts = -1 where id = eid;
    assert false, 'negative attempts accepted';
  exception when check_violation then null; end;
  begin
    update public.events set analysis_attempts = null where id = eid;
    assert false, 'null attempts accepted';
  exception when not_null_violation then null; end;
end $$;

-- a signed-in manager cannot write the counter
create temp table t_e as select id from public.events limit 1;
grant select on t_e to authenticated;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-000000000001"}', true);
do $$
declare blocked boolean := false;
begin
  begin
    update public.events set analysis_attempts = 0 where id = (select id from t_e);
  exception when insufficient_privilege then blocked := true;
  end;
  assert blocked, 'authenticated must not update analysis_attempts';
end $$;
rollback;
\echo '   analysis_attempts ok'
