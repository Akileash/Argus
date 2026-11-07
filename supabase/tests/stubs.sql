-- TEST-ONLY stand-ins for Supabase-provided objects. Never a migration.
do $$ begin
  if not exists (select 1 from pg_roles where rolname='anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname='service_role') then create role service_role nologin bypassrls; end if;
end $$;

create schema extensions;
create extension pgcrypto with schema extensions;
create schema auth;
create table auth.users (
  instance_id uuid, id uuid primary key, aud text, role text, email text unique,
  encrypted_password text, email_confirmed_at timestamptz,
  raw_app_meta_data jsonb, raw_user_meta_data jsonb,
  created_at timestamptz, updated_at timestamptz,
  confirmation_token text, recovery_token text, email_change_token_new text, email_change text
);
create table auth.identities (
  id uuid primary key default gen_random_uuid(), user_id uuid references auth.users(id) on delete cascade,
  provider_id text, identity_data jsonb, provider text, last_sign_in_at timestamptz,
  created_at timestamptz, updated_at timestamptz
);
create function auth.jwt() returns jsonb language sql stable as
  $$ select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb $$;
create function auth.uid() returns uuid language sql stable as
  $$ select nullif(auth.jwt()->>'sub', '')::uuid $$;

create schema storage;
create table storage.buckets (id text primary key, name text not null, public boolean default false);
create table storage.objects (id uuid primary key default gen_random_uuid(), bucket_id text references storage.buckets(id), name text, owner uuid);
alter table storage.objects enable row level security;
create function storage.foldername(name text) returns text[] language sql immutable as
  $$ select string_to_array(name, '/') $$;

grant usage on schema public, auth, storage, extensions to anon, authenticated, service_role;
grant select on storage.objects to authenticated;
grant select on storage.buckets to authenticated, service_role;
grant all on storage.objects to service_role;
grant execute on function auth.uid(), auth.jwt() to anon, authenticated, service_role;
grant execute on function storage.foldername(text) to anon, authenticated, service_role;

create publication supabase_realtime;
