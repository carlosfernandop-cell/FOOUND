-- Disposable-Postgres stand-ins for what only Supabase has: pg_net, pg_cron,
-- Vault. Never production. The stubs RECORD instead of doing: every
-- http_post lands in net_calls; every cron.schedule in cron.job.
create schema if not exists net;
create schema if not exists vault;
create schema if not exists cron;

create table if not exists net.net_calls (
  id bigserial primary key, url text, headers jsonb, body jsonb, at timestamptz default now());
create or replace function net.http_post(url text, headers jsonb default '{}'::jsonb, body jsonb default '{}'::jsonb,
                                         timeout_milliseconds int default 5000) returns bigint
language plpgsql as '
declare v bigint;
begin
  insert into net.net_calls (url, headers, body) values (url, headers, body) returning id into v;
  return v;
end';

create table if not exists net._http_response (
  id bigint primary key, status_code int, error_msg text, created timestamptz default now());

create table if not exists vault.decrypted_secrets (name text primary key, decrypted_secret text);

create table if not exists cron.job (jobid bigserial primary key, jobname text, schedule text, command text);
create or replace function cron.schedule(job_name text, schedule text, command text) returns bigint
language plpgsql as '
declare v bigint;
begin
  insert into cron.job (jobname, schedule, command) values (job_name, schedule, command) returning jobid into v;
  return v;
end';
create or replace function cron.unschedule(job_id bigint) returns boolean
language plpgsql as '
begin
  delete from cron.job where jobid = job_id;
  return true;
end';

-- the migration's `create extension` lines must not fail here
create or replace function public.__noop() returns void language sql as 'select 1';
