"""
FOOUND — per-agent configuration.

Everything here is DATA ABOUT ONE PERSON. It was extracted verbatim from
job_alerts.py so that №001 behaves identically after the refactor.

Next step (before №002): `load_agent_config` reads these fields from the
`agent_config` table instead of this file. The dataclass is the contract;
where the values come from is an implementation detail. Nothing below should
ever move back into the execution path.
"""
from dataclasses import dataclass, field


@dataclass
class AgentConfig:
    agent_no: int
    agent_id: str
    name: str                       # greeting / salutation
    recipient_email: str            # delivery target
    profile_path: str               # CANDIDATE substrate (file today, row later)
    output_dir: str                 # where the edition is written
    edition_url: str                # deep link used in delivery
    publish_public: bool            # №001 showroom only; false for every other agent

    # --- AGENT LAYER: what this agent considers worth judging ---
    include: list = field(default_factory=list)
    exclude_type: list = field(default_factory=list)
    accepted_locations: list = field(default_factory=list)
    priority_companies: set = field(default_factory=set)

    # --- MARKET LAYER: what this agent needs the collector to gather ---
    # Kept separate deliberately. If one agent's terms decide what the shared
    # collector retrieves, every other agent silently inherits that agent's
    # recall ceiling — it can only ever judge roles №001 caused to be fetched.
    # The collector runs market_query_union() across all active agents.
    search_queries: list = field(default_factory=list)
    market_sources: list = field(default_factory=list)   # empty = all sources

    # --- presentation ---
    manual_jobs: list = field(default_factory=list)
    evidence_map: list = field(default_factory=list)
    email_footer: list = field(default_factory=list)

    notion_db_id: str = ""

    def validate(self) -> "AgentConfig":
        """Fail loudly at the boundary rather than subtly during a run."""
        problems = []
        if not self.name:
            problems.append("name is empty (greeting would read 'Good morning, .')")
        if not self.include:
            problems.append("include is empty (agent would match no titles)")
        if not self.accepted_locations:
            problems.append("accepted_locations is empty (agent would match no roles)")
        if not self.output_dir:
            problems.append("output_dir is empty")
        if self.publish_public and self.agent_id != "001":
            problems.append(
                f"agent {self.agent_id} has publish_public=True — only the "
                "showroom agent may write to a public path")
        if problems:
            raise ValueError(f"invalid AgentConfig for {self.agent_id}: "
                             + "; ".join(problems))
        return self

    @classmethod
    def from_row(cls, row: dict, agent_no: int, agent_id: str) -> "AgentConfig":
        """database row -> validated AgentConfig. The ONLY DB->execution path.

        Nothing downstream may read config fields from the database directly;
        if a value belongs to one agent it arrives here or not at all.
        """
        return cls(
            agent_no=agent_no,
            agent_id=agent_id,
            name=row["display_name"],
            recipient_email=row.get("recipient_email", ""),
            profile_path=row.get("profile_path", "profile.md"),
            output_dir=row.get("output_dir", "editions"),
            edition_url=row.get("edition_url", ""),
            publish_public=bool(row.get("publish_public", False)),
            include=list(row.get("include") or []),
            exclude_type=list(row.get("exclude_type") or []),
            accepted_locations=list(row.get("accepted_locations") or []),
            priority_companies=set(row.get("priority_companies") or []),
            search_queries=list(row.get("search_queries") or []),
            market_sources=list(row.get("market_sources") or []),
            manual_jobs=list(row.get("manual_jobs") or []),
            evidence_map=[tuple(x) for x in (row.get("evidence_map") or [])],
            email_footer=list(row.get("email_footer") or []),
        ).validate()

    def to_row(self) -> dict:
        """Inverse of from_row — used to seed the table from the bootstrap."""
        return {
            "display_name": self.name,
            "recipient_email": self.recipient_email,
            "profile_path": self.profile_path,
            "output_dir": self.output_dir,
            "edition_url": self.edition_url,
            "publish_public": self.publish_public,
            "include": list(self.include),
            "exclude_type": list(self.exclude_type),
            "accepted_locations": list(self.accepted_locations),
            "priority_companies": sorted(self.priority_companies),
            "search_queries": list(self.search_queries),
            "market_sources": list(self.market_sources),
            "manual_jobs": list(self.manual_jobs),
            "evidence_map": [list(x) for x in self.evidence_map],
            "email_footer": list(self.email_footer),
        }


BOOTSTRAP_001 = AgentConfig(
    agent_no=1,
    agent_id="001",
    name="Carlos",
    recipient_email="",              # from RECIPIENT_EMAIL env for now
    profile_path="profile.md",
    output_dir="docs",
    # The custom domain, never the github.io URL: a repo rename kills the
    # github.io address (learned Aug 18) but foound.ai survives anything.
    edition_url='https://foound.ai/',
    publish_public=True,
    include=[
    "creative director",
    "director of creative", "director, creative",
    "head of creative", "creative lead",
    "head of brand", "brand director",
    "director of brand", "director, brand", "brand lead",
    "vp of creative", "vp, creative",
    "vp of brand", "vp, brand",
    "brand marketing director",
    "executive creative",
    "brand experience",
    "design director",   # studio-world equivalent of CD (Koto, Collins, Pentagram-tier)
],
    exclude_type=["intern", "internship", "part-time", "part time", "contractor"],
    accepted_locations=[
    # US — California
    "california", "san francisco", "bay area", "los angeles", "culver city",
    "santa monica", "burbank", "mountain view", "menlo park", "palo alto",
    "cupertino", "sunnyvale", "san jose", "santa clara", "los gatos",
    # US — other hubs
    "new york", "nyc", "brooklyn", "austin", "chicago",
    "seattle", "bellevue", "redmond",
    "boston", "cambridge", "pittsburgh", "miami", "denver", "boulder",
    "washington", "atlanta", "portland",
    # US — general / remote
    "united states", "usa", "us", "remote", "north america",
    # Canada (major AI hubs — Cohere is Toronto-based)
    "toronto", "montreal", "vancouver",
    # Europe
    "london", "paris", "dublin", "amsterdam", "berlin", "munich",
    "zurich", "geneva", "stockholm", "copenhagen", "oslo", "helsinki",
    "madrid", "barcelona", "lisbon", "milan", "vienna", "brussels",
    "united kingdom", "uk", "england", "france", "germany", "ireland",
    "netherlands", "spain", "portugal", "italy", "switzerland",
    "sweden", "denmark", "norway", "finland", "austria", "belgium",
    "europe", "emea",
],
    search_queries=["creative director", "brand", "creative lead"],
    priority_companies={"Apple"},
    manual_jobs=[
    {
        "title":     "Creative Director, A/V, Apple TV+ Marketing",
        "company":   "Apple",
        "location":  "",
        "url":       "https://jobs.apple.com/en-us/details/200666406-0670/creative-director-a-v-apple-tv-marketing?team=MKTG",
        "posted_at": None,
        "added_by":  "Carlos — 2026-08-11",
    },
],
    evidence_map=[("MAL", "/candidate/#c-mal"), ("Airbnb", "/candidate/#c-airbnb"),
               ("Publicis", "/candidate/#c-publicis"), ("Ogilvy", "/candidate/#c-ogilvy"),
               ("AKQA", "/candidate/#c-akqa")],
    email_footer=[
        "Filters: Creative Director / Head of Brand / Creative & Brand leadership",
        "Locations: US hubs (CA, NYC, Austin, Chicago, Seattle, Boston, Miami...), Toronto, Europe + Remote",
        "Not auto-checked (visit manually): Meta, Google, Microsoft, Midjourney, Notion, "
        "W+K, Droga5, Mother, Mischief, GUT, Uncommon, Buck, COLLINS, Pentagram, Porto Rocha, Instrument, ManvsMachine",
    ],
)

_REGISTRY = {"001": BOOTSTRAP_001}


def load_agent_config(agent_id: str = "001") -> AgentConfig:
    """Today: the seeded bootstrap. Next: a row from `agent_config`.

    Callers must not assume a specific agent — pass the id through.
    """
    try:
        return _REGISTRY[str(agent_id)]
    except KeyError:
        raise KeyError(f"no config for agent {agent_id!r}")


# ---------------------------------------------------------------------------
# Market layer: the union across agents
# ---------------------------------------------------------------------------
def market_query_union(agents: list) -> list:
    """What the SHARED collector should gather, across every active agent.

    This is the recall guarantee. Running the collector on one agent's terms
    means every other agent can only judge roles that agent caused to be
    fetched — №002 would never see a role №001's vocabulary missed. The union
    is deduplicated and order-stable so runs are reproducible.
    """
    seen, out = set(), []
    for agent in agents:
        for q in agent.search_queries:
            k = q.lower().strip()
            if k and k not in seen:
                seen.add(k)
                out.append(q)
    return out


def market_source_union(agents: list, all_source_ids: list) -> list:
    """Which sources the collector should visit. An agent with no explicit
    subscription means 'all', which is №001's current behaviour."""
    if any(not a.market_sources for a in agents):
        return list(all_source_ids)
    seen, out = set(), []
    for agent in agents:
        for s in agent.market_sources:
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out


# ---------------------------------------------------------------------------
# Database-backed loading
# ---------------------------------------------------------------------------
def load_agent_config_from_db(agent_id: str, fetch_row) -> AgentConfig:
    """database row -> validated AgentConfig.

    `fetch_row(agent_id) -> dict` is injected so this stays testable without a
    live Supabase, and so the transport (REST today, psycopg later) is not
    baked into the contract.
    """
    row = fetch_row(agent_id)
    if not row:
        raise KeyError(f"no agent_config row for agent {agent_id!r}")
    return AgentConfig.from_row(row,
                                agent_no=int(row.get("agent_no") or 0),
                                agent_id=agent_id)
