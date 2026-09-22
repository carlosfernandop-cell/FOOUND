#!/usr/bin/env bash
# Disposable local/CI harness for migration 018 (synthesis request boundary).
# NEVER against production, never against a database that holds anything.
#
# Applies: test_harness + 000_harness_base + 005 .. 010 + 011 extras + 011 +
#          013 + 015 + 016 + 017 + 018 (018 twice: idempotence), then runs the
#          REAL runner against the REAL doors: test_synthesis_runner.py (the
#          existing R battery) and test_upload_boundary.py (B1 to B12).
# Needs psycopg2 (pip install psycopg2-binary) and pytest.
#
# Safety, as for 017 (no override): the target must be named foound_test or
# foound_018 or foound_018_<n>; PGHOST must be local; nothing existing is
# ever dropped; an existing target must hold no user relations; a failed
# preflight aborts.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT"
export PGHOST="${PGHOST:-127.0.0.1}" PGPORT="${PGPORT:-5432}" PGUSER="${PGUSER:-postgres}" PGPASSWORD="${PGPASSWORD:-harness}"
CREATED=0
if [[ -z "${PGDATABASE:-}" ]]; then export PGDATABASE="foound_018_$$"; FOOUND_018_CREATE_DB=1; fi
if [[ ! "${PGDATABASE}" =~ ^(foound_test|foound_018(_[0-9]+)?)$ ]]; then echo "REFUSED: target must be foound_test, foound_018 or foound_018_<n>"; exit 2; fi
if [[ "${PGHOST}" != "127.0.0.1" && "${PGHOST}" != "localhost" ]]; then echo "REFUSED: harness requires an isolated local PostgreSQL service"; exit 2; fi
psql_admin() { PGDATABASE=postgres psql -v ON_ERROR_STOP=1 "$@"; }
psql_db() { psql -v ON_ERROR_STOP=1 "$@"; }
if [[ "${FOOUND_018_CREATE_DB:-}" == "1" ]]; then
  exists="$(psql_admin -tAc "select count(*) from pg_database where datname = '${PGDATABASE}';")"
  if [[ "${exists}" != "0" ]]; then echo "REFUSED: database '${PGDATABASE}' already exists; this harness never drops a database it did not create"; exit 2; fi
  echo "==> creating disposable database ${PGDATABASE}"; psql_admin -c "create database ${PGDATABASE};"; CREATED=1
fi
echo "==> validating target ${PGDATABASE} before DDL"
tables="$(psql_db -tAc "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where c.relkind in ('r','p','v','m','f') and n.nspname not in ('information_schema') and n.nspname !~ '^pg_';")" || { echo "REFUSED: preflight query failed"; exit 2; }
if [[ "${tables}" != "0" ]]; then echo "REFUSED: harness requires an empty target, found ${tables} existing relations"; exit 2; fi
echo "==> applying harness + 005..010 + 011 + 013 + 015 + 016 + 017 + 018 (not production)"
for f in sql/test_harness.sql sql/dev/000_harness_base.sql sql/005_multiuser.sql sql/006_jobs.sql sql/007_evidence_intake.sql sql/008_synthesis_settlement.sql sql/009_mirror_doors.sql sql/010_memory_handles.sql sql/dev/011_harness_extras.sql sql/011_commission_recovery.sql sql/013_brief_doors.sql sql/015_candidate_doors.sql sql/016_seen.sql sql/017_edition_authority.sql sql/018_synthesis_request_boundary.sql sql/018_synthesis_request_boundary.sql; do
  psql_db -q -f "$f" >/dev/null
done
echo "==> running the real runner against the real doors"
FOOUND_TEST_DSN="dbname=${PGDATABASE} user=${PGUSER} host=${PGHOST} password=${PGPASSWORD} port=${PGPORT}" \
  python3 -m pytest -q test_synthesis_runner.py test_upload_boundary.py -p no:cacheprovider
echo "==> residue check (runner fixture agents 300-399 must be gone)"
psql_db -qc "delete from agents where agent_no between 300 and 399;" >/dev/null
if [[ "${CREATED}" == "1" && "${FOOUND_018_KEEP_DB:-}" != "1" ]]; then echo "==> dropping the database this run created: ${PGDATABASE}"; psql_admin -c "drop database ${PGDATABASE};"; fi
echo "M018 HARNESS OK"
