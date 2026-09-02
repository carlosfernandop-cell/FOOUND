-- ============================================================================
-- FOOUND — Migration 012: invite record + first-session agent provision
--
-- Door 1 companion. Smallest server path so an invited auth user gets an
-- agents row without a Carlos SQL write. №001 is frozen.
--
-- Search result (do not invent what exists):
--   · public.invitations already exists (005): email, reserved agent_no,
--     status sent/accepted/expired, RLS on, no client policies.
--   · No invite / provision / accept RPC exists in repo or live project.
--   · agents RLS is owner SELECT only — the app cannot INSERT agents.
--   · commission_agent / hunt / synthesis are later doors — untouched.
--
-- What this adds, and nothing else:
--   · invite_agent(email) — owner / agent_no=1 only. Records a sent
--     invitation and reserves the next serial. Does not INSERT agents.
--     Does not copy №001 memory / briefs / editions.
--   · provision_agent() — first authenticated session for that email.
--     INSERT agents (user_id, reserved agent_no, state='invited').
--     Attaches an existing auth identity (spike user) — does not mint a
--     second auth.users row. Uninvited emails get blocked:not_invited.
--
-- New agents are born invited (005 lifecycle start). Production's column
-- default is still at_work (№001 leftover) — this door sets invited
-- explicitly. at_work is never written here.
--
-- Paste-safe: no dollar-quoting. Idempotent: CREATE OR REPLACE.
-- Prove on disposable Postgres only — never the live FOOUND project.
-- ============================================================================

do '
begin
  if to_regclass(''public.invitations'') is null then
    raise exception ''run_005_first: invitations not found'';
  end if;
  if to_regclass(''public.agents'') is null then
    raise exception ''run_base_first: agents not found'';
  end if;
end';

-- One pending invite per email. Reserved serial stays unique (005).
create unique index if not exists invitations_one_sent_per_email
  on invitations (lower(trim(email))) where status = 'sent';

create or replace function invite_agent(p_email text) returns text
language plpgsql security definer set search_path = public as '
declare
  a agents%rowtype;
  v_email text;
  v_no int;
begin
  select * into a from agents
    where user_id = auth.uid() and agent_no = 1
    limit 1;
  if a.id is null then return ''blocked:not_owner''; end if;

  v_email := lower(trim(coalesce(p_email, '''')));
  if v_email = '''' or v_email !~ ''^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$''
     or length(v_email) > 254 then
    return ''blocked:invalid_email'';
  end if;

  if exists (
    select 1 from agents ag
      join auth.users u on u.id = ag.user_id
     where lower(trim(u.email)) = v_email
  ) then
    return ''blocked:already_agent'';
  end if;

  if exists (
    select 1 from invitations
     where lower(trim(email)) = v_email and status = ''sent''
  ) then
    return ''sent'';
  end if;

  select coalesce(max(n), 0) + 1 into v_no from (
    select agent_no as n from agents
    union all
    select agent_no as n from invitations
  ) s;

  begin
    insert into invitations (email, agent_no, status)
      values (v_email, v_no, ''sent'');
  exception when unique_violation then
    if exists (
      select 1 from invitations
       where lower(trim(email)) = v_email and status = ''sent''
    ) then
      return ''sent'';
    end if;
    raise;
  end;
  return ''sent'';
end';

create or replace function provision_agent() returns text
language plpgsql security definer set search_path = public as '
declare
  a agents%rowtype;
  inv invitations%rowtype;
  v_uid uuid;
  v_email text;
begin
  v_uid := auth.uid();
  if v_uid is null then return ''no_session''; end if;

  select * into a from agents where user_id = v_uid
    order by agent_no limit 1;
  if a.id is not null then return a.state; end if;

  select lower(trim(email)) into v_email
    from auth.users where id = v_uid;
  if v_email is null or v_email = '''' then
    return ''blocked:not_invited'';
  end if;

  select * into inv from invitations
    where lower(trim(email)) = v_email and status = ''sent''
    order by created_at
    limit 1;
  if inv.id is null then return ''blocked:not_invited''; end if;

  begin
    insert into agents (user_id, agent_no, state)
      values (v_uid, inv.agent_no, ''invited'')
      returning * into a;
  exception when unique_violation then
    select * into a from agents where user_id = v_uid
      order by agent_no limit 1;
    if a.id is not null then return a.state; end if;
    raise;
  end;

  update invitations set status = ''accepted'' where id = inv.id;
  return a.state;
end';

revoke execute on function invite_agent(text) from public, anon;
revoke execute on function provision_agent() from public, anon;
grant  execute on function invite_agent(text) to authenticated;
grant  execute on function provision_agent() to authenticated;
