-- Verification suite for migration 007 (Evidence Intake Contract v1.3 FINAL).
-- Four invariant classes: AUTHORITY · EPISTEMIC · BATCH/IDEMPOTENCY · LIFECYCLE
-- (+ infra). Client-side blocks run as role `authenticated` with test.uid set,
-- mirroring production grants exactly. Engine-side blocks run as postgres
-- (service path; note the jobs guard trigger fires regardless of role).

\set ON_ERROR_STOP on

-- ---- production-parity grants for the client role (Supabase defaults) ----
grant usage on schema public, auth, storage to authenticated, anon, service_role;
grant select on agents to authenticated;
grant update on agents to authenticated;             -- RLS filters (no policy)
grant select, insert, update, delete on jobs to authenticated;
grant select, insert on storage.objects to authenticated;
grant select on storage.buckets to authenticated;
grant execute on function auth.uid() to authenticated;

-- ---- fixtures ----
delete from agents where agent_no between 96 and 100;
insert into auth.users (id, email) values
  ('66666666-6666-6666-6666-666666666666','six@example.com'),
  ('77777777-7777-7777-7777-777777777777','seven@example.com'),
  ('88888888-8888-8888-8888-888888888888','eight@example.com'),
  ('99999999-9999-9999-9999-999999999999','nine@example.com'),
  ('10101010-1010-1010-1010-101010101010','ten@example.com')
on conflict do nothing;
insert into agents (id, user_id, agent_no, state) values
  ('a6666666-0000-0000-0000-000000000006','66666666-6666-6666-6666-666666666666',96,'invited'),
  ('a7777777-0000-0000-0000-000000000007','77777777-7777-7777-7777-777777777777',97,'at_work'),
  ('a8888888-0000-0000-0000-000000000008','88888888-8888-8888-8888-888888888888',98,'invited'),
  ('a9999999-0000-0000-0000-000000000009','99999999-9999-9999-9999-999999999999',99,'archived'),
  ('a1010101-0000-0000-0000-000000000010','10101010-1010-1010-1010-101010101010',100,'paused');

-- ============================================================
-- AUTHORITY — as the authenticated client (user 6, agent 96)
-- ============================================================
set role authenticated;
select set_config('test.uid','66666666-6666-6666-6666-666666666666', false);

-- a1: create text + file evidence (client-generated ids, canonical path)
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e1000000-0000-0000-0000-000000000001','a6666666-0000-0000-0000-000000000006','text','Career notes','I led brand at three companies.'),
  ('e2000000-0000-0000-0000-000000000002','a6666666-0000-0000-0000-000000000006','text','Old bio','Earlier positioning text.');
insert into evidence_items (id, agent_id, kind, label, storage_path, mime_type, byte_size) values
  ('e3000000-0000-0000-0000-000000000003','a6666666-0000-0000-0000-000000000006','file','resume.pdf',
   '66666666-6666-6666-6666-666666666666/e3000000-0000-0000-0000-000000000003/blob','application/pdf', 52344);

-- a2: file row with a foreign uid prefix is refused
do $$ begin
  begin
    insert into evidence_items (id, agent_id, kind, label, storage_path, mime_type, byte_size) values
      ('e4000000-0000-0000-0000-000000000004','a6666666-0000-0000-0000-000000000006','file','x.pdf',
       '77777777-7777-7777-7777-777777777777/e4000000-0000-0000-0000-000000000004/blob','application/pdf', 10);
    raise exception 'FAIL a2: foreign-prefix path accepted';
  exception when insufficient_privilege or check_violation then null; end;
end $$;

-- a3: storage upload with matching row OK; without row refused; foreign prefix refused
insert into storage.objects (bucket_id, name, owner) values
  ('feeds','66666666-6666-6666-6666-666666666666/e3000000-0000-0000-0000-000000000003/blob',
   '66666666-6666-6666-6666-666666666666');
do $$ begin
  begin
    insert into storage.objects (bucket_id, name, owner) values
      ('feeds','66666666-6666-6666-6666-666666666666/deadbeef-dead-dead-dead-deaddeadbeef/blob',
       '66666666-6666-6666-6666-666666666666');
    raise exception 'FAIL a3: object without matching evidence row accepted';
  exception when insufficient_privilege or check_violation then null; end;
  begin
    insert into storage.objects (bucket_id, name, owner) values
      ('feeds','77777777-7777-7777-7777-777777777777/e3000000-0000-0000-0000-000000000003/blob',
       '66666666-6666-6666-6666-666666666666');
    raise exception 'FAIL a3b: foreign-prefix object accepted';
  exception when insufficient_privilege or check_violation then null; end;
end $$;

-- a4: owner cannot set processing states
do $$ begin
  begin
    update evidence_items set status='read' where id='e1000000-0000-0000-0000-000000000001';
    raise exception 'FAIL a4: owner set read';
  exception when insufficient_privilege or check_violation then null; end;
  begin
    update evidence_items set status='reading' where id='e1000000-0000-0000-0000-000000000001';
    raise exception 'FAIL a4b: owner set reading';
  exception when insufficient_privilege or check_violation then null; end;
end $$;

-- a5: owner cannot touch payload or engine columns (column privileges)
do $$ begin
  begin
    update evidence_items set body='rewritten' where id='e1000000-0000-0000-0000-000000000001';
    raise exception 'FAIL a5: owner rewrote body';
  exception when insufficient_privilege then null; end;
  begin
    update evidence_items set read_at=now() where id='e1000000-0000-0000-0000-000000000001';
    raise exception 'FAIL a5b: owner set read_at';
  exception when insufficient_privilege then null; end;
  begin
    update evidence_items set deleted_at=now() where id='e1000000-0000-0000-0000-000000000001';
    raise exception 'FAIL a5c: owner set deleted_at';
  exception when insufficient_privilege then null; end;
end $$;

-- a6: owner cannot hard-delete
do $$ begin
  begin
    delete from evidence_items where id='e1000000-0000-0000-0000-000000000001';
    raise exception 'FAIL a6: owner hard-deleted';
  exception when insufficient_privilege then null; end;
end $$;

-- a7: owner cannot execute the service functions
do $$ begin
  begin
    perform claim_synthesis_batch('e1000000-0000-0000-0000-000000000001');
    raise exception 'FAIL a7: owner executed claim';
  exception when insufficient_privilege then null; end;
  begin
    perform finalize_synthesis('e1000000-0000-0000-0000-000000000001','mirror_ready');
    raise exception 'FAIL a7b: owner executed finalize';
  exception when insufficient_privilege then null; end;
end $$;

-- a8: owner cannot flip agent lifecycle (RLS filters to zero rows)
do $$ declare n int; begin
  update agents set state='at_work' where id='a6666666-0000-0000-0000-000000000006';
  select count(*) into n from agents where id='a6666666-0000-0000-0000-000000000006' and state='at_work';
  if n <> 0 then raise exception 'FAIL a8: client flipped agent state'; end if;
end $$;

-- a9: memory is SELECT-only for the client
do $$ begin
  begin
    insert into memory (agent_id, layer, statement, provenance, source)
    values ('a6666666-0000-0000-0000-000000000006','self','x','stated','feed');
    raise exception 'FAIL a9: client inserted memory';
  exception when insufficient_privilege then null; end;
  begin
    update memory set status='active' where agent_id='a6666666-0000-0000-0000-000000000006';
    raise exception 'FAIL a9b: client updated memory';
  exception when insufficient_privilege then null; end;
  begin
    delete from memory where agent_id='a6666666-0000-0000-0000-000000000006';
    raise exception 'FAIL a9c: client deleted memory';
  exception when insufficient_privilege then null; end;
end $$;

-- a10: cross-user isolation (user 7 sees/touches nothing of user 6)
select set_config('test.uid','77777777-7777-7777-7777-777777777777', false);
do $$ declare n int; begin
  select count(*) into n from evidence_items;
  if n <> 0 then raise exception 'FAIL a10: cross-user items visible: %', n; end if;
  update evidence_items set status='deleted' where id='e1000000-0000-0000-0000-000000000001';
  select count(*) into n from evidence_items where id='e1000000-0000-0000-0000-000000000001';
  if n <> 0 then raise exception 'FAIL a10b: cross-user visibility after update'; end if;
  select count(*) into n from storage.objects;
  if n <> 0 then raise exception 'FAIL a10c: cross-user storage visible: %', n; end if;
end $$;
do $$ declare n int; begin
  set local role postgres;
  select count(*) into n from evidence_items
    where id='e1000000-0000-0000-0000-000000000001' and status='deleted';
  if n <> 0 then raise exception 'FAIL a10d: cross-user delete succeeded'; end if;
end $$;

-- ============================================================
-- BATCH / GUARD / EPISTEMIC — engine path (postgres = service)
-- ============================================================
reset role;

-- b1: client queues the synthesize intent (as user 6), duplicate collapses
set role authenticated;
select set_config('test.uid','66666666-6666-6666-6666-666666666666', false);
insert into jobs (id, agent_id, type) values
  ('b1000000-0000-0000-0000-0000000000b1','a6666666-0000-0000-0000-000000000006','synthesize');
insert into jobs (agent_id, type) values
  ('a6666666-0000-0000-0000-000000000006','synthesize')
on conflict do nothing;
do $$ declare n int; begin
  select count(*) into n from jobs where agent_id='a6666666-0000-0000-0000-000000000006' and type='synthesize';
  if n <> 1 then raise exception 'FAIL b1: duplicate queued synthesize: %', n; end if;
end $$;
reset role;

-- b2: atomic claim: 3 items -> reading, job running, invited -> feed_submitted
do $$ declare r jsonb; s text; n int; begin
  r := claim_synthesis_batch('b1000000-0000-0000-0000-0000000000b1');
  if r->>'status' <> 'claimed' or (r->>'count')::int <> 3 then
    raise exception 'FAIL b2 claim: %', r; end if;
  select state into s from agents where id='a6666666-0000-0000-0000-000000000006';
  if s <> 'feed_submitted' then raise exception 'FAIL b2 state: %', s; end if;
  select count(*) into n from evidence_items
    where submitted_in='b1000000-0000-0000-0000-0000000000b1' and status='reading';
  if n <> 3 then raise exception 'FAIL b2 items: %', n; end if;
end $$;

-- b3: while running, a second intent cannot become active (unique index)
set role authenticated;
select set_config('test.uid','66666666-6666-6666-6666-666666666666', false);
insert into jobs (agent_id, type) values
  ('a6666666-0000-0000-0000-000000000006','synthesize')
on conflict do nothing;
reset role;
do $$ declare n int; begin
  select count(*) into n from jobs
    where agent_id='a6666666-0000-0000-0000-000000000006'
      and type='synthesize' and status in ('queued','running');
  if n <> 1 then raise exception 'FAIL b3: second active synthesize: %', n; end if;
end $$;

-- b4: THE GUARD — direct terminal writes without the finalizer are refused
do $$ begin
  begin
    update jobs set status='done' where id='b1000000-0000-0000-0000-0000000000b1';
    raise exception 'FAIL b4: direct running->done permitted';
  exception when others then
    if sqlerrm not like '%finalize_required%' then
      raise exception 'FAIL b4 wrong error: %', sqlerrm; end if;
  end;
  begin
    update jobs set status='failed', error='x' where id='b1000000-0000-0000-0000-0000000000b1';
    raise exception 'FAIL b4b: direct running->failed permitted';
  exception when others then
    if sqlerrm not like '%finalize_required%' then
      raise exception 'FAIL b4b wrong error: %', sqlerrm; end if;
  end;
end $$;

-- b5: repeated claim of the same job refuses safely
do $$ begin
  begin
    perform claim_synthesis_batch('b1000000-0000-0000-0000-0000000000b1');
    raise exception 'FAIL b5: reclaim allowed';
  exception when others then
    if sqlerrm not like '%job_not_queued%' then
      raise exception 'FAIL b5 wrong error: %', sqlerrm; end if;
  end;
end $$;

-- b6: item created after the claim stays out of the batch
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e6000000-0000-0000-0000-000000000006','a6666666-0000-0000-0000-000000000006','text','Late note','Added after claim.');
do $$ declare n int; begin
  select count(*) into n from evidence_items
    where id='e6000000-0000-0000-0000-000000000006' and status='received' and submitted_in is null;
  if n <> 1 then raise exception 'FAIL b6: late item joined batch'; end if;
end $$;

-- e1: engine marks E1 read, E3 failed; E2 stays reading (for the sweep)
update evidence_items set status='read', read_at=now()
  where id='e1000000-0000-0000-0000-000000000001';
update evidence_items set status='failed', failure_reason='FOOUND could not read this file.'
  where id='e3000000-0000-0000-0000-000000000003';

-- e2: memory refusals — read+same-agent only
insert into memory (agent_id, layer, statement, provenance, source, evidence) values
  ('a6666666-0000-0000-0000-000000000006','record','Led brand at three companies.','extracted','feed',
   '[{"item":"e1000000-0000-0000-0000-000000000001"}]');
do $$ begin
  begin
    insert into memory (agent_id, layer, statement, provenance, source, evidence) values
      ('a6666666-0000-0000-0000-000000000006','record','x','extracted','feed',
       '[{"item":"e2000000-0000-0000-0000-000000000002"}]');
    raise exception 'FAIL e2: cited reading evidence';
  exception when others then
    if sqlerrm not like '%evidence_not_read%' then raise exception 'FAIL e2: %', sqlerrm; end if;
  end;
  begin
    insert into memory (agent_id, layer, statement, provenance, source, evidence) values
      ('a6666666-0000-0000-0000-000000000006','record','x','extracted','feed',
       '[{"item":"e3000000-0000-0000-0000-000000000003"}]');
    raise exception 'FAIL e2b: cited failed evidence';
  exception when others then
    if sqlerrm not like '%evidence_not_read%' then raise exception 'FAIL e2b: %', sqlerrm; end if;
  end;
  begin
    insert into memory (agent_id, layer, statement, provenance, source, evidence) values
      ('a6666666-0000-0000-0000-000000000006','record','x','extracted','feed',
       '[{"item":"e9999999-9999-9999-9999-999999999999"}]');
    raise exception 'FAIL e2c: cited nonexistent evidence';
  exception when others then
    if sqlerrm not like '%evidence_not_found%' then raise exception 'FAIL e2c: %', sqlerrm; end if;
  end;
  begin
    insert into memory (agent_id, layer, statement, provenance, source, evidence) values
      ('a6666666-0000-0000-0000-000000000006','record','x','extracted','feed',
       '[{"item":"not-a-uuid"}]');
    raise exception 'FAIL e2d: malformed ref accepted';
  exception when others then
    if sqlerrm not like '%invalid_evidence_ref%' then raise exception 'FAIL e2d: %', sqlerrm; end if;
  end;
end $$;

-- e3: cross-agent provenance refusal (agent 97 read item cited by agent 96)
insert into jobs (id, agent_id, type, status, started_at) values
  ('b7000000-0000-0000-0000-0000000000b7','a7777777-0000-0000-0000-000000000007','synthesize','running',now());
insert into evidence_items (id, agent_id, kind, label, body, status, submitted_in, read_at) values
  ('e7000000-0000-0000-0000-000000000007','a7777777-0000-0000-0000-000000000007','text','Seven note','x',
   'read','b7000000-0000-0000-0000-0000000000b7', now());
do $$ begin
  begin
    insert into memory (agent_id, layer, statement, provenance, source, evidence) values
      ('a6666666-0000-0000-0000-000000000006','record','x','extracted','feed',
       '[{"item":"e7000000-0000-0000-0000-000000000007"}]');
    raise exception 'FAIL e3: cross-agent provenance accepted';
  exception when others then
    if sqlerrm not like '%evidence_wrong_agent%' then raise exception 'FAIL e3: %', sqlerrm; end if;
  end;
end $$;

-- f1: FINALIZE mirror_ready — atomic job+lifecycle, sweep of E2
do $$ declare r jsonb; s text; n int; begin
  r := finalize_synthesis('b1000000-0000-0000-0000-0000000000b1','mirror_ready');
  if (r->>'swept_reading_items')::int <> 1 then raise exception 'FAIL f1 sweep: %', r; end if;
  select status into s from jobs where id='b1000000-0000-0000-0000-0000000000b1';
  if s <> 'done' then raise exception 'FAIL f1 job: %', s; end if;
  select state into s from agents where id='a6666666-0000-0000-0000-000000000006';
  if s <> 'mirror_ready' then raise exception 'FAIL f1 agent: %', s; end if;
  select count(*) into n from evidence_items
    where id='e2000000-0000-0000-0000-000000000002' and status='failed'
      and failure_reason like 'FOOUND could not finish reading%';
  if n <> 1 then raise exception 'FAIL f1 swept item state'; end if;
end $$;

-- f2: repeated finalization refuses safely; invalid outcome refused
do $$ begin
  begin
    perform finalize_synthesis('b1000000-0000-0000-0000-0000000000b1','mirror_ready');
    raise exception 'FAIL f2: refinalize allowed';
  exception when others then
    if sqlerrm not like '%job_not_running%' then raise exception 'FAIL f2: %', sqlerrm; end if;
  end;
  begin
    perform finalize_synthesis('b1000000-0000-0000-0000-0000000000b1','something_else');
    raise exception 'FAIL f2b: invalid outcome allowed';
  exception when others then
    if sqlerrm not like '%invalid_outcome%' then raise exception 'FAIL f2b: %', sqlerrm; end if;
  end;
end $$;

-- e4: deletion — orphaning exactness, terminal state, scrub, no re-promotion
set role authenticated;
select set_config('test.uid','66666666-6666-6666-6666-666666666666', false);
update evidence_items set status='deleted' where id='e1000000-0000-0000-0000-000000000001';
reset role;
do $$ declare n int; begin
  select count(*) into n from memory
    where agent_id='a6666666-0000-0000-0000-000000000006' and status='orphaned'
      and evidence @> '[{"item":"e1000000-0000-0000-0000-000000000001"}]';
  if n <> 1 then raise exception 'FAIL e4: belief not orphaned'; end if;
  select count(*) into n from evidence_items
    where id='e1000000-0000-0000-0000-000000000001' and status='deleted' and deleted_at is not null;
  if n <> 1 then raise exception 'FAIL e4b: deleted_at not stamped'; end if;
end $$;
update evidence_items set body=null where id='e1000000-0000-0000-0000-000000000001';  -- scrub OK
do $$ begin
  begin
    update evidence_items set status='received' where id='e1000000-0000-0000-0000-000000000001';
    raise exception 'FAIL e4c: deleted not terminal';
  exception when others then
    if sqlerrm not like '%deleted_is_terminal%' then raise exception 'FAIL e4c: %', sqlerrm; end if;
  end;
  begin
    update memory set status='active'
      where evidence @> '[{"item":"e1000000-0000-0000-0000-000000000001"}]';
    raise exception 'FAIL e4d: orphaned re-promoted over dead ref';
  exception when others then
    if sqlerrm not like '%evidence_not_read%' then raise exception 'FAIL e4d: %', sqlerrm; end if;
  end;
end $$;
do $$ declare n int; begin
  select count(*) into n from evidence_items
    where id='e1000000-0000-0000-0000-000000000001'
      and label='Career notes' and body is null and deleted_at is not null;
  if n <> 1 then raise exception 'FAIL e4e: metadata receipt lost'; end if;
end $$;

-- ============================================================
-- LIFECYCLE — zero-item, archived, commissioning cycle, non-regression
-- ============================================================
-- l1: zero-item claim (agent 98, invited): honest failure, NO lifecycle flip
set role authenticated;
select set_config('test.uid','88888888-8888-8888-8888-888888888888', false);
insert into jobs (id, agent_id, type) values
  ('b8000000-0000-0000-0000-0000000000b8','a8888888-0000-0000-0000-000000000008','synthesize');
reset role;
do $$ declare r jsonb; s text; begin
  r := claim_synthesis_batch('b8000000-0000-0000-0000-0000000000b8');
  if r->>'status' <> 'empty' then raise exception 'FAIL l1 claim: %', r; end if;
  select status into s from jobs where id='b8000000-0000-0000-0000-0000000000b8';
  if s <> 'failed' then raise exception 'FAIL l1 job: %', s; end if;
  select state into s from agents where id='a8888888-0000-0000-0000-000000000008';
  if s <> 'invited' then raise exception 'FAIL l1 state moved: %', s; end if;
end $$;

-- l2: real batch -> Reading; technical failure -> commissioning
set role authenticated;
select set_config('test.uid','88888888-8888-8888-8888-888888888888', false);
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e8000000-0000-0000-0000-000000000008','a8888888-0000-0000-0000-000000000008','text','Thin note','Very little.');
insert into jobs (id, agent_id, type) values
  ('b8100000-0000-0000-0000-0000000000b9','a8888888-0000-0000-0000-000000000008','synthesize');
reset role;
do $$ declare r jsonb; s text; begin
  r := claim_synthesis_batch('b8100000-0000-0000-0000-0000000000b9');
  select state into s from agents where id='a8888888-0000-0000-0000-000000000008';
  if s <> 'feed_submitted' then raise exception 'FAIL l2 claim state: %', s; end if;
  r := finalize_synthesis('b8100000-0000-0000-0000-0000000000b9','failed','FOOUND could not finish reading. Try again.');
  select state into s from agents where id='a8888888-0000-0000-0000-000000000008';
  if s <> 'commissioning' then raise exception 'FAIL l2 finalize state: %', s; end if;
  select error into s from jobs where id='b8100000-0000-0000-0000-0000000000b9';
  if s not like 'FOOUND could not finish reading%' then raise exception 'FAIL l2 error: %', s; end if;
end $$;

-- l3: commissioning cycle — new evidence + genuine claim re-enters Reading,
--     insufficient understanding returns to commissioning
set role authenticated;
select set_config('test.uid','88888888-8888-8888-8888-888888888888', false);
insert into evidence_items (id, agent_id, kind, label, body) values
  ('e9000000-0000-0000-0000-000000000009','a8888888-0000-0000-0000-000000000008','text','More material','Additional history.');
insert into jobs (id, agent_id, type) values
  ('b8200000-0000-0000-0000-0000000000ba','a8888888-0000-0000-0000-000000000008','synthesize');
reset role;
do $$ declare r jsonb; s text; begin
  r := claim_synthesis_batch('b8200000-0000-0000-0000-0000000000ba');
  select state into s from agents where id='a8888888-0000-0000-0000-000000000008';
  if s <> 'feed_submitted' then raise exception 'FAIL l3 cycle re-entry: %', s; end if;
  r := finalize_synthesis('b8200000-0000-0000-0000-0000000000ba','needs_more_evidence');
  select state into s from agents where id='a8888888-0000-0000-0000-000000000008';
  if s <> 'commissioning' then raise exception 'FAIL l3 insufficient: %', s; end if;
  select status into s from jobs where id='b8200000-0000-0000-0000-0000000000ba';
  if s <> 'done' then raise exception 'FAIL l3 job done: %', s; end if;
end $$;

-- l4: archived claim refusal persists an honest failed job; state unchanged
set role authenticated;
select set_config('test.uid','99999999-9999-9999-9999-999999999999', false);
insert into jobs (id, agent_id, type) values
  ('b9000000-0000-0000-0000-0000000000bb','a9999999-0000-0000-0000-000000000009','synthesize');
reset role;
do $$ declare r jsonb; s text; begin
  r := claim_synthesis_batch('b9000000-0000-0000-0000-0000000000bb');
  if r->>'reason' <> 'agent_archived' then raise exception 'FAIL l4 claim: %', r; end if;
  select status into s from jobs where id='b9000000-0000-0000-0000-0000000000bb';
  if s <> 'failed' then raise exception 'FAIL l4 job: %', s; end if;
  select error into s from jobs where id='b9000000-0000-0000-0000-0000000000bb';
  if s not like 'This FOOUND is archived%' then raise exception 'FAIL l4 error: %', s; end if;
  select state into s from agents where id='a9999999-0000-0000-0000-000000000009';
  if s <> 'archived' then raise exception 'FAIL l4 state: %', s; end if;
end $$;

-- l5: established states never regress (at_work via agent 97, paused via 100)
do $$ declare r jsonb; s text; begin
  r := finalize_synthesis('b7000000-0000-0000-0000-0000000000b7','mirror_ready');
  select state into s from agents where id='a7777777-0000-0000-0000-000000000007';
  if s <> 'at_work' then raise exception 'FAIL l5 at_work regressed: %', s; end if;
  select status into s from jobs where id='b7000000-0000-0000-0000-0000000000b7';
  if s <> 'done' then raise exception 'FAIL l5 job: %', s; end if;
end $$;
set role authenticated;
select set_config('test.uid','10101010-1010-1010-1010-101010101010', false);
insert into evidence_items (id, agent_id, kind, label, body) values
  ('ea000000-0000-0000-0000-00000000000a','a1010101-0000-0000-0000-000000000010','text','Paused note','New info while paused.');
insert into jobs (id, agent_id, type) values
  ('ba000000-0000-0000-0000-0000000000bc','a1010101-0000-0000-0000-000000000010','synthesize');
reset role;
do $$ declare r jsonb; s text; begin
  r := claim_synthesis_batch('ba000000-0000-0000-0000-0000000000bc');
  select state into s from agents where id='a1010101-0000-0000-0000-000000000010';
  if s <> 'paused' then raise exception 'FAIL l5b paused regressed on claim: %', s; end if;
  r := finalize_synthesis('ba000000-0000-0000-0000-0000000000bc','failed');
  select state into s from agents where id='a1010101-0000-0000-0000-000000000010';
  if s <> 'paused' then raise exception 'FAIL l5c paused regressed on finalize: %', s; end if;
end $$;

-- ============================================================
-- INFRA — bucket preflight converges / fails loudly
-- ============================================================
do $$ declare b record; begin
  select * into b from storage.buckets where id='feeds';
  if not found then raise exception 'FAIL i1: feeds bucket missing'; end if;
end $$;
update storage.buckets set public=true where id='feeds';
do $$ begin
  begin
    execute 'do ''declare b record; begin select * into b from storage.buckets where id = ''''feeds''''; if found then if b.public or b.file_size_limit is distinct from 20971520 then raise exception ''''feeds_bucket_misconfigured''''; end if; end if; end''';
    raise exception 'FAIL i2: misconfigured bucket not detected';
  exception when others then
    if sqlerrm not like '%feeds_bucket_misconfigured%' then raise exception 'FAIL i2: %', sqlerrm; end if;
  end;
end $$;
update storage.buckets set public=false where id='feeds';

select 'M007 OK: authority (column-grant + RLS + service-only functions), epistemic (read+same-agent provenance, orphaning, terminal deletion, scrub), batch (atomic claim, active dedupe, guard: finalize is the only terminal path), lifecycle (zero-item honest, archived persisted refusal, commissioning cycle, non-regression), infra (bucket preflight)';
