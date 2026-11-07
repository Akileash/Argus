-- Argus seed data. Fully fictional people and company. Re-runnable only on an empty DB (supabase db reset).
-- Dates are relative to now() so the dashboard always looks like a live site.

-- ids
-- org  a0000000-0000-4000-8000-000000000001   site b0000000-...-0001   demo user d0000000-...-0001
-- crews/workers/devices use md5('<kind>-<n>')::uuid so they are stable.

insert into public.organizations (id, name) values
  ('a0000000-0000-4000-8000-000000000001', 'Northline Builders');

insert into public.sites (id, org_id, name, address, province) values
  ('b0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001',
   'Riverbend Mixed-Use Tower (Phase 2)', '10450 Example Avenue NW, Edmonton, AB', 'AB');

insert into public.crews (id, org_id, site_id, name, supervisor_name)
select md5('crew-'||n)::uuid, 'a0000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', name, sup
from (values (1,'Foundations & Concrete','Marcus Delacroix'),
             (2,'Structural Framing','Priya Raman'),
             (3,'Mechanical & Electrical','Tomasz Wieczorek')) c(n,name,sup);

-- 40 workers, round-robin across the 3 crews
insert into public.workers (id, org_id, crew_id, full_name, trade, employee_no)
select md5('worker-'||i)::uuid, 'a0000000-0000-4000-8000-000000000001',
  md5('crew-'||(((i-1) % 3)+1))::uuid,
  (array['Jordan','Casey','Riley','Morgan','Avery','Quinn','Taylor','Rowan','Sasha','Devon',
         'Harper','Emery','Logan','Marlow','Nico','Parker','Reese','Sloane','Tatum','Wren'])[((i-1) % 20)+1]
   || ' ' ||
  (array['Okafor','Lindqvist','Barrera','Nakamura','Fitzgerald','Haddad','Petrov','Moreau','Castellan','Ibsen',
         'Duquesne','Varga','Oyelaran','Stroud','Kowalski','Brandt','Mirza','Halvorsen','Quintero','Ashby'])[(((i-1)*7) % 20)+1],
  (array['Carpenter','Ironworker','Concrete finisher','Electrician','Pipefitter','Labourer','Crane operator','Roofer'])[((i-1) % 8)+1],
  'NB-'||(1000+i)
from generate_series(1,40) i;

-- 8 devices. ARG-0001 = the real hat: no token yet (set when paired via /api/devices/register).
-- ARG-0002..0008 get token sha256('seed-token-ARG-000N') so the mock demo can authenticate as them.
insert into public.devices (id, org_id, site_id, serial, token_hash, last_event_at)
select md5('device-'||n)::uuid, 'a0000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001',
  'ARG-'||lpad(n::text,4,'0'),
  case when n = 1 then null else encode(extensions.digest('seed-token-ARG-'||lpad(n::text,4,'0'),'sha256'),'hex') end,
  null
from generate_series(1,8) n;

-- assignments: device n -> worker n*4 (devices 1..8 -> workers 4..32), open since 30 days ago.
-- Device 3 was reassigned 10 days ago (worker 12 -> worker 13).
insert into public.device_assignments (org_id, device_id, worker_id, starts_at, ends_at)
select 'a0000000-0000-4000-8000-000000000001', md5('device-'||n)::uuid, md5('worker-'||(n*4))::uuid,
  now() - interval '30 days', case when n = 3 then now() - interval '10 days' end
from generate_series(1,8) n;
insert into public.device_assignments (org_id, device_id, worker_id, starts_at, ends_at) values
  ('a0000000-0000-4000-8000-000000000001', md5('device-3')::uuid, md5('worker-13')::uuid, now() - interval '10 days', null);

-- demo manager login: demo@argus.local / argus-demo-2026
insert into auth.users (instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
  confirmation_token, recovery_token, email_change_token_new, email_change)
values ('00000000-0000-0000-0000-000000000000', 'd0000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated',
  'demo@argus.local', extensions.crypt('argus-demo-2026', extensions.gen_salt('bf')), now(),
  '{"provider":"email","providers":["email"]}', '{"full_name":"Dana Whitfield"}', now(), now(), '', '', '', '');
insert into auth.identities (user_id, provider_id, identity_data, provider, last_sign_in_at, created_at, updated_at)
values ('d0000000-0000-4000-8000-000000000001', 'd0000000-0000-4000-8000-000000000001',
  '{"sub":"d0000000-0000-4000-8000-000000000001","email":"demo@argus.local","email_verified":true}', 'email', now(), now(), now());
insert into public.profiles (user_id, org_id, full_name, role) values
  ('d0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'Dana Whitfield', 'manager');

-- historical events, media, analyses, reports
create temp table tpl (kind text primary key, body jsonb not null);
insert into tpl values
('near', $j${
 "summary": "A rear-mounted ultrasonic sensor detected an object closing on the worker from behind at high speed. The haptic alert fired and the worker stepped aside; no contact was recorded.",
 "sequence_of_events": [
  {"offset_s": -1.4, "description": "Rear sensor begins tracking an object approaching from behind.", "source": "sensor"},
  {"offset_s": 0.0, "description": "Time-to-collision threshold crossed; rear haptic motor fires.", "source": "sensor"},
  {"offset_s": 0.5, "description": "Worker turns and moves clear of the path of the moving load.", "source": "video"},
  {"offset_s": 1.8, "description": "Object passes behind the worker without contact.", "source": "inferred"}],
 "evidence": {"clip_refs": [{"offset_s": 0.5, "observation": "Worker turns toward the alert and steps sideways."}],
  "environment": "Ambient CO2 and temperature normal for outdoor work; readings are biased by the wearer."},
 "hazard": {"category": "struck_by", "energy_source": "Moving equipment or materials (kinetic)"},
 "risk": {"severity": 4, "likelihood": 3, "score": 12,
  "severity_justification": "A strike to the head or upper body from a moving load could cause serious injury.",
  "likelihood_justification": "Workers and moving loads share this area with limited separation."},
 "causes": {"immediate": [{"kind": "substandard_condition", "description": "Moving load travelled through an area occupied by a worker on foot."}],
  "root_five_whys": ["The worker was inside the load path.", "The path was not barricaded.", "Traffic routes were not reviewed after the layout changed.", "Layout changes do not trigger a hazard re-check."]},
 "corrective_actions": [
  {"description": "Barricade the load path and add a spotter during lifts.", "control_level": "engineering", "suggested_owner_role": "Site supervisor"},
  {"description": "Review traffic routes at each layout change.", "control_level": "administrative", "suggested_owner_role": "Safety manager"}],
 "regulatory_note": "Unlikely to meet a serious-incident threshold on its own; may be a dangerous occurrence depending on circumstances. Verify with your provincial OHS regulator.",
 "confidence_and_gaps": "Sensor data is clear. The video angle is limited, so the type of moving object could not be confirmed."
}$j$),
('impact', $j${
 "summary": "The hat registered a sharp impact consistent with an object striking the worker's head. The worker remained on their feet. A first-aid check is recommended.",
 "sequence_of_events": [
  {"offset_s": -0.6, "description": "Hat stable, worker walking.", "source": "sensor"},
  {"offset_s": 0.0, "description": "Impact spike recorded on the hat accelerometer.", "source": "sensor"},
  {"offset_s": 0.4, "description": "Worker stops and raises a hand toward the head.", "source": "video"},
  {"offset_s": 3.0, "description": "Worker resumes standing posture; no fall observed.", "source": "video"}],
 "evidence": {"clip_refs": [{"offset_s": 0.4, "observation": "Worker stops abruptly and reaches toward the hard hat."}],
  "environment": "Readings within normal range; biased by the wearer."},
 "hazard": {"category": "falling_object", "energy_source": "Gravity (falling or swung object)"},
 "risk": {"severity": 4, "likelihood": 3, "score": 12,
  "severity_justification": "Head impacts can cause concussion even when protected by a hard hat.",
  "likelihood_justification": "Overhead work was ongoing in the same bay."},
 "causes": {"immediate": [{"kind": "substandard_condition", "description": "Overhead work without a controlled drop zone below."}],
  "root_five_whys": ["An object struck the worker's hat.", "The area below overhead work was not closed off.", "Drop zones are set up inconsistently.", "No drop-zone checklist is part of the pre-task plan."]},
 "corrective_actions": [
  {"description": "Establish and sign a drop zone under overhead work.", "control_level": "engineering", "suggested_owner_role": "Site supervisor"},
  {"description": "Add a drop-zone check to the pre-task hazard assessment.", "control_level": "administrative", "suggested_owner_role": "Safety manager"},
  {"description": "Offer a first-aid and concussion check to the worker.", "control_level": "administrative", "suggested_owner_role": "First-aid attendant"}],
 "regulatory_note": "May meet a provincial notification threshold if medical aid or time loss follows. Verify with your provincial OHS regulator.",
 "confidence_and_gaps": "Impact magnitude is from the hat sensor only. The striking object is not visible in the clip."
}$j$),
('fall', $j${
 "summary": "The hat recorded a free-fall followed by a hard landing, consistent with a fall from a low height. The worker was on the ground briefly and then stood up.",
 "sequence_of_events": [
  {"offset_s": -0.5, "description": "Free-fall detected on the hat accelerometer.", "source": "sensor"},
  {"offset_s": 0.0, "description": "Landing impact recorded.", "source": "sensor"},
  {"offset_s": 1.0, "description": "Worker is seated on the ground.", "source": "video"},
  {"offset_s": 8.0, "description": "Worker stands up and a coworker approaches.", "source": "video"}],
 "evidence": {"clip_refs": [{"offset_s": 1.0, "observation": "Worker seated on the ground beside a stepladder."}],
  "environment": "Within normal range; biased by the wearer."},
 "hazard": {"category": "fall_from_height", "energy_source": "Gravity (elevation)"},
 "risk": {"severity": 4, "likelihood": 3, "score": 12,
  "severity_justification": "Falls from this height can cause fractures or head injury.",
  "likelihood_justification": "Stepladders are in frequent use on this floor."},
 "causes": {"immediate": [{"kind": "substandard_act", "description": "Stepladder used at its top rung."}],
  "root_five_whys": ["The worker fell from a stepladder.", "The ladder was too short for the task.", "A taller access platform was not available.", "Access equipment is not planned per task."]},
 "corrective_actions": [
  {"description": "Provide a platform or scaffold for this task.", "control_level": "substitution", "suggested_owner_role": "Site supervisor"},
  {"description": "Add ladder use rules to the toolbox talk.", "control_level": "administrative", "suggested_owner_role": "Safety manager"}],
 "regulatory_note": "May meet a provincial notification threshold depending on injury. Verify with your provincial OHS regulator.",
 "confidence_and_gaps": "Fall height is estimated from free-fall time. Injury status is not known from the evidence."
}$j$),
('knocked', $j${
 "summary": "The hat was knocked off the worker's head by a sharp contact and landed nearby. The worker retrieved and replaced it.",
 "sequence_of_events": [
  {"offset_s": 0.0, "description": "Sudden impact and loss of head contact recorded.", "source": "sensor"},
  {"offset_s": 0.8, "description": "Hat seen on the ground near the worker.", "source": "video"},
  {"offset_s": 6.0, "description": "Worker picks up and puts the hat back on.", "source": "video"}],
 "evidence": {"clip_refs": [{"offset_s": 0.8, "observation": "Hat on the ground; worker looking up at the structure above."}],
  "environment": "Within normal range; biased by the wearer."},
 "hazard": {"category": "ppe", "energy_source": "Mechanical contact"},
 "risk": {"severity": 3, "likelihood": 3, "score": 9,
  "severity_justification": "Head exposed briefly; no injury indicated.",
  "likelihood_justification": "Low clearance beams are present on this route."},
 "causes": {"immediate": [{"kind": "substandard_condition", "description": "Low-clearance beam not marked."}],
  "root_five_whys": ["The hat contacted a beam.", "The beam is at head height.", "Clearance hazards are not marked.", "Walk-throughs do not check clearances."]},
 "corrective_actions": [
  {"description": "Pad and mark the low-clearance beam.", "control_level": "engineering", "suggested_owner_role": "Site supervisor"},
  {"description": "Confirm chin straps are used where required.", "control_level": "ppe", "suggested_owner_role": "Crew supervisor"}],
 "regulatory_note": "Unlikely to meet a notification threshold on its own. Verify with your provincial OHS regulator.",
 "confidence_and_gaps": "Cause of dislodgement is inferred; the beam is only partly visible."
}$j$);

do $$
declare
  e record; v_eid uuid; v_cls public.event_class; v_t0 timestamptz; v_kind text; v_tpl jsonb;
  v_worker record; v_rno text; v_hdr jsonb; v_draft jsonb; v_final jsonb; v_signed boolean;
  v_peak numeric; v_cl numeric; v_ttc numeric; v_rng numeric; v_sen text; v_ff numeric; v_dh numeric;
  v_org uuid := 'a0000000-0000-4000-8000-000000000001'; v_status public.report_status;
  v_in int; v_out int; v_rid uuid; v_sevcls text;
begin
  for e in
    select * from (values
      (1, 'struck_by_near_miss', 0.5, 2, 'draft',  'incident', 'Video appears to show the load brushing the hat brim before the worker stepped clear.', null, null),
      (2, 'struck_by_near_miss', 2,   3, 'signed', null, null, 'near_miss', 'not_applicable'),
      (3, 'struck_by_near_miss', 5,   4, 'draft',  null, null, null, null),
      (4, 'head_impact',         3,   5, 'draft',  null, null, null, null),
      (5, 'fall',                9,   6, 'signed', null, null, 'incident', 'not_applicable'),
      (6, 'struck_by_near_miss', 11,  7, 'signed', 'incident', 'Possible contact with the worker''s shoulder.', 'near_miss', 'rejected'),
      (7, 'struck_by_near_miss', 14,  2, 'signed', 'incident', 'Video shows the load clipping the hat.', 'incident', 'accepted'),
      (8, 'hat_knocked_off',     17,  8, 'signed', null, null, 'incident', 'not_applicable'),
      (9, 'hat_removed',         1,   3, null, null, null, null, null),
      (10,'hat_removed',         6,   4, null, null, null, null, null),
      (11,'hat_removed',         12,  3, null, null, null, null, null),
      (12,'struck_by_contact',   19,  5, 'signed', null, null, 'incident', 'not_applicable')
    ) v(idx, type, days, dev, rstatus, sugg, reason, final, decision)
    order by days desc
  loop
    v_eid := ('00000000-0000-4000-8000-0000000000'||lpad(e.idx::text,2,'0'))::uuid;
    v_t0 := now() - (e.days * interval '1 day');
    v_cls := case e.type when 'struck_by_near_miss' then 'near_miss' when 'hat_removed' then 'ppe_log' else 'incident' end;
    v_kind := case e.type when 'struck_by_near_miss' then 'near' when 'fall' then 'fall'
                          when 'hat_knocked_off' then 'knocked' else 'impact' end;
    v_peak := case e.type when 'head_impact' then 9.4 when 'fall' then 12.1 when 'hat_knocked_off' then 6.2 when 'struck_by_contact' then 5.1 else null end;
    v_cl  := case when e.type in ('struck_by_near_miss','struck_by_contact') then 3.1 + e.idx * 0.3 end;
    v_ttc := case when e.type = 'struck_by_near_miss' then 0.7 end;
    v_rng := case when e.type in ('struck_by_near_miss','struck_by_contact') then 1.2 end;
    v_sen := case when e.type in ('struck_by_near_miss','struck_by_contact') then 'R3' end;
    v_ff  := case when e.type = 'fall' then 410 end;
    v_dh  := case when e.type = 'fall' then round((0.5 * 9.81 * 0.41 * 0.41)::numeric, 2) end;

    insert into public.events (id, device_id, type, t0, detected_at, device_class, llm_suggested_class, llm_reclass_reason,
      final_class, peak_g, closing_rate_mps, ttc_s, range_m, sensor_id, free_fall_ms, drop_height_m, hat_on,
      co2_ppm, temp_c, rh_pct, firmware, payload, status)
    values (v_eid, md5('device-'||e.dev)::uuid, e.type::public.event_type, v_t0, v_t0 + interval '200 ms', v_cls,
      e.sugg::public.event_class, e.reason, e.final::public.event_class,
      v_peak, v_cl, v_ttc, v_rng, v_sen, v_ff, v_dh, e.type <> 'hat_removed',
      560 + e.idx * 12, 6 + e.idx % 5, 48 + e.idx,
      '{"version":"0.1.0-seed","impls":{"imu":"mock","range":"mock"}}',
      jsonb_build_object('seeded', true, 'event_id', v_eid, 'type', e.type), 'ready');

    if e.rstatus is null then continue; end if;

    insert into public.media (event_id, kind, camera, filename, content_type, storage_path, duration_s, bytes, sha256, confirmed) values
      (v_eid, 'clip', 'front', 'front.mp4', 'video/mp4', v_org||'/'||v_eid||'/front.mp4', 30, 4200000 + e.idx*1000, md5('clip'||e.idx)||md5('clip2'||e.idx), true),
      (v_eid, 'sensor_trace', null, 'sensor_trace.json', 'application/json', v_org||'/'||v_eid||'/sensor_trace.json', null, 180000, md5('trace'||e.idx)||md5('trace2'||e.idx), true);

    v_in := 17000 + e.idx * 650; v_out := 800 + e.idx * 20;
    insert into public.analyses (event_id, stage, model, input_tokens, output_tokens, cost_usd, latency_ms, output_json) values
      (v_eid, 'video_analysis', 'gemini-flash', v_in, v_out, round((v_in*0.30 + v_out*2.50)/1e6, 6), 9000 + e.idx*350,
        jsonb_build_object('observations', jsonb_build_array(jsonb_build_object('offset_s', 0.5, 'text', 'seeded observation')))),
      (v_eid, 'report_draft', 'claude-opus', 6200 + e.idx*90, 2100 + e.idx*15,
        round(((6200 + e.idx*90)*15.0 + (2100 + e.idx*15)*75.0)/1e6, 6), 38000 + e.idx*900, '{"seeded": true}');

    select w.full_name, c.name as crew, c.supervisor_name, d.serial into v_worker
    from public.events ev join public.workers w on w.id = ev.worker_id join public.crews c on c.id = w.crew_id
    join public.devices d on d.id = ev.device_id where ev.id = v_eid;

    v_rno := public.next_report_no();
    v_tpl := (select body from tpl where kind = v_kind);
    v_signed := e.rstatus = 'signed';
    v_hdr := jsonb_build_object('report_no', v_rno, 'classification', v_cls, 'occurred_at', v_t0,
      'site', 'Riverbend Mixed-Use Tower (Phase 2)', 'location_on_site', 'Location to be determined',
      'worker', v_worker.full_name, 'crew', v_worker.crew, 'supervisor', v_worker.supervisor_name, 'device', v_worker.serial);
    v_draft := jsonb_build_object('schema_version', '1', 'header', v_hdr) || v_tpl || jsonb_build_object(
      'classification_review', jsonb_build_object('suggested_class', e.sugg, 'reason', e.reason),
      'sign_off', jsonb_build_object('signed_by', null, 'signed_at', null, 'final_class', null, 'reclass_decision', null),
      'footer', 'AI-generated draft for review by a competent person. Does not replace provincial OHS reporting or your employer''s incident-reporting procedure.');
    v_draft := jsonb_set(v_draft, '{evidence,key_sensor_values}', to_jsonb(array_remove(array[
      case when v_peak is not null then jsonb_build_object('name','peak_g','value',v_peak||' g') end,
      case when v_cl is not null then jsonb_build_object('name','closing_rate','value',v_cl||' m/s') end,
      case when v_ttc is not null then jsonb_build_object('name','ttc','value',v_ttc||' s') end,
      case when v_sen is not null then jsonb_build_object('name','sensor','value',v_sen) end,
      case when v_ff is not null then jsonb_build_object('name','free_fall','value',v_ff||' ms') end,
      jsonb_build_object('name','hat_on','value','true')], null)));
    update public.events set severity = (v_draft#>>'{risk,score}')::smallint where id = v_eid;

    v_final := v_draft;
    v_status := e.rstatus::public.report_status;
    if v_signed then
      v_final := jsonb_set(v_final, '{sign_off}', jsonb_build_object('signed_by', 'Dana Whitfield',
        'signed_at', v_t0 + interval '1 day', 'final_class', e.final, 'reclass_decision', e.decision));
    end if;
    if e.idx = 2 then  -- manager edited the summary before signing
      v_final := jsonb_set(v_final, '{summary}', to_jsonb('The hat flagged a fast-moving load passing close behind the worker. The worker was alerted and stepped clear; no contact occurred.'::text));
    end if;

    insert into public.reports (event_id, report_no, llm_draft_json, report_json, edited, status, signed_off_by, signed_off_at)
    values (v_eid, v_rno, v_draft, v_final, e.idx = 2, v_status,
      case when v_signed then 'Dana Whitfield' end, case when v_signed then v_t0 + interval '1 day' end)
    returning id into v_rid;
    if e.idx = 2 then
      insert into public.report_edits (org_id, report_id, edited_by, edited_at, field_path, old_value, new_value)
      values (v_org, v_rid, 'd0000000-0000-4000-8000-000000000001', v_t0 + interval '20 hours', 'summary',
        v_draft->'summary', v_final->'summary');
    end if;
  end loop;
end $$;
