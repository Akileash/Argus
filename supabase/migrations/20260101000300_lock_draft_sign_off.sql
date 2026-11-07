-- report_json.sign_off belongs to sign_off_report(). A manager can edit a draft's report_json, so without this
-- they could write made-up "signed by" values into a draft through the REST API.
create or replace function public.reports_lock_draft_sign_off() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  if old.status = 'draft' and new.status = 'draft'
     and (new.report_json -> 'sign_off') is distinct from (old.report_json -> 'sign_off') then
    raise exception 'report_json.sign_off can only be set by sign_off_report()' using errcode = '42501';
  end if;
  return new;
end $$;

drop trigger if exists reports_lock_draft_sign_off on public.reports;
create trigger reports_lock_draft_sign_off before update on public.reports
  for each row execute function public.reports_lock_draft_sign_off();
