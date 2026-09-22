-- ============================================================================
-- FOOUND — Migration 018 ROLLBACK (synthesis request boundary)
--
-- Restores claim_synthesis_batch(uuid) exactly as migration 007 defines it
-- (verbatim copy below) and drops the 018 helper. Nothing else: 018 added no
-- table, column, policy or grant.
--
-- CONSEQUENCE, read before running: a queued synthesize job that 018 left
-- waiting for files (status queued, started_at not null) becomes an ordinary
-- queued job to 007's door and is claimed on the next beat with 007's scope:
-- every received row of the agent, including rows whose object has not
-- arrived, which 007 then fails as unreadable. Run the read only check first;
-- if it returns rows, either wait for them to settle or accept that outcome
-- knowingly. A founder decision after diagnosis, never automatic.
--
--   select id, agent_id, requested_at, started_at
--     from jobs where type = 'synthesize' and status = 'queued' and started_at is not null;
--
-- Paste-safe, idempotent.
-- ============================================================================

create or replace function claim_synthesis_batch(p_job uuid) returns jsonb
language plpgsql security definer set search_path = public as '
declare j jobs%rowtype; s text; ids uuid[];
begin
  select * into j from jobs where id = p_job for update;
  if j.id is null then raise exception ''no_such_job''; end if;
  if j.type <> ''synthesize'' then raise exception ''not_synthesize''; end if;
  if j.status <> ''queued'' then raise exception ''job_not_queued''; end if;
  select state into s from agents where id = j.agent_id for update;
  if s = ''archived'' then
    update jobs set status = ''failed'', completed_at = now(),
      error = ''This FOOUND is archived and cannot read new evidence.''
      where id = j.id;
    return jsonb_build_object(''status'',''refused'',''reason'',''agent_archived'');
  end if;
  with claimed as (
    update evidence_items
       set status = ''reading'', submitted_in = j.id
     where agent_id = j.agent_id and status = ''received''
     returning id)
  select coalesce(array_agg(id), ''{}''::uuid[]) into ids from claimed;
  if coalesce(array_length(ids, 1), 0) = 0 then
    update jobs set status = ''failed'', completed_at = now(),
      error = ''There was nothing new to read. Add evidence first.''
      where id = j.id;
    return jsonb_build_object(''status'',''empty'');
  end if;
  update jobs set status = ''running'', started_at = now() where id = j.id;
  update agents set state = ''feed_submitted''
    where id = j.agent_id and state in (''invited'', ''commissioning'');
  return jsonb_build_object(''status'',''claimed'',
    ''items'', to_jsonb(ids), ''count'', array_length(ids, 1));
end';
revoke execute on function claim_synthesis_batch(uuid) from public, anon, authenticated;
grant execute on function claim_synthesis_batch(uuid) to service_role;

drop function if exists synthesis_defer_max();
drop function if exists synthesis_wait_minutes();
