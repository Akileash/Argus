-- Viewer accounts (judges) are READ-ONLY. Every write path the app uses is tried as a viewer and must be refused
-- (privilege error) or touch zero rows. Rolled back at the end. Also: a viewer cannot promote themselves.
begin;

insert into auth.users (id, email) values ('d0000000-0000-4000-8000-0000000000c4', 'judge@example.test');
insert into public.profiles (user_id, org_id, full_name, role)
  values ('d0000000-0000-4000-8000-0000000000c4', 'a0000000-0000-4000-8000-000000000001', 'Judge View', 'viewer');

create temp table t_v as
  select (select id from public.reports where status = 'draft' limit 1) as draft_report,
         (select id from public.workers limit 1) as a_worker,
         (select id from public.device_assignments where ends_at is null limit 1) as an_assignment,
         (select id from public.devices limit 1) as a_device,
         (select id from public.sites limit 1) as a_site,
         (select event_id from public.reports where status = 'draft' limit 1) as an_event;
grant select on t_v to authenticated;

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-0000000000c4"}', true);
do $$
declare
  t t_v%rowtype;
  n int;
begin
  select * into t from t_v;
  assert t.draft_report is not null and t.a_worker is not null and t.an_assignment is not null, 'seed rows missing';

  -- reads work
  select count(*) into n from public.reports;  assert n > 0, 'viewer reads reports';
  select count(*) into n from public.events;   assert n > 0, 'viewer reads events';

  -- editing a draft report: zero rows (RLS hides it from UPDATE)
  update public.reports set report_json = '{}' where id = t.draft_report;
  get diagnostics n = row_count; assert n = 0, 'viewer edited a draft report';

  -- signing: the sign_off_report RPC refuses (42501), and the direct column writes are not granted
  begin
    perform public.sign_off_report(t.draft_report, null);
    assert false, 'viewer signed a report';
  exception when insufficient_privilege then null; end;
  begin
    update public.reports set status = 'signed', signed_off_by = 'Judge' where id = t.draft_report;
    assert false, 'viewer wrote report status';
  exception when insufficient_privilege then null; end;
  begin
    update public.events set final_class = 'incident' where id = t.an_event;
    assert false, 'viewer wrote final_class';
  exception when insufficient_privilege then null; end;

  -- assigning devices: insert refused, close/reopen touches nothing, delete touches nothing
  begin
    insert into public.device_assignments (org_id, device_id, worker_id)
      values ('a0000000-0000-4000-8000-000000000001', t.a_device, t.a_worker);
    assert false, 'viewer inserted an assignment';
  exception when insufficient_privilege then null; end;
  update public.device_assignments set ends_at = now() where id = t.an_assignment;
  get diagnostics n = row_count; assert n = 0, 'viewer closed an assignment';
  delete from public.device_assignments where id = t.an_assignment;
  get diagnostics n = row_count; assert n = 0, 'viewer deleted an assignment';

  -- org data: workers, crews, sites
  begin
    insert into public.workers (org_id, full_name) values ('a0000000-0000-4000-8000-000000000001', 'Nope');
    assert false, 'viewer inserted a worker';
  exception when insufficient_privilege then null; end;
  begin
    insert into public.crews (org_id, site_id, name) values ('a0000000-0000-4000-8000-000000000001', t.a_site, 'Nope');
    assert false, 'viewer inserted a crew';
  exception when insufficient_privilege then null; end;
  begin
    insert into public.sites (org_id, name) values ('a0000000-0000-4000-8000-000000000001', 'Nope');
    assert false, 'viewer inserted a site';
  exception when insufficient_privilege then null; end;
  update public.workers set full_name = 'Renamed' where id = t.a_worker;
  get diagnostics n = row_count; assert n = 0, 'viewer renamed a worker';
  delete from public.workers where id = t.a_worker;
  get diagnostics n = row_count; assert n = 0, 'viewer deleted a worker';

  -- cannot touch devices (no write grant) or promote themselves (profiles are read-only to clients)
  begin
    update public.devices set serial = 'X' where id = t.a_device;
    assert false, 'viewer updated a device';
  exception when insufficient_privilege then null; end;
  begin
    update public.profiles set role = 'manager' where user_id = 'd0000000-0000-4000-8000-0000000000c4';
    assert false, 'viewer promoted themselves';
  exception when insufficient_privilege then null; end;
  begin
    insert into public.profiles (user_id, org_id, full_name, role)
      values (gen_random_uuid(), 'a0000000-0000-4000-8000-000000000001', 'Ghost', 'manager');
    assert false, 'viewer created a profile';
  exception when insufficient_privilege or foreign_key_violation then null; end;
end $$;

rollback;
select 'viewer readonly ok' as result;
