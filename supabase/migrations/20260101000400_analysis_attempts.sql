-- Worker attempt cap: how many times a worker claimed this event for analysis (reclaims included).
-- The worker bumps it on each claim and marks the event failed once it reaches WORKER_MAX_ANALYSIS_ATTEMPTS (3),
-- so a stuck event cannot loop forever and keep spending LLM money.
--
-- Access: service_role already has table-level `grant all` (the worker's key), which covers a new column.
-- authenticated keeps its column-level UPDATE on events(final_class) only, so dashboard users cannot touch
-- the counter. The events_updated_at trigger still fires on every update, which is what the worker's heartbeat uses.
alter table public.events
  add column analysis_attempts int not null default 0 check (analysis_attempts >= 0);

comment on column public.events.analysis_attempts is
  'Times a worker claimed this event for analysis (reclaims included). Worker-maintained; capped by the worker.';
