# Argus database (M4.1)

Postgres schema, RLS, seed and tests for Supabase. Migration: `migrations/20260101000000_argus_schema.sql`. Seed: `seed.sql`.

## Test
`supabase/tests/run.sh` starts a throwaway Postgres 16 (`brew install postgresql@16`), loads `tests/stubs.sql` (test-only stand-ins for `auth`, `storage`, roles), applies migration and seed, runs `tests/*.test.sql`, then `validate_seed_reports.py` (uses `uv` and the `device` env).

## Access model
- Dashboard users: `authenticated` role, scoped by `profiles.org_id` (RLS). Managers can write sites/crews/workers/device_assignments, edit draft `reports.report_json`, and sign through `sign_off_report(report_id, reclass_decision)` (the only way to set `status`/signer/`events.final_class`; final_class is locked after sign-off). Viewers read only.
- Everything else (event ingest, media, analyses, report insert, device registration, `devices.token_hash`) is service role only. Devices never touch RLS: the API checks the bearer token (sha256 compared with `devices.token_hash`).
- `authenticated` cannot read `devices.token_hash` (column grant). Do not `select *` from `devices` in client code; list columns.
- Storage bucket `event-media` is private; objects at `<org_id>/<event_id>/<filename>`; authenticated users read their own org's folder.

## Tables
organizations, sites, crews, workers, profiles, devices, device_assignments, events (PK `id` = device `event_id`), media, analyses, reports, report_edits, report_counters. Enums: `event_type`, `event_class`, `event_status` (received, uploading, uploaded, analyzing, ready, failed), `media_kind` (clip, sensor_trace), `report_status` (draft, signed), `profile_role` (manager, viewer).

## Behaviours the API/worker can rely on
- Insert an event idempotently: `insert into events (id, device_id, type, ...) values (...) on conflict (id) do nothing` (check row count; if 0, it is a repeat). A trigger fills `org_id`, `site_id` (from the device) and `worker_id` (from `resolve_worker(device_id, t0)`), and bumps `devices.last_event_at`.
- `events.device_class` must match `type` (check constraint).
- `media`, `analyses`, `reports` get `org_id` from their event by trigger; you need not supply it. `media` is unique on `(event_id, filename)`; set `confirmed = true` after `/complete` verifies the object.
- `reports.llm_draft_json` is immutable (error 55006). `report_json` is editable while `status = 'draft'`; any change outside `sign_off.*` sets `edited = true`, and every changed leaf is logged to `report_edits` (dotted `field_path`, old/new, `auth.uid()`). Signing goes only through `select public.sign_off_report(report_id, decision)`: it takes the signer from the caller's profile and sets the report fields, `report_json.sign_off` and `events.final_class` in one transaction. Direct writes to those columns are not granted, `report_json.sign_off` can't be changed on a draft, and after signing all updates and deletes fail (55006) and `final_class` is locked.
- Insert a report with `report_no => public.next_report_no()`.

## Functions and views
| Name | Use |
|---|---|
| `resolve_worker(device_id uuid, t timestamptz) returns uuid` | Worker whose assignment covers `t` (`starts_at <= t < ends_at or open`); null if none. |
| `next_report_no() returns text` | `ARG-<year>-<0000>`; per-year counter in `report_counters`, concurrency-safe; rolls back with the caller's transaction. |
| `current_org_id()`, `is_manager()` | RLS helpers (security definer). |
| `jsonb_diff(old, new)` | Leaf-level diff used by the report audit trigger. |
| view `org_kpis_week` | Per org: `near_misses_week`, `incidents_week` (rolling 7 days on `t0`, class = `coalesce(final_class, device_class)`), `reports_awaiting_signoff` (reports with status draft). |
| view `device_last_seen` | `device_id, org_id, serial, site_id, last_event_at, assigned_worker_id, assigned_worker_name`. |

Views use `security_invoker`, so RLS applies.

## Seed
Org "Northline Builders", site "Riverbend Mixed-Use Tower (Phase 2)" (Edmonton, AB), 3 crews, 40 workers, devices ARG-0001..0008 (ARG-0001 = real hat, `token_hash` null, assigned to a worker; ARG-0002..0008 token = `seed-token-ARG-000N`, stored hashed), 12 events over 19 days (9 with reports: 6 signed, 3 draft; 3 `hat_removed`). Demo login `demo@argus.local` / `argus-demo-2026` (manager). Fixed ids: org `a0000000-0000-4000-8000-000000000001`, events `00000000-0000-4000-8000-0000000000NN`. Other ids use `md5('worker-N')::uuid`, `md5('device-N')::uuid`, `md5('crew-N')::uuid`.
