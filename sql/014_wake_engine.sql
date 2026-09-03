-- ============================================================================
-- FOOUND — Migration 014: the database wakes the engine
--
-- Finding (2026-09-03, first disposable-client rehearsal): GitHub ran the
-- `*/15` heartbeat ONCE in seven hours. GitHub's cron is best-effort and
-- throttled; a product that promises "within the quarter hour" cannot rest
-- on it. The database already holds every intent (jobs) and every verdict
-- (memory); it is the right thing to wake the engine.
--
-- What this adds, and nothing else:
--   · engine_wakes — a small ledger of wake-ups (reason, when, request id).
--     Also the debounce: at most one wake per WAKE_DEBOUNCE seconds.
--   · wake_engine(p_reason) — POSTs a workflow_dispatch for heartbeat.yml
--     through pg_net, with a fine-grained GitHub token read from Vault
--     (secret name: github_dispatch_token; Actions: read+write on the
--     FOOUND repository only). No token in this file, in a table, or in
--     any log. If the secret is absent the function records
--     'skipped:no_token' and returns; nothing raises inside a trigger.
--   · Triggers: a queued job is inserted (any type) → wake; a confirmed
--     memory row is inserted (the client's verdict) → wake. Both debounced.
--   · pg_cron: every 15 minutes → wake('clock'). The GitHub schedule stays
--     in heartbeat.yml as a second, weaker clock; the concurrency group
--     keeps the two from overlapping. A daily prune keeps the ledger to
--     30 days. engine_wake_outcomes shows each wake's HTTP result.
--
-- Proof in production, two steps, no throwaway project:
--   1. Apply with NO secret in Vault. Every trigger and the clock then
--      record 'skipped:no_token' — triggers, debounce, cron and ledger are
--      proven live with zero outbound calls.
--   2. Carlos adds the fine-grained token (repository FOOUND only, Actions:
--      read+write, one-year expiry) as Vault secret github_dispatch_token.
--      The next queued job dispatches heartbeat.yml with reason job:<type>;
--      engine_wake_outcomes shows 204. Revocation: delete the token on
--      GitHub → outcomes show 401 → the GitHub schedule still beats.
--
-- The engine's contract is untouched: it still only executes intents the
-- client expressed. This changes WHEN it looks, never WHAT it does.
-- Paste-safe: no dollar-quoting. Idempotent: safe to re-run.
-- Prove on disposable Postgres with stubs (sql/test_migration_014.sql);
-- pg_net / pg_cron / vault exist only on Supabase.
-- ============================================================================

create extension if not exists pg_net;
create extension if not exists pg_cron;

create table if not exists engine_wakes (
  id          bigserial primary key,
  reason      text not null check (length(reason) between 1 and 80),
  at          timestamptz not null default now(),
  outcome     text not null default 'sent'
              check (outcome in ('sent','skipped:debounced','skipped:no_token','skipped:error')),
  request_id  bigint
);
create index if not exists engine_wakes_at_idx on engine_wakes (at desc);
alter table engine_wakes enable row level security;   -- no client policies: engine-only ledger

create or replace function wake_engine(p_reason text) returns text
language plpgsql security definer set search_path = public as '
declare
  v_token text;
  v_last timestamptz;
  v_req bigint;
  v_reason text := left(coalesce(p_reason, ''unspecified''), 80);
begin
  -- debounce: one wake per 45 seconds is plenty (a run takes longer than that)
  select max(at) into v_last from engine_wakes where outcome = ''sent'';
  if v_last is not null and v_last > now() - interval ''45 seconds'' then
    insert into engine_wakes (reason, outcome) values (v_reason, ''skipped:debounced'');
    return ''skipped:debounced'';
  end if;

  begin
    select decrypted_secret into v_token
      from vault.decrypted_secrets where name = ''github_dispatch_token'' limit 1;
  exception when others then
    v_token := null;
  end;
  if v_token is null or length(v_token) = 0 then
    insert into engine_wakes (reason, outcome) values (v_reason, ''skipped:no_token'');
    return ''skipped:no_token'';
  end if;

  begin
    select net.http_post(
      url := ''https://api.github.com/repos/carlosfernandop-cell/FOOUND/actions/workflows/heartbeat.yml/dispatches'',
      headers := jsonb_build_object(
        ''Authorization'', ''Bearer '' || v_token,
        ''Accept'', ''application/vnd.github+json'',
        ''X-GitHub-Api-Version'', ''2022-11-28'',
        ''User-Agent'', ''foound-wake'',
        ''Content-Type'', ''application/json''),
      body := jsonb_build_object(''ref'', ''main'', ''inputs'', jsonb_build_object(''reason'', v_reason)),
      timeout_milliseconds := 5000
    ) into v_req;
  exception when others then
    insert into engine_wakes (reason, outcome) values (v_reason, ''skipped:error'');
    return ''skipped:error'';
  end;

  insert into engine_wakes (reason, outcome, request_id) values (v_reason, ''sent'', v_req);
  return ''sent'';
end';
revoke execute on function wake_engine(text) from public, anon, authenticated;

-- ---- a queued job is an intent: wake --------------------------------------
create or replace function wake_engine_on_job() returns trigger
language plpgsql security definer set search_path = public as '
begin
  if new.status = ''queued'' then
    perform wake_engine(''job:'' || new.type);
  end if;
  return new;
end';
drop trigger if exists jobs_wake_engine on jobs;
create trigger jobs_wake_engine
  after insert on jobs
  for each row execute function wake_engine_on_job();

-- ---- a confirmed memory row is a verdict: wake (the sweep decides) --------
create or replace function wake_engine_on_confirm() returns trigger
language plpgsql security definer set search_path = public as '
begin
  if new.provenance = ''confirmed'' and new.status = ''active'' then
    perform wake_engine(''memory:confirmed'');
  end if;
  return new;
end';
drop trigger if exists memory_wake_engine on memory;
create trigger memory_wake_engine
  after insert on memory
  for each row execute function wake_engine_on_confirm();

-- ---- what happened to each wake: visible, never a secret ------------------
-- pg_net keeps responses in net._http_response for a few hours; the join
-- shows 204 (dispatched), 401/403 (token revoked or mis-scoped), 404
-- (workflow or repo moved), or nothing yet. The token never appears here.
create or replace view engine_wake_outcomes as
  select w.id, w.reason, w.at, w.outcome, w.request_id,
         r.status_code, left(r.error_msg, 120) as error
    from engine_wakes w
    left join net._http_response r on r.id = w.request_id
   order by w.at desc;
revoke all on engine_wake_outcomes from public, anon, authenticated;

-- ---- the clock: every quarter hour; the ledger never grows without bound --
do '
begin
  perform cron.unschedule(jobid) from cron.job where jobname in (''foound_wake_engine'', ''foound_prune_wakes'');
exception when others then null;
end';
select cron.schedule('foound_wake_engine', '*/15 * * * *', 'select wake_engine(''clock'')');
select cron.schedule('foound_prune_wakes', '7 3 * * *',
  'delete from engine_wakes where at < now() - interval ''30 days''');
