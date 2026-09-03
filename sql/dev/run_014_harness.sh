#!/usr/bin/env bash
# Disposable local/CI harness for migration 014 (the database wakes the engine). NEVER against production.
#
# Applies: test_harness + 000_harness_base + 005 + 006 + extras + 013 + 014 stubs + 014 + test_014
# (pg_net / pg_cron / vault are stubbed: sql/dev/014_harness_stubs.sql records instead of calling.)
#
# Usage:
#   bash sql/dev/run_014_harness.sh
# Env:
#   PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE
#   FOOUND_014_CREATE_DB=1  create/drop a throwaway database (local default
#                           when PGDATABASE is unset)
#   FOOUND_014_KEEP_DB=1    do not drop the database at the end (CI)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"
export PGPASSWORD="${PGPASSWORD:-harness}"

CREATED=0
if [[ -z "${PGDATABASE:-}" ]]; then
  export PGDATABASE="foound_014_$$"
  FOOUND_014_CREATE_DB=1
fi

psql_admin() {
  PGDATABASE=postgres psql -v ON_ERROR_STOP=1 "$@"
}

psql_db() {
  psql -v ON_ERROR_STOP=1 "$@"
}

if [[ "${FOOUND_014_CREATE_DB:-}" == "1" ]]; then
  echo "==> creating disposable database ${PGDATABASE}"
  psql_admin -c "drop database if exists ${PGDATABASE};"
  psql_admin -c "create database ${PGDATABASE};"
  CREATED=1
fi

echo "==> applying harness + 005 + 006 + 013 + 014 stubs + 014 (not production)"
psql_db -f sql/test_harness.sql
psql_db -f sql/dev/000_harness_base.sql
psql_db -f sql/005_multiuser.sql
psql_db -f sql/006_jobs.sql
psql_db -f sql/dev/011_harness_extras.sql
psql_db -f sql/013_brief_doors.sql
psql_db -f sql/dev/014_harness_stubs.sql
# pg_net / pg_cron do not exist on disposable Postgres: the stubs stand in
grep -v '^create extension if not exists pg_' sql/014_wake_engine.sql | psql_db -f -

echo "==> running sql/test_migration_014.sql"
psql_db -f sql/test_migration_014.sql

echo "==> residue check (agent 54 must be gone)"
psql_db -c "select count(*) as leftover_014_agents from agents where agent_no = 54;" \
  | tee /tmp/foound_014_residue.txt
leftover="$(psql_db -tAc "select count(*) from agents where agent_no = 54;")"
if [[ "${leftover}" != "0" ]]; then
  echo "FAIL: leftover 014 fixture agent: ${leftover}"
  exit 1
fi
echo "M014 harness residue: 0"

if [[ "${CREATED}" == "1" && "${FOOUND_014_KEEP_DB:-}" != "1" ]]; then
  echo "==> dropping disposable database ${PGDATABASE}"
  psql_admin -c "drop database if exists ${PGDATABASE};"
fi

echo "M014 HARNESS OK"
