-- RLS isolation between two orgs + column/role restrictions. Rolled back at the end.
begin;

-- org B with a manager, a device, an event (created as superuser)
insert into public.organizations (id, name) values ('a0000000-0000-4000-8000-0000000000b2', 'Other Org');
insert into auth.users (id, email) values ('d0000000-0000-4000-8000-0000000000b2', 'b@example.test');
insert into public.profiles (user_id, org_id, full_name, role)
  values ('d0000000-0000-4000-8000-0000000000b2', 'a0000000-0000-4000-8000-0000000000b2', 'Bee Manager', 'manager');
insert into public.devices (id, org_id, serial) values ('c0000000-0000-4000-8000-0000000000b2', 'a0000000-0000-4000-8000-0000000000b2', 'ARG-B-1');
insert into public.events (id, device_id, type, t0, detected_at, device_class)
  values ('e0000000-0000-4000-8000-0000000000b2', 'c0000000-0000-4000-8000-0000000000b2', 'struck_by_near_miss', now(), now(), 'near_miss');
insert into storage.objects (bucket_id, name) values
  ('event-media', 'a0000000-0000-4000-8000-0000000000b2/e0000000-0000-4000-8000-0000000000b2/front.mp4'),
  ('event-media', 'a0000000-0000-4000-8000-000000000001/00000000-0000-4000-8000-000000000001/front.mp4');

-- as demo manager (org A)
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-000000000001"}', true);
do $$
declare n int;
begin
  select count(*) into n from public.events;                 assert n = 12, 'A sees its 12 events, got '||n;
  select count(*) into n from public.events where org_id = 'a0000000-0000-4000-8000-0000000000b2'; assert n = 0, 'A sees no B events';
  select count(*) into n from public.organizations;          assert n = 1, 'A sees only its org';
  select count(*) into n from public.devices;                assert n = 8, 'A sees 8 devices';
  select count(*) into n from public.workers;                assert n = 40, 'A sees 40 workers';
  select count(*) into n from public.reports;                assert n = 9, 'A sees 9 reports, got '||n;
  select count(*) into n from public.profiles;               assert n = 1, 'A sees only own-org profiles';
  select count(*) into n from storage.objects;               assert n = 1, 'A sees only its storage objects, got '||n;
  select count(*) into n from public.org_kpis_week;          assert n = 1, 'KPI view scoped';
  -- token_hash must not be readable
  begin
    perform token_hash from public.devices; assert false, 'token_hash readable';
  exception when insufficient_privilege then null; end;
  -- cannot insert events directly (service role only)
  begin
    insert into public.events (id, device_id, type, t0, detected_at, device_class)
      values (gen_random_uuid(), md5('device-1')::uuid, 'fall', now(), now(), 'incident');
    assert false, 'client insert into events allowed';
  exception when insufficient_privilege then null; end;
  -- cannot write into org B
  begin
    insert into public.workers (org_id, full_name) values ('a0000000-0000-4000-8000-0000000000b2', 'Intruder');
    assert false, 'cross-org insert allowed';
  exception when insufficient_privilege then null; end;
  -- manager can write own org
  insert into public.workers (org_id, full_name) values ('a0000000-0000-4000-8000-000000000001', 'New Hire');
  -- cross-org update touches zero rows
  begin
    update public.events set final_class = 'incident' where id = 'e0000000-0000-4000-8000-0000000000b2';
    assert false, 'client final_class write allowed';
  exception when insufficient_privilege then null; end;
end $$;

-- as org B manager
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-0000000000b2"}', true);
do $$
declare n int;
begin
  select count(*) into n from public.events;    assert n = 1, 'B sees only its event, got '||n;
  select count(*) into n from public.workers;   assert n = 0, 'B sees no A workers';
  select count(*) into n from storage.objects;  assert n = 1, 'B sees only its object';
end $$;

-- anonymous / no profile sees nothing
select set_config('request.jwt.claims', '{}', true);
do $$
declare n int;
begin
  select count(*) into n from public.events; assert n = 0, 'no-claims user sees nothing';
end $$;

-- viewer role: read-only
reset role;
insert into auth.users (id, email) values ('d0000000-0000-4000-8000-0000000000c3', 'v@example.test');
insert into public.profiles (user_id, org_id, full_name, role)
  values ('d0000000-0000-4000-8000-0000000000c3', 'a0000000-0000-4000-8000-000000000001', 'View Only', 'viewer');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-0000000000c3"}', true);
do $$
declare n int;
begin
  select count(*) into n from public.events; assert n = 12, 'viewer reads org events';
  begin
    insert into public.workers (org_id, full_name) values ('a0000000-0000-4000-8000-000000000001', 'Nope');
    assert false, 'viewer insert allowed';
  exception when insufficient_privilege then null; end;
  update public.reports set report_json = '{}' where status = 'draft';
  get diagnostics n = row_count; assert n = 0, 'viewer cannot update reports';
end $$;
rollback;
select 'rls ok' as result;
