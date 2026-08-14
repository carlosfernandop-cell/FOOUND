"""
End-to-end test of main() with every external service mocked.

WHY THIS EXISTS: the render harness proved the EDITION was unchanged, but it
calls build_shortlist() directly and never runs main(). A refactor left three
calls inside main() using old signatures — passes_title, send_email,
build_shortlist — and every other test passed while the real pipeline crashed
on its first filter.

Rendering correctly is not the same as orchestrating correctly. This test
exercises the orchestration: the wiring between collection, filtering,
exclusions, ranking, delivery and reporting.

    python3 test_main_endtoend.py
"""
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

for k, v in {
    "NOTION_TOKEN": "x", "NOTION_DB_ID": "x", "GMAIL_USER": "x",
    "GMAIL_APP_PASSWORD": "x", "RECIPIENT_EMAIL": "x",
    "SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_KEY": "k",
}.items():
    os.environ[k] = v
os.environ.pop("ANTHROPIC_API_KEY", None)      # heuristic path, no AI calls
sys.argv = ["job_alerts.py"]

OK, FAIL = [], []
def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(f"{'OK  ' if cond else 'FAIL'}: {name}{'' if cond else ' — ' + str(detail)}")


def run_main(signals, break_publish=False, break_build=False,
             break_state=False, ai=False):
    """Run main() against a fake market and a fake Supabase. Returns the log."""
    import importlib
    import job_alerts as ja
    import foound_state as fs
    importlib.reload(fs)
    importlib.reload(ja)

    work = tempfile.mkdtemp()
    os.makedirs(os.path.join(work, "docs", "archive"), exist_ok=True)
    import shutil
    shutil.copy(os.path.join(HERE, "profile.md"), os.path.join(work, "profile.md"))

    def when(d):
        return datetime(2026, 8, d, 12, 0, 0, tzinfo=timezone.utc)

    market = [
        {"title": "Head of Brand", "company": "Suno", "location": "New York, NY",
         "url": "https://x/1", "posted_at": when(13)},
        {"title": "Creative Director", "company": "Anthropic", "location": "San Francisco, CA",
         "url": "https://x/2", "posted_at": when(12)},
        {"title": "VP of Brand", "company": "Ramp", "location": "New York, NY",
         "url": "https://x/3", "posted_at": when(11)},
        {"title": "Software Engineer", "company": "Stripe", "location": "Dublin",
         "url": "https://x/4", "posted_at": when(11)},          # filtered by title
        {"title": "Brand Director", "company": "Someone", "location": "Jakarta",
         "url": "https://x/5", "posted_at": when(11)},          # filtered by location
    ]

    ja.SCRAPERS = [("FakeMarket", lambda: list(market))]
    if ai:
        # Exercise the MODEL path: fits below 80 so every conditional branch
        # in the deep-read gate actually evaluates (a leftover global hid
        # behind a short-circuit here and crashed only in production).
        ja.ANTHROPIC_KEY = "test-key"
        ja.score_fit = lambda agent, profile, j, jd: (78, "why", "pause")
        ja.deep_look = lambda j, p: ""

    ja.get_existing_keys = lambda: set()
    ja.add_to_notion = lambda job: True
    ja.fetch_jd_text = lambda url: ""
    ja.publish_shortlist = (lambda agent: False) if break_publish else (lambda agent: True)
    if break_build:
        def _crash(*a, **k): raise RuntimeError("simulated build crash")
        ja.build_shortlist = _crash

    sent = {}
    ja.send_email = lambda agent, jobs, n=0: sent.update(
        {"agent": agent.name, "count": len(jobs)})

    if break_state:
        def _down(*a, **k): raise RuntimeError("simulated outage")
        fs._fetch_live = _down
        fs.LIVE_READ_BACKOFF = 0
    else:
        fs._fetch_live = lambda agent_id, url, key, agent_no=None: list(signals)

    import io, contextlib
    buf = io.StringIO()
    cwd = os.getcwd()
    os.chdir(work)
    exit_code = 0
    try:
        with contextlib.redirect_stdout(buf):
            try:
                ja.main()
            except SystemExit as e:
                exit_code = int(e.code or 0)
    finally:
        os.chdir(cwd)
        shutil.rmtree(work, ignore_errors=True)
    return buf.getvalue(), sent, exit_code


def snap(rk, title, company):
    return {"version": 1, "role_key": rk, "title": title, "company": company,
            "url": "https://x", "location": "NY", "fit": 90, "why": "w",
            "pause": "p", "edition_date": "2026-08-14"}


# ---------------------------------------------------------------- clean run
try:
    log, sent, code = run_main([])
    check("main() completes without raising", True)
    check("market union computed", "Market: 3 query term(s)" in log, log[:300])
    # 3 of 5 fixture roles pass, plus the agent's pinned manual job = 4.
    check("filters applied (engineer + wrong-location dropped)",
          "After filters: 4" in log,
          [l for l in log.split("\n") if "After filters" in l])
    check("pinned manual job survives filtering", "Manual: pinned" in log,
          [l for l in log.split("\n") if "Manual" in l])
    check("email sent through the agent", sent.get("agent") == "Carlos", str(sent))
    check("operator line emitted", "[operator]" in log,
          [l for l in log.split("\n") if "operator" in l])
    check("engine reported as heuristic", "· heuristic ·" in log,
          [l for l in log.split("\n") if "operator" in l])
    check("edition reported built", "built" in log)
    check("healthy run exits green", code == 0, str(code))
    check("outcome resolves (heuristic day = degraded-delivered, never ambiguous)",
          "outcome=degraded-delivered" in log,
          [l for l in log.split("\n") if "outcome" in l])
except Exception:
    check("main() completes without raising", False, "raised")
    traceback.print_exc()

# ------------------------------------------------- a PASS excludes its role
try:
    log2, _, _ = run_main([{
        "kind": "pass", "role_key": "head of brand|suno", "reason": "scope",
        "state": "active", "created_at": "2026-08-13T12:00:00+00:00",
        "snapshot": snap("head of brand|suno", "Head of Brand", "Suno")}])
    check("passed role excluded before ranking",
          "excluded 1 role(s)" in log2,
          [l for l in log2.split("\n") if "excluded" in l])
    check("exclusion runs after filtering, before ranking",
          "After filters: 4" in log2 and "excluded 1 role(s)" in log2,
          [l for l in log2.split("\n") if "filters" in l or "excluded" in l])
except Exception:
    check("passed role excluded before ranking", False, "raised")
    traceback.print_exc()

# ------------------------------------- an APPLIED role also leaves the list
try:
    log3, _, _ = run_main([{
        "kind": "applied", "role_key": "vp of brand|ramp", "role_state": "applied",
        "state": "active", "created_at": "2026-08-13T12:00:00+00:00",
        "snapshot": snap("vp of brand|ramp", "VP of Brand", "Ramp")}])
    check("applied role withdrawn from the competition",
          "excluded 1 role(s)" in log3 and "1 applied" in log3,
          [l for l in log3.split("\n") if "excluded" in l])
except Exception:
    check("applied role withdrawn from the competition", False, "raised")
    traceback.print_exc()

# ----------------------- GREEN MUST MEAN DELIVERED: exit-code truthfulness
try:
    logp, _, codep = run_main([], break_publish=True)
    check("publish failure turns the run RED", codep == 1, str(codep))
    check("red publish is named in the log", "RED: edition was not published" in logp,
          [l for l in logp.split("\n") if "RED" in l])
    check("publish failure resolves to outcome=failed", "outcome=failed" in logp)
except Exception:
    check("publish failure turns the run RED", False, "raised")
    traceback.print_exc()

try:
    logb, _, codeb = run_main([], break_build=True)
    check("build crash turns the run RED", codeb == 1, str(codeb))
    check("build crash is still caught (no stack trace to the runner)",
          "Shortlist failed" in logb,
          [l for l in logb.split("\n") if "failed" in l.lower()][:2])
except Exception:
    check("build crash turns the run RED", False, "raised")
    traceback.print_exc()

try:
    logs_, _, codes_ = run_main([], break_state=True)
    check("protective skip (no state, no snapshot) turns the run RED",
          codes_ == 1, str(codes_))
    check("skip is explained as deliberate", "ABORT" in logs_,
          [l for l in logs_.split("\n") if "ABORT" in l])
    check("skip resolves to outcome=skipped", "outcome=skipped" in logs_,
          [l for l in logs_.split("\n") if "outcome" in l])
except Exception:
    check("protective skip turns the run RED", False, "raised")
    traceback.print_exc()

# ----------------------- AI path: conditional branches must execute
try:
    loga, _, codea = run_main([], ai=True)
    check("AI-path run completes (deep-read gate evaluates fully)",
          codea == 0, str(codea) + " " + loga[-300:])
    check("AI path reports engine=ai", "· ai ·" in loga,
          [l for l in loga.split("\n") if "operator" in l])
    check("AI path delivers", "outcome=delivered" in loga or "outcome=degraded-delivered" in loga)
except Exception:
    check("AI-path run completes", False, "raised")
    traceback.print_exc()

print(f"\n{len(OK)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
