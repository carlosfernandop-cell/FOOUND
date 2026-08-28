#!/usr/bin/env bash
# Disposable local/CI harness for migration 011. NEVER against production.
#
# Applies: test_harness + 000_harness_base + 005 + 006 + extras + 011 + test_011
# (007-010 are not required: 011 only replaces commission_agent from 006.)
#
# Usage:
#   bash sql/dev/run_011_harness.sh
# Env:
#   PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE
#   FOOUND_011_CREATE_DB=1  create/drop a throwaway database (local default
#                           when PGDATABASE is unset)
#   FOOUND_011_KEEP_DB=1    do not drop the database at the end (CI)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"
export PGPASSWORD="${PGPASSWORD:-harness}"

CREATED=0
if [[ -z "${PGDATABASE:-}" ]]; then
  export PGDATABASE="foound_011_$$"
  FOOUND_011_CREATE_DB=1
fi

psql_admin() {
  PGDATABASE=postgres psql -v ON_ERROR_STOP=1 "$@"
}

psql_db() {
  psql -v ON_ERROR_STOP=1 "$@"
}

if [[ "${FOOUND_011_CREATE_DB:-}" == "1" ]]; then
  echo "==> creating disposable database ${PGDATABASE}"
  psql_admin -c "drop database if exists ${PGDATABASE};"
  psql_admin -c "create database ${PGDATABASE};"
  CREATED=1
fi

echo "==> applying harness + 005 + 006 + 011 (not production)"
psql_db -f sql/test_harness.sql
psql_db -f sql/dev/000_harness_base.sql
psql_db -f sql/005_multiuser.sql
psql_db -f sql/006_jobs.sql
psql_db -f sql/dev/011_harness_extras.sql
psql_db -f sql/011_commission_recovery.sql

echo "==> running sql/test_migration_011.sql"
psql_db -f sql/test_migration_011.sql

echo "==> residue check (agents 96-99 must be gone)"
psql_db -c "select count(*) as leftover_011_agents from agents where agent_no in (96,97,98,99);" \
  | tee /tmp/foound_011_residue.txt
leftover="$(psql_db -tAc "select count(*) from agents where agent_no in (96,97,98,99);")"
if [[ "${leftover}" != "0" ]]; then
  echo "FAIL: leftover 011 fixture agents: ${leftover}"
  exit 1
fi
echo "M011 harness residue: 0"

if [[ "${CREATED}" == "1" && "${FOOUND_011_KEEP_DB:-}" != "1" ]]; then
  echo "==> dropping disposable database ${PGDATABASE}"
  psql_admin -c "drop database if exists ${PGDATABASE};"
fi

echo "M011 HARNESS OK"
