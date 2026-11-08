-- Argus production setup data. NOT a migration and NOT the demo seed: exactly what a real deployment starts with.
--   org "Argus" / site "natHacks" (Edmonton, AB) / crew "natHacks Crew" (supervisor Dip Khadka)
--   worker "Okitha Biyanwila" / device ARG-0001 assigned to Okitha (open assignment)
-- Nothing else: no events, no reports, no fake people, no auth users (scripts/hosted/create-user.ts makes those).
--
-- Idempotent: safe to run again, it never duplicates or overwrites anything. Fixed ids make that possible.
--
-- Run (after the 5 migrations are applied):
--   pnpm exec supabase db query --linked -f supabase/hosted/setup.sql
-- or with psql against the database connection string:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/hosted/setup.sql

insert into public.organizations (id, name)
values ('a5000000-0000-4000-8000-000000000001', 'Argus')
on conflict (id) do nothing;

insert into public.sites (id, org_id, name, address, province)
values ('b5000000-0000-4000-8000-000000000001', 'a5000000-0000-4000-8000-000000000001',
        'natHacks', 'Edmonton, AB', 'AB')
on conflict (id) do nothing;

insert into public.crews (id, org_id, site_id, name, supervisor_name)
values ('c5000000-0000-4000-8000-000000000001', 'a5000000-0000-4000-8000-000000000001',
        'b5000000-0000-4000-8000-000000000001', 'natHacks Crew', 'Dip Khadka')
on conflict (id) do nothing;

insert into public.workers (id, org_id, crew_id, full_name)
values ('e5000000-0000-4000-8000-000000000001', 'a5000000-0000-4000-8000-000000000001',
        'c5000000-0000-4000-8000-000000000001', 'Okitha Biyanwila')
on conflict (id) do nothing;

-- token_hash stays null until the Pi is paired (argus-cli pair), which sets it.
insert into public.devices (id, org_id, site_id, serial)
values ('d5000000-0000-4000-8000-000000000001', 'a5000000-0000-4000-8000-000000000001',
        'b5000000-0000-4000-8000-000000000001', 'ARG-0001')
on conflict (serial) do nothing;

-- Open assignment ARG-0001 -> Okitha, only when neither the device nor the worker already has an open one
-- (so a re-run, or a hat the manager has since reassigned in the dashboard, is left alone).
insert into public.device_assignments (org_id, device_id, worker_id)
select 'a5000000-0000-4000-8000-000000000001', d.id, 'e5000000-0000-4000-8000-000000000001'
from public.devices d
where d.serial = 'ARG-0001'
  and exists (select 1 from public.workers w where w.id = 'e5000000-0000-4000-8000-000000000001')
  and not exists (select 1 from public.device_assignments a where a.device_id = d.id and a.ends_at is null)
  and not exists (select 1 from public.device_assignments a
                  where a.worker_id = 'e5000000-0000-4000-8000-000000000001' and a.ends_at is null);
