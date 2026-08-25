-- Verification suite for migration 010 (memory handles).
-- Groups: K column/CHECK · P settle passthrough (incl. fail-soft + whitelist
-- regression) · C confirm carry-over · R retract untouched · B handle-blind
-- norms (suppression + duplicate remain statement-based) · G regression.
-- Client calls simulate production: auth.uid() reads test.uid via the
-- harness stub. Fixtures use agents 70-71 (no collision with 60-68 of 009).

\set ON_ERROR_STOP on

grant usage on schema public, auth, storage to authenticated, anon, service_role;
grant execute on function auth.uid() to authenticated, anon;

-- ---- fixtures ----
delete from agents where agent_no between 70 and 71;
insert into auth.users (id, email) values
  ('70707070-7070-4070-8070-707070707070','h70@example.com'),
  ('71717171-7171-4171-8171-717171717171','h71@example.com')
on conflict do nothing;
insert into agents (id, user_id, agent_no, state) values
  ('a7070000-0000-4000-8000-000000000070','70707070-7070-4070-8070-707070707070',70,'at_work'),
  ('a7171000-0000-4000-8000-000000000071','71717171-7171-4171-8171-717171717171',71,'at_work');

insert into evidence_items (id, agent_id, kind, label, body) values
  ('e7070001-0000-4000-8000-000000000001','a7070000-0000-4000-8000-000000000070','text','H seed A','ha'),
  ('e7070002-0000-4000-8000-000000000002','a7070000-0000-4000-8000-000000000070','text','H seed B','hb');
insert into jobs (id, agent_id, type) values
  ('b7070001-0000-4000-8000-000000000001','a7070000-0000-4000-8000-000000000070','synthesize');

-- ---------------------------------------------------------------------------
-- P1 · Settle passthrough: five statements exercising every handle variant.
--   s1 valid handle · s2 absent -> NULL · s3 oversized(41) -> NULL (degrade,
--   settle still succeeds) · s4 non-string -> NULL · s5 whitespace -> NULL.
-- ---------------------------------------------------------------------------
do $$ declare r jsonb; begin
  r := claim_synthesis_batch('b7070001-0000-4000-8000-000000000001');
  r := settle_synthesis_results('b7070001-0000-4000-8000-000000000001',
    '{"read":["e7070001-0000-4000-8000-000000000001","e7070002-0000-4000-8000-000000000002"],"failed":[],
      "memory":[
        {"layer":"record","statement":"H record one.","provenance":"stated","evidence":["e7070001-0000-4000-8000-000000000001"],"handle":"Airbnb now"},
        {"layer":"record","statement":"H record two.","provenance":"stated","evidence":["e7070001-0000-4000-8000-000000000001"]},
        {"layer":"record","statement":"H record three.","provenance":"extracted","evidence":["e7070002-0000-4000-8000-000000000002"],"handle":"AAAAAAAAAABBBBBBBBBBCCCCCCCCCCDDDDDDDDDDX"},
        {"layer":"self","statement":"H self one.","provenance":"stated","evidence":["e7070002-0000-4000-8000-000000000002"],"handle":12345},
        {"layer":"self","statement":"H direction.","provenance":"stated","evidence":["e7070002-0000-4000-8000-000000000002"],"is_direction":true,"handle":"   "}
      ],"reinforce":[]}'::jsonb,
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  if r->>'outcome' <> 'mirror_ready' then raise exception 'FAIL P1 settle: %', r; end if;
end $$;

do $$ begin
  if (select handle from memory where agent_id='a7070000-0000-4000-8000-000000000070'
        and statement='H record one.') is distinct from 'Airbnb now' then
    raise exception 'FAIL P1a: valid handle not stored'; end if;
  if (select handle from memory where agent_id='a7070000-0000-4000-8000-000000000070'
        and statement='H record two.') is not null then
    raise exception 'FAIL P1b: absent handle must be NULL'; end if;
  if (select handle from memory where agent_id='a7070000-0000-4000-8000-000000000070'
        and statement='H record three.') is not null then
    raise exception 'FAIL P1c: oversized handle must degrade to NULL'; end if;
  if (select handle from memory where agent_id='a7070000-0000-4000-8000-000000000070'
        and statement='H self one.') is not null then
    raise exception 'FAIL P1d: non-string handle must degrade to NULL'; end if;
  if (select handle from memory where agent_id='a7070000-0000-4000-8000-000000000070'
        and statement='H direction.') is not null then
    raise exception 'FAIL P1e: whitespace handle must degrade to NULL'; end if;
  raise notice 'PASS P1: settle passthrough + fail-soft (5/5)';
end $$;

-- ---------------------------------------------------------------------------
-- P2 · Whitelist regression: an UNKNOWN key still raises invalid_results.
-- ---------------------------------------------------------------------------
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e7070003-0000-4000-8000-000000000003','a7070000-0000-4000-8000-000000000070','text','H seed C','hc');
insert into jobs (id, agent_id, type) values
  ('b7070002-0000-4000-8000-000000000002','a7070000-0000-4000-8000-000000000070','synthesize');
do $$ declare r jsonb; ok boolean := false; begin
  r := claim_synthesis_batch('b7070002-0000-4000-8000-000000000002');
  begin
    r := settle_synthesis_results('b7070002-0000-4000-8000-000000000002',
      '{"read":["e7070003-0000-4000-8000-000000000003"],"failed":[],
        "memory":[{"layer":"record","statement":"H bad key.","provenance":"stated",
                   "evidence":["e7070003-0000-4000-8000-000000000003"],"handles":"typo"}],
        "reinforce":[]}'::jsonb,
      '{"min_grounded":1,"require_record":false,"require_self":false,"require_direction":false,"max_failed_ratio":0.5}'::jsonb);
  exception when others then
    if sqlerrm like '%invalid_results%' then ok := true;
    else raise exception 'FAIL P2: wrong error %', sqlerrm; end if;
  end;
  if not ok then raise exception 'FAIL P2: unknown key accepted'; end if;
  perform finalize_synthesis('b7070002-0000-4000-8000-000000000002','failed','test-cleanup');
  raise notice 'PASS P2: unknown keys still refused';
end $$;

-- ---------------------------------------------------------------------------
-- K1 · CHECK ceiling: direct write above 40 chars must violate the constraint
--      (service-side; the doors can never produce this by construction).
-- ---------------------------------------------------------------------------
do $$ declare ok boolean := false; begin
  begin
    update memory set handle = repeat('x', 41)
      where agent_id='a7070000-0000-4000-8000-000000000070' and statement='H record one.';
  exception when others then ok := true; end;
  if not ok then raise exception 'FAIL K1: 41-char handle accepted'; end if;
  if (select handle from memory where agent_id='a7070000-0000-4000-8000-000000000070'
        and statement='H record one.') <> 'Airbnb now' then
    raise exception 'FAIL K1b: handle mutated after failed update'; end if;
  update memory set handle = repeat('y', 40)
    where agent_id='a7070000-0000-4000-8000-000000000070' and statement='H record two.';
  update memory set handle = null
    where agent_id='a7070000-0000-4000-8000-000000000070' and statement='H record two.';
  raise notice 'PASS K1: CHECK ceiling enforced (41 refused, 40 + NULL fine)';
end $$;

-- ---------------------------------------------------------------------------
-- C1 · Confirm carry-over: successor inherits the handle; original keeps it.
-- ---------------------------------------------------------------------------
select set_config('test.uid','70707070-7070-4070-8070-707070707070', false);
do $$ declare rid uuid; r jsonb; s memory%rowtype; o memory%rowtype; begin
  select id into rid from memory
    where agent_id='a7070000-0000-4000-8000-000000000070' and statement='H record one.';
  r := confirm_memory(array[rid]);
  if (r->>'count')::int <> 1 then raise exception 'FAIL C1: count %', r; end if;
  select * into s from memory where supersedes = rid;
  if s.id is null then raise exception 'FAIL C1: no successor'; end if;
  if s.handle is distinct from 'Airbnb now' then
    raise exception 'FAIL C1a: successor handle % (want Airbnb now)', coalesce(s.handle,'NULL'); end if;
  if s.provenance <> 'confirmed' or s.status <> 'active'
     or s.can_affect_search or s.can_appear_publicly then
    raise exception 'FAIL C1b: successor contract broken'; end if;
  select * into o from memory where id = rid;
  if o.status <> 'superseded' or o.handle is distinct from 'Airbnb now' then
    raise exception 'FAIL C1c: original altered beyond status'; end if;
  raise notice 'PASS C1: confirm carries handle; original intact';
end $$;

-- C2 · Confirm of a NULL-handle row: successor handle stays NULL (regression).
do $$ declare rid uuid; r jsonb; s memory%rowtype; begin
  select id into rid from memory
    where agent_id='a7070000-0000-4000-8000-000000000070' and statement='H record two.'
      and status='active';
  r := confirm_memory(array[rid]);
  select * into s from memory where supersedes = rid;
  if s.handle is not null then raise exception 'FAIL C2: NULL handle not preserved'; end if;
  raise notice 'PASS C2: NULL handle rides supersession unchanged';
end $$;

-- ---------------------------------------------------------------------------
-- R1 · Retract untouched: status flips, handle and row survive, no inserts.
-- ---------------------------------------------------------------------------
do $$ declare rid uuid; r jsonb; n0 int; n1 int; h text; begin
  update memory set handle = 'Direction' where agent_id='a7070000-0000-4000-8000-000000000070'
    and statement='H direction.';   -- service-side arrangement for the test
  select id into rid from memory
    where agent_id='a7070000-0000-4000-8000-000000000070' and statement='H direction.';
  select count(*) into n0 from memory where agent_id='a7070000-0000-4000-8000-000000000070';
  r := retract_memory(array[rid]);
  select count(*) into n1 from memory where agent_id='a7070000-0000-4000-8000-000000000070';
  if n1 <> n0 then raise exception 'FAIL R1: rowcount changed'; end if;
  select handle into h from memory where id = rid;
  if h is distinct from 'Direction' then raise exception 'FAIL R1b: handle changed on retract'; end if;
  if (select status from memory where id = rid) <> 'retracted' then
    raise exception 'FAIL R1c: not retracted'; end if;
  raise notice 'PASS R1: retract is a pure status transition; handle intact';
end $$;

-- ---------------------------------------------------------------------------
-- B1 · Suppression stays STATEMENT-based: same statement + different handle
--      is still suppressed (handle plays no part in the norm).
-- ---------------------------------------------------------------------------
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e7070004-0000-4000-8000-000000000004','a7070000-0000-4000-8000-000000000070','text','H seed D','hd');
insert into jobs (id, agent_id, type) values
  ('b7070003-0000-4000-8000-000000000003','a7070000-0000-4000-8000-000000000070','synthesize');
do $$ declare r jsonb; begin
  r := claim_synthesis_batch('b7070003-0000-4000-8000-000000000003');
  r := settle_synthesis_results('b7070003-0000-4000-8000-000000000003',
    '{"read":["e7070004-0000-4000-8000-000000000004"],"failed":[],
      "memory":[{"layer":"self","statement":"H direction.","provenance":"stated",
                 "evidence":["e7070004-0000-4000-8000-000000000004"],"handle":"Different name"}],
      "reinforce":[]}'::jsonb,
    '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  if coalesce((r->>'suppressed_retracted')::int, 0) < 1 then
    raise exception 'FAIL B1: retracted statement not suppressed: %', r; end if;
  if exists (select 1 from memory where agent_id='a7070000-0000-4000-8000-000000000070'
               and statement='H direction.' and status='active') then
    raise exception 'FAIL B1b: suppressed statement was inserted'; end if;
  raise notice 'PASS B1: suppression guard is handle-blind';
end $$;

-- ---------------------------------------------------------------------------
-- B2 · Duplicate norm stays STATEMENT-based: same statement as an ACTIVE row
--      with a different handle -> reinforce_required (not a new row).
-- ---------------------------------------------------------------------------
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e7070005-0000-4000-8000-000000000005','a7070000-0000-4000-8000-000000000070','text','H seed E','he');
insert into jobs (id, agent_id, type) values
  ('b7070004-0000-4000-8000-000000000004','a7070000-0000-4000-8000-000000000070','synthesize');
do $$ declare r jsonb; ok boolean := false; begin
  r := claim_synthesis_batch('b7070004-0000-4000-8000-000000000004');
  begin
    r := settle_synthesis_results('b7070004-0000-4000-8000-000000000004',
      '{"read":["e7070005-0000-4000-8000-000000000005"],"failed":[],
        "memory":[{"layer":"record","statement":"H record three.","provenance":"stated",
                   "evidence":["e7070005-0000-4000-8000-000000000005"],"handle":"New name"}],
        "reinforce":[]}'::jsonb,
      '{"min_grounded":5,"require_record":true,"require_self":true,"require_direction":true,"max_failed_ratio":0.5}'::jsonb);
  exception when others then
    if sqlerrm like '%reinforce_required%' then ok := true;
    else raise exception 'FAIL B2: wrong error %', sqlerrm; end if;
  end;
  if not ok then raise exception 'FAIL B2: duplicate-of-active accepted as new row'; end if;
  perform finalize_synthesis('b7070004-0000-4000-8000-000000000004','failed','test-cleanup');
  raise notice 'PASS B2: duplicate norm is handle-blind';
end $$;

-- ---------------------------------------------------------------------------
-- G1 · Regression sweep: flags all false; no unexpected rows; statuses sane.
-- ---------------------------------------------------------------------------
do $$ begin
  if exists (select 1 from memory where agent_id='a7070000-0000-4000-8000-000000000070'
               and (can_affect_search or can_appear_publicly)) then
    raise exception 'FAIL G1: operational flag set'; end if;
  if (select count(*) from memory where agent_id='a7070000-0000-4000-8000-000000000070'
        and status='active') <> 4 then
    -- 5 seeded - 1 retracted = 4 active lineage heads (2 of them confirmed successors)
    raise exception 'FAIL G1b: active count %', (select count(*) from memory
      where agent_id='a7070000-0000-4000-8000-000000000070' and status='active'); end if;
  raise notice 'PASS G1: flags false; row lineage as contracted';
end $$;

-- ---- cleanup ----
delete from agents where agent_no between 70 and 71;
do $$ begin raise notice 'MIGRATION 010 BATTERY COMPLETE'; end $$;
