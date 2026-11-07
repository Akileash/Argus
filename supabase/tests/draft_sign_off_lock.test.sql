-- A manager can't write made-up sign-off details into a draft; ordinary draft edits still work.
begin;
create temp table t_d as
  select id from public.reports where status = 'draft' and event_id = '00000000-0000-4000-8000-000000000003';
grant select on t_d to authenticated;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-000000000001"}', true);
do $$
declare v uuid := (select id from t_d); blocked boolean := false;
begin
  assert v is not null, 'seed draft missing';
  update public.reports set report_json = jsonb_set(report_json, '{summary}', '"Edited summary."') where id = v;
  assert found, 'normal draft edit must still work';
  begin
    update public.reports set report_json = jsonb_set(report_json, '{sign_off,signed_by}', '"Someone Else"') where id = v;
  exception when insufficient_privilege then blocked := true;
  end;
  assert blocked, 'forged sign_off on a draft must be rejected';
end $$;
rollback;
\echo '   draft sign-off lock ok'
