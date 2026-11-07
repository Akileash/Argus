-- sign_off_report(): atomic sign-off, forged signer, double sign-off, final_class lock, role / org checks.
begin;
insert into public.organizations (id, name) values ('a0000000-0000-4000-8000-0000000000b2', 'Other Org');
insert into auth.users (id, email) values
  ('d0000000-0000-4000-8000-0000000000b2', 'b@example.test'),
  ('d0000000-0000-4000-8000-0000000000c3', 'v@example.test');
insert into public.profiles (user_id, org_id, full_name, role) values
  ('d0000000-0000-4000-8000-0000000000b2', 'a0000000-0000-4000-8000-0000000000b2', 'Bee Manager', 'manager'),
  ('d0000000-0000-4000-8000-0000000000c3', 'a0000000-0000-4000-8000-000000000001', 'View Only', 'viewer');

create temp table t_ids as
  select (select id from public.reports where status = 'draft' and event_id = '00000000-0000-4000-8000-000000000001') as with_sugg,
         (select id from public.reports where status = 'draft' and event_id = '00000000-0000-4000-8000-000000000003') as plain,
         (select id from public.reports where status = 'draft' and event_id = '00000000-0000-4000-8000-000000000004') as spare;
grant select on t_ids to authenticated;

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-000000000001"}', true);
do $$
declare
  v_sugg uuid := (select with_sugg from t_ids); v_plain uuid := (select plain from t_ids);
  v_ev uuid; v_name text; r jsonb; n int;
begin
  assert v_sugg is not null and v_plain is not null, 'seed drafts missing';
  select full_name into v_name from public.profiles where user_id = 'd0000000-0000-4000-8000-000000000001';

  -- forged signer: direct writes to status / signer columns and final_class are refused
  begin
    update public.reports set status = 'signed', signed_off_by = 'Forged', signed_off_at = now() where id = v_plain;
    assert false, 'direct sign allowed';
  exception when insufficient_privilege then null; end;
  begin
    update public.reports set signed_off_by = 'Forged' where id = v_plain;
    assert false, 'direct signer write allowed';
  exception when insufficient_privilege then null; end;
  begin
    update public.events set final_class = 'incident' where id = (select event_id from public.reports where id = v_plain);
    assert false, 'direct final_class write allowed';
  exception when insufficient_privilege then null; end;
  -- report_json editing of drafts still works
  update public.reports set report_json = jsonb_set(report_json, '{summary}', '"edited"') where id = v_plain;
  get diagnostics n = row_count; assert n = 1, 'draft edit blocked';

  -- reclass decision required when the AI suggested a different class; invalid values rejected
  begin perform public.sign_off_report(v_sugg, null); assert false, 'signed without decision';
  exception when sqlstate '22023' then null; end;
  begin perform public.sign_off_report(v_sugg, 'maybe'); assert false, 'bad decision accepted';
  exception when sqlstate '22023' then null; end;
  assert (select status from public.reports where id = v_sugg) = 'draft', 'failed sign-off left changes';

  -- happy path: signer from profile, event derived, class + sign_off set together
  r := public.sign_off_report(v_sugg, 'accepted');
  assert r->>'signed_by' = v_name, 'signer not from profile';
  assert r->>'final_class' = 'incident', 'final class not applied';
  select event_id into v_ev from public.reports where id = v_sugg;
  assert (select final_class from public.events where id = v_ev) = 'incident', 'event final_class not set';
  assert (select status from public.reports where id = v_sugg) = 'signed';
  assert (select signed_off_by from public.reports where id = v_sugg) = v_name;
  assert (select report_json #>> '{sign_off,signed_by}' from public.reports where id = v_sugg) = v_name;
  assert (select report_json #>> '{sign_off,reclass_decision}' from public.reports where id = v_sugg) = 'accepted';
  assert (select report_json #>> '{sign_off,final_class}' from public.reports where id = v_sugg) = 'incident';

  -- double sign-off rejected
  begin perform public.sign_off_report(v_sugg, 'rejected'); assert false, 'double sign-off allowed';
  exception when sqlstate '55006' then null; end;

  -- final_class locked after sign-off, even for the table owner
  reset role;
  begin
    update public.events set final_class = 'near_miss' where id = v_ev;
    assert false, 'final_class changed after sign-off';
  exception when sqlstate '55006' then null; end;
  update public.events set final_class = final_class where id = v_ev;  -- no-op write stays allowed
  set local role authenticated;

  -- event with no suggestion signs with not_applicable and device class
  r := public.sign_off_report(v_plain, null);
  assert r->>'reclass_decision' = 'not_applicable' and r->>'final_class' = 'near_miss', 'plain sign-off wrong: '||r::text;
end $$;

-- cross-org manager rejected
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-0000000000b2"}', true);
do $$
begin
  begin perform public.sign_off_report((select spare from t_ids), null); assert false, 'cross-org sign-off allowed';
  exception when insufficient_privilege then null; end;
  begin perform public.sign_off_report(gen_random_uuid(), null); assert false, 'unknown report signed';
  exception when insufficient_privilege then null; end;
end $$;

-- viewer rejected
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-0000000000c3"}', true);
do $$
begin
  begin perform public.sign_off_report((select spare from t_ids), null); assert false, 'viewer signed';
  exception when insufficient_privilege then null; end;
end $$;

-- signed out / anon rejected
select set_config('request.jwt.claims', '{}', true);
do $$
begin
  begin perform public.sign_off_report((select spare from t_ids), null); assert false, 'anonymous signed';
  exception when insufficient_privilege then null; end;
end $$;
reset role;
rollback;
select 'sign_off ok' as result;
