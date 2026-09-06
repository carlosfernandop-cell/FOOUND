-- ============================================================================
-- FOOUND — Migration 017: edition write authority, settled at the write
--
-- Why: _first_edition reads the active Brief, hunts for minutes, then POSTs
-- the edition under the service role with the pre-hunt brief_version. Nothing
-- at write time checks that the agent is still at work or that the Brief is
-- still the one in force. A pause or a new Brief during the hunt lands an
-- edition the person did not authorize (LAW 2, LAW 3). A Python re-read
-- before the write narrows the window; only the database closes it.
--
-- Search result (do not invent what exists):
--   · editions (005): unique (agent_id, edition_date); owner READ policy;
--     no trigger; the service role bypasses RLS but not triggers (007 guard).
--   · activate_brief (013) locks the REQUESTED Brief P for update first, then
--     agent A for update, then updates the active Brief B. When P is already
--     the active Brief (re-activation), that order is B then A. pause_agent /
--     resume_agent / commission_agent (005, 011) lock A only.
--
-- Lock protocol (revised after reciprocal review, 2026-09-06): the guard
-- holds ONE row lock, the agent FOR SHARE. It reads the active Brief without
-- a row lock. A guard that also locked B (v1: A then B) deadlocked against
-- re-activation of the already active Brief (B then A); no fixed two-lock
-- order is safe against a door that takes P, A, B for a proposed Brief and
-- B, A for the active one. With one lock there is no cycle: every door waits
-- on A, and the guard waits on nothing once it holds A.
--
-- Why the unlocked read is still settled: under READ COMMITTED each
-- statement in the function takes a fresh snapshot. The Brief read starts
-- after FOR SHARE on A returned, so any activate_brief that had reached A
-- has committed and the read sees its supersession. An activate_brief that
-- holds P but has not reached A waits for this write to commit; the
-- edition then carries the Brief that was in force at the moment it was
-- written, and enqueue_daily rewrites the day from the new Brief.
--
-- What this adds, and nothing else:
--   · guard_edition_authority(): BEFORE INSERT OR UPDATE on editions.
--     Raises authority_changed:<reason> unless A.state = at_work, an active
--     Brief exists, it is confirmed, and NEW.brief_version = B.version.
--     Reasons: no_agent | paused | state_<s> | no_active_brief |
--     brief_unconfirmed | no_version | brief_superseded.
--   · Editions carrying a NULL brief_version are refused (no_version): an
--     edition without its authority is not an edition. An active Brief with
--     no confirmed_at does not authorize (brief_unconfirmed).
--
-- Engine consequence (separate patch): _first_edition completes the job
-- with reason authority_changed on refusal; enqueue_daily re-queues when
-- authority is restored and treats a same-day edition from an older Brief
-- as no edition.
--
-- Paste-safe: no dollar-quoting. Idempotent: safe to re-run.
-- Prove on disposable Postgres only — never the live FOOUND project.
-- ============================================================================

do '
begin
  if to_regclass(''public.editions'') is null then
    raise exception ''run_005_first: editions not found'';
  end if;
  if to_regclass(''public.briefs'') is null then
    raise exception ''run_005_first: briefs not found'';
  end if;
end';

create or replace function guard_edition_authority() returns trigger
language plpgsql security definer set search_path = public as '
declare a_state text;
        b_version int;
        b_confirmed timestamptz;
begin
  -- the one lock: the agent, shared. Serialises against pause / resume /
  -- commission and against activate_brief once it holds A. Nothing else is
  -- locked, so no door can wait on this write while this write waits on it.
  select state into a_state from agents where id = NEW.agent_id for share;
  if a_state is null then
    raise exception ''authority_changed:no_agent'';
  end if;
  if a_state = ''paused'' then
    raise exception ''authority_changed:paused'';
  end if;
  if a_state <> ''at_work'' then
    raise exception ''authority_changed:state_%'', a_state;
  end if;
  -- the Brief in force, read without a row lock, on a snapshot taken after
  -- the agent lock was granted (read committed): committed truth.
  select version, confirmed_at into b_version, b_confirmed from briefs
    where agent_id = NEW.agent_id and state = ''active''
    order by version desc limit 1;
  if b_version is null then
    raise exception ''authority_changed:no_active_brief'';
  end if;
  if b_confirmed is null then
    raise exception ''authority_changed:brief_unconfirmed'';
  end if;
  if NEW.brief_version is null then
    raise exception ''authority_changed:no_version'';
  end if;
  if NEW.brief_version <> b_version then
    raise exception ''authority_changed:brief_superseded'';
  end if;
  return NEW;
end';

drop trigger if exists editions_guard_authority on editions;
create trigger editions_guard_authority
  before insert or update on editions
  for each row execute function guard_edition_authority();
