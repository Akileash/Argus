-- Argus M4.1: core schema (multi-tenant). See supabase/README.md.

------------------------------------------------------------------ enums
create type public.event_type as enum
  ('struck_by_near_miss','struck_by_contact','head_impact','fall','hat_knocked_off','hat_removed','module_fallback');
create type public.event_class as enum ('near_miss','incident','ppe_log','system');
create type public.event_status as enum ('received','uploading','uploaded','analyzing','ready','failed');
create type public.media_kind as enum ('clip','sensor_trace');
create type public.report_status as enum ('draft','signed');
create type public.profile_role as enum ('manager','viewer');

------------------------------------------------------------------ tenancy
create table public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  created_at timestamptz not null default now()
);

create table public.sites (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  name text not null,
  address text,
  province text not null default 'AB' check (char_length(province) = 2),
  created_at timestamptz not null default now()
);

create table public.crews (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  name text not null,
  supervisor_name text,
  created_at timestamptz not null default now()
);

create table public.workers (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  crew_id uuid references public.crews(id) on delete set null,
  full_name text not null,
  trade text,
  employee_no text,
  created_at timestamptz not null default now(),
  unique (org_id, employee_no)
);

create table public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  org_id uuid not null references public.organizations(id) on delete cascade,
  full_name text not null,
  role public.profile_role not null default 'viewer',
  created_at timestamptz not null default now()
);
create index on public.profiles (org_id);

create table public.devices (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  site_id uuid references public.sites(id) on delete set null,
  serial text not null unique,
  token_hash text unique,              -- sha256 hex of the device token; null until paired
  last_event_at timestamptz,           -- bumped by trigger on each event insert
  created_at timestamptz not null default now()
);
create index on public.devices (org_id);

create table public.device_assignments (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  device_id uuid not null references public.devices(id) on delete cascade,
  worker_id uuid not null references public.workers(id) on delete cascade,
  starts_at timestamptz not null default now(),
  ends_at timestamptz,
  check (ends_at is null or ends_at > starts_at)
);
-- at most one open assignment per device, and per worker
create unique index device_assignments_one_open_per_device on public.device_assignments (device_id) where ends_at is null;
create unique index device_assignments_one_open_per_worker on public.device_assignments (worker_id) where ends_at is null;
create index on public.device_assignments (device_id, starts_at);

------------------------------------------------------------------ events
create table public.events (
  id uuid primary key,                 -- = event_id generated on the device (idempotency key)
  org_id uuid not null references public.organizations(id) on delete cascade,
  device_id uuid not null references public.devices(id),
  worker_id uuid references public.workers(id) on delete set null,
  site_id uuid references public.sites(id) on delete set null,
  type public.event_type not null,
  severity smallint check (severity between 1 and 25),   -- risk score, set by worker after report
  t0 timestamptz not null,
  detected_at timestamptz not null,
  window_pre_s numeric not null default 20 check (window_pre_s >= 0),
  window_post_s numeric not null default 10 check (window_post_s >= 0),
  device_class public.event_class not null,
  llm_suggested_class public.event_class,
  llm_reclass_reason text,
  final_class public.event_class,
  peak_g numeric, peak_g_saturated boolean not null default false,
  closing_rate_mps numeric, ttc_s numeric, range_m numeric,
  sensor_id text check (sensor_id in ('R1','R2','R3','R4','TOP')),
  free_fall_ms numeric, drop_height_m numeric,
  hat_on boolean,
  co2_ppm numeric, temp_c numeric, rh_pct numeric,
  merged_triggers jsonb not null default '[]'::jsonb,
  firmware jsonb,
  payload jsonb,                       -- raw ArgusEvent body as received
  status public.event_status not null default 'received',
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint events_class_matches_type check (device_class = case type
    when 'struck_by_near_miss' then 'near_miss'::public.event_class
    when 'hat_removed' then 'ppe_log'::public.event_class
    when 'module_fallback' then 'system'::public.event_class
    else 'incident'::public.event_class end)
);
create index on public.events (org_id, t0 desc);
create index on public.events (device_id, t0 desc);
create index on public.events (status) where status in ('uploaded','analyzing');

create table public.media (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  event_id uuid not null references public.events(id) on delete cascade,
  kind public.media_kind not null,
  camera text check (camera in ('front')),
  filename text not null,
  content_type text,
  storage_path text not null,          -- <org_id>/<event_id>/<filename> in bucket event-media
  duration_s numeric,
  bytes bigint check (bytes >= 0),
  sha256 text check (sha256 ~ '^[a-f0-9]{64}$'),
  confirmed boolean not null default false,
  created_at timestamptz not null default now(),
  unique (event_id, filename)
);
create index on public.media (event_id);

create table public.analyses (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  event_id uuid not null references public.events(id) on delete cascade,
  stage text not null,                 -- e.g. video_analysis | report_draft
  model text not null,
  input_tokens integer, output_tokens integer,
  cost_usd numeric(10,6),
  latency_ms integer,
  output_json jsonb,
  created_at timestamptz not null default now()
);
create index on public.analyses (event_id);

create table public.report_counters (year int primary key, last_no int not null);

create table public.reports (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  event_id uuid not null unique references public.events(id) on delete cascade,
  report_no text not null unique,
  llm_draft_json jsonb not null,       -- immutable after insert
  report_json jsonb not null,          -- manager-editable copy
  edited boolean not null default false,
  status public.report_status not null default 'draft',
  signed_off_by text,
  signed_off_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (status = 'draft' or (signed_off_by is not null and signed_off_at is not null))
);
create index on public.reports (org_id, status);

create table public.report_edits (
  id bigint generated always as identity primary key,
  org_id uuid not null,
  report_id uuid not null references public.reports(id) on delete cascade,
  edited_by uuid,                      -- auth.uid(); null when changed by service role
  edited_at timestamptz not null default now(),
  field_path text not null,            -- dotted path into report_json, e.g. summary or risk.severity
  old_value jsonb,
  new_value jsonb
);
create index on public.report_edits (report_id, edited_at);

------------------------------------------------------------------ functions
create function public.set_updated_at() returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end $$;

-- worker assigned to the device at time t (null if none)
create function public.resolve_worker(p_device_id uuid, p_t timestamptz) returns uuid
language sql stable as $$
  select worker_id from public.device_assignments
  where device_id = p_device_id and starts_at <= p_t and (ends_at is null or p_t < ends_at)
  order by starts_at desc limit 1
$$;

-- ARG-<year>-<0000>; concurrency-safe, gapless unless the caller's txn rolls back
create function public.next_report_no() returns text language plpgsql as $$
declare y int := extract(year from now() at time zone 'utc')::int; n int;
begin
  insert into public.report_counters(year,last_no) values (y,1)
  on conflict (year) do update set last_no = public.report_counters.last_no + 1
  returning last_no into n;
  return format('ARG-%s-%s', y, lpad(n::text, 4, '0'));
end $$;

create function public.events_before_insert() returns trigger language plpgsql as $$
declare d public.devices;
begin
  select * into d from public.devices where id = new.device_id;
  if not found then raise exception 'unknown device %', new.device_id; end if;
  new.org_id := d.org_id;
  if new.site_id is null then new.site_id := d.site_id; end if;
  if new.worker_id is null then new.worker_id := public.resolve_worker(new.device_id, new.t0); end if;
  return new;
end $$;
create trigger events_before_insert before insert on public.events
  for each row execute function public.events_before_insert();
create trigger events_updated_at before update on public.events
  for each row execute function public.set_updated_at();

create function public.events_after_insert() returns trigger language plpgsql as $$
begin
  update public.devices set last_event_at = greatest(coalesce(last_event_at, new.t0), new.t0)
  where id = new.device_id;
  return null;
end $$;
create trigger events_after_insert after insert on public.events
  for each row execute function public.events_after_insert();

-- children inherit org_id from their event
create function public.org_from_event() returns trigger language plpgsql as $$
begin
  select org_id into new.org_id from public.events where id = new.event_id;
  if new.org_id is null then raise exception 'unknown event %', new.event_id; end if;
  return new;
end $$;
create trigger media_org before insert on public.media for each row execute function public.org_from_event();
create trigger analyses_org before insert on public.analyses for each row execute function public.org_from_event();
create trigger reports_org before insert on public.reports for each row execute function public.org_from_event();

-- leaf-level diff of two jsonb docs (objects recursed, arrays/scalars compared whole)
create function public.jsonb_diff(p_old jsonb, p_new jsonb, p_path text default '')
returns table(path text, old_value jsonb, new_value jsonb) language plpgsql immutable as $$
declare k text;
begin
  if p_old is not distinct from p_new then return; end if;
  if jsonb_typeof(p_old) = 'object' and jsonb_typeof(p_new) = 'object' then
    for k in select jsonb_object_keys(p_old) union select jsonb_object_keys(p_new) loop
      return query select * from public.jsonb_diff(p_old->k, p_new->k, case when p_path = '' then k else p_path||'.'||k end);
    end loop;
  else
    return query select p_path, p_old, p_new;
  end if;
end $$;

create function public.reports_guard() returns trigger language plpgsql as $$
declare d record; n int := 0;
begin
  if old.status = 'signed' then
    raise exception 'report % is signed and locked', old.report_no using errcode = '55006';
  end if;
  if new.llm_draft_json is distinct from old.llm_draft_json then
    raise exception 'llm_draft_json is immutable' using errcode = '55006';
  end if;
  if new.report_no is distinct from old.report_no or new.event_id is distinct from old.event_id then
    raise exception 'report_no / event_id are immutable' using errcode = '55006';
  end if;
  for d in select * from public.jsonb_diff(old.report_json, new.report_json) loop
    insert into public.report_edits(org_id, report_id, edited_by, field_path, old_value, new_value)
    values (old.org_id, old.id, auth.uid(), d.path, d.old_value, d.new_value);
    if d.path not like 'sign_off%' then n := n + 1; end if;
  end loop;
  if n > 0 then new.edited := true; end if;
  new.updated_at := now();
  return new;
end $$;
create trigger reports_guard before update on public.reports
  for each row execute function public.reports_guard();

create function public.reports_no_delete_signed() returns trigger language plpgsql as $$
begin
  if old.status = 'signed' then raise exception 'signed reports cannot be deleted' using errcode = '55006'; end if;
  return old;
end $$;
create trigger reports_no_delete_signed before delete on public.reports
  for each row execute function public.reports_no_delete_signed();

------------------------------------------------------------------ auth helpers (RLS)
create function public.current_org_id() returns uuid
language sql stable security definer set search_path = '' as $$
  select org_id from public.profiles where user_id = auth.uid()
$$;
create function public.is_manager() returns boolean
language sql stable security definer set search_path = '' as $$
  select coalesce((select role = 'manager' from public.profiles where user_id = auth.uid()), false)
$$;

------------------------------------------------------------------ views
create view public.org_kpis_week with (security_invoker = on) as
select o.id as org_id,
  (select count(*) from public.events e where e.org_id = o.id
     and coalesce(e.final_class, e.device_class) = 'near_miss' and e.t0 >= now() - interval '7 days')::int as near_misses_week,
  (select count(*) from public.events e where e.org_id = o.id
     and coalesce(e.final_class, e.device_class) = 'incident' and e.t0 >= now() - interval '7 days')::int as incidents_week,
  (select count(*) from public.reports r where r.org_id = o.id and r.status = 'draft')::int as reports_awaiting_signoff
from public.organizations o;

create view public.device_last_seen with (security_invoker = on) as
select d.id as device_id, d.org_id, d.serial, d.site_id, d.last_event_at,
  a.worker_id as assigned_worker_id, w.full_name as assigned_worker_name
from public.devices d
left join public.device_assignments a on a.device_id = d.id and a.ends_at is null
left join public.workers w on w.id = a.worker_id;

------------------------------------------------------------------ RLS + grants
alter table public.organizations enable row level security;
alter table public.sites enable row level security;
alter table public.crews enable row level security;
alter table public.workers enable row level security;
alter table public.profiles enable row level security;
alter table public.devices enable row level security;
alter table public.device_assignments enable row level security;
alter table public.events enable row level security;
alter table public.media enable row level security;
alter table public.analyses enable row level security;
alter table public.reports enable row level security;
alter table public.report_edits enable row level security;
alter table public.report_counters enable row level security;   -- no policies: service role only

revoke all on all tables in schema public from anon, authenticated;

-- read: members of the org
create policy org_select on public.organizations for select to authenticated using (id = public.current_org_id());
create policy profiles_select on public.profiles for select to authenticated using (org_id = public.current_org_id());
grant select on public.organizations, public.profiles to authenticated;
do $$ declare t text; begin
  foreach t in array array['sites','crews','workers','devices','device_assignments','events','media','analyses','reports','report_edits'] loop
    execute format('grant select on public.%I to authenticated', t);
    execute format('create policy %I on public.%I for select to authenticated using (org_id = public.current_org_id())', t||'_select', t);
  end loop;
end $$;
-- never expose token_hash to clients
revoke select on public.devices from authenticated;
grant select (id, org_id, site_id, serial, last_event_at, created_at) on public.devices to authenticated;
grant select on public.org_kpis_week, public.device_last_seen to authenticated;

-- writes: managers only, within their org
do $$ declare t text; begin
  foreach t in array array['sites','crews','workers','device_assignments'] loop
    execute format('grant insert, update, delete on public.%I to authenticated', t);
    execute format('create policy %I on public.%I for insert to authenticated with check (org_id = public.current_org_id() and public.is_manager())', t||'_ins', t);
    execute format('create policy %I on public.%I for update to authenticated using (org_id = public.current_org_id() and public.is_manager()) with check (org_id = public.current_org_id())', t||'_upd', t);
    execute format('create policy %I on public.%I for delete to authenticated using (org_id = public.current_org_id() and public.is_manager())', t||'_del', t);
  end loop;
end $$;
-- accept/reject reclassification
grant update (final_class) on public.events to authenticated;
create policy events_upd on public.events for update to authenticated
  using (org_id = public.current_org_id() and public.is_manager()) with check (org_id = public.current_org_id());
-- edit / sign reports (guard trigger enforces lock + draft immutability)
grant update (report_json, status, signed_off_by, signed_off_at) on public.reports to authenticated;
create policy reports_upd on public.reports for update to authenticated
  using (org_id = public.current_org_id() and public.is_manager() and status = 'draft')
  with check (org_id = public.current_org_id());

-- service_role bypasses RLS; make sure it has table rights
grant all on all tables in schema public to service_role;
grant all on all sequences in schema public to service_role;
grant execute on all functions in schema public to service_role;

------------------------------------------------------------------ storage
insert into storage.buckets (id, name, public) values ('event-media', 'event-media', false)
on conflict (id) do update set public = false;

create policy event_media_read on storage.objects for select to authenticated
  using (bucket_id = 'event-media' and (storage.foldername(name))[1] = public.current_org_id()::text);

------------------------------------------------------------------ realtime
do $$ begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table public.events, public.reports;
  end if;
end $$;
