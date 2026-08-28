\set ON_ERROR_STOP on
-- Verification for migration 011 (commission_agent at_work recovery).
-- Fixtures use agents 96-99 (no collision with 92-95 of 005/006).

grant usage on schema public, auth to authenticated;
grant select, insert, update, delete on all tables in schema public to authenticated;
grant execute on all functions in schema public to authenticated;

delete from agents where agent_no in (96,97,98,99);
insert into auth.users (id, email) values
  ('66666666-6666-4666-8666-666666666666','six@example.com'),
  ('77777777-7777-4777-8777-777777777777','seven@example.com'),
  ('88888888-8888-4888-8888-888888888888','eight@example.com'),
  ('99999999-9999-4999-8999-999999999999','nine@example.com')
on conflict do nothing;
insert into agents (id, user_id, agent_no, state) values
  ('a6666666-0000-4000-8000-000000000096','66666666-6666-4666-8666-666666666666',96,'at_work'),
  ('a7777777-0000-4000-8000-000000000097','77777777-7777-4777-8777-777777777777',97,'at_work'),
  ('a8888888-0000-4000-8000-000000000098','88888888-8888-4888-8888-888888888888',98,'mirror_ready'),
  ('a9999999-0000-4000-8000-000000000099','99999999-9999-4999-8999-999999999999',99,'mirror_ready');

-- 96: ready Brief, no editions, no first_edition jobs → recovery inserts once
insert into briefs (agent_id, version, state, content, readiness, confirmed_at)
values ('a6666666-0000-4000-8000-000000000096', 1, 'active', '{"move":"x"}', 'ready', now());

-- 97: at_work + not_ready → recovery must NOT insert (no readiness bypass)
insert into briefs (agent_id, version, state, content, readiness, confirmed_at)
values ('a7777777-0000-4000-8000-000000000097', 1, 'active', '{"move":"x"}', 'not_ready', now());

set role authenticated;
select set_config('test.uid','66666666-6666-4666-8666-666666666666', false);

-- R1 · recovery inserts one first_edition, returns at_work, no state change
do $$ declare r text; n int; s text; v int; begin
  r := commission_agent();
  if r <> 'at_work' then raise exception 'FAIL R1 return: %', r; end if;
  select state into s from agents where id='a6666666-0000-4000-8000-000000000096';
  if s <> 'at_work' then raise exception 'FAIL R1 state reset: %', s; end if;
  select count(*) into n from jobs
    where agent_id='a6666666-0000-4000-8000-000000000096'
      and type='first_edition' and status='queued';
  if n <> 1 then raise exception 'FAIL R1 insert count: %', n; end if;
  select (payload->>'brief_version')::int into v from jobs
    where agent_id='a6666666-0000-4000-8000-000000000096'
      and type='first_edition' and status='queued';
  if v <> 1 then raise exception 'FAIL R1 brief_version: %', v; end if;
end $$;

-- R2 · second press is a no-op (queued first_edition exists)
do $$ declare r text; n int; begin
  r := commission_agent();
  if r <> 'at_work' then raise exception 'FAIL R2 return: %', r; end if;
  select count(*) into n from jobs
    where agent_id='a6666666-0000-4000-8000-000000000096'
      and type='first_edition';
  if n <> 1 then raise exception 'FAIL R2 second insert: %', n; end if;
end $$;

reset role;

-- R3 · done first_edition → no-op even with zero editions
delete from jobs where agent_id='a6666666-0000-4000-8000-000000000096';
insert into jobs (agent_id, type, status, completed_at)
values ('a6666666-0000-4000-8000-000000000096','first_edition','done', now());
set role authenticated;
select set_config('test.uid','66666666-6666-4666-8666-666666666666', false);
do $$ declare r text; n int; begin
  r := commission_agent();
  if r <> 'at_work' then raise exception 'FAIL R3 return: %', r; end if;
  select count(*) into n from jobs
    where agent_id='a6666666-0000-4000-8000-000000000096'
      and type='first_edition';
  if n <> 1 then raise exception 'FAIL R3 inserted over done: %', n; end if;
end $$;
reset role;

-- R4 · editions row exists → no-op
delete from jobs where agent_id='a6666666-0000-4000-8000-000000000096';
insert into editions (agent_id, edition_date, html, outcome)
values ('a6666666-0000-4000-8000-000000000096', current_date, '<html></html>', 'empty');
set role authenticated;
select set_config('test.uid','66666666-6666-4666-8666-666666666666', false);
do $$ declare r text; n int; begin
  r := commission_agent();
  if r <> 'at_work' then raise exception 'FAIL R4 return: %', r; end if;
  select count(*) into n from jobs
    where agent_id='a6666666-0000-4000-8000-000000000096'
      and type='first_edition';
  if n <> 0 then raise exception 'FAIL R4 inserted over edition: %', n; end if;
end $$;
reset role;

-- R5 · not_ready at_work → no insert (no readiness bypass)
set role authenticated;
select set_config('test.uid','77777777-7777-4777-8777-777777777777', false);
do $$ declare r text; n int; s text; begin
  r := commission_agent();
  if r <> 'at_work' then raise exception 'FAIL R5 return: %', r; end if;
  select state into s from agents where id='a7777777-0000-4000-8000-000000000097';
  if s <> 'at_work' then raise exception 'FAIL R5 state: %', s; end if;
  select count(*) into n from jobs
    where agent_id='a7777777-0000-4000-8000-000000000097'
      and type='first_edition';
  if n <> 0 then raise exception 'FAIL R5 bypass insert: %', n; end if;
end $$;
reset role;

-- R6 · failed first_edition + ready + zero editions → recover (insert)
delete from editions where agent_id='a6666666-0000-4000-8000-000000000096';
delete from jobs where agent_id='a6666666-0000-4000-8000-000000000096';
insert into jobs (agent_id, type, status, error, completed_at)
values ('a6666666-0000-4000-8000-000000000096','first_edition','failed','hunt_adapter_failed', now());
set role authenticated;
select set_config('test.uid','66666666-6666-4666-8666-666666666666', false);
do $$ declare r text; nq int; nt int; begin
  r := commission_agent();
  if r <> 'at_work' then raise exception 'FAIL R6 return: %', r; end if;
  select count(*) into nq from jobs
    where agent_id='a6666666-0000-4000-8000-000000000096'
      and type='first_edition' and status='queued';
  if nq <> 1 then raise exception 'FAIL R6 recover insert: %', nq; end if;
  select count(*) into nt from jobs
    where agent_id='a6666666-0000-4000-8000-000000000096'
      and type='first_edition';
  if nt <> 2 then raise exception 'FAIL R6 total jobs: %', nt; end if;
end $$;
reset role;

-- R7 · non-at_work gates preserved (null/not_ready → blocked:market_not_ready)
insert into briefs (agent_id, version, state, content, readiness, confirmed_at)
values ('a8888888-0000-4000-8000-000000000098', 1, 'active', '{"move":"x"}', 'not_ready', now());
set role authenticated;
select set_config('test.uid','88888888-8888-4888-8888-888888888888', false);
do $$ declare r text; begin
  r := commission_agent();
  if r <> 'blocked:market_not_ready' then
    raise exception 'FAIL R7 market_not_ready: %', r; end if;
end $$;
reset role;

-- R8 · non-at_work happy path still flips + enqueues
update briefs set readiness='ready'
 where agent_id='a8888888-0000-4000-8000-000000000098';
set role authenticated;
select set_config('test.uid','88888888-8888-4888-8888-888888888888', false);
do $$ declare r text; n int; s text; begin
  r := commission_agent();
  if r <> 'at_work' then raise exception 'FAIL R8 return: %', r; end if;
  select state into s from agents where id='a8888888-0000-4000-8000-000000000098';
  if s <> 'at_work' then raise exception 'FAIL R8 flip: %', s; end if;
  select count(*) into n from jobs
    where agent_id='a8888888-0000-4000-8000-000000000098'
      and type='first_edition' and status='queued';
  if n <> 1 then raise exception 'FAIL R8 enqueue: %', n; end if;
end $$;
reset role;

-- R9 · 006 limited-unacknowledged gate remains intact
insert into briefs (agent_id, version, state, content, readiness, confirmed_at)
values ('a9999999-0000-4000-8000-000000000099', 1, 'active', '{"move":"x"}', 'limited', now());
set role authenticated;
select set_config('test.uid','99999999-9999-4999-8999-999999999999', false);
do $$ declare r text; n int; s text; begin
  r := commission_agent();
  if r <> 'blocked:limited_unacknowledged' then
    raise exception 'FAIL R9 limited_unacknowledged: %', r; end if;
  select state into s from agents where id='a9999999-0000-4000-8000-000000000099';
  if s <> 'mirror_ready' then raise exception 'FAIL R9 state flip: %', s; end if;
  select count(*) into n from jobs
    where agent_id='a9999999-0000-4000-8000-000000000099';
  if n <> 0 then raise exception 'FAIL R9 enqueue: %', n; end if;
end $$;
reset role;

select 'M011 OK: at_work recovery inserts one job then no-ops; no readiness bypass; 006 gates preserved';

delete from agents where agent_no in (96,97,98,99);
