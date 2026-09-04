"""FOOUND Hunt Runner — Move 1 (ONE JUDGE) tests. No network.

Contract v1.1 (2026-09-02). Groups:

C   compile: families as written; include = ROLE_SYNONYMS expansion;
    places → LOCATION_GAZETTEER; engine-default exclusions; priority houses
    structured only; seat_cap default 11; readiness reasons; deterministic
G   gazetteer + synonym tables, row by row, by meaning
E   eligibility on the AgentConfig: job_alerts.passes_title / passes_location
    (J3 preserved: ROLE is a hard gate; CONTEXT cannot rescue a role);
    C1 mechanical-recall fixtures (SF / Culver City / New York, NY)
V   verdicts: exact role_key; legacy title|company compatibility only;
    url: keys never fall back; reconsider → second look; no legacy writes
B   read budget isolation: default 25 untouched; private 40; public main()
    never passes read_budget
S   seat_edition equivalence with the pre-lift build_shortlist logic
L   ledger: complete refusal set, ≤5 shown, unread beyond budget
F   fail closed: non-001 agent → no_candidate_context before any model
    or adapter call; №001 resolves to profile.md
R   runner: empty edition is success; same-day noop; history fields;
    fail-closed brief errors; refresh; edition html contract for the At
    Work bind + voice + refusals + colophon; operator line hygiene;
    log/stdout hygiene
K   role_key precedence / source-qualified id / gh_jid identity
H9  boundaries: no market_seen, agent_config, publish, docs/, memory
H10 commission recovery predicate (mirrors 011)
"""

from __future__ import annotations

import inspect
import io
import json
import re
import uuid
import contextlib
from datetime import date, datetime, timedelta, timezone

import hunt_runner as hr


# ---------------------------------------------------------------------------
# In-memory DB
# ---------------------------------------------------------------------------

class MemoryDb(hr.HuntDb):
    def __init__(self):
        self.briefs: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.editions: list[dict] = []
        self.memory_reads = 0
        self.market_seen_reads = 0
        self.publish_calls = 0
        self.agent_numbers: dict[str, int] = {}
        self.memory: dict[str, list[dict]] = {}
        self.agent_state: dict[str, str] = {}

    def add_memory(self, agent_id, statements, *, layer="record", provenance="confirmed",
                   status="active", source="profile.md", handle="Subject"):
        rows = self.memory.setdefault(agent_id, [])
        for i, st in enumerate(statements):
            rows.append({"id": str(uuid.uuid4()), "agent_id": agent_id, "layer": layer,
                         "statement": st, "provenance": provenance, "status": status, "handle": handle,
                         "source": source, "created_at": f"2026-08-{10 + len(rows):02d}T00:00:00Z"})
        return rows

    def add_brief(self, agent_id, content, compiled_config=None,
                  readiness=None, version=1, state="active"):
        bid = str(uuid.uuid4())
        self.briefs[bid] = {
            "id": bid, "agent_id": agent_id, "version": version,
            "state": state, "content": content,
            "compiled_config": compiled_config, "readiness": readiness,
        }
        return bid

    def add_job(self, agent_id, type, status="queued", payload=None, requested_at=None):
        jid = str(uuid.uuid4())
        self.jobs[jid] = {
            "id": jid, "agent_id": agent_id, "type": type,
            "status": status, "payload": payload or {},
            "requested_at": requested_at or f"2026-09-{1 + len(self.jobs) % 28:02d}T00:00:00Z",
        }
        return jid

    def oldest_queued_hunt_jobs(self, limit: int):
        rows = [j for j in self.jobs.values()
                if j["status"] == "queued" and j["type"] in hr.HUNT_JOB_TYPES]
        return rows[:limit]

    def claim(self, job_id: str):
        j = self.jobs.get(job_id)
        if not j or j["status"] != "queued":
            return None
        j["status"] = "running"
        return dict(j)

    def complete(self, job_id: str):
        self.jobs[job_id]["status"] = "done"
        self.jobs[job_id]["error"] = None

    def fail(self, job_id: str, error: str):
        self.jobs[job_id]["status"] = "failed"
        self.jobs[job_id]["error"] = error

    def active_brief(self, agent_id: str):
        for b in self.briefs.values():
            if b["agent_id"] == agent_id and b["state"] == "active":
                return b
        return None

    def write_compile(self, brief_id: str, compiled: dict, readiness: str):
        assert readiness in ("ready", "not_ready")
        assert readiness != "limited"
        self.briefs[brief_id]["compiled_config"] = hr.persistable_compiled(compiled)
        self.briefs[brief_id]["readiness"] = readiness

    def editions_for_day(self, agent_id: str, day):
        iso = day.isoformat() if hasattr(day, "isoformat") else str(day)
        return [e for e in self.editions
                if e["agent_id"] == agent_id and e["edition_date"] == iso]

    def prior_edition_payloads(self, agent_id: str):
        return [e.get("payload") or {} for e in self.editions
                if e["agent_id"] == agent_id]

    def insert_edition(self, row: dict):
        day = row["edition_date"]
        for e in self.editions:
            if e["agent_id"] == row["agent_id"] and e["edition_date"] == day:
                raise RuntimeError("duplicate edition")
        rec = dict(row)
        rec.setdefault("id", str(uuid.uuid4()))
        self.editions.append(rec)

    def replace_edition(self, edition_id: str, row: dict):
        for e in self.editions:
            if e["id"] == edition_id:
                e.update({k: v for k, v in row.items() if k not in ("agent_id", "edition_date")})
                return
        raise RuntimeError("no such edition")

    def agent_no(self, agent_id: str):
        return self.agent_numbers.get(agent_id)

    def agent_id_for_no(self, agent_no):
        for aid, n in self.agent_numbers.items():
            if n == agent_no:
                return aid
        return None

    def confirmed_memory(self, agent_id):
        return [dict(r) for r in self.memory.get(agent_id, [])
                if r.get("status") == "active" and r.get("provenance") == "confirmed"]

    def next_brief_version(self, agent_id):
        vs = [b["version"] for b in self.briefs.values() if b["agent_id"] == agent_id]
        return (max(vs) + 1) if vs else 1

    def abandon_proposed_briefs(self, agent_id):
        n = 0
        for b in self.briefs.values():
            if b["agent_id"] == agent_id and b["state"] == "proposed":
                b["state"] = "abandoned"; n += 1
        return n

    def insert_brief(self, row):
        bid = str(uuid.uuid4())
        self.briefs[bid] = {"id": bid, "compiled_config": None, "readiness": None, **row}
        return bid

    def at_work_agents(self):
        return [{"id": aid, "agent_no": n} for aid, n in self.agent_numbers.items()
                if self.agent_state.get(aid) == "at_work"]

    def enqueue_job(self, agent_id, job_type, payload):
        if any(j["agent_id"] == agent_id and j["type"] == job_type and j["status"] == "queued" for j in self.jobs.values()):
            return False
        self.add_job(agent_id, job_type, payload=payload)
        return True

    def live_agents(self):
        return [{"id": aid, "agent_no": n, "state": self.agent_state.get(aid, "mirror_ready")}
                for aid, n in self.agent_numbers.items() if self.agent_state.get(aid) != "archived"]

    def briefs_in_force(self, agent_id):
        rows = [dict(b) for b in self.briefs.values() if b["agent_id"] == agent_id and b["state"] in ("proposed", "active")]
        return sorted(rows, key=lambda b: -b["version"])

    def open_mirror_count(self, agent_id):
        return len([r for r in self.memory.get(agent_id, [])
                    if r.get("status") == "active" and r.get("provenance") in ("stated", "extracted", "inferred")
                    and r.get("handle") and r.get("layer") in ("record", "self", "model")])

    def candidate_count(self, agent_id):
        return len([c for c in getattr(self, "candidates", []) if c["agent_id"] == agent_id])

    def next_candidate_version(self, agent_id):
        return len([c for c in getattr(self, "candidates", []) if c["agent_id"] == agent_id]) + 1

    def retire_candidate_drafts(self, agent_id):
        n = 0
        for c in getattr(self, "candidates", []):
            if c["agent_id"] == agent_id and c["state"] == "draft":
                c["state"] = "unpublished"; n += 1
        return n

    def insert_candidate(self, row):
        if not hasattr(self, "candidates"):
            self.candidates = []
        self.candidates.append(dict(row))

    def latest_candidate_page(self, agent_id):
        rows = [c for c in getattr(self, "candidates", []) if c["agent_id"] == agent_id]
        rows.sort(key=lambda c: c.get("version") or 0)
        return dict(rows[-1].get("page") or {}) if rows else None

    def last_job(self, agent_id, job_type):
        rows = [j for j in self.jobs.values() if j["agent_id"] == agent_id and j["type"] == job_type]
        return dict(rows[-1]) if rows else None


class FakeState:
    """Stands in for foound_state.PrivateState."""

    def __init__(self, excluded=(), second_look=()):
        self.excluded_keys = set(excluded)
        self.second_look_keys = set(second_look)


FROZEN_NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
FIXTURE_PROFILE = "Fixture Candidate Context. Twenty years in brand. Bar: build the function."
FROZEN_TODAY = date(2026, 9, 2)
# 09:00 Berlin on FROZEN_TODAY: past EDITION_HOUR_LOCAL, so tests of the daily
# sweep describe the sweep and never the wall clock. The hour has its own test.
AFTER_EDITION_HOUR = datetime(2026, 9, 2, 7, 0, tzinfo=timezone.utc)


def check(name, cond, detail=""):
    if not cond:
        raise AssertionError(f"FAIL {name}: {detail}")
    print(f"  ok  {name}")


def _seats_json(html_doc: str):
    return json.loads(html_doc.split('id="foound-seats">')[1].split("</script>")[0])


def _score_by_title(table: dict, default=None):
    """score hook: title → (fit, why, pause)."""
    def score(_agent, _profile, job, _jd):
        hit = table.get(job["title"])
        if hit is None:
            return default if default is not None else (None, None, None)
        fit, why, pause = hit
        return fit, why, pause
    return score


def _runner(db, raw, *, score=None, profile=FIXTURE_PROFILE, state=None,
            today=FROZEN_TODAY, fetch_jd=None, read_budget=hr.PRIVATE_READ_BUDGET):
    return hr.Runner(
        db, collector=lambda _c: raw, today=today,
        fetch_jd=fetch_jd if fetch_jd is not None else (lambda _u: ""),
        score=score, profile=profile,
        state_loader=(lambda _a, _n: state),
        read_budget=read_budget,
    )


def _ready_agent(db, content=None, agent_no=None):
    aid = str(uuid.uuid4())
    compiled = hr.compile_from_content(content or COMPLETE)
    assert hr.readiness_of(compiled) == "ready"
    db.add_brief(aid, content or COMPLETE,
                 compiled_config=hr.persistable_compiled(compiled),
                 readiness="ready")
    if agent_no is not None:
        db.agent_numbers[aid] = agent_no
    return aid


INCOMPLETE = {
    "chapters": [
        {
            "title": "THE MOVE",
            "subjects": [
                {"handle": "Lead", "lines": [
                    "Lead the creative function for a culture-shaping brand."
                ]},
            ],
        }
    ]
}

COMPLETE = {
    "chapters": [
        {
            "title": "THE MOVE",
            "subjects": [
                {"handle": "Lead", "lines": [
                    "Lead the creative function for a culture-shaping brand."
                ]},
            ],
        },
        {
            "title": "ROLE SPACE",
            "subjects": [
                {"handle": "Craft", "lines": [
                    "The seat is senior creative and brand leadership. "
                    "Creative Director, Head of Creative, Head of Brand."
                ]},
            ],
        },
        {
            "title": "WHERE",
            "subjects": [
                {"handle": "Geography", "lines": [
                    "NYC, California, remote US, London, Paris."
                ]},
            ],
        },
    ]
}

OTHER_TITLES = {
    "subjects": [
        {"title": "Ambition", "text": "Build the function, do not inherit it."},
        {"title": "Craft", "text": "Product Designer, Design Lead"},
        {"title": "Geography", "text": "London, Remote"},
    ]
}

STRUCTURED = {
    "include": ["creative director"],
    "accepted_locations": ["remote"],
    "search_queries": ["creative director"],
    "seat_cap": 3,
}

# Specimen Brief 5d260731 — authorized №001 text as stored
# (subjects THE MOVE / ROLE SPACE / WHERE). THE MOVE is ambition prose
# plus a seat line that names ECD exactly. ROLE SPACE writes the seat
# list; bare CD in that list is Creative Director. Fixture only — not
# a compiler catalog.
SPECIMEN_5d260731 = {
    "chapters": [
        {
            "title": "THE MOVE",
            "subjects": [
                {"handle": "Lead", "lines": [
                    "Lead the creative function for a culture-shaping brand. "
                    "Across markets. Build or transform that function. "
                    "Not inherit a finished one. Stay culturally relevant, "
                    "creatively ambitious, and unmistakably themselves. "
                    "The seat is ECD."
                ]},
            ],
        },
        {
            "title": "ROLE SPACE",
            "subjects": [
                {"handle": "Craft", "lines": [
                    "Seat: senior creative/brand leadership (CD, Group CD, "
                    "Executive CD, Head of Creative, Head of Brand, "
                    "VP Brand/Creative). "
                    "Global / multi-market; building or transforming a "
                    "creative function; consumer tech, platforms, and "
                    "culture-shaping brands. "
                    "Creatively ambitious, and unmistakably themselves."
                ]},
            ],
        },
        {
            "title": "WHERE",
            "subjects": [
                {"handle": "Geography", "lines": [
                    "NYC, California, remote US, London, Paris."
                ]},
            ],
        },
    ]
}

# Locked clean families for THIS specimen Brief only — how Brand writes them.
# First-seen order: ECD on THE MOVE seat line, then ROLE SPACE list with
# bare CD expanded to Creative Director.
SPECIMEN_FAMILIES = [
    "ecd",
    "creative director",
    "group cd",
    "executive cd",
    "head of creative",
    "head of brand",
    "vp brand/creative",
]

# Families first, then coherent ROLE SPACE concepts in first-seen order.
# "and unmistakably themselves" is authorized prose and must not appear.
SPECIMEN_INCLUDE = [
    "ecd",
    "creative director",
    "group cd",
    "executive cd",
    "head of creative",
    "head of brand",
    "vp brand/creative",
    "senior creative/brand leadership",
    "global / multi-market",
    "building or transforming a creative function",
    "consumer tech",
    "platforms",
    "culture-shaping brands",
    "creatively ambitious",
]

# Fictional Brief with different seat language. Must not yield SPECIMEN_FAMILIES.
FICTIONAL_OTHER_ROLES = {
    "subjects": [
        {
            "title": "Ambition",
            "text": (
                "Across markets. Build or transform that function. "
                "Not inherit a finished one."
            ),
        },
        {
            "title": "Craft",
            "text": "Staff Product Designer / Design Director.",
        },
        {
            "title": "Geography",
            "text": "Berlin, Remote",
        },
    ]
}

FICTIONAL_FAMILIES = [
    "staff product designer",
    "design director",
]



def test_h1_incomplete_brief_blocked():
    cfg = hr.compile_from_content(INCOMPLETE)
    check("H1 not_ready", hr.readiness_of(cfg) == "not_ready")
    check("H1 never limited", hr.readiness_of(cfg) != "limited")
    check("H1 reasons present", len(cfg["readiness_reasons"]) >= 1)
    check("H1 no_accepted_locations",
          "no_accepted_locations" in cfg["readiness_reasons"])
    check("H1 architecture note", "temporary" in cfg["readiness_architecture"])


def test_h2c_empty_content():
    cfg = hr.compile_from_content({})
    check("H2c not_ready", hr.readiness_of(cfg) == "not_ready")
    check("H2c no authority", "no_usable_hunt_authority" in cfg["readiness_reasons"])


def test_h6_other_titles_compile():
    cfg = hr.compile_from_content(OTHER_TITLES)
    check("H6 ready without THE MOVE labels", hr.readiness_of(cfg) == "ready")
    check("H6 include from Craft",
          any("designer" in x or "lead" in x for x in cfg["include"]))
    check("H6 locations from Geography",
          any("london" in x or "remote" in x for x in cfg["accepted_locations"]))
    used = " ".join(cfg["subjects_used"]).lower()
    check("H6 recorded other titles", "craft" in used and "geography" in used)


def _without_compiled_at(cfg: dict) -> dict:
    return {k: v for k, v in cfg.items() if k != "compiled_at"}


def test_compile_specimen_5d260731_no_punctuation_junk():
    cfg = hr.compile_from_content(SPECIMEN_5d260731)
    junk = {
        "brand leadership (cd",
        "creative)",
        "cd",
    }
    for term in cfg["include"] + cfg["families"]:
        check(f"CQ not junk {term!r}", term not in junk)
        check(f"CQ balanced punct {term!r}",
              term.count("(") == term.count(")"))
        check(f"CQ no leading cut {term!r}",
              not term.startswith(("(", ")", "/")))
        check(f"CQ no trailing cut {term!r}",
              not (term.endswith(")") and "(" not in term))
    check("CQ compound slash kept",
          "vp brand/creative" in cfg["families"])
    check("CQ parenthetical concept intact or absent",
          all("(" not in t or ")" in t for t in cfg["include"]))


def test_compile_specimen_5d260731_deterministic():
    a = hr.compile_from_content(SPECIMEN_5d260731)
    b = hr.compile_from_content(SPECIMEN_5d260731)
    check("CQ same families twice", a["search_queries"] == b["search_queries"])
    check("CQ minus compiled_at identical",
          _without_compiled_at(a) == _without_compiled_at(b))
    check("CQ compiled_at may differ or match",
          isinstance(a["compiled_at"], str) and isinstance(b["compiled_at"], str))


def test_h7_never_limited_or_at_work_inference():
    cfg = hr.compile_from_content(INCOMPLETE)
    check("H7 compile ignores agent state", hr.readiness_of(cfg) == "not_ready")
    db = MemoryDb()
    aid = str(uuid.uuid4())
    db.add_brief(aid, INCOMPLETE, readiness=None)
    jid = db.add_job(aid, "compile_brief")
    # Runner has no agent.state; compile cannot see at_work.
    Runner = hr.Runner(db, collector=lambda _c: [])
    reports = Runner.run()
    check("H7 compiled", reports[0].action == "compiled")
    check("H7 still not_ready", reports[0].readiness == "not_ready")
    brief = db.active_brief(aid)
    check("H7 persisted not_ready", brief["readiness"] == "not_ready")
    check("H7 persisted not limited", brief["readiness"] != "limited")
    check("H7 job done", db.jobs[jid]["status"] == "done")


def test_refresh_compiles_if_missing():
    db = MemoryDb()
    aid = str(uuid.uuid4())
    db.add_brief(aid, COMPLETE, compiled_config=None, readiness=None)
    db.add_memory(aid, ["Led brand at Acme."])
    jid = db.add_job(aid, "refresh_readiness")
    reports = hr.Runner(db, collector=lambda _c: []).run()
    check("refresh action", reports[0].action == "refreshed")
    check("refresh ready", reports[0].readiness == "ready")
    brief = db.active_brief(aid)
    check("refresh wrote config", bool(brief["compiled_config"]))
    check("refresh wrote ready", brief["readiness"] == "ready")
    check("refresh job done", db.jobs[jid]["status"] == "done")


def should_insert_recovery(state, readiness, editions_count, job_statuses):
    """Same gates as sql/011_commission_recovery.sql at_work branch."""
    if state != "at_work":
        return False
    if editions_count > 0:
        return False
    if any(s in ("queued", "running", "done") for s in job_statuses):
        return False
    if readiness != "ready":
        return False
    return True


def test_h10_commission_recovery_predicate():
    check("H10 insert when empty",
          should_insert_recovery("at_work", "ready", 0, []))
    check("H10 insert after failed only",
          should_insert_recovery("at_work", "ready", 0, ["failed"]))
    check("H10 no-op queued",
          not should_insert_recovery("at_work", "ready", 0, ["queued"]))
    check("H10 no-op running",
          not should_insert_recovery("at_work", "ready", 0, ["running"]))
    check("H10 no-op done",
          not should_insert_recovery("at_work", "ready", 0, ["done"]))
    check("H10 no-op editions exist",
          not should_insert_recovery("at_work", "ready", 1, []))
    check("H10 no-op not_ready",
          not should_insert_recovery("at_work", "not_ready", 0, []))
    check("H10 no-op limited",
          not should_insert_recovery("at_work", "limited", 0, []))
    check("H10 no-op other state",
          not should_insert_recovery("mirror_ready", "ready", 0, []))
    # second press after insert looks like queued/done present
    check("H10 second press no-op",
          not should_insert_recovery("at_work", "ready", 0, ["queued"]))


def test_compile_job_writes_ready():
    db = MemoryDb()
    aid = str(uuid.uuid4())
    db.add_brief(aid, COMPLETE)
    db.add_memory(aid, ["Led brand at Acme."])          # a person FOOUND can judge for
    jid = db.add_job(aid, "compile_brief")
    reports = hr.Runner(db, collector=lambda _c: []).run()
    check("compile action", reports[0].action == "compiled")
    check("compile ready", reports[0].readiness == "ready")
    check("compile job done", db.jobs[jid]["status"] == "done")
    brief = db.active_brief(aid)
    cfg = brief["compiled_config"]
    check("compile persisted include", bool(cfg["include"]))
    check("compile persisted note", "temporary" in cfg["readiness_architecture"])
    check("compile: no person reason when memory is confirmed", "no_candidate_context" not in cfg["readiness_reasons"])


def test_compile_names_the_missing_person():
    """Move 2: a complete Brief with nothing confirmed in Memory is not ready —
    the reason is named so the app can say 'confirm your record' instead of
    letting a commission fail later with no_candidate_context."""
    db = MemoryDb()
    aid = str(uuid.uuid4())
    db.add_brief(aid, COMPLETE)
    db.add_memory(aid, ["Led brand at Acme."], provenance="stated")   # a belief, not yet confirmed
    db.add_job(aid, "compile_brief")
    reports = hr.Runner(db, collector=lambda _c: []).run()
    brief = db.active_brief(aid)
    check("compile: not ready without a person", reports[0].readiness == "not_ready" and brief["readiness"] == "not_ready")
    check("compile: reason named", "no_candidate_context" in brief["compiled_config"]["readiness_reasons"])
    check("compile: Brief itself still complete", bool(brief["compiled_config"]["include"]) and bool(brief["compiled_config"]["accepted_locations"]))
    db2 = MemoryDb()
    aid2 = str(uuid.uuid4()); db2.agent_numbers[aid2] = 1
    db2.add_brief(aid2, COMPLETE)
    db2.add_job(aid2, "refresh_readiness")
    r2 = hr.Runner(db2, collector=lambda _c: []).run()
    check("refresh: №001 ready via interim profile.md", r2[0].readiness == "ready"
          and "no_candidate_context" not in db2.active_brief(aid2)["compiled_config"]["readiness_reasons"])


def test_role_key_precedence():
    a = {"title": "CD", "company": "Acme", "location": "NYC",
         "posting_id": "gh-99", "source": "greenhouse",
         "url": "https://Example.com/jobs/1?utm_source=x"}
    b = {"title": "CD Tweaked", "company": "Acme", "location": "NYC",
         "posting_id": "gh-99", "source": "greenhouse",
         "url": "https://example.com/jobs/2"}
    check("RKp id wins", hr.role_key(a) == "id:greenhouse:gh-99")
    check("RKp same source+id survives title tweak", hr.role_key(a) == hr.role_key(b))

    u1 = {"title": "CD", "company": "Acme", "location": "NYC",
          "url": "https://WWW.Jobs.Example/apply/42/?utm_campaign=x&gclid=1"}
    u2 = {"title": "CD, Brand", "company": "Acme", "location": "Remote",
          "url": "https://jobs.example/apply/42"}
    check("RKp url normalized equal", hr.role_key(u1) == hr.role_key(u2))
    check("RKp url prefix", hr.role_key(u1).startswith("url:"))

    d1 = {"title": "CD", "company": "Acme", "location": "NYC",
          "url": "https://jobs.example/a"}
    d2 = {"title": "CD", "company": "Acme", "location": "NYC",
          "url": "https://jobs.example/b"}
    check("RKp distinct urls do not collapse", hr.role_key(d1) != hr.role_key(d2))

    f1 = {"title": "CD", "company": "Acme", "location": "NYC"}
    f2 = {"title": "CD", "company": "Acme", "location": "London"}
    check("RKp fallback includes location",
          hr.role_key(f1) == "tcl:cd|acme|nyc")
    check("RKp two openings no collapse", hr.role_key(f1) != hr.role_key(f2))


def test_role_key_source_qualified():
    """Same posting_id from two ATSs must not collapse. Same source+id is one key."""
    gh = {"title": "CD", "company": "Acme", "location": "NYC",
          "posting_id": "99", "source": "greenhouse"}
    lever = {"title": "CD", "company": "Acme", "location": "NYC",
             "posting_id": "99", "source": "lever"}
    gh_again = {"title": "CD Tweaked", "company": "Acme", "location": "Remote",
                "posting_id": "99", "source": "greenhouse"}
    check("RKs two sources two keys", hr.role_key(gh) != hr.role_key(lever))
    check("RKs greenhouse form", hr.role_key(gh) == "id:greenhouse:99")
    check("RKs lever form", hr.role_key(lever) == "id:lever:99")
    check("RKs same source+id one key", hr.role_key(gh) == hr.role_key(gh_again))
    check("RKs never bare id", hr.role_key(gh) != "id:99")
    check("RKs never bare prefix", not hr.role_key(gh).startswith("id:99"))

    via_provider = {"title": "CD", "company": "Acme", "posting_id": "99",
                    "provider": "ashby"}
    check("RKs provider field", hr.role_key(via_provider) == "id:ashby:99")
    via_gh_id = {"title": "CD", "company": "Acme", "gh_id": "99"}
    check("RKs gh_id implies greenhouse", hr.role_key(via_gh_id) == "id:greenhouse:99")

    no_src = {"title": "CD", "company": "Acme", "location": "NYC",
              "posting_id": "99", "url": "https://jobs.example/x"}
    check("RKs no source never bare id", hr.role_key(no_src) == "url:https://jobs.example/x")
    hunt_tag = {"title": "CD", "company": "Acme", "location": "NYC",
                "posting_id": "99", "source": "adapter",
                "url": "https://jobs.example/y"}
    check("RKs hunt tag is not a namespace",
          hr.role_key(hunt_tag) == "url:https://jobs.example/y")


def test_role_key_gh_jid_is_identity():
    """Greenhouse URLs that differ only by gh_jid are two roles when no posting id."""
    a = {"title": "Creative Director", "company": "Stripe", "location": "US",
         "url": "https://stripe.com/jobs/search?gh_jid=8001341&utm_source=x&gh_src=abc"}
    b = {"title": "Creative Director", "company": "Stripe", "location": "US",
         "url": "https://stripe.com/jobs/search?gh_jid=8001342&utm_source=x&gh_src=abc"}
    same = {"title": "Creative Director, Copy", "company": "Stripe", "location": "Remote",
            "url": "https://stripe.com/jobs/search?gh_jid=8001341&utm_campaign=other&gh_src=zzz"}
    check("RKg two gh_jid two url keys", hr.role_key(a) != hr.role_key(b))
    check("RKg both url:", hr.role_key(a).startswith("url:") and hr.role_key(b).startswith("url:"))
    check("RKg same gh_jid same key", hr.role_key(a) == hr.role_key(same))
    check("RKg gh_jid kept", "gh_jid=8001341" in hr.role_key(a))
    check("RKg other gh_jid kept", "gh_jid=8001342" in hr.role_key(b))
    check("RKg tracking stripped",
          "utm_" not in hr.role_key(a) and "gh_src" not in hr.role_key(a))
    check("RKg exact form",
          hr.role_key(a) == "url:https://stripe.com/jobs/search?gh_jid=8001341")

    duo_a = {"title": "CD", "company": "Duolingo",
             "url": "https://careers.duolingo.com/jobs/8442934002?gh_jid=8442934002"}
    duo_b = {"title": "CD", "company": "Duolingo",
             "url": "https://careers.duolingo.com/jobs/8442934002?gh_jid=8442932002"}
    check("RKg path-same gh_jid-diff two keys", hr.role_key(duo_a) != hr.role_key(duo_b))


def test_role_key_single_definition():
    """One role_key(row, company=None). The two-arg title, company form is gone."""
    import inspect
    check("RKd one def", open(hr.__file__, encoding="utf-8").read().count("def role_key(") == 1)
    params = list(inspect.signature(hr.role_key).parameters)
    check("RKd row first", params[0] == "row")
    check("RKd optional company", params == ["row", "company"])
    check("RKd string title is not a key", hr.role_key("Creative Director", "Acme") == "")
    check("RKd string not tcl", not str(hr.role_key("CD", "Acme")).startswith("tcl:"))
    row = {"title": "CD", "company": "Acme", "location": "NYC"}
    check("RKd row tcl", hr.role_key(row) == "tcl:cd|acme|nyc")
    check("RKd company override", hr.role_key(row, company="Nova") == "tcl:cd|nova|nyc")


def test_adapter_isolation_smoke():
    """Import job_alerts.SCRAPERS via hunt_runner and collect through a
    stubbed scraper. Public Shortlist / Notion / email / docs must stay idle.
    """
    hits = {"publish": 0, "notion": 0, "smtp": 0, "docs": 0, "main": 0}

    def stub_scraper(*_a, **_k):
        return [{
            "title": "Creative Director",
            "company": "IsoCo",
            "location": "Remote",
            "url": "https://example.invalid/iso",
            "posting_id": "iso-1",
        }]

    ja = hr._import_job_alerts_adapters()
    check("smoke SCRAPERS present", hasattr(ja, "SCRAPERS") and len(ja.SCRAPERS) > 0)
    check("smoke publish_shortlist exists but unused",
          hasattr(ja, "publish_shortlist"))

    orig_pub = ja.publish_shortlist
    orig_mail = getattr(ja, "send_email", None)
    orig_notion = getattr(ja, "add_to_notion", None)

    def _boom_pub(*_a, **_k):
        hits["publish"] += 1
        raise AssertionError("publish_shortlist called")

    def _boom_mail(*_a, **_k):
        hits["smtp"] += 1
        raise AssertionError("send_email called")

    def _boom_notion(*_a, **_k):
        hits["notion"] += 1
        raise AssertionError("add_to_notion called")

    ja.publish_shortlist = _boom_pub
    if orig_mail:
        ja.send_email = _boom_mail
    if orig_notion:
        ja.add_to_notion = _boom_notion

    real_open = open

    def guarded_open(path, *a, **k):
        p = str(path).replace("\\", "/")
        if "/docs/" in p or p.startswith("docs/") or p.endswith("/docs"):
            hits["docs"] += 1
            raise AssertionError(f"docs write: {path}")
        return real_open(path, *a, **k)

    import builtins
    builtins.open = guarded_open
    try:
        raw = hr.live_collect(
            {"search_queries": ["creative director"]},
            scraper_entries=[("Stub", stub_scraper)],
        )
    finally:
        builtins.open = real_open
        ja.publish_shortlist = orig_pub
        if orig_mail:
            ja.send_email = orig_mail
        if orig_notion:
            ja.add_to_notion = orig_notion

    check("smoke collected stub row", len(raw) == 1)
    check("smoke no publish", hits["publish"] == 0)
    check("smoke no notion", hits["notion"] == 0)
    check("smoke no smtp", hits["smtp"] == 0)
    check("smoke no docs write", hits["docs"] == 0)
    check("smoke hunt publish_public false", hr.HUNT_PUBLISH_PUBLIC is False)
    check("smoke did not call main", hits["main"] == 0)
    src = open(hr.__file__, encoding="utf-8").read()
    check("smoke runner never calls publisher",
          not re.search(r"publish_shortlist\s*\(", src))
    check("smoke runner never calls send_email",
          not re.search(r"send_email\s*\(", src))
    check("smoke runner never calls add_to_notion",
          not re.search(r"add_to_notion\s*\(", src))


def test_logs_have_no_brief_copy(caplog=None):
    """Public-log hygiene: processor log lines are ids/counts/enums."""
    import logging
    db = MemoryDb()
    aid = _ready_agent(db, COMPLETE)
    db.add_job(aid, "first_edition")
    raw = [{"title": "Creative Director SECRET", "company": "HiddenCo",
            "location": "Remote", "url": "https://secret.example/job"}]
    buf = []

    class H(logging.Handler):
        def emit(self, record):
            buf.append(self.format(record))

    h = H()
    h.setFormatter(logging.Formatter("%(message)s"))
    hr.log.addHandler(h)
    hr.log.setLevel(logging.INFO)
    try:
        hr.Runner(db, collector=lambda _c: raw, today=date(2026, 8, 28)).run()
    finally:
        hr.log.removeHandler(h)
    blob = "\n".join(buf)
    check("log no seat title", "SECRET" not in blob)
    check("log no company", "HiddenCo" not in blob)
    check("log no url", "secret.example" not in blob)
    check("log no brief copy", "culture-shaping" not in blob)


def _fake_score(_agent, profile, job, _jd):
    # Profile is personal context. Brief must not be the ranker.
    if profile and ("THE MOVE" in profile or "search_queries" in profile):
        raise AssertionError("Brief fed into score_fit")
    if job["company"] == "Duolingo":
        return 68, "Duolingo is building a culturally fluent brand voice.", \
            "Posting is thin; seniority needs verifying."
    return 78, "Suno is building brand identity in real time.", \
        "Title reads campaign-execution rather than full brand ownership."


def _fake_jd(url):
    return "JD TEXT" if url else ""


def test_why_now_reuses_shortlist_logic():
    ja = hr._import_job_alerts_adapters()
    job = {
        "title": "Creative Director",
        "company": "Acme",
        "posted_at": datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc),
    }
    text_new = ja.why_now_text(job, True, now=FROZEN_NOW)
    check("H14 new surfaces",
          text_new.startswith("Surfaced for the first time this morning"))
    check("H14 new posted today", "posted today" in text_new)
    check("H14 still open", "still open as of 8:00 AM ET" in text_new)
    older = {
        "title": "Creative Director",
        "company": "Acme",
        "posted_at": datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    }
    resurfaced = ja.why_now_text(older, False, now=FROZEN_NOW)
    check("H14 resurfaced age",
          resurfaced == "Posted 8 days ago &middot; still open as of 8:00 AM ET")
    iso_job = {
        "title": "Creative Director",
        "company": "Acme",
        "posted_at": "2026-08-20T12:00:00Z",
    }
    from_iso = ja.why_now_text(iso_job, False, now=FROZEN_NOW)
    check("H14 iso posted_at", from_iso == resurfaced)


def test_seclabels_follow_fit_order():
    seats = [
        {"role_key": "a", "company": "First", "title": "CD",
         "location": "X", "fit": 70, "ai_why": "w", "ai_pause": "p",
         "why_now": "n"},
        {"role_key": "b", "company": "Second", "title": "CD",
         "location": "X", "fit": 90, "ai_why": "w", "ai_pause": "p",
         "why_now": "n"},
        {"role_key": "c", "company": "Third", "title": "CD",
         "location": "X", "fit": 65, "ai_why": "w", "ai_pause": "p",
         "why_now": "n"},
    ]
    labeled = hr.assign_editorial_labels(seats)
    check("H14 label order by fit",
          [s["company"] for s in labeled] == ["Second", "First", "Third"])
    check("H14 lead is highest fit",
          labeled[0]["lead"] is True and labeled[0]["seclabel"].startswith(
              "I'd start with Second"))
    check("H14 unusually strong not used for 70",
          labeled[1]["lead"] is False
          and labeled[1]["seclabel"] == "Worth your attention")
    check("H14 worth attention",
          labeled[2]["seclabel"] == "Worth your attention")
    html_doc = hr.render_edition_html(seats)
    i0, i1, i2 = (html_doc.index(name) for name in ("Second", "First", "Third"))
    check("H14 html ordered by fit", i0 < i1 < i2)
    check("H14 html worth attention",
          '<div class="seclabel">Worth your attention</div>' in html_doc)
    check("H14 html no rank_with_fit", "rank_with_fit" not in html_doc)
    strong = [
        {**seats[0], "fit": 66},
        {**seats[1], "fit": 85},
        {**seats[2], "fit": 82},
    ]
    strong_labeled = hr.assign_editorial_labels(strong)
    check("H14 unusually strong remainder",
          [s["company"] for s in strong_labeled] == ["Second", "Third", "First"])
    check("H14 unusually strong label",
          strong_labeled[1]["seclabel"] == "Unusually strong"
          and strong_labeled[2]["seclabel"] == "Worth your attention")
    tied = [
        {**seats[0], "fit": 70},
        {**seats[1], "fit": 70},
        {**seats[2], "fit": 70},
    ]
    tied_labeled = hr.assign_editorial_labels(tied)
    check("H14 ties stay stable",
          [s["company"] for s in tied_labeled] == ["First", "Second", "Third"]
          and tied_labeled[0]["seclabel"] == "I'd start with First")


# ---------------------------------------------------------------------------
# C · compile
# ---------------------------------------------------------------------------

SPECIMEN_INCLUDE_EXPANDED = [
    "executive creative",
    "creative director", "director of creative", "director, creative",
    "group creative",
    "head of creative", "creative lead",
    "head of brand", "brand director", "director of brand", "director, brand", "brand lead",
    "vp of creative", "vp, creative", "vp creative", "vp of brand", "vp, brand", "vp brand",
]


def test_c_h2_complete_brief_ready():
    cfg = hr.compile_from_content(COMPLETE)
    check("C ready", hr.readiness_of(cfg) == "ready")
    check("C families as written",
          cfg["families"] == ["creative director", "head of creative", "head of brand"], cfg["families"])
    check("C include expanded", "director of creative" in cfg["include"]
          and "creative lead" in cfg["include"] and "brand director" in cfg["include"])
    check("C location phrases kept",
          cfg["location_phrases"] == ["nyc", "california", "remote us", "london", "paris"],
          cfg["location_phrases"])
    check("C gazetteer expanded", "culver city" in cfg["accepted_locations"]
          and "san francisco" in cfg["accepted_locations"]
          and "new york" in cfg["accepted_locations"])
    check("C engine default excludes", cfg["exclude_type"] == list(hr.ENGINE_DEFAULT_EXCLUDES))
    check("C seat_cap default 11", cfg["seat_cap"] == 11)
    check("C priority empty unless structured", cfg["priority_companies"] == [])
    check("C search queries: the Brief's craft nouns bare, then its seats quoted (Move 3)",
          cfg["search_queries"] == ["creative", "brand", '"creative director"',
                                    '"head of creative"', '"head of brand"']
          and len(cfg["search_queries"]) <= hr.MAX_SEARCH_QUERIES, cfg["search_queries"])
    check("C reasons empty when ready", cfg["readiness_reasons"] == [])
    check("C _readiness stripped on persist",
          "_readiness" not in hr.persistable_compiled(cfg))
    for key in ("subjects_used", "families", "include", "exclude_type", "location_phrases",
                "accepted_locations", "search_queries", "priority_companies", "seat_cap",
                "compiled_at", "engine_sha", "readiness_reasons", "readiness_architecture"):
        check(f"C key {key}", key in cfg)


def test_c_structured_content():
    cfg = hr.compile_from_content({
        "include": ["creative director"], "accepted_locations": ["remote"],
        "search_queries": ["creative director"], "seat_cap": 3,
        "priority_companies": ["apple"], "exclude_type": ["freelance"],
    })
    check("C2b ready", hr.readiness_of(cfg) == "ready")
    check("C2b include", cfg["include"] == ["creative director", "director of creative", "director, creative"])
    check("C2b remote", "remote" in cfg["accepted_locations"])
    check("C2b seat_cap", cfg["seat_cap"] == 3)
    check("C2b priority kept as written", cfg["priority_companies"] == ["apple"])
    check("C2b brief exclusions ∪ defaults",
          cfg["exclude_type"][0] == "freelance" and "intern" in cfg["exclude_type"])


def test_c_specimen_5d260731():
    cfg = hr.compile_from_content(SPECIMEN_5d260731)
    check("CQ specimen ready", hr.readiness_of(cfg) == "ready")
    check("CQ families are the seven", cfg["families"] == SPECIMEN_FAMILIES, cfg["families"])
    check("CQ include is the expansion", cfg["include"] == SPECIMEN_INCLUDE_EXPANDED, cfg["include"])
    check("CQ no bare cd", "cd" not in cfg["include"])
    check("CQ no concept mining",
          not any(x in cfg["include"] for x in ("platforms", "consumer tech", "creatively ambitious",
                                                "global / multi-market", "culture-shaping brands")))
    check("CQ locations intact", cfg["location_phrases"] == ["nyc", "california", "remote us", "london", "paris"])
    check("CQ Apple not a priority house unless the Brief says so", cfg["priority_companies"] == [])


def test_c_fictional_brief_own_families():
    cfg = hr.compile_from_content(FICTIONAL_OTHER_ROLES)
    check("CQ fictional ready", hr.readiness_of(cfg) == "ready")
    check("CQ fictional families", cfg["families"] == FICTIONAL_FAMILIES, cfg["families"])
    check("CQ none of №001's", not any(f in cfg["include"] for f in ("executive creative", "head of brand")))
    check("CQ berlin + remote", "berlin" in cfg["accepted_locations"] and "remote" in cfg["accepted_locations"])


def test_c_ready_requires_families_and_locations():
    locs_only = hr.compile_from_content({"accepted_locations": ["remote"]})
    check("CQ locations-only not_ready", hr.readiness_of(locs_only) == "not_ready")
    check("CQ locations-only needs families", "no_role_families" in locs_only["readiness_reasons"])
    fams_only = hr.compile_from_content({"search_queries": ["creative director"]})
    check("CQ families-only not_ready", hr.readiness_of(fams_only) == "not_ready")
    check("CQ families-only needs locations", "no_accepted_locations" in fams_only["readiness_reasons"])
    empty = hr.compile_from_content({})
    check("CQ empty no authority", "no_usable_hunt_authority" in empty["readiness_reasons"])


def test_c_unmapped_location_is_informational():
    cfg = hr.compile_from_content({"search_queries": ["creative director"],
                                   "accepted_locations": ["london", "bogotá"]})
    check("C unmapped still ready", hr.readiness_of(cfg) == "ready")
    check("C unmapped reason present",
          "unmapped_location_phrase:bogotá" in cfg["readiness_reasons"], cfg["readiness_reasons"])
    check("C unmapped phrase kept literally", "bogotá" in cfg["accepted_locations"])


def test_c_skip_market_and_avoid():
    cfg = hr.compile_from_content({
        "subjects": [
            {"title": "ROLE SPACE", "text": "Creative Director"},
            {"title": "WHERE", "text": "Remote"},
            {"title": "MARKET", "text": "Anthropic, OpenAI"},
            {"title": "AVOID", "text": "Events, content ops"},
        ]
    })
    check("C6b no include from MARKET", "anthropic, openai" not in cfg["include"])
    check("C6b AVOID prose is off (engine defaults only)",
          cfg["exclude_type"] == list(hr.ENGINE_DEFAULT_EXCLUDES))


# ---------------------------------------------------------------------------
# G · gazetteer and synonym tables — by meaning
# ---------------------------------------------------------------------------

def test_g_gazetteer_rows():
    rows = [
        ("major us hubs", {"new york", "los angeles", "san francisco", "chicago", "austin", "seattle"}),
        ("california", {"culver city", "san francisco", "san jose", "los angeles", "san diego"}),
        ("nyc", {"new york", "nyc", "brooklyn"}),
        ("new york, ny", {"new york"}),
        ("remote us", {"remote", "united states"}),
        ("remote", {"remote"}),
        ("london", {"london"}),
        ("uk", {"london", "united kingdom", "manchester"}),
        ("paris", {"paris"}),
        ("major european capitals", {"london", "paris", "berlin", "madrid", "rome", "europe"}),
        ("and the major european capitals", {"london", "paris", "berlin"}),
        ("toronto", {"toronto"}),
        ("canada", {"toronto", "montreal", "vancouver"}),
        ("united states", {"united states", "us", "new york", "san francisco"}),
    ]
    for phrase, expect in rows:
        tokens, mapped = hr.expand_location_phrase(phrase)
        check(f"G mapped '{phrase}'", mapped)
        check(f"G meaning '{phrase}'", expect <= set(tokens), set(tokens) - expect)
    for phrase in ("bogotá", "singapore", "tokyo"):
        tokens, mapped = hr.expand_location_phrase(phrase)
        check(f"G unmapped '{phrase}' → itself", tokens == [phrase] and not mapped)
    check("G every row has a meaning", all(m for m, _ in hr.LOCATION_GAZETTEER.values()))


def test_g_role_synonyms():
    rows = [
        ("cd", {"creative director"}),
        ("creative director", {"creative director", "director of creative", "director, creative"}),
        ("ecd", {"executive creative"}),
        ("executive cd", {"executive creative"}),
        ("gcd", {"group creative"}),
        ("group cd", {"group creative"}),
        ("head of creative", {"head of creative", "creative lead"}),
        ("head of brand", {"head of brand", "brand director", "director of brand", "brand lead"}),
        ("vp brand/creative", {"vp of creative", "vp, creative", "vp of brand", "vp, brand"}),
        ("design director", {"design director"}),
        ("chief creative officer", {"chief creative"}),
        ("staff product designer", {"staff product designer"}),
    ]
    for fam, expect in rows:
        got = set(hr.expand_role_family(fam))
        check(f"G synonym '{fam}'", expect <= got, got)


# ---------------------------------------------------------------------------
# E · eligibility on the AgentConfig (job_alerts gates)
# ---------------------------------------------------------------------------

def _specimen_agent():
    ja = hr._import_job_alerts_adapters()
    cfg = hr.compile_from_content(SPECIMEN_5d260731)
    return ja, hr.agent_config_from_brief(ja, cfg, agent_id="a", agent_no=7)


def test_e_c1_location_recall_fixtures():
    ja, agent = _specimen_agent()
    for loc in ("San Francisco", "Culver City", "New York, NY", "London, England",
                "Remote - US", "3 Locations", "", "Paris, France", "Remote"):
        check(f"E location passes '{loc}'", ja.passes_location(agent, loc))
    for loc in ("Jakarta", "Singapore", "Tokyo, Japan"):
        check(f"E location fails '{loc}'", not ja.passes_location(agent, loc))


def test_e_role_gate_j3_preserved():
    ja, agent = _specimen_agent()
    for title in ("Creative Director, Marketing", "Senior Creative Director",
                  "Executive Creative Director", "Head of Brand, Creative Studio",
                  "VP, Brand Creative", "Group Creative Director, Apple Music",
                  "Head of Brand, Adobe Creative", "Creative Director, Design - Apple TV Sports Marketing"):
        check(f"E role passes '{title}'", ja.passes_title(agent, title))
    for title in ("Solutions Architect, Platforms",
                  "Senior Manager, Interactive World Model Platforms",
                  "Senior Software Engineer - GPU Local AI Platforms",
                  "Creative Director Intern", "Brand Designer", "Head of Creative Operations Contractor"):
        check(f"E role fails '{title}'", not ja.passes_title(agent, title))


def test_e_context_cannot_rescue_role():
    """J3: a role that fails ROLE is out, however the company or a description reads."""
    ja, agent = _specimen_agent()
    raw = [
        {"title": "Solutions Architect, Platforms", "company": "Stripe brand creative director",
         "location": "San Francisco", "url": "https://stripe.com/jobs/1",
         "description": "creative director head of brand executive creative"},
        {"title": "Creative Director, Marketing", "company": "Suno",
         "location": "NYC", "url": "https://jobs.ashbyhq.com/suno/1"},
    ]
    db = MemoryDb()
    aid = _ready_agent(db, SPECIMEN_5d260731)
    db.add_job(aid, "first_edition")
    r = _runner(db, raw, score=_score_by_title({"Creative Director, Marketing": (70, "why", "pause")}))
    r.run()
    ed = db.editions[0]
    keys = [s["role_key"] for s in ed["payload"]["seats"]]
    check("E platforms role never eligible", not any("stripe" in k for k in keys))
    check("E suno seated", any("suno" in k for k in keys))
    check("E eligible count 1", ed["payload"]["counts"]["eligible"] == 1)


# ---------------------------------------------------------------------------
# V · verdicts
# ---------------------------------------------------------------------------

def _raw_two():
    return [
        {"title": "Creative Director, Marketing", "company": "Duolingo",
         "location": "London, England", "url": "https://careers.duolingo.com/jobs/1"},
        {"title": "Creative Director, Marketing", "company": "Duolingo",
         "location": "New York, NY", "url": "https://careers.duolingo.com/jobs/2"},
        {"title": "Creative Director, Marketing Campaigns", "company": "Suno",
         "location": "NYC", "url": "https://jobs.ashbyhq.com/suno/1"},
    ]


def test_v_exact_role_key_excludes_one_posting():
    raw = _raw_two()
    k_london = hr.role_key(raw[0])
    db = MemoryDb()
    aid = _ready_agent(db, SPECIMEN_5d260731)
    db.add_job(aid, "first_edition")
    score = _score_by_title({}, default=(70, "why", "pause"))
    _runner(db, raw, score=score, state=FakeState(excluded={k_london})).run()
    ed = db.editions[0]
    keys = [s["role_key"] for s in ed["payload"]["seats"]]
    check("V london excluded", k_london not in keys)
    check("V new york twin kept", hr.role_key(raw[1]) in keys)
    check("V excluded count 1", ed["payload"]["counts"]["excluded"] == 1)
    check("V legacy hits 0", ed["payload"]["counts"]["legacy_hits"] == 0)


def test_v_legacy_key_compatibility_only():
    ja = hr._import_job_alerts_adapters()
    raw = _raw_two()
    legacy = ja.dedup_key("Creative Director, Marketing", "Duolingo")
    check("V legacy key shape recognised", not hr.is_role_key(legacy))
    check("V role key shape recognised", hr.is_role_key(hr.role_key(raw[0])))
    db = MemoryDb()
    aid = _ready_agent(db, SPECIMEN_5d260731)
    db.add_job(aid, "first_edition")
    score = _score_by_title({}, default=(70, "why", "pause"))
    _runner(db, raw, score=score, state=FakeState(excluded={legacy})).run()
    ed = db.editions[0]
    keys = [s["role_key"] for s in ed["payload"]["seats"]]
    check("V legacy removes both twins (known legacy weakness, compatibility only)",
          not any("duolingo" in k for k in keys))
    check("V suno kept", any("suno" in k for k in keys))
    check("V legacy hits counted", ed["payload"]["counts"]["legacy_hits"] == 2)


def test_v_url_key_never_falls_back_to_legacy():
    ja = hr._import_job_alerts_adapters()
    raw = _raw_two()
    # A url: key for a DIFFERENT posting must not match this one via title|company.
    other = "url:https://careers.duolingo.com/jobs/999"
    exact, legacy = hr.split_verdict_keys({other})
    check("V url key is exact-only", exact == {other} and legacy == set())
    check("V no match by legacy path",
          not hr.verdict_matches(ja, raw[0], exact, legacy))


def test_v_reconsider_forces_full_read():
    raw = _raw_two()
    k_suno = hr.role_key(raw[2])
    db = MemoryDb()
    aid = _ready_agent(db, SPECIMEN_5d260731)
    db.add_job(aid, "first_edition")
    read = []

    def score(_a, _p, job, _jd):
        read.append(job["title"])
        return (40, "why", "pause")   # below floor: refused

    _runner(db, raw, score=score, state=FakeState(second_look={k_suno}), read_budget=1).run()
    ed = db.editions[0]
    check("V second look read despite budget 1", "Creative Director, Marketing Campaigns" in read)
    check("V second_look counted", ed["payload"]["counts"]["second_look"] == 1)
    refused = [r for r in ed["payload"]["refused"] if r["role_key"] == k_suno]
    check("V second look refused with reason and relook flag",
          refused and refused[0]["relook"] is True and refused[0]["pause"] == "pause")


def test_v_engine_writes_no_legacy_keys():
    raw = _raw_two()
    db = MemoryDb()
    aid = _ready_agent(db, SPECIMEN_5d260731)
    db.add_job(aid, "first_edition")
    _runner(db, raw, score=_score_by_title({}, default=(70, "w", "p")), read_budget=2).run()
    ed = db.editions[0]
    p = ed["payload"]
    all_keys = ([s["role_key"] for s in p["seats"]] + [r["role_key"] for r in p["refused"]]
                + list(p["refused_shown"]) + list(p["unread"]))
    check("V every persisted key is role_key-shaped", all(hr.is_role_key(k) for k in all_keys), all_keys)
    html_ids = re.findall(r'data-id="([^"]+)"', ed["html"])
    check("V html data-id keys role_key-shaped", all(hr.is_role_key(k) for k in html_ids), html_ids)


# ---------------------------------------------------------------------------
# B · read budget isolation
# ---------------------------------------------------------------------------

def _budget_probe(read_budget=None):
    ja = hr._import_job_alerts_adapters()
    matches = [{"title": f"Creative Director {i}", "company": f"C{i}", "location": "Remote",
                "url": f"https://x.invalid/{i}", "posted_at": None} for i in range(60)]
    reads = []

    def score(_a, _p, job, _jd):
        reads.append(job["title"])
        return (70, "w", "p")

    agent = ja.load_agent_config("001")
    with hr._judgment_hooks(ja, fetch_jd=lambda _u: "", score=score, profile="p"):
        kwargs = {} if read_budget is None else {"read_budget": read_budget}
        ranked, used = ja.rank_with_fit(agent, matches, set(), set(), **kwargs)
    return len(reads), used


def test_b_read_budget_isolation():
    ja = hr._import_job_alerts_adapters()
    check("B public constant is 25", ja.MAX_CANDIDATES_TO_SCORE == 25)
    n_default, used = _budget_probe()
    check("B default reads exactly the public constant", n_default == 25 and used, n_default)
    n_private, _ = _budget_probe(hr.PRIVATE_READ_BUDGET)
    check("B private budget reads 40", n_private == 40, n_private)
    check("B PRIVATE_READ_BUDGET fixed at 40 for stage 1", hr.PRIVATE_READ_BUDGET == 40)
    src = open(ja.__file__, encoding="utf-8").read()
    body_calls = re.findall(r"(?<!def )rank_with_fit\([^)]*\)", src)
    check("B exactly one public call site", len(body_calls) == 1, body_calls)
    check("B public path never passes read_budget", not any("read_budget" in c for c in body_calls), body_calls)
    check("B public path never passes key_fn", not any("key_fn" in c for c in body_calls), body_calls)


# ---------------------------------------------------------------------------
# S · seat_edition equivalence with the pre-lift logic
# ---------------------------------------------------------------------------

def _reference_seating(agent, ranked_all, used_ai, second_look, dedup_key):
    """The build_shortlist seating block as it stood at main@1180b02."""
    FOOUND_FLOOR = 60
    if used_ai:
        cleared = [j for j in ranked_all if (j.get("fit") or 0) >= FOOUND_FLOOR]
    else:
        cleared = list(ranked_all)
    ranked = cleared[:11]
    for j in cleared[11:]:
        if j["company"] in agent.priority_companies and j not in ranked:
            for k in range(len(ranked) - 1, -1, -1):
                if ranked[k]["company"] not in agent.priority_companies:
                    ranked[k] = j
                    break
    ranked.sort(key=lambda j: (j.get("fit") or -1), reverse=True)
    n = len(ranked)
    seen_pass = set()
    shown_ids = {id(j) for j in ranked}
    rejects = []
    for j in ranked_all:
        if id(j) in shown_ids:
            continue
        k = (j["company"], j["title"])
        if k in seen_pass:
            continue
        seen_pass.add(k)
        rejects.append(j)
    shown = []
    if n > 0 and rejects:
        with_reason = [j for j in rejects if j.get("ai_pause")]
        relooked = [j for j in with_reason if dedup_key(j["title"], j["company"]) in second_look]
        others = [j for j in with_reason if j not in relooked]
        shown = (relooked + others)[:max(5, len(relooked))]
    return ranked, rejects, shown


def test_s_seat_edition_equivalence():
    ja = hr._import_job_alerts_adapters()
    import random
    rng = random.Random(7)
    agent = ja.load_agent_config("001")   # priority_companies = {"Apple"}
    for trial in range(40):
        n = rng.randint(0, 30)
        ranked_all = []
        for i in range(n):
            co = rng.choice(["Apple", "Suno", "Adobe", "Duolingo", "Harvey", "Stripe"])
            ranked_all.append({"title": f"T{rng.randint(0, 6)}", "company": co,
                               "fit": rng.choice([None, 42, 55, 60, 64, 70, 78, 82, 90]),
                               "ai_pause": rng.choice(["", "p"])})
        ranked_all.sort(key=lambda j: (j.get("fit") or -1), reverse=True)
        used_ai = rng.random() < 0.8
        second_look = set()
        if ranked_all and rng.random() < 0.5:
            j = rng.choice(ranked_all)
            second_look.add(ja.dedup_key(j["title"], j["company"]))
        ref = _reference_seating(agent, ranked_all, used_ai, second_look, ja.dedup_key)
        got = ja.seat_edition(agent, ranked_all, used_ai, second_look)
        same = ([id(x) for x in ref[0]] == [id(x) for x in got["ranked"]]
                and [id(x) for x in ref[1]] == [id(x) for x in got["rejects"]]
                and [id(x) for x in ref[2]] == [id(x) for x in got["shown"]])
        if not same:
            raise AssertionError(f"FAIL S trial {trial}: seating differs")
    check("S seat_edition ≡ pre-lift logic over 40 random boards", True)


# ---------------------------------------------------------------------------
# L · ledger
# ---------------------------------------------------------------------------

def test_l_ledger_complete_refusals_five_shown_unread():
    raw = [{"title": f"Creative Director {i:02d}", "company": f"Co{i:02d}", "location": "Remote",
            "url": f"https://x.invalid/{i}"} for i in range(20)]
    fits = {f"Creative Director {i:02d}": (90 - i * 3, "w", "p") for i in range(20)}   # 90..33
    db = MemoryDb()
    content = dict(STRUCTURED); content["seat_cap"] = 6
    aid = _ready_agent(db, content)
    db.add_job(aid, "first_edition")
    _runner(db, raw, score=_score_by_title(fits), read_budget=14).run()
    p = db.editions[0]["payload"]
    c = p["counts"]
    check("L eligible 20", c["eligible"] == 20, c)
    check("L read 14", c["read"] == 14, c)
    check("L unread 6", c["unread"] == 6 and len(p["unread"]) == 6, c)
    check("L seated 6 (cap)", c["seated"] == 6 and len(p["seats"]) == 6, c)
    check("L refused = judged − seated = 8", c["refused"] == 8 and len(p["refused"]) == 8, c)
    check("L shown ≤ 5", len(p["refused_shown"]) == 5, p["refused_shown"])
    check("L shown ⊆ refused", set(p["refused_shown"]) <= {r["role_key"] for r in p["refused"]})
    check("L every refusal has a reason", all(r["pause"] for r in p["refused"]))
    check("L unread ∩ refused = ∅", not (set(p["unread"]) & {r["role_key"] for r in p["refused"]}))
    check("L below-floor refusals in ledger",
          any((r["fit"] or 0) < 60 for r in p["refused"]))
    check("L above-floor-beyond-cap refusals in ledger",
          any((r["fit"] or 0) >= 60 for r in p["refused"]))
    check("L read_budget recorded", p["read_budget"] == 14)
    check("L engine ai", p["engine"] == "ai")
    html_doc = db.editions[0]["html"]
    check("L html shows exactly five refusals", html_doc.count('class="pitem') == 5)
    check("L html passintro total = 8", "8 more read in full and declined" in html_doc)


# ---------------------------------------------------------------------------
# F · fail closed
# ---------------------------------------------------------------------------

def test_f_non_001_refused_before_any_call():
    db = MemoryDb()
    aid = _ready_agent(db, FICTIONAL_OTHER_ROLES, agent_no=2)
    jid = db.add_job(aid, "first_edition")
    calls = {"collect": 0, "score": 0, "jd": 0}

    def collect(_c):
        calls["collect"] += 1
        return [{"title": "Design Director", "company": "X", "location": "Berlin", "url": "https://x/1"}]

    def score(*_a, **_k):
        calls["score"] += 1
        return (70, "w", "p")

    def jd(_u):
        calls["jd"] += 1
        return ""

    r = hr.Runner(db, collector=collect, today=FROZEN_TODAY, fetch_jd=jd, score=score,
                  profile=None, state_loader=lambda _a, _n: None)
    reports = r.run()
    check("F job failed", db.jobs[jid]["status"] == "failed")
    check("F error no_candidate_context", db.jobs[jid]["error"] == "no_candidate_context")
    check("F no edition row", db.editions == [])
    check("F zero adapter calls", calls["collect"] == 0)
    check("F zero score calls", calls["score"] == 0)
    check("F zero jd fetches", calls["jd"] == 0)
    check("F report action failed", reports[0].action == "failed")


def test_f_001_resolves_to_profile_md():
    ja = hr._import_job_alerts_adapters()
    asked = []
    real = ja.load_agent_config

    def watch(key, *a, **k):
        asked.append(key)
        return real(key, *a, **k)

    ja.load_agent_config = watch
    try:
        ctx = hr.candidate_context(ja, "some-uuid", 1)
    finally:
        ja.load_agent_config = real
    check("F №001 asked for 001", "001" in asked, asked)
    check("F №001 interim is profile.md", ctx.kind == "profile.md" and "Candidate Profile" in ctx.text and ctx.hash)
    check("F №001 evidence map carried", len(ctx.evidence_map) >= 1)
    ctx2 = hr.candidate_context(ja, "other-uuid", 2)
    check("F №002 has no context without confirmed memory", ctx2.kind == "" and ctx2.text == "")
    rows = [{"id": "m1", "layer": "record", "statement": "Led design at Acme.", "provenance": "confirmed",
             "status": "active", "source": "resume.pdf", "created_at": "2026-08-01"}]
    ctx3 = hr.candidate_context(ja, "other-uuid", 2, memory_rows=rows, brief_content=STRUCTURED)
    check("F №002 with confirmed memory has a memory context", ctx3.kind == "memory" and "Led design at Acme." in ctx3.text
          and ctx3.statements == 1 and ctx3.base is None and ctx3.evidence_map == [])
    ctx4 = hr.candidate_context(ja, "some-uuid", 1, memory_rows=rows, brief_content=STRUCTURED)
    check("F №001 with confirmed memory: Memory wins over profile.md", ctx4.kind == "memory" and "Candidate Profile" not in ctx4.text
          and ctx4.name == "Carlos" and len(ctx4.evidence_map) >= 1)


def test_f_non_001_never_touches_profile_md():
    """Even an authority compile for a non-001 agent must not open profile.md."""
    import builtins
    opened = []
    real_open = open

    def guard(path, *a, **k):
        if "profile.md" in str(path):
            opened.append(str(path))
        return real_open(path, *a, **k)

    db = MemoryDb()
    aid = _ready_agent(db, FICTIONAL_OTHER_ROLES, agent_no=2)
    db.add_job(aid, "first_edition")
    builtins.open = guard
    try:
        hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY,
                  state_loader=lambda _a, _n: None).run()
    finally:
        builtins.open = real_open
    check("F profile.md never opened for №002", opened == [], opened)


# ---------------------------------------------------------------------------
# R · runner
# ---------------------------------------------------------------------------

def test_r_h3_empty_edition_is_success():
    db = MemoryDb()
    aid = _ready_agent(db)
    jid = db.add_job(aid, "first_edition", payload={"brief_version": 1})
    reports = _runner(db, []).run()
    check("R3 action edition", reports[0].action == "edition")
    check("R3 seats 0", reports[0].seats == 0)
    check("R3 job done", db.jobs[jid]["status"] == "done")
    check("R3 no job.error", db.jobs[jid].get("error") is None)
    ed = db.editions[0]
    check("R3 outcome empty", ed["outcome"] == "empty")
    check("R3 payload seats empty", ed["payload"]["seats"] == [])
    check("R3 html empty marker", 'data-edition="empty"' in ed["html"])
    check("R3 html seat-count 0", 'data-seat-count="0"' in ed["html"])
    check("R3 no DUMMY ROLE", "DUMMY ROLE" not in ed["html"])
    check("R3 no dummy seats in json", _seats_json(ed["html"]) == [])
    check("R3 honest cascade", "Nothing cleared the bar today." in ed["html"])
    check("R3 counts present", ed["payload"]["counts"]["eligible"] == 0)


def test_r_h4_same_day_second_is_noop():
    db = MemoryDb()
    aid = _ready_agent(db)
    j1 = db.add_job(aid, "first_edition")
    r = _runner(db, [])
    r.run()
    html1 = db.editions[0]["html"]
    payload1 = json.dumps(db.editions[0]["payload"], sort_keys=True)
    j2 = db.add_job(aid, "first_edition")
    reports = r.run()
    check("R4 noop", reports[0].action == "noop")
    check("R4 second job done", db.jobs[j2]["status"] == "done")
    check("R4 still one edition", len(db.editions) == 1)
    check("R4 html unchanged", db.editions[0]["html"] == html1)
    check("R4 payload unchanged", json.dumps(db.editions[0]["payload"], sort_keys=True) == payload1)
    check("R4 first still done", db.jobs[j1]["status"] == "done")


def test_r_h5_market_history_fields():
    db = MemoryDb()
    aid = _ready_agent(db, STRUCTURED)
    raw = [{"title": "Creative Director", "company": "Acme", "location": "Remote",
            "url": "https://example.invalid/role", "source": "adapter"}]
    prior_key = hr.role_key(raw[0])
    db.editions.append({"agent_id": aid, "edition_date": "2026-08-01",
                        "payload": {"seats": [{"role_key": prior_key, "first_seen": "2026-07-15"}]},
                        "html": "<html></html>", "outcome": "seats"})
    db.add_job(aid, "first_edition")
    reports = _runner(db, raw, score=_score_by_title({"Creative Director": (72, "w", "p")})).run()
    check("R5 edition", reports[0].action == "edition")
    check("R5 one seat", reports[0].seats == 1)
    today_ed = [e for e in db.editions if e["edition_date"] == FROZEN_TODAY.isoformat()][0]
    payload = today_ed["payload"]
    check("R5 engine_sha", "engine_sha" in payload)
    check("R5 compiled_config_hash", len(payload.get("compiled_config_hash") or "") == 64)
    seat = payload["seats"][0]
    for key in ("role_key", "first_seen", "previously_seen", "source", "new_or_resurfaced",
                "survived_because", "fit", "tier", "ai_why", "ai_pause", "why_now", "lead", "seclabel"):
        check(f"R5 field {key}", key in seat)
    check("R5 role_key url precedence", seat["role_key"] == prior_key and seat["role_key"].startswith("url:"))
    check("R5 previously_seen", seat["previously_seen"] is True)
    check("R5 first_seen from prior", seat["first_seen"] == "2026-07-15")
    check("R5 resurfaced", seat["new_or_resurfaced"] == "resurfaced")
    check("R5 why_now for resurfaced has no 'first time'",
          "Surfaced for the first time" not in seat["why_now"] and "still open" in seat["why_now"].lower())
    check("R5 tier from job_alerts", seat["tier"] == "Worth considering")
    check("R5 lead label", seat["lead"] is True and seat["seclabel"] == "I'd start with Acme")
    check("R5 edition number 2", "Edition 002" in today_ed["html"])


def test_r_new_seat_history():
    db = MemoryDb()
    aid = _ready_agent(db, STRUCTURED)
    raw = [{"title": "Creative Director", "company": "Nova", "location": "Remote", "url": ""}]
    db.add_job(aid, "first_edition")
    _runner(db, raw, score=_score_by_title({"Creative Director": (66, "w", "p")})).run()
    seat = db.editions[0]["payload"]["seats"][0]
    check("R5b new", seat["new_or_resurfaced"] == "new")
    check("R5b not previously", seat["previously_seen"] is False)
    check("R5b first_seen today", seat["first_seen"] == FROZEN_TODAY.isoformat())
    check("R5b tcl key when no url", seat["role_key"].startswith("tcl:"))
    check("R5b why_now first time", "Surfaced for the first time this morning" in seat["why_now"])
    check("R5b NEW tag in html", '<span class="new">NEW</span>' in db.editions[0]["html"])


def test_r_first_edition_fail_closed():
    db = MemoryDb()
    aid = str(uuid.uuid4())
    db.add_brief(aid, COMPLETE, compiled_config=None, readiness=None)
    jid = db.add_job(aid, "first_edition")
    _runner(db, []).run()
    # v1.2: no stored compiled_config is not a failure. The hunt compiles
    # the active Brief.content itself; the stored column is a receipt.
    check("R no stored config still hunts", db.jobs[jid]["status"] == "done"
          and db.jobs[jid].get("error") in (None, ""), db.jobs[jid])
    check("R fresh compile edition written", len(db.editions) == 1
          and db.editions[0]["payload"]["authority"]["compiled_at_hunt"] is True)
    db.editions.clear()
    aid2 = str(uuid.uuid4())
    compiled = hr.compile_from_content(INCOMPLETE)
    db.add_brief(aid2, INCOMPLETE, compiled_config=hr.persistable_compiled(compiled), readiness="not_ready")
    jid2 = db.add_job(aid2, "first_edition")
    _runner(db, []).run()
    check("R readiness_blocked", db.jobs[jid2]["error"] == "readiness_blocked")
    aid3 = str(uuid.uuid4())
    jid3 = db.add_job(aid3, "first_edition")
    _runner(db, []).run()
    check("R no_active_brief", db.jobs[jid3]["error"] == "no_active_brief")
    aid4 = _ready_agent(db)
    jid4 = db.add_job(aid4, "first_edition")

    def boom(_c):
        raise RuntimeError("adapter down")

    hr.Runner(db, collector=boom, today=FROZEN_TODAY, profile=FIXTURE_PROFILE,
              state_loader=lambda _a, _n: None).run()
    check("R hunt_adapter_failed", db.jobs[jid4]["error"] == "hunt_adapter_failed")


def test_r_seat_cap_from_brief():
    content = dict(STRUCTURED); content["seat_cap"] = 2
    raw = [{"title": f"Creative Director {c}", "company": c, "location": "Remote", "url": f"https://x/{c}"}
           for c in ("A", "B", "C")]
    db = MemoryDb()
    aid = _ready_agent(db, content)
    db.add_job(aid, "first_edition")
    _runner(db, raw, score=_score_by_title({}, default=(70, "w", "p"))).run()
    p = db.editions[0]["payload"]
    check("R cap 2 seats", len(p["seats"]) == 2)
    check("R third in refusal ledger", len(p["refused"]) == 1)


def test_r_edition_html_contract():
    """What the At Work bind parses today, plus what Move 3 renders."""
    raw = [
        {"title": "Head of Brand, Adobe Creative", "company": "Adobe", "location": "San Francisco",
         "url": "https://adobe.wd5.myworkdayjobs.com/x/1", "posted_at": datetime(2026, 8, 20, tzinfo=timezone.utc)},
        {"title": "Creative Director, Marketing Campaigns", "company": "Suno", "location": "NYC",
         "url": "https://jobs.ashbyhq.com/suno/1"},
        {"title": "Creative Director, Copy", "company": "Stripe", "location": "US",
         "url": "https://stripe.com/jobs/1"},
    ]
    fits = {"Head of Brand, Adobe Creative": (82, "Adobe is rebuilding its brand.", "Scope reads narrower."),
            "Creative Director, Marketing Campaigns": (70, "Suno from zero.", "Posting is thin."),
            "Creative Director, Copy": (48, "Copy-led.", "Writer-led remit.")}
    db = MemoryDb()
    aid = _ready_agent(db, SPECIMEN_5d260731, agent_no=1)
    db.add_job(aid, "first_edition")
    deep = lambda job, _p, **_kw: {"role": "New seat", "moment": "Rebrand", "leadership": "CMO",
                            "signal": "Hiring", "question": "Scope", "verdict": "Still 82.", "fit_after": 80}
    brief_args = []

    def brief_fn(n, total_fetched, n_companies, ranked, new_keys, **_kw):
        brief_args.append((n, total_fetched, n_companies, [j["company"] for j in ranked], set(new_keys)))
        return "Adobe leads clear of the field."

    r = hr.Runner(db, collector=lambda _c: raw, today=FROZEN_TODAY, fetch_jd=lambda _u: "",
                  score=_score_by_title(fits), profile=FIXTURE_PROFILE, deep=deep,
                  brief_line_fn=brief_fn, state_loader=lambda _a, _n: None)
    r.run()
    ed = db.editions[0]
    ja = hr._import_job_alerts_adapters()
    check("R write_brief gets Shortlist-shaped new keys",
          brief_args and brief_args[0][4] == {ja.dedup_key(j["title"], j["company"]) for j in raw[:2]},
          brief_args)
    check("R write_brief facts n=2 fetched=3", brief_args[0][0] == 2 and brief_args[0][1] == 3)
    h = ed["html"]
    pic = _seats_json(h)
    check("R html seats json shape", [set(s) for s in pic] == [{"id", "handle", "line"}] * 2)
    check("R html fit order Adobe then Suno", [s["handle"] for s in pic] == ["Adobe", "Suno"])
    k_adobe = hr.role_key(raw[0])
    check("R html data-id = role_key", f'data-id="{k_adobe}"' in h)
    for attr in ("data-company", "data-title", "data-location", "data-url", "data-fit=\"82\"",
                 "data-posted-at", "data-why", "data-pause", "data-why-now"):
        check(f"R html {attr}", attr in h)
    check("R html anno", "{fit&nbsp;82}" in h)
    check("R html scoreline number · tier", '<div class="scoreline">82 &middot; Strong fit</div>' in h)
    check("R html role", '<div class="role">Head of Brand, Adobe Creative' in h)
    for lab in ("Why I chose it", "What gives me pause", "Why now", "I kept looking"):
        check(f"R html plabel {lab}", f'<div class="plabel">{lab}</div>' in h)
    check("R html meta", '<div class="meta"><b>San Francisco</b>' in h and "posted Aug 20" in h)
    check("R html apply", 'class="apply" href="https://adobe.wd5.myworkdayjobs.com/x/1"' in h)
    check("R html greeting", '<p class="brief">Good ' in h)
    check("R html cascade", "I searched 3 jobs overnight." in h and "FOOUND 2 for you." in h)
    check("R html statline", "3 read in full &middot; everything else dismissed on sight. Adobe leads clear of the field." in h)
    check("R html lead seclabel", "I&rsquo;d start with Adobe" in h)
    check("R html refusals", 'Found, not FOOUND' in h and "1 more read in full and declined" in h
          and 'class="pitem' in h and "Writer-led remit." in h)
    check("R html colophon", "FOOUND AT WORK &middot; Edition 001" in h and "companies watched" in h)
    check("R html no DUMMY ROLE", "DUMMY ROLE" not in h)
    p = ed["payload"]
    check("R payload deep kept", p["deep"]["verdict"] == "Still 82.")
    check("R payload brief_line", p["brief_line"] == "Adobe leads clear of the field.")
    check("R payload counts", {k: v for k, v in p["counts"].items() if k != "sources"} == {
        "market_fetched": 3, "eligible": 3, "excluded": 0,
        "second_look": 0, "legacy_hits": 0, "read": 3,
        "unread": 0, "seated": 2, "refused": 1,
        "model_reads_attempted": 3, "model_reads_failed": 0, "model_reads_remembered": 0}, p["counts"])
    check("R payload sources (Move 3): the universe is recorded with the edition",
          p["counts"].get("sources") == p["sources"]["selected"] > 0
          and p["sources"]["founding"] == p["sources"]["founding_total"]
          and "regions" in p["sources"], p.get("sources"))
    check("R fit_after not applied (deferred)", p["seats"][0]["fit"] == 82)


def test_r_heuristic_day_is_degraded_not_broken():
    raw = [{"title": "Creative Director A", "company": "A", "location": "Remote", "url": "https://x/a"},
           {"title": "Creative Director B", "company": "B", "location": "Remote", "url": "https://x/b"}]
    db = MemoryDb()
    aid = _ready_agent(db, STRUCTURED)
    db.add_job(aid, "first_edition")
    ja = hr._import_job_alerts_adapters()
    saved = ja.ANTHROPIC_KEY
    ja.ANTHROPIC_KEY = ""
    try:
        hr.Runner(db, collector=lambda _c: raw, today=FROZEN_TODAY, fetch_jd=lambda _u: "",
                  profile=FIXTURE_PROFILE, state_loader=lambda _a, _n: None).run()
    finally:
        ja.ANTHROPIC_KEY = saved
    p = db.editions[0]["payload"]
    check("R heuristic engine recorded", p["engine"] == "heuristic")
    check("R heuristic seats present, no fit", len(p["seats"]) == 2 and all(s["fit"] is None for s in p["seats"]))
    check("R heuristic no refusals", p["refused"] == [])
    check("R heuristic no scoreline", "scoreline" not in db.editions[0]["html"])


def test_r_operator_line_and_stdout_hygiene():
    raw = [{"title": "Creative Director SECRET", "company": "HiddenCo", "location": "Remote",
            "url": "https://secret.example/job"}]
    db = MemoryDb()
    aid = _ready_agent(db, STRUCTURED, agent_no=9)
    db.add_job(aid, "first_edition")
    import logging
    buf = []

    class H(logging.Handler):
        def emit(self, record):
            buf.append(self.format(record))

    h = H(); h.setFormatter(logging.Formatter("%(message)s"))
    hr.log.addHandler(h); hr.log.setLevel(logging.INFO)
    out = io.StringIO()

    def score(_a, _p, job, _jd):
        print("MODEL SAYS " + job["title"])      # must never reach stdout
        return (70, "why SECRET", "pause")

    try:
        with contextlib.redirect_stdout(out):
            _runner(db, raw, score=score).run()
    finally:
        hr.log.removeHandler(h)
    blob = "\n".join(buf) + "\n" + out.getvalue()
    check("hygiene operator line printed", "[operator] agent=№009" in out.getvalue())
    check("hygiene no seat title", "SECRET" not in blob)
    check("hygiene no company", "HiddenCo" not in blob)
    check("hygiene no url", "secret.example" not in blob)
    check("hygiene no model stdout", "MODEL SAYS" not in blob)
    check("hygiene no brief copy", "culture-shaping" not in blob)
    check("hygiene source has no log.exception", "log.exception" not in open(hr.__file__, encoding="utf-8").read())


def test_r_adapter_stdout_silenced():
    def noisy(*_a, **_k):
        print("ADAPTER PRINTS Secret Title at HiddenCo")
        return [{"title": "Creative Director", "company": "HiddenCo", "location": "Remote", "url": "https://h/1"}]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        raw = hr.live_collect({"search_queries": ["creative director"]}, scraper_entries=[("Stub", noisy)])
    check("adapter row collected", len(raw) == 1)
    check("adapter stdout swallowed", "ADAPTER PRINTS" not in out.getvalue())


def test_r_dry_run_writes_nothing():
    raw = [{"title": "Creative Director", "company": "Acme", "location": "Remote", "url": "https://x/1"}]
    db = MemoryDb()
    aid = _ready_agent(db, STRUCTURED, agent_no=1)
    r = hr.Runner(db, collector=lambda _c: raw, today=FROZEN_TODAY, fetch_jd=lambda _u: "",
                  score=_score_by_title({"Creative Director": (75, "w", "p")}), profile=FIXTURE_PROFILE,
                  state_loader=lambda _a, _n: None)
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = r.dry_run(aid, path)
    check("dry-run no edition row", db.editions == [])
    check("dry-run no jobs", db.jobs == {})
    check("dry-run result seats", len(result["seats"]) == 1)
    fx = json.load(open(path, encoding="utf-8"))
    check("dry-run fixture rows", fx["rows"][0]["status"] == "seated" and fx["rows"][0]["fit"] == 75)
    check("dry-run fixture compiled authority", "include" in fx["compiled"] and "accepted_locations" in fx["compiled"])
    check("dry-run console counts only", "Acme" not in out.getvalue() and "[operator]" in out.getvalue())
    os.unlink(path)


def test_r_h8_html_seat_shape_minimal():
    html_doc = hr.render_edition_html([{
        "role_key": "url:https://a/1", "handle": "Acme", "line": "Creative Director — Remote",
        "title": "Creative Director", "company": "Acme", "location": "Remote",
        "ai_why": "A reason.", "ai_pause": "", "why_now": "Still open as of 8:00 AM ET",
    }])
    check("H8 no dummy", "DUMMY ROLE" not in html_doc)
    pic = _seats_json(html_doc)
    check("H8 id/handle/line", set(pic[0]) == {"id", "handle", "line"})
    check("H8 omit empty pause block", '<div class="plabel">What gives me pause</div>' not in html_doc)
    check("H8 why + now present", '<div class="plabel">Why I chose it</div>' in html_doc
          and '<div class="plabel">Why now</div>' in html_doc)
    check("H8 no scoreline without fit", "scoreline" not in html_doc)
    empty = hr.render_edition_html([])
    check("H8 empty array", _seats_json(empty) == [] and "Nothing today." in empty)


# ---------------------------------------------------------------------------
# H9 · boundaries
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# A — v1.2: authority is compiled at hunt time; engine_reason; voice/clock
# ---------------------------------------------------------------------------

JUNK_STORED_CONFIG = {
    # The retired compiler's shape: raw phrases, leaky include, cap 5.
    "include": ["platforms", "consumer tech", "culture-shaping brands"],
    "accepted_locations": ["nyc", "california", "major us hubs"],
    "exclude_type": [],
    "seat_cap": 5,
    "readiness_reasons": [],
}


def test_a_stale_stored_config_is_never_authority():
    """Stored compiled_config is a receipt. The hunt compiles Brief.content."""
    raw = [
        {"title": "Creative Director", "company": "Apple", "location": "Culver City",
         "url": "https://apple/1"},
        {"title": "Creative Director", "company": "Duolingo", "location": "New York, NY",
         "url": "https://duo/1"},
        {"title": "Solutions Architect, Platforms", "company": "Stripe", "location": "NYC",
         "url": "https://stripe/1"},
    ]
    # (a) COMPLETE content + junk stored config + stored readiness "ready":
    #     the junk must not shape eligibility.
    db = MemoryDb()
    aid = str(uuid.uuid4())
    db.add_brief(aid, COMPLETE, compiled_config=dict(JUNK_STORED_CONFIG), readiness="ready", version=3)
    db.add_job(aid, "first_edition")
    seen_cfg = {}

    def collector(cfg):
        seen_cfg.update(cfg)
        return raw

    hr.Runner(db, collector=collector, today=FROZEN_TODAY, fetch_jd=lambda _u: "",
              score=_score_by_title({}, default=(70, "w", "p")), profile=FIXTURE_PROFILE,
              state_loader=lambda _a, _n: None).run()
    fresh = hr.compile_from_content(COMPLETE)
    p = db.editions[0]["payload"]
    check("A collector saw fresh compilation, not junk", "platforms" not in (seen_cfg.get("include") or [])
          and seen_cfg.get("families") == fresh["families"])
    companies = sorted(s["company"] for s in p["seats"])
    check("A Culver City + New York, NY eligible under fresh compile", companies == ["Apple", "Duolingo"], companies)
    check("A platforms role never eligible", p["counts"]["eligible"] == 2 and p["refused"] == [])
    a = p["authority"]
    check("A authority fingerprints recorded", a["compiled_at_hunt"] is True
          and a["brief_content_hash"] == hr.brief_content_hash(COMPLETE)
          and a["compiled_config_hash"] == hr.compiled_config_hash(fresh)
          and a["brief_version"] == 3 and a["stored_readiness"] == "ready" and a["hunt_readiness"] == "ready"
          and len(a["brief_content_hash"]) == 64, a)
    check("A compiled hash differs from junk", hr.compiled_config_hash(dict(JUNK_STORED_CONFIG)) != a["compiled_config_hash"])
    check("A stored column untouched by the hunt", db.briefs[next(iter(db.briefs))]["compiled_config"] == JUNK_STORED_CONFIG)

    # (b) INCOMPLETE content + a "ready"-looking stored config: fail closed on
    #     the fresh compilation, before any adapter or model call.
    db2 = MemoryDb()
    aid2 = str(uuid.uuid4())
    good = hr.persistable_compiled(hr.compile_from_content(COMPLETE))
    db2.add_brief(aid2, INCOMPLETE, compiled_config=good, readiness="ready")
    jid2 = db2.add_job(aid2, "first_edition")
    calls = {"collector": 0, "score": 0}

    def boom_collector(_c):
        calls["collector"] += 1
        return raw

    def boom_score(*_a):
        calls["score"] += 1
        return (70, "w", "p")

    hr.Runner(db2, collector=boom_collector, today=FROZEN_TODAY, fetch_jd=lambda _u: "",
              score=boom_score, profile=FIXTURE_PROFILE, state_loader=lambda _a, _n: None).run()
    check("A stored 'ready' cannot unblock incomplete content", db2.jobs[jid2]["error"] == "readiness_blocked")
    check("A blocked before collection and judgment", calls == {"collector": 0, "score": 0} and db2.editions == [])

    # (c) dry run: same rule, fixture carries the authority.
    import tempfile, os
    db3 = MemoryDb()
    aid3 = str(uuid.uuid4())
    db3.add_brief(aid3, COMPLETE, compiled_config=dict(JUNK_STORED_CONFIG), readiness="ready", version=2)
    db3.agent_numbers[aid3] = 1
    r = hr.Runner(db3, collector=lambda _c: raw, today=FROZEN_TODAY, fetch_jd=lambda _u: "",
                  score=_score_by_title({}, default=(70, "w", "p")), profile=FIXTURE_PROFILE,
                  state_loader=lambda _a, _n: None)
    fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        r.dry_run(aid3, path)
    fx = json.load(open(path, encoding="utf-8")); os.unlink(path)
    check("A dry-run fixture compiled is fresh", "platforms" not in fx["compiled"]["include"]
          and fx["compiled"]["families"] == fresh["families"])
    check("A dry-run fixture authority + reason", fx["authority"]["compiled_at_hunt"] is True
          and fx["authority"]["brief_version"] == 2 and fx["engine_reason"] == "ai")
    check("A dry-run operator line fingerprints",
          f"brief={hr.brief_content_hash(COMPLETE)[:8]}" in out.getvalue()
          and f"compile={hr.compiled_config_hash(fresh)[:8]}" in out.getvalue()
          and " v=2" in out.getvalue() and "reason=ai" in out.getvalue(), out.getvalue())
    check("A dry-run wrote nothing", db3.editions == [] and db3.jobs == {}
          and db3.briefs[next(iter(db3.briefs))]["compiled_config"] == JUNK_STORED_CONFIG)


def test_a_engine_reason_enum():
    raw = [{"title": "Creative Director A", "company": "A", "location": "Remote", "url": "https://x/a"},
           {"title": "Creative Director B", "company": "B", "location": "Remote", "url": "https://x/b"}]
    ja = hr._import_job_alerts_adapters()
    check("A enum fixed", hr.ENGINE_REASONS == ("ai", "no_key", "authentication_failed",
                                                "all_model_reads_failed", "no_candidate_context"))

    def run(score, model_probe=None, key=None):
        db = MemoryDb()
        aid = _ready_agent(db, STRUCTURED, agent_no=7)
        db.add_job(aid, "first_edition")
        saved = ja.ANTHROPIC_KEY
        if key is not None:
            ja.ANTHROPIC_KEY = key
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                hr.Runner(db, collector=lambda _c: raw, today=FROZEN_TODAY, fetch_jd=lambda _u: "",
                          score=score, profile=FIXTURE_PROFILE, state_loader=lambda _a, _n: None,
                          model_probe=model_probe).run()
        finally:
            ja.ANTHROPIC_KEY = saved
        p = db.editions[0]["payload"]
        return p, out.getvalue()

    p, line = run(_score_by_title({}, default=(70, "w", "p")))
    check("A ai", p["engine"] == "ai" and p["engine_reason"] == "ai" and "reason=ai" in line)
    check("A ai counters", p["counts"]["model_reads_attempted"] == 2 and p["counts"]["model_reads_failed"] == 0)

    p, line = run(None, key="")
    check("A no_key", p["engine"] == "heuristic" and p["engine_reason"] == "no_key" and "reason=no_key" in line)
    check("A no_key attempted nothing", p["counts"]["model_reads_attempted"] == 0)

    probes = []

    def probe_auth():
        probes.append(1)
        return "authentication_failed"

    p, line = run(lambda *_a: (None, None, None), model_probe=probe_auth)
    check("A authentication_failed", p["engine"] == "heuristic" and p["engine_reason"] == "authentication_failed"
          and "reason=authentication_failed" in line and len(probes) == 1)
    check("A failed counters", p["counts"]["model_reads_attempted"] == 2 and p["counts"]["model_reads_failed"] == 2)

    p, line = run(lambda *_a: (None, None, None), model_probe=lambda: "all_model_reads_failed")
    check("A all_model_reads_failed", p["engine_reason"] == "all_model_reads_failed")

    p, _ = run(lambda *_a: (None, None, None), model_probe=lambda: "not an enum value")
    check("A probe answer outside enum collapses to all_model_reads_failed", p["engine_reason"] == "all_model_reads_failed")

    # Partial failure is still an AI day.
    flaky = {"n": 0}

    def half(_a, _p, job, _jd):
        flaky["n"] += 1
        return (70, "w", "p") if flaky["n"] == 1 else (None, None, None)

    p, _ = run(half)
    check("A one success = ai", p["engine"] == "ai" and p["engine_reason"] == "ai"
          and p["counts"]["model_reads_failed"] == 1)

    # The probe is never consulted when judgment succeeded or no key exists.
    called = []
    run(_score_by_title({}, default=(70, "w", "p")), model_probe=lambda: called.append(1) or "ai")
    run(None, model_probe=lambda: called.append(1) or "ai", key="")
    check("A probe not called on ai / no_key days", called == [])

    # No secrets or raw errors anywhere in the ledger.
    blob = json.dumps(p)
    check("A reason is enum only", all(v in hr.ENGINE_REASONS for v in [p["engine_reason"]])
          and "Traceback" not in blob and "x-api-key" not in blob)


def test_a_classify_model_failure_offline():
    class JA: ANTHROPIC_KEY = ""
    check("A classify no key", hr.classify_model_failure(JA()) == "no_key")

    class R:
        def __init__(self, code, msg=""):
            self.status_code = code; self._m = msg
        def json(self): return {"error": {"message": self._m}}

    import requests
    saved = requests.post
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url; seen["max_tokens"] = json["max_tokens"]
        return fake_post.resp

    requests.post = fake_post
    try:
        class JK: ANTHROPIC_KEY = "k"; CLAUDE_MODEL = "m"
        for code, msg, want in ((401, "", "authentication_failed"), (403, "", "authentication_failed"),
                                (400, "workspace header required", "authentication_failed"),
                                (400, "invalid request", "all_model_reads_failed"),
                                (500, "", "all_model_reads_failed"), (200, "", "all_model_reads_failed")):
            fake_post.resp = R(code, msg)
            check(f"A classify {code} {msg!r}", hr.classify_model_failure(JK()) == want)
        check("A classify probe is minimal", seen["max_tokens"] == 1 and "api.anthropic.com" in seen["url"])

        def raiser(*_a, **_k):
            raise ConnectionError("down")

        requests.post = raiser
        check("A classify network error", hr.classify_model_failure(JK()) == "all_model_reads_failed")
    finally:
        requests.post = saved


def test_a_voice_name_and_clock():
    raw = [{"title": "Creative Director", "company": "Acme", "location": "Remote", "url": "https://x/1"}]
    db = MemoryDb()
    aid = _ready_agent(db, STRUCTURED, agent_no=1)   # №001 → base name "Carlos", Eastern clock
    db.add_job(aid, "first_edition")
    _runner(db, raw, score=_score_by_title({}, default=(70, "w", "p"))).run()
    h = db.editions[0]["html"]
    check("A greeting carries the agent's name", ", Carlos.</p>" in h and 'class="brief">Good ' in h, h[:400])
    check("A old literal clock gone", "Compiled 8:00 AM ET" not in h)
    import re
    m = re.search(r"Compiled (\d{1,2}:\d{2} [AP]M E[DS]T)", h)
    check("A colophon shows an actual Eastern time", m is not None, h[h.find("Compiled"):h.find("Compiled") + 40])

    db2 = MemoryDb()
    aid2 = _ready_agent(db2, STRUCTURED, agent_no=9)
    db2.add_job(aid2, "first_edition")
    _runner(db2, raw, score=_score_by_title({}, default=(70, "w", "p"))).run()
    h2 = db2.editions[0]["html"]
    check("A non-001 without a name greets plainly", re.search(r'class="brief">Good (morning|afternoon|evening)\.</p>', h2) is not None)
    check("A non-001 clock is UTC", re.search(r"Compiled \d{1,2}:\d{2} [AP]M UTC", h2) is not None)

    from datetime import datetime as _dt
    t = _dt(2026, 9, 2, 17, 18, tzinfo=timezone.utc)
    check("A clock helper ET", hr.compiled_clock(t, 1) == "1:18 PM EDT")
    check("A clock helper UTC", hr.compiled_clock(t, 2) == "5:18 PM UTC")
    check("A daypart follows local clock", hr._daypart(hr.local_now(t, 1).hour) == "afternoon"
          and hr._daypart(hr.local_now(_dt(2026, 9, 2, 12, 30, tzinfo=timezone.utc), 1).hour) == "morning")


def test_a_fingerprints_are_deterministic():
    c1 = hr.brief_content_hash(COMPLETE)
    c2 = hr.brief_content_hash(json.loads(json.dumps(COMPLETE)))
    check("A brief hash stable across serialisation", c1 == c2 and len(c1) == 64)
    check("A brief hash accepts JSON string", hr.brief_content_hash(json.dumps(COMPLETE)) == c1)
    alt = dict(COMPLETE); alt["_note"] = "x"
    check("A brief hash changes with content", hr.brief_content_hash(alt) != c1)
    k1 = hr.compiled_config_hash(hr.compile_from_content(COMPLETE))
    k2 = hr.compiled_config_hash(hr.compile_from_content(COMPLETE))
    check("A compiled hash deterministic", k1 == k2)


# ---------------------------------------------------------------------------
# I — v1.3: the original intelligence survives the live path, and is observable
# ---------------------------------------------------------------------------

DEEP_STUB = {"role": "New seat, not a succession.", "moment": "Brand under construction.",
             "leadership": "Reports to the CMO.", "signal": "Two senior hires this quarter.",
             "question": "Scope of the team is unstated.", "fit_after": 86,
             "verdict": "My view changed: 85 to 86."}


def _live_run(raw, *, score_fit, deep_look, write_brief, key="k", agent_no=1, out=None):
    """Run first_edition on the LIVE path (Runner.score=None) with the original
    loop's module-level functions replaced by recording stubs. This is the
    only way to prove that deep_look / write_brief are reached for real:
    Runner(score=...) is the test seam, and the seam stubs them by design."""
    ja = hr._import_job_alerts_adapters()
    saved = {k: getattr(ja, k) for k in ("score_fit", "deep_look", "write_brief", "ANTHROPIC_KEY")}
    ja.score_fit, ja.deep_look, ja.write_brief, ja.ANTHROPIC_KEY = score_fit, deep_look, write_brief, key
    db = MemoryDb()
    aid = _ready_agent(db, STRUCTURED, agent_no=agent_no)
    db.add_job(aid, "first_edition")
    buf = out if out is not None else io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            hr.Runner(db, collector=lambda _c: raw, today=FROZEN_TODAY, fetch_jd=lambda _u: "",
                      score=None, profile=FIXTURE_PROFILE, state_loader=lambda _a, _n: None).run()
    finally:
        for k, v in saved.items():
            setattr(ja, k, v)
    return db, buf.getvalue()


def test_i_live_path_runs_deep_look_and_statline():
    """Regression for 8afd4fb: live path, lead >= 80 → deep_look and
    write_brief execute and both reach the rendered edition."""
    raw = [{"title": "Creative Director", "company": "Acme", "location": "Remote", "url": "https://x/1"},
           {"title": "Creative Director, Brand", "company": "Beta", "location": "Remote", "url": "https://x/2"}]
    calls = {"score": 0, "deep": 0, "brief": 0}

    def score_fit(_a, _p, job, _jd):
        calls["score"] += 1
        return (85 if job["company"] == "Acme" else 66, "why " + job["company"], "pause " + job["company"])

    def deep_look(job, profile, **_kw):
        calls["deep"] += 1
        check("I deep_look sees the lead", job["company"] == "Acme" and job.get("fit") == 85)
        check("I deep_look sees the candidate context", "Carlos" in (profile or "") or len(profile or "") > 50)
        return dict(DEEP_STUB)

    def write_brief(n, total, n_companies, ranked, new_keys, **_kw):
        calls["brief"] += 1
        check("I write_brief gets the seated list", n == 2 and len(ranked) == 2 and total == 2)
        check("I write_brief gets Shortlist-shaped new keys", all("|" in k for k in new_keys) and len(new_keys) == 2)
        return "Acme leads clear of the field."

    db, out = _live_run(raw, score_fit=score_fit, deep_look=deep_look, write_brief=write_brief)
    check("I live path: scorer, deep look and statline all executed", calls == {"score": 2, "deep": 1, "brief": 1}, calls)
    ed = db.editions[0]
    h, p = ed["html"], ed["payload"]
    check("I deep look rendered", "I kept looking" in h and "My view changed: 85 to 86." in h
          and "Reports to the CMO." in h)
    check("I statline rendered", "Acme leads clear of the field." in h and "2 read in full" in h)
    check("I payload deep + brief_line", p["deep"]["verdict"] == "My view changed: 85 to 86."
          and p["brief_line"] == "Acme leads clear of the field.")
    check("I fit_after still not applied", p["seats"][0]["fit"] == 85)
    check("I intelligence enums ok", p["intelligence"] == {"deep": "ok", "statline": "ok"}, p.get("intelligence"))
    check("I operator line carries both", "deep=ok statline=ok" in out and "reason=ai" in out, out)
    check("I counters still count on the live path", p["counts"]["model_reads_attempted"] == 2
          and p["counts"]["model_reads_failed"] == 0 and p["engine"] == "ai")


def test_i_deep_look_not_triggered_below_threshold():
    raw = [{"title": "Creative Director", "company": "Acme", "location": "Remote", "url": "https://x/1"}]
    calls = {"deep": 0, "brief": 0}

    def deep_look(*_a, **_kw):
        calls["deep"] += 1
        return dict(DEEP_STUB)

    def write_brief(*_a, **_kw):
        calls["brief"] += 1
        return ""       # the model may decide nothing is notable

    db, out = _live_run(raw, score_fit=lambda *_a: (72, "w", "p"), deep_look=deep_look, write_brief=write_brief)
    p = db.editions[0]["payload"]
    check("I threshold is the original 80", hr.DEEP_LOOK_THRESHOLD == 80)
    check("I below 80: deep not called, statline asked", calls == {"deep": 0, "brief": 1})
    check("I enums: not_triggered / empty", p["intelligence"] == {"deep": "not_triggered", "statline": "empty"}, p["intelligence"])
    check("I no deep panel", "I kept looking" not in db.editions[0]["html"])
    check("I operator line", "deep=not_triggered statline=empty" in out)


def test_i_heuristic_day_marks_both_not_run():
    raw = [{"title": "Creative Director", "company": "Acme", "location": "Remote", "url": "https://x/1"}]
    calls = {"deep": 0, "brief": 0}
    db, out = _live_run(raw, score_fit=lambda *_a: (None, None, None), key="",
                        deep_look=lambda *_a, **_kw: calls.__setitem__("deep", 1) or DEEP_STUB,
                        write_brief=lambda *_a, **_kw: calls.__setitem__("brief", 1) or "x")
    p = db.editions[0]["payload"]
    check("I heuristic: neither called", calls == {"deep": 0, "brief": 0})
    check("I heuristic: not_run / not_run", p["intelligence"] == {"deep": "not_run", "statline": "not_run"}
          and p["engine_reason"] == "no_key", p["intelligence"])


def test_i_silent_failures_are_named_and_never_leak():
    """The original functions print their failure to stdout and return None.
    The private path classifies that text and drops it."""
    raw = [{"title": "Creative Director SECRETCO", "company": "SecretCo", "location": "Remote", "url": "https://secret.example/1"}]
    import logging
    logs = []

    class H(logging.Handler):
        def emit(self, record):
            logs.append(self.format(record))

    h = H(); h.setFormatter(logging.Formatter("%(message)s")); hr.log.addHandler(h); hr.log.setLevel(logging.INFO)

    def deep_look(*_a, **_kw):
        print("[deep look skipped: HTTP 400 {\"error\": \"workspace header required RAWBODY\"}]")
        return None

    def write_brief(*_a, **_kw):
        print("  [Brief API 529] overloaded RAWBODY")
        return None

    try:
        db, out = _live_run(raw, score_fit=lambda *_a: (90, "why RAWWHY", "p"), deep_look=deep_look, write_brief=write_brief)
    finally:
        hr.log.removeHandler(h)
    p = db.editions[0]["payload"]
    check("I named: http_4xx / http_5xx", p["intelligence"] == {"deep": "http_4xx", "statline": "http_5xx"}, p["intelligence"])
    check("I operator line names them", "deep=http_4xx statline=http_5xx" in out)
    blob = out + "\n" + "\n".join(logs) + "\n" + json.dumps(p["intelligence"]) + json.dumps(p["counts"])
    check("I captured text never reaches stdout, logs or ledger enums", "RAWBODY" not in blob and "SecretCo" not in blob
          and "secret.example" not in blob and "workspace" not in blob)
    check("I edition still built, seat kept, no deep panel", len(p["seats"]) == 1 and p["deep"] is None
          and p["brief_line"] == "" and "I kept looking" not in db.editions[0]["html"])


def test_i_classifiers():
    c = hr.classify_deep_look
    check("I deep ok", c(DEEP_STUB, "") == "ok")
    check("I deep 4xx", c(None, "[deep look skipped: HTTP 403 forbidden]") == "http_4xx")
    check("I deep 5xx", c(None, "[deep look skipped: HTTP 529 overloaded]") == "http_5xx")
    check("I deep no_json", c(None, "[deep look skipped: no JSON in reply]") == "no_json")
    check("I deep error", c(None, "[deep look skipped: ReadTimeout]") == "error")
    check("I deep thin", c(None, "") == "thin_reply")
    b = hr.classify_brief_line
    check("I brief ok", b("Line.", "Observation: Line.") == "ok")
    check("I brief empty", b(None, "") == "empty")
    check("I brief 4xx", b(None, "  [Brief API 401] {}") == "http_4xx")
    check("I brief 5xx", b(None, "  [Brief API 500] {}") == "http_5xx")
    check("I brief unusable", b(None, "  [Brief attempt 2] unusable reply: '...'") == "unusable_reply")
    check("I brief error", b(None, "  [Brief error] boom") == "error")
    check("I enums closed", all(x in hr.DEEP_REASONS for x in ("ok", "not_run", "not_triggered", "http_4xx", "http_5xx", "no_json", "thin_reply", "error"))
          and all(x in hr.STATLINE_REASONS for x in ("ok", "not_run", "empty", "http_4xx", "http_5xx", "unusable_reply", "error")))


def test_i_injected_scorer_still_stubs_deep_and_brief():
    """The test seam must keep isolating tests from the network: an injected
    scorer with no deep/brief hook means no deep_look / write_brief call."""
    raw = [{"title": "Creative Director", "company": "Acme", "location": "Remote", "url": "https://x/1"}]
    ja = hr._import_job_alerts_adapters()
    saved = (ja.deep_look, ja.write_brief)
    hit = {"deep": 0, "brief": 0}
    ja.deep_look = lambda *_a, **_kw: hit.__setitem__("deep", 1) or DEEP_STUB
    ja.write_brief = lambda *_a, **_kw: hit.__setitem__("brief", 1) or "x"
    try:
        db = MemoryDb(); aid = _ready_agent(db, STRUCTURED); db.add_job(aid, "first_edition")
        _runner(db, raw, score=_score_by_title({}, default=(90, "w", "p"))).run()
    finally:
        ja.deep_look, ja.write_brief = saved
    p = db.editions[0]["payload"]
    check("I seam isolates the network", hit == {"deep": 0, "brief": 0} and p["deep"] is None)
    check("I seam marks them thin/empty, not ok", p["intelligence"] == {"deep": "thin_reply", "statline": "empty"}, p["intelligence"])


def test_i_why_now_uses_the_real_clock():
    import re
    raw = [{"title": "Creative Director", "company": "Acme", "location": "Remote", "url": "https://x/1",
            "posted_at": datetime(2026, 8, 20, tzinfo=timezone.utc)}]
    db = MemoryDb(); aid = _ready_agent(db, STRUCTURED, agent_no=1); db.add_job(aid, "first_edition")
    _runner(db, raw, score=_score_by_title({}, default=(70, "w", "p"))).run()
    wn = db.editions[0]["payload"]["seats"][0]["why_now"]
    m = re.search(r"still open as of (\d{1,2}:\d{2} [AP]M E[DS]T)$", wn)
    check("I why_now names the real Eastern clock", m is not None, wn)
    h = db.editions[0]["html"]
    colophon = re.search(r"Compiled (\d{1,2}:\d{2} [AP]M E[DS]T)", h)
    check("I why_now and colophon agree", colophon is not None and m is not None and colophon.group(1) == m.group(1))
    ja = hr._import_job_alerts_adapters()
    check("I public default unchanged", ja.why_now_text({"title": "x", "company": "y"}, False) == "Still open as of 8:00 AM ET")
    db2 = MemoryDb(); aid2 = _ready_agent(db2, STRUCTURED, agent_no=9); db2.add_job(aid2, "first_edition")
    _runner(db2, raw, score=_score_by_title({}, default=(70, "w", "p"))).run()
    check("I non-001 why_now is UTC", re.search(r"still open as of \d{1,2}:\d{2} [AP]M UTC$", db2.editions[0]["payload"]["seats"][0]["why_now"]) is not None)


# ---------------------------------------------------------------------------
# D — v1.4: the original deep look made reliable (parsing + budget + pause)
# ---------------------------------------------------------------------------

GOOD_JSON = ('{"role": "New seat, not a succession.", "moment": "Brand under construction.", '
             '"leadership": "Reports to the CMO.", "signal": "Two senior hires.", '
             '"question": "Team size unstated.", "fit_after": 84, "verdict": "Still 82."}')


class _Resp:
    def __init__(self, blocks, stop_reason="end_turn", status=200):
        self.status_code = status; self.ok = status < 400
        self._body = {"content": blocks, "stop_reason": stop_reason, "usage": {"output_tokens": 1}}
        self.text = "" if status < 400 else '{"error": {"message": "RAWERR"}}'
    def json(self): return self._body


def _with_deep_look(responses, fn):
    """Run fn() with job_alerts.requests.post answering from `responses` in
    order; returns (result, list of request bodies)."""
    ja = hr._import_job_alerts_adapters()
    saved_post, saved_key = ja.requests.post, ja.ANTHROPIC_KEY
    calls = []
    it = iter(responses)
    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return next(it)
    ja.requests.post, ja.ANTHROPIC_KEY = fake_post, "k"
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            res = fn(ja)
        return res, calls, out.getvalue()
    finally:
        ja.requests.post, ja.ANTHROPIC_KEY = saved_post, saved_key


JOB = {"title": "Head of Brand", "company": "Acme", "location": "SF", "url": "https://x/1", "fit": 82,
       "ai_why": "w", "ai_pause": "p"}


def test_d_deep_look_json_anywhere_in_reply():
    # (b) JSON in an earlier text block, a citation-only block last (the original read only the last block)
    blocks = [{"type": "text", "text": "Let me look."}, {"type": "server_tool_use", "id": "1", "name": "web_search"},
              {"type": "web_search_tool_result", "content": []},
              {"type": "text", "text": "Findings.\n" + GOOD_JSON}, {"type": "text", "text": "Sources: example.com"}]
    res, calls, out = _with_deep_look([_Resp(blocks)], lambda ja: ja.deep_look(JOB, "profile"))
    check("D json in an earlier block is found", res is not None and res["verdict"] == "Still 82." and res["fit_after"] == 84, res)
    check("D one call, bigger budget", len(calls) == 1 and calls[0]["max_tokens"] >= 3000 and calls[0]["max_tokens"] == hr._import_job_alerts_adapters().DEEP_LOOK_MAX_TOKENS)
    # (c) nested brace and a brace inside a string (the flat regex could not match this)
    nested = GOOD_JSON.replace('"Brand under construction."', '"Brand {under construction}."')
    res, _, _ = _with_deep_look([_Resp([{"type": "text", "text": "Here:\n" + nested + "\nDone."}])],
                                lambda ja: ja.deep_look(JOB, "profile"))
    check("D nested braces parse", res is not None and res["moment"] == "Brand {under construction}.", res)
    # a leading prose object without a verdict must not be mistaken for the answer
    two = '{"note": "scratch"}\n' + GOOD_JSON
    res, _, _ = _with_deep_look([_Resp([{"type": "text", "text": two}])], lambda ja: ja.deep_look(JOB, "profile"))
    check("D picks the object that names a verdict", res is not None and res["verdict"] == "Still 82.")
    # thin reply (fewer than four fields) is still refused as before
    res, _, _ = _with_deep_look([_Resp([{"type": "text", "text": '{"verdict": "Still 82.", "role": "x"}'}])],
                                lambda ja: ja.deep_look(JOB, "profile"))
    check("D thin reply still None", res is None)


def test_d_deep_look_truncation_and_pause_are_named():
    # (a) budget exhausted mid-JSON → None, and the report names it
    cut = GOOD_JSON[:60]
    res, _, out = _with_deep_look([_Resp([{"type": "text", "text": "Findings.\n" + cut}], stop_reason="max_tokens")],
                                  lambda ja: ja.deep_look(JOB, "profile"))
    check("D truncated → None", res is None)
    check("D truncated report carries stop_reason", "stop_reason=max_tokens" in out and "RAWERR" not in out)
    check("D classifier: truncated", hr.classify_deep_look(None, out) == "truncated")
    # (d) server pauses the turn: the call is continued with the assistant content and finishes
    first = _Resp([{"type": "text", "text": "Searching."}, {"type": "server_tool_use", "id": "1", "name": "web_search"}], stop_reason="pause_turn")
    second = _Resp([{"type": "text", "text": GOOD_JSON}])
    res, calls, out = _with_deep_look([first, second], lambda ja: ja.deep_look(JOB, "profile"))
    check("D pause_turn continued and parsed", res is not None and res["verdict"] == "Still 82." and len(calls) == 2)
    check("D continuation carries the paused content", calls[1]["messages"][-1]["role"] == "assistant"
          and calls[1]["messages"][-1]["content"] == first.json()["content"])
    # a pause that never resolves within the turn cap is named, not looped forever
    ja = hr._import_job_alerts_adapters()
    paused = [_Resp([{"type": "text", "text": "..."}], stop_reason="pause_turn")] * ja.DEEP_LOOK_MAX_TURNS
    res, calls, out = _with_deep_look(paused, lambda ja: ja.deep_look(JOB, "profile"))
    check("D pause cap", res is None and len(calls) == ja.DEEP_LOOK_MAX_TURNS and hr.classify_deep_look(None, out) == "paused")
    # HTTP failure path unchanged
    res, _, out = _with_deep_look([_Resp([], status=400)], lambda ja: ja.deep_look(JOB, "profile"))
    check("D http failure unchanged", res is None and hr.classify_deep_look(None, out) == "http_4xx")
    check("D enums extended", "truncated" in hr.DEEP_REASONS and "paused" in hr.DEEP_REASONS)


def test_d_deep_look_reaches_the_private_edition_end_to_end():
    """Live path + the real deep_look parser over a realistic multi-block reply."""
    raw = [{"title": "Creative Director", "company": "Acme", "location": "Remote", "url": "https://x/1"}]
    ja = hr._import_job_alerts_adapters()
    blocks = [{"type": "text", "text": "Looking."}, {"type": "server_tool_use", "id": "1", "name": "web_search"},
              {"type": "web_search_tool_result", "content": []}, {"type": "text", "text": GOOD_JSON},
              {"type": "text", "text": "(sources)"}]
    saved_post = ja.requests.post
    ja.requests.post = lambda url, headers=None, json=None, timeout=None: _Resp(blocks)
    try:
        db, out = _live_run(raw, score_fit=lambda *_a: (85, "w", "p"), deep_look=ja.deep_look,
                            write_brief=lambda *_a, **_kw: "One line.")
    finally:
        ja.requests.post = saved_post
    p = db.editions[0]["payload"]
    check("D end to end: deep=ok statline=ok", p["intelligence"] == {"deep": "ok", "statline": "ok"}, p["intelligence"])
    check("D end to end: panel rendered", "I kept looking" in db.editions[0]["html"] and "Still 82." in db.editions[0]["html"])


def test_d_dry_run_accepts_agent_number():
    db = MemoryDb()
    aid = _ready_agent(db, STRUCTURED, agent_no=1)
    check("D memory db resolves 1", db.agent_id_for_no(1) == aid and db.agent_id_for_no(2) is None)
    src = open(hr.__file__, encoding="utf-8").read()
    check("D main resolves a numeric agent read-only", 'agent_id_for_no(int(agent_id))' in src and 'fullmatch(r"\\d{1,4}"' in src)
    check("D RestDb query is a read", 'agents?agent_no=eq.' in src and 'select=id' in src)


# ---------------------------------------------------------------------------
# P — Move 2: Person from Memory (Candidate Context + judge voice)
# ---------------------------------------------------------------------------

N002_MEMORY = [
    ("record", "Head of Product Design at Northwind since 2022, leading a team of 14 across Berlin and Lisbon.", "linkedin"),
    ("record", "Design Director at Klarna (2018–2022): rebuilt the design system and the onboarding flow used by 20M people.", "resume.pdf"),
    ("self", "I want to run design for a company whose product is still being defined, not polish a finished one.", "conversation"),
    ("self", "Berlin is home; London and Amsterdam are easy. Not the US for now.", "conversation"),
]

N002_BRIEF = {"chapters": [
    {"title": "THE MOVE", "subjects": [{"handle": "Lead", "lines": ["Lead design for a product still being defined."]}]},
    {"title": "ROLE SPACE", "subjects": [{"handle": "Craft", "lines": ["Head of Design, VP Design, Design Director."]}]},
    {"title": "WHERE", "subjects": [{"handle": "Geography", "lines": ["Berlin, London, Amsterdam, remote Europe."]}]},
]}


def _n002(db, confirm=True):
    """A second client: confirmed memory (or not), an active Brief, no profile.md."""
    aid = str(uuid.uuid4())
    compiled = hr.compile_from_content(N002_BRIEF)
    assert hr.readiness_of(compiled) == "ready", compiled.get("readiness_reasons")
    db.add_brief(aid, N002_BRIEF, compiled_config=hr.persistable_compiled(compiled), readiness="ready", version=2)
    db.agent_numbers[aid] = 2
    for layer, st, src in N002_MEMORY:
        db.add_memory(aid, [st], layer=layer, source=src,
                      provenance="confirmed" if confirm else "stated")
    return aid


def test_p_compiler_reads_seats_outside_001_vocabulary():
    """Move 2: the ROLE gate must work for a designer, a marketer, a C-level —
    not only for creative-director seats. Bare ranks never become families."""
    fam = hr._extract_role_families
    check("P vp design is one family, never bare vp", fam("VP Design") == ["vp design"] and fam("VP of Design") == ["vp design"])
    check("P chief officers", fam("Chief Design Officer") == ["chief design officer"] and fam("Chief Brand Officer") == ["chief brand officer"])
    check("P bare ranks refused", fam("VP") == [] and fam("Director") == [] and fam("Head") == [])
    check("P creative abbreviations still whole titles", fam("Group CD, ECD, Head of Creative") == ["group cd", "ecd", "head of creative"])
    check("P generic variants for an unknown seat", hr.expand_role_family("vp design") == ["vp design", "vp of design", "vp, design"]
          and hr.expand_role_family("head of product design") == ["head of product design", "head, product design"])
    check("P hand rows untouched for №001's seats", hr.expand_role_family("creative director") == list(hr.ROLE_SYNONYMS["creative director"]))
    c = hr.compile_from_content(N002_BRIEF)
    check("P №002 Brief compiles ready with real families", hr.readiness_of(c) == "ready"
          and c["families"] == ["head of design", "vp design", "design director"] and "vp" not in c["include"])
    check("P №002 market is fetched for their seats, not only filtered",
          '"head of design"' in c["search_queries"] and '"vp design"' in c["search_queries"] and '"design director"' in c["search_queries"])
    ja = hr._import_job_alerts_adapters()
    agent = hr.agent_config_from_brief(ja, c, agent_id="x", agent_no=2)
    check("P VP Sales is not eligible under a design Brief", not ja.passes_title(agent, "VP Sales, EMEA")
          and not ja.passes_title(agent, "SVP Engineering") and ja.passes_title(agent, "VP, Design") and ja.passes_title(agent, "Director of Design, Growth"))


def test_p_compiler_is_verbatim_deterministic_and_confirmed_only():
    import candidate_context as cc
    rows = []
    for i, (layer, st, src) in enumerate(N002_MEMORY):
        rows.append({"id": f"m{i}", "layer": layer, "statement": st, "source": src,
                     "provenance": "confirmed", "status": "active", "created_at": f"2026-08-0{i+1}"})
    rows += [
        {"id": "x1", "layer": "record", "statement": "UNCONFIRMED CLAIM", "source": "s", "provenance": "stated", "status": "active", "created_at": "2026-08-09"},
        {"id": "x2", "layer": "model", "statement": "INFERRED CLAIM", "source": "s", "provenance": "inferred", "status": "active", "created_at": "2026-08-09"},
        {"id": "x3", "layer": "record", "statement": "RETRACTED CLAIM", "source": "s", "provenance": "confirmed", "status": "retracted", "created_at": "2026-08-09"},
        {"id": "x4", "layer": "record", "statement": "SUPERSEDED CLAIM", "source": "s", "provenance": "stated", "status": "superseded", "created_at": "2026-08-09"},
        {"id": "x5", "layer": "self", "statement": "TENSION CLAIM", "source": "s", "provenance": "confirmed", "status": "tension", "created_at": "2026-08-09"},
    ]
    out = cc.compile_candidate_context(name="Ada", rows=rows, brief_content=N002_BRIEF)
    t = out["text"]
    check("P confirmed statements verbatim", all(st in t for _l, st, _s in N002_MEMORY))
    check("P nothing unconfirmed enters", not any(k in t for k in ("UNCONFIRMED", "INFERRED", "RETRACTED", "SUPERSEDED", "TENSION")))
    check("P counts", out["statements"] == 4 and out["layers"]["record"] == 2 and out["layers"]["self"] == 2)
    check("P sources listed", out["sources"] == ["conversation", "linkedin", "resume.pdf"])
    check("P layer order record then self", t.index("## Record") < t.index("## In their own words"))
    check("P brief rendered as authorization", "## What they are looking for" in t and "ROLE SPACE: Head of Design, VP Design, Design Director." in t
          and "never to admit a role the Brief did not authorize" in t)
    check("P name in heading", t.startswith("# Candidate Context — Ada"))
    import random
    shuffled = list(rows); random.Random(7).shuffle(shuffled)
    out2 = cc.compile_candidate_context(name="Ada", rows=shuffled, brief_content=N002_BRIEF)
    check("P deterministic", out2["text"] == t and out2["hash"] == out["hash"] and len(out["hash"]) == 64)
    rows[0] = dict(rows[0], statement=rows[0]["statement"] + " Also Paris.")
    check("P hash tracks statements", cc.compile_candidate_context(name="Ada", rows=rows, brief_content=N002_BRIEF)["hash"] != out["hash"])
    check("P hash tracks Brief", cc.compile_candidate_context(name="Ada", rows=shuffled, brief_content=STRUCTURED)["hash"] != out["hash"])
    empty = cc.compile_candidate_context(name="Ada", rows=[r for r in rows if r["provenance"] != "confirmed"], brief_content=N002_BRIEF)
    check("P no confirmed rows → no context", empty["text"] == "" and empty["hash"] == "" and empty["statements"] == 0)
    check("P no Brief → no authorization section, still a context", "## What they are looking for" not in
          cc.compile_candidate_context(name="Ada", rows=shuffled, brief_content=None)["text"])


def test_p_second_client_is_judged_from_memory_without_profile_md():
    """№002 end to end on the live path: confirmed Memory → context → the
    original judge (neutral voice) → private edition, with no profile.md."""
    raw = [{"title": "Head of Design", "company": "Fabric", "location": "Berlin", "url": "https://fabric/1"},
           {"title": "Design Director, Growth", "company": "Orbit", "location": "London", "url": "https://orbit/1"},
           {"title": "Creative Director", "company": "Suno", "location": "NYC", "url": "https://suno/1"}]
    ja = hr._import_job_alerts_adapters()
    seen = {"profiles": [], "prompts": [], "deep": 0, "brief": 0, "opened_profile_md": 0}
    real_open = ja.load_profile

    def load_profile_spy(agent):
        seen["opened_profile_md"] += 1
        return real_open(agent)

    def score_fit(agent, profile, job, jd):
        seen["profiles"].append(profile)
        seen["prompts"].append(ja.JudgeVoice(agent).one + "|" + ja.JudgeVoice(agent).obj)
        return (86 if job["company"] == "Fabric" else 71, "why " + job["company"], "pause")

    def deep_look(job, profile, agent=None, **_kw):
        seen["deep"] += 1
        check("P deep look reads the memory context", "Northwind" in profile and "Candidate Profile" not in profile)
        check("P deep look voice is the client's", ja.JudgeVoice(agent).one == "one client")
        return dict(DEEP_STUB)

    def write_brief(n, total, nc, ranked, new_keys, agent=None, as_of=None, **_kw):
        seen["brief"] += 1
        check("P statline voice and clock", ja.JudgeVoice(agent).one == "one client" and as_of and "UTC" in as_of)
        return "Fabric leads clear of the field."

    saved = {k: getattr(ja, k) for k in ("score_fit", "deep_look", "write_brief", "ANTHROPIC_KEY", "load_profile")}
    ja.score_fit, ja.deep_look, ja.write_brief, ja.ANTHROPIC_KEY, ja.load_profile = score_fit, deep_look, write_brief, "k", load_profile_spy
    db = MemoryDb()
    aid = _n002(db)
    jid = db.add_job(aid, "first_edition")
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            hr.Runner(db, collector=lambda _c: raw, today=FROZEN_TODAY, fetch_jd=lambda _u: "",
                      score=None, profile=None, state_loader=lambda _a, _n: None).run()
    finally:
        for k, v in saved.items():
            setattr(ja, k, v)
    check("P job done", db.jobs[jid]["status"] == "done" and db.jobs[jid].get("error") in (None, ""), db.jobs[jid])
    p = db.editions[0]["payload"]
    check("P eligibility is the Brief's: Suno NYC out, Berlin/London in", sorted(s["company"] for s in p["seats"]) == ["Fabric", "Orbit"]
          and p["counts"]["eligible"] == 2)
    check("P judged against the memory context, never profile.md", len(seen["profiles"]) == 2
          and all("Northwind" in pr and "Klarna" in pr and "Candidate Profile" not in pr for pr in seen["profiles"])
          and seen["opened_profile_md"] == 0)
    check("P Brief rendered inside the context", all("ROLE SPACE: Head of Design" in pr for pr in seen["profiles"]))
    check("P neutral voice", all(pp == "one client|them" for pp in seen["prompts"]), seen["prompts"])
    check("P deep look and statline ran for №002", seen["deep"] == 1 and seen["brief"] == 1
          and p["intelligence"] == {"deep": "ok", "statline": "ok"})
    cc = p["candidate_context"]
    check("P receipt: memory, 4 statements, hash, no text", cc["kind"] == "memory" and cc["statements"] == 4
          and len(cc["hash"]) == 64 and cc["sources"] == ["conversation", "linkedin", "resume.pdf"]
          and "Northwind" not in json.dumps(cc))
    h = db.editions[0]["html"]
    check("P greeting without a name is plain", re.search(r'class="brief">Good (morning|afternoon|evening)\.</p>', h) is not None)
    check("P edition rendered", "I kept looking" in h and "Fabric leads clear of the field." in h and "Northwind" not in h)


def test_p_second_client_without_confirmed_memory_is_refused_before_any_call():
    raw = [{"title": "Head of Design", "company": "Fabric", "location": "Berlin", "url": "https://fabric/1"}]
    calls = {"collector": 0, "score": 0}

    def collector(_c):
        calls["collector"] += 1
        return raw

    db = MemoryDb()
    aid = _n002(db, confirm=False)      # beliefs exist, none confirmed
    jid = db.add_job(aid, "first_edition")
    hr.Runner(db, collector=collector, today=FROZEN_TODAY, fetch_jd=lambda _u: "",
              score=lambda *_a: calls.__setitem__("score", 1) or (80, "w", "p"), profile=None,
              state_loader=lambda _a, _n: None).run()
    check("P unconfirmed beliefs are not a context", db.jobs[jid]["error"] == "no_candidate_context")
    check("P refused before collection and judgment", calls == {"collector": 0, "score": 0} and db.editions == [])


def test_p_001_prompts_are_byte_identical_to_the_originals():
    """The three prompts for №001 must not move: his persona, pronouns and
    judgment lenses are the original literals, now as config."""
    ja = hr._import_job_alerts_adapters()
    agent = ja.load_agent_config("001")
    v = ja.JudgeVoice(agent)
    check("P 001 persona", v.one == "one senior creative director" and (v.subj, v.obj, v.poss) == ("he", "him", "his"))
    check("P 001 lenses verbatim", v.lenses == "the companies he has built for, brands he entered before their identity was fixed, cities he calls home, teams he built from zero")
    check("P 001 rubric verbatim", v.rubric == "seniority match, craft match, brand-led scope, AI-era relevance")
    check("P public call sites pass no agent (original wording)", ja.JudgeVoice(None).one == "one senior creative director"
          and ja.JudgeVoice(None).a_persona == "a senior creative director")
    captured = []
    saved = (ja.requests.post, ja.ANTHROPIC_KEY)
    ja.requests.post = lambda url, headers=None, json=None, timeout=None: (captured.append(json["messages"][0]["content"]), _Resp([{"type": "text", "text": "{}"}]))[1]
    ja.ANTHROPIC_KEY = "k"
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            ja.score_fit(agent, "PROFILE", {"title": "Creative Director", "company": "Apple", "location": "Culver City", "url": "u"}, "JD")
            ja.deep_look({"title": "Creative Director", "company": "Apple", "location": "Culver City", "url": "u", "fit": 85, "ai_why": "w", "ai_pause": "p"}, "PROFILE")
            ja.write_brief(1, 100, 41, [{"title": "Creative Director", "company": "Apple", "location": "Culver City", "fit": 85}], set())
    finally:
        ja.requests.post, ja.ANTHROPIC_KEY = saved
    check("P 001 score prompt literal", "You are the personal career agent of one senior creative director." in captured[0]
          and "judging ONE role for HIM specifically" in captured[0]
          and "NOTE: He has flagged Apple as a priority target — he asked his agent to watch this company closely." in captured[0]
          and "seniority match, craft match, brand-led scope, AI-era relevance for THIS candidate" in captured[0]
          and "spoken to him as you/your (never his name, never he/his)" in captured[0]
          and "name what he should verify first" in captured[0])
    deep_p = [c for c in captured if c.startswith("You are FOOUND")]
    brief_p = [c for c in captured if "THE SHORTLIST" in c]
    check("P 001 deep prompt literal", deep_p and "You are FOOUND, the personal career agent of one senior creative director." in deep_p[0]
          and "CANDIDATE PROFILE (judge against HIM):" in deep_p[0])
    check("P 001 statline prompt literal", brief_p and "your one client, a senior creative director. The page already" in brief_p[0]
          and "8:00 AM ET" in brief_p[0])
    neutral = ja.JudgeVoice(hr.agent_config_from_brief(ja, hr.compile_from_content(N002_BRIEF), agent_id="x", agent_no=2))
    check("P a neutral client never sees he/him/his or a discipline", not re.search(r"\b(he|him|his)\b", neutral.one + "|" + neutral.lenses)
          and "creative director" not in neutral.one)


# ---------------------------------------------------------------------------
# Q — Move 2: FOOUND drafts a proposed Working Brief from confirmed Memory
# ---------------------------------------------------------------------------

def _draft_json(ids, role_line="Head of Design, VP Design, Design Director.", where_line="Berlin, London, Amsterdam, remote Europe."):
    return json.dumps({"chapters": [
        {"title": "THE MOVE", "subjects": [
            {"handle": "Lead", "lines": ["Lead design for a product still being defined."], "grounds": [ids[2]]},
            {"handle": "Build", "lines": ["Build the team and the system, not inherit a finished one."], "grounds": [ids[0], ids[1]]}]},
        {"title": "ROLE SPACE", "subjects": [{"handle": "Craft", "lines": [role_line], "grounds": [ids[0]]}]},
        {"title": "WHERE", "subjects": [{"handle": "Geography", "lines": [where_line], "grounds": [ids[3]]}]},
        {"title": "AVOID", "subjects": [{"handle": "Not this", "lines": ["Not the US for now."], "grounds": [ids[3]]},
                                        {"handle": "Invented", "lines": ["No agencies."], "grounds": ["not-a-real-id"]}]},
    ]})


def test_q_parse_is_strict_and_grounded():
    import brief_proposal as bp
    ids = ["m0", "m1", "m2", "m3"]
    content, reason = bp.parse_brief_draft("Here is the brief:\n" + _draft_json(ids) + "\nHope this helps.", ids)
    check("Q parsed", content is not None and reason == "")
    titles = [c["title"] for c in content["chapters"]]
    check("Q chapter order fixed", titles == ["THE MOVE", "ROLE SPACE", "WHERE", "AVOID"])
    avoid = [c for c in content["chapters"] if c["title"] == "AVOID"][0]
    check("Q ungrounded subject dropped", [s["handle"] for s in avoid["subjects"]] == ["Not this"])
    check("Q grounds kept only when real", content["chapters"][0]["subjects"][1]["grounds"] == ["m0", "m1"])
    bad, reason = bp.parse_brief_draft("no json here", ids)
    check("Q no json named", bad is None and reason == "no_json")
    partial = json.dumps({"chapters": [{"title": "THE MOVE", "subjects": [{"handle": "Lead", "lines": ["x"], "grounds": ["m0"]}]}]})
    bad, reason = bp.parse_brief_draft(partial, ids)
    check("Q missing chapters named", bad is None and reason == "missing_chapters:ROLE SPACE,WHERE")
    ok, why = bp.check_proposal(content, hr.compile_from_content, hr.readiness_of)
    check("Q executable draft passes the compiler", ok and why == "")
    prose, _ = bp.parse_brief_draft(_draft_json(ids, role_line="Something senior in design leadership.", where_line="Somewhere in Europe with a good design scene."), ids)
    ok2, why2 = bp.check_proposal(prose, hr.compile_from_content, hr.readiness_of)
    check("Q prose ROLE SPACE is caught by the compiler", not ok2 and "ROLE family" in why2)


def test_q_propose_brief_job_end_to_end():
    db = MemoryDb()
    aid = str(uuid.uuid4()); db.agent_numbers[aid] = 2
    rows = []
    for layer, st, src in N002_MEMORY:
        rows += db.add_memory(aid, [st], layer=layer, source=src)[-1:]
    ids = [r["id"] for r in rows]
    prompts = []

    def drafter(prompt):
        prompts.append(prompt)
        # first draft is prose (not huntable); the second, after feedback, is plain titles
        if len(prompts) == 1:
            return _draft_json(ids, role_line="A senior design leadership seat.", where_line="Somewhere in Europe.")
        return _draft_json(ids)

    jid = db.add_job(aid, "propose_brief")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        reports = hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY, drafter=drafter).run()
    check("Q job done", db.jobs[jid]["status"] == "done" and reports[0].action == "proposed", db.jobs[jid])
    check("Q two attempts, feedback carried the compiler's reading", len(prompts) == 2
          and "COULD NOT BE HUNTED" in prompts[1] and "ROLE family" in prompts[1])
    check("Q prompt grounded in confirmed statements with ids", all(f"[{i}]" in prompts[0] for i in ids)
          and "Northwind" in prompts[0] and "Candidate Context" in prompts[0])
    briefs = [b for b in db.briefs.values() if b["agent_id"] == aid]
    check("Q one proposed brief, version 1, not active", len(briefs) == 1 and briefs[0]["state"] == "proposed" and briefs[0]["version"] == 1)
    content = briefs[0]["content"]
    check("Q content in the Brief grammar", [c["title"] for c in content["chapters"]] == ["THE MOVE", "ROLE SPACE", "WHERE", "AVOID"])
    prov = content["provenance"]
    check("Q provenance receipt", prov["proposed_by"] == "engine" and prov["executable"] is True and prov["attempts"] == 2
          and len(prov["candidate_context_hash"]) == 64 and prov["compiler_reasons"] == [])
    compiled = hr.compile_from_content(content)
    check("Q the proposal compiles ready", hr.readiness_of(compiled) == "ready" and compiled["families"] == ["head of design", "vp design", "design director"])
    check("Q nothing is authority yet: no active brief, agent untouched", db.active_brief(aid) is None and db.editions == [])
    check("Q console carries no statement text", "Northwind" not in out.getvalue() and "Berlin" not in out.getvalue())
    # a second proposal abandons the first
    db.add_job(aid, "propose_brief")
    hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY, drafter=lambda p: _draft_json(ids)).run()
    states = sorted((b["version"], b["state"]) for b in db.briefs.values() if b["agent_id"] == aid)
    check("Q re-proposal abandons the old, versions advance", states == [(1, "abandoned"), (2, "proposed")], states)


def test_q_propose_brief_refuses_without_confirmed_memory_and_never_uses_profile_md():
    db = MemoryDb()
    aid = str(uuid.uuid4()); db.agent_numbers[aid] = 1          # even №001: intent needs confirmed Memory
    jid = db.add_job(aid, "propose_brief")
    called = []
    hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY, drafter=lambda p: called.append(1) or "").run()
    check("Q no confirmed memory → no proposal, no model call", db.jobs[jid]["error"] == "no_candidate_context" and called == []
          and not [b for b in db.briefs.values() if b["agent_id"] == aid])
    db2 = MemoryDb()
    aid2 = str(uuid.uuid4()); db2.agent_numbers[aid2] = 2
    db2.add_memory(aid2, ["Led design at Acme."])
    jid2 = db2.add_job(aid2, "propose_brief")
    hr.Runner(db2, collector=lambda _c: [], today=FROZEN_TODAY, drafter=lambda p: "sorry, no").run()
    check("Q unusable drafts fail honestly", db2.jobs[jid2]["error"] == "proposal_failed"
          and not [b for b in db2.briefs.values() if b["agent_id"] == aid2])


def test_q_redraft_hears_the_clients_objection():
    db = MemoryDb()
    aid = str(uuid.uuid4()); db.agent_numbers[aid] = 2
    rows = []
    for layer, st, src in N002_MEMORY:
        rows += db.add_memory(aid, [st], layer=layer, source=src)[-1:]
    ids = [r["id"] for r in rows]
    prompts = []
    db.add_job(aid, "propose_brief", payload={"wrong": [{"chapter": "THE MOVE", "handle": "Build"}]})
    hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY, drafter=lambda p: prompts.append(p) or _draft_json(ids)).run()
    check("Q objection reaches the drafter", len(prompts) == 1 and "marked these subjects of the previous draft as wrong: THE MOVE / Build" in prompts[0])


def test_q_daily_enqueue_is_idempotent_and_gated():
    db = MemoryDb()
    a1 = _n002(db); db.agent_state[a1] = "at_work"                       # ready, no edition → queued
    a2 = _n002(db); db.agent_state[a2] = "at_work"                       # ready, edition today → skipped
    db.editions.append({"agent_id": a2, "edition_date": FROZEN_TODAY.isoformat(), "payload": {}, "html": "", "outcome": "seats"})
    a3 = _n002(db); db.agent_state[a3] = "paused"                        # not at_work → ignored
    a4 = str(uuid.uuid4()); db.agent_numbers[a4] = 9; db.agent_state[a4] = "at_work"   # no brief
    a5 = str(uuid.uuid4()); db.agent_numbers[a5] = 10; db.agent_state[a5] = "at_work"
    db.add_brief(a5, N002_BRIEF, readiness="not_ready")                  # not ready
    r = hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY)
    out = r.enqueue_daily(now=AFTER_EDITION_HOUR)
    check("Q daily counts", out == {"at_work": 4, "queued": 1, "already_queued": 0, "has_edition": 1, "no_brief": 1, "not_ready": 1, "before_hour": 0}, out)
    q = [j for j in db.jobs.values() if j["agent_id"] == a1 and j["type"] == "first_edition"]
    check("Q one queued edition job with the brief version", len(q) == 1 and q[0]["payload"] == {"brief_version": 2, "daily": True})
    out2 = r.enqueue_daily(now=AFTER_EDITION_HOUR)
    check("Q second beat queues nothing new", out2["queued"] == 0 and out2["already_queued"] == 1
          and len([j for j in db.jobs.values() if j["type"] == "first_edition"]) == 1)


def test_q_daily_waits_for_the_persons_morning():
    """The day's edition is made before the person's morning, not at
    midnight UTC: a beat at 02:30 Berlin queues nothing and says so; the
    first beat after 05:00 Berlin queues it. Summer and winter clocks."""
    from datetime import datetime, timezone
    db = MemoryDb()
    a1 = _n002(db); db.agent_state[a1] = "at_work"
    r = hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY)
    early = r.enqueue_daily(now=datetime(2026, 9, 2, 0, 30, tzinfo=timezone.utc))   # 02:30 CEST
    check("Q before the hour: nothing queued, named", early["before_hour"] == 1 and early["queued"] == 0
          and not [j for j in db.jobs.values() if j["type"] == "first_edition"])
    late = r.enqueue_daily(now=datetime(2026, 9, 2, 3, 10, tzinfo=timezone.utc))    # 05:10 CEST
    check("Q after the hour: queued", late["queued"] == 1 and late["before_hour"] == 0)
    check("Q winter clock", not hr.edition_hour_reached(datetime(2026, 12, 2, 3, 30, tzinfo=timezone.utc))
          and hr.edition_hour_reached(datetime(2026, 12, 2, 4, 5, tzinfo=timezone.utc)))


def test_q_full_chain_for_a_second_client():
    """№002, end to end through the engine: confirmed Memory → FOOUND drafts
    the Brief → the client confirms (the 013 door, simulated on the memory
    db) → compile names readiness → the daily beat queues an edition → the
    original judge produces a private edition from the Memory context."""
    db = MemoryDb()
    aid = str(uuid.uuid4()); db.agent_numbers[aid] = 2; db.agent_state[aid] = "at_work"
    rows = []
    for layer, st, src in N002_MEMORY:
        rows += db.add_memory(aid, [st], layer=layer, source=src)[-1:]
    ids = [r["id"] for r in rows]
    # 1. draft
    db.add_job(aid, "propose_brief")
    hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY, drafter=lambda p: _draft_json(ids)).run()
    proposed = [b for b in db.briefs.values() if b["agent_id"] == aid and b["state"] == "proposed"]
    check("Q chain: proposal exists, nothing active", len(proposed) == 1 and db.active_brief(aid) is None)
    # 2. the client confirms (activate_brief): proposed → active + compile job (as migration 013 does)
    proposed[0]["state"] = "active"; proposed[0]["confirmed_at"] = "2026-09-02T12:00:00Z"
    db.add_job(aid, "compile_brief", payload={"brief_version": 1})
    hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY).run()
    active = db.active_brief(aid)
    check("Q chain: compiled ready with the person present", active["readiness"] == "ready"
          and "no_candidate_context" not in active["compiled_config"]["readiness_reasons"])
    # 3. the daily beat queues the edition; the hunt judges from Memory
    raw = [{"title": "Head of Design", "company": "Fabric", "location": "Berlin", "url": "https://fabric/1"},
           {"title": "VP, Design", "company": "Orbit", "location": "Amsterdam", "url": "https://orbit/1"},
           {"title": "VP Sales", "company": "Orbit", "location": "Amsterdam", "url": "https://orbit/2"},
           {"title": "Creative Director", "company": "Suno", "location": "NYC", "url": "https://suno/1"}]
    r = hr.Runner(db, collector=lambda _c: raw, today=FROZEN_TODAY, fetch_jd=lambda _u: "",
                  score=_score_by_title({"Head of Design": (84, "why", "pause")}, default=(66, "w", "p")),
                  profile=None, state_loader=lambda _a, _n: None,
                  deep=lambda *_a, **_k: dict(DEEP_STUB), brief_line_fn=lambda *_a, **_k: "Fabric leads clear of the field.")
    # The Brief coming into force already earned its edition at compile time
    # (the agent is at work); the daily beat finds it queued and adds none.
    from_brief = [j for j in db.jobs.values()
                  if j["agent_id"] == aid and j["type"] == "first_edition" and j["status"] == "queued"]
    check("Q chain: the Brief in force queued its own edition",
          len(from_brief) == 1 and (from_brief[0].get("payload") or {}).get("reason") == "brief_in_force")
    beat = r.enqueue_daily(now=AFTER_EDITION_HOUR)
    check("Q chain: beat finds that edition queued, adds none", beat["queued"] == 0 and beat["already_queued"] == 1)
    r.run()
    check("Q chain: edition written", len(db.editions) == 1 and db.editions[0]["agent_id"] == aid)
    p = db.editions[0]["payload"]
    check("Q chain: Brief authority, not Carlos's", sorted(s["company"] for s in p["seats"]) == ["Fabric", "Orbit"]
          and p["counts"]["eligible"] == 2 and p["candidate_context"]["kind"] == "memory")
    check("Q chain: the original intelligence, in the edition", p["intelligence"] == {"deep": "ok", "statline": "ok"}
          and "I kept looking" in db.editions[0]["html"])
    check("Q chain: second beat is a no-op", r.enqueue_daily(now=AFTER_EDITION_HOUR)["has_edition"] == 1)


def test_h9_boundaries():
    src = open(hr.__file__, encoding="utf-8").read()
    check("H9 no market_seen access",
          not re.search(r"""['\"]market_seen['\"]|/market_seen|from market_seen""", src))
    check("H9 no agent_config access",
          not re.search(r"""['\"]agent_config['\"]|/agent_config|from agent_config""", src))
    check("H9 no publish_shortlist call", not re.search(r"publish_shortlist\s*\(", src))
    check("H9 no docs/ write", not re.search(r"""open\([^)]*docs/|['\"]docs/""", src))
    check("H9 DUMMY ROLE rejected at persist", 'if "DUMMY ROLE" in result["html"]' in src)
    check("H9 memory not imported as authority", "from memory" not in src and "table memory" not in src)
    check("H9 judge_seats gone", "def judge_seats" not in src and "def title_fit" not in src
          and "def context_fit" not in src and "def mandate_fit" not in src)
    check("H9 enrich gone", "enrich_persisted_edition" not in src and "PROTECTED_EDITION_PREFIXES" not in src)
    check("H9 no update_edition write path", "def update_edition" not in src)
    check("H9 original loop reused", "rank_with_fit(" in src and "seat_edition(" in src
          and "deep_look(" in src and "write_brief(" in src)
    db = MemoryDb()
    aid = _ready_agent(db)
    db.add_job(aid, "first_edition")
    _runner(db, []).run()
    check("H9 no memory reads", db.memory_reads == 0)
    check("H9 no market_seen reads", db.market_seen_reads == 0)
    check("H9 no publish", db.publish_calls == 0)


def test_q_sweep_proposes_without_being_asked():
    """Move 2: nobody asks FOOUND for a Brief. A settled Mirror with no Brief
    in force gets one propose_brief job; an active Brief is never touched; an
    open Mirror waits unless the client has been quiet; a proposal drafted
    from the current understanding is left alone; a stale one is redrafted;
    a draft that failed on this understanding is not retried."""
    import candidate_context as cc
    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    db = MemoryDb()

    def person(no, *, confirm_all=True, state="mirror_ready"):
        aid = str(uuid.uuid4()); db.agent_numbers[aid] = no; db.agent_state[aid] = state
        rows = []
        for layer, st, src in N002_MEMORY:
            rows += db.add_memory(aid, [st], layer=layer, source=src)[-1:]
        if not confirm_all:
            db.add_memory(aid, ["Spent a year in Tokyo."], layer="record", source="resume.pdf", provenance="extracted")
        return aid, rows

    settled, _ = person(2)                                             # → queued
    active, _ = person(3); db.add_brief(active, N002_BRIEF, readiness="ready")   # → has_active
    nothing = str(uuid.uuid4()); db.agent_numbers[nothing] = 4; db.agent_state[nothing] = "feed_submitted"   # → no_confirmed
    open_recent, rows_open = person(5, confirm_all=False)              # open Mirror, confirmed 5 min ago → waits
    for r in db.memory[open_recent]:
        r["created_at"] = "2026-09-03T11:55:00Z"
    open_quiet, _ = person(6, confirm_all=False)                       # open Mirror, quiet 30 min → queued
    for r in db.memory[open_quiet]:
        r["created_at"] = "2026-09-03T11:30:00Z"
    current, rows_cur = person(7)                                      # proposal from this understanding → current
    h_cur = cc.context_hash(cc.confirmed_rows(db.confirmed_memory(current)), None)
    db.add_brief(current, dict(N002_BRIEF, provenance={"candidate_context_hash": h_cur}), state="proposed", readiness="ready")
    stale, _ = person(8)                                               # proposal from an older understanding → queued (redraft)
    db.add_brief(stale, dict(N002_BRIEF, provenance={"candidate_context_hash": "old"}), state="proposed", readiness="ready")
    failed, _ = person(9)                                              # failed after the newest confirmation → not retried
    db.add_job(failed, "propose_brief", status="failed", requested_at="2026-09-03T11:00:00Z",
               payload={"auto": True, "engine": hr.current_engine_sha()})
    archived, _ = person(10, state="archived")                         # never seen
    running, _ = person(11); db.add_job(running, "propose_brief", status="running")   # in flight

    r = hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY)
    out = r.sweep_proposals(now=now)
    check("Q sweep counts", out == {"agents": 9, "queued": 3, "has_active": 1, "no_confirmed": 1, "mirror_open": 1,
                                    "current": 1, "in_flight": 1, "failed_on_this": 1, "already_queued": 0}, out)
    queued_for = sorted(db.agent_numbers[j["agent_id"]] for j in db.jobs.values()
                        if j["type"] == "propose_brief" and j["status"] == "queued")
    check("Q sweep queued for the settled, the quiet, and the stale", queued_for == [2, 6, 8], queued_for)
    j = next(j for j in db.jobs.values() if j["agent_id"] == settled)
    check("Q sweep payload names the understanding it drafts from",
          j["payload"].get("auto") is True and len(j["payload"].get("context_hash", "")) == 64)
    out2 = r.sweep_proposals(now=now)
    check("Q second beat proposes nothing new", out2["queued"] == 0 and out2["in_flight"] == 4, out2)
    # a row the person cannot see (no handle, or the behavior layer) never holds the Brief hostage
    ghost, _ = person(12)
    db.add_memory(ghost, ["Reads design Twitter."], layer="model", source="synthesis", provenance="inferred", handle=None)
    db.add_memory(ghost, ["Opened three editions."], layer="behavior", source="app", provenance="extracted", handle="Habit")
    out_g = r.sweep_proposals(now=now)
    check("Q invisible rows do not keep the Mirror open", out_g["queued"] == 1 and out_g["mirror_open"] == 1
          and any(j["agent_id"] == ghost and j["type"] == "propose_brief" for j in db.jobs.values()), out_g)
    # the failed one is retried once the client confirms something new
    db.add_memory(failed, ["Open to Copenhagen."], layer="self", source="conversation")
    db.memory[failed][-1]["created_at"] = "2026-09-03T11:59:00Z"
    out3 = r.sweep_proposals(now=now + timedelta(minutes=30))
    check("Q new confirmation reopens a failed draft; the recent one is now quiet", out3["queued"] == 2
          and any(j["agent_id"] == failed and j["status"] == "queued" for j in db.jobs.values())
          and any(j["agent_id"] == open_recent and j["status"] == "queued" for j in db.jobs.values()), out3)


def test_q_proposal_carries_its_own_readiness():
    """A proposal FOOUND cannot hunt from is stored with readiness not_ready
    and the compiler's reasons, so the room can say so instead of offering
    to confirm it; a huntable one is stored ready."""
    db = MemoryDb()
    aid = str(uuid.uuid4()); db.agent_numbers[aid] = 2
    ids = [r["id"] for layer, st, src in N002_MEMORY for r in db.add_memory(aid, [st], layer=layer, source=src)[-1:]]
    db.add_job(aid, "propose_brief")
    hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY, drafter=lambda p: _draft_json(ids)).run()
    ok = next(b for b in db.briefs.values() if b["agent_id"] == aid and b["state"] == "proposed")
    blocking = [r for r in ok["compiled_config"]["readiness_reasons"] if not r.startswith("unmapped_location_phrase:")]
    check("Q huntable proposal stored ready", ok["readiness"] == "ready" and blocking == [],
          (ok["readiness"], ok["compiled_config"]["readiness_reasons"]))
    no_where = json.dumps({"chapters": [
        {"title": "THE MOVE", "subjects": [{"handle": "Lead", "lines": ["Lead design."], "grounds": [ids[2]]}]},
        {"title": "ROLE SPACE", "subjects": [{"handle": "Craft", "lines": ["Head of Design."], "grounds": [ids[0]]}]},
        {"title": "WHERE", "subjects": [{"handle": "Still learning", "lines": ["Where you want to work."], "grounds": []}]},
    ]})
    db.add_job(aid, "propose_brief")
    hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY, drafter=lambda p: no_where).run()
    gap = next(b for b in db.briefs.values() if b["agent_id"] == aid and b["state"] == "proposed")
    check("Q unhuntable proposal stored not_ready with the reason",
          gap["readiness"] == "not_ready" and "no_accepted_locations" in gap["compiled_config"]["readiness_reasons"]
          and gap["content"]["provenance"]["executable"] is False and ok["id"] != gap["id"]
          and db.briefs[ok["id"]]["state"] == "abandoned", (gap["readiness"], gap["compiled_config"].get("readiness_reasons")))


def test_q_sweep_stands_down_when_the_door_is_not_in_the_database():
    """Before migration 013 is applied, jobs.type does not know propose_brief.
    The production adapter names that (job_type_unknown) and the sweep stands
    down with door_closed=1 instead of failing the heartbeat every beat."""
    class Resp:
        def __init__(self, code, text=""):
            self.status_code, self.text, self.content = code, text, text.encode()
        def raise_for_status(self):
            if self.status_code >= 400:
                import requests
                raise requests.HTTPError(response=self)
        def json(self):
            return json.loads(self.text) if self.text else []
    class FakeRequests:
        def post(self, url, headers=None, data=None, timeout=None):
            return Resp(400, '{"code":"23514","message":"new row for relation \"jobs\" violates check constraint \"jobs_type_check\""}')
    rest = hr.RestDb("https://example.supabase.co", "k")
    rest._requests = FakeRequests()
    try:
        rest.enqueue_job("a", "propose_brief", {})
        check("Q closed door named", False, "no error raised")
    except hr.HuntError as e:
        check("Q closed door named", e.name == "job_type_unknown", e.name)

    class ClosedDb(MemoryDb):
        def enqueue_job(self, agent_id, job_type, payload):
            raise hr.HuntError("job_type_unknown")
    db = ClosedDb()
    for no in (2, 3):
        aid = str(uuid.uuid4()); db.agent_numbers[aid] = no; db.agent_state[aid] = "mirror_ready"
        for layer, st, src in N002_MEMORY:
            db.add_memory(aid, [st], layer=layer, source=src)
    out = hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY).sweep_proposals()
    check("Q sweep stands down once, queues nothing", out.get("door_closed") == 1 and out["queued"] == 0
          and not any(j["type"] == "propose_brief" for j in db.jobs.values()), out)


# ---------------------------------------------------------------------------
# V · Move 4: the Candidate page is drafted from confirmed Memory, once
# ---------------------------------------------------------------------------

def _candidate_json(ids, **over):
    page = {
        "line": "This is the candidate I work for. Fourteen years of product design. The pattern: she builds the function while the product is still being decided.",
        "now": "Head of Product Design, Northwind", "based": "Berlin", "since": "2012",
        "chapters": [
            {"company": "Northwind", "years": "{2022–}", "at_rest": "Head of Product Design · first design leader",
             "narrative": "Hired as the first design leader. Built the team.", "meta": "Berlin · 2022–present", "grounds": [ids[0]]},
            {"company": "Klarna", "years": "2018-22", "at_rest": "Lead Product Designer", "narrative": "Checkout, then the design system.",
             "meta": "Stockholm · 2018–2022", "grounds": [ids[1]]},
            {"company": "Invented Corp", "years": "{2010–12}", "at_rest": "Nothing", "narrative": "Made up.", "meta": "", "grounds": ["not-a-real-id"]},
        ],
        "trusted_with": [
            {"word": "The first hire", "line": "Twice the first design leader.", "grounds": [ids[0]]},
            {"word": "Ungrounded", "line": "No statement says this.", "grounds": []},
        ],
        "references": [{"name": "Jonas", "quote": "Invented quote", "who": "CPO"}],
        "own_words": "The model must never write this.",
        "links": {"linkedin": "https://linkedin.com/in/invented"},
        "languages": "EN / SV",
    }
    page.update(over)
    return "Here is the page:\n" + json.dumps(page)


def test_v_draft_candidate_job_writes_a_grounded_inert_draft():
    db = MemoryDb()
    aid = str(uuid.uuid4()); db.agent_numbers[aid] = 2
    rows = []
    for layer, st, src in N002_MEMORY:
        rows += db.add_memory(aid, [st], layer=layer, source=src)[-1:]
    ids = [r["id"] for r in rows]
    prompts = []

    def drafter(prompt):
        prompts.append(prompt)
        return _candidate_json(ids)

    jid = db.add_job(aid, "draft_candidate")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        reports = hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY, drafter=drafter).run()
    check("V job done", db.jobs[jid]["status"] == "done" and reports[0].action == "drafted", db.jobs[jid])
    check("V prompt grounded with ids, third person, no em dashes asked", all(f"[{i}]" in prompts[0] for i in ids)
          and "third person" in prompts[0] and "Never use em dashes" in prompts[0] and "where they are based" in prompts[0])
    cands = db.candidates
    check("V one draft, version 1, never published by the engine", len(cands) == 1 and cands[0]["state"] == "draft"
          and cands[0]["version"] == 1 and cands[0]["content"] == "")
    page = cands[0]["page"]
    check("V ungrounded chapter dropped, years normalised", [c["company"] for c in page["chapters"]] == ["Northwind", "Klarna"]
          and page["chapters"][1]["years"] == "{2018-22}")
    check("V ungrounded trusted-with dropped", [t["word"] for t in page["trusted_with"]] == ["The first hire"])
    check("V nothing invented survives: no references, no own words, no links, no name",
          page["references"] == [] and page["own_words"] == "" and page["links"] == {} and page["name"] == [])
    check("V never open_to", "open_to" not in page)
    check("V line begins as the dossier", page["line"].startswith("This is the candidate I work for."))
    check("V provenance receipt", page["provenance"]["drafted_by"] == "engine" and len(page["provenance"]["candidate_context_hash"]) == 64)
    check("V console carries no page text", "Northwind" not in out.getvalue() and "Berlin" not in out.getvalue())
    # a second draft retires the first
    db.add_job(aid, "draft_candidate")
    hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY, drafter=drafter).run()
    states = sorted((c["version"], c["state"]) for c in db.candidates)
    check("V redraft retires the old draft", states == [(1, "unpublished"), (2, "draft")], states)


def test_v_draft_candidate_refuses_a_page_with_no_grounded_chapter():
    db = MemoryDb()
    aid = str(uuid.uuid4()); db.agent_numbers[aid] = 2
    for layer, st, src in N002_MEMORY:
        db.add_memory(aid, [st], layer=layer, source=src)
    jid = db.add_job(aid, "draft_candidate")
    hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY,
              drafter=lambda p: _candidate_json(["nope", "nope-2"])).run()
    check("V failed, named", db.jobs[jid]["status"] == "failed" and db.jobs[jid]["error"] == "candidate_draft_failed", db.jobs[jid])
    check("V nothing written", not getattr(db, "candidates", []))


def test_v_sweep_drafts_the_candidate_once():
    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    db = MemoryDb()

    def person(no, *, confirm_all=True):
        aid = str(uuid.uuid4()); db.agent_numbers[aid] = no; db.agent_state[aid] = "at_work"
        for layer, st, src in N002_MEMORY:
            db.add_memory(aid, [st], layer=layer, source=src)
        if not confirm_all:
            db.add_memory(aid, ["Spent a year in Tokyo."], layer="record", source="resume.pdf", provenance="extracted")
        return aid

    settled = person(2)                                                # → queued
    has_page = person(3); db.insert_candidate({"agent_id": has_page, "version": 1, "state": "published", "page": {}})  # → has_page
    nothing = str(uuid.uuid4()); db.agent_numbers[nothing] = 4; db.agent_state[nothing] = "feed_submitted"   # → no_confirmed
    open_recent = person(5, confirm_all=False)
    for r in db.memory[open_recent]:
        r["created_at"] = "2026-09-03T11:55:00Z"                        # → mirror_open
    failed = person(6); db.add_job(failed, "draft_candidate", status="failed", requested_at="2026-09-03T11:59:00Z",
                                   payload={"auto": True, "engine": hr.current_engine_sha()})  # → failed_on_this
    out = hr.Runner(db, collector=lambda _c: []).sweep_candidates(now=now)
    check("V sweep counts", out["agents"] == 5 and out["queued"] == 1 and out["has_page"] == 1
          and out["no_confirmed"] == 1 and out["mirror_open"] == 1 and out["failed_on_this"] == 1, out)
    queued = [j for j in db.jobs.values() if j["type"] == "draft_candidate" and j["status"] == "queued"]
    check("V one job, for the settled person", len(queued) == 1 and queued[0]["agent_id"] == settled)
    again = hr.Runner(db, collector=lambda _c: []).sweep_candidates(now=now)
    check("V second beat queues nothing new", again["queued"] == 0 and again["in_flight"] == 1, again)


def test_v3_a_redraft_keeps_what_the_person_wrote():
    """Live (2026-09-04): Mara typed her name and uploaded her portrait, then a
    redraft would have handed her an empty name and an empty plate. FOOUND
    rewrites only what FOOUND wrote."""
    import candidate_draft as cd
    prev = {"name": ["Mara", "Lindqvist"], "portrait": "https://x/p.jpg", "links": {"linkedin": "https://l/in"},
            "own_words": "", "work": [], "references": [{"name": "A", "quote": "q", "who": "w"}],
            "line": "old line", "chapters": [{"company": "Old"}], "confirmed": ["line", "chapter:0"]}
    fresh = {"name": [], "portrait": "", "links": {}, "own_words": "", "work": [], "references": [],
             "line": "new line", "chapters": [{"company": "New"}]}
    out = cd.carry_own_fields(fresh, prev)
    check("V3 name and portrait carry", out["name"] == ["Mara", "Lindqvist"] and out["portrait"] == "https://x/p.jpg")
    check("V3 links and references carry", out["links"] == {"linkedin": "https://l/in"} and out["references"] == prev["references"])
    check("V3 FOOUND's lines are the new ones", out["line"] == "new line" and out["chapters"][0]["company"] == "New")
    check("V3 confirmations do not carry", "confirmed" not in out)
    check("V3 empty previous fields do not overwrite", out["own_words"] == "" and out["work"] == [])
    check("V3 no previous page is fine", cd.carry_own_fields(fresh, None) is fresh)


def test_v2_a_failure_stands_up_when_the_engine_changes():
    """Live on 2026-09-03: No.002's Candidate draft failed on a token budget;
    the budget was fixed hours later and nothing retried, because she had
    confirmed nothing new. A failure stands down against the engine that
    made it, not forever."""
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)

    def failed_person(engine):
        db = MemoryDb()
        aid = str(uuid.uuid4()); db.agent_numbers[aid] = 2; db.agent_state[aid] = "at_work"
        for layer, st, src in N002_MEMORY:
            db.add_memory(aid, [st], layer=layer, source=src)
        db.add_job(aid, "draft_candidate", status="failed", requested_at="2026-09-03T17:07:00Z",
                   payload=({"auto": True, "engine": engine} if engine else {"auto": True}))
        return db, aid

    real_sha = hr.current_engine_sha
    hr.current_engine_sha = lambda: "bbbb222"
    try:
        db, _ = failed_person("bbbb222")
        same = hr.Runner(db, collector=lambda _c: []).sweep_candidates(now=now)
        check("V2 the same engine stands down", same["queued"] == 0 and same["failed_on_this"] == 1, same)

        db, _ = failed_person("aaaa111")
        moved = hr.Runner(db, collector=lambda _c: []).sweep_candidates(now=now)
        check("V2 a newer engine tries again", moved["queued"] == 1 and moved["failed_on_this"] == 0, moved)
        job = [j for j in db.jobs.values() if j["status"] == "queued"][0]
        check("V2 the new attempt records its engine", job["payload"].get("engine") == "bbbb222", job["payload"])

        db, _ = failed_person(None)
        older = hr.Runner(db, collector=lambda _c: []).sweep_candidates(now=now)
        check("V2 an attempt from before the rule tries once", older["queued"] == 1, older)
    finally:
        hr.current_engine_sha = real_sha

    # with no engine known at all, the old behaviour stands: one attempt, then down
    hr.current_engine_sha = lambda: "unknown"
    try:
        db, _ = failed_person("aaaa111")
        unknown = hr.Runner(db, collector=lambda _c: []).sweep_candidates(now=now)
        check("V2 an unknown engine never loops", unknown["queued"] == 0 and unknown["failed_on_this"] == 1, unknown)
    finally:
        hr.current_engine_sha = real_sha


def test_w_a_brief_in_force_earns_its_edition_today():
    """Mara's case (2026-09-03): she confirmed Brief v3 while already at work,
    with today's edition made from v2. "FOOUND is at work from it" must be
    true today: compile queues the edition; the hunt rewrites the day's
    edition from the Brief in force; a second job from the same Brief is a
    no-op; an agent not at work queues nothing."""
    db = MemoryDb()
    aid = _n002(db); db.agent_state[aid] = "at_work"
    old_id = str(uuid.uuid4())
    db.editions.append({"id": old_id, "agent_id": aid, "edition_date": FROZEN_TODAY.isoformat(),
                        "brief_version": 1, "payload": {"seats": []}, "html": "<p>v1</p>", "outcome": "empty"})
    # the 013 door queued compile_brief for the Brief now in force (v2)
    db.add_job(aid, "compile_brief", payload={"brief_version": 2})
    hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY).run()
    q = [j for j in db.jobs.values() if j["agent_id"] == aid and j["type"] == "first_edition" and j["status"] == "queued"]
    check("W compile queued the edition for the Brief in force",
          len(q) == 1 and q[0]["payload"] == {"brief_version": 2, "reason": "brief_in_force"})
    raw = [{"title": "Head of Design", "company": "Fabric", "location": "Berlin", "url": "https://fabric/1"}]
    r = hr.Runner(db, collector=lambda _c: raw, today=FROZEN_TODAY, fetch_jd=lambda _u: "",
                  score=_score_by_title({"Head of Design": (84, "why", "pause")}, default=(66, "w", "p")),
                  profile=None, state_loader=lambda _a, _n: None,
                  deep=lambda *_a, **_k: dict(DEEP_STUB), brief_line_fn=lambda *_a, **_k: "Fabric leads.")
    reports = r.run()
    ed = [e for e in db.editions if e["agent_id"] == aid]
    check("W one edition for the day, rewritten from v2",
          len(ed) == 1 and ed[0]["id"] == old_id and ed[0]["brief_version"] == 2 and ed[0]["outcome"] == "seats"
          and reports[0].action == "edition" and reports[0].detail.get("replaced_brief_version") == 1)
    # the same Brief again today: nothing to do
    db.add_job(aid, "first_edition", payload={"brief_version": 2, "daily": True})
    rep = r.run()
    check("W same Brief, same day: noop", rep[0].action == "noop" and len([e for e in db.editions if e["agent_id"] == aid]) == 1)
    # not at work: compile queues nothing
    b = _n002(db); db.agent_state[b] = "mirror_ready"
    db.add_job(b, "compile_brief", payload={"brief_version": 2})
    hr.Runner(db, collector=lambda _c: [], today=FROZEN_TODAY).run()
    check("W not at work: no edition queued",
          not [j for j in db.jobs.values() if j["agent_id"] == b and j["type"] == "first_edition"])


def test_x_a_judgment_is_remembered_across_days():
    """Daily editions must not re-judge the same posting every morning. A
    posting judged within 14 days keeps its fit, why and pause with no model
    call; a second look the person asked for re-reads it; an old judgment
    is re-read; the payload carries judged_on so tomorrow can remember."""
    from datetime import timedelta
    db = MemoryDb()
    aid = _n002(db); db.agent_state[aid] = "at_work"
    raw = [{"title": "Head of Design", "company": "Fabric", "location": "Berlin", "url": "https://fabric/1"},
           {"title": "Design Director", "company": "Orbit", "location": "Amsterdam", "url": "https://orbit/1"}]
    calls = []
    def score(table):
        base = _score_by_title(table, default=(66, "w", "p"))
        def f(a, p, job, jd):
            calls.append(job["title"])
            return base(a, p, job, jd)
        return f
    day1 = FROZEN_TODAY
    r1 = hr.Runner(db, collector=lambda _c: raw, today=day1, fetch_jd=lambda _u: "",
                   score=score({"Head of Design": (84, "why1", "pause1"), "Design Director": (40, "w1", "no1")}),
                   profile=None, state_loader=lambda _a, _n: None,
                   deep=lambda *_a, **_k: dict(DEEP_STUB), brief_line_fn=lambda *_a, **_k: "x")
    db.add_job(aid, "first_edition", payload={"brief_version": 2, "daily": True})
    r1.run()
    e1 = [e for e in db.editions if e["agent_id"] == aid][0]
    check("X day 1: two model reads, judged_on stamped",
          calls == ["Head of Design", "Design Director"] and e1["payload"]["judged_on"] == day1.isoformat()
          and e1["payload"]["seats"][0]["judged_on"] == day1.isoformat()
          and e1["payload"]["refused"][0]["judged_on"] == day1.isoformat())
    # day 2: the judge would now say something else; FOOUND remembers instead
    calls.clear()
    day2 = day1 + timedelta(days=1)
    r2 = hr.Runner(db, collector=lambda _c: raw, today=day2, fetch_jd=lambda _u: "",
                   score=score({"Head of Design": (55, "why2", "pause2"), "Design Director": (90, "w2", "no2")}),
                   profile=None, state_loader=lambda _a, _n: None,
                   deep=lambda *_a, **_k: dict(DEEP_STUB), brief_line_fn=lambda *_a, **_k: "x")
    db.add_job(aid, "first_edition", payload={"brief_version": 2, "daily": True})
    r2.run()
    e2 = [e for e in db.editions if e["agent_id"] == aid and e["edition_date"] == day2.isoformat()][0]
    seat = e2["payload"]["seats"][0]
    check("X day 2: no model reads; the seat keeps its judgment",
          calls == [] and seat["company"] == "Fabric" and seat["fit"] == 84 and seat["ai_why"] == "why1"
          and seat["judged_on"] == day1.isoformat()
          and e2["payload"]["counts"]["model_reads_remembered"] == 2
          and e2["payload"]["counts"]["model_reads_attempted"] == 0)
    # day 16: old enough to re-read
    calls.clear()
    day16 = day1 + timedelta(days=16)
    r3 = hr.Runner(db, collector=lambda _c: raw, today=day16, fetch_jd=lambda _u: "",
                   score=score({"Head of Design": (70, "why3", "pause3"), "Design Director": (41, "w3", "no3")}),
                   profile=None, state_loader=lambda _a, _n: None,
                   deep=lambda *_a, **_k: dict(DEEP_STUB), brief_line_fn=lambda *_a, **_k: "x")
    db.add_job(aid, "first_edition", payload={"brief_version": 2, "daily": True})
    r3.run()
    e3 = [e for e in db.editions if e["agent_id"] == aid and e["edition_date"] == day16.isoformat()][0]
    check("X day 16: re-read, re-stamped", len(calls) == 2 and e3["payload"]["seats"][0]["fit"] == 70
          and e3["payload"]["seats"][0]["judged_on"] == day16.isoformat())
    # a different Brief in force: yesterday's judgments do not carry
    rem = hr.remembered_judgments([e3["payload"]], day16, compile_hash="not-that-brief")
    same = hr.remembered_judgments([e3["payload"]], day16, compile_hash=e3["payload"]["compiled_config_hash"])
    check("X a new Brief re-judges; the same Brief remembers", rem == {} and len(same) == 2)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        print(t.__name__)
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
    print()
    if failed:
        print(f"GATE: FAIL — {failed}/{len(tests)} hunt tests failed.")
        return 1
    print(f"GATE: PASS — {len(tests)} hunt tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
