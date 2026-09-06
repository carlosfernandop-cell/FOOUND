"""Executable acceptance regressions, NOT production safety certifications.

The authority test uses GuardedDb to exercise the database refusal contract.
These tests do not prove
PostgreSQL concurrency; that requires a disposable two session database test.
"""
import copy
from datetime import date, datetime, timezone

import pytest
import hunt_runner as hr
from test_intelligence_contract import BRIEF, hunt
from test_hunt_runner import MemoryDb


def test_failed_judgment_cannot_be_a_foound_recommendation():
    result, _ = hunt(scores=(None, None))
    assert result["seats"] == []
    assert "FOOUND 2 for you" not in result["html"]
    assert result["outcome"] == "unavailable"
    assert result["counts"]["read"] == 0
    assert result["counts"]["unread"] == 2
    assert "Nothing cleared the bar" not in result["html"]


def test_failed_read_is_unknown_not_refused():
    result, _ = hunt(scores=(78, None))
    assert result["counts"]["model_reads_failed"] == 1
    assert result["payload"]["refused"] == []
    assert result["payload"]["judgment_status"] == "incomplete"
    assert result["counts"]["read"] == 1
    assert result["counts"]["unread"] == 1


def test_unavailable_judgment_does_not_persist_a_successful_empty_edition():
    db, aid, _ = ready_db()
    jid = db.add_job(aid, "first_edition")
    runner = hr.Runner(db, today=date(2026, 9, 6))
    runner._hunt = lambda *args, **kwargs: hunt(scores=(None, None))[0]
    report = runner._run_one(db.jobs[jid])
    assert db.editions == []
    assert report.action == "failed"
    assert report.detail["error"] == "judgment_unavailable"


def ready_db():
    db = MemoryDb()
    aid = "authority-fixture"
    db.agent_numbers[aid] = 2
    db.agent_state[aid] = "at_work"
    bid = db.add_brief(aid, copy.deepcopy(BRIEF["content"]), readiness="ready")
    return db, aid, bid


def test_pause_during_hunt_prevents_persistence_with_database_guard():
    from test_edition_authority import guarded_db
    db, aid, _ = guarded_db()
    jid = db.add_job(aid, "first_edition")
    runner = hr.Runner(db, today=date(2026, 9, 6))

    def pause_while_hunting(*args, **kwargs):
        db.agent_state[aid] = "paused"
        return {"html": "fixture", "payload": {}, "outcome": "empty", "seats": [],
                "counts": {}, "engine": "ai"}

    runner._hunt = pause_while_hunting
    runner._first_edition(db.jobs[jid], hr.RunReport(job_id=jid, job_type="first_edition"))
    assert db.editions == []


def test_resume_with_old_edition_queues_current_brief():
    db, aid, bid = ready_db()
    db.briefs[bid]["version"] = 2
    db.insert_edition({"agent_id": aid, "edition_date": "2026-09-06", "brief_version": 1})
    runner = hr.Runner(db, today=date(2026, 9, 6))
    result = runner.enqueue_daily(datetime(2026, 9, 6, 12, tzinfo=timezone.utc))
    assert result["queued"] == 1


@pytest.mark.parametrize("version, queued", [(1, 0), (None, 1), (0, 1)])
def test_daily_recovery_requires_matching_edition_version(version, queued):
    db, aid, _ = ready_db()
    db.insert_edition({"agent_id": aid, "edition_date": "2026-09-06", "brief_version": version})
    runner = hr.Runner(db, today=date(2026, 9, 6))
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    assert runner.enqueue_daily(now)["queued"] == queued
    assert runner.enqueue_daily(now)["queued"] == 0


def test_daily_recovery_never_queues_a_paused_person():
    db, aid, _ = ready_db()
    db.agent_state[aid] = "paused"
    runner = hr.Runner(db, today=date(2026, 9, 6))
    assert runner.enqueue_daily(datetime(2026, 9, 6, 12, tzinfo=timezone.utc))["queued"] == 0


def test_daily_failure_stands_down_until_basis_or_day_changes():
    db, aid, bid = ready_db()
    runner = hr.Runner(db, today=date(2026, 9, 6))
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    assert runner.enqueue_daily(now)["queued"] == 1
    job = next(iter(db.jobs.values()))
    db.fail(job["id"], "judgment_unavailable")
    for _ in range(10):
        assert runner.enqueue_daily(now)["failed_on_basis"] == 1
    assert len(db.jobs) == 1
    db.briefs[bid]["version"] = 2
    assert runner.enqueue_daily(now)["queued"] == 1
    latest = list(db.jobs.values())[-1]
    db.fail(latest["id"], "judgment_unavailable")
    runner.today = date(2026, 9, 7)
    assert runner.enqueue_daily(datetime(2026, 9, 7, 12, tzinfo=timezone.utc))["queued"] == 1


def test_daily_scheduler_does_not_queue_behind_a_running_hunt():
    db, aid, _ = ready_db()
    jid = db.add_job(aid, "first_edition")
    db.jobs[jid]["status"] = "running"
    result = hr.Runner(db, today=date(2026, 9, 6)).enqueue_daily(
        datetime(2026, 9, 6, 12, tzinfo=timezone.utc))
    assert result["already_queued"] == 1
    assert len(db.jobs) == 1
