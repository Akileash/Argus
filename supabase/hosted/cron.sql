-- Argus hosted: the report-worker safety net. NOT a migration: it needs the deployed URL and a secret.
--
-- Every minute Supabase (pg_cron + pg_net) POSTs to  <APP_URL>/api/worker/run  with
--   Authorization: Bearer <WORKER_CRON_SECRET>
-- The route picks up any event still waiting (e.g. the after() run on /complete was cut off or failed), re-queues
-- events whose worker died, and gives up on events that used all their attempts. With nothing to do it returns in
-- well under a second.
--
-- DO NOT edit the placeholders by hand and DO NOT commit a filled copy. Use:
--     scripts/hosted/setup-cron.sh https://your-app.vercel.app
-- which fills a private temp copy (mode 600) and prints the exact command to run it.
--
-- Placeholders (the double-underscore words below, replaced by that script; this comment avoids writing them):
--     APP_URL                   https://your-app.vercel.app   (no trailing slash)
--     WORKER_CRON_SECRET        the same value as the WORKER_CRON_SECRET Vercel env var
--
-- Where the secret lives: Supabase Vault (vault.secrets, encrypted at rest; readable only by postgres/service_role,
-- never exposed through the REST API). The job reads it at run time, so the secret is NOT in cron.job.command.
-- Re-running this file is safe: it updates the two vault entries in place and replaces the job.

create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;
create extension if not exists supabase_vault;

do $argus$
declare
  v_id uuid;
begin
  select id into v_id from vault.secrets where name = 'argus_worker_url';
  if v_id is null then
    perform vault.create_secret('__APP_URL__/api/worker/run', 'argus_worker_url', 'Argus report worker endpoint');
  else
    perform vault.update_secret(v_id, '__APP_URL__/api/worker/run', 'argus_worker_url', 'Argus report worker endpoint');
  end if;

  select id into v_id from vault.secrets where name = 'argus_worker_cron_secret';
  if v_id is null then
    perform vault.create_secret('__WORKER_CRON_SECRET__', 'argus_worker_cron_secret', 'Bearer secret for /api/worker/run');
  else
    perform vault.update_secret(v_id, '__WORKER_CRON_SECRET__', 'argus_worker_cron_secret', 'Bearer secret for /api/worker/run');
  end if;
end
$argus$;

-- Replace any earlier version of the job, then schedule it (every minute).
select cron.unschedule(jobid) from cron.job where jobname = 'argus-worker-run';

select cron.schedule(
  'argus-worker-run',
  '* * * * *',
  $job$
    select net.http_post(
      url     := (select decrypted_secret from vault.decrypted_secrets where name = 'argus_worker_url'),
      headers := jsonb_build_object(
                   'Content-Type',  'application/json',
                   'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets where name = 'argus_worker_cron_secret')
                 ),
      body    := '{}'::jsonb,
      -- the route may work for up to 300 s; do not let pg_net give up (and look like a failure) after its 5 s default
      timeout_milliseconds := 300000
    );
  $job$
);

-- ---------------------------------------------------------------------------------------------------------------
-- Check it:      select jobid, jobname, schedule, active from cron.job where jobname = 'argus-worker-run';
-- Last runs:     select status, return_message, start_time from cron.job_run_details order by start_time desc limit 5;
-- HTTP results:  select id, status_code, left(content::text, 200) as body, created from net._http_response order by created desc limit 5;
--
-- Turn it OFF (see also supabase/hosted/cron-off.sql):
--     select cron.unschedule('argus-worker-run');
-- Remove the stored secrets as well:
--     delete from vault.secrets where name in ('argus_worker_url', 'argus_worker_cron_secret');
