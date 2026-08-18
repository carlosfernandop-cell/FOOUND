"""Offline verification of foound_state.py — no Supabase required."""
import json, os, shutil, tempfile, sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foound_state as fs

OK, FAIL = [], []
def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(f"{'OK  ' if cond else 'FAIL'}: {name}{'' if cond else ' — ' + detail}")

def snap(rk, title, company):
    return {"version": 1, "role_key": rk, "title": title, "company": company,
            "url": "https://x", "location": "Austin", "fit": 90,
            "why": "...", "pause": "...", "edition_date": "2026-08-14"}

def row(rk, kind, reason=None, days_ago=0, note=None, title="T", company="C"):
    return {"kind": kind, "role_key": rk, "reason": reason, "note": note,
            "state": "active", "snapshot": snap(rk, title, company),
            "created_at": (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()}

tmp = tempfile.mkdtemp()
AG = "aaaa-1111"
# A configured endpoint that cannot be reached — distinct from "not configured".
UNREACHABLE = "http://127.0.0.1:9/nope"

# ---------------------------------------------------------------- 1. no state
try:
    fs.load_private_state(AG, snapshot_dir=os.path.join(tmp, "state"),
                          supabase_url=UNREACHABLE, service_key="k")
    check("no live + no snapshot raises", False, "did not raise")
except fs.PrivateStateUnavailable:
    check("no live + no snapshot raises PrivateStateUnavailable", True)
except Exception as e:
    check("no live + no snapshot raises", False, f"wrong type {type(e).__name__}")

# ------------------------------------- 1b. unconfigured != unreachable
st_unconf = fs.load_private_state(AG, snapshot_dir=os.path.join(tmp, "unconf"),
                                  supabase_url="", service_key="")
check("unconfigured loop returns empty state instead of failing",
      st_unconf.source == "unconfigured" and not st_unconf.excluded_keys)
check("unconfigured state is not marked stale", not st_unconf.stale)
check("unconfigured state reports as such in operator line",
      "unconfigured" in fs.AgentRunReport.from_state(st_unconf).line())

# ------------------------------------------------- 2. snapshot fallback works
sd = os.path.join(tmp, "state")
os.makedirs(sd, exist_ok=True)
rows = [row("head of brand|suno", "pass", "scope", title="Head of Brand", company="Suno"),
        row("vp brand|ramp", "applied", title="VP Brand", company="Ramp"),
        row("ecd|koto", "pass", "company", days_ago=30, title="ECD", company="Koto")]
with open(os.path.join(sd, f"agent_{AG}.json"), "w") as f:
    # RELATIVE, never a literal date: a hardcoded fetched_at is a time bomb
    # that detonates exactly MAX_SNAPSHOT_AGE_DAYS after it is written.
    json.dump({"snapshot_format": 1, "agent_id": AG,
               "fetched_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
               "rows": rows}, f)

st = fs.load_private_state(AG, snapshot_dir=sd, supabase_url=UNREACHABLE, service_key="k")
check("falls back to last-known-valid snapshot", st.source == "snapshot" and st.stale)
check("snapshot yields 3 exclusions", len(st.excluded_keys) == 3, str(st.excluded_keys))
check("passes and applied separated",
      len(st.user_passes) == 2 and len(st.applied) == 1)

# ------------------------------------------------------- 3. exclusion choke point
jobs = [{"k": "head of brand|suno"}, {"k": "vp brand|ramp"},
        {"k": "brand director|spotify"}, {"k": "ecd|koto"}]
kept = fs.apply_private_exclusions(jobs, st, key_fn=lambda j: j["k"])
check("exclusions remove passed AND applied",
      [j["k"] for j in kept] == ["brand director|spotify"], str(kept))

# ------------------------------------------------------- 4. leak assertion
try:
    fs.assert_no_private_leak([{"k": "head of brand|suno"}], st, lambda j: j["k"])
    check("leak assertion catches excluded role in near-misses", False, "did not raise")
except RuntimeError:
    check("leak assertion catches excluded role in near-misses", True)
fs.assert_no_private_leak(kept, st, lambda j: j["k"])
check("leak assertion passes on clean set", True)

# ------------------------------------------------------- 5. bounded context
block = fs.recent_decisions_block(st, name="Carlos")
check("recent decisions excludes >14d old role", "Koto" not in block, block)
check("recent decisions includes recent pass", "Suno" in block)
check("APPLIED never appears in ranking context", "Ramp" not in block, block)
check("instruction forbids generalization", "Do NOT infer general rules" in block)

# many passes -> capped at 10
many = [row(f"r{i}|c{i}", "pass", "scope", days_ago=1, company=f"Co{i}") for i in range(25)]
st2 = fs._build_state(AG, many, source="live")
check("recent decisions capped at 10", len(st2.recent_decisions()) == 10,
      str(len(st2.recent_decisions())))

# ------------------------------------------------------- 6. validation
check("rejects unversioned snapshot", not fs._valid_row(
    {"kind": "pass", "role_key": "a|b", "snapshot": {"title": "x"}}))
check("rejects applied carrying a reason", not fs._valid_row(
    {"kind": "applied", "role_key": "a|b", "reason": "scope", "snapshot": snap("a|b","t","c")}))
check("accepts a valid pass row", fs._valid_row(row("a|b", "pass", "scope")))

# ------------------------------------------------------- 7. private-path guard
try:
    fs.load_private_state(AG, snapshot_dir=os.path.join(tmp, "docs", "state"),
                          published_dir=os.path.join(tmp, "docs"))
    check("refuses to write private state under docs/", False, "did not raise")
except RuntimeError as e:
    check("refuses to write private state under docs/", "refusing" in str(e))

# ------------------------------------------------------- 8. atomic snapshot write
st3 = fs._build_state(AG, rows, source="live")
fs._write_snapshot(sd, st3, rows)
check("snapshot round-trips", fs._read_snapshot(sd, AG)["agent_id"] == AG)
check("no .tmp left behind",
      not any(f.endswith(".tmp") for f in os.listdir(sd)), str(os.listdir(sd)))
check("snapshot for wrong agent returns None", fs._read_snapshot(sd, "other") is None)

# ------------------------------------------- 9. freshness policy on snapshots
def write_snap(dirpath, agent, age_days):
    os.makedirs(dirpath, exist_ok=True)
    when = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    with open(os.path.join(dirpath, f"agent_{agent}.json"), "w") as f:
        json.dump({"snapshot_format": 1, "agent_id": agent,
                   "fetched_at": when, "rows": rows}, f)

fresh_dir = os.path.join(tmp, "fresh")
write_snap(fresh_dir, AG, 3.0)          # a Monday run after a Friday read
st_fresh = fs.load_private_state(AG, snapshot_dir=fresh_dir, agent_no=1,
                                 supabase_url=UNREACHABLE, service_key="k")
check("3-day-old snapshot is accepted (long-weekend case)",
      st_fresh.stale and st_fresh.snapshot_age_days is not None
      and 2.9 < st_fresh.snapshot_age_days < 3.1, str(st_fresh.snapshot_age_days))
check("stale state records last_live_read", bool(st_fresh.last_live_read))

old_dir = os.path.join(tmp, "old")
write_snap(old_dir, AG, 9.0)
try:
    fs.load_private_state(AG, snapshot_dir=old_dir, agent_no=1,
                          supabase_url=UNREACHABLE, service_key="k")
    check("snapshot beyond freshness limit is refused", False, "did not raise")
except fs.PrivateStateUnavailable as e:
    check("snapshot beyond freshness limit is refused", "old" in str(e).lower())

tuned_dir = os.path.join(tmp, "tuned")
write_snap(tuned_dir, AG, 9.0)
st_tuned = fs.load_private_state(AG, snapshot_dir=tuned_dir, agent_no=1,
                                 supabase_url=UNREACHABLE, service_key="k", max_age_days=30)
check("freshness limit is operationally tunable", st_tuned.stale)

bad_dir = os.path.join(tmp, "badts")
os.makedirs(bad_dir, exist_ok=True)
with open(os.path.join(bad_dir, f"agent_{AG}.json"), "w") as f:
    json.dump({"snapshot_format": 1, "agent_id": AG, "fetched_at": "nonsense",
               "rows": rows}, f)
try:
    fs.load_private_state(AG, snapshot_dir=bad_dir, supabase_url=UNREACHABLE, service_key="k")
    check("snapshot with unusable timestamp is refused", False, "did not raise")
except fs.PrivateStateUnavailable:
    check("snapshot with unusable timestamp is refused", True)

# ------------------------------------------ 10. operator report STATE/ENGINE/EDITION
rep = fs.AgentRunReport.from_state(st_fresh)
rep.engine, rep.edition, rep.delivered = "ai", "built", True
rep.last_good, rep.market_fetched, rep.foound = "Aug 11", 6204, 7
line = rep.line()
check("operator line carries agent number", "№001" in line, line)
check("operator line carries state", "· stale ·" in line, line)
check("operator line carries engine", "· ai ·" in line, line)
check("operator line carries edition+delivery", "built+delivered" in line, line)
check("operator line carries last good", "last_good Aug 11" in line, line)
check("operator line carries last live read when stale", "last_live " in line, line)
check("operator line carries state age", "state_age 3.0d" in line, line)
check("operator line carries market/foound", "6,204/7" in line, line)

live_rep = fs.AgentRunReport.from_state(fs._build_state(AG, rows, "live", agent_no=2))
check("live state reports live", "· live ·" in live_rep.line(), live_rep.line())
check("operator row is machine-readable", fs.AgentRunReport.as_row(rep)["foound"] == 7)

# ---------------------------------- 11. decision-only reasons teach nothing
fs.DECISION_ONLY_REASONS.add("timing")
fs.LEARNING_BEARING_REASONS.discard("timing")
check("'other' ships as decision-only (the neutral pass)",
      not fs.is_learning_bearing("other"))
mixed = [row("a|co", "pass", "scope", days_ago=1, company="ScopeCo"),
         row("b|co", "pass", "timing", days_ago=1, company="TimingCo")]
st_mixed = fs._build_state(AG, mixed, source="live")
blk = fs.recent_decisions_block(st_mixed)
check("learning-bearing reason reaches the prompt", "ScopeCo" in blk, blk)
check("decision-only reason teaches nothing", "TimingCo" not in blk, blk)
check("decision-only pass still excludes the role",
      "b|co" in st_mixed.excluded_keys)
fs.DECISION_ONLY_REASONS.discard("timing")

# ---------------------------------- 12. applied enrichment never invalidates
applied_row = row("vp brand|ramp", "applied", title="VP Brand", company="Ramp")
applied_row["id"] = "sig-1"
st_en = fs._build_state(AG, [applied_row], source="live")
check("applied row is flagged as needing source",
      len(fs.applied_rows_needing_source(st_en)) == 1)

def boom(url): raise RuntimeError("posting pulled")
n = fs.enrich_applied_source(st_en, fetch_jd=boom, patch_row=lambda *a: None)
check("failed enrichment enriches nothing", n == 0)
check("failed enrichment leaves APPLIED intact",
      "vp brand|ramp" in st_en.applied and "vp brand|ramp" in st_en.excluded_keys)

patched = {}
n2 = fs.enrich_applied_source(st_en, fetch_jd=lambda u: "JD TEXT",
                              patch_row=lambda i, d: patched.update({i: d}))
check("successful enrichment patches the snapshot",
      n2 == 1 and patched["sig-1"]["snapshot"]["source"]["jd_chars"] == 7)
check("enrichment preserves the guaranteed snapshot keys",
      patched["sig-1"]["snapshot"]["title"] == "VP Brand")

# ---------------------------------- 13. engine health: degraded vs fault
hd = tempfile.mkdtemp()
h1 = fs.record_engine_run(hd, "001", "heuristic")
check("first heuristic run is degraded, not healthy", h1["status"] == "degraded", str(h1))
fs.record_engine_run(hd, "001", "heuristic")
h3 = fs.record_engine_run(hd, "001", "heuristic")
check("three consecutive heuristic runs become a fault", h3["status"] == "fault", str(h3))
check("consecutive count tracked", h3["consecutive_heuristic"] == 3)
h4 = fs.record_engine_run(hd, "001", "ai")
check("a model-backed run resets health", h4["status"] == "healthy" and h4["consecutive_heuristic"] == 0)
check("last ai run recorded", bool(h4["last_ai_run"]))
h5 = fs.record_engine_run(hd, "001", "heuristic")
check("health persists across calls", h5["consecutive_heuristic"] == 1 and h5["last_ai_run"])
shutil.rmtree(hd, ignore_errors=True)

# ---------------------------------- 14. enrichment idempotency + backoff
done = row("x|co", "applied", title="X", company="Co")
done["id"] = "s2"
done["snapshot"]["source"] = {"jd_text": "t", "jd_chars": 1, "jd_fetched_at": "now"}
st_done = fs._build_state(AG, [done], source="live")
check("captured source is never re-fetched",
      len(fs.applied_rows_needing_source(st_done)) == 0)

dead = row("y|co", "applied", title="Y", company="Co")
dead["id"] = "s3"
dead["snapshot"]["source_attempts"] = fs.MAX_SOURCE_ATTEMPTS
st_dead = fs._build_state(AG, [dead], source="live")
check("dead posting backs off after max attempts",
      len(fs.applied_rows_needing_source(st_dead)) == 0)

trying = row("z|co", "applied", title="Z", company="Co")
trying["id"] = "s4"
st_try = fs._build_state(AG, [trying], source="live")
patches = {}
fs.enrich_applied_source(st_try, fetch_jd=lambda u: "", patch_row=lambda i, d: patches.update({i: d}))
check("failed capture increments attempts",
      patches["s4"]["snapshot"]["source_attempts"] == 1, str(patches))
check("failed capture preserves guaranteed keys",
      patches["s4"]["snapshot"]["title"] == "Z")

refresh = row("w|co", "applied", title="W", company="Co")
refresh["id"] = "s5"
refresh["snapshot"]["source"] = {"jd_text": "old"}
refresh["snapshot"]["source_refresh_requested"] = True
st_ref = fs._build_state(AG, [refresh], source="live")
check("explicit refresh request re-opens capture",
      len(fs.applied_rows_needing_source(st_ref)) == 1)

# ---------------------------------- 15. RECONSIDER: the persuade verb
rc = row("head of brand|suno", "reconsider")
rc["id"] = "r1"
st_rc = fs._build_state(AG, [row("a|co", "pass", reason="scope"),
                             row("b|co", "applied"), rc], source="live")
check("reconsider routed to its own map",
      set(st_rc.reconsider) == {"head of brand|suno"})
check("reconsider is NOT an exclusion — it pushes a role IN, not out",
      "head of brand|suno" not in st_rc.excluded_keys)
check("second_look_keys exposes exactly the reconsider set",
      st_rc.second_look_keys == {"head of brand|suno"})
check("pass and applied still excluded alongside reconsider",
      st_rc.excluded_keys == {"a|co", "b|co"})
check("reconsider never enters RECENT DECISIONS",
      all(s.get("kind") == "pass" for s in st_rc.recent_decisions()))

check("a bare reconsider row is valid", fs._valid_row(rc))
bad_rc = row("x|co", "reconsider", reason="scope")
check("a reconsider carrying a reason is rejected", not fs._valid_row(bad_rc))
bad_rc2 = row("y|co", "reconsider", note="please")
check("a reconsider carrying a note is rejected", not fs._valid_row(bad_rc2))

# answered lifecycle: settle only what the edition answered; failures tolerated
rc2 = row("cd|udio", "reconsider"); rc2["id"] = "r2"
st_ans = fs._build_state(AG, [rc, rc2], source="live")
settled = {}
n_set = fs.mark_reconsiders_answered(
    st_ans, {"head of brand|suno"}, patch_row=lambda i, d: settled.update({i: d}))
check("answered second look settles to 'answered'",
      n_set == 1 and settled == {"r1": {"state": "answered"}}, str(settled))

def boom(i, d): raise RuntimeError("db down")
n_fail = fs.mark_reconsiders_answered(st_ans, {"head of brand|suno"}, patch_row=boom)
check("a failed settle is swallowed — signal stays active, edition unaffected",
      n_fail == 0)
n_none = fs.mark_reconsiders_answered(st_ans, set(), patch_row=boom)
check("no answers means no patches, even with open questions", n_none == 0)

shutil.rmtree(tmp)
print(f"\n{len(OK)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
