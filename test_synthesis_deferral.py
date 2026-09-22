"""Runner side of migration 018, offline (no database): the claim door's
"deferred" answer is walked past, bounded, receipted in counts only, and the
discovery query excludes deferred ids and jobs still on the door's clock. These are emulations of the door; the
door itself is proven by test_upload_boundary.py on Postgres."""
import logging

import synthesis_runner as sr


class FakeDb(sr.Db):
    def __init__(self, jobs, answers):
        self.jobs = jobs            # ordered queued jobs
        self.answers = answers      # job id -> claim result
        self.claimed = []
        self.finalized = []

    def oldest_queued_synthesize_job(self, exclude=None):
        for j in self.jobs:
            if j["id"] not in (exclude or []):
                return j
        return None

    def claim(self, job_id):
        self.claimed.append(job_id)
        return self.answers[job_id]

    def finalize_failed(self, job_id, error):
        self.finalized.append((job_id, error))
        return {"status": "finalized"}

    def stale_running_synthesize_jobs(self, stale_minutes):
        return []


def runner(db):
    return sr.Runner(db, sr.Storage(), sr.ModelClient())


def job(i):
    return {"id": f"j{i}", "agent_id": f"a{i}", "requested_at": "2026-09-22T06:00:00Z"}


def test_deferred_jobs_are_walked_past_to_a_ready_one():
    db = FakeDb([job(1), job(2), job(3)], {
        "j1": {"status": "deferred", "pending": 1, "ready": 0},
        "j2": {"status": "deferred", "pending": 2, "ready": 1},
        "j3": {"status": "empty", "waited_out": False, "pending": 0},
    })
    rep = runner(db).run_once()
    assert db.claimed == ["j1", "j2", "j3"]
    assert rep.action == "empty" and rep.job_id == "j3"
    assert rep.detail["deferred"] == ["j1", "j2"]


def test_only_deferred_jobs_ends_the_beat_as_deferred_without_a_retry_loop():
    db = FakeDb([job(1)], {"j1": {"status": "deferred", "pending": 1, "ready": 0}})
    rep = runner(db).run_once()
    assert db.claimed == ["j1"]          # exactly once this beat
    assert rep.action == "deferred" and rep.detail["deferred"] == ["j1"]
    assert db.finalized == []


def test_walk_is_bounded():
    n = sr.DEFERRED_WALK_LIMIT + 3
    db = FakeDb([job(i) for i in range(n)],
                {f"j{i}": {"status": "deferred", "pending": 1, "ready": 0} for i in range(n)})
    rep = runner(db).run_once()
    assert len(db.claimed) == sr.DEFERRED_WALK_LIMIT
    assert rep.action == "deferred"


def test_deferral_log_carries_counts_never_ids(caplog):
    # a door (or a future door) that answered with lists instead of counts is
    # still logged as counts: ids never reach the public log
    db = FakeDb([job(1), job(2)], {
        "j1": {"status": "deferred", "pending": ["secret-item-id"], "ready": 0},
        "j2": {"status": "empty", "waited_out": True, "pending": ["other-secret"], "skipped": ["s"]},
    })
    with caplog.at_level(logging.INFO, logger="synthesis_runner"):
        runner(db).run_once()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "secret-item-id" not in text and "other-secret" not in text
    assert "pending=1" in text and "waited_out=True" in text


def test_restdb_discovery_excludes_deferred_ids_and_ignores_junk():
    class R(sr.RestDb):
        def __init__(self):
            self.paths = []

        def _select(self, path):
            self.paths.append(path)
            return []

    r = R()
    good = "0f7f7e2a-1b7e-4b2e-8c3a-0123456789ab"
    r.oldest_queued_synthesize_job(exclude=[good, "not a uuid; drop table jobs"])
    assert len(r.paths) == 2                                  # both tiers asked when idle
    assert all(f"&id=not.in.({good})" in p and "drop" not in p for p in r.paths)
    r.oldest_queued_synthesize_job()
    assert all("not.in" not in p for p in r.paths[2:])


def test_restdb_discovery_skips_jobs_still_on_the_door_clock():
    """Discovery asks only for queued jobs that are not waiting, or whose wait
    is over: started_at null, or started_at <= now - SYNTHESIS_WAIT_MINUTES,
    in the URL safe UTC form (no '+')."""
    import re

    class R(sr.RestDb):
        def __init__(self):
            self.paths = []

        def _select(self, path):
            self.paths.append(path)
            return []

    r = R()
    r.oldest_queued_synthesize_job()
    m = re.search(r"&or=\(started_at\.is\.null,started_at\.lte\.(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\)", r.paths[0])
    assert m, r.paths[0]
    assert "+" not in r.paths[0]
    assert sr.SYNTHESIS_WAIT_MINUTES == 60


def test_refused_malformed_request_ends_the_beat_as_refused_and_terminal():
    db = FakeDb([job(1), job(2)], {"j1": {"status": "refused", "reason": "malformed_request"}})
    rep = runner(db).run_once()
    assert rep.action == "refused" and db.claimed == ["j1"]   # the door failed it; next beat moves on
