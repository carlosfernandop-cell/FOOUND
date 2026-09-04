\set ON_ERROR_STOP on
-- Verification for migration 016 (seen on the agent).
-- Fixtures: agents 58-59. Never run against the live FOOUND project.

grant usage on schema public, auth to authenticated, anon;
grant select on agents to authenticated;
grant execute on function auth.uid() to authenticated, anon;

delete from agents where agent_no between 58 and 59;
insert into auth.users (id, email) values
  ('58585858-5858-4585-8585-585858585858','n058@example.com'),
  ('59595959-5959-4595-8595-595959595959','n059@example.com')
on conflict (id) do update set email = excluded.email;
insert into agents (id, user_id, agent_no, state) values
  ('a5858585-0000-4000-8000-000000000058','58585858-5858-4585-8585-585858585858',58,'at_work'),
  ('a5959595-0000-4000-8000-000000000059','59595959-5959-4595-8595-595959595959',59,'at_work');

set role authenticated;
select set_config('test.uid','58585858-5858-4585-8585-585858585858', false);

-- S0 · the column exists and starts empty
do $$ declare s jsonb; begin
  select seen into s from agents where agent_no = 58;
  if s <> '{}'::jsonb then raise exception 'FAIL S0 seen not empty: %', s; end if;
end $$;

-- S1 · the owner marks a room; the value is on their row
do $$ declare r text; s jsonb; begin
  r := mark_seen('at-work', 'ed-0001');
  if r <> 'seen:at-work' then raise exception 'FAIL S1 result: %', r; end if;
  select seen into s from agents where agent_no = 58;
  if s->>'at-work' <> 'ed-0001' then raise exception 'FAIL S1 value: %', s; end if;
end $$;

-- S2 · a second room adds; the first stays
do $$ declare s jsonb; begin
  perform mark_seen('candidate', '2026-09-04T18:00:00Z');
  select seen into s from agents where agent_no = 58;
  if s->>'at-work' <> 'ed-0001' or s->>'candidate' <> '2026-09-04T18:00:00Z' then
    raise exception 'FAIL S2 merge: %', s; end if;
end $$;

-- S3 · marking again replaces
do $$ declare s jsonb; begin
  perform mark_seen('at-work', 'ed-0002');
  select seen into s from agents where agent_no = 58;
  if s->>'at-work' <> 'ed-0002' then raise exception 'FAIL S3 replace: %', s; end if;
end $$;

-- S4 · an unknown room, an empty value, an oversized value: ignored, nothing raised
do $$ declare s jsonb; begin
  if mark_seen('kitchen', 'x') <> 'ignored' then raise exception 'FAIL S4 room'; end if;
  if mark_seen('brief', '') <> 'ignored' then raise exception 'FAIL S4 empty'; end if;
  if mark_seen('brief', repeat('x', 65)) <> 'ignored' then raise exception 'FAIL S4 long'; end if;
  select seen into s from agents where agent_no = 58;
  if s ? 'kitchen' or s ? 'brief' then raise exception 'FAIL S4 wrote: %', s; end if;
end $$;

-- S5 · the other person's row is untouched, and they cannot see 58's
select set_config('test.uid','59595959-5959-4595-8595-595959595959', false);
do $$ declare s jsonb; c int; begin
  select seen into s from agents where agent_no = 59;
  if s <> '{}'::jsonb then raise exception 'FAIL S5 other row: %', s; end if;
  select count(*) into c from agents where agent_no = 58;
  if c <> 0 then raise exception 'FAIL S5 58 visible to 59'; end if;
end $$;

-- S6 · nobody signed in: ignored
select set_config('test.uid','', false);
do $$ begin
  if mark_seen('at-work', 'ed-0003') <> 'ignored' then raise exception 'FAIL S6 anon wrote'; end if;
end $$;

reset role;
delete from agents where agent_no between 58 and 59;
delete from auth.users where id in ('58585858-5858-4585-8585-585858585858','59595959-5959-4595-8595-595959595959');
select 'M016 OK' as result;
