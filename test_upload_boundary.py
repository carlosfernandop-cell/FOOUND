"""The synthesis request boundary (migration 018, rev 3) against the REAL
doors in the local Postgres harness, model and storage stubbed. Database
integration tests, not emulations: every claim, settle and finalize runs the
SQL doors; only the model and the object fetch are stubs. The storage.objects
stub records that an upload completed (metadata), which is what the door
reads; it does not prove object bytes, and neither does the door.

Written failing against 007's claim_synthesis_batch, then green with 018.

B1  a file row whose object is not uploaded yet is not claimed and not failed
B2  an explicit request reads only the ids it names; an earlier incomplete row is untouched
B3  a legacy job (no key) reads ready rows made before the request, never after
B4  foreign, unknown, deleted and read ids are skipped and only counted
B5  the upload lands while the request waits: the next beat reads it
B6  the wait is a trusted clock: after it the ready rows are read and the missing row stays received
B7  nothing ready after the wait: honest failure, the row stays received (recoverable)
B8  removal before claim: never claimed
B9  a replayed request cannot queue twice (006 index)
B10 text items never wait for an object
B11 the runner walks past a waiting job to the next agent's job in the same beat
B12 legacy job with a late upload: never waits, the row waits for the next request
M1  malformed key types (null, number, string, object) fail closed: refused, nothing claimed
M2  empty list is explicit and empty; invalid entries are counted and dropped; duplicates collapse
M3  payload.deferrals and other client fields are ignored; started_at cannot be preset by a client
M4  a malformed oldest request cannot block a later agent's request
C1  removal in flight before the claim commits: the claim is observed waiting on the row lock, then re-evaluates: not claimed
C2  claim holding its locks: the removal is observed waiting, lands after commit, settle records it withdrawn
C3  an upload committing during the wait is seen on the next beat, never marked failed
C4  two connections claiming the same job: the second is observed waiting on the job lock, then gets job_not_queued
F1  fairness: eight older waiting requests and a ready ninth; the ninth is read by the second beat
F2  the runner's mirror of synthesis_wait_minutes() equals the door's
F3  requests already waiting are never walked while a ready request exists; idle beats re-ask the oldest, bounded (one claim each per idle beat)
P1  an authenticated owner can read jobs.payload of their own jobs; another owner and anon read nothing (006 policy, harness grants)
P2  a client supplied jobs.id is accepted for the owner's agent, refused for a foreign agent; the owner reads it by exact id
    through queued, running, done and failed with payload->evidence unchanged (whole payload gains read_scope at claim);
    another owner reads no row in any status
P3  the SAME client supplied id inserted again, while queued, running, done or failed, never creates a second job and never
    changes status or payload: the primary key refuses it (23505; while queued a partial index may be reported first);
    a DIFFERENT id while one is queued or running is refused by a partial unique index (006 queued per type, 007
    jobs_one_active_synthesize covers queued AND running); once terminal a new id is accepted and rows already read
    are skipped; another owner's insert of the id collides (existence only) without reading; no identity cannot insert
X1  the pinned old runner (9a4bf0f) against the new door: stalls until the door's clock ends the wait
"""
import json
import threading
import time
import uuid

import psycopg2
import pytest

import synthesis_runner as sr
from test_synthesis_runner import (
    DSN, SpyDb, StubStorage, StubModel, mk_agent, mk_text, job_row,
    item_row, model_json, five_grounded, make_runner,
)

COPY_WAITED = "FOOUND waited for your file and it did not arrive. Check it is still there, then hand it off again."
COPY_MALFORMED = "FOOUND could not tell what you asked it to read. Hand it off again."
COPY_EMPTY = "There was nothing new to read. Add evidence first."


@pytest.fixture(scope="module")
def db():
    d = SpyDb(DSN)
    d._rows("delete from agents where agent_no between 300 and 399 returning id")
    yield d


@pytest.fixture()
def fresh(db):
    db.settle_calls = 0
    db.before_settle = None
    db.before_evidence_rows = None
    db.claim_hook = None
    yield db


def mk_file(db, uid, aid, label, data, storage, uploaded=True, mime="text/plain"):
    iid = str(uuid.uuid4())
    path = f"{uid}/{iid}/blob"
    db._rows(
        "insert into evidence_items (id,agent_id,kind,label,storage_path,"
        "mime_type,byte_size) values (%s,%s,'file',%s,%s,%s,%s) returning id",
        (iid, aid, label, path, mime, len(data)),
    )
    if uploaded:
        upload(db, path, data, storage)
    return iid


def upload(db, path, data, storage):
    storage.objects[path] = data
    db._rows("insert into storage.objects (bucket_id, name) values ('feeds', %s) returning id", (path,))


def mk_request(db, aid, ids=None, raw=None):
    payload = raw if raw is not None else ({"evidence": list(ids)} if ids is not None else {})
    jid = str(uuid.uuid4())
    db._rows(
        "insert into jobs (id,agent_id,type,payload) values (%s,%s,'synthesize',%s) returning id",
        (jid, aid, json.dumps(payload)),
    )
    return jid


def claim(db, jid):
    return db._door("select claim_synthesis_batch(%s)", (jid,))


def status_of(db, iid):
    return item_row(db, iid)["status"]


def job_full(db, jid):
    return db._rows("select status, error, started_at, payload from jobs where id=%s", (jid,))[0]


def expire_wait(db, jid):
    """The wait is a clock on the service written started_at; move it back."""
    db._rows("update jobs set started_at = now() - interval '61 minutes' where id=%s returning id", (jid,))


def items(out):
    return sorted(i.lower() for i in out["items"])


# ---------------------------------------------------------------------------

def test_b1_row_without_object_is_neither_claimed_nor_failed(fresh):
    db = fresh; storage = StubStorage()
    uid, aid = mk_agent(db)
    ready = mk_text(db, aid, "note", "I lead design teams. " * 5)
    late = mk_file(db, uid, aid, "cv.txt", b"x" * 40, storage, uploaded=False)
    jid = mk_request(db, aid, [ready, late])
    out = claim(db, jid)
    assert out["status"] == "deferred" and out["pending"] == 1 and out["ready"] == 1, out
    assert status_of(db, late) == "received" and status_of(db, ready) == "received"
    j = job_full(db, jid)
    assert j["status"] == "queued" and j["started_at"] is not None  # waiting since, service written


def test_b2_explicit_request_leaves_an_earlier_incomplete_row_alone(fresh):
    db = fresh; storage = StubStorage()
    uid, aid = mk_agent(db)
    stale = mk_file(db, uid, aid, "old.txt", b"y" * 30, storage, uploaded=False)
    a = mk_text(db, aid, "note a", "Led brand at three companies.")
    out = claim(db, mk_request(db, aid, [a]))
    assert out["status"] == "claimed" and items(out) == [a]
    assert status_of(db, stale) == "received" and status_of(db, a) == "reading"


def test_b3_legacy_job_reads_ready_rows_before_the_request_never_after(fresh):
    db = fresh; storage = StubStorage()
    uid, aid = mk_agent(db)
    before = mk_text(db, aid, "before", "Managed a team of twelve people.")
    late = mk_file(db, uid, aid, "late.txt", b"z" * 20, storage, uploaded=False)
    jid = mk_request(db, aid, None)
    after = mk_text(db, aid, "after", "Based in Lisbon.")
    db._rows("update evidence_items set created_at = now() + interval '1 second' where id=%s returning id", (after,))
    out = claim(db, jid)
    assert out["status"] == "claimed" and items(out) == [before] and out["explicit"] is False
    assert status_of(db, late) == "received" and status_of(db, after) == "received"


def test_b4_foreign_unknown_deleted_and_read_ids_are_skipped_and_only_counted(fresh):
    db = fresh; storage = StubStorage()
    uid, aid = mk_agent(db)
    uid2, aid2 = mk_agent(db)
    foreign = mk_text(db, aid2, "theirs", "Someone else's record.")
    unknown = str(uuid.uuid4())
    gone = mk_text(db, aid, "gone", "Removed before the request.")
    db._rows("update evidence_items set status='deleted' where id=%s returning id", (gone,))
    mine = mk_text(db, aid, "mine", "I want senior brand leadership roles next.")
    out = claim(db, mk_request(db, aid, [foreign, unknown, gone, mine]))
    assert out["status"] == "claimed" and items(out) == [mine]
    assert out["skipped"] == 3 and out["malformed"] == 0
    assert foreign not in json.dumps(out) and unknown not in json.dumps(out)  # counts only, no echo
    assert status_of(db, foreign) == "received"


def test_b5_upload_lands_while_waiting_then_the_next_beat_reads_it(fresh):
    db = fresh; storage = StubStorage()
    uid, aid = mk_agent(db)
    data = b"Head of Design at Acme for six years. " * 3
    late = mk_file(db, uid, aid, "cv.txt", data, storage, uploaded=False)
    jid = mk_request(db, aid, [late])
    assert claim(db, jid)["status"] == "deferred"
    upload(db, f"{uid}/{late}/blob", data, storage)
    rep = make_runner(db, storage, StubModel([model_json(five_grounded(late))])).run_once()
    assert rep.action == "settled", rep
    assert status_of(db, late) == "read" and job_row(db, jid)["status"] == "done"


def test_b6_after_the_wait_ready_rows_are_read_and_the_missing_row_stays_received(fresh):
    db = fresh; storage = StubStorage()
    uid, aid = mk_agent(db)
    ok = mk_text(db, aid, "note", "Led brand at three companies.")
    never = mk_file(db, uid, aid, "lost.txt", b"q" * 10, storage, uploaded=False)
    jid = mk_request(db, aid, [ok, never])
    assert claim(db, jid)["status"] == "deferred"
    assert claim(db, jid)["status"] == "deferred"  # any number of calls within the window: still waiting
    expire_wait(db, jid)
    out = claim(db, jid)
    assert out["status"] == "claimed" and items(out) == [ok]
    assert out["waited_out"] is True and out["pending"] == 1
    assert status_of(db, never) == "received"          # no verdict from a clock; still held
    assert job_full(db, jid)["payload"]["read_scope"] == {
        "claimed": 1, "pending": 1, "skipped": 0, "malformed": 0, "waited_out": True}


def test_b7_nothing_ready_after_the_wait_fails_honestly_and_keeps_the_row(fresh):
    db = fresh; storage = StubStorage()
    uid, aid = mk_agent(db)
    never = mk_file(db, uid, aid, "lost.txt", b"q" * 10, storage, uploaded=False)
    jid = mk_request(db, aid, [never])
    assert claim(db, jid)["status"] == "deferred"
    expire_wait(db, jid)
    out = claim(db, jid)
    assert out["status"] == "empty" and out["waited_out"] is True
    j = job_row(db, jid)
    assert j["status"] == "failed" and j["error"] == COPY_WAITED
    assert status_of(db, never) == "received"          # recoverable: hand off again or remove


def test_b8_removal_before_claim_is_never_claimed(fresh):
    db = fresh
    uid, aid = mk_agent(db)
    a = mk_text(db, aid, "a", "Kept.")
    b = mk_text(db, aid, "b", "Removed.")
    jid = mk_request(db, aid, [a, b])
    db._rows("update evidence_items set status='deleted' where id=%s returning id", (b,))
    out = claim(db, jid)
    assert items(out) == [a] and out["skipped"] == 1 and status_of(db, b) == "deleted"


def test_b9_replayed_request_cannot_queue_twice(fresh):
    db = fresh
    uid, aid = mk_agent(db)
    a = mk_text(db, aid, "a", "Kept.")
    first = mk_request(db, aid, [a])
    with pytest.raises(psycopg2.errors.UniqueViolation):
        mk_request(db, aid, [a])
    db._rows("delete from jobs where id=%s returning id", (first,))


def test_b10_text_items_never_wait_for_an_object(fresh):
    db = fresh
    uid, aid = mk_agent(db)
    t = mk_text(db, aid, "t", "I value autonomy in how work is structured.")
    out = claim(db, mk_request(db, aid, [t]))
    assert out["status"] == "claimed" and out["pending"] == 0


def test_b11_runner_walks_past_a_waiting_job_in_the_same_beat(fresh):
    db = fresh; storage = StubStorage()
    uid1, aid1 = mk_agent(db)
    late = mk_file(db, uid1, aid1, "cv.txt", b"w" * 12, storage, uploaded=False)
    j1 = mk_request(db, aid1, [late])
    db._rows("update jobs set requested_at = now() - interval '1 minute' where id=%s returning id", (j1,))
    uid2, aid2 = mk_agent(db)
    t = mk_text(db, aid2, "t", "Led brand at three companies. " * 2)
    j2 = mk_request(db, aid2, [t])
    rep = make_runner(db, storage, StubModel([model_json(five_grounded(t))])).run_once()
    assert rep.action == "settled" and rep.job_id == j2, rep
    assert j1 in rep.detail.get("deferred", [])
    assert job_full(db, j1)["status"] == "queued"


def test_b12_legacy_job_with_a_late_upload_never_waits(fresh):
    db = fresh; storage = StubStorage()
    uid, aid = mk_agent(db)
    late = mk_file(db, uid, aid, "cv.txt", b"w" * 12, storage, uploaded=False)
    jid = mk_request(db, aid, None)
    out = claim(db, jid)
    assert out["status"] == "empty" and out["waited_out"] is False
    assert job_row(db, jid)["error"] == COPY_EMPTY
    assert status_of(db, late) == "received"


# ---- malformed intent ------------------------------------------------------

@pytest.mark.parametrize("raw", [
    {"evidence": None},
    {"evidence": 7},
    {"evidence": "not-a-list"},
    {"evidence": {"id": "x"}},
])
def test_m1_malformed_key_types_fail_closed(fresh, raw):
    db = fresh; storage = StubStorage()
    uid, aid = mk_agent(db)
    ready = mk_text(db, aid, "t", "A confirmed career fact.")
    late = mk_file(db, uid, aid, "cv.txt", b"w" * 12, storage, uploaded=False)
    jid = mk_request(db, aid, raw=raw)
    out = claim(db, jid)
    assert out["status"] == "refused" and out["reason"] == "malformed_request"
    assert status_of(db, ready) == "received" and status_of(db, late) == "received"  # nothing claimed
    j = job_row(db, jid)
    assert j["status"] == "failed" and j["error"] == COPY_MALFORMED


def test_m2_empty_list_invalid_entries_and_duplicates(fresh):
    db = fresh
    uid, aid = mk_agent(db)
    t = mk_text(db, aid, "t", "A confirmed career fact.")
    # empty list: explicit and empty, never the legacy scope
    out = claim(db, mk_request(db, aid, []))
    assert out["status"] == "empty" and out["waited_out"] is False
    assert status_of(db, t) == "received"
    # invalid entries counted and dropped; duplicates collapse; nothing echoed
    out = claim(db, mk_request(db, aid, raw={"evidence": [t, t, t.upper(), 5, None, "x", {"id": t}, "not a uuid"]}))
    assert out["status"] == "claimed" and items(out) == [t]
    assert out["malformed"] == 5 and out["skipped"] == 0
    assert "not a uuid" not in json.dumps(out)


def test_m3_client_fields_are_ignored_and_started_at_cannot_be_preset(fresh):
    db = fresh; storage = StubStorage()
    uid, aid = mk_agent(db)
    late = mk_file(db, uid, aid, "cv.txt", b"w" * 12, storage, uploaded=False)
    jid = mk_request(db, aid, raw={"evidence": [late], "deferrals": 99, "waiting_since": "1970-01-01T00:00:00Z",
                                   "read_scope": {"claimed": 9}})
    out = claim(db, jid)
    assert out["status"] == "deferred"                 # still waits: the clock is started_at
    assert job_full(db, jid)["started_at"] is not None
    assert status_of(db, late) == "received"
    db._rows("delete from jobs where id=%s returning id", (jid,))
    # the client cannot preset started_at: the 006 insert policy refuses it
    other = psycopg2.connect(DSN)
    other.autocommit = True
    cur = other.cursor()
    cur.execute("grant usage on schema public to authenticated; grant insert on jobs to authenticated")
    cur.execute("set role authenticated; select set_config('test.uid', %s, false)", (uid,))
    with pytest.raises(psycopg2.Error):
        cur.execute("insert into jobs (agent_id, type, started_at) values (%s, 'synthesize', now() - interval '2 hours')", (aid,))
    cur.execute("reset role")
    other.close()


def test_m4_a_malformed_oldest_request_cannot_block_a_later_agent(fresh):
    db = fresh; storage = StubStorage()
    uid1, aid1 = mk_agent(db)
    mk_text(db, aid1, "t", "Theirs.")
    j1 = mk_request(db, aid1, raw={"evidence": "junk"})
    db._rows("update jobs set requested_at = now() - interval '1 minute' where id=%s returning id", (j1,))
    uid2, aid2 = mk_agent(db)
    t = mk_text(db, aid2, "t", "Led brand at three companies. " * 2)
    j2 = mk_request(db, aid2, [t])
    runner = make_runner(db, storage, StubModel([model_json(five_grounded(t))]))
    first = runner.run_once()
    assert first.action == "refused" and first.job_id == j1     # terminal, this beat
    assert job_row(db, j1)["status"] == "failed"
    second = runner.run_once()
    assert second.action == "settled" and second.job_id == j2   # nothing blocks the next agent


# ---- two connections, real locks -------------------------------------------
# Synchronisation is a lock observation, not a sleep: the side that must block
# is a separate backend, and the test proceeds only once pg_stat_activity
# shows that backend waiting on a lock (wait_event_type = Lock). What is
# exercised is exactly that: a real row or job lock held by one transaction
# and a second transaction observed waiting on it, then released.

def other_connection():
    c = psycopg2.connect(DSN)
    c.autocommit = False
    return c


def backend_pid(conn):
    cur = conn.cursor(); cur.execute("select pg_backend_pid()")
    return cur.fetchone()[0]


def wait_until_blocked(pid, timeout=5.0):
    """Barrier: return once backend `pid` is observed waiting on a lock."""
    obs = psycopg2.connect(DSN); obs.autocommit = True
    try:
        cur = obs.cursor()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cur.execute("select wait_event_type, wait_event from pg_stat_activity where pid=%s", (pid,))
            row = cur.fetchone()
            if row and row[0] == "Lock":
                return row[1]
            time.sleep(0.01)
        raise AssertionError(f"backend {pid} was never observed waiting on a lock")
    finally:
        obs.close()


def test_c1_removal_in_flight_before_the_claim_commits_is_not_claimed(fresh):
    db = fresh
    uid, aid = mk_agent(db)
    a = mk_text(db, aid, "a", "Kept.")
    b = mk_text(db, aid, "b", "Being removed.")
    jid = mk_request(db, aid, [a, b])
    remover = other_connection()
    rc = remover.cursor()
    rc.execute("update evidence_items set status='deleted', deleted_at=now() where id=%s", (b,))  # b locked, uncommitted
    claimer = other_connection(); claimer.autocommit = True
    claimer_pid = backend_pid(claimer)
    result = {}

    def do_claim():
        cur = claimer.cursor()
        cur.execute("select claim_synthesis_batch(%s)", (jid,))
        result["out"] = cur.fetchone()[0]

    th = threading.Thread(target=do_claim); th.start()
    assert wait_until_blocked(claimer_pid) in ("transactionid", "tuple")   # the claim waits on b's row lock
    assert "out" not in result
    remover.commit(); remover.close()
    th.join(5); assert not th.is_alive()
    claimer.close()
    out = result["out"]
    assert items(out) == [a] and out["skipped"] == 1          # re-evaluated after the lock: b is deleted
    assert status_of(db, b) == "deleted" and status_of(db, a) == "reading"


def test_c2_claim_holding_locks_then_a_removal_lands_and_settle_records_it_withdrawn(fresh):
    db = fresh
    uid, aid = mk_agent(db)
    a = mk_text(db, aid, "a", "Kept.")
    b = mk_text(db, aid, "b", "Removed after claim.")
    jid = mk_request(db, aid, [a, b])
    holder = other_connection()
    hc = holder.cursor()
    hc.execute("select claim_synthesis_batch(%s)", (jid,))     # locks held, uncommitted
    out = hc.fetchone()[0]
    assert items(out) == sorted([a, b])
    remover = other_connection(); remover.autocommit = True
    remover_pid = backend_pid(remover)
    done = {}

    def do_remove():
        cur = remover.cursor()
        cur.execute("update evidence_items set status='deleted', deleted_at=now() where id=%s returning id", (b,))
        done["rows"] = cur.rowcount

    th = threading.Thread(target=do_remove); th.start()
    assert wait_until_blocked(remover_pid) in ("transactionid", "tuple")   # the removal waits for the claim
    assert "rows" not in done
    holder.commit(); holder.close()
    th.join(5); assert not th.is_alive()
    remover.close()
    assert done["rows"] == 1 and status_of(db, b) == "deleted"  # landed after the claim, as the contract allows
    settled = db.settle(jid, {"read": [a, b], "failed": [], "memory": [], "reinforce": []}, sr.POLICY)
    assert settled["items_withdrawn"] == 1 and settled["items_read"] == 1


def test_c3_upload_committing_during_the_wait_is_read_next_beat_never_failed(fresh):
    db = fresh; storage = StubStorage()
    uid, aid = mk_agent(db)
    data = b"Head of Design at Acme. " * 4
    late = mk_file(db, uid, aid, "cv.txt", data, storage, uploaded=False)
    jid = mk_request(db, aid, [late])
    other = other_connection()
    oc = other.cursor()
    oc.execute("insert into storage.objects (bucket_id, name) values ('feeds', %s)", (f"{uid}/{late}/blob",))
    out = claim(db, jid)                                       # the object row is not committed yet
    assert out["status"] == "deferred"
    other.commit(); other.close()
    storage.objects[f"{uid}/{late}/blob"] = data
    expire_wait(db, jid)                                       # even at the end of the wait
    out = claim(db, jid)
    assert out["status"] == "claimed" and items(out) == [late] and out["waited_out"] is False
    assert status_of(db, late) == "reading"


def test_c4_two_connections_claiming_the_same_job(fresh):
    db = fresh
    uid, aid = mk_agent(db)
    t = mk_text(db, aid, "t", "One.")
    jid = mk_request(db, aid, [t])
    holder = other_connection()
    hc = holder.cursor()
    hc.execute("select claim_synthesis_batch(%s)", (jid,))      # job row locked, uncommitted
    second = other_connection(); second.autocommit = True
    second_pid = backend_pid(second)
    outcome = {}

    def do_second():
        cur = second.cursor()
        try:
            cur.execute("select claim_synthesis_batch(%s)", (jid,))
            outcome["r"] = "claimed"
        except psycopg2.Error as e:
            outcome["r"] = (e.diag.message_primary or "").strip()

    th = threading.Thread(target=do_second); th.start()
    assert wait_until_blocked(second_pid) in ("transactionid", "tuple")   # waits on the job's FOR UPDATE
    assert "r" not in outcome
    holder.commit(); holder.close()
    th.join(5); assert not th.is_alive()
    second.close()
    assert outcome["r"] == "job_not_queued"


# ---- fairness: a bounded walk must still make progress ----------------------

def test_f1_eight_waiting_requests_do_not_starve_a_ready_ninth(fresh):
    """Source derived counterexample (Astra): run_once starts each beat with
    an empty exclusion list and stops after DEFERRED_WALK_LIMIT deferrals, so
    eight older waiting requests would be walked again every beat and a ready
    ninth never reached inside the 60 minute window. Discovery must skip
    requests the door has already put on its clock."""
    db = fresh; storage = StubStorage()
    n = sr.DEFERRED_WALK_LIMIT
    waiting = []
    for i in range(n):
        uid, aid = mk_agent(db)
        late = mk_file(db, uid, aid, "cv.txt", b"w" * 12, storage, uploaded=False)
        j = mk_request(db, aid, [late])
        db._rows("update jobs set requested_at = now() - make_interval(mins => %s) where id=%s returning id",
                 (n + 1 - i, j))
        waiting.append(j)
    uid9, aid9 = mk_agent(db)
    t = mk_text(db, aid9, "t", "Led brand at three companies. " * 2)
    j9 = mk_request(db, aid9, [t])
    runner = make_runner(db, storage, StubModel([model_json(five_grounded(t))]))
    beats = []
    for _ in range(3):
        rep = runner.run_once()
        beats.append((rep.action, rep.job_id, len(rep.detail.get("deferred", []))))
        if rep.job_id == j9:
            break
    # beat one converts the eight to waiting (their first claim each); beat two reaches the ninth
    assert beats[0][0] == "deferred" and beats[0][2] == n, beats
    assert len(beats) == 2 and beats[1] == ("settled", j9, 0), beats
    assert all(job_full(db, j)["status"] == "queued" and job_full(db, j)["started_at"] is not None
               for j in waiting)


def test_f3_already_waiting_requests_are_not_walked_when_a_ready_one_exists(fresh):
    """Tier one first: nine requests already on the door's clock (more than the
    walk limit) and a newer ready request; the ready one is read in this beat
    without a single claim spent on the waiting ones. In an idle beat the
    waiting ones are asked again (bounded), so a landed upload is read then."""
    db = fresh; storage = StubStorage()
    n = sr.DEFERRED_WALK_LIMIT + 1
    waiting = []
    for i in range(n):
        uid, aid = mk_agent(db)
        late = mk_file(db, uid, aid, "cv.txt", b"w" * 12, storage, uploaded=False)
        j = mk_request(db, aid, [late])
        db._rows("update jobs set requested_at = now() - make_interval(mins => %s) where id=%s returning id",
                 (n + 1 - i, j))
        assert claim(db, j)["status"] == "deferred"            # on the clock already
        waiting.append((j, uid, late))
    uid9, aid9 = mk_agent(db)
    t = mk_text(db, aid9, "t", "Led brand at three companies. " * 2)
    j9 = mk_request(db, aid9, [t])
    runner = make_runner(db, storage, StubModel([model_json(five_grounded(t))]))
    rep = runner.run_once()
    assert (rep.action, rep.job_id, rep.detail["deferred"]) == ("settled", j9, [])
    # idle beat: the waiting ones are asked again, oldest first, bounded
    j_old, uid_old, late_old = waiting[0]
    upload(db, f"{uid_old}/{late_old}/blob", b"w" * 12, storage)
    rep = make_runner(db, storage, StubModel([model_json(five_grounded(late_old))])).run_once()
    assert rep.job_id == j_old and rep.action == "settled" and rep.detail["deferred"] == []
    rep = make_runner(db, storage, StubModel([])).run_once()
    assert rep.action == "deferred" and len(rep.detail["deferred"]) == sr.DEFERRED_WALK_LIMIT


def test_p1_owner_reads_own_job_payload_foreign_owner_reads_nothing(fresh):
    """Source: 006 jobs_owner_read is a SELECT policy on every column for the
    owner's agents; jobs_owner_insert accepts payload. Table level privileges
    for authenticated are Supabase defaults on schema public, emulated here by
    the harness grants (sql/dev/011_harness_extras.sql); the live grant is not
    proven by this test."""
    db = fresh
    uid, aid = mk_agent(db)
    t = mk_text(db, aid, "t", "A confirmed career fact.")
    other_uid, other_aid = mk_agent(db)
    c = psycopg2.connect(DSN); c.autocommit = True
    cur = c.cursor()
    cur.execute("grant usage on schema public to authenticated; grant select, insert on jobs to authenticated")
    # the owner inserts the request with its intent, as the app would
    cur.execute("set role authenticated; select set_config('test.uid', %s, false)", (uid,))
    cur.execute("insert into jobs (agent_id, type, payload) values (%s, 'synthesize', %s) returning id",
                (aid, json.dumps({"evidence": [t]})))
    jid = cur.fetchone()[0]
    cur.execute("select payload->'evidence', status from jobs where id=%s", (jid,))
    row = cur.fetchone()
    assert row[0] == [t] and row[1] == "queued"                # the owner reads the requested set
    # another owner sees no row at all, not an empty payload
    cur.execute("select set_config('test.uid', %s, false)", (other_uid,))
    cur.execute("select count(*) from jobs where id=%s", (jid,)); assert cur.fetchone()[0] == 0
    cur.execute("select count(*) from jobs where agent_id=%s", (aid,)); assert cur.fetchone()[0] == 0
    # no user at all sees nothing
    cur.execute("select set_config('test.uid', '', false)")
    cur.execute("select count(*) from jobs where id=%s", (jid,)); assert cur.fetchone()[0] == 0
    cur.execute("reset role"); c.close()
    db._rows("delete from jobs where id=%s returning id", (jid,))


def test_p2_client_supplied_job_id_owner_reads_by_id_across_statuses(fresh):
    """Source: 006 jobs.id has a default but the insert policy does not
    constrain it, so the client may supply it; jobs_owner_read has no status
    filter; nothing in 007 to 018 deletes jobs; 018's claim adds
    payload.read_scope (jsonb_set) and leaves payload.evidence as inserted.
    Table privileges are the harness emulation of Supabase defaults."""
    db = fresh; storage = StubStorage()
    uid, aid = mk_agent(db)
    a = mk_text(db, aid, "a", "Led brand at three companies. " * 2)
    b = mk_text(db, aid, "b", "Team of twelve. Lisbon. Autonomy.")
    other_uid, other_aid = mk_agent(db)
    j_done, j_failed = str(uuid.uuid4()), str(uuid.uuid4())
    c = psycopg2.connect(DSN); c.autocommit = True
    cur = c.cursor()
    cur.execute("grant usage on schema public to authenticated; grant select, insert on jobs to authenticated")
    cur.execute("set role authenticated; select set_config('test.uid', %s, false)", (uid,))
    # a foreign agent with a chosen id: refused by the insert policy
    with pytest.raises(psycopg2.Error):
        cur.execute("insert into jobs (id, agent_id, type, payload) values (%s, %s, 'synthesize', %s)",
                    (j_done, other_aid, json.dumps({"evidence": [a]})))
    cur.execute("insert into jobs (id, agent_id, type, payload) values (%s, %s, 'synthesize', %s) returning id",
                (j_done, aid, json.dumps({"evidence": [a]})))
    assert cur.fetchone()[0] == j_done
    # the same id twice: primary key, whatever the owner
    with pytest.raises(psycopg2.Error):
        cur.execute("insert into jobs (id, agent_id, type, payload) values (%s, %s, 'synthesize', %s)",
                    (j_done, aid, json.dumps({"evidence": [a]})))

    def owner_sees(jid, as_uid):
        cur.execute("select set_config('test.uid', %s, false)", (as_uid,))
        cur.execute("select status, payload->'evidence', payload ? 'read_scope' from jobs where id=%s", (jid,))
        return cur.fetchone()

    assert owner_sees(j_done, uid) == ("queued", [a], False)
    assert owner_sees(j_done, other_uid) is None
    # queued -> running (claim) -> done (settle), through the real runner
    rep = make_runner(db, storage, StubModel([model_json(five_grounded(a))])).run_once()
    assert rep.job_id == j_done and rep.action == "settled"
    assert owner_sees(j_done, uid) == ("done", [a], True)      # evidence unchanged; read_scope added at claim
    assert owner_sees(j_done, other_uid) is None
    # a second chosen id: queued -> running -> failed (finalize)
    cur.execute("select set_config('test.uid', %s, false)", (uid,))
    cur.execute("insert into jobs (id, agent_id, type, payload) values (%s, %s, 'synthesize', %s) returning id",
                (j_failed, aid, json.dumps({"evidence": [b]})))
    out = claim(db, j_failed)
    assert out["status"] == "claimed" and owner_sees(j_failed, uid) == ("running", [b], True)
    db.finalize_failed(j_failed, "FOOUND could not finish reading. Try again.")
    assert owner_sees(j_failed, uid) == ("failed", [b], True)
    assert owner_sees(j_failed, other_uid) is None
    cur.execute("reset role"); c.close()


def test_p3_same_id_reinsert_never_creates_or_mutates_a_job(fresh):
    db = fresh; storage = StubStorage()
    uid, aid = mk_agent(db)
    a = mk_text(db, aid, "a", "Led brand at three companies. " * 2)
    b = mk_text(db, aid, "b", "Team of twelve. Lisbon. Autonomy.")
    other_uid, other_aid = mk_agent(db)
    X, Y, Z = (str(uuid.uuid4()) for _ in range(3))
    c = psycopg2.connect(DSN); c.autocommit = True
    cur = c.cursor()
    cur.execute("grant usage on schema public to authenticated; grant select, insert on jobs to authenticated")
    cur.execute("set role authenticated")

    def as_user(u):
        cur.execute("select set_config('test.uid', %s, false)", (u or "",))

    def insert(jid, agent, ev):
        """(sqlstate, constraint) of a refused insert, or None when a row was created."""
        try:
            cur.execute("insert into jobs (id, agent_id, type, payload) values (%s, %s, 'synthesize', %s)",
                        (jid, agent, json.dumps({"evidence": ev})))
            return None
        except psycopg2.Error as e:
            return (e.pgcode, e.diag.constraint_name)

    def snapshot(jid):
        return db._rows("select status, payload, requested_at, started_at, completed_at, error "
                        "from jobs where id=%s", (jid,))

    def count(jid):
        return db._rows("select count(*) as n from jobs where id=%s", (jid,))[0]["n"]

    as_user(uid)
    assert insert(X, aid, [a]) is None
    q0 = snapshot(X)
    # same id, same or different payload, while queued: refused, nothing changes
    same_q = insert(X, aid, [a]); diff_q = insert(X, aid, [b])
    assert same_q[0] == "23505" and diff_q[0] == "23505"
    assert same_q[1] in ("jobs_pkey", "jobs_one_queued_per_type")   # both are violated while queued; recorded below
    assert count(X) == 1 and snapshot(X) == q0
    # a different id while one is queued: a partial unique index, not the primary key (006's
    # jobs_one_queued_per_type and 007's jobs_one_active_synthesize both apply; which name is reported is
    # an index order detail, recorded, not a contract)
    diff_id_q = insert(Y, aid, [a])
    assert diff_id_q[0] == "23505" and diff_id_q[1] in ("jobs_one_queued_per_type", "jobs_one_active_synthesize")
    assert count(Y) == 0
    # another owner, own agent, same id: collides (existence only), reads nothing
    as_user(other_uid)
    assert insert(X, other_aid, [a]) == ("23505", "jobs_pkey")
    cur.execute("select count(*) from jobs where id=%s", (X,)); assert cur.fetchone()[0] == 0
    # another owner naming this agent, or no identity at all: refused by the insert policy first
    assert insert(str(uuid.uuid4()), aid, [a])[0] == "42501"
    as_user(None)
    assert insert(str(uuid.uuid4()), aid, [a])[0] == "42501"
    as_user(uid)

    # running: checked from inside the real runner, after its claim and before its settle
    seen = {}

    def while_running(jid, results):
        r0 = snapshot(X)
        assert jid == X and r0[0]["status"] == "running" and r0[0]["payload"]["evidence"] == [a]
        seen["same_a"] = insert(X, aid, [a]); seen["same_b"] = insert(X, aid, [b])
        seen["new_id"] = insert(Y, aid, [a])      # 007 jobs_one_active_synthesize covers queued AND running
        seen["unchanged"] = (count(X) == 1 and snapshot(X) == r0 and count(Y) == 0)

    db.before_settle = while_running
    # done: X settled through the real runner, then the same id again
    rep = make_runner(db, storage, StubModel([model_json(five_grounded(a))])).run_once()
    assert rep.job_id == X and rep.action == "settled"
    assert seen["same_a"] == ("23505", "jobs_pkey") and seen["same_b"] == ("23505", "jobs_pkey")
    assert seen["new_id"] == ("23505", "jobs_one_active_synthesize") and seen["unchanged"]
    d0 = snapshot(X)
    assert d0[0]["status"] == "done"
    assert insert(X, aid, [a]) == ("23505", "jobs_pkey")
    assert count(X) == 1 and snapshot(X) == d0
    # once X is terminal a NEW id is accepted; naming the row X already read skips it: never read twice
    assert insert(Y, aid, [a]) is None
    out = claim(db, Y)
    assert out["status"] == "empty" and out["skipped"] == 1 and status_of(db, a) == "read"

    # failed: Z claimed then finalized failed, then the same id again
    assert insert(Z, aid, [b]) is None and claim(db, Z)["status"] == "claimed"
    db.finalize_failed(Z, "FOOUND could not finish reading. Try again.")
    f0 = snapshot(Z)
    assert f0[0]["status"] == "failed"
    assert insert(Z, aid, [b]) == ("23505", "jobs_pkey")
    assert count(Z) == 1 and snapshot(Z) == f0
    cur.execute("reset role"); c.close()
    print(f"P3 constraints reported: same id while queued={same_q[1]}, other id while queued={diff_id_q[1]}")


def test_f2_runner_wait_mirror_equals_the_door_clock(fresh):
    assert fresh._rows("select synthesis_wait_minutes() as m")[0]["m"] == sr.SYNTHESIS_WAIT_MINUTES


# ---- mixed versions: the runner on main today against the 018 door ---------

OLD_RUNNER_COMMIT = "9a4bf0fe5139dfc91172cf04378a392399f4c51b"   # engine main before 018 (= v6 runner)
OLD_RUNNER_SHA256 = "dd0004eaf7fe07020abd5af4857f090b8ca13f83ff4855ae88ddf7698d2094af"


def load_old_runner(tmp_path):
    """The runner exactly as deployed before 018, from the pinned commit: a
    verified fixture (source hash pinned) without a second copy of the file
    in the tree. Fails, never skips, when the commit is not readable; CI
    checks out with full history for this."""
    import hashlib, importlib.util, subprocess, sys
    try:
        old_src = subprocess.run(["git", "show", f"{OLD_RUNNER_COMMIT}:synthesis_runner.py"],
                                 capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, OSError) as e:
        pytest.fail(f"pinned pre-018 runner {OLD_RUNNER_COMMIT[:7]} not readable from git history "
                    f"(shallow checkout? use fetch-depth 0): {e}")
    digest = hashlib.sha256(old_src.encode()).hexdigest()
    assert digest == OLD_RUNNER_SHA256, f"pinned old runner source changed: {digest}"
    p = tmp_path / "old_synthesis_runner.py"
    p.write_text(old_src)
    spec = importlib.util.spec_from_file_location("old_synthesis_runner", p)
    old = importlib.util.module_from_spec(spec)
    sys.modules["old_synthesis_runner"] = old   # dataclasses resolve annotations by module name
    spec.loader.exec_module(old)
    assert not hasattr(old, "DEFERRED_WALK_LIMIT")            # it really is the pre-018 runner
    return old


def test_x1_old_runner_against_new_door_blocks_on_a_waiting_job(fresh, tmp_path):
    """The runner deployed before this patch treats 'deferred' as an
    unexpected claim status: it logs an error and ends the beat with the job
    still queued and oldest, so nothing behind it is processed until the
    door's own clock ends the wait. This is why the engine must be deployed
    before the door. Runs the ACTUAL pinned old module, not an emulation."""
    old = load_old_runner(tmp_path)

    db = fresh; storage = StubStorage()
    # earlier tests in this module leave waiting (queued, deferred) jobs behind
    # on purpose; the old runner takes the oldest queued job, so this test
    # must own the head of the queue among the fixture agents (300 to 399)
    db._rows("delete from jobs where type='synthesize' and status='queued' and agent_id in "
             "(select id from agents where agent_no between 300 and 399) returning id")
    uid1, aid1 = mk_agent(db)
    late = mk_file(db, uid1, aid1, "cv.txt", b"w" * 12, storage, uploaded=False)
    j1 = mk_request(db, aid1, [late])
    db._rows("update jobs set requested_at = now() - interval '1 minute' where id=%s returning id", (j1,))
    uid2, aid2 = mk_agent(db)
    t = mk_text(db, aid2, "t", "Led brand at three companies. " * 2)
    j2 = mk_request(db, aid2, [t])
    old_db = old.PgDb(DSN)
    rep = old.Runner(old_db, StubStorage(), StubModel([])).run_once()
    assert rep.action == "error" and rep.job_id == j1
    assert job_full(db, j1)["status"] == "queued"               # still oldest next beat
    assert job_row(db, j2)["status"] == "queued"                # never reached: the queue is blocked
    rep = old.Runner(old_db, StubStorage(), StubModel([])).run_once()
    assert rep.action == "error" and rep.job_id == j1           # and again, every beat
    # the block is bounded by the door's own clock, not by the old runner:
    # once the wait is over the door answers in the old vocabulary again
    expire_wait(db, j1)
    rep = old.Runner(old_db, StubStorage(), StubModel([])).run_once()
    assert rep.action == "empty" and rep.job_id == j1
    assert job_full(db, j1)["status"] == "failed" and job_full(db, j1)["error"] == COPY_WAITED
    assert status_of(db, late) == "received"                    # the late row is kept, not failed
    rep = old.Runner(old_db, StubStorage(), StubModel([])).run_once()
    assert rep.job_id == j2                                     # the queue moves again
    assert job_row(db, j2)["status"] != "queued"                # both jobs are terminal; nothing left behind
