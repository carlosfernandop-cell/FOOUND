#!/usr/bin/env bash
# Disposable local/CI harness for migration 013 (Working Brief doors). NEVER against production.
#
# Applies: test_harness + 000_harness_base + 005 + 006 + extras + 013 + test_013
# (007-012 are not required: 013 only adds doors on 005 briefs + 006 jobs.)
#
# Usage:
#   bash sql/dev/run_013_harness.sh
# Env:
#   PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE
#   FOOUND_013_CREATE_DB=1  create/drop a throwaway database (local default
#                           when PGDATABASE is unset)
#   FOOUND_013_KEEP_DB=1    do not drop the database at the end (CI)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"
export PGPASSWORD="${PGPASSWORD:-harness}"

CREATED=0
if [[ -z "${PGDATABASE:-}" ]]; then
  export PGDATABASE="foound_013_$$"
  FOOUND_013_CREATE_DB=1
fi

psql_admin() {
  PGDATABASE=postgres psql -v ON_ERROR_STOP=1 "$@"
}

psql_db() {
  psql -v ON_ERROR_STOP=1 "$@"
}

if [[ "${FOOUND_013_CREATE_DB:-}" == "1" ]]; then
  echo "==> creating disposable database ${PGDATABASE}"
  psql_admin -c "drop database if exists ${PGDATABASE};"
  psql_admin -c "create database ${PGDATABASE};"
  CREATED=1
fi

echo "==> applying harness + 005 + 006 + 013 (not production)"
psql_db -f sql/test_harness.sql
psql_db -f sql/dev/000_harness_base.sql
psql_db -f sql/005_multiuser.sql
psql_db -f sql/006_jobs.sql
psql_db -f sql/dev/011_harness_extras.sql
psql_db -f sql/013_brief_doors.sql

echo "==> running sql/test_migration_013.sql"
psql_db -f sql/test_migration_013.sql

echo "==> residue check (agents 50-53 must be gone)"
psql_db -c "select count(*) as leftover_013_agents from agents where agent_no between 50 and 53;" \
  | tee /tmp/foound_013_residue.txt
leftover="$(psql_db -tAc "select count(*) from agents where agent_no between 50 and 53;")"
if [[ "${leftover}" != "0" ]]; then
  echo "FAIL: leftover 013 fixture agents: ${leftover}"
  exit 1
fi
echo "M013 harness residue: 0"

if [[ "${CREATED}" == "1" && "${FOOUND_013_KEEP_DB:-}" != "1" ]]; then
  echo "==> dropping disposable database ${PGDATABASE}"
  psql_admin -c "drop database if exists ${PGDATABASE};"
fi

echo "M013 HARNESS OK"
