-- ============================================================================
-- FOOUND — Migration 007: evidence intake (Evidence Intake Contract v1.3 FINAL)
--
-- One new object (evidence_items) connecting three reserved contracts:
--   jobs.synthesize (006) · feeds/{uid}/ storage · memory.orphaned (005).
--
-- Guarantees enforced HERE, not promised:
--   · impossible item states are structurally excluded (CHECKs)
--   · client authority = create received evidence + request logical deletion;
--     one updatable column (status), one accepted value ('deleted')
--   · deletion: DB-owned timestamp, DB-enforced orphaning, terminal for all
--   · active memory may cite only READ, SAME-AGENT evidence (trigger)
--   · one active synthesize job per agent across queued+running (index)
--   · claim and finalize are the ONLY doors for synthesis lifecycle, both
--     atomic, both service-only; a running synthesize job cannot reach a
--     terminal state except through finalize_synthesis() (jobs guard trigger)
--   · storage objects require a matching owned evidence row (policy)
--   · feeds bucket config is asserted, never silently adopted (preflight)
--
-- Paste-safe: no dollar-quoting. Idempotent: safe to re-run.
-- Run AFTER 006.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- EVIDENCE_ITEMS: one semantic object for all intake (file and text variants).
-- id is CLIENT-GENERATED (row-first flow: insert row, then upload object).
-- ---------------------------------------------------------------------------
create table if not exists evidence_items (
  id            uuid primary key,
  agent_id      uuid not null references agents(id) on delete cascade,
  kind          text not null check (kind in ('file','text')),
  label         text not null check (length(label) between 1 and 200),
  storage_path  text,
  body          text,
  mime_type     text,
  byte_size     int,
  status        text not null default 'received'
                check (status in ('received','reading','read','failed','deleted')),
  failure_reason text,
  submitted_in  uuid references jobs(id),
  created_at    timestamptz not null default now(),
  read_at       timestamptz,
  deleted_at    timestamptz,
  check (kind <> 'file' or (storage_path is not null and mime_type is not null
         and byte_size is not null and byte_size > 0 and body is null)),
  check (kind <> 'file' or storage_path like '%/' || id::text || '/blob'),
  check (kind <> 'text' or (storage_path is null and mime_type is null and byte_size is null)),
  check (kind <> 'text' or status = 'deleted' or body is not null),
  check (body is null or length(body) <= 100000),
  check (status not in ('reading','read','failed') or submitted_in is not null),
  check (status <> 'read' or read_at is not null),
  check (read_at is null or status in ('read','deleted')),
  check (status <> 'failed' or failure_reason is not null),
  check (failure_reason is null or status in ('failed','deleted')),
  check (status <> 'deleted' or deleted_at is not null),
  check (deleted_at is null or status = 'deleted')
);
create index if not exists evidence_agent_status_idx on evidence_items (agent_id, status);
create unique index if not exists evidence_storage_path_unique
  on evidence_items (storage_path) where storage_path is not null;
create index if not exists memory_evidence_gin on memory using gin (evidence jsonb_path_ops);

alter table evidence_items enable row level security;

-- Privileges: precise and column-level. Reset broad defaults first.
revoke all on evidence_items from anon, authenticated;
grant select on evidence_items to authenticated;
grant insert (id, agent_id, kind, label, storage_path, body, mime_type, byte_size)
  on evidence_items to authenticated;
grant update (status) on evidence_items to authenticated;

drop policy if exists evidence_owner_read on evidence_items;
create policy evidence_owner_read on evidence_items
  for select using (exists (select 1 from agents a
    where a.id = evidence_items.agent_id and a.user_id = auth.uid()));
drop policy if exists evidence_owner_insert on evidence_items;
create policy evidence_owner_insert on evidence_items
  for insert with check (
    status = 'received'
    and submitted_in is null and read_at is null
    and failure_reason is null and deleted_at is null
    and (kind <> 'file' or split_part(storage_path, '/', 1) = auth.uid()::text)
    and exists (select 1 from agents a
      where a.id = evidence_items.agent_id and a.user_id = auth.uid()));
drop policy if exists evidence_owner_delete_logical on evidence_items;
create policy evidence_owner_delete_logical on evidence_items
  for update
  using (status <> 'deleted'
    and exists (select 1 from agents a
      where a.id = evidence_items.agent_id and a.user_id = auth.uid()))
  with check (status = 'deleted');
-- No owner DELETE grant or policy: deletion is logical, always.

-- ---------------------------------------------------------------------------
-- Deletion effects: terminal for everyone, DB-owned timestamp, orphaning.
-- ---------------------------------------------------------------------------
create or replace function evidence_delete_effects() returns trigger
language plpgsql security definer set search_path = public as '
begin
  if OLD.status = ''deleted'' and NEW.status <> ''deleted'' then
    raise exception ''deleted_is_terminal'';
  end if;
  if NEW.status = ''deleted'' and OLD.status <> ''deleted'' then
    NEW.deleted_at := now();
    update memory set status = ''orphaned''
      where agent_id = NEW.agent_id and status = ''active''
        and evidence @> jsonb_build_array(jsonb_build_object(''item'', NEW.id::text));
  end if;
  return NEW;
end';
drop trigger if exists evidence_delete_orphans on evidence_items;
create trigger evidence_delete_orphans
  before update on evidence_items
  for each row execute function evidence_delete_effects();

-- ---------------------------------------------------------------------------
-- Active memory may cite only READ evidence OF THE SAME AGENT. Cast-safe.
-- ---------------------------------------------------------------------------
create or replace function memory_requires_read_evidence() returns trigger
language plpgsql security definer set search_path = public as '
declare e jsonb; v text; es text; ea uuid;
begin
  if NEW.status <> ''active'' or NEW.evidence is null
     or jsonb_typeof(NEW.evidence) <> ''array'' then
    return NEW;
  end if;
  for e in select * from jsonb_array_elements(NEW.evidence) loop
    if jsonb_typeof(e) = ''object'' and e ? ''item'' then
      v := e->>''item'';
      if v is null or v !~* ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'' then
        raise exception ''invalid_evidence_ref'';
      end if;
      select ei.status, ei.agent_id into es, ea
        from evidence_items ei where ei.id = v::uuid;
      if not found then raise exception ''evidence_not_found''; end if;
      if ea <> NEW.agent_id then raise exception ''evidence_wrong_agent''; end if;
      if es <> ''read'' then raise exception ''evidence_not_read''; end if;
    end if;
  end loop;
  return NEW;
end';
drop trigger if exists memory_live_evidence on memory;
drop trigger if exists memory_read_evidence on memory;
create trigger memory_read_evidence
  before insert or update on memory
  for each row execute function memory_requires_read_evidence();

-- ---------------------------------------------------------------------------
-- Memory authority: client access is SELECT-only (replaces 005's broad policy).
-- The Mirror contract will introduce gated correction paths later.
-- ---------------------------------------------------------------------------
drop policy if exists memory_owner_all on memory;
drop policy if exists memory_owner_read on memory;
create policy memory_owner_read on memory
  for select using (exists (select 1 from agents a
    where a.id = memory.agent_id and a.user_id = auth.uid()));
revoke all on memory from anon, authenticated;
grant select on memory to authenticated;

-- ---------------------------------------------------------------------------
-- One active synthesize job per agent, across the whole active window.
-- ---------------------------------------------------------------------------
create unique index if not exists jobs_one_active_synthesize
  on jobs (agent_id) where type = 'synthesize' and status in ('queued','running');

-- ---------------------------------------------------------------------------
-- CLAIM: the only door INTO synthesis. Atomic, service-only.
-- Expected refusals persist honest job outcomes; exceptions = engine bugs.
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- FINALIZE: the only door OUT of synthesis. Atomic, service-only.
-- Terminal job state and lifecycle truth commit together, or not at all.
-- Sweeps leftover ''reading'' items to an honest failed state.
-- ---------------------------------------------------------------------------
drop function if exists mark_mirror_ready(uuid);
drop function if exists mark_needs_more_evidence(uuid);

create or replace function finalize_synthesis(p_job uuid, p_outcome text, p_error text default null)
returns jsonb
language plpgsql security definer set search_path = public as '
declare j jobs%rowtype; s text; swept int;
begin
  if p_outcome not in (''mirror_ready'',''needs_more_evidence'',''failed'') then
    raise exception ''invalid_outcome'';
  end if;
  select * into j from jobs where id = p_job for update;
  if j.id is null then raise exception ''no_such_job''; end if;
  if j.type <> ''synthesize'' then raise exception ''not_synthesize''; end if;
  if j.status <> ''running'' then raise exception ''job_not_running''; end if;
  perform 1 from agents where id = j.agent_id for update;

  update evidence_items
     set status = ''failed'',
         failure_reason = ''FOOUND could not finish reading this. Remove it and try again.''
   where submitted_in = j.id and status = ''reading'';
  get diagnostics swept = row_count;

  perform set_config(''foound.synthesis_finalize'', j.id::text, true);

  if p_outcome = ''mirror_ready'' then
    update jobs set status = ''done'', completed_at = now() where id = j.id;
    update agents set state = ''mirror_ready''
      where id = j.agent_id and state = ''feed_submitted'';
  elsif p_outcome = ''needs_more_evidence'' then
    update jobs set status = ''done'', completed_at = now() where id = j.id;
    update agents set state = ''commissioning''
      where id = j.agent_id and state = ''feed_submitted'';
  else
    update jobs set status = ''failed'', completed_at = now(),
        error = coalesce(p_error, ''FOOUND could not finish reading. Try again.'')
      where id = j.id;
    update agents set state = ''commissioning''
      where id = j.agent_id and state = ''feed_submitted'';
  end if;

  select state into s from agents where id = j.agent_id;
  return jsonb_build_object(''status'',''finalized'', ''outcome'', p_outcome,
    ''agent_state'', s, ''swept_reading_items'', swept);
end';
revoke execute on function finalize_synthesis(uuid, text, text) from public, anon, authenticated;
grant execute on function finalize_synthesis(uuid, text, text) to service_role;

-- ---------------------------------------------------------------------------
-- THE GUARD: once a synthesize job is running, only the finalizer may make it
-- terminal — verified by a transaction-local, job-specific marker. This holds
-- even for service_role (RLS bypass does not bypass triggers).
-- Deliberately unguarded: queued→running (claim), queued→failed (claim
-- refusals), all other job types, every non-terminal transition.
-- ---------------------------------------------------------------------------
create or replace function guard_synthesis_finalization() returns trigger
language plpgsql security definer set search_path = public as '
begin
  if OLD.type = ''synthesize'' and OLD.status = ''running''
     and NEW.status in (''done'',''failed'') then
    if coalesce(current_setting(''foound.synthesis_finalize'', true), '''') <> OLD.id::text then
      raise exception ''finalize_required'';
    end if;
  end if;
  return NEW;
end';
drop trigger if exists jobs_guard_synthesis_finalization on jobs;
create trigger jobs_guard_synthesis_finalization
  before update on jobs
  for each row execute function guard_synthesis_finalization();

-- ---------------------------------------------------------------------------
-- STORAGE: the feeds bucket. Config is asserted (fail loudly), never adopted.
-- Object policies enforce object↔evidence-row integrity and owner prefix.
-- Physical cleanup is engine work THROUGH THE STORAGE API, never SQL.
-- ---------------------------------------------------------------------------
do '
declare b record;
begin
  select * into b from storage.buckets where id = ''feeds'';
  if found then
    if b.public
       or b.file_size_limit is distinct from 20971520
       or b.allowed_mime_types is distinct from array[
            ''application/pdf'',
            ''application/vnd.openxmlformats-officedocument.wordprocessingml.document'',
            ''text/plain'',''text/markdown'']::text[] then
      raise exception ''feeds_bucket_misconfigured'';
    end if;
  end if;
end';
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('feeds','feeds', false, 20971520,
        array['application/pdf',
              'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              'text/plain','text/markdown'])
on conflict (id) do nothing;

drop policy if exists feeds_owner_insert on storage.objects;
create policy feeds_owner_insert on storage.objects
  for insert with check (
    bucket_id = 'feeds'
    and (storage.foldername(name))[1] = auth.uid()::text
    and array_length(storage.foldername(name), 1) = 2
    and storage.filename(name) = 'blob'
    and exists (
      select 1 from evidence_items ei
      join agents a on a.id = ei.agent_id
      where ei.storage_path = name and ei.kind = 'file'
        and ei.status = 'received' and a.user_id = auth.uid()));
drop policy if exists feeds_owner_read on storage.objects;
create policy feeds_owner_read on storage.objects
  for select using (bucket_id = 'feeds'
    and (storage.foldername(name))[1] = auth.uid()::text);
-- No owner update/delete on feeds objects.
