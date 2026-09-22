-- ============================================================================
-- FOOUND — Migration 018 (PROPOSAL, rev 3): the synthesis request boundary
--
-- Why (verified 2026-09-21, read only): a file's evidence row is inserted
-- BEFORE its object is uploaded (007 row-first flow; the storage policy
-- requires the owned received row). claim_synthesis_batch(job) then claims
-- EVERY received row of the agent, whether or not its object exists and
-- whether or not the person asked for it. The runner fetches the missing
-- object, gets nothing, and records the false verdict "unreadable" on a file
-- whose upload was merely late; a queued job from an earlier request can
-- claim a row the person has not finished adding; a job reads rows the
-- person never handed off.
--
-- What this replaces, and nothing else: claim_synthesis_batch(uuid), same
-- signature, same service-only grant, same archived refusal. Rules:
--
--   REQUESTED  The payload key "evidence" is the person's explicit intent.
--              Absent key (legacy client): today's scope narrowed to received
--              rows created no later than the request. Present key: it must
--              be a JSON array of uuid strings; any other type, or a null,
--              is malformed intent and FAILS CLOSED (the job fails with the
--              honest copy, nothing is claimed). Entries that are not uuids
--              are counted as malformed and dropped; duplicates collapse;
--              ids that are not this agent's received rows are skipped and
--              counted. Nothing foreign is ever read or echoed.
--
--   READY      A file row is ready only when an object exists in
--              storage.objects under bucket feeds at the row's exact
--              storage_path (Supabase writes that row when the upload
--              completes; this is metadata, not proof of bytes, and the
--              runner's fetch and size check remain the proof). Text rows
--              are always ready. Unready rows are NOT claimed and NOT failed.
--
--   LOCKED     Candidate rows are locked FOR UPDATE with the agent row; the
--              returned membership is the rows the UPDATE actually claimed
--              (RETURNING), never an earlier snapshot. A concurrent removal
--              either lands before the lock (the row is not a candidate) or
--              after the claim (the runner records it withdrawn, as today).
--
--   WAITING    An explicit request naming file rows that are not ready is
--              deferred as a whole: the job stays queued and the door stamps
--              started_at (service written; the 006 insert policy forbids a
--              client from presetting it, so it is a trusted clock, not a
--              counter) on the first deferral. The wait ends at started_at +
--              synthesis_wait_minutes() (60). After that the door claims what
--              is ready and leaves the still missing rows RECEIVED: no verdict
--              is ever written because a wait expired; the rows remain held,
--              are reported in the claim result, and are read by a later
--              request once their object exists, or removed by the person.
--              If nothing is ready after the wait, the job fails with the
--              honest copy and the rows stay received. Legacy jobs never wait.
--              Discovery (synthesis_runner) is two tiers: queued jobs that are
--              not waiting first, oldest first; jobs still waiting only when
--              there is no such job, oldest first, bounded by the runner''s
--              walk limit. So waiting requests never delay a request that is
--              not waiting; a waiting request is re-asked in idle beats if it
--              is among the oldest waiting, and otherwise only once its wait
--              is over, when it is claimed like any other request (what is
--              ready is read, or the job fails empty). The runner mirrors
--              this helper as SYNTHESIS_WAIT_MINUTES; test_f2 pins them equal.
--
-- Consent: this narrows what a job may read; it never widens it. Storage,
-- RLS, grants, tables and columns are untouched. settle and finalize are
-- untouched. Note the semantic extension: a QUEUED synthesize job with a
-- non-null started_at is one that has been waiting for files since then.
--
-- Paste-safe: no dollar-quoting. Idempotent. Run AFTER 007. Never applied to
-- production by this proposal; Carlos's act after review.
-- ============================================================================

do '
begin
  if to_regclass(''public.evidence_items'') is null then
    raise exception ''run_007_first: evidence_items not found'';
  end if;
  if to_regclass(''storage.objects'') is null then
    raise exception ''storage_objects_missing: this database has no storage.objects'';
  end if;
end';

drop function if exists synthesis_defer_max();
create or replace function synthesis_wait_minutes() returns int
language sql immutable as 'select 60';

create or replace function claim_synthesis_batch(p_job uuid) returns jsonb
language plpgsql security definer set search_path = public as '
declare j jobs%rowtype; s text;
        explicit boolean := false; malformed_key boolean := false;
        requested uuid[] := ''{}''::uuid[]; n_malformed int := 0;
        n_skipped int := 0;
        ids uuid[] := ''{}''::uuid[]; pending uuid[] := ''{}''::uuid[];
        waited_out boolean := false;
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

  -- REQUESTED: explicit intent is the presence of the key, whatever its shape
  if jsonb_typeof(j.payload) = ''object'' and j.payload ? ''evidence'' then
    explicit := true;
    if jsonb_typeof(j.payload->''evidence'') <> ''array'' then
      malformed_key := true;
    else
      select coalesce(array_agg(distinct (e #>> ''{}'')::uuid), ''{}''::uuid[])
        into requested
        from jsonb_array_elements(j.payload->''evidence'') as t(e)
       where jsonb_typeof(e) = ''string''
         and (e #>> ''{}'') ~* ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'';
      select count(*) into n_malformed
        from jsonb_array_elements(j.payload->''evidence'') as t(e)
       where jsonb_typeof(e) <> ''string''
          or (e #>> ''{}'') !~* ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'';
    end if;
  end if;
  if malformed_key then
    update jobs set status = ''failed'', completed_at = now(),
      error = ''FOOUND could not tell what you asked it to read. Hand it off again.''
      where id = j.id;
    return jsonb_build_object(''status'',''refused'',''reason'',''malformed_request'');
  end if;

  -- LOCKED candidates: this agent''s received rows in scope, row locks held
  -- until commit (the agent row is already locked above); readiness is read
  -- once, under those locks
  select coalesce(array_agg(c.id) filter (where c.ready), ''{}''::uuid[]),
         coalesce(array_agg(c.id) filter (where not c.ready), ''{}''::uuid[])
    into ids, pending
    from (select l.id,
                 l.kind = ''text''
                 or exists (select 1 from storage.objects o
                             where o.bucket_id = ''feeds'' and o.name = l.storage_path) as ready
            from (select ei.id, ei.kind, ei.storage_path
                    from evidence_items ei
                   where ei.agent_id = j.agent_id and ei.status = ''received''
                     and ((explicit and ei.id = any(requested))
                          or (not explicit and ei.created_at <= j.requested_at))
                     for update) as l) as c;
  if explicit then
    select count(*) into n_skipped
      from unnest(requested) as r
     where not (r = any(ids)) and not (r = any(pending));
  end if;

  -- WAITING: an explicit request with files still on their way, on a trusted clock
  if explicit and coalesce(array_length(pending, 1), 0) > 0 then
    if j.started_at is null then
      update jobs set started_at = now() where id = j.id;
      return jsonb_build_object(''status'',''deferred'', ''waiting_since'', now(),
        ''pending'', coalesce(array_length(pending, 1), 0),
        ''ready'', coalesce(array_length(ids, 1), 0),
        ''skipped'', n_skipped, ''malformed'', n_malformed);
    elsif now() < j.started_at + make_interval(mins => synthesis_wait_minutes()) then
      return jsonb_build_object(''status'',''deferred'', ''waiting_since'', j.started_at,
        ''pending'', coalesce(array_length(pending, 1), 0),
        ''ready'', coalesce(array_length(ids, 1), 0),
        ''skipped'', n_skipped, ''malformed'', n_malformed);
    end if;
    waited_out := true;  -- the wait is over; the missing rows stay received
  end if;

  -- CLAIM: the rows actually updated are the membership (locks held above)
  with claimed as (
    update evidence_items ei
       set status = ''reading'', submitted_in = j.id
     where ei.id = any(ids) and ei.status = ''received''
     returning ei.id)
  select coalesce(array_agg(id), ''{}''::uuid[]) into ids from claimed;

  if coalesce(array_length(ids, 1), 0) = 0 then
    update jobs set status = ''failed'', completed_at = now(),
      error = case when waited_out
                   then ''FOOUND waited for your file and it did not arrive. Check it is still there, then hand it off again.''
                   else ''There was nothing new to read. Add evidence first.'' end
      where id = j.id;
    return jsonb_build_object(''status'',''empty'', ''waited_out'', waited_out,
      ''pending'', coalesce(array_length(pending, 1), 0),
      ''skipped'', n_skipped, ''malformed'', n_malformed);
  end if;

  update jobs set status = ''running'', started_at = now(),
         payload = jsonb_set(coalesce(payload, ''{}''::jsonb), ''{read_scope}'',
                   jsonb_build_object(''claimed'', array_length(ids, 1),
                                      ''pending'', coalesce(array_length(pending, 1), 0),
                                      ''skipped'', n_skipped, ''malformed'', n_malformed,
                                      ''waited_out'', waited_out), true)
   where id = j.id;
  update agents set state = ''feed_submitted''
    where id = j.agent_id and state in (''invited'', ''commissioning'');
  return jsonb_build_object(''status'',''claimed'',
    ''items'', to_jsonb(ids), ''count'', array_length(ids, 1),
    ''pending'', coalesce(array_length(pending, 1), 0),
    ''skipped'', n_skipped, ''malformed'', n_malformed,
    ''waited_out'', waited_out, ''explicit'', explicit);
end';
revoke execute on function claim_synthesis_batch(uuid) from public, anon, authenticated;
grant execute on function claim_synthesis_batch(uuid) to service_role;
