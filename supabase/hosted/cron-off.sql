-- Argus hosted: stop the worker safety net and forget its secrets. Safe to run when it was never set up.
--   pnpm exec supabase db query --linked -f supabase/hosted/cron-off.sql
select cron.unschedule(jobid) from cron.job where jobname = 'argus-worker-run';
delete from vault.secrets where name in ('argus_worker_url', 'argus_worker_cron_secret');
