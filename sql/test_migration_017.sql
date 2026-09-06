\set ON_ERROR_STOP on
-- Verification for migration 017 (edition write authority). Single session.
-- Fixtures: agents 60-61. Never run against the live FOOUND project.
-- The two-session interleavings live in run_017_concurrency.sh.

-- production-parity grants (Supabase defaults): the service role owns the tables' privileges
grant usage on schema public, auth to authenticated, anon, service_role;
grant select, insert, update, delete on all tables in schema public to authenticated, service_role;
grant execute on function auth.uid() to authenticated, anon;

delete from jobs where agent_id in (select id from agents where agent_no between 60 and 61);
delete from editions where agent_id in (select id from agents where agent_no between 60 and 61);
delete from briefs where agent_id in (select id from agents where agent_no between 60 and 61);
delete from agents where agent_no between 60 and 61;

insert into auth.users (id, email) values
  ('60606060-6060-4606-8606-606060606060','n060@example.com')
on conflict (id) do update set email = excluded.email;
insert into agents (id, user_id, agent_no, state) values
  ('a6060606-0000-4000-8000-000000000060','60606060-6060-4606-8606-606060606060',60,'at_work');
insert into briefs (id, agent_id, version, state, content, confirmed_at) values
  ('b6060606-0000-4000-8000-000000000003','a6060606-0000-4000-8000-000000000060',3,'active',
   '{"chapters":[{"title":"ROLE SPACE","subjects":[{"handle":"Craft","lines":["Head of Design."]}]}]}', now());

-- the engine writes as the service role: RLS bypass, triggers still fire
set role service_role;

-- E0 · at work, current version: the write lands
insert into editions (agent_id, edition_date, brief_version, html, payload, outcome)
values ('a6060606-0000-4000-8000-000000000060','2026-09-06',3,'<p>v3</p>','{}','empty');

-- E1 · a stale version is refused
do $$ begin
  begin
    insert into editions (agent_id, edition_date, brief_version, html, outcome)
    values ('a6060606-0000-4000-8000-000000000060','2026-09-07',2,'<p>v2</p>','empty');
    raise exception 'FAIL E1 stale version accepted';
  exception when raise_exception then
    if sqlerrm <> 'authority_changed:brief_superseded' then raise exception 'FAIL E1 wrong reason: %', sqlerrm; end if;
  end;
end $$;

-- E2 · a NULL version is refused
do $$ begin
  begin
    insert into editions (agent_id, edition_date, brief_version, html, outcome)
    values ('a6060606-0000-4000-8000-000000000060','2026-09-07',null,'<p>?</p>','empty');
    raise exception 'FAIL E2 null version accepted';
  exception when raise_exception then
    if sqlerrm <> 'authority_changed:no_version' then raise exception 'FAIL E2 wrong reason: %', sqlerrm; end if;
  end;
end $$;

-- E3 · the replace path (update) is guarded too: pause, then rewrite the day
reset role;
update agents set state = 'paused' where agent_no = 60;
set role service_role;
do $$ begin
  begin
    update editions set html = '<p>rewritten</p>'
     where agent_id = 'a6060606-0000-4000-8000-000000000060' and edition_date = '2026-09-06';
    raise exception 'FAIL E3 paused rewrite accepted';
  exception when raise_exception then
    if sqlerrm <> 'authority_changed:paused' then raise exception 'FAIL E3 wrong reason: %', sqlerrm; end if;
  end;
end $$;

-- E4 · paused: a new day is refused
do $$ begin
  begin
    insert into editions (agent_id, edition_date, brief_version, html, outcome)
    values ('a6060606-0000-4000-8000-000000000060','2026-09-07',3,'<p>v3</p>','empty');
    raise exception 'FAIL E4 paused insert accepted';
  exception when raise_exception then
    if sqlerrm <> 'authority_changed:paused' then raise exception 'FAIL E4 wrong reason: %', sqlerrm; end if;
  end;
end $$;

-- E5 · resumed, but the Brief moved to v4: v3 refused, v4 lands, rewrite of the old day at v4 lands
reset role;
update agents set state = 'at_work' where agent_no = 60;
update briefs set state = 'superseded' where id = 'b6060606-0000-4000-8000-000000000003';
insert into briefs (id, agent_id, version, state, content, confirmed_at) values
  ('b6060606-0000-4000-8000-000000000004','a6060606-0000-4000-8000-000000000060',4,'active',
   '{"chapters":[{"title":"ROLE SPACE","subjects":[{"handle":"Craft","lines":["VP Design."]}]}]}', now());
set role service_role;
do $$ begin
  begin
    insert into editions (agent_id, edition_date, brief_version, html, outcome)
    values ('a6060606-0000-4000-8000-000000000060','2026-09-07',3,'<p>v3</p>','empty');
    raise exception 'FAIL E5 superseded accepted';
  exception when raise_exception then
    if sqlerrm <> 'authority_changed:brief_superseded' then raise exception 'FAIL E5 wrong reason: %', sqlerrm; end if;
  end;
end $$;
insert into editions (agent_id, edition_date, brief_version, html, outcome)
values ('a6060606-0000-4000-8000-000000000060','2026-09-07',4,'<p>v4</p>','empty');
update editions set brief_version = 4, html = '<p>v4 rewrite</p>'
 where agent_id = 'a6060606-0000-4000-8000-000000000060' and edition_date = '2026-09-06';

-- E6 · no active Brief at all: refused
reset role;
update briefs set state = 'abandoned' where id = 'b6060606-0000-4000-8000-000000000004';
set role service_role;
do $$ begin
  begin
    insert into editions (agent_id, edition_date, brief_version, html, outcome)
    values ('a6060606-0000-4000-8000-000000000060','2026-09-08',4,'<p>v4</p>','empty');
    raise exception 'FAIL E6 no active brief accepted';
  exception when raise_exception then
    if sqlerrm <> 'authority_changed:no_active_brief' then raise exception 'FAIL E6 wrong reason: %', sqlerrm; end if;
  end;
end $$;

-- E7 · the day's record after all of it: two editions, both at v4
reset role;
do $$ declare n int; v int; begin
  select count(*), min(brief_version) into n, v from editions where agent_id = 'a6060606-0000-4000-8000-000000000060';
  if n <> 2 or v <> 4 then raise exception 'FAIL E7 editions n=% minv=%', n, v; end if;
end $$;

-- E8 · active but unconfirmed: an unconfirmed Brief authorizes nothing
reset role;
update briefs set state = 'active', confirmed_at = null where id = 'b6060606-0000-4000-8000-000000000004';
set role service_role;
do $$ begin
  begin
    insert into editions (agent_id, edition_date, brief_version, html, outcome)
    values ('a6060606-0000-4000-8000-000000000060','2026-09-08',4,'<p>v4</p>','empty');
    raise exception 'FAIL E8 unconfirmed brief accepted';
  exception when raise_exception then
    if sqlerrm <> 'authority_changed:brief_unconfirmed' then raise exception 'FAIL E8 wrong reason: %', sqlerrm; end if;
  end;
end $$;
reset role;
update briefs set confirmed_at = now() where id = 'b6060606-0000-4000-8000-000000000004';
set role service_role;
insert into editions (agent_id, edition_date, brief_version, html, outcome)
values ('a6060606-0000-4000-8000-000000000060','2026-09-08',4,'<p>v4</p>','empty');
reset role;

-- cleanup (zero residue)
delete from editions where agent_id = 'a6060606-0000-4000-8000-000000000060';
delete from briefs where agent_id = 'a6060606-0000-4000-8000-000000000060';
delete from agents where agent_no between 60 and 61;
delete from auth.users where id = '60606060-6060-4606-8606-606060606060';
select 'M017 OK' as result;
