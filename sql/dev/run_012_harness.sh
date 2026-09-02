#!/usr/bin/env bash
# Disposable local/CI harness for migration 012. NEVER against production.
#
# Applies: test_harness + 000_harness_base + 005 + extras + 012 + test_012
# (006-011 are not required: 012 only adds invite/provision on 005 objects.)
#
# Usage:
#   bash sql/dev/run_012_harness.sh
# Env:
#   PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE
#   FOOUND_012_CREATE_DB=1  create/drop a throwaway database (local default
#                           when PGDATABASE is unset)
#   FOOUND_012_KEEP_DB=1    do not drop the database at the end (CI)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"
export PGPASSWORD="${PGPASSWORD:-harness}"

CREATED=0
if [[ -z "${PGDATABASE:-}" ]]; then
  export PGDATABASE="foound_012_$$"
  FOOUND_012_CREATE_DB=1
fi

psql_admin() {
  PGDATABASE=postgres psql -v ON_ERROR_STOP=1 "$@"
}

psql_db() {
  psql -v ON_ERROR_STOP=1 "$@"
}

if [[ "${FOOUND_012_CREATE_DB:-}" == "1" ]]; then
  echo "==> creating disposable database ${PGDATABASE}"
  psql_admin -c "drop database if exists ${PGDATABASE};"
  psql_admin -c "create database ${PGDATABASE};"
  CREATED=1
fi

echo "==> applying harness + 005 + 012 (not production)"
psql_db -f sql/test_harness.sql
psql_db -f sql/dev/000_harness_base.sql
psql_db -f sql/005_multiuser.sql
psql_db -f sql/dev/012_harness_extras.sql
psql_db -f sql/012_invite_provision.sql

echo "==> running sql/test_migration_012.sql"
psql_db -f sql/test_migration_012.sql

echo "==> residue check (agent 1 + 40-45 fixtures must be gone)"
psql_db -c "select count(*) as leftover_012_agents from agents where agent_no = 1 or agent_no between 40 and 45;" \
  | tee /tmp/foound_012_residue.txt
leftover="$(psql_db -tAc "select count(*) from agents where agent_no = 1 or agent_no between 40 and 45;")"
if [[ "${leftover}" != "0" ]]; then
  echo "FAIL: leftover 012 fixture agents: ${leftover}"
  exit 1
fi
echo "M012 harness residue: 0"

if [[ "${CREATED}" == "1" && "${FOOUND_012_KEEP_DB:-}" != "1" ]]; then
  echo "==> dropping disposable database ${PGDATABASE}"
  psql_admin -c "drop database if exists ${PGDATABASE};"
fi

echo "M012 HARNESS OK"
