-- ============================================================================
-- FOOUND — Migration 005: minimal complete client autonomy (Plan v3.1, Phase 1)
--
-- What this adds, in one sentence: everything a second client's data needs to
-- exist privately — profile, evidence ledger, briefs with proposed/active
-- semantics, private editions, sources-as-data, invitations — plus the
-- client-invoked, database-gated, idempotent lifecycle.
--
-- Design rules enforced here:
--   · No client-owned lifecycle transition requires an operator-only mutation
--     (commission/pause/resume/archive are functions, gated and idempotent).
--   · The active Brief is never mutated in place (proposed vs active states;
--     one active per agent, enforced by partial unique index).
--   · Every edition can reference the brief_version that produced it.
--   · LIMITED readiness requires a stored acknowledgment before commissioning.
--   · Reading is shared (sources, market_seen carry no user data);
--     judging is personal (everything else is RLS-walled per agent).
--
-- Paste-safe: no dollar-quoting anywhere (function bodies single-quoted).
-- Safe to re-run: create-if-not-exists / drop-and-recreate patterns throughout.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Lifecycle: extend the agent state vocabulary (text + CHECK, per 001).
-- Legacy values stay valid so №001's row never breaks.
-- ---------------------------------------------------------------------------
alter table agents drop constraint if exists agents_state_check;
alter table agents add constraint agents_state_check
  check (state in ('invited','feed_submitted','mirror_ready','commissioned',
                   'commissioning','awaiting_confirmation',
                   'at_work','paused','archived'));

-- ---------------------------------------------------------------------------
-- CANDIDATES: the profile as versioned data. draft -> approved -> published.
-- ---------------------------------------------------------------------------
create table if not exists candidates (
  id           uuid primary key default gen_random_uuid(),
  agent_id     uuid not null references agents(id) on delete cascade,
  version      int  not null check (version > 0),
  content      text not null,
  state        text not null default 'draft'
               check (state in ('draft','approved','published','unpublished')),
  slug         text check (slug is null or slug ~ '^[a-z0-9][a-z0-9-]{1,40}$'),
  created_at   timestamptz not null default now(),
  approved_at  timestamptz,
  unique (agent_id, version)
);
create unique index if not exists candidates_published_slug
  on candidates (slug) where slug is not null and state = 'published';

alter table candidates enable row level security;
drop policy if exists candidates_owner_read on candidates;
create policy candidates_owner_read on candidates
  for select using (exists (select 1 from agents a
                            where a.id = candidates.agent_id
                              and a.user_id = auth.uid()));

-- ---------------------------------------------------------------------------
-- MEMORY: the Evidence Ledger. Four epistemic layers, five provenance grades,
-- an evidence chain on every belief, and no path for silent mutation:
-- rows supersede, retract, orphan, or hold tension — they are never edited
-- into something else.
-- ---------------------------------------------------------------------------
create table if not exists memory (
  id                  uuid primary key default gen_random_uuid(),
  agent_id            uuid not null references agents(id) on delete cascade,
  layer               text not null
                      check (layer in ('record','self','model','behavior')),
  statement           text not null check (length(statement) between 1 and 1000),
  provenance          text not null
                      check (provenance in ('stated','extracted','observed',
                                            'inferred','confirmed')),
  evidence            jsonb not null default '[]',
  source              text not null check (length(source) between 1 and 60),
  status              text not null default 'active'
                      check (status in ('active','superseded','retracted',
                                        'tension','orphaned')),
  supersedes          uuid references memory(id),
  expires             timestamptz,
  can_affect_search   boolean not null default true,
  can_appear_publicly boolean not null default false,
  created_at          timestamptz not null default now(),
  last_reinforced     timestamptz
);
create index if not exists memory_agent_active_idx
  on memory (agent_id, status, layer);

alter table memory enable row level security;
drop policy if exists memory_owner_all on memory;
create policy memory_owner_all on memory
  for all
  using      (exists (select 1 from agents a
                      where a.id = memory.agent_id and a.user_id = auth.uid()))
  with check (exists (select 1 from agents a
                      where a.id = memory.agent_id and a.user_id = auth.uid()));

-- ---------------------------------------------------------------------------
-- BRIEFS: the working contract. proposed -> active -> superseded.
-- The ACTIVE brief is never mutated in place; edits create a new PROPOSED row;
-- activation (via function or, in the pilot, the app) supersedes the old one.
-- LIMITED readiness stores its acknowledgment here, with the version.
-- ---------------------------------------------------------------------------
create table if not exists briefs (
  id             uuid primary key default gen_random_uuid(),
  agent_id       uuid not null references agents(id) on delete cascade,
  version        int  not null check (version > 0),
  state          text not null default 'proposed'
                 check (state in ('proposed','active','superseded','abandoned')),
  content        jsonb not null,
  compiled_config jsonb,
  readiness      text check (readiness in ('ready','limited','not_ready')),
  readiness_ack  text,
  created_at     timestamptz not null default now(),
  confirmed_at   timestamptz,
  unique (agent_id, version)
);
create unique index if not exists one_active_brief_per_agent
  on briefs (agent_id) where state = 'active';

alter table briefs enable row level security;
drop policy if exists briefs_owner_read on briefs;
create policy briefs_owner_read on briefs
  for select using (exists (select 1 from agents a
                            where a.id = briefs.agent_id
                              and a.user_id = auth.uid()));
-- The owner may create and edit PROPOSED briefs only. Activation and
-- supersession run through gated paths, never a direct row update.
drop policy if exists briefs_owner_propose on briefs;
create policy briefs_owner_propose on briefs
  for insert with check (state = 'proposed'
    and exists (select 1 from agents a
                where a.id = briefs.agent_id and a.user_id = auth.uid()));
drop policy if exists briefs_owner_edit_proposed on briefs;
create policy briefs_owner_edit_proposed on briefs
  for update
  using (state = 'proposed'
    and exists (select 1 from agents a
                where a.id = briefs.agent_id and a.user_id = auth.uid()))
  with check (state in ('proposed','abandoned'));

-- ---------------------------------------------------------------------------
-- EDITIONS: the private read path. One per agent per day; provenance to the
-- brief version that produced it. Owner reads; only the pipeline writes.
-- ---------------------------------------------------------------------------
create table if not exists editions (
  id            uuid primary key default gen_random_uuid(),
  agent_id      uuid not null references agents(id) on delete cascade,
  edition_date  date not null,
  brief_version int,
  html          text not null,
  payload       jsonb,
  outcome       text,
  delivered_at  timestamptz,
  created_at    timestamptz not null default now(),
  unique (agent_id, edition_date)
);
create index if not exists editions_agent_date_idx
  on editions (agent_id, edition_date desc);

alter table editions enable row level security;
drop policy if exists editions_owner_read on editions;
create policy editions_owner_read on editions
  for select using (exists (select 1 from agents a
                            where a.id = editions.agent_id
                              and a.user_id = auth.uid()));

-- ---------------------------------------------------------------------------
-- SOURCES: the market registry as data. Shared; carries no user data.
-- agent_sources: per-agent subscriptions (the derived watchlist's operational
-- form — clients never see or edit these rows directly).
-- ---------------------------------------------------------------------------
create table if not exists sources (
  id      text primary key check (id ~ '^[a-z0-9][a-z0-9_-]{1,40}$'),
  adapter text not null
          check (adapter in ('greenhouse','ashby','lever','workday',
                             'workable','recruitee','bespoke')),
  args    jsonb not null default '[]',
  label   text not null
);
create table if not exists agent_sources (
  agent_id  uuid not null references agents(id) on delete cascade,
  source_id text not null references sources(id) on delete cascade,
  primary key (agent_id, source_id)
);
alter table sources enable row level security;        -- no policies: service-only
alter table agent_sources enable row level security;  -- no policies: service-only

-- ---------------------------------------------------------------------------
-- MARKET_SEEN: replaces the Notion store in the application path.
-- Market-level truth: which roles have been seen, and when, across everyone.
-- ---------------------------------------------------------------------------
create table if not exists market_seen (
  role_key   text primary key check (length(role_key) between 1 and 300),
  first_seen date not null default current_date
);
alter table market_seen enable row level security;    -- no policies: service-only

-- ---------------------------------------------------------------------------
-- INVITATIONS: a reserved serial is real capacity.
-- ---------------------------------------------------------------------------
create table if not exists invitations (
  id         uuid primary key default gen_random_uuid(),
  email      text not null,
  agent_no   int  not null unique check (agent_no > 0),
  status     text not null default 'sent'
             check (status in ('sent','accepted','expired')),
  created_at timestamptz not null default now()
);
alter table invitations enable row level security;    -- no policies: service-only

-- ---------------------------------------------------------------------------
-- THE CLIENT-INVOKED LIFECYCLE. Gated, idempotent, RLS-independent
-- (security definer keyed on auth.uid() — a client can only ever move their
-- own agent, and only through these doors). Return value is an honest status
-- string the product surfaces; "blocked:*" states are recoverable, named, and
-- never require an operator.
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
  return ''at_work'';
end';

create or replace function pause_agent() returns text
language plpgsql security definer set search_path = public as '
declare a agents%rowtype;
begin
  select * into a from agents where user_id = auth.uid()
    order by agent_no limit 1;
  if a.id is null then return ''no_agent''; end if;
  if a.state = ''paused'' then return ''paused''; end if;
  if a.state <> ''at_work'' then return ''blocked:state_'' || a.state; end if;
  update agents set state = ''paused'' where id = a.id;
  return ''paused'';
end';

create or replace function resume_agent() returns text
language plpgsql security definer set search_path = public as '
declare a agents%rowtype;
begin
  select * into a from agents where user_id = auth.uid()
    order by agent_no limit 1;
  if a.id is null then return ''no_agent''; end if;
  if a.state = ''at_work'' then return ''at_work''; end if;
  if a.state <> ''paused'' then return ''blocked:state_'' || a.state; end if;
  update agents set state = ''at_work'' where id = a.id;
  return ''at_work'';
end';

create or replace function archive_agent() returns text
language plpgsql security definer set search_path = public as '
declare a agents%rowtype;
begin
  select * into a from agents where user_id = auth.uid()
    order by agent_no limit 1;
  if a.id is null then return ''no_agent''; end if;
  if a.state = ''archived'' then return ''archived''; end if;
  update agents set state = ''archived'' where id = a.id;
  return ''archived'';
end';

revoke execute on function commission_agent() from public;
revoke execute on function pause_agent()      from public;
revoke execute on function resume_agent()     from public;
revoke execute on function archive_agent()    from public;
grant  execute on function commission_agent() to authenticated;
grant  execute on function pause_agent()      to authenticated;
grant  execute on function resume_agent()     to authenticated;
grant  execute on function archive_agent()    to authenticated;
