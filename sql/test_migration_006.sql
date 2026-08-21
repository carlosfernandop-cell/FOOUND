\set ON_ERROR_STOP on
grant select, insert, update, delete on jobs to app_user;
grant execute on all functions in schema public to app_user;
delete from agents where agent_no in (94,95);
insert into auth.users (id, email) values
  ('44444444-4444-4444-4444-444444444444','four@example.com'),
  ('55555555-5555-5555-5555-555555555555','five@example.com')
on conflict do nothing;
insert into agents (id, user_id, agent_no, state) values
  ('a4444444-0000-0000-0000-000000000004','44444444-4444-4444-4444-444444444444',94,'mirror_ready'),
  ('a5555555-0000-0000-0000-000000000005','55555555-5555-5555-5555-555555555555',95,'feed_submitted');
insert into briefs (agent_id, version, state, content, readiness, confirmed_at)
values ('a4444444-0000-0000-0000-000000000004', 1, 'active', '{"move":"x"}', 'ready', now());

set role app_user;
select set_config('test.uid','44444444-4444-4444-4444-444444444444', false);

-- 1. client can express allowed intent; duplicate queue attempt collapses
insert into jobs (agent_id, type) values ('a4444444-0000-0000-0000-000000000004','synthesize');
do $$ begin
  begin
    insert into jobs (agent_id, type) values ('a4444444-0000-0000-0000-000000000004','synthesize');
    raise exception 'FAIL: duplicate queued intent accepted';
  exception when unique_violation then null; end;
end $$;

-- 2. client cannot mint first_edition directly
do $$ begin
  begin
    insert into jobs (agent_id, type) values ('a4444444-0000-0000-0000-000000000004','first_edition');
    raise exception 'FAIL: client inserted first_edition';
  exception when insufficient_privilege or check_violation then null; end;
end $$;

-- 3. commission flips state AND enqueues first_edition atomically; idempotent
do $$ declare r text; n int; begin
  r := commission_agent();
  if r <> 'at_work' then raise exception 'FAIL commission: %', r; end if;
  select count(*) into n from jobs where type='first_edition';
  if n <> 1 then raise exception 'FAIL first_edition jobs: %', n; end if;
  r := commission_agent();
  select count(*) into n from jobs where type='first_edition';
  if n <> 1 then raise exception 'FAIL idempotent first_edition: %', n; end if;
end $$;

-- 4. client cannot run/complete jobs: no UPDATE policy means RLS filters the
-- write to zero rows — verify nothing changed
do $$ declare n int; begin
  update jobs set status='done' where type='synthesize';
  select count(*) into n from jobs where type='synthesize' and status='done';
  if n <> 0 then raise exception 'FAIL: job status mutated by client'; end if;
end $$;

-- 5. cross-user: user 5 sees nothing of user 4's jobs, cannot insert for them
select set_config('test.uid','55555555-5555-5555-5555-555555555555', false);
do $$ declare n int; begin
  select count(*) into n from jobs;
  if n <> 0 then raise exception 'FAIL RLS jobs visible cross-user: %', n; end if;
  begin
    insert into jobs (agent_id, type) values ('a4444444-0000-0000-0000-000000000004','synthesize');
    raise exception 'FAIL: cross-user intent accepted';
  exception when insufficient_privilege or check_violation then null; end;
end $$;
reset role;

select 'M006 OK: intent-only inserts, no duplicate queues, atomic commission+first_edition, engine-only completion, cross-user isolation';
grant select, insert, update, delete on jobs to app_user;
grant execute on all functions in schema public to app_user;
delete from agents where agent_no in (94,95);
