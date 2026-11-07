-- single open assignment, worker resolution at t0, idempotent event insert, last_event_at.
begin;
do $$
declare
  d1 uuid := md5('device-1')::uuid; d3 uuid := md5('device-3')::uuid; d8 uuid := md5('device-8')::uuid;
  eid uuid := gen_random_uuid(); n int; w uuid; last1 timestamptz; t timestamptz := now() - interval '3 hours';
begin
  -- second open assignment for same device rejected
  begin
    insert into public.device_assignments (org_id, device_id, worker_id)
    values ('a0000000-0000-4000-8000-000000000001', d1, md5('worker-40')::uuid);
    assert false, 'two open assignments on one device';
  exception when unique_violation then null; end;
  -- same worker on two devices rejected
  begin
    insert into public.device_assignments (org_id, device_id, worker_id)
    values ('a0000000-0000-4000-8000-000000000001', md5('device-2')::uuid, md5('worker-4')::uuid);
    assert false, 'worker on two open devices';
  exception when unique_violation then null; end;
  -- closing then reopening is fine
  update public.device_assignments set ends_at = now() where device_id = d8 and ends_at is null;
  insert into public.device_assignments (org_id, device_id, worker_id, starts_at)
    values ('a0000000-0000-4000-8000-000000000001', d8, md5('worker-40')::uuid, now());

  -- worker resolution at t0: device 3 was worker 12 before 10 days ago, worker 13 after
  assert public.resolve_worker(d3, now() - interval '12 days') = md5('worker-12')::uuid, 'resolve before reassignment';
  assert public.resolve_worker(d3, now() - interval '1 day') = md5('worker-13')::uuid, 'resolve after reassignment';
  assert public.resolve_worker(d3, now() - interval '40 days') is null, 'resolve before any assignment';
  assert public.resolve_worker(d8, now() - interval '1 day') = md5('worker-32')::uuid, 'closed assignment still resolves historically';

  -- seeded events resolved by trigger
  assert (select worker_id from public.events where id = '00000000-0000-4000-8000-000000000011') = md5('worker-12')::uuid, 'seed e11 worker';
  assert (select worker_id from public.events where id = '00000000-0000-4000-8000-000000000002') = md5('worker-13')::uuid, 'seed e2 worker';

  -- idempotent insert: same event_id twice => one row
  insert into public.events (id, device_id, type, t0, detected_at, device_class, payload)
    values (eid, d1, 'struck_by_near_miss', t, t, 'near_miss', '{"v":1}');
  insert into public.events (id, device_id, type, t0, detected_at, device_class, payload)
    values (eid, d1, 'struck_by_near_miss', t, t, 'near_miss', '{"v":2}') on conflict (id) do nothing;
  select count(*) into n from public.events where id = eid;
  assert n = 1, 'duplicate event row';
  assert (select payload->>'v' from public.events where id = eid) = '1', 'first write must win';
  assert (select worker_id from public.events where id = eid) = md5('worker-4')::uuid, 'worker resolved from open assignment';
  assert (select org_id from public.events where id = eid) = 'a0000000-0000-4000-8000-000000000001', 'org_id from device';
  assert (select site_id from public.events where id = eid) = 'b0000000-0000-4000-8000-000000000001', 'site_id from device';
  assert (select status from public.events where id = eid) = 'received', 'default status';
  select last_event_at into last1 from public.devices where id = d1;
  assert last1 = t, 'last_event_at bumped';

  -- plain duplicate without ON CONFLICT is a PK violation (API must use ON CONFLICT)
  begin
    insert into public.events (id, device_id, type, t0, detected_at, device_class)
      values (eid, d1, 'struck_by_near_miss', t, t, 'near_miss');
    assert false, 'duplicate accepted';
  exception when unique_violation then null; end;

  -- class/type mismatch rejected
  begin
    insert into public.events (id, device_id, type, t0, detected_at, device_class)
      values (gen_random_uuid(), d1, 'fall', t, t, 'near_miss');
    assert false, 'class/type mismatch accepted';
  exception when check_violation then null; end;

  -- status enum
  begin
    update public.events set status = 'bogus' where id = eid; assert false, 'bad status';
  exception when invalid_text_representation then null; end;
end $$;
rollback;
select 'events ok' as result;
