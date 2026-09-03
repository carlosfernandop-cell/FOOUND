\set ON_ERROR_STOP on
-- Verification for migration 013 (Working Brief doors).
-- Fixtures: agents 50-53. Never run against the live FOOUND project.

grant usage on schema public, auth to authenticated, anon;
grant select, insert, update, delete on all tables in schema public to authenticated;
grant execute on function activate_brief(uuid) to authenticated;
grant execute on function next_brief_version(uuid) to authenticated;
grant execute on function auth.uid() to authenticated, anon;

delete from jobs where agent_id in (select id from agents where agent_no between 50 and 53);
delete from briefs where agent_id in (select id from agents where agent_no between 50 and 53);
delete from agents where agent_no between 50 and 53;

insert into auth.users (id, email) values
  ('50505050-5050-4505-8505-505050505050','n002@example.com'),
  ('51515151-5151-4515-8515-515151515151','other@example.com')
on conflict (id) do update set email = excluded.email;

insert into agents (id, user_id, agent_no, state) values
  ('a5050505-0000-4000-8000-000000000050','50505050-5050-4505-8505-505050505050',50,'mirror_ready'),
  ('a5151515-0000-4000-8000-000000000051','51515151-5151-4515-8515-515151515151',51,'mirror_ready'),
  ('a5252525-0000-4000-8000-000000000052','50505050-5050-4505-8505-505050505050',52,'archived');

-- B0 · next_brief_version starts at 1
do $$ declare v int; begin
  v := next_brief_version('a5050505-0000-4000-8000-000000000050');
  if v <> 1 then raise exception 'FAIL B0 next version: %', v; end if;
end $$;

-- the client proposes a Brief (005 policy) with the next version
set role authenticated;
select set_config('test.uid','50505050-5050-4505-8505-505050505050', false);
insert into briefs (id, agent_id, version, state, content) values
  ('b0000000-0000-4000-8000-000000000001','a5050505-0000-4000-8000-000000000050', 1, 'proposed',
   '{"chapters":[{"title":"ROLE SPACE","subjects":[{"handle":"Craft","lines":["Head of Design."]}]},{"title":"WHERE","subjects":[{"handle":"Geography","lines":["Berlin, London."]}]}]}');

-- B1 · a stranger cannot activate it
select set_config('test.uid','51515151-5151-4515-8515-515151515151', false);
do $$ declare r text; begin
  r := activate_brief('b0000000-0000-4000-8000-000000000001');
  if r <> 'blocked:not_owned' then raise exception 'FAIL B1 not_owned: %', r; end if;
end $$;

-- B2 · the owner activates: state, confirmed_at, one compile_brief job
select set_config('test.uid','50505050-5050-4505-8505-505050505050', false);
do $$ declare r text; st text; ca timestamptz; n int; begin
  r := activate_brief('b0000000-0000-4000-8000-000000000001');
  if r <> 'active:v1' then raise exception 'FAIL B2 return: %', r; end if;
  select state, confirmed_at into st, ca from briefs where id = 'b0000000-0000-4000-8000-000000000001';
  if st <> 'active' or ca is null then raise exception 'FAIL B2 state/confirmed: % %', st, ca; end if;
  select count(*) into n from jobs where agent_id = 'a5050505-0000-4000-8000-000000000050'
    and type = 'compile_brief' and status = 'queued';
  if n <> 1 then raise exception 'FAIL B2 compile job: %', n; end if;
end $$;

-- B3 · idempotent: activating the active Brief again changes nothing
do $$ declare r text; n int; begin
  r := activate_brief('b0000000-0000-4000-8000-000000000001');
  if r <> 'active:v1' then raise exception 'FAIL B3 return: %', r; end if;
  select count(*) into n from jobs where agent_id = 'a5050505-0000-4000-8000-000000000050' and type = 'compile_brief';
  if n <> 1 then raise exception 'FAIL B3 duplicate job: %', n; end if;
end $$;

-- B4 · a second proposal supersedes the first on activation; never in place
do $$ declare r text; v int; st1 text; st2 text; n int; begin
  v := next_brief_version('a5050505-0000-4000-8000-000000000050');
  if v <> 2 then raise exception 'FAIL B4 next version: %', v; end if;
  insert into briefs (id, agent_id, version, state, content) values
    ('b0000000-0000-4000-8000-000000000002','a5050505-0000-4000-8000-000000000050', v, 'proposed',
     '{"chapters":[{"title":"ROLE SPACE","subjects":[{"handle":"Craft","lines":["VP Design."]}]}]}');
  r := activate_brief('b0000000-0000-4000-8000-000000000002');
  if r <> 'active:v2' then raise exception 'FAIL B4 return: %', r; end if;
  select state into st1 from briefs where id = 'b0000000-0000-4000-8000-000000000001';
  select state into st2 from briefs where id = 'b0000000-0000-4000-8000-000000000002';
  if st1 <> 'superseded' or st2 <> 'active' then raise exception 'FAIL B4 states: % %', st1, st2; end if;
  select count(*) into n from briefs where agent_id = 'a5050505-0000-4000-8000-000000000050' and state = 'active';
  if n <> 1 then raise exception 'FAIL B4 one active: %', n; end if;
  -- content of the superseded row is intact
  if (select content->'chapters'->0->'subjects'->0->'lines'->>0 from briefs where id = 'b0000000-0000-4000-8000-000000000001') <> 'Head of Design.' then
    raise exception 'FAIL B4 superseded content mutated'; end if;
end $$;

-- B5 · an empty proposal cannot be activated
do $$ declare r text; begin
  insert into briefs (id, agent_id, version, state, content) values
    ('b0000000-0000-4000-8000-000000000003','a5050505-0000-4000-8000-000000000050', 3, 'proposed', '{"chapters":[]}');
  r := activate_brief('b0000000-0000-4000-8000-000000000003');
  if r <> 'blocked:empty_brief' then raise exception 'FAIL B5 empty: %', r; end if;
  if (select state from briefs where id = 'b0000000-0000-4000-8000-000000000003') <> 'proposed' then
    raise exception 'FAIL B5 state moved'; end if;
end $$;

-- B6 · a superseded row cannot be re-activated (no going back through the door)
do $$ declare r text; begin
  r := activate_brief('b0000000-0000-4000-8000-000000000001');
  if r <> 'blocked:state_superseded' then raise exception 'FAIL B6 superseded: %', r; end if;
end $$;

-- B7 · the client may express propose_brief; never first_edition
do $$ declare n int; begin
  insert into jobs (agent_id, type) values ('a5050505-0000-4000-8000-000000000050', 'propose_brief');
  select count(*) into n from jobs where agent_id = 'a5050505-0000-4000-8000-000000000050' and type = 'propose_brief' and status = 'queued';
  if n <> 1 then raise exception 'FAIL B7 propose_brief: %', n; end if;
  begin
    insert into jobs (agent_id, type) values ('a5050505-0000-4000-8000-000000000050', 'first_edition');
    raise exception 'FAIL B7 first_edition insert should be refused';
  exception when insufficient_privilege or check_violation then null;
  end;
end $$;

-- B8 · archived agent: door closed
do $$ declare r text; begin
  reset role;
  insert into briefs (id, agent_id, version, state, content) values
    ('b0000000-0000-4000-8000-000000000004','a5252525-0000-4000-8000-000000000052', 1, 'proposed',
     '{"chapters":[{"title":"WHERE","subjects":[{"handle":"G","lines":["Berlin."]}]}]}');
  set role authenticated;
  perform set_config('test.uid','50505050-5050-4505-8505-505050505050', false);
  r := activate_brief('b0000000-0000-4000-8000-000000000004');
  if r <> 'blocked:state_archived' then raise exception 'FAIL B8 archived: %', r; end if;
end $$;

-- B9 · no session → no_session; nothing else moved
reset role;
do $$ declare r text; begin
  perform set_config('test.uid','', false);
  set role anon;
  begin
    r := activate_brief('b0000000-0000-4000-8000-000000000002');
    if r <> 'no_session' then raise exception 'FAIL B9 no_session: %', r; end if;
  exception when insufficient_privilege then null;  -- anon may not even call it
  end;
  reset role;
end $$;
reset role;

-- B10 · agents.state never touched by activation
do $$ declare st text; begin
  select state into st from agents where id = 'a5050505-0000-4000-8000-000000000050';
  if st <> 'mirror_ready' then raise exception 'FAIL B10 agent state moved: %', st; end if;
end $$;

-- cleanup (zero residue)
delete from jobs where agent_id in (select id from agents where agent_no between 50 and 53);
delete from briefs where agent_id in (select id from agents where agent_no between 50 and 53);
delete from agents where agent_no between 50 and 53;
delete from auth.users where id in ('50505050-5050-4505-8505-505050505050','51515151-5151-4515-8515-515151515151');

select 'M013 OK' as result;
