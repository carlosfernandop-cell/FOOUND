#!/usr/bin/env bash
# Disposable local/CI harness for migration 017 (edition write authority).
# NEVER against production, never against a database that holds anything.
#
# Applies: test_harness + 000_harness_base + 005 + 006 + extras + 013 + 017
#          (twice: idempotence) + test_017 (single session) + the two session
#          interleavings in run_017_concurrency.sh.
#
# Safety (revised after reciprocal review; there is no override):
#   · the target's name must match ^foound_017(_[0-9]+)?$ and PGHOST must be
#     127.0.0.1 or localhost; anything else is refused before any connection
#     does work;
#   · nothing is ever dropped at setup. FOOUND_017_CREATE_DB=1 creates the
#     database and aborts if it already exists; only a database this run
#     created is dropped at the end (and not when FOOUND_017_KEEP_DB=1);
#   · before any DDL the target must hold no user relations at all; a
#     preflight query that fails aborts the run (never read as "empty").
#
# Usage:
#   bash sql/dev/run_017_harness.sh                # creates foound_017_<pid>, drops it after
#   PGDATABASE=foound_017 bash sql/dev/run_017_harness.sh   # CI: existing empty database
# Env: PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE
#      FOOUND_017_CREATE_DB=1 FOOUND_017_KEEP_DB=1 FOOUND_017_SKIP_CONCURRENCY=1
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"
export PGPASSWORD="${PGPASSWORD:-harness}"

CREATED=0
if [[ -z "${PGDATABASE:-}" ]]; then
  export PGDATABASE="foound_017_$$"
  FOOUND_017_CREATE_DB=1
fi

psql_admin() { PGDATABASE=postgres psql -v ON_ERROR_STOP=1 "$@"; }
psql_db()    { psql -v ON_ERROR_STOP=1 "$@"; }

# 1. the name must say disposable, or the operator must say it explicitly
if [[ ! "${PGDATABASE}" =~ ^foound_017(_[0-9]+)?$ ]]; then
  echo "REFUSED: target must be foound_017 or foound_017_<n>"
  exit 2
fi
if [[ "${PGHOST}" != "127.0.0.1" && "${PGHOST}" != "localhost" ]]; then
  echo "REFUSED: harness requires an isolated local PostgreSQL service"
  exit 2
fi

if [[ "${FOOUND_017_CREATE_DB:-}" == "1" ]]; then
  exists="$(psql_admin -tAc "select count(*) from pg_database where datname = '${PGDATABASE}';")"
  if [[ "${exists}" != "0" ]]; then
    echo "REFUSED: database '${PGDATABASE}' already exists; this harness never drops a database it did not create"
    exit 2
  fi
  echo "==> creating disposable database ${PGDATABASE}"
  psql_admin -c "create database ${PGDATABASE};"
  CREATED=1
fi

# 2. the target must hold nothing of anyone's before any DDL runs
echo "==> validating target ${PGDATABASE} before DDL"
tables="$(psql_db -tAc "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where c.relkind in ('r','p','v','m','f') and n.nspname <> 'information_schema' and n.nspname !~ '^pg_';")"
if [[ "${tables}" != "0" ]]; then
  echo "REFUSED: harness requires an empty target, found ${tables} existing relations"
  exit 2
fi

echo "==> applying harness + 005 + 006 + 013 + 017 (not production)"
psql_db -f sql/test_harness.sql
psql_db -f sql/dev/000_harness_base.sql
psql_db -f sql/005_multiuser.sql
psql_db -f sql/006_jobs.sql
psql_db -f sql/dev/011_harness_extras.sql
psql_db -f sql/013_brief_doors.sql
psql_db -f sql/017_edition_authority.sql
# idempotence: a second application must be a no-op
psql_db -f sql/017_edition_authority.sql

echo "==> running sql/test_migration_017.sql (single session)"
psql_db -f sql/test_migration_017.sql

if [[ "${FOOUND_017_SKIP_CONCURRENCY:-}" != "1" ]]; then
  echo "==> running sql/dev/run_017_concurrency.sh (two sessions)"
  bash sql/dev/run_017_concurrency.sh
fi

echo "==> residue check (agents 60-61 must be gone)"
leftover="$(psql_db -tAc "select count(*) from agents where agent_no between 60 and 61;")"
if [[ "${leftover}" != "0" ]]; then
  echo "FAIL: leftover 017 fixture agents: ${leftover}"
  exit 1
fi
echo "M017 harness residue: 0"

if [[ "${CREATED}" == "1" && "${FOOUND_017_KEEP_DB:-}" != "1" ]]; then
  echo "==> dropping the database this run created: ${PGDATABASE}"
  psql_admin -c "drop database ${PGDATABASE};"
fi

echo "M017 HARNESS OK"
