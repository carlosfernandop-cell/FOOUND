#!/usr/bin/env bash
# Two session interleavings for migration 017 (edition write authority).
# Disposable Postgres ONLY. Never the live FOOUND project.
#
# Each scenario runs two psql sessions against the same database:
#   engine : the service role inserting an edition (the trigger takes the
#            agent FOR SHARE and reads the active Brief without a lock) and
#            holding its transaction open for HOLD seconds before committing.
#   person : the authenticated owner calling pause_agent() or
#            activate_brief(...) through the SQL doors, or, in the manual
#            scenarios, issuing activate_brief's own statements one by one
#            with a gap between them, which is the only way to place the
#            engine's write between a door's two locks.
# The session that starts second must WAIT for the first to commit (the
# elapsed time proves the lock, not luck), then see the committed truth.
#
#   C0 REPRODUCTION against the v1 guard (agent then Brief, both FOR SHARE):
#      re-activation of the already active Brief locks B then A; the v1
#      guard A then B between those two statements: SQLSTATE 40P01 expected.
#      The v1 function is installed for this scenario only, then 017 is
#      re-applied and the same interleaving must complete without 40P01.
#   C1 engine first, then pause     -> pause waits; edition v3 landed; paused
#   C2 pause first, then engine     -> engine waits; refused authority_changed:paused
#   C3 engine first, then activate  -> activate waits; edition v3 landed; v4 active
#   C4 activate first, then engine  -> engine waits; refused authority_changed:brief_superseded
#   C5 engine first, then re-activate the active Brief v3 -> re-activation waits; lands
#   C6 re-activate v3 first (held), then engine -> engine waits; lands at v3
#   C7 activate an unconfirmed proposal is impossible through the door
#      (activate_brief sets confirmed_at); the guard's brief_unconfirmed case
#      is proven in the single session battery (E8).
#
# Deadlock is asserted absent in C1 to C6: neither session may report 40P01.
# Expects the harness chain already applied (run_017_harness.sh does this).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SQL_017="${FOOUND_017_SQL:-${ROOT}/sql/017_edition_authority.sql}"

export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"
export PGPASSWORD="${PGPASSWORD:-harness}"
: "${PGDATABASE:?PGDATABASE must name the disposable harness database}"

if [[ ! "${PGDATABASE}" =~ ^foound_017(_[0-9]+)?$ ]]; then
  echo "REFUSED: target must be foound_017 or foound_017_<n>"
  exit 2
fi
if [[ "${PGHOST}" != "127.0.0.1" && "${PGHOST}" != "localhost" ]]; then
  echo "REFUSED: harness requires an isolated local PostgreSQL service"
  exit 2
fi

HOLD="${FOOUND_017_HOLD:-2}"      # seconds the first session holds its locks
GAP="${FOOUND_017_GAP:-0.7}"      # seconds before the second session starts
AGENT='a6060606-0000-4000-8000-000000000061'
USER_ID='61616161-6161-4616-8616-616161616161'
B3='b6060606-0000-4000-8000-000000000013'
B4='b6060606-0000-4000-8000-000000000014'
DAY='2026-09-06'

psql_q() { psql -v ON_ERROR_STOP=1 -qAtX "$@"; }

foreign="$(psql_q -c "select count(*) from agents where agent_no not between 60 and 61;")"
if [[ "${foreign}" != "0" ]]; then
  echo "REFUSED: '${PGDATABASE}' holds ${foreign} agents outside the 017 fixture range; not disposable"
  exit 2
fi

fixture() {
  psql_q <<SQL
create table if not exists c017_log (scenario text, who text, note text, sqlstate text, elapsed float8);
grant usage on schema public, auth to authenticated, anon, service_role;
grant select, insert, update, delete on all tables in schema public to authenticated, service_role;
grant execute on function auth.uid() to authenticated, anon;
delete from c017_log where scenario = '$1';
delete from jobs where agent_id = '$AGENT';
delete from editions where agent_id = '$AGENT';
delete from briefs where agent_id = '$AGENT';
delete from agents where id = '$AGENT';
insert into auth.users (id, email) values ('$USER_ID','n061@example.com')
  on conflict (id) do update set email = excluded.email;
insert into agents (id, user_id, agent_no, state) values ('$AGENT','$USER_ID',61,'at_work');
insert into briefs (id, agent_id, version, state, content, confirmed_at) values
  ('$B3','$AGENT',3,'active','{"chapters":[{"title":"ROLE SPACE","subjects":[{"handle":"Craft","lines":["Head of Design."]}]}]}', now()),
  ('$B4','$AGENT',4,'proposed','{"chapters":[{"title":"ROLE SPACE","subjects":[{"handle":"Craft","lines":["VP Design."]}]}]}', null);
SQL
}

# engine session: one transaction, insert then hold, then commit. The refusal
# (if any) is caught inside the transaction and logged with its SQLSTATE.
engine_session() {
  local scenario="$1"
  psql -qAtX >/dev/null <<SQL
\\set ON_ERROR_STOP off
set role service_role;
begin;
do \$c\$
declare t0 float8 := extract(epoch from clock_timestamp());
begin
  insert into editions (agent_id, edition_date, brief_version, html, payload, outcome)
  values ('$AGENT','$DAY',3,'<p>v3</p>','{}','empty');
  insert into c017_log values ('$scenario','engine','landed', '00000', extract(epoch from clock_timestamp()) - t0);
exception when others then
  insert into c017_log values ('$scenario','engine', sqlerrm, sqlstate, extract(epoch from clock_timestamp()) - t0);
end
\$c\$;
select pg_sleep($HOLD);
commit;
SQL
}

# person session: the door call, timed. Doors return text, they do not raise,
# except a deadlock, which would surface as an error and be logged as such.
person_session() {
  local scenario="$1" door="$2"
  psql -qAtX >/dev/null <<SQL
\\set ON_ERROR_STOP off
begin;
select set_config('test.uid','$USER_ID', true);
set local role authenticated;
do \$c\$
declare t0 float8 := extract(epoch from clock_timestamp()); r text;
begin
  select $door into r;
  reset role;
  insert into c017_log values ('$scenario','person', r, '00000', extract(epoch from clock_timestamp()) - t0);
exception when others then
  reset role;
  insert into c017_log values ('$scenario','person', sqlerrm, sqlstate, extract(epoch from clock_timestamp()) - t0);
end
\$c\$;
select pg_sleep($HOLD);
commit;
SQL
}

# person, by hand: activate_brief's own two locks for the already active
# Brief (B for update, then A for update) with a pause between them, so the
# engine's write can land between the two. Same order the door takes.
person_manual_reactivation() {
  local scenario="$1" pause="$2"
  psql -qAtX >/dev/null <<SQL
\\set ON_ERROR_STOP off
begin;
do \$c\$
declare t0 float8 := extract(epoch from clock_timestamp()); r record;
begin
  select * into r from briefs where id = '$B3' for update;
  perform pg_sleep($pause);
  select * into r from agents where id = '$AGENT' for update;
  insert into c017_log values ('$scenario','person', 'locked B then A', '00000', extract(epoch from clock_timestamp()) - t0);
exception when others then
  insert into c017_log values ('$scenario','person', sqlerrm, sqlstate, extract(epoch from clock_timestamp()) - t0);
end
\$c\$;
select pg_sleep($HOLD);
commit;
SQL
}

# the v1 guard, kept only to reproduce the deadlock it allowed
install_v1_guard() {
  psql_q <<'SQL'
create or replace function guard_edition_authority() returns trigger
language plpgsql security definer set search_path = public as '
declare a_state text; b_version int;
begin
  select state into a_state from agents where id = NEW.agent_id for share;
  if a_state is null then raise exception ''authority_changed:no_agent''; end if;
  if a_state = ''paused'' then raise exception ''authority_changed:paused''; end if;
  if a_state <> ''at_work'' then raise exception ''authority_changed:state_%'', a_state; end if;
  select version into b_version from briefs
    where agent_id = NEW.agent_id and state = ''active'' for share;
  if b_version is null then raise exception ''authority_changed:no_active_brief''; end if;
  if NEW.brief_version is null then raise exception ''authority_changed:no_version''; end if;
  if NEW.brief_version <> b_version then raise exception ''authority_changed:brief_superseded''; end if;
  return NEW;
end';
SQL
}

# assert scenario who note-regex min_elapsed
expect() {
  local scenario="$1" who="$2" want="$3" min="$4"
  local row
  row="$(psql_q -c "select note||'|'||sqlstate||'|'||round(elapsed::numeric,2) from c017_log where scenario='$scenario' and who='$who'")"
  local note="${row%%|*}" rest="${row#*|}" state elapsed
  state="${rest%%|*}"; elapsed="${rest#*|}"
  if [[ "$state" == "40P01" ]]; then echo "FAIL $scenario $who: DEADLOCK (40P01)"; exit 1; fi
  if [[ ! "$note" =~ $want ]]; then echo "FAIL $scenario $who: got '$note' ($state), want /$want/"; exit 1; fi
  if ! awk -v e="$elapsed" -v m="$min" 'BEGIN{exit !(e+0 >= m+0)}'; then
    echo "FAIL $scenario $who: elapsed ${elapsed}s < ${min}s (did not wait on the lock)"; exit 1
  fi
  echo "  ok  $scenario $who: $note (${elapsed}s)"
}

expect_deadlock() {  # the reproduction: at least one session was the deadlock victim
  local n
  n="$(psql_q -c "select count(*) from c017_log where scenario='$1' and sqlstate='40P01'")"
  if [[ "$n" == "0" ]]; then echo "FAIL $1: expected a deadlock under the v1 guard, none occurred"; exit 1; fi
  echo "  ok  $1: deadlock reproduced under the v1 guard (40P01 in $n session)"
}

final() {  # scenario expected_state expected_edition_count expected_active_version
  local s="$1" st="$2" n="$3" v="$4" got
  got="$(psql_q -c "select (select state from agents where id='$AGENT')||'|'||(select count(*) from editions where agent_id='$AGENT')||'|'||coalesce((select version::text from briefs where agent_id='$AGENT' and state='active'),'none')")"
  if [[ "$got" != "$st|$n|$v" ]]; then echo "FAIL $s final state: got '$got', want '$st|$n|$v'"; exit 1; fi
  echo "  ok  $s final: agent=$st editions=$n active=v$v"
}

MIN_WAIT="$(awk -v h="$HOLD" -v g="$GAP" 'BEGIN{print h-g-0.3}')"

echo "C0 reproduction: v1 guard (A then B) against re-activation of the active Brief (B then A)"
fixture C0
install_v1_guard
person_manual_reactivation C0 1.0 & p1=$!
sleep 0.4
engine_session C0 & p2=$!
wait $p1 $p2
expect_deadlock C0
echo "==> re-applying 017 (the one lock guard) from ${SQL_017}"
psql_q -f "$SQL_017" >/dev/null
echo "C0b the same interleaving under 017: no deadlock, the write lands, re-activation waits"
fixture C0b
person_manual_reactivation C0b 1.0 & p1=$!
sleep 0.4
engine_session C0b & p2=$!
wait $p1 $p2
expect C0b engine '^landed$' 0
expect C0b person '^locked B then A$' "$(awk -v h="$HOLD" 'BEGIN{print h-0.4-0.3}')"
final C0b at_work 1 3

echo "C1 engine writes (holding), then the person pauses"
fixture C1
engine_session C1 & p1=$!
sleep "$GAP"
person_session C1 "pause_agent()" & p2=$!
wait $p1 $p2
expect C1 engine '^landed$' 0
expect C1 person '^paused$' "$MIN_WAIT"
final C1 paused 1 3

echo "C2 the person pauses (holding), then the engine writes"
fixture C2
person_session C2 "pause_agent()" & p1=$!
sleep "$GAP"
engine_session C2 & p2=$!
wait $p1 $p2
expect C2 person '^paused$' 0
expect C2 engine '^authority_changed:paused$' "$MIN_WAIT"
final C2 paused 0 3

echo "C3 engine writes (holding), then the person activates Brief v4"
fixture C3
engine_session C3 & p1=$!
sleep "$GAP"
person_session C3 "activate_brief('$B4')" & p2=$!
wait $p1 $p2
expect C3 engine '^landed$' 0
expect C3 person '^active:v4$' "$MIN_WAIT"
final C3 at_work 1 4
stale="$(psql_q -c "select brief_version from editions where agent_id='$AGENT' and edition_date='$DAY'")"
echo "  note C3: the day's edition carries brief_version=$stale while v4 is active (engine rewrites the day)"

echo "C4 the person activates Brief v4 (holding), then the engine writes"
fixture C4
person_session C4 "activate_brief('$B4')" & p1=$!
sleep "$GAP"
engine_session C4 & p2=$!
wait $p1 $p2
expect C4 person '^active:v4$' 0
expect C4 engine '^authority_changed:brief_superseded$' "$MIN_WAIT"
final C4 at_work 0 4

echo "C5 engine writes (holding), then the person re-activates the active Brief v3"
fixture C5
engine_session C5 & p1=$!
sleep "$GAP"
person_session C5 "activate_brief('$B3')" & p2=$!
wait $p1 $p2
expect C5 engine '^landed$' 0
expect C5 person '^active:v3$' "$MIN_WAIT"
final C5 at_work 1 3

echo "C6 the person re-activates the active Brief v3 (holding), then the engine writes"
fixture C6
person_session C6 "activate_brief('$B3')" & p1=$!
sleep "$GAP"
engine_session C6 & p2=$!
wait $p1 $p2
expect C6 person '^active:v3$' 0
expect C6 engine '^landed$' "$MIN_WAIT"
final C6 at_work 1 3

echo "cleanup"
psql_q <<SQL
drop table if exists c017_log;
delete from jobs where agent_id = '$AGENT';
delete from editions where agent_id = '$AGENT';
delete from briefs where agent_id = '$AGENT';
delete from agents where id = '$AGENT';
delete from auth.users where id = '$USER_ID';
SQL
echo "M017 CONCURRENCY OK"
