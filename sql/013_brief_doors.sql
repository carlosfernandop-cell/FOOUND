-- ============================================================================
-- FOOUND — Migration 013: the Working Brief doors (Move 2 — Person from Memory)
--
-- Today only №001 has a Working Brief, written into the database by hand.
-- A second client has no path to one: the app may INSERT a proposed brief
-- (005 policy) but nothing can make it ACTIVE, and commission_agent()
-- refuses without an active, confirmed, ready Brief. This migration adds the
-- two smallest doors that close that gap, and nothing else.
--
-- Search result (do not invent what exists):
--   · briefs: proposed -> active -> superseded / abandoned (005); one active
--     per agent enforced by partial unique index; confirmed_at is what
--     commission_agent() reads as "the client confirmed".
--   · The client may insert / edit PROPOSED rows only (005 RLS). There is
--     no activation function anywhere in repo or live project.
--   · jobs.type is a check constraint over four types (006); the client may
--     express synthesize / compile_brief / refresh_readiness (006 RLS).
--
-- What this adds:
--   · activate_brief(p_brief uuid) — client door. The authorization act.
--     Validates ownership, state = proposed, non-empty content; supersedes
--     the current active Brief (if any); sets state = active and
--     confirmed_at = now(); enqueues one compile_brief job so readiness is
--     computed by the engine (never inferred here). Returns 'active:v<N>'.
--     Never touches agents.state — commissioning stays its own door.
--   · jobs.type gains 'propose_brief' — the engine drafts a PROPOSED Brief
--     from confirmed Memory for the client to confirm. The client may
--     express it (RLS), the engine claims it (service role). Nothing in
--     this door makes Memory authority: a proposal is inert until
--     activate_brief is called by the client.
--
-- Confirmation is explicit and versioned; nothing here mutates an active
-- Brief in place. Paste-safe: no dollar-quoting. Idempotent: safe to re-run.
-- Prove on disposable Postgres only — never the live FOOUND project.
-- ============================================================================

do '
begin
  if to_regclass(''public.briefs'') is null then
    raise exception ''run_005_first: briefs not found'';
  end if;
  if to_regclass(''public.jobs'') is null then
    raise exception ''run_006_first: jobs not found'';
  end if;
end';

-- ---------------------------------------------------------------------------
-- jobs.type: add propose_brief (engine job; client may express it)
-- ---------------------------------------------------------------------------
alter table jobs drop constraint if exists jobs_type_check;
alter table jobs add constraint jobs_type_check
  check (type in ('synthesize','compile_brief','first_edition',
                  'refresh_readiness','propose_brief'));

drop policy if exists jobs_owner_insert on jobs;
create policy jobs_owner_insert on jobs
  for insert with check (
    status = 'queued'
    and started_at is null and completed_at is null and error is null
    and type in ('synthesize','compile_brief','refresh_readiness','propose_brief')
    and exists (select 1 from agents a
                where a.id = jobs.agent_id and a.user_id = auth.uid()));

-- ---------------------------------------------------------------------------
-- activate_brief: proposed -> active, by the client, atomically.
-- ---------------------------------------------------------------------------
create or replace function activate_brief(p_brief uuid) returns text
language plpgsql security definer set search_path = public as '
declare
  uid uuid;
  b briefs%rowtype;
  a agents%rowtype;
  n_old int := 0;
begin
  uid := auth.uid();
  if uid is null then return ''no_session''; end if;
  if p_brief is null then return ''blocked:no_brief''; end if;

  select * into b from briefs where id = p_brief for update;
  if b.id is null then return ''blocked:no_such_brief''; end if;

  select * into a from agents where id = b.agent_id for update;
  if a.id is null or a.user_id is distinct from uid then
    return ''blocked:not_owned'';
  end if;
  if a.state = ''archived'' then return ''blocked:state_archived''; end if;

  if b.state = ''active'' then return ''active:v'' || b.version; end if;
  if b.state <> ''proposed'' then return ''blocked:state_'' || b.state; end if;
  if b.content is null or jsonb_typeof(b.content) <> ''object''
     or coalesce(jsonb_array_length(b.content->''chapters''), 0) = 0 then
    return ''blocked:empty_brief'';
  end if;

  -- supersede the current active Brief, if any (never mutated in place:
  -- the row keeps its content; only its state moves)
  update briefs set state = ''superseded''
    where agent_id = b.agent_id and state = ''active'' and id <> b.id;
  get diagnostics n_old = row_count;

  update briefs
     set state = ''active'', confirmed_at = now()
   where id = b.id;

  -- readiness is the engine''s to compute; ask for it (idempotent: one
  -- queued compile per agent)
  begin
    insert into jobs (agent_id, type, payload)
    values (b.agent_id, ''compile_brief'',
            jsonb_build_object(''brief_version'', b.version,
                               ''superseded'', n_old));
  exception when unique_violation then
    null;
  end;

  return ''active:v'' || b.version;
end';
revoke execute on function activate_brief(uuid) from public, anon;
grant execute on function activate_brief(uuid) to authenticated;

-- ---------------------------------------------------------------------------
-- next_brief_version: the number a new proposal should carry (client or
-- engine). Read-only helper; unique (agent_id, version) still guards races.
-- ---------------------------------------------------------------------------
create or replace function next_brief_version(p_agent uuid) returns int
language sql stable security definer set search_path = public as '
  select coalesce(max(version), 0) + 1 from briefs where agent_id = p_agent
';
revoke execute on function next_brief_version(uuid) from public, anon;
grant execute on function next_brief_version(uuid) to authenticated;
