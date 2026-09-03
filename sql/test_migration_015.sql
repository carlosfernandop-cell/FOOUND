\set ON_ERROR_STOP on
-- Verification for migration 015 (the Candidate doors).
-- Fixtures: agents 55-57. Never run against the live FOOUND project.

grant usage on schema public, auth, storage to authenticated, anon;
grant select, insert, update, delete on all tables in schema public to authenticated;
grant select on candidates, agents to anon;   -- Supabase grants anon select; RLS is what must hold
grant select, insert, update on storage.objects to authenticated, anon;
grant select on storage.buckets to authenticated, anon;
grant execute on function publish_candidate(uuid, jsonb) to authenticated;
grant execute on function unpublish_candidate() to authenticated;
grant execute on function candidate_public(text) to anon, authenticated;
grant execute on function next_candidate_version(uuid) to authenticated;
grant execute on function auth.uid() to authenticated, anon;

delete from jobs where agent_id in (select id from agents where agent_no between 55 and 57);
delete from candidates where agent_id in (select id from agents where agent_no between 55 and 57);
delete from storage.objects where bucket_id = 'portraits';
delete from agents where agent_no between 55 and 57;

insert into auth.users (id, email) values
  ('55555555-5555-4555-8555-555555555555','n055@example.com'),
  ('56565656-5656-4565-8565-565656565656','other@example.com')
on conflict (id) do update set email = excluded.email;
insert into agents (id, user_id, agent_no, state) values
  ('a5555555-0000-4000-8000-000000000055','55555555-5555-4555-8555-555555555555',55,'at_work'),
  ('a5656565-0000-4000-8000-000000000056','56565656-5656-4565-8565-565656565656',56,'mirror_ready');

-- C0 · nothing public before anything is published
do $$ declare j jsonb; begin
  j := candidate_public('055');
  if j is not null then raise exception 'FAIL C0 public before publish: %', j; end if;
end $$;

-- the engine drafts (service role): version 1, page from confirmed Memory
insert into candidates (id, agent_id, version, state, page) values
  ('c0000000-0000-4000-8000-000000000001','a5555555-0000-4000-8000-000000000055', 1, 'draft',
   '{"name":["Mara","Lindqvist"],"line":"This is the candidate I work for.","now":"Head of Product Design, Northwind","based":"Berlin","since":"2012",
     "chapters":[{"company":"Northwind","years":"{2022–}","at_rest":"Head of Product Design","narrative":"Hired as the first design leader."}],
     "trusted_with":[{"word":"The first hire","line":"Twice."}],"links":{"linkedin":"https://linkedin.com/in/x"}}');

-- C1 · the owner may edit the draft (page), a stranger may not; nobody may edit it into published by row
set role authenticated;
select set_config('test.uid','55555555-5555-4555-8555-555555555555', false);
update candidates set page = page || '{"own_words":"Only products still being decided."}'
  where id = 'c0000000-0000-4000-8000-000000000001';
do $$ declare t text; begin
  select page->>'own_words' into t from candidates where id = 'c0000000-0000-4000-8000-000000000001';
  if t is null then raise exception 'FAIL C1 owner edit'; end if;
  begin
    update candidates set state = 'published' where id = 'c0000000-0000-4000-8000-000000000001';
    raise exception 'FAIL C1 row edit to published allowed';
  exception when insufficient_privilege or check_violation then null;
             when raise_exception then raise;
  end;
end $$;
select set_config('test.uid','56565656-5656-4565-8565-565656565656', false);
do $$ declare n int; begin
  update candidates set page = page || '{"own_words":"stranger"}' where id = 'c0000000-0000-4000-8000-000000000001';
  get diagnostics n = row_count;
  if n <> 0 then raise exception 'FAIL C1 stranger edited: %', n; end if;
end $$;

-- C2 · publishing needs a portrait; a stranger cannot publish; open_to is refused
select set_config('test.uid','55555555-5555-4555-8555-555555555555', false);
do $$ declare r text; begin
  r := publish_candidate('c0000000-0000-4000-8000-000000000001', null);
  if r <> 'blocked:no_portrait' then raise exception 'FAIL C2 no portrait: %', r; end if;
  r := publish_candidate('c0000000-0000-4000-8000-000000000001',
        (select page from candidates where id = 'c0000000-0000-4000-8000-000000000001')
        || '{"portrait":"https://x/portraits/u/portrait.jpg","open_to":"London"}');
  if r <> 'blocked:open_to_is_private' then raise exception 'FAIL C2 open_to: %', r; end if;
end $$;
select set_config('test.uid','56565656-5656-4565-8565-565656565656', false);
do $$ declare r text; begin
  r := publish_candidate('c0000000-0000-4000-8000-000000000001', null);
  if r <> 'blocked:not_owned' then raise exception 'FAIL C2 stranger: %', r; end if;
end $$;

-- C3 · the owner publishes with a portrait: slug is the serial, public read works, page is exact
select set_config('test.uid','55555555-5555-4555-8555-555555555555', false);
do $$ declare r text; j jsonb; st text; begin
  r := publish_candidate('c0000000-0000-4000-8000-000000000001',
        (select page from candidates where id = 'c0000000-0000-4000-8000-000000000001')
        || '{"portrait":"https://x/portraits/u/portrait.jpg"}');
  if r <> 'published:055' then raise exception 'FAIL C3 return: %', r; end if;
  select state into st from candidates where id = 'c0000000-0000-4000-8000-000000000001';
  if st <> 'published' then raise exception 'FAIL C3 state: %', st; end if;
  j := candidate_public('055');
  if j->>'serial' <> '055' or j->'page'->>'line' <> 'This is the candidate I work for.'
     or j->'page'->>'own_words' <> 'Only products still being decided.' then
    raise exception 'FAIL C3 public page: %', j; end if;
  if j ? 'agent_id' or j ? 'content' then raise exception 'FAIL C3 leaks row fields'; end if;
  r := publish_candidate('c0000000-0000-4000-8000-000000000001', null);
  if r <> 'published:055' then raise exception 'FAIL C3 idempotent: %', r; end if;
end $$;

-- C4 · anon reads the public page and nothing else
set role anon;
select set_config('test.uid','', false);
do $$ declare j jsonb; n int; begin
  j := candidate_public('055');
  if j->>'serial' <> '055' then raise exception 'FAIL C4 anon read: %', j; end if;
  if candidate_public('056') is not null then raise exception 'FAIL C4 unpublished visible'; end if;
  select count(*) into n from candidates;
  if n <> 0 then raise exception 'FAIL C4 anon sees rows: %', n; end if;
end $$;

-- C5 · a new draft published supersedes the old page: one published per serial
set role authenticated;
select set_config('test.uid','55555555-5555-4555-8555-555555555555', false);
do $$ declare v int; r text; n int; j jsonb; begin
  v := next_candidate_version('a5555555-0000-4000-8000-000000000055');
  if v <> 2 then raise exception 'FAIL C5 next version: %', v; end if;
  insert into candidates (id, agent_id, version, state, page) values
    ('c0000000-0000-4000-8000-000000000002','a5555555-0000-4000-8000-000000000055', 2, 'draft',
     (select page from candidates where id = 'c0000000-0000-4000-8000-000000000001') || '{"since":"2011"}');
  r := publish_candidate('c0000000-0000-4000-8000-000000000002', null);
  if r <> 'published:055' then raise exception 'FAIL C5 publish v2: %', r; end if;
  select count(*) into n from candidates where agent_id = 'a5555555-0000-4000-8000-000000000055' and state = 'published';
  if n <> 1 then raise exception 'FAIL C5 published count: %', n; end if;
  j := candidate_public('055');
  if j->'page'->>'since' <> '2011' then raise exception 'FAIL C5 public is old: %', j; end if;
  select state into r from candidates where id = 'c0000000-0000-4000-8000-000000000001';
  if r <> 'unpublished' then raise exception 'FAIL C5 old not unpublished: %', r; end if;
end $$;

-- C6 · the person takes the page down; nothing public; the row keeps its page
do $$ declare r text; j jsonb; p jsonb; begin
  r := unpublish_candidate();
  if r <> 'unpublished' then raise exception 'FAIL C6 return: %', r; end if;
  j := candidate_public('055');
  if j is not null then raise exception 'FAIL C6 still public'; end if;
  select page into p from candidates where id = 'c0000000-0000-4000-8000-000000000002';
  if p is null then raise exception 'FAIL C6 page lost'; end if;
  r := unpublish_candidate();
  if r <> 'nothing_published' then raise exception 'FAIL C6 idempotent: %', r; end if;
end $$;

-- C7 · the client may express draft_candidate; the constraint allows it
do $$ begin
  insert into jobs (agent_id, type) values ('a5555555-0000-4000-8000-000000000055', 'draft_candidate');
end $$;

-- C8 · the portrait: owner writes <uid>/portrait.jpg only; anyone reads; bucket is public, jpeg only
do $$ begin
  insert into storage.objects (bucket_id, name, owner) values
    ('portraits', '55555555-5555-4555-8555-555555555555/portrait.jpg', '55555555-5555-4555-8555-555555555555');
  begin
    insert into storage.objects (bucket_id, name, owner) values
      ('portraits', '56565656-5656-4565-8565-565656565656/portrait.jpg', '55555555-5555-4555-8555-555555555555');
    raise exception 'FAIL C8 foreign prefix allowed';
  exception when insufficient_privilege or check_violation then null;
             when raise_exception then raise;
  end;
  begin
    insert into storage.objects (bucket_id, name, owner) values
      ('portraits', '55555555-5555-4555-8555-555555555555/anything.png', '55555555-5555-4555-8555-555555555555');
    raise exception 'FAIL C8 other filename allowed';
  exception when insufficient_privilege or check_violation then null;
             when raise_exception then raise;
  end;
end $$;
set role anon;
select set_config('test.uid','', false);
do $$ declare n int; b record; begin
  select count(*) into n from storage.objects where bucket_id = 'portraits';
  if n <> 1 then raise exception 'FAIL C8 anon cannot read portrait: %', n; end if;
  select * into b from storage.buckets where id = 'portraits';
  if not b.public or b.allowed_mime_types <> array['image/jpeg']::text[] then raise exception 'FAIL C8 bucket config'; end if;
end $$;
reset role;

-- C9 · a stranger cannot call the doors on someone else's page; anon cannot publish
do $$ begin
  if has_function_privilege('anon', 'publish_candidate(uuid, jsonb)', 'execute') then raise exception 'FAIL C9 anon can publish'; end if;
  if has_function_privilege('anon', 'unpublish_candidate()', 'execute') then raise exception 'FAIL C9 anon can unpublish'; end if;
  if not has_function_privilege('anon', 'candidate_public(text)', 'execute') then raise exception 'FAIL C9 anon cannot read'; end if;
end $$;

-- cleanup (zero residue)
delete from jobs where agent_id in (select id from agents where agent_no between 55 and 57);
delete from candidates where agent_id in (select id from agents where agent_no between 55 and 57);
delete from storage.objects where bucket_id = 'portraits';
delete from agents where agent_no between 55 and 57;
delete from auth.users where id in ('55555555-5555-4555-8555-555555555555','56565656-5656-4565-8565-565656565656');

select 'M015 OK' as result;
