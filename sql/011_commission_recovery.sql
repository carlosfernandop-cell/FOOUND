-- ============================================================================
-- FOOUND — Migration 011: commission_agent at_work recovery (first-real-hunt)
--
-- Narrow replace of public.commission_agent(). Zero table changes, zero
-- column changes, zero new RPCs, zero new job types.
--
-- If the agent is already at_work AND the active Brief readiness is ready
-- AND editions count for that agent is 0 AND there is no queued/running
-- first_edition job AND no done first_edition job → INSERT first_edition
-- (payload brief_version) and return 'at_work'. No state reset. No
-- readiness bypass.
--
-- If a first_edition already exists (queued/running/done) or an editions
-- row exists, keep today's no-op (return 'at_work', insert nothing).
-- A prior failed first_edition does not block recovery.
--
-- Non-at_work paths are unchanged from 006: active confirmed brief;
-- null/not_ready → blocked:market_not_ready; limited-unacknowledged gate
-- preserved. This slice does not invent new limited-ack behavior.
--
-- Paste-safe: no dollar-quoting. Idempotent: CREATE OR REPLACE.
-- Run AFTER 006 (function exists). Do not apply to production from this PR.
-- ============================================================================

do '
begin
  if to_regprocedure(''commission_agent()'') is null then
    raise exception ''run_006_first: commission_agent() not found'';
  end if;
end';

create or replace function commission_agent() returns text
language plpgsql security definer set search_path = public as '
declare
  a agents%rowtype;
  b briefs%rowtype;
  n_ed int;
begin
  select * into a from agents where user_id = auth.uid()
    order by agent_no limit 1;
  if a.id is null then return ''no_agent''; end if;

  -- ---- already at_work: recovery or no-op. Never reset state.
  if a.state = ''at_work'' then
    select count(*) into n_ed from editions where agent_id = a.id;
    if n_ed > 0 then return ''at_work''; end if;
    if exists (
      select 1 from jobs
       where agent_id = a.id and type = ''first_edition''
         and status in (''queued'',''running'',''done'')
    ) then
      return ''at_work'';
    end if;
    select * into b from briefs
      where agent_id = a.id and state = ''active'' limit 1;
    -- No readiness bypass: only recover when the Brief is ready.
    if b.id is null or b.readiness is distinct from ''ready'' then
      return ''at_work'';
    end if;
    insert into jobs (agent_id, type, payload)
      values (a.id, ''first_edition'',
              jsonb_build_object(''brief_version'', b.version))
      on conflict do nothing;
    return ''at_work'';
  end if;

  -- ---- non-at_work: existing 006 gates, unchanged
  if a.state not in (''mirror_ready'',''commissioned'',''awaiting_confirmation'') then
    return ''blocked:state_'' || a.state;
  end if;
  select * into b from briefs
    where agent_id = a.id and state = ''active'' limit 1;
  if b.id is null or b.confirmed_at is null then
    return ''blocked:no_confirmed_brief'';
  end if;
  if b.readiness is null or b.readiness = ''not_ready'' then
    return ''blocked:market_not_ready'';
  end if;
  if b.readiness = ''limited''
     and (b.readiness_ack is null or length(b.readiness_ack) = 0) then
    return ''blocked:limited_unacknowledged'';
  end if;
  update agents set state = ''at_work'' where id = a.id;
  insert into jobs (agent_id, type, payload)
    values (a.id, ''first_edition'',
            jsonb_build_object(''brief_version'', b.version))
    on conflict do nothing;
  return ''at_work'';
end';

revoke execute on function commission_agent() from public;
grant  execute on function commission_agent() to authenticated;
