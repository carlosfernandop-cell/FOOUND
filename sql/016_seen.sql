-- ============================================================================
-- FOOUND — Migration 016: "since you last looked" lives on the agent
--
-- Why: At Work replays what arrived since the person last looked (the fills,
-- the count, the return). For the private test that moment was kept in the
-- browser, so a phone and a desktop each had their own truth. The product's
-- version is one fact per room on the agent row, so the return is truthful
-- everywhere the person signs in.
--
-- Search result (do not invent what exists):
--   · agents (001-005): id, user_id, agent_no, state; owner READ policy only.
--     No owner UPDATE policy; every owner write goes through a door.
--
-- What this adds, and nothing else:
--   · agents.seen jsonb — {"at-work": "<edition id or ISO time>", ...}.
--     Read by the owner with the row they already read.
--   · mark_seen(p_room text, p_value text) — the owner's one write. Rooms are
--     the four rooms; anything else is ignored, never raised to the client.
--     Returns 'seen:<room>' or 'ignored'.
--
-- Paste-safe: no dollar-quoting. Idempotent: safe to re-run.
-- Prove on disposable Postgres only — never the live FOOUND project.
-- ============================================================================

do '
begin
  if to_regclass(''public.agents'') is null then
    raise exception ''agents not found'';
  end if;
end';

alter table agents add column if not exists seen jsonb not null default '{}'::jsonb;

create or replace function mark_seen(p_room text, p_value text)
returns text
language plpgsql
security definer
set search_path = public
as '
declare n int;
begin
  if p_room is null or p_room not in (''at-work'',''brief'',''memory'',''candidate'') then
    return ''ignored'';
  end if;
  if p_value is null or length(p_value) = 0 or length(p_value) > 64 then
    return ''ignored'';
  end if;
  update agents
     set seen = coalesce(seen, ''{}''::jsonb) || jsonb_build_object(p_room, p_value)
   where user_id = auth.uid();
  get diagnostics n = row_count;
  if n = 0 then return ''ignored''; end if;
  return ''seen:'' || p_room;
end';

revoke all on function mark_seen(text, text) from public;
grant execute on function mark_seen(text, text) to authenticated;
