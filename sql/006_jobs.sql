-- ============================================================================
-- FOOUND — Migration 006: the job boundary (app intent → engine processing)
--
-- Answers the orchestration question before any button exists:
--   The app expresses intent.  Supabase enforces state transitions.
--   The engine interprets and produces intelligence.
--
-- Mechanism (V1, chosen for the actual runtime we have — a Python engine in
-- GitHub Actions, no always-on server):
--   · `jobs` is the intent queue. The app INSERTS a queued job for the
--     client's own agent (RLS-checked, allowed types only) and READS its
--     status to render honest progress ("FOOUND is reading what you gave
--     it"). It can never run, edit, or complete a job.
--   · The engine (service role) polls: at every scheduled run, at a
--     lightweight jobs-only workflow cadence, and on manual fire. It claims
--     queued jobs, processes, and writes done/failed with an error the
--     product can show honestly.
--   · commission_agent() enqueues the first_edition job ATOMICALLY with the
--     state flip — the app never triggers engine work directly, and a
--     commissioned agent can never be missing its first-edition intent.
--   Upgrade path when polling latency matters (post-pilot): a Supabase Edge
--   Function fires repository_dispatch at GitHub on insert. Contract
--   unchanged — only the wake-up gets faster.
--
-- Paste-safe: no dollar-quoting. Idempotent: safe to re-run.
-- Run AFTER 005.
-- ============================================================================

create table if not exists jobs (
  id           uuid primary key default gen_random_uuid(),
  agent_id     uuid not null references agents(id) on delete cascade,
  type         text not null check (type in
                 ('synthesize','compile_brief','first_edition','refresh_readiness')),
  status       text not null default 'queued'
               check (status in ('queued','running','done','failed')),
  payload      jsonb not null default '{}',
  error        text,
  requested_at timestamptz not null default now(),
  started_at   timestamptz,
  completed_at timestamptz
);
create index if not exists jobs_queue_idx on jobs (status, requested_at)
  where status in ('queued','running');
create index if not exists jobs_agent_idx on jobs (agent_id, requested_at desc);

-- One queued job per (agent, type): pressing a button twice, or a retry storm,
-- never stacks duplicate intents. (Running jobs are the engine's to manage.)
create unique index if not exists jobs_one_queued_per_type
  on jobs (agent_id, type) where status = 'queued';

alter table jobs enable row level security;

-- The client may EXPRESS intent for their own agent — allowed types only,
-- always born queued, never with engine-owned fields set.
drop policy if exists jobs_owner_insert on jobs;
create policy jobs_owner_insert on jobs
  for insert with check (
    status = 'queued'
    and started_at is null and completed_at is null and error is null
    and type in ('synthesize','compile_brief','refresh_readiness')
    and exists (select 1 from agents a
                where a.id = jobs.agent_id and a.user_id = auth.uid()));

-- The client may WATCH their own jobs (honest progress and honest failure).
drop policy if exists jobs_owner_read on jobs;
create policy jobs_owner_read on jobs
  for select using (exists (select 1 from agents a
                            where a.id = jobs.agent_id
                              and a.user_id = auth.uid()));

-- No owner update/delete policies: claiming, completing, and failing jobs is
-- engine work (service role). first_edition is deliberately NOT insertable by
-- the client — it exists only through commission_agent(), below.

-- ---------------------------------------------------------------------------
-- commission_agent v2: the state flip and the first-edition intent are one
-- atomic act. Idempotent: re-pressing returns 'at_work' without a second job
-- (the partial unique index guarantees it even under races).
-- ---------------------------------------------------------------------------
create or replace function commission_agent() returns text
language plpgsql security definer set search_path = public as '
declare a agents%rowtype; b briefs%rowtype;
begin
  select * into a from agents where user_id = auth.uid()
    order by agent_no limit 1;
  if a.id is null then return ''no_agent''; end if;
  if a.state = ''at_work'' then return ''at_work''; end if;
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
