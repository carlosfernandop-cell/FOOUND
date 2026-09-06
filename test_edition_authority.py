"""Engine side of migration 017 (edition write authority).

The database refuses an edition write with authority_changed:<reason> when
the person paused or a new Brief came into force during the hunt. These
tests emulate that refusal in MemoryDb to prove the runner's handling: the
job completes with the reason (never fails, never retries), nothing is
written, and the daily sweep re-queues the day once authority is restored.
They do not prove PostgreSQL locking; sql/dev/run_017_concurrency.sh does.
"""
from datetime import date, datetime, timezone

import hunt_runner as hr
from test_hunt_runner import MemoryDb
from test_open_intelligence_gates import ready_db


class _Resp:
    def __init__(self, text):
        self.status_code = 400
        self.text = text


class _RestError(Exception):
    def __init__(self, text):
        super().__init__("400 Bad Request")
        self.response = _Resp(text)


class GuardedDb(MemoryDb):
    """MemoryDb with guard_edition_authority's rule at the write."""

    def _guard(self, agent_id, version):
        if self.agent_state.get(agent_id) == "paused":
            raise _RestError('{"code":"P0001","message":"authority_changed:paused"}')
        active = [b for b in self.briefs.values()
                  if b["agent_id"] == agent_id and b.get("state") == "active"]
        if not active:
            raise _RestError('{"code":"P0001","message":"authority_changed:no_active_brief"}')
        if not active[0].get("confirmed_at"):
            raise _RestError('{"code":"P0001","message":"authority_changed:brief_unconfirmed"}')
        if version is None:
            raise _RestError('{"code":"P0001","message":"authority_changed:no_version"}')
        if version != active[0].get("version"):
            raise _RestError('{"code":"P0001","message":"authority_changed:brief_superseded"}')

    def insert_edition(self, row):
        self._guard(row["agent_id"], row.get("brief_version"))
        super().insert_edition(row)

    def replace_edition(self, edition_id, row):
        self._guard(row["agent_id"], row.get("brief_version"))
        super().replace_edition(edition_id, row)


def _fixture_hunt(*args, **kwargs):
    return {"html": "fixture", "payload": {}, "outcome": "empty", "seats": [],
            "counts": {}, "engine": "ai"}


def guarded_db():
    db, aid, bid = ready_db()
    g = GuardedDb()
    g.__dict__.update(db.__dict__)
    g.briefs[bid]["confirmed_at"] = "2026-09-06T08:00:00Z"
    return g, aid, bid


def test_authority_refusal_reads_the_reason_off_the_error_body():
    assert hr.authority_refusal(_RestError('{"code":"P0001","message":"authority_changed:paused"}')) == "paused"
    assert hr.authority_refusal(_RestError('{"code":"P0001","message":"authority_changed:brief_superseded"}')) == "brief_superseded"
    assert hr.authority_refusal(_RestError('{"code":"P0001","message":"authority_changed:state_archived"}')) == "state_archived"
    assert hr.authority_refusal(Exception("authority_changed:no_version")) == ""
    assert hr.authority_refusal(_RestError('{"message":"authority_changed:paused"}')) == ""
    assert hr.authority_refusal(_RestError('{"code":"23505","message":"authority_changed:paused"}')) == ""
    assert hr.authority_refusal(_RestError('{"code":"P0001","message":"authority_changed:unknown"}')) == ""
    assert hr.authority_refusal(_RestError("proxy error authority_changed:paused")) == ""
    assert hr.authority_refusal(_RestError('{"code":"23505","message":"duplicate key"}')) == ""
    assert hr.authority_refusal(ConnectionError("reset")) == ""


def test_pause_during_hunt_withholds_the_edition_and_completes_the_job():
    db, aid, _ = guarded_db()
    jid = db.add_job(aid, "first_edition")
    runner = hr.Runner(db, today=date(2026, 9, 6))

    def pause_while_hunting(*args, **kwargs):
        db.agent_state[aid] = "paused"
        return _fixture_hunt()

    runner._hunt = pause_while_hunting
    report = runner._run_one(db.jobs[jid])
    assert db.editions == []
    assert report.action == "noop"
    assert report.detail["reason"] == "authority_changed"
    assert report.detail["authority_reason"] == "paused"
    assert db.jobs[jid]["status"] == "done"


def test_new_brief_during_hunt_withholds_the_stale_edition():
    db, aid, bid = guarded_db()
    jid = db.add_job(aid, "first_edition")
    runner = hr.Runner(db, today=date(2026, 9, 6))

    def activate_while_hunting(*args, **kwargs):
        db.briefs[bid] = dict(db.briefs[bid], version=2)  # a new row, as activate_brief writes one
        return _fixture_hunt()

    runner._hunt = activate_while_hunting
    report = runner._run_one(db.jobs[jid])
    assert db.editions == []
    assert report.detail["authority_reason"] == "brief_superseded"
    assert db.jobs[jid]["status"] == "done"


def test_daily_sweep_requeues_after_resume_because_the_job_completed():
    db, aid, _ = guarded_db()
    jid = db.add_job(aid, "first_edition")
    runner = hr.Runner(db, today=date(2026, 9, 6))

    def pause_while_hunting(*args, **kwargs):
        db.agent_state[aid] = "paused"
        return _fixture_hunt()

    runner._hunt = pause_while_hunting
    runner._run_one(db.jobs[jid])
    noon = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    assert runner.enqueue_daily(noon)["queued"] == 0          # paused: never queued
    db.agent_state[aid] = "at_work"
    out = runner.enqueue_daily(noon)
    assert out["queued"] == 1 and out["failed_on_basis"] == 0  # completed, not failed: no stand down


def test_other_persist_errors_still_fail_the_job():
    db, aid, _ = guarded_db()
    jid = db.add_job(aid, "first_edition")
    runner = hr.Runner(db, today=date(2026, 9, 6))
    runner._hunt = _fixture_hunt

    def boom(row):
        raise _RestError('{"code":"23505","message":"duplicate key value"}')

    db.insert_edition = boom
    report = runner._run_one(db.jobs[jid])
    assert report.action == "failed"
    assert report.detail["error"] == "edition_persist_failed"


def test_unconfirmed_active_brief_cannot_persist_an_edition():
    db, aid, bid = guarded_db()
    db.briefs[bid]["confirmed_at"] = None
    jid = db.add_job(aid, "first_edition")
    runner = hr.Runner(db, today=date(2026, 9, 6))
    runner._hunt = _fixture_hunt
    report = runner._run_one(db.jobs[jid])
    assert db.editions == []
    assert report.detail["authority_reason"] == "brief_unconfirmed"


def test_real_requests_http_error_is_decoded_without_printing_body():
    import requests
    response = requests.Response()
    response.status_code = 400
    response._content = b'{"code":"P0001","message":"authority_changed:paused","details":null,"hint":null}'
    response.url = "https://fixture.invalid/rest/v1/editions"
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        assert hr.authority_refusal(exc) == "paused"
    else:
        raise AssertionError("Expected HTTP error")
