"""FOOUND Synthesis Runner v1 — local harness.

Runs the REAL runner against the REAL doors (005–008) in the local Postgres
harness (foound_test), with the model and storage stubbed. Every test records
its expectation in an assert; ON failure pytest names the exact broken claim.

R1  empty queued job -> honest 'empty' path, settle never called
R2  TXT/MD synthesis -> mirror_ready, memory rows, derived source, forced flags
R3  PDF/DOCX parse through real parsers
R4  malformed model output -> one retry, then honest abort (and retry-recovers)
R5  oversized item -> too_large per-item failure, batch continues
R6  batch input budget -> whole-job abort with frozen copy, model never called
R7  per-item parse failures -> exact taxonomy copy, batch continues
R8  withdrawal during processing -> withdrawn recorded, statements discarded
R9  exact duplicate vs existing memory -> reinforce channel, citations merged
R10 cross-batch contradiction -> tension row; existing memory fenced in prompt
R11 inferred direction cannot satisfy readiness
R12 settle door failure -> runner aborts through finalize, job never stuck
R13 janitor recovery -> stale running job finalized, items swept
R14 lost claim race -> clean 'race' exit, nothing touched
R15 all items failed -> job failed with whole-batch copy, per-item reasons kept
R16 at_work non-regression across a full successful run
R17 archived agent -> claim's refusal honored, runner stops
R18 frozen-copy audit: every abort error across the whole suite is None/frozen
R19 log privacy: no evidence/model text in any captured log line
"""

import json
import logging
import uuid

import psycopg2
import pytest

import synthesis_runner as sr
from synthesis_runner import (
    DoorError, FROZEN_CLIENT_COPY, PgDb, Runner, Storage,
)

DSN = "dbname=foound_test user=postgres host=127.0.0.1 password=harness"

POLICY_COPY_SWEEP = "FOOUND could not finish reading this. Remove it and try again."
COPY_DEFAULT = "FOOUND could not finish reading. Try again."
COPY_EMPTY = "There was nothing new to read. Add evidence first."
COPY_BATCH_FAILED = "FOOUND couldn't read what you added. Remove the failed items and try again."
COPY_UNREADABLE = "FOOUND couldn't read this file. Remove it and try again."
COPY_NO_TEXT_PDF = "FOOUND couldn't find readable text in this PDF. Add a text-based PDF, DOCX, TXT or MD instead."
COPY_TOO_LARGE = "This file is too large for FOOUND to read. Add a shorter document instead."
COPY_ARCHIVED = "This FOOUND is archived and cannot read new evidence."

ABORT_ERRORS_SEEN = []  # R18 collects every finalize_failed error argument


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

class SpyDb(PgDb):
    """PgDb + call recording + injectable hooks for race/withdrawal tests."""

    def __init__(self, dsn):
        super().__init__(dsn)
        self.settle_calls = 0
        self.before_settle = None      # callable, runs just before the door
        self.before_evidence_rows = None
        self.claim_hook = None

    def claim(self, job_id):
        if self.claim_hook:
            self.claim_hook(job_id)
        return super().claim(job_id)

    def evidence_rows(self, item_ids):
        if self.before_evidence_rows:
            self.before_evidence_rows()
        return super().evidence_rows(item_ids)

    def settle(self, job_id, results, policy):
        self.settle_calls += 1
        if self.before_settle:
            self.before_settle(job_id, results)
        return super().settle(job_id, results, policy)

    def finalize_failed(self, job_id, error):
        ABORT_ERRORS_SEEN.append(error)
        return super().finalize_failed(job_id, error)


class StubStorage(Storage):
    def __init__(self):
        self.objects = {}

    def fetch(self, path):
        return self.objects[path]


class StubModel(sr.ModelClient):
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.prompts = []
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        self.prompts.append(user)
        if not self.responses:
            raise AssertionError("StubModel exhausted")
        return self.responses.pop(0)


@pytest.fixture(scope="module")
def db():
    d = SpyDb(DSN)
    # preflight: all four doors present in the harness
    names = [r["proname"] for r in d._rows(
        "select proname from pg_proc where proname in "
        "('claim_synthesis_batch','settle_synthesis_results','finalize_synthesis')"
    )]
    assert sorted(names) == [
        "claim_synthesis_batch", "finalize_synthesis", "settle_synthesis_results",
    ], "harness missing doors — apply 005..008 first"
    d._rows("delete from agents where agent_no between 300 and 399 returning id")
    # clear residue from the SQL battery fixtures (agents 86..92) so their
    # queued jobs can never be discovered by runner tests
    d._rows("delete from agents where agent_no between 86 and 92 returning id")
    yield d


@pytest.fixture()
def fresh(db):
    """Reset spy state between tests."""
    db.settle_calls = 0
    db.before_settle = None
    db.before_evidence_rows = None
    db.claim_hook = None
    yield db


_agent_no = iter(range(300, 400))


def mk_agent(db, state="invited"):
    uid, aid = str(uuid.uuid4()), str(uuid.uuid4())
    n = next(_agent_no)
    db._rows(
        "insert into auth.users (id,email) values (%s,%s) returning id",
        (uid, f"runner-{uid[:13]}@example.com"),
    )
    db._rows(
        "insert into agents (id,user_id,agent_no,state) values (%s,%s,%s,%s) returning id",
        (aid, uid, n, state),
    )
    return uid, aid


def mk_text(db, aid, label, body):
    iid = str(uuid.uuid4())
    db._rows(
        "insert into evidence_items (id,agent_id,kind,label,body) "
        "values (%s,%s,'text',%s,%s) returning id",
        (iid, aid, label, body),
    )
    return iid


def mk_file(db, uid, aid, label, mime, data, storage, byte_size=None):
    iid = str(uuid.uuid4())
    path = f"{uid}/{iid}/blob"
    storage.objects[path] = data
    db._rows(
        "insert into evidence_items (id,agent_id,kind,label,storage_path,"
        "mime_type,byte_size) values (%s,%s,'file',%s,%s,%s,%s) returning id",
        (iid, aid, label, path, mime, byte_size if byte_size is not None else len(data)),
    )
    return iid


def mk_job(db, aid):
    jid = str(uuid.uuid4())
    db._rows(
        "insert into jobs (id,agent_id,type) values (%s,%s,'synthesize') returning id",
        (jid, aid),
    )
    return jid


def job_row(db, jid):
    return db._rows("select status, error from jobs where id=%s", (jid,))[0]


def item_row(db, iid):
    return db._rows(
        "select status, failure_reason, read_at from evidence_items where id=%s",
        (iid,),
    )[0]


def memory_rows(db, aid):
    return db._rows(
        "select layer, statement, provenance, evidence, source, status, "
        "can_affect_search, can_appear_publicly, supersedes, expires, "
        "last_reinforced from memory where agent_id=%s order by created_at",
        (aid,),
    )


def agent_state(db, aid):
    return db._rows("select state from agents where id=%s", (aid,))[0]["state"]


def stmt(layer, text, prov, cites, is_dir=False):
    return {
        "layer": layer, "statement": text, "provenance": prov,
        "evidence": cites, "is_direction": is_dir,
    }


def model_json(statements=(), contradictions=(), reinforcements=(), unknowns=()):
    return json.dumps(
        {
            "statements": list(statements),
            "contradictions": list(contradictions),
            "reinforcements": list(reinforcements),
            "unknowns": list(unknowns),
        }
    )


def five_grounded(a, b=None):
    b = b or a
    return [
        stmt("record", "Led brand at three companies.", "stated", [a]),
        stmt("record", "Managed a team of twelve people.", "extracted", [b]),
        stmt("self", "I value autonomy in how work is structured.", "stated", [b]),
        stmt("self", "I want senior brand leadership roles next.", "stated", [a], True),
        stmt("record", "Based in Lisbon and open to hybrid.", "stated", [b]),
    ]


def make_runner(db, storage=None, model=None):
    return Runner(db, storage or StubStorage(), model or StubModel())


def make_pdf(text):
    import io

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.save()
    return buf.getvalue()


def make_docx(text):
    import io

    import docx

    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# R1 — empty queued job: honest zero-item path, settle never called
# ---------------------------------------------------------------------------

def test_r1_empty_job(fresh):
    db = fresh
    _, aid = mk_agent(db)
    jid = mk_job(db, aid)
    report = make_runner(db).run_once()
    assert report.action == "empty" and report.job_id == jid
    j = job_row(db, jid)
    assert j["status"] == "failed" and j["error"] == COPY_EMPTY
    assert db.settle_calls == 0
    assert agent_state(db, aid) == "invited"  # empty path never flips state


# ---------------------------------------------------------------------------
# R2 — TXT/MD synthesis: mirror_ready, derived source, forced authority columns
# ---------------------------------------------------------------------------

def test_r2_text_synthesis(fresh):
    db = fresh
    _, aid = mk_agent(db)
    a = mk_text(db, aid, "Career notes", "I led brand at three companies.")
    b = mk_text(db, aid, "Review 2025", "Team of twelve. Lisbon. Autonomy.")
    jid = mk_job(db, aid)
    model = StubModel([model_json(five_grounded(a, b))])
    report = make_runner(db, model=model).run_once()
    assert report.action == "settled" and report.outcome == "mirror_ready"
    assert job_row(db, jid)["status"] == "done"
    assert agent_state(db, aid) == "mirror_ready"
    assert item_row(db, a)["status"] == "read"
    rows = memory_rows(db, aid)
    assert len(rows) == 5 and all(r["status"] == "active" for r in rows)
    first = next(r for r in rows if r["statement"] == "Led brand at three companies.")
    assert first["source"] == "Career notes"          # derived by the door
    assert first["evidence"] == [{"item": a}]
    assert all(
        not r["can_affect_search"] and not r["can_appear_publicly"]
        and r["supersedes"] is None and r["expires"] is None
        for r in rows
    )


# ---------------------------------------------------------------------------
# R3 — PDF and DOCX parse through the real parsers
# ---------------------------------------------------------------------------

def test_r3_pdf_docx(fresh):
    db = fresh
    storage = StubStorage()
    uid, aid = mk_agent(db)
    p = mk_file(db, uid, aid, "resume.pdf", "application/pdf",
                make_pdf("Ten years in product marketing."), storage)
    d = mk_file(
        db, uid, aid, "notes.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        make_docx("I want a senior marketing role."), storage,
    )
    jid = mk_job(db, aid)
    model = StubModel([model_json(five_grounded(p, d))])
    report = make_runner(db, storage=storage, model=model).run_once()
    assert report.action == "settled"
    assert item_row(db, p)["status"] == "read"
    assert item_row(db, d)["status"] == "read"
    assert "Ten years in product marketing." in model.prompts[0]
    assert "I want a senior marketing role." in model.prompts[0]
    assert job_row(db, jid)["status"] == "done"


# ---------------------------------------------------------------------------
# R4 — malformed model output: one retry then honest abort; retry can recover
# ---------------------------------------------------------------------------

def test_r4_malformed_then_fail(fresh):
    db = fresh
    _, aid = mk_agent(db)
    a = mk_text(db, aid, "Note", "text")
    jid = mk_job(db, aid)
    model = StubModel(["not json at all", '{"also": "wrong"}'])
    report = make_runner(db, model=model).run_once()
    assert report.action == "aborted"
    assert report.detail["reason"] == "model_output_invalid"
    assert model.calls == 2                          # exactly one retry
    j = job_row(db, jid)
    assert j["status"] == "failed" and j["error"] == COPY_DEFAULT
    assert item_row(db, a)["status"] == "failed"     # finalize sweep
    assert item_row(db, a)["failure_reason"] == POLICY_COPY_SWEEP
    assert memory_rows(db, aid) == []


def test_r4b_malformed_then_recover(fresh):
    db = fresh
    _, aid = mk_agent(db)
    a = mk_text(db, aid, "Note", "text")
    jid = mk_job(db, aid)
    model = StubModel(["garbage", model_json(five_grounded(a))])
    report = make_runner(db, model=model).run_once()
    assert report.action == "settled" and model.calls == 2
    assert "previous output was invalid" in model.prompts[1]
    assert job_row(db, jid)["status"] == "done"


# ---------------------------------------------------------------------------
# R5 — oversized item: honest too_large failure, batch continues, no truncation
# ---------------------------------------------------------------------------

def test_r5_oversized_item(fresh):
    db = fresh
    storage = StubStorage()
    uid, aid = mk_agent(db)
    big = ("x" * (sr.PER_ITEM_CHAR_CAP + 1)).encode()
    huge = mk_file(db, uid, aid, "huge.txt", "text/plain", big, storage)
    ok = mk_text(db, aid, "Small note", "fine")
    jid = mk_job(db, aid)
    model = StubModel([model_json([stmt("record", "One fact.", "stated", [ok])])])
    report = make_runner(db, storage=storage, model=model).run_once()
    assert report.action == "settled" and report.outcome == "needs_more_evidence"
    h = item_row(db, huge)
    assert h["status"] == "failed" and h["failure_reason"] == COPY_TOO_LARGE
    assert item_row(db, ok)["status"] == "read"
    assert job_row(db, jid)["status"] == "done"
    # nothing truncated: the oversized item's content never reached the model
    assert "x" * 1000 not in model.prompts[0]


# ---------------------------------------------------------------------------
# R6 — batch input budget: whole-job abort, frozen copy, model never called
# ---------------------------------------------------------------------------

def test_r6_batch_budget(fresh):
    db = fresh
    _, aid = mk_agent(db)
    items = [
        mk_text(db, aid, f"Big {i}", "y" * 95_000) for i in range(5)
    ]  # 475k > 400k budget, each under the per-item cap and the DB body CHECK
    jid = mk_job(db, aid)
    model = StubModel([])
    report = make_runner(db, model=model).run_once()
    assert report.action == "aborted" and report.detail["reason"] == "batch_too_large"
    assert model.calls == 0
    j = job_row(db, jid)
    assert j["status"] == "failed"
    assert j["error"] == FROZEN_CLIENT_COPY["batch_too_large"]
    for iid in items:  # swept by finalize, standing sweep copy
        r = item_row(db, iid)
        assert r["status"] == "failed" and r["failure_reason"] == POLICY_COPY_SWEEP


# ---------------------------------------------------------------------------
# R7 — per-item parse failures: exact taxonomy copy, batch continues
# ---------------------------------------------------------------------------

def test_r7_parse_failures(fresh):
    db = fresh
    storage = StubStorage()
    uid, aid = mk_agent(db)
    corrupt = mk_file(db, uid, aid, "corrupt.pdf", "application/pdf",
                      b"not a pdf at all", storage)
    scanned = mk_file(db, uid, aid, "scan.pdf", "application/pdf",
                      make_pdf(""), storage)  # valid PDF, zero extractable text
    good = mk_text(db, aid, "Good note", "usable")
    jid = mk_job(db, aid)
    model = StubModel([model_json([stmt("record", "A fact.", "stated", [good])])])
    report = make_runner(db, storage=storage, model=model).run_once()
    assert report.action == "settled"
    assert item_row(db, corrupt)["failure_reason"] == COPY_UNREADABLE
    assert item_row(db, scanned)["failure_reason"] == COPY_NO_TEXT_PDF
    assert item_row(db, good)["status"] == "read"
    assert job_row(db, jid)["status"] == "done"


# ---------------------------------------------------------------------------
# R8 — withdrawal during processing: client Remove wins after synthesis
# ---------------------------------------------------------------------------

def test_r8_withdrawal_during_processing(fresh):
    db = fresh
    _, aid = mk_agent(db)
    keep = mk_text(db, aid, "Kept", "stays")
    pulled = mk_text(db, aid, "Pulled", "goes away mid-run")
    jid = mk_job(db, aid)

    def withdraw(_jid, _results):  # between model output and the settle door
        db._rows(
            "update evidence_items set status='deleted' where id=%s returning id",
            (pulled,),
        )

    db.before_settle = withdraw
    model = StubModel([model_json([
        stmt("record", "Grounded in the kept item.", "stated", [keep]),
        stmt("record", "Grounded in the pulled item.", "stated", [pulled]),
    ])])
    report = make_runner(db, model=model).run_once()
    assert report.action == "settled"
    assert report.detail["items_withdrawn"] == 1
    assert report.detail["statements_discarded"] == 1
    assert report.detail["memory_inserted"] == 1
    p = item_row(db, pulled)
    assert p["status"] == "deleted" and p["read_at"] is None  # never became read
    rows = memory_rows(db, aid)
    assert [r["statement"] for r in rows] == ["Grounded in the kept item."]
    assert job_row(db, jid)["status"] == "done"


# ---------------------------------------------------------------------------
# R9 — exact duplicate of existing memory goes through reinforce, not insert
# ---------------------------------------------------------------------------

def test_r9_reinforce_vs_duplicate(fresh):
    db = fresh
    _, aid = mk_agent(db, state="at_work")
    a1 = mk_text(db, aid, "First doc", "text one")
    j1 = mk_job(db, aid)
    make_runner(db, model=StubModel([model_json(five_grounded(a1))])).run_once()
    assert job_row(db, j1)["status"] == "done"
    before = memory_rows(db, aid)
    assert len(before) == 5

    a2 = mk_text(db, aid, "Second doc", "text two")
    j2 = mk_job(db, aid)
    # duplicate (case/whitespace variant) + one fresh statement
    model = StubModel([model_json([
        stmt("record", "  led  brand at THREE companies. ", "stated", [a2]),
        stmt("record", "A genuinely new fact.", "stated", [a2]),
    ])])
    report = make_runner(db, model=model).run_once()
    assert report.action == "settled"
    assert report.detail["reinforced"] == 1
    assert report.detail["memory_inserted"] == 1
    rows = memory_rows(db, aid)
    assert len(rows) == 6  # 5 + 1 new; NO duplicate row
    target = next(r for r in rows if r["statement"] == "Led brand at three companies.")
    assert target["last_reinforced"] is not None
    assert {"item": a2} in target["evidence"]         # citations merged
    assert job_row(db, j2)["status"] == "done"


# ---------------------------------------------------------------------------
# R10 — cross-batch contradiction becomes a tension row; memory fenced
# ---------------------------------------------------------------------------

def test_r10_cross_batch_contradiction(fresh):
    db = fresh
    _, aid = mk_agent(db, state="at_work")
    a1 = mk_text(db, aid, "Batch one", "I want to remain in Austin.")
    mk_job(db, aid)
    make_runner(db, model=StubModel([model_json([
        stmt("self", "Wants to remain in Austin.", "stated", [a1], True),
    ])])).run_once()
    austin_id = memory_rows(db, aid)[0]
    a2 = mk_text(db, aid, "Batch two", "I am only considering roles in New York.")
    j2 = mk_job(db, aid)

    mem_id = db._rows(
        "select id::text from memory where agent_id=%s limit 1", (aid,)
    )[0]["id"]
    model = StubModel([model_json(
        statements=[stmt("self", "Only considering roles in New York.", "stated", [a2], True)],
        contradictions=[{
            "kind": "existing",
            "a": "wants to remain in Austin",
            "b": "only considering roles in New York",
            "evidence": [a2],
            "existing_memory_id": mem_id,
        }],
    )])
    report = make_runner(db, model=model).run_once()
    assert report.action == "settled"
    # existing memory was in the prompt, clearly fenced and never citable
    assert "Wants to remain in Austin." in model.prompts[0]
    assert "NEVER CITABLE" in model.prompts[0]
    rows = memory_rows(db, aid)
    tensions = [r for r in rows if r["status"] == "tension"]
    assert len(tensions) == 1
    assert "Austin" in tensions[0]["statement"] and "New York" in tensions[0]["statement"]
    assert tensions[0]["evidence"] == [{"item": a2}]  # cites only current batch
    actives = [r for r in rows if r["status"] == "active"]
    assert {r["statement"] for r in actives} == {
        "Wants to remain in Austin.", "Only considering roles in New York.",
    }  # both sides preserved, no silent winner
    assert job_row(db, j2)["status"] == "done"


# ---------------------------------------------------------------------------
# R11 — inferred direction can never satisfy readiness
# ---------------------------------------------------------------------------

def test_r11_inferred_direction(fresh):
    db = fresh
    _, aid = mk_agent(db)
    a = mk_text(db, aid, "Doc", "text")
    jid = mk_job(db, aid)
    statements = [
        stmt("record", "R11 record one.", "stated", [a]),
        stmt("record", "R11 record two.", "stated", [a]),
        stmt("self", "R11 self one.", "stated", [a]),
        stmt("self", "R11 self two.", "stated", [a]),
        stmt("model", "R11 guessed direction.", "inferred", [a], True),
    ]
    report = make_runner(db, model=StubModel([model_json(statements)])).run_once()
    assert report.action == "settled"
    assert report.outcome == "needs_more_evidence"    # 5 grounded, but no
    assert agent_state(db, aid) == "commissioning"    # grounded direction
    assert job_row(db, jid)["status"] == "done"


# ---------------------------------------------------------------------------
# R12 — settle door failure: runner aborts through finalize, never stuck
# ---------------------------------------------------------------------------

def test_r12_settle_failure_aborts(fresh):
    db = fresh
    _, aid = mk_agent(db)
    a = mk_text(db, aid, "Doc", "text")
    jid = mk_job(db, aid)

    def explode(_jid, _results):
        raise DoorError("evidence_not_committed")

    db.before_settle = explode
    report = make_runner(
        db, model=StubModel([model_json([stmt("record", "F.", "stated", [a])])])
    ).run_once()
    assert report.action == "aborted"
    j = job_row(db, jid)
    assert j["status"] == "failed" and j["error"] == COPY_DEFAULT
    assert memory_rows(db, aid) == []
    assert item_row(db, a)["failure_reason"] == POLICY_COPY_SWEEP


# ---------------------------------------------------------------------------
# R13 — janitor: stale running job finalized honestly, races skipped
# ---------------------------------------------------------------------------

def test_r13_janitor(fresh):
    db = fresh
    _, aid = mk_agent(db)
    a = mk_text(db, aid, "Stale doc", "text")
    jid = mk_job(db, aid)
    claim = db.claim(jid)
    assert claim["status"] == "claimed"
    db._rows(
        "update jobs set started_at = now() - interval '40 minutes' "
        "where id=%s returning id", (jid,),
    )
    n = make_runner(db).janitor()
    assert n == 1
    j = job_row(db, jid)
    assert j["status"] == "failed" and j["error"] == COPY_DEFAULT
    r = item_row(db, a)
    assert r["status"] == "failed" and r["failure_reason"] == POLICY_COPY_SWEEP
    assert make_runner(db).janitor() == 0            # idempotent: nothing stale


# ---------------------------------------------------------------------------
# R14 — lost claim race: clean exit, nothing else touched
# ---------------------------------------------------------------------------

def test_r14_lost_claim_race(fresh):
    db = fresh
    _, aid = mk_agent(db)
    mk_text(db, aid, "Doc", "text")
    jid = mk_job(db, aid)

    def rival_claims(job_id):  # a concurrent invocation wins the race
        db.claim_hook = None
        PgDb(DSN).claim(job_id)

    db.claim_hook = rival_claims
    report = make_runner(db).run_once()
    assert report.action == "race" and report.job_id == jid
    assert job_row(db, jid)["status"] == "running"   # rival's claim stands
    db.finalize_failed(jid, None)                    # tidy for later tests


# ---------------------------------------------------------------------------
# R15 — all items failed: whole-batch copy, per-item reasons preserved
# ---------------------------------------------------------------------------

def test_r15_all_items_failed(fresh):
    db = fresh
    storage = StubStorage()
    uid, aid = mk_agent(db)
    c1 = mk_file(db, uid, aid, "bad1.pdf", "application/pdf", b"junk1", storage)
    c2 = mk_file(db, uid, aid, "bad2.pdf", "application/pdf", make_pdf(""), storage)
    jid = mk_job(db, aid)
    model = StubModel([])
    report = make_runner(db, storage=storage, model=model).run_once()
    assert report.action == "settled" and report.outcome == "failed"
    assert model.calls == 0                          # nothing readable
    j = job_row(db, jid)
    assert j["status"] == "failed" and j["error"] == COPY_BATCH_FAILED
    assert item_row(db, c1)["failure_reason"] == COPY_UNREADABLE
    assert item_row(db, c2)["failure_reason"] == COPY_NO_TEXT_PDF
    assert agent_state(db, aid) == "commissioning"


# ---------------------------------------------------------------------------
# R16 — at_work non-regression across a full successful run
# ---------------------------------------------------------------------------

def test_r16_at_work_non_regression(fresh):
    db = fresh
    _, aid = mk_agent(db, state="at_work")
    a = mk_text(db, aid, "Doc", "text")
    jid = mk_job(db, aid)
    report = make_runner(
        db, model=StubModel([model_json(five_grounded(a))])
    ).run_once()
    assert report.action == "settled" and report.outcome == "mirror_ready"
    assert agent_state(db, aid) == "at_work"          # never moves
    assert job_row(db, jid)["status"] == "done"


# ---------------------------------------------------------------------------
# R17 — archived agent: claim's persisted refusal honored, runner stops
# ---------------------------------------------------------------------------

def test_r17_archived_agent(fresh):
    db = fresh
    _, aid = mk_agent(db, state="archived")
    mk_text(db, aid, "Doc", "text")
    jid = mk_job(db, aid)
    report = make_runner(db).run_once()
    assert report.action == "refused"
    j = job_row(db, jid)
    assert j["status"] == "failed" and j["error"] == COPY_ARCHIVED
    assert db.settle_calls == 0


# ---------------------------------------------------------------------------
# R18 — frozen-copy audit: every abort error the suite produced is None/frozen
# ---------------------------------------------------------------------------

def test_r18_frozen_copy_audit():
    allowed = set(FROZEN_CLIENT_COPY.values()) | {None}
    assert ABORT_ERRORS_SEEN, "suite produced no abort calls to audit"
    assert set(ABORT_ERRORS_SEEN) <= allowed, (
        "an abort path passed a client-visible string outside the frozen set"
    )


# ---------------------------------------------------------------------------
# R20 — file-integrity contract: stored object must match the evidence row's
# claims BEFORE any content reaches the model. Mismatch = item failure.
# ---------------------------------------------------------------------------

def test_r20_file_contract_mismatch(fresh):
    db = fresh
    storage = StubStorage()
    uid, aid = mk_agent(db)
    # byte-size mismatch: row claims 10 bytes, object holds different content
    lying = mk_file(db, uid, aid, "lying.txt", "text/plain",
                    b"MISMATCHSECRET actual content", storage, byte_size=10)
    # MIME outside the four-type contract: parser must never run on it
    weird = mk_file(db, uid, aid, "weird.bin", "application/octet-stream",
                    b"BINSECRET bytes", storage)
    good = mk_text(db, aid, "Good", "usable text")
    jid = mk_job(db, aid)
    model = StubModel([model_json([stmt("record", "A fact.", "stated", [good])])])
    report = make_runner(db, storage=storage, model=model).run_once()
    assert report.action == "settled"
    l = item_row(db, lying)
    assert l["status"] == "failed" and l["failure_reason"] == COPY_UNREADABLE
    w = item_row(db, weird)
    assert w["status"] == "failed" and w["failure_reason"] == COPY_UNREADABLE
    assert item_row(db, good)["status"] == "read"
    # the mismatched objects' content never reached the model
    assert model.calls == 1
    assert "MISMATCHSECRET" not in model.prompts[0]
    assert "BINSECRET" not in model.prompts[0]
    assert job_row(db, jid)["status"] == "done"


# ---------------------------------------------------------------------------
# R21 — accepted conservative rule: a merged duplicate statement is discarded
# if ANY citation in its merged provenance is withdrawn. Intentional for v1
# (withdrawal wins completely); provenance pruning is a later refinement.
# ---------------------------------------------------------------------------

def test_r21_merged_duplicate_withdrawal_discards_whole_statement(fresh):
    db = fresh
    _, aid = mk_agent(db)
    a = mk_text(db, aid, "Item A", "supports X")
    b = mk_text(db, aid, "Item B", "also supports X")
    jid = mk_job(db, aid)

    def withdraw_a(_jid, _results):   # A withdrawn after merge, before settle
        db._rows(
            "update evidence_items set status='deleted' where id=%s returning id",
            (a,),
        )

    db.before_settle = withdraw_a
    # the model states X twice — once per item; the runner merges to X -> [A, B]
    model = StubModel([model_json([
        stmt("record", "Statement X, twice stated.", "stated", [a]),
        stmt("record", "statement x,  twice stated.", "stated", [b]),
        stmt("record", "Independent other fact.", "stated", [b]),
    ])])
    report = make_runner(db, model=model).run_once()
    assert report.action == "settled"
    assert report.detail["items_withdrawn"] == 1
    assert report.detail["statements_discarded"] == 1   # merged X died whole
    assert report.detail["memory_inserted"] == 1        # only the B-only fact
    rows = memory_rows(db, aid)
    assert [r["statement"] for r in rows] == ["Independent other fact."]
    assert job_row(db, jid)["status"] == "done"


# ---------------------------------------------------------------------------
# R22 — fenced model output: a single markdown fence is transport, not error
# (observed live in Fire #2 run 3: model fenced its JSON despite instructions)
# ---------------------------------------------------------------------------

def test_r22_fenced_model_output(fresh):
    db = fresh
    _, aid = mk_agent(db)
    a = mk_text(db, aid, "Doc", "text")
    jid = mk_job(db, aid)
    fenced = "```json\n" + model_json(five_grounded(a)) + "\n```"
    report = make_runner(db, model=StubModel([fenced])).run_once()
    assert report.action == "settled" and report.outcome == "mirror_ready"
    assert job_row(db, jid)["status"] == "done"


def test_r22b_fence_stripping_stays_strict():
    from synthesis_runner import _strip_fences, validate_and_map, ValidationError
    # plain fence unwraps
    assert _strip_fences("```json\n{\"a\":1}\n```") == '{"a":1}'
    assert _strip_fences("```\n{\"a\":1}\n```") == '{"a":1}'
    # prose-wrapped JSON is still refused — no lenient parsing
    with pytest.raises(ValidationError):
        validate_and_map('Here is the JSON: {"statements":[]}', set(), [])
    # fenced prose is still refused
    with pytest.raises(ValidationError):
        validate_and_map("```\nnot json\n```", set(), [])


# ---------------------------------------------------------------------------
# R19 — log privacy: no evidence/model/label text in any log line
# ---------------------------------------------------------------------------

def test_r19_log_privacy(fresh, caplog):
    db = fresh
    _, aid = mk_agent(db)
    a = mk_text(db, aid, "LABELSENTINEL", "BODYSENTINEL private career text")
    mk_job(db, aid)
    statements = five_grounded(a)
    statements[0]["statement"] = "STATEMENTSENTINEL grounded fact."
    with caplog.at_level(logging.DEBUG, logger="synthesis_runner"):
        report = make_runner(db, model=StubModel([model_json(statements)])).run_once()
    assert report.action == "settled"
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert logged                                     # something was logged
    for sentinel in ("BODYSENTINEL", "STATEMENTSENTINEL", "LABELSENTINEL"):
        assert sentinel not in logged
