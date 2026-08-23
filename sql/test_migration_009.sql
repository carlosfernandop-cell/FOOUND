-- Verification suite for migration 009 (Mirror doors + suppression guard).
-- Groups: C confirm · A atomicity · N negatives · R retract · U authority ·
-- S suppression (incl. chain) · G regression. Cross-migration confirmed-
-- direction sufficiency (correction 2) is proven in pytest R28.
-- Client calls simulate production: auth.uid() reads test.uid via the
-- harness stub; role switches prove the grant boundary.

\set ON_ERROR_STOP on

grant usage on schema public, auth, storage to authenticated, anon, service_role;
grant execute on function auth.uid() to authenticated, anon;

-- ---- fixtures ----
delete from agents where agent_no between 60 and 68;
insert into auth.users (id, email) values
  ('60606060-6060-4060-8060-606060606060','m60@example.com'),
  ('61616161-6161-4161-8161-616161616161','m61@example.com'),
  ('62626262-6262-4262-8262-626262626262','m62@example.com')
on conflict do nothing;
insert into agents (id, user_id, agent_no, state) values
  ('a6060000-0000-4000-8000-000000000060','60606060-6060-4060-8060-606060606060',60,'at_work'),
  ('a6161000-0000-4000-8000-000000000061','61616161-6161-4161-8161-616161616161',61,'at_work'),
  ('a6262000-0000-4000-8000-000000000062','62626262-6262-4262-8262-626262626262',62,'at_work');

-- Seed agent 60: five active rows via the real doors (claim + settle)
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e6060001-0000-4000-8000-000000000001','a6060000-0000-4000-8000-000000000060','text','Seed A','sa'),
  ('e6060002-0000-4000-8000-000000000002','a6060000-0000-4000-8000-000000000060','text','Seed B','sb');
insert into jobs (id, agent_id, type) values
  ('b6060001-0000-4000-8000-000000000001','a6060000-0000-4000-8000-000000000060','synthesize');
do $$ declare r jsonb; begin
  r := claim_synthesis_batch('b6060001-0000-4000-8000-000000000001');
  r := settle_synthesis_results('b6060001-0000-4000-8000-000000000001',
    '{"read":["e6060001-0000-4000-8000-000000000001","e6060002-0000-4000-8000-000000000002"],"failed":[],
      "memory":[
        {"layer":"record","statement":"M seed record one.","provenance":"stated","evidence":["e6060001-0000-4000-8000-000000000001"]},
        {"layer":"record","statement":"M seed record two.","provenance":"stated","evidence":["e6060001-0000-4000-8000-000000000001"]},
        {"layer":"self","statement":"M seed self one.","provenance":"stated","evidence":["e6060002-0000-4000-8000-000000000002"]},
        {"layer":"self","statement":"M seed direction.","provenance":"stated","evidence":["e6060002-0000-4000-8000-000000000002"],"is_direction":true},
        {"layer":"record","statement":"M seed record three.","provenance":"extracted","evidence":["e6060002-0000-4000-8000-000000000002"]}
      ],"reinforce":[]}'::jsonb,
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  if r->>'outcome' <> 'mirror_ready' then raise exception 'FAIL seed60: %', r; end if;
end $$;

-- Seed agent 62: one active row + one tension row
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e6262001-0000-4000-8000-000000000001','a6262000-0000-4000-8000-000000000062','text','T seed','ts');
insert into jobs (id, agent_id, type) values
  ('b6262001-0000-4000-8000-000000000001','a6262000-0000-4000-8000-000000000062','synthesize');
do $$ declare r jsonb; begin
  r := claim_synthesis_batch('b6262001-0000-4000-8000-000000000001');
  r := settle_synthesis_results('b6262001-0000-4000-8000-000000000001',
    '{"read":["e6262001-0000-4000-8000-000000000001"],"failed":[],
      "memory":[
        {"layer":"record","statement":"T grounded row.","provenance":"stated","evidence":["e6262001-0000-4000-8000-000000000001"]},
        {"layer":"model","statement":"Tension: T reading A / T reading B.","provenance":"extracted","evidence":["e6262001-0000-4000-8000-000000000001"],"tension":true}
      ],"reinforce":[]}'::jsonb,
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
end $$;

select set_config('test.uid','60606060-6060-4060-8060-606060606060', false);

-- ============================================================
-- C · confirm happy path: supersession, history intact, flags forced
-- ============================================================
do $$
declare r1 uuid; r2 uuid; res jsonb; c1 memory%rowtype; o1 memory%rowtype; n int;
begin
  select id into r1 from memory where agent_id='a6060000-0000-4000-8000-000000000060'
    and statement='M seed record one.';
  select id into r2 from memory where agent_id='a6060000-0000-4000-8000-000000000060'
    and statement='M seed self one.';
  res := confirm_memory(array[r1, r2]);
  if (res->>'count')::int <> 2 then raise exception 'FAIL c1 count: %', res; end if;
  select * into o1 from memory where id = r1;
  if o1.status <> 'superseded' or o1.provenance <> 'stated' then
    raise exception 'FAIL c1 original mutated: % %', o1.status, o1.provenance; end if;
  select * into c1 from memory where supersedes = r1;
  if c1.id is null then raise exception 'FAIL c1 no successor'; end if;
  if c1.provenance <> 'confirmed' or c1.status <> 'active'
     or c1.statement <> o1.statement or c1.evidence <> o1.evidence
     or c1.source <> o1.source or c1.layer <> o1.layer then
    raise exception 'FAIL c1 successor shape'; end if;
  if c1.can_affect_search or c1.can_appear_publicly
     or c1.expires is not null then
    raise exception 'FAIL c1 authority columns'; end if;
  select count(*) into n from memory
    where agent_id='a6060000-0000-4000-8000-000000000060' and status='active';
  if n <> 5 then raise exception 'FAIL c1 active count: %', n; end if;  -- 3 + 2 successors
end $$;

-- ============================================================
-- A · atomicity: one bad target -> zero partial verdicts
-- ============================================================
do $$
declare r1 uuid; r3 uuid; before_n int; after_n int; s text;
begin
  select id into r1 from memory where statement='M seed record one.'
    and status='superseded';                       -- the ORIGINAL, not its confirmed successor
  select id into r3 from memory where statement='M seed record two.' and status='active';
  select count(*) into before_n from memory;
  begin
    perform confirm_memory(array[r3, r1]);   -- r3 valid, r1 not active
    raise exception 'FAIL a1: accepted superseded target';
  exception when others then
    if sqlerrm <> 'memory_not_active' then raise exception 'FAIL a1 got: %', sqlerrm; end if;
  end;
  select count(*) into after_n from memory;
  if after_n <> before_n then raise exception 'FAIL a1 partial insert'; end if;
  select status into s from memory where id = r3;
  if s <> 'active' then raise exception 'FAIL a1 partial flip: %', s; end if;
  select provenance into s from memory where id = r3;
  if s <> 'stated' then raise exception 'FAIL a1 provenance moved: %', s; end if;
end $$;

-- ============================================================
-- N · named negatives, both doors
-- ============================================================
do $$
declare r3 uuid; c1 uuid; t1 uuid; big uuid[];
begin
  select id into r3 from memory where statement='M seed record two.' and status='active';
  select id into c1 from memory where provenance='confirmed' and statement='M seed record one.';
  select id into t1 from memory where status='tension'
    and agent_id='a6262000-0000-4000-8000-000000000062';

  begin perform confirm_memory(null);            raise exception 'FAIL n1';
  exception when others then if sqlerrm <> 'empty_input' then raise exception 'FAIL n1 got %', sqlerrm; end if; end;
  begin perform confirm_memory('{}'::uuid[]);    raise exception 'FAIL n2';
  exception when others then if sqlerrm <> 'empty_input' then raise exception 'FAIL n2 got %', sqlerrm; end if; end;
  select array_agg(gen_random_uuid()) into big from generate_series(1, 201);
  begin perform confirm_memory(big);             raise exception 'FAIL n3';
  exception when others then if sqlerrm <> 'too_many_targets' then raise exception 'FAIL n3 got %', sqlerrm; end if; end;
  begin perform confirm_memory(array['dddddddd-dddd-4ddd-8ddd-dddddddddddd']::uuid[]); raise exception 'FAIL n4';
  exception when others then if sqlerrm <> 'no_such_memory' then raise exception 'FAIL n4 got %', sqlerrm; end if; end;
  begin perform confirm_memory(array[r3, r3]);   raise exception 'FAIL n5';
  exception when others then if sqlerrm <> 'duplicate_target' then raise exception 'FAIL n5 got %', sqlerrm; end if; end;
  begin perform confirm_memory(array[c1]);       raise exception 'FAIL n6';
  exception when others then if sqlerrm <> 'already_confirmed' then raise exception 'FAIL n6 got %', sqlerrm; end if; end;
  -- t1 belongs to agent 62 — act as its owner, or ownership refuses first
  perform set_config('test.uid','62626262-6262-4262-8262-626262626262', false);
  begin perform confirm_memory(array[t1]);       raise exception 'FAIL n7';
  exception when others then if sqlerrm <> 'tension_not_actionable' then raise exception 'FAIL n7 got %', sqlerrm; end if; end;
  begin perform retract_memory(array[t1]);       raise exception 'FAIL n8';
  exception when others then if sqlerrm <> 'tension_not_actionable' then raise exception 'FAIL n8 got %', sqlerrm; end if; end;
  perform set_config('test.uid','60606060-6060-4060-8060-606060606060', false);

  -- ownership: another user's uid cannot act on agent 60's rows
  perform set_config('test.uid','61616161-6161-4161-8161-616161616161', false);
  begin perform confirm_memory(array[r3]);       raise exception 'FAIL n9';
  exception when others then if sqlerrm <> 'memory_not_owned' then raise exception 'FAIL n9 got %', sqlerrm; end if; end;
  begin perform retract_memory(array[r3]);       raise exception 'FAIL n10';
  exception when others then if sqlerrm <> 'memory_not_owned' then raise exception 'FAIL n10 got %', sqlerrm; end if; end;
  perform set_config('test.uid','60606060-6060-4060-8060-606060606060', false);
end $$;

-- ============================================================
-- R · retract: active and confirmed rows; preserved verbatim
-- ============================================================
do $$
declare r3 uuid; c1 uuid; res jsonb; m memory%rowtype;
begin
  select id into r3 from memory where statement='M seed record two.' and status='active';
  select id into c1 from memory where provenance='confirmed' and statement='M seed record one.';
  res := retract_memory(array[r3]);
  if (res->>'count')::int <> 1 then raise exception 'FAIL r1: %', res; end if;
  select * into m from memory where id = r3;
  if m.status <> 'retracted' or m.statement <> 'M seed record two.'
     or m.provenance <> 'stated' then raise exception 'FAIL r1 not preserved'; end if;
  res := retract_memory(array[c1]);              -- confirmed rows retractable
  if (res->>'count')::int <> 1 then raise exception 'FAIL r2: %', res; end if;
  select * into m from memory where id = c1;
  if m.status <> 'retracted' or m.provenance <> 'confirmed' then
    raise exception 'FAIL r2 confirmed retract'; end if;
  -- already retracted -> memory_not_active
  begin perform retract_memory(array[r3]); raise exception 'FAIL r3';
  exception when others then if sqlerrm <> 'memory_not_active' then raise exception 'FAIL r3 got %', sqlerrm; end if; end;
end $$;

-- ============================================================
-- U · authority: doors touch nothing operational; grants hold
-- ============================================================
do $$
declare jobs_snap text; agents_snap text; n int;
begin
  select string_agg(id::text || status, ',' order by id) into jobs_snap from jobs;
  select string_agg(id::text || state, ',' order by id) into agents_snap from agents;
  perform set_config('test.uid','62626262-6262-4262-8262-626262626262', false);
  perform confirm_memory((select array_agg(id) from memory
    where agent_id='a6262000-0000-4000-8000-000000000062' and status='active'));
  perform set_config('test.uid','60606060-6060-4060-8060-606060606060', false);
  if jobs_snap is distinct from (select string_agg(id::text || status, ',' order by id) from jobs)
  then raise exception 'FAIL u1 jobs moved'; end if;
  if agents_snap is distinct from (select string_agg(id::text || state, ',' order by id) from agents)
  then raise exception 'FAIL u1 agents moved'; end if;
  -- scope to this battery's agents: earlier suites leave unrelated fixtures
  select count(*) into n from memory
    where (can_affect_search or can_appear_publicly)
      and agent_id in ('a6060000-0000-4000-8000-000000000060',
                       'a6161000-0000-4000-8000-000000000061',
                       'a6262000-0000-4000-8000-000000000062');
  if n <> 0 then raise exception 'FAIL u1 flags: %', n; end if;
end $$;
set role anon;
do $$ begin
  begin
    perform confirm_memory(array['dddddddd-dddd-4ddd-8ddd-dddddddddddd']::uuid[]);
    raise exception 'FAIL u2: anon executed confirm';
  exception when insufficient_privilege then null; end;
  begin
    perform retract_memory(array['dddddddd-dddd-4ddd-8ddd-dddddddddddd']::uuid[]);
    raise exception 'FAIL u3: anon executed retract';
  exception when insufficient_privilege then null; end;
end $$;
reset role;

-- ============================================================
-- S · suppression guard: retracted (incl. retracted-confirmed chain) text
-- can never re-insert; distinct statements unaffected
-- ============================================================
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e6060003-0000-4000-8000-000000000003','a6060000-0000-4000-8000-000000000060','text','Seed C','sc');
insert into jobs (id, agent_id, type) values
  ('b6060002-0000-4000-8000-000000000002','a6060000-0000-4000-8000-000000000060','synthesize');
do $$ declare r jsonb; n int; begin
  r := claim_synthesis_batch('b6060002-0000-4000-8000-000000000002');
  r := settle_synthesis_results('b6060002-0000-4000-8000-000000000002',
    '{"read":["e6060003-0000-4000-8000-000000000003"],"failed":[],
      "memory":[
        {"layer":"record","statement":"  m SEED record two. ","provenance":"stated","evidence":["e6060003-0000-4000-8000-000000000003"]},
        {"layer":"record","statement":"M seed RECORD one.","provenance":"stated","evidence":["e6060003-0000-4000-8000-000000000003"]},
        {"layer":"record","statement":"A brand new surviving fact.","provenance":"stated","evidence":["e6060003-0000-4000-8000-000000000003"]}
      ],"reinforce":[]}'::jsonb,
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  -- row 1: variant of retracted r3 -> suppressed
  -- row 2: variant of retracted-CONFIRMED c1 (chain) -> suppressed
  -- row 3: distinct -> inserts
  if (r->>'suppressed_retracted')::int <> 2 then raise exception 'FAIL s1 suppressed: %', r; end if;
  if (r->>'memory_inserted')::int <> 1 then raise exception 'FAIL s1 inserted: %', r; end if;
  select count(*) into n from memory
    where agent_id='a6060000-0000-4000-8000-000000000060'
      and btrim(lower(regexp_replace(statement,'\s+',' ','g'))) = 'm seed record two.'
      and status = 'active';
  if n <> 0 then raise exception 'FAIL s1 resurrection: %', n; end if;
  select count(*) into n from memory where statement='A brand new surviving fact.' and status='active';
  if n <> 1 then raise exception 'FAIL s1 survivor: %', n; end if;
end $$;

-- ============================================================
-- G · regression: settle still refuses authenticated callers
-- ============================================================
set role authenticated;
do $$ begin
  begin
    perform settle_synthesis_results('b6060002-0000-4000-8000-000000000002',
      '{"read":[],"failed":[],"memory":[],"reinforce":[]}'::jsonb,
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
    raise exception 'FAIL g1: client executed settle';
  exception when insufficient_privilege then null; end;
end $$;
reset role;

select 'M009 OK: confirm-by-supersession (history intact, flags forced, trigger re-verified), two-pass atomic bulk verdicts (zero partial), full named-negative set incl. ownership + tension_not_actionable on both doors, confirmed rows retractable + preserved verbatim, doors touch no jobs/agents/flags, anon refused, suppression guard blocks retracted AND retracted-confirmed-chain text with zero side effects, settle client-refusal regression green';
