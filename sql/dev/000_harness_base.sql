-- LOCAL HARNESS ONLY — reconstruction of the pre-004 base objects (created in
-- production by migrations 001-003, which are not in this repo). Shapes are
-- derived strictly from how 004-009 and their batteries reference them.
-- NEVER run against Supabase: production already has the real objects.

do $$ begin create type signal_kind  as enum ('pass'); exception when duplicate_object then null; end $$;
do $$ begin create type signal_state as enum ('active','retracted'); exception when duplicate_object then null; end $$;

create table if not exists agents (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null,
  agent_no   int  not null unique,
  state      text not null default 'dormant',
  created_at timestamptz not null default now()
);
alter table agents enable row level security;

create table if not exists signals (
  id         uuid primary key default gen_random_uuid(),
  agent_id   uuid references agents(id) on delete cascade,
  kind       signal_kind  not null,
  state      signal_state not null default 'active',
  reason     text,
  note       text,
  role_state text,
  created_at timestamptz not null default now()
);
