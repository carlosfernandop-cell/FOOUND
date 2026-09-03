\set ON_ERROR_STOP on
-- Verification for migration 014 (the database wakes the engine).
-- Fixtures: agent 54. Stubs from sql/dev/014_harness_stubs.sql. Never live.

delete from jobs where agent_id in (select id from agents where agent_no = 54);
delete from memory where agent_id in (select id from agents where agent_no = 54);
delete from agents where agent_no = 54;
delete from engine_wakes;
delete from net.net_calls;
delete from vault.decrypted_secrets;

insert into auth.users (id, email) values ('54545454-5454-4545-8545-545454545454','n054@example.com')
on conflict (id) do update set email = excluded.email;
insert into agents (id, user_id, agent_no, state) values
  ('a5454545-0000-4000-8000-000000000054','54545454-5454-4545-8545-545454545454',54,'mirror_ready');

-- W0 · no token: a wake is recorded as skipped, nothing is posted, nothing raises
do $$ declare r text; n int; begin
  r := wake_engine('test');
  if r <> 'skipped:no_token' then raise exception 'FAIL W0 return: %', r; end if;
  select count(*) into n from net.net_calls;
  if n <> 0 then raise exception 'FAIL W0 posted without token: %', n; end if;
end $$;

-- W1 · with a token: one POST to the heartbeat dispatch, bearer header, ref main, reason carried
insert into vault.decrypted_secrets (name, decrypted_secret) values ('github_dispatch_token', 'ghp_test_only');
do $$ declare r text; c record; begin
  r := wake_engine('clock');
  if r <> 'sent' then raise exception 'FAIL W1 return: %', r; end if;
  select * into c from net.net_calls order by id desc limit 1;
  if c.url not like 'https://api.github.com/repos/carlosfernandop-cell/FOOUND/actions/workflows/heartbeat.yml/dispatches' then
    raise exception 'FAIL W1 url: %', c.url; end if;
  if c.headers->>'Authorization' <> 'Bearer ghp_test_only' then raise exception 'FAIL W1 auth header'; end if;
  if c.body->>'ref' <> 'main' or c.body->'inputs'->>'reason' <> 'clock' then raise exception 'FAIL W1 body: %', c.body; end if;
end $$;

-- W2 · debounce: a second wake inside 45 s is skipped, and says so
do $$ declare r text; n int; begin
  r := wake_engine('job:synthesize');
  if r <> 'skipped:debounced' then raise exception 'FAIL W2 return: %', r; end if;
  select count(*) into n from net.net_calls;
  if n <> 1 then raise exception 'FAIL W2 posted twice: %', n; end if;
end $$;

-- W3 · a queued job wakes the engine (trigger), a non-queued row does not
update engine_wakes set at = at - interval '2 minutes';   -- let the debounce lapse
do $$ declare n int; begin
  insert into jobs (agent_id, type) values ('a5454545-0000-4000-8000-000000000054', 'synthesize');
  select count(*) into n from net.net_calls;
  if n <> 2 then raise exception 'FAIL W3 job insert did not wake: %', n; end if;
  if (select reason from engine_wakes where outcome = 'sent' order by id desc limit 1) <> 'job:synthesize' then
    raise exception 'FAIL W3 reason'; end if;
end $$;

-- W4 · a confirmed memory row wakes the engine; an extracted one does not
update engine_wakes set at = at - interval '2 minutes';
do $$ declare n int; begin
  insert into memory (agent_id, layer, statement, provenance, source, status)
    values ('a5454545-0000-4000-8000-000000000054', 'self', 'Berlin is home.', 'extracted', 'note', 'active');
  select count(*) into n from net.net_calls;
  if n <> 2 then raise exception 'FAIL W4 extracted row woke: %', n; end if;
  insert into memory (agent_id, layer, statement, provenance, source, status)
    values ('a5454545-0000-4000-8000-000000000054', 'self', 'Berlin is home.', 'confirmed', 'note', 'active');
  select count(*) into n from net.net_calls;
  if n <> 3 then raise exception 'FAIL W4 confirmed row did not wake: %', n; end if;
end $$;

-- W5 · the clock is scheduled exactly once, every quarter hour
do $$ declare n int; s text; begin
  select count(*), min(schedule) into n, s from cron.job where jobname = 'foound_wake_engine';
  if n <> 1 or s <> '*/15 * * * *' then raise exception 'FAIL W5 cron: % %', n, s; end if;
end $$;

-- W6 · clients cannot call wake_engine or read the ledger
do $$ begin
  if has_function_privilege('authenticated', 'wake_engine(text)', 'execute') then raise exception 'FAIL W6 exposed to authenticated'; end if;
  if has_function_privilege('anon', 'wake_engine(text)', 'execute') then raise exception 'FAIL W6 exposed to anon'; end if;
  if not (select relrowsecurity from pg_class where relname = 'engine_wakes') then raise exception 'FAIL W6 ledger without RLS'; end if;
end $$;

-- W7 · the token never lands in the ledger
do $$ begin
  if exists (select 1 from engine_wakes where reason like '%ghp_%' or outcome like '%ghp_%') then
    raise exception 'FAIL W7 token in ledger'; end if;
end $$;

-- cleanup (zero residue)
delete from jobs where agent_id in (select id from agents where agent_no = 54);
delete from memory where agent_id in (select id from agents where agent_no = 54);
delete from agents where agent_no = 54;
delete from auth.users where id = '54545454-5454-4545-8545-545454545454';
delete from engine_wakes;
delete from net.net_calls;
delete from vault.decrypted_secrets;

select 'M014 OK' as result;
