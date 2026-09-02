-- LOCAL HARNESS ONLY. Policies that production 001-003 already have
-- (those migrations are not in this repo). Required so authenticated
-- can read their own agents row during 012 tests. NEVER run on Supabase.

drop policy if exists agents_owner_read on agents;
create policy agents_owner_read on agents
  for select using (user_id = auth.uid());

grant usage on schema public, auth to authenticated, anon;
grant select, insert, update, delete on all tables in schema public to authenticated;
grant execute on all functions in schema public to authenticated;
grant execute on function auth.uid() to authenticated, anon;
grant select on auth.users to postgres;
