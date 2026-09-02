\set ON_ERROR_STOP on
-- Verification for migration 012 (invite record + first-session provision).
-- Fixtures: agent 1 is the owner door; 40-45 are invitees / isolation.
-- Never run against the live FOOUND project.

grant usage on schema public, auth to authenticated, anon;
grant select, insert, update, delete on all tables in schema public to authenticated;
grant execute on function invite_agent(text) to authenticated;
grant execute on function provision_agent() to authenticated;
grant execute on function auth.uid() to authenticated, anon;

delete from invitations where agent_no = 1 or agent_no between 40 and 45;
delete from agents where agent_no = 1 or agent_no between 40 and 45;

insert into auth.users (id, email) values
  ('01010101-0101-4101-8101-010101010101','owner@example.com'),
  ('40404040-4040-4404-8404-404040404040','spike@example.com'),
  ('41414141-4141-4414-8414-414141414141','fresh@example.com'),
  ('42424242-4242-4424-8424-424242424242','stranger@example.com'),
  ('43434343-4343-4434-8434-434343434343','other@example.com')
on conflict (id) do update set email = excluded.email;

-- №001-shaped owner: at_work, with memory / brief / edition that must not copy
insert into agents (id, user_id, agent_no, state) values
  ('a0101010-0000-4000-8000-000000000001','01010101-0101-4101-8101-010101010101',1,'at_work'),
  ('a4343434-0000-4000-8000-000000000043','43434343-4343-4434-8434-434343434343',43,'invited');

insert into memory (agent_id, layer, statement, provenance, source)
values ('a0101010-0000-4000-8000-000000000001','self','№001 private belief.','stated','owner');
insert into briefs (agent_id, version, state, content)
values ('a0101010-0000-4000-8000-000000000001', 1, 'active', '{"move":"001"}');
insert into editions (agent_id, edition_date, brief_version, html)
values ('a0101010-0000-4000-8000-000000000001', current_date, 1, '<html>001</html>');

-- I1 · non-owner cannot invite
set role authenticated;
select set_config('test.uid','43434343-4343-4434-8434-434343434343', false);
do $$ declare r text; begin
  r := invite_agent('spike@example.com');
  if r <> 'blocked:not_owner' then raise exception 'FAIL I1 not_owner: %', r; end if;
end $$;
reset role;
do $$ declare n int; begin
  select count(*) into n from invitations where lower(email) = 'spike@example.com';
  if n <> 0 then raise exception 'FAIL I1 leaked invite: %', n; end if;
end $$;

-- I2 · no session
select set_config('test.uid','', false);
set role authenticated;
do $$ declare r text; begin
  r := invite_agent('x@example.com');
  if r <> 'blocked:not_owner' then raise exception 'FAIL I2 no session: %', r; end if;
  r := provision_agent();
  if r <> 'no_session' then raise exception 'FAIL I2 provision: %', r; end if;
end $$;
reset role;

-- I3 · owner records invite; reserves next serial; does not INSERT agents
set role authenticated;
select set_config('test.uid','01010101-0101-4101-8101-010101010101', false);
do $$ declare r text; begin
  r := invite_agent('  Spike@Example.com  ');
  if r <> 'sent' then raise exception 'FAIL I3 return: %', r; end if;
  r := invite_agent('spike@example.com');
  if r <> 'sent' then raise exception 'FAIL I3 idempotent: %', r; end if;
end $$;
reset role;
do $$ declare n int; no int; em text; begin
  select count(*), min(agent_no), min(email) into n, no, em
    from invitations where lower(trim(email)) = 'spike@example.com' and status = 'sent';
  if n <> 1 then raise exception 'FAIL I3 invite count: %', n; end if;
  if no <> 44 then raise exception 'FAIL I3 reserved serial: %', no; end if;
  if em <> 'spike@example.com' then raise exception 'FAIL I3 stored email: %', em; end if;
  select count(*) into n from agents where user_id = '40404040-4040-4404-8404-404040404040';
  if n <> 0 then raise exception 'FAIL I3 invite inserted agent: %', n; end if;
end $$;

-- I4 · invalid email
set role authenticated;
select set_config('test.uid','01010101-0101-4101-8101-010101010101', false);
do $$ declare r text; begin
  r := invite_agent('not-an-email');
  if r <> 'blocked:invalid_email' then raise exception 'FAIL I4: %', r; end if;
  r := invite_agent('');
  if r <> 'blocked:invalid_email' then raise exception 'FAIL I4 empty: %', r; end if;
end $$;
reset role;

-- I5 · cannot invite an address that already has an agent
set role authenticated;
select set_config('test.uid','01010101-0101-4101-8101-010101010101', false);
do $$ declare r text; begin
  r := invite_agent('owner@example.com');
  if r <> 'blocked:already_agent' then raise exception 'FAIL I5 owner: %', r; end if;
  r := invite_agent('other@example.com');
  if r <> 'blocked:already_agent' then raise exception 'FAIL I5 other: %', r; end if;
end $$;
reset role;

-- I6 · uninvited auth user cannot become an agent (spike-shaped identity)
set role authenticated;
select set_config('test.uid','42424242-4242-4424-8424-424242424242', false);
do $$ declare r text; n int; begin
  r := provision_agent();
  if r <> 'blocked:not_invited' then raise exception 'FAIL I6: %', r; end if;
  select count(*) into n from agents where user_id = '42424242-4242-4424-8424-424242424242';
  if n <> 0 then raise exception 'FAIL I6 inserted: %', n; end if;
end $$;
reset role;

-- I7 · spike: existing auth user, no agent — provision attaches that user_id
set role authenticated;
select set_config('test.uid','40404040-4040-4404-8404-404040404040', false);
do $$ declare r text; n int; no int; s text; begin
  r := provision_agent();
  if r <> 'invited' then raise exception 'FAIL I7 return: %', r; end if;
  r := provision_agent();
  if r <> 'invited' then raise exception 'FAIL I7 idempotent: %', r; end if;
  select count(*), min(agent_no), min(state) into n, no, s
    from agents where user_id = '40404040-4040-4404-8404-404040404040';
  if n <> 1 then raise exception 'FAIL I7 agent count: %', n; end if;
  if no <> 44 then raise exception 'FAIL I7 used reserved serial: %', no; end if;
  if s <> 'invited' then raise exception 'FAIL I7 state: %', s; end if;
end $$;
reset role;
do $$ declare n int; mem int; br int; ed int; begin
  select count(*) into n from invitations
    where lower(email) = 'spike@example.com' and status = 'accepted' and agent_no = 44;
  if n <> 1 then raise exception 'FAIL I7 invite not accepted: %', n; end if;
  select count(*) into mem from memory
    where agent_id = (select id from agents where agent_no = 44);
  select count(*) into br from briefs
    where agent_id = (select id from agents where agent_no = 44);
  select count(*) into ed from editions
    where agent_id = (select id from agents where agent_no = 44);
  if mem <> 0 or br <> 0 or ed <> 0 then
    raise exception 'FAIL I7 copied 001 rows mem=% br=% ed=%', mem, br, ed;
  end if;
end $$;

-- I8 · later invitee still provisions on first session (existing auth identity)
set role authenticated;
select set_config('test.uid','01010101-0101-4101-8101-010101010101', false);
do $$ declare r text; begin
  r := invite_agent('fresh@example.com');
  if r <> 'sent' then raise exception 'FAIL I8 invite: %', r; end if;
end $$;
reset role;
do $$ declare no int; begin
  select agent_no into no from invitations
    where lower(email) = 'fresh@example.com' and status = 'sent';
  if no <> 45 then raise exception 'FAIL I8 next serial: %', no; end if;
end $$;
set role authenticated;
select set_config('test.uid','41414141-4141-4414-8414-414141414141', false);
do $$ declare r text; n int; s text; begin
  r := provision_agent();
  if r <> 'invited' then raise exception 'FAIL I8 provision: %', r; end if;
  select count(*), min(state) into n, s
    from agents where user_id = '41414141-4141-4414-8414-414141414141';
  if n <> 1 or s <> 'invited' then raise exception 'FAIL I8 agent: % %', n, s; end if;
end $$;
reset role;

-- I9 · authenticated cannot raw-INSERT agents (RLS)
set role authenticated;
select set_config('test.uid','42424242-4242-4424-8424-424242424242', false);
do $$ begin
  begin
    insert into agents (user_id, agent_no, state)
    values ('42424242-4242-4424-8424-424242424242', 41, 'invited');
    raise exception 'FAIL I9: raw agents insert accepted';
  exception when insufficient_privilege or check_violation then null;
  end;
end $$;
reset role;

-- I10 · anon refused both doors
set role anon;
select set_config('test.uid','01010101-0101-4101-8101-010101010101', false);
do $$ begin
  begin
    perform invite_agent('anon@example.com');
    raise exception 'FAIL I10: anon executed invite';
  exception when insufficient_privilege then null; end;
  begin
    perform provision_agent();
    raise exception 'FAIL I10: anon executed provision';
  exception when insufficient_privilege then null; end;
end $$;
reset role;

-- I11 · №001 frozen: state, serial, and private rows untouched
do $$ declare s text; n int; mem int; br int; ed int; begin
  select state into s from agents where agent_no = 1;
  if s <> 'at_work' then raise exception 'FAIL I11 state moved: %', s; end if;
  select count(*) into n from agents where agent_no = 1;
  if n <> 1 then raise exception 'FAIL I11 agent 1 count: %', n; end if;
  select count(*) into mem from memory
    where agent_id = 'a0101010-0000-4000-8000-000000000001';
  select count(*) into br from briefs
    where agent_id = 'a0101010-0000-4000-8000-000000000001';
  select count(*) into ed from editions
    where agent_id = 'a0101010-0000-4000-8000-000000000001';
  if mem <> 1 or br <> 1 or ed <> 1 then
    raise exception 'FAIL I11 001 rows moved mem=% br=% ed=%', mem, br, ed;
  end if;
end $$;

select 'M012 OK: owner-only invite, reserved serial, spike attach to existing auth user, uninvited refused, no 001 copy, raw insert blocked, anon refused, invited birth state';

-- cleanup
delete from invitations where agent_no = 1 or agent_no between 40 and 45;
delete from agents where agent_no = 1 or agent_no between 40 and 45;
