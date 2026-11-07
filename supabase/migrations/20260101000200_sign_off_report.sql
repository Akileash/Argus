-- Sign-off integrity. Signing is one atomic, server-side step (sign_off_report); clients can no longer write
-- status / signed_off_by / signed_off_at on reports or final_class on events directly, and final_class is frozen
-- once the event's report is signed.

revoke update (status, signed_off_by, signed_off_at) on public.reports from authenticated;
revoke update (final_class) on public.events from authenticated;
drop policy if exists events_upd on public.events;
-- report_json stays editable by managers on drafts (reports_upd policy + reports_guard trigger).

create function public.events_final_class_lock() returns trigger
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if new.final_class is distinct from old.final_class
     and exists (select 1 from public.reports r where r.event_id = old.id and r.status = 'signed') then
    raise exception 'final_class is locked: the report for event % is signed', old.id using errcode = '55006';
  end if;
  return new;
end $$;
create trigger events_final_class_lock before update on public.events
  for each row execute function public.events_final_class_lock();

create function public.sign_off_report(p_report_id uuid, p_reclass_decision text)
returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_uid uuid := auth.uid();
  v_name text;
  v_rep public.reports%rowtype;
  v_ev public.events%rowtype;
  v_decision text := 'not_applicable';
  v_final public.event_class;
  v_at timestamptz := now();
begin
  if v_uid is null then
    raise exception 'not signed in' using errcode = '42501';
  end if;
  if p_reclass_decision is not null and p_reclass_decision not in ('accepted', 'rejected', 'not_applicable') then
    raise exception 'invalid reclass decision %', p_reclass_decision using errcode = '22023';
  end if;

  select * into v_rep from public.reports where id = p_report_id for update;
  -- unknown report and cross-org report look identical to the caller
  select full_name into v_name from public.profiles
    where user_id = v_uid and role = 'manager' and org_id = v_rep.org_id;
  if v_rep.id is null or v_name is null then
    raise exception 'only a manager of this organization can sign off this report' using errcode = '42501';
  end if;
  if v_rep.status = 'signed' then
    raise exception 'report % is already signed', v_rep.report_no using errcode = '55006';
  end if;

  select * into v_ev from public.events where id = v_rep.event_id for update;
  v_final := v_ev.device_class;
  if v_ev.llm_suggested_class is not null and v_ev.llm_suggested_class is distinct from v_ev.device_class then
    if p_reclass_decision is null or p_reclass_decision not in ('accepted', 'rejected') then
      raise exception 'accept or reject the AI reclassification before signing' using errcode = '22023';
    end if;
    v_decision := p_reclass_decision;
    if v_decision = 'accepted' then v_final := v_ev.llm_suggested_class; end if;
  end if;

  update public.events set final_class = v_final where id = v_ev.id;
  update public.reports set
    report_json = jsonb_set(report_json, '{sign_off}', jsonb_build_object(
      'signed_by', v_name, 'signed_at', v_at, 'final_class', v_final, 'reclass_decision', v_decision)),
    status = 'signed', signed_off_by = v_name, signed_off_at = v_at
  where id = v_rep.id;

  return jsonb_build_object('signed_by', v_name, 'signed_at', v_at, 'final_class', v_final, 'reclass_decision', v_decision);
end $$;

revoke all on function public.sign_off_report(uuid, text) from public, anon;
grant execute on function public.sign_off_report(uuid, text) to authenticated, service_role;
