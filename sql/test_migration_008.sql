-- Verification suite for migration 008 (settle_synthesis_results, Amendment A.1
-- + surgical corrections 1–4 + withdrawal-ordering fix).
-- Groups: S strict shape · W withdrawal (incl. W6/W7) · A atomicity ·
-- P provenance · U authority · F taxonomy · Z finalization · X access ·
-- B sufficiency boundaries · R reinforcement.
-- Engine-side blocks run as postgres (service path); client blocks run as
-- role `authenticated` with test.uid set, mirroring production grants.

\set ON_ERROR_STOP on

-- ---- production-parity grants (idempotent re-assert) ----
grant usage on schema public, auth, storage to authenticated, anon, service_role;
grant select on agents to authenticated;
grant select, insert, update, delete on jobs to authenticated;
grant execute on function auth.uid() to authenticated;

-- ---- fixtures ----
delete from agents where agent_no between 86 and 92;
insert into auth.users (id, email) values
  ('86868686-8686-8686-8686-868686868686','u86@example.com'),
  ('87878787-8787-8787-8787-878787878787','u87@example.com'),
  ('88888888-8888-8888-8888-888888888800','u88b@example.com'),
  ('89898989-8989-8989-8989-898989898989','u89@example.com'),
  ('90909090-9090-9090-9090-909090909090','u90@example.com'),
  ('91919191-9191-9191-9191-919191919191','u91@example.com'),
  ('92929292-9292-9292-9292-929292929292','u92@example.com')
on conflict do nothing;
insert into agents (id, user_id, agent_no, state) values
  ('a8600000-0000-0000-0000-000000000086','86868686-8686-8686-8686-868686868686',86,'invited'),
  ('a8700000-0000-0000-0000-000000000087','87878787-8787-8787-8787-878787878787',87,'invited'),
  ('a8800000-0000-0000-0000-000000000088','88888888-8888-8888-8888-888888888800',88,'invited'),
  ('a8900000-0000-0000-0000-000000000089','89898989-8989-8989-8989-898989898989',89,'invited'),
  ('a9000000-0000-0000-0000-000000000090','90909090-9090-9090-9090-909090909090',90,'invited'),
  ('a9100000-0000-0000-0000-000000000091','91919191-9191-9191-9191-919191919191',91,'at_work'),
  ('a9200000-0000-0000-0000-000000000092','92929292-9292-9292-9292-929292929292',92,'invited');

-- ============================================================
-- SEED — agent 87 job 1: establish existing active memory X
-- ============================================================
set role authenticated;
select set_config('test.uid','87878787-8787-8787-8787-878787878787', false);
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e8700000-0000-0000-0000-0000000000aa','a8700000-0000-0000-0000-000000000087','text','Seed note','Remote preference.');
insert into jobs (id, agent_id, type) values
  ('ba870001-0000-0000-0000-000000000001','a8700000-0000-0000-0000-000000000087','synthesize');
reset role;
do $$ declare r jsonb; begin
  r := claim_synthesis_batch('ba870001-0000-0000-0000-000000000001');
  if r->>'status' <> 'claimed' then raise exception 'FAIL seed claim: %', r; end if;
  r := settle_synthesis_results('ba870001-0000-0000-0000-000000000001',
    '{"read":["e8700000-0000-0000-0000-0000000000aa"],"failed":[],
      "memory":[{"layer":"record","statement":"Prefers remote work.","provenance":"stated",
                 "evidence":["e8700000-0000-0000-0000-0000000000aa"]}],
      "reinforce":[]}'::jsonb,
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  if r->>'outcome' <> 'needs_more_evidence' then raise exception 'FAIL seed outcome: %', r; end if;
end $$;

-- ============================================================
-- S/A/C/U/F — agent 86: strict shape, atomicity, completeness
-- ============================================================
set role authenticated;
select set_config('test.uid','86868686-8686-8686-8686-868686868686', false);
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e8600001-0000-0000-0000-000000000001','a8600000-0000-0000-0000-000000000086','text','Career notes A','Fact one.'),
  ('e8600002-0000-0000-0000-000000000002','a8600000-0000-0000-0000-000000000086','text','Career notes B','Fact two.');
insert into jobs (id, agent_id, type) values
  ('ba860001-0000-0000-0000-000000000001','a8600000-0000-0000-0000-000000000086','synthesize');
reset role;
do $$ declare r jsonb; begin
  r := claim_synthesis_batch('ba860001-0000-0000-0000-000000000001');
  if r->>'status' <> 'claimed' or (r->>'count')::int <> 2 then
    raise exception 'FAIL s-claim: %', r; end if;
end $$;

-- Z5 · guard control: direct terminal jobs UPDATE on the running job refused
do $$ begin
  begin
    update jobs set status='done', completed_at=now()
      where id='ba860001-0000-0000-0000-000000000001';
    raise exception 'FAIL z5: direct terminal update accepted';
  exception when others then
    if sqlerrm <> 'finalize_required' then raise exception 'FAIL z5 got: %', sqlerrm; end if;
  end;
end $$;

-- S1 · policy negatives → invalid_policy, nothing committed
do $$
declare bad jsonb; lbl text;
begin
  for bad, lbl in
    select * from (values
      ('{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5,"extra":1}'::jsonb,'unknown key'),
      ('{"min_grounded":5,"require_record":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb,'missing require_self'),
      ('{"min_grounded":0,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb,'min 0'),
      ('{"min_grounded":101,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb,'min 101'),
      ('{"min_grounded":2.5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb,'min 2.5'),
      ('{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":1.5}'::jsonb,'ratio 1.5'),
      ('{"min_grounded":5,"require_record":"yes","require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb,'string boolean'),
      (null::jsonb,'null policy')
    ) v(b,l)
  loop
    begin
      perform settle_synthesis_results('ba860001-0000-0000-0000-000000000001',
        '{"read":[],"failed":[],"memory":[],"reinforce":[]}'::jsonb, bad);
      raise exception 'FAIL s1 (%): accepted', lbl;
    exception when others then
      if sqlerrm <> 'invalid_policy' then raise exception 'FAIL s1 (%) got: %', lbl, sqlerrm; end if;
    end;
  end loop;
end $$;

-- S2 · results-shape negatives → invalid_results (incl. corrections 3 & 4)
do $$
declare bad jsonb; lbl text;
begin
  for bad, lbl in
    select * from (values
      ('{"read":[],"failed":[],"memory":[],"reinforce":[],"extra":[]}'::jsonb,'unknown envelope key'),
      ('{"read":{},"failed":[],"memory":[],"reinforce":[]}'::jsonb,'read not array'),
      ('{"read":["e8600001-0000-0000-0000-000000000001","e8600002-0000-0000-0000-000000000002"],"failed":[],"memory":[{"layer":"record","statement":"x","provenance":"stated","evidence":["e8600001-0000-0000-0000-000000000001"],"source":"smuggled"}],"reinforce":[]}'::jsonb,'source key refused (corr 3)'),
      ('{"read":["e8600001-0000-0000-0000-000000000001","e8600002-0000-0000-0000-000000000002"],"failed":[],"memory":[{"layer":"record","statement":5,"provenance":"stated","evidence":["e8600001-0000-0000-0000-000000000001"]}],"reinforce":[]}'::jsonb,'numeric statement (corr 4)'),
      ('{"read":["e8600001-0000-0000-0000-000000000001"],"failed":[{"item":"e8600002-0000-0000-0000-000000000002","code":123}],"memory":[],"reinforce":[]}'::jsonb,'numeric code (corr 4)'),
      ('{"read":["e8600001-0000-0000-0000-000000000001","e8600002-0000-0000-0000-000000000002"],"failed":[],"memory":[{"layer":"record","statement":"x","provenance":"stated"}],"reinforce":[]}'::jsonb,'missing evidence'),
      ('{"read":["e8600001-0000-0000-0000-000000000001","e8600002-0000-0000-0000-000000000002"],"failed":[],"memory":[{"layer":"record","statement":"x","provenance":"stated","evidence":[]}],"reinforce":[]}'::jsonb,'empty evidence')
    ) v(b,l)
  loop
    begin
      perform settle_synthesis_results('ba860001-0000-0000-0000-000000000001', bad,
        '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
      raise exception 'FAIL s2 (%): accepted', lbl;
    exception when others then
      if sqlerrm <> 'invalid_results' then raise exception 'FAIL s2 (%) got: %', lbl, sqlerrm; end if;
    end;
  end loop;
  -- oversize statement built dynamically
  begin
    perform settle_synthesis_results('ba860001-0000-0000-0000-000000000001',
      jsonb_build_object('read', jsonb_build_array('e8600001-0000-0000-0000-000000000001','e8600002-0000-0000-0000-000000000002'),
        'failed','[]'::jsonb,
        'memory', jsonb_build_array(jsonb_build_object('layer','record','statement',repeat('x',1001),
          'provenance','stated','evidence', jsonb_build_array('e8600001-0000-0000-0000-000000000001'))),
        'reinforce','[]'::jsonb),
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
    raise exception 'FAIL s2 oversize: accepted';
  exception when others then
    if sqlerrm <> 'invalid_results' then raise exception 'FAIL s2 oversize got: %', sqlerrm; end if;
  end;
end $$;

-- S3/S4 · duplicate verdicts, foreign items, invalid uuid
do $$
declare bad jsonb; lbl text; exp text;
begin
  for bad, lbl, exp in
    select * from (values
      ('{"read":["e8600001-0000-0000-0000-000000000001","e8600001-0000-0000-0000-000000000001"],"failed":[],"memory":[],"reinforce":[]}'::jsonb,'read twice','duplicate_verdict'),
      ('{"read":["e8600001-0000-0000-0000-000000000001"],"failed":[{"item":"e8600001-0000-0000-0000-000000000001","code":"unreadable"}],"memory":[],"reinforce":[]}'::jsonb,'read+failed','duplicate_verdict'),
      ('{"read":["e8700000-0000-0000-0000-0000000000aa"],"failed":[],"memory":[],"reinforce":[]}'::jsonb,'other-job item','item_not_in_batch'),
      ('{"read":["dddddddd-dddd-dddd-dddd-dddddddddddd"],"failed":[],"memory":[],"reinforce":[]}'::jsonb,'unknown item','item_not_in_batch'),
      ('{"read":["banana"],"failed":[],"memory":[],"reinforce":[]}'::jsonb,'non-uuid','invalid_uuid')
    ) v(b,l,x)
  loop
    begin
      perform settle_synthesis_results('ba860001-0000-0000-0000-000000000001', bad,
        '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
      raise exception 'FAIL s34 (%): accepted', lbl;
    exception when others then
      if sqlerrm <> exp then raise exception 'FAIL s34 (%) got: %', lbl, sqlerrm; end if;
    end;
  end loop;
end $$;

-- U3/U4/F2 · behavior layer, reserved provenance, unknown failure code
do $$
declare bad jsonb; lbl text; exp text;
begin
  for bad, lbl, exp in
    select * from (values
      ('{"read":["e8600001-0000-0000-0000-000000000001","e8600002-0000-0000-0000-000000000002"],"failed":[],"memory":[{"layer":"behavior","statement":"x","provenance":"stated","evidence":["e8600001-0000-0000-0000-000000000001"]}],"reinforce":[]}'::jsonb,'behavior','layer_not_allowed'),
      ('{"read":["e8600001-0000-0000-0000-000000000001","e8600002-0000-0000-0000-000000000002"],"failed":[],"memory":[{"layer":"record","statement":"x","provenance":"observed","evidence":["e8600001-0000-0000-0000-000000000001"]}],"reinforce":[]}'::jsonb,'observed','provenance_not_allowed'),
      ('{"read":["e8600001-0000-0000-0000-000000000001","e8600002-0000-0000-0000-000000000002"],"failed":[],"memory":[{"layer":"record","statement":"x","provenance":"confirmed","evidence":["e8600001-0000-0000-0000-000000000001"]}],"reinforce":[]}'::jsonb,'confirmed','provenance_not_allowed'),
      ('{"read":["e8600001-0000-0000-0000-000000000001"],"failed":[{"item":"e8600002-0000-0000-0000-000000000002","code":"model_says_oops"}],"memory":[],"reinforce":[]}'::jsonb,'bad code','invalid_failure_code'),
      ('{"read":["e8600001-0000-0000-0000-000000000001","e8600002-0000-0000-0000-000000000002"],"failed":[],"memory":[{"layer":"record","statement":"Duplicate  Fact.","provenance":"stated","evidence":["e8600001-0000-0000-0000-000000000001"]},{"layer":"self","statement":"duplicate fact.","provenance":"stated","evidence":["e8600002-0000-0000-0000-000000000002"]}],"reinforce":[]}'::jsonb,'payload dup stmt','duplicate_statement'),
      ('{"read":["e8600001-0000-0000-0000-000000000001","e8600002-0000-0000-0000-000000000002"],"failed":[],"memory":[{"layer":"record","statement":"x","provenance":"stated","evidence":["e8600001-0000-0000-0000-000000000001","e8600001-0000-0000-0000-000000000001"]}],"reinforce":[]}'::jsonb,'dup citation','duplicate_citation')
    ) v(b,l,x)
  loop
    begin
      perform settle_synthesis_results('ba860001-0000-0000-0000-000000000001', bad,
        '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
      raise exception 'FAIL uf (%): accepted', lbl;
    exception when others then
      if sqlerrm <> exp then raise exception 'FAIL uf (%) got: %', lbl, sqlerrm; end if;
    end;
  end loop;
end $$;

-- A1 · atomicity: late failure rolls back EVERYTHING
do $$ declare n int; s text; begin
  begin
    perform settle_synthesis_results('ba860001-0000-0000-0000-000000000001',
      '{"read":["e8600001-0000-0000-0000-000000000001","e8600002-0000-0000-0000-000000000002"],"failed":[],
        "memory":[{"layer":"record","statement":"Good grounded row.","provenance":"stated","evidence":["e8600001-0000-0000-0000-000000000001"]},
                  {"layer":"record","statement":"Bad citation row.","provenance":"stated","evidence":["dddddddd-dddd-dddd-dddd-dddddddddddd"]}],
        "reinforce":[]}'::jsonb,
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
    raise exception 'FAIL a1: bad citation accepted';
  exception when others then
    if sqlerrm <> 'evidence_not_committed' then raise exception 'FAIL a1 got: %', sqlerrm; end if;
  end;
  select status into s from evidence_items where id='e8600001-0000-0000-0000-000000000001';
  if s <> 'reading' then raise exception 'FAIL a1 rollback item: %', s; end if;
  select count(*) into n from memory where agent_id='a8600000-0000-0000-0000-000000000086';
  if n <> 0 then raise exception 'FAIL a1 rollback memory: %', n; end if;
  select status into s from jobs where id='ba860001-0000-0000-0000-000000000001';
  if s <> 'running' then raise exception 'FAIL a1 rollback job: %', s; end if;
  select state into s from agents where id='a8600000-0000-0000-0000-000000000086';
  if s <> 'feed_submitted' then raise exception 'FAIL a1 rollback agent: %', s; end if;
end $$;

-- S6 · verdict_missing: unverdicted reading item
do $$ begin
  begin
    perform settle_synthesis_results('ba860001-0000-0000-0000-000000000001',
      '{"read":["e8600001-0000-0000-0000-000000000001"],"failed":[],"memory":[],"reinforce":[]}'::jsonb,
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
    raise exception 'FAIL s6: missing verdict accepted';
  exception when others then
    if sqlerrm <> 'verdict_missing' then raise exception 'FAIL s6 got: %', sqlerrm; end if;
  end;
end $$;

-- C1 · correction 1: a DELETED batch item still needs an explicit verdict
set role authenticated;
select set_config('test.uid','86868686-8686-8686-8686-868686868686', false);
update evidence_items set status='deleted' where id='e8600002-0000-0000-0000-000000000002';
reset role;
do $$ begin
  begin
    perform settle_synthesis_results('ba860001-0000-0000-0000-000000000001',
      '{"read":["e8600001-0000-0000-0000-000000000001"],"failed":[],"memory":[],"reinforce":[]}'::jsonb,
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
    raise exception 'FAIL c1: silently omitted deleted item accepted';
  exception when others then
    if sqlerrm <> 'verdict_missing' then raise exception 'FAIL c1 got: %', sqlerrm; end if;
  end;
end $$;

-- Successful settle for agent 86: withdrawal recorded, source derived, forced flags
do $$ declare r jsonb; s text; n int; m record; begin
  r := settle_synthesis_results('ba860001-0000-0000-0000-000000000001',
    '{"read":["e8600001-0000-0000-0000-000000000001","e8600002-0000-0000-0000-000000000002"],"failed":[],
      "memory":[{"layer":"record","statement":"Solo grounded fact.","provenance":"stated","evidence":["e8600001-0000-0000-0000-000000000001"]}],
      "reinforce":[]}'::jsonb,
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  if r->>'outcome' <> 'needs_more_evidence' then raise exception 'FAIL s-final outcome: %', r; end if;
  if (r->>'items_read')::int <> 1 or (r->>'items_withdrawn')::int <> 1 then
    raise exception 'FAIL s-final counts: %', r; end if;
  if (r->'finalize'->>'swept_reading_items')::int <> 0 then
    raise exception 'FAIL s-final sweep not zero: %', r; end if;
  select status into s from jobs where id='ba860001-0000-0000-0000-000000000001';
  if s <> 'done' then raise exception 'FAIL s-final job: %', s; end if;
  select state into s from agents where id='a8600000-0000-0000-0000-000000000086';
  if s <> 'commissioning' then raise exception 'FAIL s-final agent: %', s; end if;
  select status into s from evidence_items where id='e8600001-0000-0000-0000-000000000001';
  if s <> 'read' then raise exception 'FAIL s-final item read: %', s; end if;
  select status into s from evidence_items where id='e8600002-0000-0000-0000-000000000002';
  if s <> 'deleted' then raise exception 'FAIL s-final withdrawn item: %', s; end if;
  select * into m from memory where agent_id='a8600000-0000-0000-0000-000000000086';
  if m.source <> 'Career notes A' then raise exception 'FAIL corr3 source: %', m.source; end if;
  if m.can_affect_search or m.can_appear_publicly then raise exception 'FAIL u1 flags forced'; end if;
  if m.supersedes is not null or m.expires is not null then raise exception 'FAIL u2 engine cols'; end if;
  if m.evidence <> '[{"item":"e8600001-0000-0000-0000-000000000001"}]'::jsonb then
    raise exception 'FAIL canonical evidence: %', m.evidence; end if;
end $$;

-- Z6 · settle a terminal job again → job_not_running
do $$ begin
  begin
    perform settle_synthesis_results('ba860001-0000-0000-0000-000000000001',
      '{"read":[],"failed":[],"memory":[],"reinforce":[]}'::jsonb,
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
    raise exception 'FAIL z6: double settle accepted';
  exception when others then
    if sqlerrm <> 'job_not_running' then raise exception 'FAIL z6 got: %', sqlerrm; end if;
  end;
end $$;

-- ============================================================
-- W — agent 87 job 2: withdrawal wins, W1–W7
-- ============================================================
set role authenticated;
select set_config('test.uid','87878787-8787-8787-8787-878787878787', false);
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e8700001-0000-0000-0000-000000000001','a8700000-0000-0000-0000-000000000087','text','Survivor note','Kept.'),
  ('e8700002-0000-0000-0000-000000000002','a8700000-0000-0000-0000-000000000087','text','Withdrawn note','Gone.'),
  ('e8700003-0000-0000-0000-000000000003','a8700000-0000-0000-0000-000000000087','text','Withdrawn fail','Gone too.');
insert into jobs (id, agent_id, type) values
  ('ba870002-0000-0000-0000-000000000002','a8700000-0000-0000-0000-000000000087','synthesize');
reset role;
do $$ declare r jsonb; begin
  r := claim_synthesis_batch('ba870002-0000-0000-0000-000000000002');
  if (r->>'count')::int <> 3 then raise exception 'FAIL w-claim: %', r; end if;
end $$;
set role authenticated;
select set_config('test.uid','87878787-8787-8787-8787-878787878787', false);
update evidence_items set status='deleted' where id='e8700002-0000-0000-0000-000000000002';
update evidence_items set status='deleted' where id='e8700003-0000-0000-0000-000000000003';
reset role;
do $$ declare r jsonb; s text; n int; m record; begin
  r := settle_synthesis_results('ba870002-0000-0000-0000-000000000002',
    '{"read":["e8700001-0000-0000-0000-000000000001","e8700002-0000-0000-0000-000000000002"],
      "failed":[{"item":"e8700003-0000-0000-0000-000000000003","code":"unreadable"}],
      "memory":[
        {"layer":"record","statement":"Prefers remote work.","provenance":"stated","evidence":["e8700002-0000-0000-0000-000000000002"]},
        {"layer":"record","statement":"New skill in analytics.","provenance":"stated","evidence":["e8700002-0000-0000-0000-000000000002"]},
        {"layer":"record","statement":"new skill in ANALYTICS.","provenance":"stated","evidence":["e8700001-0000-0000-0000-000000000001"]},
        {"layer":"self","statement":"Combined claim across items.","provenance":"extracted","evidence":["e8700001-0000-0000-0000-000000000001","e8700002-0000-0000-0000-000000000002"]},
        {"layer":"model","statement":"Tension: sources differ on preference.","provenance":"extracted","evidence":["e8700001-0000-0000-0000-000000000001"],"tension":true}
      ],
      "reinforce":[]}'::jsonb,
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  -- W6: dup-vs-existing row was withdrawn → discarded, NO reinforce_required.
  -- W7: payload-dup pair → withdrawn copy discarded, survivor inserted, NO duplicate_statement.
  if r->>'outcome' <> 'needs_more_evidence' then raise exception 'FAIL w outcome: %', r; end if;
  if (r->>'items_read')::int <> 1 or (r->>'items_withdrawn')::int <> 2
     or (r->>'items_failed')::int <> 0 then raise exception 'FAIL w counts: %', r; end if;
  if (r->>'statements_discarded')::int <> 3 then raise exception 'FAIL w discarded: %', r; end if;
  if (r->>'memory_inserted')::int <> 1 or (r->>'tension_rows')::int <> 1 then
    raise exception 'FAIL w inserted: %', r; end if;
  select status into s from evidence_items where id='e8700002-0000-0000-0000-000000000002';
  if s <> 'deleted' then raise exception 'FAIL w1 withdrawn became %', s; end if;
  select failure_reason into s from evidence_items where id='e8700003-0000-0000-0000-000000000003';
  if s is not null then raise exception 'FAIL w5 reason written: %', s; end if;
  select count(*) into n from memory
    where agent_id='a8700000-0000-0000-0000-000000000087' and status='active';
  if n <> 2 then raise exception 'FAIL w memory count: %', n; end if;  -- X + W7 survivor
  select * into m from memory
    where agent_id='a8700000-0000-0000-0000-000000000087' and statement='new skill in ANALYTICS.';
  if m.source <> 'Survivor note' then raise exception 'FAIL w7 source: %', m.source; end if;
  select * into m from memory
    where agent_id='a8700000-0000-0000-0000-000000000087' and statement='Prefers remote work.';
  if m.last_reinforced is not null then raise exception 'FAIL w6 side effect on X'; end if;
  if m.evidence <> '[{"item":"e8700000-0000-0000-0000-0000000000aa"}]'::jsonb then
    raise exception 'FAIL w6 X evidence mutated: %', m.evidence; end if;
end $$;

-- W4 — agent 88: entire batch withdrawn → needs_more_evidence, no failure charged
set role authenticated;
select set_config('test.uid','88888888-8888-8888-8888-888888888800', false);
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e8800001-0000-0000-0000-000000000001','a8800000-0000-0000-0000-000000000088','text','Only item','Text.');
insert into jobs (id, agent_id, type) values
  ('ba880001-0000-0000-0000-000000000001','a8800000-0000-0000-0000-000000000088','synthesize');
reset role;
do $$ declare r jsonb; begin
  r := claim_synthesis_batch('ba880001-0000-0000-0000-000000000001');
end $$;
set role authenticated;
select set_config('test.uid','88888888-8888-8888-8888-888888888800', false);
update evidence_items set status='deleted' where id='e8800001-0000-0000-0000-000000000001';
reset role;
do $$ declare r jsonb; s text; n int; begin
  r := settle_synthesis_results('ba880001-0000-0000-0000-000000000001',
    '{"read":["e8800001-0000-0000-0000-000000000001"],"failed":[],"memory":[],"reinforce":[]}'::jsonb,
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  if r->>'outcome' <> 'needs_more_evidence' then raise exception 'FAIL w4 outcome: %', r; end if;
  select count(*) into n from memory where agent_id='a8800000-0000-0000-0000-000000000088';
  if n <> 0 then raise exception 'FAIL w4 memory: %', n; end if;
  select status into s from jobs where id='ba880001-0000-0000-0000-000000000001';
  if s <> 'done' then raise exception 'FAIL w4 job: %', s; end if;
  select state into s from agents where id='a8800000-0000-0000-0000-000000000088';
  if s <> 'commissioning' then raise exception 'FAIL w4 agent: %', s; end if;
end $$;

-- ============================================================
-- Z1 — agent 89: sufficient → mirror_ready
-- ============================================================
set role authenticated;
select set_config('test.uid','89898989-8989-8989-8989-898989898989', false);
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e8900001-0000-0000-0000-000000000001','a8900000-0000-0000-0000-000000000089','text','CV notes','Career facts.'),
  ('e8900002-0000-0000-0000-000000000002','a8900000-0000-0000-0000-000000000089','text','Goals doc','Direction.');
insert into jobs (id, agent_id, type) values
  ('ba890001-0000-0000-0000-000000000001','a8900000-0000-0000-0000-000000000089','synthesize');
reset role;
do $$ declare r jsonb; s text; n int; begin
  r := claim_synthesis_batch('ba890001-0000-0000-0000-000000000001');
  r := settle_synthesis_results('ba890001-0000-0000-0000-000000000001',
    '{"read":["e8900001-0000-0000-0000-000000000001","e8900002-0000-0000-0000-000000000002"],"failed":[],
      "memory":[
        {"layer":"record","statement":"Ten years in product marketing.","provenance":"stated","evidence":["e8900001-0000-0000-0000-000000000001"]},
        {"layer":"record","statement":"Led launches in two markets.","provenance":"extracted","evidence":["e8900001-0000-0000-0000-000000000001"]},
        {"layer":"self","statement":"I do my best work with autonomy.","provenance":"stated","evidence":["e8900001-0000-0000-0000-000000000001"]},
        {"layer":"self","statement":"I want a senior marketing leadership role.","provenance":"stated","evidence":["e8900002-0000-0000-0000-000000000002"],"is_direction":true},
        {"layer":"model","statement":"Consistent growth trajectory across roles.","provenance":"inferred","evidence":["e8900001-0000-0000-0000-000000000001","e8900002-0000-0000-0000-000000000002"]},
        {"layer":"model","statement":"Tension: pace preference differs between docs.","provenance":"extracted","evidence":["e8900002-0000-0000-0000-000000000002"],"tension":true}
      ],
      "reinforce":[]}'::jsonb,
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  if r->>'outcome' <> 'mirror_ready' then raise exception 'FAIL z1 outcome: %', r; end if;
  if (r->>'grounded_total')::int <> 5 then raise exception 'FAIL z1 grounded: %', r; end if;
  select state into s from agents where id='a8900000-0000-0000-0000-000000000089';
  if s <> 'mirror_ready' then raise exception 'FAIL z1 agent: %', s; end if;
  select status into s from jobs where id='ba890001-0000-0000-0000-000000000001';
  if s <> 'done' then raise exception 'FAIL z1 job: %', s; end if;
  select count(*) into n from memory
    where agent_id='a8900000-0000-0000-0000-000000000089' and can_affect_search;
  if n <> 0 then raise exception 'FAIL z1 search flag leaked: %', n; end if;
  -- P2: tension row landed with canonical committed citations (door-validated)
  select count(*) into n from memory
    where agent_id='a8900000-0000-0000-0000-000000000089' and status='tension';
  if n <> 1 then raise exception 'FAIL z1 tension count: %', n; end if;
end $$;

-- ============================================================
-- Z4/F1 — agent 90: all items failed → settle computes failed
-- ============================================================
set role authenticated;
select set_config('test.uid','90909090-9090-9090-9090-909090909090', false);
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e9000001-0000-0000-0000-000000000001','a9000000-0000-0000-0000-000000000090','text','Bad one','x'),
  ('e9000002-0000-0000-0000-000000000002','a9000000-0000-0000-0000-000000000090','text','Bad two','y');
insert into jobs (id, agent_id, type) values
  ('ba900001-0000-0000-0000-000000000001','a9000000-0000-0000-0000-000000000090','synthesize');
reset role;
do $$ declare r jsonb; s text; begin
  r := claim_synthesis_batch('ba900001-0000-0000-0000-000000000001');
  r := settle_synthesis_results('ba900001-0000-0000-0000-000000000001',
    '{"read":[],
      "failed":[{"item":"e9000001-0000-0000-0000-000000000001","code":"unreadable"},
                {"item":"e9000002-0000-0000-0000-000000000002","code":"no_text_pdf"}],
      "memory":[],"reinforce":[]}'::jsonb,
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  if r->>'outcome' <> 'failed' then raise exception 'FAIL z4 outcome: %', r; end if;
  select error into s from jobs where id='ba900001-0000-0000-0000-000000000001';
  if s <> 'FOOUND couldn''t read what you added. Remove the failed items and try again.' then
    raise exception 'FAIL z4 batch copy: %', s; end if;
  select failure_reason into s from evidence_items where id='e9000001-0000-0000-0000-000000000001';
  if s <> 'FOOUND couldn''t read this file. Remove it and try again.' then
    raise exception 'FAIL f1 unreadable copy: %', s; end if;
  select failure_reason into s from evidence_items where id='e9000002-0000-0000-0000-000000000002';
  if s <> 'FOOUND couldn''t find readable text in this PDF. Add a text-based PDF, DOCX, TXT or MD instead.' then
    raise exception 'FAIL f1 no_text_pdf copy: %', s; end if;
  select state into s from agents where id='a9000000-0000-0000-0000-000000000090';
  if s <> 'commissioning' then raise exception 'FAIL z4 agent: %', s; end if;
end $$;

-- ============================================================
-- Z3/B/R — agent 91 (at_work): outcomes never move an established agent;
-- sufficiency boundaries; reinforcement mechanics
-- ============================================================
set role authenticated;
select set_config('test.uid','91919191-9191-9191-9191-919191919191', false);
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e9100001-0000-0000-0000-000000000001','a9100000-0000-0000-0000-000000000091','text','Portfolio notes','Facts.'),
  ('e9100002-0000-0000-0000-000000000002','a9100000-0000-0000-0000-000000000091','text','Review 2025','More facts.');
insert into jobs (id, agent_id, type) values
  ('ba910001-0000-0000-0000-000000000001','a9100000-0000-0000-0000-000000000091','synthesize');
reset role;
do $$ declare r jsonb; s text; begin
  r := claim_synthesis_batch('ba910001-0000-0000-0000-000000000001');
  r := settle_synthesis_results('ba910001-0000-0000-0000-000000000001',
    '{"read":["e9100001-0000-0000-0000-000000000001","e9100002-0000-0000-0000-000000000002"],"failed":[],
      "memory":[
        {"layer":"record","statement":"Led brand at three companies.","provenance":"stated","evidence":["e9100001-0000-0000-0000-000000000001"]},
        {"layer":"record","statement":"Managed a team of twelve people.","provenance":"extracted","evidence":["e9100002-0000-0000-0000-000000000002"]},
        {"layer":"self","statement":"I value autonomy in how work is structured.","provenance":"stated","evidence":["e9100002-0000-0000-0000-000000000002"]},
        {"layer":"self","statement":"I want senior brand leadership roles next.","provenance":"stated","evidence":["e9100001-0000-0000-0000-000000000001"],"is_direction":true},
        {"layer":"record","statement":"Based in Lisbon and open to hybrid.","provenance":"stated","evidence":["e9100002-0000-0000-0000-000000000002"]},
        {"layer":"model","statement":"Tension: scope ambition differs between documents.","provenance":"extracted","evidence":["e9100001-0000-0000-0000-000000000001"],"tension":true}
      ],
      "reinforce":[]}'::jsonb,
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  if r->>'outcome' <> 'mirror_ready' then raise exception 'FAIL z3a outcome: %', r; end if;
  select state into s from agents where id='a9100000-0000-0000-0000-000000000091';
  if s <> 'at_work' then raise exception 'FAIL z3 at_work moved: %', s; end if;
end $$;

-- B1 · one under min_grounded + corr-2b reinforce_required negative
set role authenticated;
select set_config('test.uid','91919191-9191-9191-9191-919191919191', false);
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e9100011-0000-0000-0000-000000000011','a9100000-0000-0000-0000-000000000091','text','B1 note','b1');
insert into jobs (id, agent_id, type) values
  ('ba910002-0000-0000-0000-000000000002','a9100000-0000-0000-0000-000000000091','synthesize');
reset role;
do $$ declare r jsonb; s text; begin
  r := claim_synthesis_batch('ba910002-0000-0000-0000-000000000002');
  -- corr 2b: surviving statement duplicating EXISTING active memory → reinforce_required
  begin
    perform settle_synthesis_results('ba910002-0000-0000-0000-000000000002',
      '{"read":["e9100011-0000-0000-0000-000000000011"],"failed":[],
        "memory":[{"layer":"record","statement":"led  brand AT three companies.","provenance":"stated","evidence":["e9100011-0000-0000-0000-000000000011"]}],
        "reinforce":[]}'::jsonb,
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
    raise exception 'FAIL c2b: existing-statement insert accepted';
  exception when others then
    if sqlerrm <> 'reinforce_required' then raise exception 'FAIL c2b got: %', sqlerrm; end if;
  end;
  r := settle_synthesis_results('ba910002-0000-0000-0000-000000000002',
    '{"read":["e9100011-0000-0000-0000-000000000011"],"failed":[],
      "memory":[
        {"layer":"record","statement":"B1 fresh record fact.","provenance":"stated","evidence":["e9100011-0000-0000-0000-000000000011"]},
        {"layer":"record","statement":"B1 second record fact.","provenance":"stated","evidence":["e9100011-0000-0000-0000-000000000011"]},
        {"layer":"self","statement":"B1 self view.","provenance":"stated","evidence":["e9100011-0000-0000-0000-000000000011"]},
        {"layer":"self","statement":"B1 direction next step.","provenance":"stated","evidence":["e9100011-0000-0000-0000-000000000011"],"is_direction":true}
      ],
      "reinforce":[]}'::jsonb,
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  if r->>'outcome' <> 'needs_more_evidence' or (r->>'grounded_total')::int <> 4 then
    raise exception 'FAIL b1: %', r; end if;
  select state into s from agents where id='a9100000-0000-0000-0000-000000000091';
  if s <> 'at_work' then raise exception 'FAIL b1 at_work moved: %', s; end if;
end $$;

-- B2 · direction present only as inferred → not mirror_ready
set role authenticated;
select set_config('test.uid','91919191-9191-9191-9191-919191919191', false);
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e9100012-0000-0000-0000-000000000012','a9100000-0000-0000-0000-000000000091','text','B2 note','b2');
insert into jobs (id, agent_id, type) values
  ('ba910003-0000-0000-0000-000000000003','a9100000-0000-0000-0000-000000000091','synthesize');
reset role;
do $$ declare r jsonb; begin
  r := claim_synthesis_batch('ba910003-0000-0000-0000-000000000003');
  r := settle_synthesis_results('ba910003-0000-0000-0000-000000000003',
    '{"read":["e9100012-0000-0000-0000-000000000012"],"failed":[],
      "memory":[
        {"layer":"record","statement":"B2 record one.","provenance":"stated","evidence":["e9100012-0000-0000-0000-000000000012"]},
        {"layer":"record","statement":"B2 record two.","provenance":"stated","evidence":["e9100012-0000-0000-0000-000000000012"]},
        {"layer":"self","statement":"B2 self one.","provenance":"stated","evidence":["e9100012-0000-0000-0000-000000000012"]},
        {"layer":"self","statement":"B2 self two.","provenance":"stated","evidence":["e9100012-0000-0000-0000-000000000012"]},
        {"layer":"model","statement":"B2 guessed direction.","provenance":"inferred","evidence":["e9100012-0000-0000-0000-000000000012"],"is_direction":true}
      ],
      "reinforce":[]}'::jsonb,
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  if r->>'outcome' <> 'needs_more_evidence' then raise exception 'FAIL b2 inferred direction passed: %', r; end if;
end $$;

-- B3 · failure ratio exactly at max (strict <) → not mirror_ready
set role authenticated;
select set_config('test.uid','91919191-9191-9191-9191-919191919191', false);
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e9100013-0000-0000-0000-000000000013','a9100000-0000-0000-0000-000000000091','text','B3 good','g'),
  ('e9100014-0000-0000-0000-000000000014','a9100000-0000-0000-0000-000000000091','text','B3 bad','b');
insert into jobs (id, agent_id, type) values
  ('ba910004-0000-0000-0000-000000000004','a9100000-0000-0000-0000-000000000091','synthesize');
reset role;
do $$ declare r jsonb; begin
  r := claim_synthesis_batch('ba910004-0000-0000-0000-000000000004');
  r := settle_synthesis_results('ba910004-0000-0000-0000-000000000004',
    '{"read":["e9100013-0000-0000-0000-000000000013"],
      "failed":[{"item":"e9100014-0000-0000-0000-000000000014","code":"unreadable"}],
      "memory":[
        {"layer":"record","statement":"B3 record one.","provenance":"stated","evidence":["e9100013-0000-0000-0000-000000000013"]},
        {"layer":"record","statement":"B3 record two.","provenance":"stated","evidence":["e9100013-0000-0000-0000-000000000013"]},
        {"layer":"self","statement":"B3 self one.","provenance":"stated","evidence":["e9100013-0000-0000-0000-000000000013"]},
        {"layer":"self","statement":"B3 direction.","provenance":"stated","evidence":["e9100013-0000-0000-0000-000000000013"],"is_direction":true},
        {"layer":"record","statement":"B3 record three.","provenance":"stated","evidence":["e9100013-0000-0000-0000-000000000013"]}
      ],
      "reinforce":[]}'::jsonb,
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  if r->>'outcome' <> 'needs_more_evidence' then
    raise exception 'FAIL b3 ratio-at-max passed: %', r; end if;
end $$;

-- B4 · relaxed policy → policy drives the outcome
set role authenticated;
select set_config('test.uid','91919191-9191-9191-9191-919191919191', false);
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e9100015-0000-0000-0000-000000000015','a9100000-0000-0000-0000-000000000091','text','B4 note','b4');
insert into jobs (id, agent_id, type) values
  ('ba910005-0000-0000-0000-000000000005','a9100000-0000-0000-0000-000000000091','synthesize');
reset role;
do $$ declare r jsonb; s text; begin
  r := claim_synthesis_batch('ba910005-0000-0000-0000-000000000005');
  r := settle_synthesis_results('ba910005-0000-0000-0000-000000000005',
    '{"read":["e9100015-0000-0000-0000-000000000015"],"failed":[],
      "memory":[{"layer":"record","statement":"B4 single fact.","provenance":"stated","evidence":["e9100015-0000-0000-0000-000000000015"]}],
      "reinforce":[]}'::jsonb,
    '{"min_grounded":1,"require_record":false,"require_self":false,"require_direction":false,"max_failed_ratio":1}'::jsonb);
  if r->>'outcome' <> 'mirror_ready' then raise exception 'FAIL b4: %', r; end if;
  select state into s from agents where id='a9100000-0000-0000-0000-000000000091';
  if s <> 'at_work' then raise exception 'FAIL b4 at_work moved: %', s; end if;
end $$;

-- R · reinforcement mechanics (agent 91 has active memory from run A)
set role authenticated;
select set_config('test.uid','91919191-9191-9191-9191-919191919191', false);
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e9100021-0000-0000-0000-000000000021','a9100000-0000-0000-0000-000000000091','text','RJ live','live'),
  ('e9100022-0000-0000-0000-000000000022','a9100000-0000-0000-0000-000000000091','text','RJ pulled','pulled');
insert into jobs (id, agent_id, type) values
  ('ba910006-0000-0000-0000-000000000006','a9100000-0000-0000-0000-000000000091','synthesize');
reset role;
do $$ declare r jsonb; begin
  r := claim_synthesis_batch('ba910006-0000-0000-0000-000000000006');
end $$;
set role authenticated;
select set_config('test.uid','91919191-9191-9191-9191-919191919191', false);
update evidence_items set status='deleted' where id='e9100022-0000-0000-0000-000000000022';
reset role;
do $$
declare r jsonb; a1 uuid; a5 uuid; tns uuid; foreign_m uuid; m record; payload jsonb;
begin
  select id into a1 from memory where agent_id='a9100000-0000-0000-0000-000000000091'
    and statement='Led brand at three companies.';
  select id into a5 from memory where agent_id='a9100000-0000-0000-0000-000000000091'
    and statement='Based in Lisbon and open to hybrid.';
  select id into tns from memory where agent_id='a9100000-0000-0000-0000-000000000091'
    and status='tension' limit 1;
  select id into foreign_m from memory where agent_id='a8900000-0000-0000-0000-000000000089'
    and status='active' limit 1;

  -- P4a: foreign agent's row → reinforce_target_invalid
  begin
    perform settle_synthesis_results('ba910006-0000-0000-0000-000000000006',
      jsonb_build_object('read', jsonb_build_array('e9100021-0000-0000-0000-000000000021','e9100022-0000-0000-0000-000000000022'),
        'failed','[]'::jsonb,'memory','[]'::jsonb,
        'reinforce', jsonb_build_array(jsonb_build_object('memory', foreign_m::text,
          'evidence', jsonb_build_array('e9100021-0000-0000-0000-000000000021')))),
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
    raise exception 'FAIL p4a: foreign reinforce accepted';
  exception when others then
    if sqlerrm <> 'reinforce_target_invalid' then raise exception 'FAIL p4a got: %', sqlerrm; end if;
  end;
  -- P4b: tension row target → reinforce_target_invalid
  begin
    perform settle_synthesis_results('ba910006-0000-0000-0000-000000000006',
      jsonb_build_object('read', jsonb_build_array('e9100021-0000-0000-0000-000000000021','e9100022-0000-0000-0000-000000000022'),
        'failed','[]'::jsonb,'memory','[]'::jsonb,
        'reinforce', jsonb_build_array(jsonb_build_object('memory', tns::text,
          'evidence', jsonb_build_array('e9100021-0000-0000-0000-000000000021')))),
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
    raise exception 'FAIL p4b: tension reinforce accepted';
  exception when others then
    if sqlerrm <> 'reinforce_target_invalid' then raise exception 'FAIL p4b got: %', sqlerrm; end if;
  end;
  -- S5: duplicate reinforce target → duplicate_reinforce
  begin
    perform settle_synthesis_results('ba910006-0000-0000-0000-000000000006',
      jsonb_build_object('read', jsonb_build_array('e9100021-0000-0000-0000-000000000021','e9100022-0000-0000-0000-000000000022'),
        'failed','[]'::jsonb,'memory','[]'::jsonb,
        'reinforce', jsonb_build_array(
          jsonb_build_object('memory', a1::text, 'evidence', jsonb_build_array('e9100021-0000-0000-0000-000000000021')),
          jsonb_build_object('memory', a1::text, 'evidence', jsonb_build_array('e9100021-0000-0000-0000-000000000021')))),
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
    raise exception 'FAIL s5: duplicate reinforce accepted';
  exception when others then
    if sqlerrm <> 'duplicate_reinforce' then raise exception 'FAIL s5 got: %', sqlerrm; end if;
  end;
  -- P4c: reinforce citing uncommitted evidence → evidence_not_committed
  begin
    perform settle_synthesis_results('ba910006-0000-0000-0000-000000000006',
      jsonb_build_object('read', jsonb_build_array('e9100021-0000-0000-0000-000000000021','e9100022-0000-0000-0000-000000000022'),
        'failed','[]'::jsonb,'memory','[]'::jsonb,
        'reinforce', jsonb_build_array(jsonb_build_object('memory', a1::text,
          'evidence', jsonb_build_array('dddddddd-dddd-dddd-dddd-dddddddddddd')))),
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
    raise exception 'FAIL p4c: uncommitted reinforce citation accepted';
  exception when others then
    if sqlerrm <> 'evidence_not_committed' then raise exception 'FAIL p4c got: %', sqlerrm; end if;
  end;

  -- Success: A1 reinforced via live item; A5 reinforcement dropped (withdrawn-only)
  r := settle_synthesis_results('ba910006-0000-0000-0000-000000000006',
    jsonb_build_object('read', jsonb_build_array('e9100021-0000-0000-0000-000000000021','e9100022-0000-0000-0000-000000000022'),
      'failed','[]'::jsonb,'memory','[]'::jsonb,
      'reinforce', jsonb_build_array(
        jsonb_build_object('memory', a1::text, 'evidence', jsonb_build_array('e9100021-0000-0000-0000-000000000021')),
        jsonb_build_object('memory', a5::text, 'evidence', jsonb_build_array('e9100022-0000-0000-0000-000000000022')))),
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  if (r->>'reinforced')::int <> 1 or (r->>'reinforcements_dropped')::int <> 1 then
    raise exception 'FAIL r counts: %', r; end if;
  select * into m from memory where id = a1;
  if m.last_reinforced is null then raise exception 'FAIL r a1 not bumped'; end if;
  if jsonb_array_length(m.evidence) <> 2
     or not (m.evidence @> '[{"item":"e9100021-0000-0000-0000-000000000021"}]'::jsonb) then
    raise exception 'FAIL p3 merge: %', m.evidence; end if;
  select * into m from memory where id = a5;
  if m.last_reinforced is not null or jsonb_array_length(m.evidence) <> 1 then
    raise exception 'FAIL w3 dropped reinforcement mutated target'; end if;
end $$;

-- ============================================================
-- X — access and wrong-state refusals (agent 92)
-- ============================================================
set role authenticated;
select set_config('test.uid','92929292-9292-9292-9292-929292929292', false);
insert into jobs (id, agent_id, type) values
  ('ba920001-0000-0000-0000-000000000001','a9200000-0000-0000-0000-000000000092','synthesize'),
  ('ba920002-0000-0000-0000-000000000002','a9200000-0000-0000-0000-000000000092','compile_brief');
-- X1: authenticated client cannot execute settle
do $$ begin
  begin
    perform settle_synthesis_results('ba920001-0000-0000-0000-000000000001',
      '{"read":[],"failed":[],"memory":[],"reinforce":[]}'::jsonb,
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
    raise exception 'FAIL x1: client executed settle';
  exception when insufficient_privilege then null; end;
end $$;
reset role;
-- X3: queued (unclaimed) job → job_not_running
do $$ begin
  begin
    perform settle_synthesis_results('ba920001-0000-0000-0000-000000000001',
      '{"read":[],"failed":[],"memory":[],"reinforce":[]}'::jsonb,
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
    raise exception 'FAIL x3: queued job settled';
  exception when others then
    if sqlerrm <> 'job_not_running' then raise exception 'FAIL x3 got: %', sqlerrm; end if;
  end;
  -- X4: non-synthesize type → not_synthesize
  begin
    perform settle_synthesis_results('ba920002-0000-0000-0000-000000000002',
      '{"read":[],"failed":[],"memory":[],"reinforce":[]}'::jsonb,
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
    raise exception 'FAIL x4: compile_brief settled';
  exception when others then
    if sqlerrm <> 'not_synthesize' then raise exception 'FAIL x4 got: %', sqlerrm; end if;
  end;
  -- X5: unknown job → no_such_job
  begin
    perform settle_synthesis_results('dddddddd-dddd-dddd-dddd-dddddddddddd',
      '{"read":[],"failed":[],"memory":[],"reinforce":[]}'::jsonb,
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
    raise exception 'FAIL x5: unknown job settled';
  exception when others then
    if sqlerrm <> 'no_such_job' then raise exception 'FAIL x5 got: %', sqlerrm; end if;
  end;
end $$;

select 'M008 OK: strict shape (policy+results+corr3/4), withdrawal wins incl. W6/W7 ordering, atomic rollback, tension+reinforce provenance held by the door, forced authority columns, closed failure taxonomy byte-exact, settle closes through finalize (guard live, sweep 0, at_work never moves, mirror_ready/commissioning flips correct), sufficiency boundaries incl. strict ratio and grounded-direction gate, access denied to clients';
