"""receipt_unavailable: the attempt receipt (basis saved before any paid
work) failing to persist. Transient with hourly recovery; no work without a
saved receipt; bounded by the clock and the day; authority refusals and the
ambiguous edition write stay as they were (held)."""
from datetime import timedelta

import pytest
import hunt_runner as hr
from test_hunt_runner import MemoryDb
from test_open_intelligence_gates import ready_db
from test_hunt_retry import NOW

TODAY = NOW.date()


class ClockDb(MemoryDb):
    """MemoryDb whose fail() stamps completed_at like RestDb.fail does, and
    whose receipt write can be switched off and on."""

    def __init__(self):
        super().__init__()
        self.receipts_ok = True
        self.receipt_calls = 0
        self.clock = NOW

    def fail(self, job_id, error):
        super().fail(job_id, error)
        self.jobs[job_id]["completed_at"] = self.clock.isoformat()

    def record_hunt_basis(self, job_id, payload):
        self.receipt_calls += 1
        if not self.receipts_ok:
            raise hr.HuntError("receipt_unavailable")
        super().record_hunt_basis(job_id, payload)


def fixture():
    base, aid, bid = ready_db()
    db = ClockDb()
    db.__dict__.update({k: v for k, v in base.__dict__.items() if k not in ("receipts_ok", "receipt_calls", "clock")})
    return db, aid, bid


def queued_job(db, aid):
    return [k for k, j in db.jobs.items() if j["agent_id"] == aid and j["type"] == "first_edition" and j["status"] == "queued"]


def no_work(*a, **k):
    pytest.fail("paid work started without a saved receipt")


def test_rest_receipt_write_names_its_own_failure():
    class Fake(hr.RestDb):
        def __init__(self):
            pass

        def _patch(self, path, body):
            return []  # the running row was not there to patch

    with pytest.raises(hr.HuntError) as e:
        Fake().record_hunt_basis("j", {})
    assert e.value.name == "receipt_unavailable"


def test_transport_error_on_the_receipt_is_receipt_unavailable_not_a_deterministic_class():
    db, aid, _ = fixture()

    def boom(job_id, payload):
        raise ConnectionError("reset")

    db.record_hunt_basis = boom
    jid = db.add_job(aid, "first_edition", requested_at=NOW.isoformat())
    runner = hr.Runner(db, today=TODAY)
    runner._hunt = no_work
    report = runner._run_one(db.jobs[jid])
    assert report.action == "failed"
    assert report.detail["error"] == "receipt_unavailable"
    assert db.jobs[jid]["status"] == "failed"


def test_sweep_door_hourly_recovery_and_work_only_after_the_receipt_persists():
    db, aid, _ = fixture()
    db.receipts_ok = False
    runner = hr.Runner(db, today=TODAY)
    runner._hunt = no_work
    worked = {"n": 0}

    assert runner.enqueue_daily(NOW)["queued"] == 1
    (jid,) = queued_job(db, aid)
    assert runner._run_one(db.jobs[jid]).detail["error"] == "receipt_unavailable"
    assert "attempt" not in hr.decoded_job_payload(db.jobs[jid])  # no receipt: no attempt counted

    # within the hour: held, not re-queued
    for minutes in (15, 30, 45, 59):
        out = runner.enqueue_daily(NOW + timedelta(minutes=minutes))
        assert out["queued"] == 0 and out["failed_on_basis"] == 1, minutes
    # after the hour: one more try, which fails at the receipt again
    db.clock = NOW + timedelta(hours=1)
    assert runner.enqueue_daily(db.clock)["queued"] == 1
    (jid2,) = queued_job(db, aid)
    assert runner._run_one(db.jobs[jid2]).detail["error"] == "receipt_unavailable"
    assert runner.enqueue_daily(db.clock + timedelta(minutes=30))["queued"] == 0

    # the receipt persists again: the next hourly try does real work
    db.receipts_ok = True
    db.clock = NOW + timedelta(hours=2)
    assert runner.enqueue_daily(db.clock)["queued"] == 1
    (jid3,) = queued_job(db, aid)

    def work(*a, **k):
        worked["n"] += 1
        return {"html": "fixture", "payload": {}, "outcome": "empty", "seats": [], "counts": {}, "engine": "ai"}

    runner._hunt = work
    report = runner._run_one(db.jobs[jid3])
    assert report.action == "edition" and worked["n"] == 1
    receipt = hr.decoded_job_payload(db.jobs[jid3])
    assert receipt["attempt"] == 1 and receipt["attempts_today"] == 1  # receipt failures were not attempts
    assert db.editions and db.editions[0]["brief_version"] == receipt["brief_version"]


def test_compile_door_job_is_held_hourly_after_a_receipt_failure():
    db, aid, bid = fixture()
    db.receipts_ok = False
    runner = hr.Runner(db, today=TODAY)
    runner._hunt = no_work
    # as _compile enqueues it when a Brief comes into force at work
    jid = db.add_job(aid, "first_edition", requested_at=NOW.isoformat(),
                     payload={"brief_version": db.briefs[bid]["version"], "reason": "brief_in_force"})
    assert runner._run_one(db.jobs[jid]).detail["error"] == "receipt_unavailable"
    for minutes in (0, 20, 59):
        out = runner.enqueue_daily(NOW + timedelta(minutes=minutes))
        assert out["queued"] == 0 and out["failed_on_basis"] == 1, minutes
    assert runner.enqueue_daily(NOW + timedelta(hours=1))["queued"] == 1


def test_repeated_receipt_failures_are_bounded_by_the_clock():
    db, aid, _ = fixture()
    db.receipts_ok = False
    runner = hr.Runner(db, today=TODAY)
    runner._hunt = no_work
    runs = 0
    for quarter in range(0, 4 * 6):  # six hours of quarter hour beats
        db.clock = NOW + timedelta(minutes=15 * quarter)
        if runner.enqueue_daily(db.clock)["queued"]:
            (jid,) = queued_job(db, aid)
            assert runner._run_one(db.jobs[jid]).detail["error"] == "receipt_unavailable"
            runs += 1
    assert runs == 6  # one per hour, never one per beat
    assert db.receipt_calls >= 6 and not db.editions


def test_receipt_failure_records_eligibility_when_the_second_write_lands():
    """The failure path writes retry_policy and retry_after when it can; for a
    receipt failure no attempt is counted, so eligibility is by the clock."""
    db, aid, _ = fixture()
    calls = {"n": 0}
    real = MemoryDb.record_hunt_basis

    def first_fails(job_id, payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise hr.HuntError("receipt_unavailable")
        real(db, job_id, payload)

    db.record_hunt_basis = first_fails
    runner = hr.Runner(db, today=TODAY)
    runner._hunt = no_work
    assert runner.enqueue_daily(NOW)["queued"] == 1
    (jid,) = queued_job(db, aid)
    runner._run_one(db.jobs[jid])
    payload = hr.decoded_job_payload(db.jobs[jid])
    assert payload["retry_policy"] == "bounded_hourly_v1"
    assert payload["retry_after"] is not None
    assert "attempt" not in payload


def test_authority_refusal_and_ambiguous_edition_write_are_unchanged():
    db, aid, _ = fixture()
    runner = hr.Runner(db, today=TODAY)
    assert runner.enqueue_daily(NOW)["queued"] == 1
    (jid,) = queued_job(db, aid)

    class Resp:
        status_code = 400
        text = '{"code":"P0001","message":"authority_changed:paused"}'

    class RestError(Exception):
        response = Resp()

    def refuse(row):
        raise RestError()

    db.insert_edition = refuse
    runner._hunt = lambda *a, **k: {"html": "f", "payload": {}, "outcome": "empty", "seats": [], "counts": {}, "engine": "ai"}
    report = runner._run_one(db.jobs[jid])
    assert report.action == "noop" and report.detail["reason"] == "authority_changed"

    # the ambiguous edition write stays held for the day
    db2, aid2, _ = fixture()
    r2 = hr.Runner(db2, today=TODAY)
    assert r2.enqueue_daily(NOW)["queued"] == 1
    (j2,) = queued_job(db2, aid2)

    def ambiguous(row):
        raise ConnectionError("reset after send")

    db2.insert_edition = ambiguous
    r2._hunt = runner._hunt
    assert r2._run_one(db2.jobs[j2]).detail["error"] == "edition_persist_failed"
    out = r2.enqueue_daily(NOW + timedelta(hours=3))
    assert out["queued"] == 0 and out["failed_on_basis"] == 1


def test_receipt_cooldown_survives_another_door_without_latest_job_fallback():
    db, aid, bid = fixture()
    runner = hr.Runner(db, today=TODAY)
    jid = db.add_job(aid, "first_edition", requested_at=NOW.isoformat(),
                     payload={"brief_version": db.briefs[bid]["version"],
                              "edition_date": TODAY.isoformat(), "engine": hr.current_engine_sha()})
    db.fail(jid, "receipt_unavailable")
    history = db.edition_attempts(aid, TODAY)
    # Execution checks history without the scheduler's latest job argument.
    assert runner._retry_hold(history, None, db.briefs[bid], NOW + timedelta(minutes=1)) == "retry_cooldown"
    assert runner._retry_hold(history, None, db.briefs[bid], NOW + timedelta(hours=1)) == ""


def test_receipt_failure_recovery_keeps_authority_without_counting_work():
    db, aid, bid = fixture()
    calls = 0
    original = db.record_hunt_basis
    def first_fails(job_id, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise hr.HuntError("receipt_unavailable")
        original(job_id, payload)
    db.record_hunt_basis = first_fails
    runner = hr.Runner(db, today=TODAY)
    runner._hunt = no_work
    jid = db.add_job(aid, "first_edition", requested_at=NOW.isoformat())
    assert runner._run_one(db.jobs[jid]).detail["error"] == "receipt_unavailable"
    payload = hr.decoded_job_payload(db.jobs[jid])
    assert payload.get("brief_version") == db.briefs[bid]["version"]
    assert payload.get("engine") == hr.current_engine_sha()
    assert payload.get("edition_date") == TODAY.isoformat()
    assert "attempt" not in payload
