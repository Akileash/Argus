-- supabase/hosted/setup.sql: creates exactly the production starting data, and is idempotent. Rolled back at the end.
begin;

-- the demo seed is loaded in this test database; move its ARG-0001 out of the way so we test setup.sql on its own
update public.devices set serial = 'SEED-' || serial;

\ir ../hosted/setup.sql
\ir ../hosted/setup.sql

do $$
declare n int; o uuid;
begin
  select count(*) into n from public.organizations where name = 'Argus';           assert n = 1, 'one Argus org, got '||n;
  select id into o from public.organizations where name = 'Argus';
  select count(*) into n from public.sites where org_id = o and name = 'natHacks' and province = 'AB' and address like 'Edmonton%';
  assert n = 1, 'site natHacks Edmonton AB';
  select count(*) into n from public.crews where org_id = o and name = 'natHacks Crew' and supervisor_name = 'Dip Khadka';
  assert n = 1, 'crew with supervisor';
  select count(*) into n from public.workers where org_id = o;                      assert n = 1, 'one worker, got '||n;
  select count(*) into n from public.workers where org_id = o and full_name = 'Okitha Biyanwila'; assert n = 1, 'Okitha';
  select count(*) into n from public.devices where org_id = o and serial = 'ARG-0001' and token_hash is null; assert n = 1, 'ARG-0001 unpaired';
  select count(*) into n from public.devices where org_id = o;                      assert n = 1, 'one device';
  select count(*) into n from public.device_assignments a
    join public.devices d on d.id = a.device_id and d.serial = 'ARG-0001'
    join public.workers w on w.id = a.worker_id and w.full_name = 'Okitha Biyanwila'
    where a.org_id = o and a.ends_at is null;
  assert n = 1, 'one open assignment ARG-0001 -> Okitha, got '||n;
  select count(*) into n from public.device_assignments where org_id = o; assert n = 1, 'no other assignments';
  select count(*) into n from public.events where org_id = o;               assert n = 0, 'no events';
  select count(*) into n from public.reports where org_id = o;              assert n = 0, 'no reports';
  select count(*) into n from public.profiles where org_id = o;             assert n = 0, 'no profiles (create-user.ts makes them)';
end $$;

-- a manager who already moved the hat to someone else is not overridden by a re-run
update public.device_assignments set ends_at = now() + interval '1 second'
  where device_id = (select id from public.devices where serial = 'ARG-0001') and ends_at is null;
insert into public.workers (id, org_id, full_name) values ('e5000000-0000-4000-8000-0000000000f2', 'a5000000-0000-4000-8000-000000000001', 'Someone Else');
insert into public.device_assignments (org_id, device_id, worker_id, starts_at)
  select 'a5000000-0000-4000-8000-000000000001', id, 'e5000000-0000-4000-8000-0000000000f2', now() + interval '2 seconds'
  from public.devices where serial = 'ARG-0001';
\ir ../hosted/setup.sql
do $$
declare n int;
begin
  select count(*) into n from public.device_assignments a join public.devices d on d.id = a.device_id and d.serial = 'ARG-0001'
    where a.ends_at is null and a.worker_id = 'e5000000-0000-4000-8000-0000000000f2';
  assert n = 1, 'reassigned hat kept';
  select count(*) into n from public.device_assignments a join public.devices d on d.id = a.device_id and d.serial = 'ARG-0001' where a.ends_at is null;
  assert n = 1, 'still exactly one open assignment';
end $$;

rollback;
select 'hosted setup ok' as result;
