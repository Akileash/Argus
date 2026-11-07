-- KPI view numbers and shape of seed data.
begin;
do $$
declare k record; n int;
begin
  select * into k from public.org_kpis_week where org_id = 'a0000000-0000-4000-8000-000000000001';
  assert k.near_misses_week = 3, 'near misses this week, got '||k.near_misses_week;       -- e1 e2 e3
  assert k.incidents_week = 1, 'incidents this week, got '||k.incidents_week;             -- e4
  assert k.reports_awaiting_signoff = 3, 'awaiting sign-off, got '||k.reports_awaiting_signoff; -- e1 e3 e4

  assert (select count(*) from public.workers) = 40;
  assert (select count(*) from public.crews) = 3;
  assert (select count(*) from public.devices) = 8;
  assert (select token_hash is null from public.devices where serial = 'ARG-0001'), 'real hat has no token yet';
  assert (select count(*) from public.events) = 12;
  assert (select count(*) from public.events where device_class = 'ppe_log') = 3;
  assert (select count(*) from public.reports where status = 'signed') = 6;
  assert (select count(*) from public.analyses) = 18, 'two analyses per reported event';
  assert (select bool_and(cost_usd > 0 and latency_ms > 0) from public.analyses);
  assert (select count(*) from public.events where worker_id is null) = 0, 'every seeded event has a worker';
  assert (select count(*) from public.reports where report_json#>>'{header,location_on_site}' <> 'Location to be determined') = 0;
  assert (select count(*) from public.reports where llm_draft_json#>>'{header,location_on_site}' <> 'Location to be determined') = 0;

  -- device_last_seen
  select count(*) into n from public.device_last_seen where last_event_at is not null; assert n = 7, 'devices with events: '||n;
  assert (select assigned_worker_name is not null from public.device_last_seen where serial = 'ARG-0001'), 'demo hat assigned';

  -- demo login exists and password verifies
  assert exists (select 1 from auth.users where email = 'demo@argus.local'
    and encrypted_password = extensions.crypt('argus-demo-2026', encrypted_password) and email_confirmed_at is not null);
  assert (select role from public.profiles where full_name = 'Dana Whitfield') = 'manager';

  -- storage bucket + realtime
  assert (select public from storage.buckets where id = 'event-media') = false, 'bucket must be private';
  assert (select count(*) from pg_publication_tables where pubname = 'supabase_realtime' and tablename in ('events','reports')) = 2;
end $$;
rollback;
select 'seed/kpi ok' as result;
