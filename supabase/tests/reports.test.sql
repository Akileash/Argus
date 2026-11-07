-- llm_draft_json immutability, signed lock, edit audit, report numbering.
begin;
do $$
declare
  v_draft uuid; v_signed uuid; n int; v_no text;
begin
  select id into v_draft from public.reports where status = 'draft' limit 1;
  select id into v_signed from public.reports where status = 'signed' limit 1;

  -- llm_draft_json immutable (even on drafts)
  begin
    update public.reports set llm_draft_json = '{"x":1}' where id = v_draft;
    assert false, 'llm_draft_json was updated';
  exception when sqlstate '55006' then null; end;

  -- report_json editable, flips edited, logs audit
  update public.reports set report_json = jsonb_set(report_json, '{summary}', '"Edited by test"') where id = v_draft;
  assert (select edited from public.reports where id = v_draft), 'edited flag not set';
  select count(*) into n from public.report_edits where report_id = v_draft and field_path = 'summary';
  assert n = 1, 'audit row missing';

  -- sign: allowed once, sign_off changes do not count as edits on a fresh draft
  update public.reports set status = 'signed', signed_off_by = 'T', signed_off_at = now() where id = v_draft;
  assert (select status from public.reports where id = v_draft) = 'signed';

  -- signed rows locked for any update or delete
  begin
    update public.reports set report_json = '{}' where id = v_draft; assert false, 'signed report updated';
  exception when sqlstate '55006' then null; end;
  begin
    update public.reports set status = 'draft' where id = v_signed; assert false, 'signed report reopened';
  exception when sqlstate '55006' then null; end;
  begin
    delete from public.reports where id = v_signed; assert false, 'signed report deleted';
  exception when sqlstate '55006' then null; end;

  -- signed requires name + timestamp
  begin
    update public.reports set status = 'signed' where id = (select id from public.reports where status = 'draft' limit 1);
    assert false, 'signed without signer';
  exception when check_violation then null; end;

  -- report_no format and monotonic
  v_no := public.next_report_no();
  assert v_no ~ '^ARG-[0-9]{4}-[0-9]{4}$', 'bad report_no '||v_no;
  assert public.next_report_no() > v_no, 'report_no not increasing';
  assert (select count(distinct report_no) from public.reports) = (select count(*) from public.reports), 'duplicate report_no';
end $$;
rollback;
select 'reports ok' as result;
