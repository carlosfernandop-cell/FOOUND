\set ON_ERROR_STOP on
-- local-only stubs
do $$ begin create role authenticated nologin; exception when duplicate_object then null; end $$;
do $$ begin create role app_user nologin; exception when duplicate_object then null; end $$;
grant usage on schema public, auth to app_user;
grant select, insert, update, delete on all tables in schema public to app_user;
grant execute on all functions in schema public to app_user;

-- fixtures: two users, two agents (002 in mirror_ready, 003 fresh invited)
insert into auth.users (id, email) values
  ('22222222-2222-2222-2222-222222222222','two@example.com'),
  ('33333333-3333-3333-3333-333333333333','three@example.com')
on conflict do nothing;
delete from agents where agent_no in (92,93);
insert into agents (id, user_id, agent_no, state) values
  ('a2222222-0000-0000-0000-000000000002','22222222-2222-2222-2222-222222222222',92,'mirror_ready'),
  ('a3333333-0000-0000-0000-000000000003','33333333-3333-3333-3333-333333333333',93,'invited');

-- ===== 1. commission gates, in order =====
set role app_user;
select set_config('test.uid','22222222-2222-2222-2222-222222222222', false);
do $$ declare r text; begin
  r := commission_agent();
  if r <> 'blocked:no_confirmed_brief' then raise exception 'FAIL gate1: %', r; end if;
end $$;
reset role;

-- active brief, unconfirmed
insert into briefs (agent_id, version, state, content, readiness)
values ('a2222222-0000-0000-0000-000000000002', 1, 'active', '{"move":"x"}', 'ready');
set role app_user;
select set_config('test.uid','22222222-2222-2222-2222-222222222222', false);
do $$ declare r text; begin
  r := commission_agent();
  if r <> 'blocked:no_confirmed_brief' then raise exception 'FAIL gate2: %', r; end if;
end $$;
reset role;

-- confirmed but LIMITED without acknowledgment
update briefs set confirmed_at = now(), readiness = 'limited'
 where agent_id='a2222222-0000-0000-0000-000000000002';
set role app_user;
select set_config('test.uid','22222222-2222-2222-2222-222222222222', false);
do $$ declare r text; begin
  r := commission_agent();
  if r <> 'blocked:limited_unacknowledged' then raise exception 'FAIL gate3: %', r; end if;
end $$;
reset role;

-- acknowledged: commission succeeds; second call idempotent
update briefs set readiness_ack = 'I understand coverage is weaker in early-stage biotech.'
 where agent_id='a2222222-0000-0000-0000-000000000002';
set role app_user;
select set_config('test.uid','22222222-2222-2222-2222-222222222222', false);
do $$ declare r text; begin
  r := commission_agent();
  if r <> 'at_work' then raise exception 'FAIL commission: %', r; end if;
  r := commission_agent();
  if r <> 'at_work' then raise exception 'FAIL idempotent commission: %', r; end if;
  r := pause_agent();
  if r <> 'paused' then raise exception 'FAIL pause: %', r; end if;
  r := pause_agent();
  if r <> 'paused' then raise exception 'FAIL idempotent pause: %', r; end if;
  r := resume_agent();
  if r <> 'at_work' then raise exception 'FAIL resume: %', r; end if;
  r := archive_agent();
  if r <> 'archived' then raise exception 'FAIL archive: %', r; end if;
  r := resume_agent();
  if r <> 'blocked:state_archived' then raise exception 'FAIL resume-after-archive: %', r; end if;
end $$;
reset role;

-- ===== 2. a fresh agent cannot skip the lifecycle =====
set role app_user;
select set_config('test.uid','33333333-3333-3333-3333-333333333333', false);
do $$ declare r text; begin
  r := commission_agent();
  if r <> 'blocked:state_invited' then raise exception 'FAIL fresh-gate: %', r; end if;
end $$;
reset role;

-- ===== 3. RLS isolation: user 3 sees nothing of user 2 =====
insert into memory (agent_id, layer, statement, provenance, source)
values ('a2222222-0000-0000-0000-000000000002','self','Prefers building over inheriting.','stated','commission');
insert into editions (agent_id, edition_date, brief_version, html)
values ('a2222222-0000-0000-0000-000000000002', current_date, 1, '<html>02</html>');

set role app_user;
select set_config('test.uid','33333333-3333-3333-3333-333333333333', false);
do $$ declare n int; begin
  select count(*) into n from briefs;    if n <> 0 then raise exception 'FAIL RLS briefs: %', n; end if;
  select count(*) into n from memory;    if n <> 0 then raise exception 'FAIL RLS memory: %', n; end if;
  select count(*) into n from editions;  if n <> 0 then raise exception 'FAIL RLS editions: %', n; end if;
  select count(*) into n from candidates;if n <> 0 then raise exception 'FAIL RLS candidates: %', n; end if;
end $$;
-- and user 3 cannot write into user 2's ledger
do $$ begin
  begin
    insert into memory (agent_id, layer, statement, provenance, source)
    values ('a2222222-0000-0000-0000-000000000002','self','forged','stated','attack');
    raise exception 'FAIL: cross-user memory write accepted';
  exception when insufficient_privilege or check_violation then null;
  end;
end $$;
reset role;

-- ===== 4. owner CAN read own + propose brief, but not self-activate via row =====
set role app_user;
select set_config('test.uid','22222222-2222-2222-2222-222222222222', false);
do $$ declare n int; begin
  select count(*) into n from memory;   if n <> 1 then raise exception 'FAIL own memory: %', n; end if;
  select count(*) into n from editions; if n <> 1 then raise exception 'FAIL own editions: %', n; end if;
end $$;
insert into briefs (agent_id, version, state, content)
values ('a2222222-0000-0000-0000-000000000002', 2, 'proposed', '{"move":"y"}');
do $$ begin
  begin
    update briefs set state='active'
     where agent_id='a2222222-0000-0000-0000-000000000002' and version=2;
    raise exception 'FAIL: owner activated a brief via raw row update';
  exception when check_violation or insufficient_privilege then null;
  end;
end $$;
reset role;

-- ===== 5. structural invariants =====
do $$ begin
  begin
    insert into briefs (agent_id, version, state, content)
    values ('a2222222-0000-0000-0000-000000000002', 3, 'active', '{}');
    raise exception 'FAIL: two active briefs allowed';
  exception when unique_violation then null;
  end;
end $$;
do $$ begin
  begin
    insert into editions (agent_id, edition_date, html)
    values ('a2222222-0000-0000-0000-000000000002', current_date, 'dup');
    raise exception 'FAIL: duplicate edition per day allowed';
  exception when unique_violation then null;
  end;
end $$;

select 'M005 OK: gates, LIMITED ack, idempotency, lifecycle, RLS isolation, brief+edition invariants all hold';

-- cleanup
delete from agents where agent_no in (92,93);
