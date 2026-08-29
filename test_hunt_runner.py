"""FOOUND Hunt Runner v1 — focused tests. No network.

H1  incomplete Brief → BLOCKED / not_ready
H2  complete Brief → ready + compiled_config
H3  first_edition empty → persisted empty edition, job done
H4  same-day second first_edition → no-op, no rewrite
H5  payload market-history fields present
H6  subjects with other titles still compile (labels are not architecture)
CQ  compile quality: specimen 5d260731 families; no punctuation junk;
    deterministic; fictional Brief keeps its own titles; ready needs
    families + locations
H7  never writes 'limited'; never infers ready from at_work
H8  empty html has no DUMMY ROLE and no dummy seats
H9  compile does not read Memory; hunt does not touch market_seen / docs
H10 commission recovery predicate: insert once, then no-op
H11 judgment: stronger-fit beats weaker-fit; not cap-alone
H12 role_key precedence + source-qualified id + gh_jid identity
H13 adapter isolation smoke: SCRAPERS import without publisher
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date

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

    def add_brief(self, agent_id, content, compiled_config=None,
                  readiness=None, version=1, state="active"):
        bid = str(uuid.uuid4())
        self.briefs[bid] = {
            "id": bid, "agent_id": agent_id, "version": version,
            "state": state, "content": content,
            "compiled_config": compiled_config, "readiness": readiness,
        }
        return bid

    def add_job(self, agent_id, type, status="queued", payload=None):
        jid = str(uuid.uuid4())
        self.jobs[jid] = {
            "id": jid, "agent_id": agent_id, "type": type,
            "status": status, "payload": payload or {},
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
        self.editions.append(dict(row))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


def check(name, cond, detail=""):
    if not cond:
        raise AssertionError(f"FAIL {name}: {detail}")
    print(f"  ok  {name}")


# ---------------------------------------------------------------------------
# H1 / H2 compile + readiness
# ---------------------------------------------------------------------------

def test_h1_incomplete_brief_blocked():
    cfg = hr.compile_from_content(INCOMPLETE)
    check("H1 not_ready", hr.readiness_of(cfg) == "not_ready")
    check("H1 never limited", hr.readiness_of(cfg) != "limited")
    check("H1 reasons present", len(cfg["readiness_reasons"]) >= 1)
    check("H1 no_accepted_locations",
          "no_accepted_locations" in cfg["readiness_reasons"])
    check("H1 architecture note", "temporary" in cfg["readiness_architecture"])


def test_h2_complete_brief_ready():
    cfg = hr.compile_from_content(COMPLETE)
    check("H2 ready", hr.readiness_of(cfg) == "ready")
    check("H2 include", any("creative director" in x or "head of" in x
                            for x in cfg["include"]))
    check("H2 locations", any("nyc" in x or "california" in x or "london" in x
                              for x in cfg["accepted_locations"]))
    check("H2 reasons empty when ready", cfg["readiness_reasons"] == [])
    check("H2 seat_cap default", cfg["seat_cap"] == 5)
    for key in ("subjects_used", "include", "exclude_type",
                "accepted_locations", "search_queries", "seat_cap",
                "compiled_at", "engine_sha", "readiness_reasons",
                "readiness_architecture"):
        check(f"H2 field {key}", key in cfg)
    persisted = hr.persistable_compiled(cfg)
    check("H2 persist drops internal", "_readiness" not in persisted)


def test_h2b_structured_content():
    cfg = hr.compile_from_content(STRUCTURED)
    check("H2b ready", hr.readiness_of(cfg) == "ready")
    check("H2b include", cfg["include"] == ["creative director"])
    check("H2b locations", cfg["accepted_locations"] == ["remote"])
    check("H2b seat_cap", cfg["seat_cap"] == 3)


def test_h2c_empty_content():
    cfg = hr.compile_from_content({})
    check("H2c not_ready", hr.readiness_of(cfg) == "not_ready")
    check("H2c no authority", "no_usable_hunt_authority" in cfg["readiness_reasons"])


# ---------------------------------------------------------------------------
# H6 other titles / no hard-coded architecture
# ---------------------------------------------------------------------------

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


def test_compile_specimen_5d260731_families():
    cfg = hr.compile_from_content(SPECIMEN_5d260731)
    check("CQ specimen ready", hr.readiness_of(cfg) == "ready")
    check("CQ specimen families", cfg["search_queries"] == SPECIMEN_FAMILIES)
    check("CQ has Creative Director", "creative director" in cfg["search_queries"])
    check("CQ no bare cd family",
          "cd" not in cfg["search_queries"] and "cd" not in cfg["include"])
    check("CQ families are include",
          all(f in cfg["include"] for f in SPECIMEN_FAMILIES))
    check("CQ no invented Global Creative Director",
          "global creative director" not in cfg["search_queries"]
          and "global creative director" not in cfg["include"])
    check("CQ ECD not expanded",
          "executive creative director" not in cfg["search_queries"])
    check("CQ Group CD stays abbreviated", "group cd" in cfg["search_queries"])
    check("CQ Executive CD stays abbreviated",
          "executive cd" in cfg["search_queries"])
    check("CQ move prose not queries",
          not any(x in cfg["search_queries"] for x in (
              "across markets",
              "build or transform that function",
              "not inherit a finished one",
          )))
    locs = cfg["accepted_locations"]
    check("CQ locations intact",
          locs == ["nyc", "california", "remote us", "london", "paris"]
          or all(x in locs for x in ("nyc", "california", "london", "paris")))
    check("CQ remote US kept", any("remote" in x for x in locs))


def test_compile_specimen_5d260731_no_punctuation_junk():
    cfg = hr.compile_from_content(SPECIMEN_5d260731)
    junk = {
        "brand leadership (cd",
        "creative)",
        "vp brand",
        "cd",
    }
    for term in cfg["include"] + cfg["search_queries"]:
        check(f"CQ not junk {term!r}", term not in junk)
        check(f"CQ balanced punct {term!r}",
              term.count("(") == term.count(")"))
        check(f"CQ no leading cut {term!r}",
              not term.startswith(("(", ")", "/")))
        check(f"CQ no trailing cut {term!r}",
              not (term.endswith(")") and "(" not in term))
    check("CQ compound slash kept",
          "vp brand/creative" in cfg["search_queries"])
    check("CQ parenthetical concept intact or absent",
          all("(" not in t or ")" in t for t in cfg["include"]))


def test_compile_specimen_5d260731_include_coherent():
    cfg = hr.compile_from_content(SPECIMEN_5d260731)
    check("CQ include exact", cfg["include"] == SPECIMEN_INCLUDE)
    check("CQ include no unmistakably scrap",
          "and unmistakably themselves" not in cfg["include"])
    check("CQ include no conjunction scraps",
          not any(t.startswith(("and ", "or ", "but ", "not "))
                  for t in cfg["include"]))
    check("CQ queries stay seven families",
          cfg["search_queries"] == SPECIMEN_FAMILIES)
    check("CQ queries have Creative Director",
          "creative director" in cfg["search_queries"])
    check("CQ queries no bare cd", "cd" not in cfg["search_queries"])


def test_compile_specimen_5d260731_deterministic():
    a = hr.compile_from_content(SPECIMEN_5d260731)
    b = hr.compile_from_content(SPECIMEN_5d260731)
    check("CQ same families twice", a["search_queries"] == b["search_queries"])
    check("CQ minus compiled_at identical",
          _without_compiled_at(a) == _without_compiled_at(b))
    check("CQ compiled_at may differ or match",
          isinstance(a["compiled_at"], str) and isinstance(b["compiled_at"], str))


def test_compile_fictional_brief_own_families():
    cfg = hr.compile_from_content(FICTIONAL_OTHER_ROLES)
    check("CQ fictional ready", hr.readiness_of(cfg) == "ready")
    check("CQ fictional families", cfg["search_queries"] == FICTIONAL_FAMILIES)
    check("CQ fictional not specimen seven",
          set(cfg["search_queries"]) != set(SPECIMEN_FAMILIES))
    for locked in SPECIMEN_FAMILIES:
        check(f"CQ fictional lacks {locked!r}",
              locked not in cfg["search_queries"])
    check("CQ fictional move prose not queries",
          not any(x in cfg["search_queries"] for x in (
              "across markets",
              "build or transform that function",
              "not inherit a finished one",
          )))


def test_compile_ready_requires_families_and_locations():
    only_move = hr.compile_from_content(INCOMPLETE)
    check("CQ incomplete not_ready", hr.readiness_of(only_move) == "not_ready")
    check("CQ incomplete has reasons", len(only_move["readiness_reasons"]) >= 1)
    check("CQ incomplete no_accepted_locations",
          "no_accepted_locations" in only_move["readiness_reasons"])
    locs_only = hr.compile_from_content({
        "subjects": [{"title": "Geography", "text": "NYC, London"}]
    })
    check("CQ locations-only not_ready", hr.readiness_of(locs_only) == "not_ready")
    check("CQ locations-only needs families",
          "no_include_terms" in locs_only["readiness_reasons"])
    fams_only = hr.compile_from_content({
        "subjects": [{"title": "Craft", "text": "Design Director"}]
    })
    check("CQ families-only not_ready", hr.readiness_of(fams_only) == "not_ready")
    check("CQ families-only needs locations",
          "no_accepted_locations" in fams_only["readiness_reasons"])
    ready = hr.compile_from_content(SPECIMEN_5d260731)
    check("CQ specimen executable",
          bool(ready["search_queries"]) and bool(ready["accepted_locations"]))
    check("CQ specimen ready reasons empty", ready["readiness_reasons"] == [])


def test_h6b_skip_market_and_avoid():
    content = {
        "subjects": [
            {"title": "MARKET", "text": "creative director, remote"},
            {"title": "AVOID", "text": "intern, contractor"},
            {"title": "READINESS", "text": "ready"},
        ]
    }
    cfg = hr.compile_from_content(content)
    check("H6b skip non-authority chapters", hr.readiness_of(cfg) == "not_ready")
    check("H6b no include from MARKET", cfg["include"] == [])
    check("H6b AVOID off", cfg["exclude_type"] == [])


# ---------------------------------------------------------------------------
# H7 never limited; at_work is not readiness
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# H3 / H4 / H5 first_edition
# ---------------------------------------------------------------------------

def _ready_agent(db, content=None):
    aid = str(uuid.uuid4())
    compiled = hr.compile_from_content(content or COMPLETE)
    assert hr.readiness_of(compiled) == "ready"
    db.add_brief(aid, content or COMPLETE,
                 compiled_config=hr.persistable_compiled(compiled),
                 readiness="ready")
    return aid


def test_h3_empty_edition_is_success():
    db = MemoryDb()
    aid = _ready_agent(db)
    jid = db.add_job(aid, "first_edition", payload={"brief_version": 1})
    today = date(2026, 8, 28)
    runner = hr.Runner(db, collector=lambda _c: [], today=today)
    reports = runner.run()
    check("H3 action edition", reports[0].action == "edition")
    check("H3 seats 0", reports[0].seats == 0)
    check("H3 job done", db.jobs[jid]["status"] == "done")
    check("H3 no job.error", db.jobs[jid].get("error") is None)
    check("H3 one edition", len(db.editions) == 1)
    ed = db.editions[0]
    check("H3 outcome empty", ed["outcome"] == "empty")
    check("H3 payload seats empty", ed["payload"]["seats"] == [])
    check("H3 html empty marker", 'data-edition="empty"' in ed["html"])
    check("H3 html seat-count 0", 'data-seat-count="0"' in ed["html"])
    check("H3 no DUMMY ROLE", "DUMMY ROLE" not in ed["html"])
    check("H3 no dummy seats in json",
          json.loads(ed["html"].split("id=\"foound-seats\">")[1].split("</script>")[0]) == [])


def test_h4_same_day_second_is_noop():
    db = MemoryDb()
    aid = _ready_agent(db)
    today = date(2026, 8, 28)
    j1 = db.add_job(aid, "first_edition")
    runner = hr.Runner(db, collector=lambda _c: [], today=today)
    runner.run()
    html1 = db.editions[0]["html"]
    payload1 = json.dumps(db.editions[0]["payload"], sort_keys=True)
    j2 = db.add_job(aid, "first_edition")
    reports = runner.run()
    check("H4 noop", reports[0].action == "noop")
    check("H4 second job done", db.jobs[j2]["status"] == "done")
    check("H4 still one edition", len(db.editions) == 1)
    check("H4 html unchanged", db.editions[0]["html"] == html1)
    check("H4 payload unchanged",
          json.dumps(db.editions[0]["payload"], sort_keys=True) == payload1)
    check("H4 first still done", db.jobs[j1]["status"] == "done")


def test_h5_market_history_fields():
    db = MemoryDb()
    aid = _ready_agent(db, STRUCTURED)
    today = date(2026, 8, 28)
    raw = [{
        "title": "Creative Director",
        "company": "Acme",
        "location": "Remote",
        "url": "https://example.invalid/role",
        "source": "adapter",
    }]
    prior_key = hr.role_key(raw[0])
    # Prior private edition — personal history only.
    db.editions.append({
        "agent_id": aid,
        "edition_date": "2026-08-01",
        "payload": {
            "seats": [{
                "role_key": prior_key,
                "first_seen": "2026-07-15",
            }]
        },
        "html": "<html></html>",
        "outcome": "seats",
    })
    db.add_job(aid, "first_edition")
    runner = hr.Runner(db, collector=lambda _c: raw, today=today)
    reports = runner.run()
    check("H5 edition", reports[0].action == "edition")
    check("H5 one new seat", reports[0].seats == 1)
    # today's row is the second edition
    today_ed = [e for e in db.editions if e["edition_date"] == "2026-08-28"][0]
    payload = today_ed["payload"]
    check("H5 engine_sha", "engine_sha" in payload)
    check("H5 compiled_config_hash", len(payload.get("compiled_config_hash") or "") == 64)
    seat = payload["seats"][0]
    for key in ("role_key", "first_seen", "previously_seen", "source",
                "new_or_resurfaced", "survived_because"):
        check(f"H5 field {key}", key in seat)
    check("H5 role_key url precedence", seat["role_key"] == prior_key)
    check("H5 role_key is url:", seat["role_key"].startswith("url:"))
    check("H5 previously_seen", seat["previously_seen"] is True)
    check("H5 first_seen from prior", seat["first_seen"] == "2026-07-15")
    check("H5 resurfaced", seat["new_or_resurfaced"] == "resurfaced")
    check("H5 survived_because list", isinstance(seat["survived_because"], list))
    check("H5 html has seat shape", 'data-handle=' in today_ed["html"])
    check("H5 no DUMMY ROLE", "DUMMY ROLE" not in today_ed["html"])


def test_h5b_new_seat_history():
    compiled = hr.compile_from_content(STRUCTURED)
    seats = hr.filter_and_cap([{
        "title": "Creative Director", "company": "Nova",
        "location": "Remote", "url": "",
    }], compiled)
    seats = hr.attach_market_fields(seats, {}, date(2026, 8, 28))
    check("H5b new", seats[0]["new_or_resurfaced"] == "new")
    check("H5b not previously", seats[0]["previously_seen"] is False)
    check("H5b first_seen today", seats[0]["first_seen"] == "2026-08-28")
    payload = hr.build_payload(seats, compiled, "abc")
    check("H5b payload history keys",
          set(payload["seats"][0]) >= {
              "role_key", "first_seen", "previously_seen",
              "source", "new_or_resurfaced", "survived_because",
          })


# ---------------------------------------------------------------------------
# Fail-closed first_edition
# ---------------------------------------------------------------------------

def test_first_edition_fail_closed():
    db = MemoryDb()
    aid = str(uuid.uuid4())
    db.add_brief(aid, COMPLETE, compiled_config=None, readiness=None)
    jid = db.add_job(aid, "first_edition")
    hr.Runner(db, collector=lambda _c: [], today=date(2026, 8, 28)).run()
    check("no_compiled_config", db.jobs[jid]["error"] == "no_compiled_config")
    check("no edition on fail", db.editions == [])

    aid2 = str(uuid.uuid4())
    compiled = hr.compile_from_content(INCOMPLETE)
    db.add_brief(aid2, INCOMPLETE,
                 compiled_config=hr.persistable_compiled(compiled),
                 readiness="not_ready")
    jid2 = db.add_job(aid2, "first_edition")
    hr.Runner(db, collector=lambda _c: [], today=date(2026, 8, 28)).run()
    check("readiness_blocked", db.jobs[jid2]["error"] == "readiness_blocked")

    aid3 = str(uuid.uuid4())
    jid3 = db.add_job(aid3, "first_edition")
    hr.Runner(db, collector=lambda _c: [], today=date(2026, 8, 28)).run()
    check("no_active_brief", db.jobs[jid3]["error"] == "no_active_brief")


def test_refresh_compiles_if_missing():
    db = MemoryDb()
    aid = str(uuid.uuid4())
    db.add_brief(aid, COMPLETE, compiled_config=None, readiness=None)
    jid = db.add_job(aid, "refresh_readiness")
    reports = hr.Runner(db, collector=lambda _c: []).run()
    check("refresh action", reports[0].action == "refreshed")
    check("refresh ready", reports[0].readiness == "ready")
    brief = db.active_brief(aid)
    check("refresh wrote config", bool(brief["compiled_config"]))
    check("refresh wrote ready", brief["readiness"] == "ready")
    check("refresh job done", db.jobs[jid]["status"] == "done")


def test_seat_cap():
    compiled = hr.compile_from_content({
        "include": ["director"],
        "accepted_locations": ["remote"],
        "seat_cap": 2,
    })
    raw = [
        {"title": "Director A", "company": "A", "location": "Remote"},
        {"title": "Director B", "company": "B", "location": "Remote"},
        {"title": "Director C", "company": "C", "location": "Remote"},
    ]
    seats = hr.judge_seats(raw, compiled)
    check("cap is ceiling after judgment", len(seats) == 2)


# ---------------------------------------------------------------------------
# H9 boundaries
# ---------------------------------------------------------------------------

def test_h9_boundaries():
    src = open(hr.__file__, encoding="utf-8").read()
    check("H9 no market_seen access",
          not re.search(r"""['\"]market_seen['\"]|/market_seen|from market_seen""", src))
    check("H9 no agent_config access",
          not re.search(r"""['\"]agent_config['\"]|/agent_config|from agent_config""", src))
    check("H9 no publish_shortlist call",
          not re.search(r"publish_shortlist\s*\(", src))
    check("H9 no docs/ write",
          not re.search(r"""open\([^)]*docs/|['\"]docs/""", src))
    check("H9 DUMMY ROLE rejected at persist",
          "if \"DUMMY ROLE\" in html_doc" in src)
    check("H9 memory not imported as authority",
          "from memory" not in src and "table memory" not in src)
    db = MemoryDb()
    aid = _ready_agent(db)
    db.add_job(aid, "first_edition")
    hr.Runner(db, collector=lambda _c: [], today=date(2026, 8, 28)).run()
    check("H9 no memory reads", db.memory_reads == 0)
    check("H9 no market_seen reads", db.market_seen_reads == 0)
    check("H9 no publish", db.publish_calls == 0)


def test_h8_html_seat_shape():
    html_doc = hr.render_edition_html([{
        "role_key": "cd|acme",
        "handle": "Acme",
        "line": "Creative Director — Remote",
        "title": "Creative Director",
        "company": "Acme",
        "location": "Remote",
    }])
    check("H8 no dummy", "DUMMY ROLE" not in html_doc)
    check("H8 seats json", 'id="foound-seats"' in html_doc)
    blob = html_doc.split('id="foound-seats">')[1].split("</script>")[0]
    seats = json.loads(blob)
    check("H8 id/handle/line", set(seats[0]) == {"id", "handle", "line"})
    empty = hr.render_edition_html([])
    check("H8 empty no dummy", "DUMMY ROLE" not in empty)
    check("H8 empty array",
          json.loads(empty.split('id="foound-seats">')[1].split("</script>")[0]) == [])


# ---------------------------------------------------------------------------
# H10 commission recovery predicate (mirrors 011; SQL is source of truth)
# ---------------------------------------------------------------------------

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
    jid = db.add_job(aid, "compile_brief")
    reports = hr.Runner(db, collector=lambda _c: []).run()
    check("compile action", reports[0].action == "compiled")
    check("compile ready", reports[0].readiness == "ready")
    check("compile job done", db.jobs[jid]["status"] == "done")
    brief = db.active_brief(aid)
    cfg = brief["compiled_config"]
    check("compile persisted include", bool(cfg["include"]))
    check("compile persisted note", "temporary" in cfg["readiness_architecture"])


def test_judgment_stronger_beats_weaker():
    """Weaker-fit listed first; seat_cap=1. Filter/cap-alone would keep
    the first eligible. Judgment must keep the stronger Brief fit."""
    compiled = {
        "include": ["creative director", "head of creative"],
        "exclude_type": ["intern"],
        "accepted_locations": ["new york", "remote"],
        "seat_cap": 1,
    }
    raw = [
        {"title": "Junior Creative Director", "company": "FirstCo",
         "location": "Remote", "url": "https://example.invalid/weak"},
        {"title": "Head of Creative", "company": "BestCo",
         "location": "New York", "url": "https://example.invalid/strong"},
        {"title": "Creative Director Intern", "company": "NoCo",
         "location": "New York", "url": "https://example.invalid/intern"},
    ]
    seats = hr.judge_seats(raw, compiled)
    check("J1 one seat", len(seats) == 1)
    check("J1 stronger wins", seats[0]["company"] == "BestCo")
    check("J1 not first-listed weaker", seats[0]["company"] != "FirstCo")
    reasons = seats[0]["survived_because"]
    check("J1 title_fit named", "title_fit" in reasons)
    check("J1 location_fit named", "location_fit" in reasons)
    check("J1 exclude_cleared named", "exclude_cleared" in reasons)
    check("J1 ranked_above_peers", "ranked_above_peers" in reasons)
    check("J1 not filter-only labels",
          reasons != ["compiled_include", "within_seat_cap"])
    check("J1 intern excluded",
          all(s["company"] != "NoCo" for s in seats))


def test_judgment_not_cap_alone():
    compiled = {
        "include": ["director"],
        "exclude_type": [],
        "accepted_locations": ["london", "remote"],
        "seat_cap": 1,
    }
    # First row is eligible but weaker (remote + generic). Second is stronger.
    raw = [
        {"title": "Associate Director", "company": "Early",
         "location": "Remote", "posting_id": "early-1"},
        {"title": "Director", "company": "Later",
         "location": "London", "posting_id": "later-1"},
    ]
    seats = hr.judge_seats(raw, compiled)
    check("J2 judgment picks later stronger", seats[0]["company"] == "Later")
    first_n = []
    for job in raw:
        if hr.passes_title(compiled, job["title"]) and hr.passes_location(
                compiled, job["location"]):
            first_n.append(job)
            if len(first_n) >= 1:
                break
    check("J2 cap-alone would have kept Early", first_n[0]["company"] == "Early")
    check("J2 they differ", seats[0]["company"] != first_n[0]["company"])


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


def test_role_key_history_title_tweak_same_id():
    compiled = {
        "include": ["creative director"],
        "accepted_locations": ["remote"],
        "seat_cap": 5,
        "exclude_type": [],
    }
    prior = [{"role_key": "id:greenhouse:board-7", "first_seen": "2026-07-01"}]
    hist = hr.personal_history([{"seats": prior}])
    today = [{
        "title": "Creative Director, Brand",
        "company": "Acme",
        "location": "Remote",
        "posting_id": "board-7",
        "source": "greenhouse",
    }]
    seats = hr.attach_market_fields(hr.judge_seats(today, compiled), hist,
                                    date(2026, 8, 28))
    check("RKh same id resurfaced", seats[0]["previously_seen"] is True)
    check("RKh not new", seats[0]["new_or_resurfaced"] == "resurfaced")
    check("RKh first_seen kept", seats[0]["first_seen"] == "2026-07-01")
    check("RKh source-qualified key", seats[0]["role_key"] == "id:greenhouse:board-7")


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


def main():
    tests = [v for k, v in globals().items()
             if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        print(fn.__name__)
        try:
            fn()
        except Exception as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print()
    if failed:
        print(f"GATE: FAIL — {failed}/{len(tests)} hunt tests failed.")
        raise SystemExit(1)
    print(f"GATE: PASS — {len(tests)} hunt tests.")


if __name__ == "__main__":
    main()
