-- Local test harness ONLY. Emulates the parts of Supabase the migrations
-- depend on (auth schema, auth.uid()) so 001/002 can be verified offline.
-- Never run this against Supabase — it provides these already.

create schema if not exists auth;

create table if not exists auth.users (
  id uuid primary key default gen_random_uuid(),
  email text unique
);

-- auth.uid() reads the current request's JWT claim. Locally we fake it with
-- a session GUC so tests can "become" a user.
create or replace function auth.uid() returns uuid
language sql stable as $$
  select nullif(current_setting('test.uid', true), '')::uuid
$$;

-- Roles Supabase provides in production; stubbed for local verification of 005+.
do $$ begin create role authenticated nologin; exception when duplicate_object then null; end $$;
do $$ begin create role anon nologin; exception when duplicate_object then null; end $$;

-- Stubs for 007 local verification: the service role and a minimal storage
-- schema (Supabase provides all of this in production — never run there).
do $$ begin create role service_role nologin bypassrls; exception when duplicate_object then null; end $$;

create schema if not exists storage;
create table if not exists storage.buckets (
  id text primary key,
  name text,
  public boolean default false,
  file_size_limit bigint,
  allowed_mime_types text[]
);
create table if not exists storage.objects (
  id uuid primary key default gen_random_uuid(),
  bucket_id text,
  name text,
  owner uuid,
  created_at timestamptz default now()
);
alter table storage.objects enable row level security;
create or replace function storage.foldername(name text) returns text[]
language sql immutable as $$
  select (string_to_array(name,'/'))[1:array_length(string_to_array(name,'/'),1)-1]
$$;
create or replace function storage.filename(name text) returns text
language sql immutable as $$
  select (string_to_array(name,'/'))[array_length(string_to_array(name,'/'),1)]
$$;
