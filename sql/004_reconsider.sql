-- ============================================================================
-- FOOUND — Migration 004: the persuade verb
--
-- RECONSIDER — the owner's answer to "Found, not FOOUND": look again.
-- Completes the learning loop in the false-negative direction. PASS corrects
-- "you showed me too much"; RECONSIDER corrects "you filtered too hard".
--
--   · owner-only write, same RLS as every signal
--   · carries no reason, no note, no role_state — the verb IS the message
--   · lifecycle: active -> answered (pipeline, after the edition responds)
--                active -> retracted (owner UNDO, as always)
--
-- PASTE IN TWO RUNS. Postgres requires new enum values to be committed
-- before anything references them, so Chunk A must be Run on its own first.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- CHUNK A — run this alone first
-- ---------------------------------------------------------------------------
alter type signal_kind  add value if not exists 'reconsider';
alter type signal_state add value if not exists 'answered';

-- ---------------------------------------------------------------------------
-- CHUNK B — run after Chunk A has succeeded
-- ---------------------------------------------------------------------------
-- The verb is bare by design: a reconsider that carried reasons would become
-- an argument channel, and the argument belongs in the edition's answer.
alter table signals drop constraint if exists reconsider_is_bare;
alter table signals add constraint reconsider_is_bare
  check (kind <> 'reconsider'
         or (reason is null and note is null and role_state is null));

-- 'answered' is a settled state, not an active one: the partial unique index
-- one_active_signal_per_role already frees the role the moment the pipeline
-- answers, so the owner may persuade again after reading the new argument.
-- (No index change needed — documented here so nobody "fixes" it later.)
