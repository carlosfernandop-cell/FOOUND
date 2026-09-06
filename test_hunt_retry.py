"""Offline recovery policy and real RestDb wire checks. No live service."""
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import hunt_runner as hr
from test_open_intelligence_gates import ready_db

NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)


def attempt(db, aid, *, n=1, error="judgment_unavailable", elapsed=3600,
            version=1, engine=None):
    jid = db.add_job(aid, "first_edition", status="failed", payload={
        "edition_date": "2026-09-06", "brief_version": version,
        "engine": engine or hr.current_engine_sha(), "attempt": n})
    db.jobs[jid].update(error=error, completed_at=(NOW - timedelta(seconds=elapsed)).isoformat())
    return db.jobs[jid]


@pytest.mark.parametrize("elapsed, queued", [(3599, 0), (3600, 1), (3601, 1)])
def test_transient_retry_waits_a_full_hour(elapsed, queued):
    db, aid, _ = ready_db()
    attempt(db, aid, elapsed=elapsed)
    runner = hr.Runner(db, today=NOW.date())
    assert runner.enqueue_daily(NOW)["queued"] == queued
    assert runner.enqueue_daily(NOW)["queued"] == 0


@pytest.mark.parametrize("error", ["compile_failed", "no_candidate_context", "no_role_families",
                                    "edition_persist_failed", "unexpected"])
def test_nontransient_and_ambiguous_persistence_failures_stay_held(error):
    db, aid, _ = ready_db()
    attempt(db, aid, error=error, elapsed=7200)
    assert hr.Runner(db, today=NOW.date()).enqueue_daily(NOW)["failed_on_basis"] == 1


def test_three_attempts_cap_does_not_reset_when_another_job_completes():
    db, aid, _ = ready_db()
    for n in range(1, 4):
        attempt(db, aid, n=n)
    db.add_job(aid, "first_edition", status="done")
    runner = hr.Runner(db, today=NOW.date())
    assert runner.enqueue_daily(NOW)["failed_on_basis"] == 1
    assert len(db.jobs) == 4


def test_cap_is_also_enforced_when_execution_came_from_another_door():
    db, aid, _ = ready_db()
    for n in range(1, 4):
        attempt(db, aid, n=n)
    jid = db.add_job(aid, "first_edition", payload={"brief_version": 1})
    runner = hr.Runner(db, today=NOW.date())
    runner._hunt = lambda *args, **kwargs: pytest.fail("No model work after cap")
    report = runner._run_one(db.jobs[jid])
    assert report.detail["reason"] == "daily_limit"
    assert db.jobs[jid]["status"] == "done"
    assert db.editions == []


def test_new_brief_engine_and_day_reopen_recovery():
    db, aid, bid = ready_db()
    for n in range(1, 4):
        attempt(db, aid, n=n)
    runner = hr.Runner(db, today=NOW.date())
    db.briefs[bid]["version"] = 2
    assert runner.enqueue_daily(NOW)["queued"] == 1
    db.jobs[list(db.jobs)[-1]]["status"] = "done"
    db.briefs[bid]["version"] = 1
    for j in db.jobs.values():
        if j["status"] == "failed":
            j["payload"]["engine"] = "older-engine"
    assert runner.enqueue_daily(NOW)["queued"] == 1
    db.jobs[list(db.jobs)[-1]]["status"] = "done"
    runner.today = date(2026, 9, 7)
    assert runner.enqueue_daily(NOW + timedelta(days=1))["queued"] == 1


@pytest.mark.parametrize("stamp", [None, "invalid", "2026-09-06T10:00:00", "2026-09-07T12:00:00Z"])
def test_unproven_or_future_failure_time_does_not_trigger_retry(stamp):
    db, aid, _ = ready_db()
    j = attempt(db, aid)
    j["completed_at"] = stamp
    assert hr.Runner(db, today=NOW.date()).enqueue_daily(NOW)["queued"] == 0


def test_unavailable_execution_records_basis_before_work():
    db, aid, _ = ready_db()
    jid = db.add_job(aid, "first_edition", payload={"brief_version": 1})
    runner = hr.Runner(db, today=NOW.date())
    def unavailable(*args, **kwargs):
        assert db.jobs[jid]["payload"]["attempt"] == 1
        assert db.jobs[jid]["payload"]["retry_limit"] == 3
        return {"outcome": "unavailable", "counts": {}, "engine": "heuristic"}
    runner._hunt = unavailable
    assert runner._run_one(db.jobs[jid]).detail["error"] == "judgment_unavailable"
    assert db.editions == []
    receipt = db.jobs[jid]["payload"]
    assert receipt["retry_policy"] == "bounded_hourly_v1"
    assert receipt["retry_after"] is not None
    assert receipt["attempts_today"] == 1


def test_daily_cost_ceiling_survives_brief_and_engine_changes():
    db, aid, bid = ready_db()
    for n in range(6):
        attempt(db, aid, n=n + 1, version=n + 10, engine="old-" + str(n))
    db.briefs[bid]["version"] = 99
    runner = hr.Runner(db, today=NOW.date())
    assert runner.enqueue_daily(NOW)["failed_on_basis"] == 1
    jid = db.add_job(aid, "first_edition", payload={"brief_version": 99})
    runner._hunt = lambda *args, **kwargs: pytest.fail("No work beyond daily ceiling")
    assert runner._run_one(db.jobs[jid]).detail["reason"] == "daily_limit"


def test_receipt_failure_does_not_leave_the_job_running():
    db, aid, _ = ready_db()
    jid = db.add_job(aid, "first_edition")
    runner = hr.Runner(db, today=NOW.date())
    def refuse_receipt(*args):
        raise hr.HuntError("edition_persist_failed")
    db.record_hunt_basis = refuse_receipt
    report = runner._run_one(db.jobs[jid])
    assert report.action == "failed"
    assert db.jobs[jid]["status"] == "failed"


def test_supported_live_entrypoints_share_the_non_cancelling_mutex():
    from pathlib import Path
    for name in ("heartbeat.yml", "hunt.yml", "dry-run.yml"):
        source = (Path(__file__).parent / ".github" / "workflows" / name).read_text()
        assert "group: hunt-runner" in source
        assert "cancel-in-progress: false" in source


def test_history_and_receipt_transport_are_agent_scoped_and_running_only():
    db = hr.RestDb("https://fixture.invalid", "fixture-key")
    calls = []
    db._get = lambda path: calls.append(path) or []
    db.edition_attempts("agent-fixture", NOW.date())
    assert "agent_id=eq.agent-fixture&type=eq.first_edition" in calls[0]
    assert "payload->>edition_date=eq.2026-09-06" in calls[0]
    assert "limit=100" in calls[0]
    db._patch = lambda path, body: calls.append((path, body)) or [{"id": "job-fixture"}]
    db.record_hunt_basis("job-fixture", {"attempt": 1})
    assert calls[-1] == ("jobs?id=eq.job-fixture&status=eq.running", {"payload": {"attempt": 1}})
    db._patch = lambda *args: []
    with pytest.raises(hr.HuntError):
        db.record_hunt_basis("job-fixture", {"attempt": 1})
