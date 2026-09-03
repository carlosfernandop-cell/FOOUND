-- Re-applies migration 014 inside the battery (idempotency check). The
-- `create extension` lines are skipped exactly as run_014_harness.sh does.
\set ON_ERROR_STOP on
\! grep -v '^create extension if not exists pg_' sql/014_wake_engine.sql > /tmp/foound_014_reapply.sql
\i /tmp/foound_014_reapply.sql
