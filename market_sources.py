"""FOOUND — Move 3: sources follow the Brief.

Finding 8 of the first disposable-client rehearsal (2026-09-03): a Head of
Product Design in Berlin was hunted inside №001's map — 41 company boards
chosen for a US creative-leadership search — and got 8 eligible postings
out of 6,859. The judgment was fine; the field was wrong.

What this module does, and nothing else:
  · REGISTRY — engine data: company boards FOOUND knows how to read, each
    with the regions it actually posts in (observed, not guessed, when the
    board was added) and a sector tag or two. Data, not code.
  · FOUNDING_REGIONS — the same metadata for the founding 41 in
    job_alerts.SCRAPERS, which stay exactly as they are (the founding
    registry is shared engine code; its shape is untouched).
  · brief_regions(compiled) — WHERE, compiled, read back as region keys.
  · select_sources(compiled, ja) — the market universe for THIS Brief:
      1. founding boards whose regions meet the Brief's (or are global);
      2. registry boards whose regions meet the Brief's;
      3. companies the Brief names (priority_companies) — in the registry
         they are simply kept; otherwise their board is probed on the three
         open ATS APIs and, if it answers with postings, read for this hunt.
    Order is stable (founding first, registry next, named last) so counts
    and logs are comparable run to run.

The Brief decides eligibility; nothing here judges. A source that fails is
skipped by live_collect as before. No agent literals: a US Brief selects the
founding boards because they post in the US, not because it is №001's.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Regions. Keys are small and few on purpose; a board's tuple says where it
# has been seen posting. "global" = posts on several continents (selected
# for every Brief). "remote" = hires distributed roles.
# ---------------------------------------------------------------------------
EU_REGIONS = frozenset({
    "eu", "uk", "ie", "de", "nl", "fr", "es", "pt", "it", "ch", "at", "be",
    "se", "dk", "no", "fi", "pl", "cz", "gr", "ee", "lv", "bg", "hu", "sk",
})

# gazetteer token → region key (tokens are what compiled.accepted_locations holds)
_TOKEN_REGION: dict[str, str] = {}


def _reg(region: str, *tokens: str) -> None:
    for t in tokens:
        _TOKEN_REGION[t] = region


_reg("us", "united states", "usa", "us", "north america", "u.s.", "u.s.a.",
     "new york", "nyc", "brooklyn", "manhattan", "new york city", "los angeles",
     "culver city", "santa monica", "burbank", "venice", "playa vista", "el segundo",
     "pasadena", "san francisco", "bay area", "oakland", "mountain view", "menlo park",
     "palo alto", "cupertino", "sunnyvale", "san jose", "santa clara", "los gatos",
     "redwood city", "san mateo", "california", "san diego", "sacramento", "chicago",
     "seattle", "bellevue", "redmond", "boston", "cambridge", "austin", "miami",
     "washington", "denver", "boulder", "atlanta", "portland", "pittsburgh",
     "philadelphia", "dallas", "houston", "minneapolis", "nashville", "phoenix")
_reg("ca", "canada", "toronto", "montreal", "vancouver", "ottawa", "calgary")
_reg("remote", "remote", "work from home", "wfh", "distributed")
_reg("uk", "london", "united kingdom", "uk", "england", "manchester", "edinburgh",
     "bristol", "scotland", "wales", "britain", "great britain")
_reg("ie", "dublin", "ireland")
_reg("fr", "paris", "france", "lyon")
_reg("de", "berlin", "munich", "germany", "hamburg", "frankfurt", "cologne")
_reg("nl", "amsterdam", "netherlands", "rotterdam", "utrecht")
_reg("es", "madrid", "barcelona", "spain")
_reg("pt", "lisbon", "portugal", "porto")
_reg("it", "milan", "rome", "italy")
_reg("ch", "zurich", "geneva", "switzerland")
_reg("at", "vienna", "austria")
_reg("be", "brussels", "belgium")
_reg("se", "stockholm", "sweden")
_reg("dk", "copenhagen", "denmark")
_reg("no", "oslo", "norway")
_reg("fi", "helsinki", "finland")
_reg("pl", "warsaw", "poland")
_reg("cz", "prague", "czech republic")
_reg("gr", "athens", "greece")
_reg("eu", "europe", "emea")


def brief_regions(compiled: dict) -> set[str]:
    """The region keys a compiled Brief's accepted locations reach.
    Unknown tokens contribute nothing (they still filter postings later)."""
    out: set[str] = set()
    for tok in compiled.get("accepted_locations") or []:
        r = _TOKEN_REGION.get(str(tok).strip().lower())
        if r:
            out.add(r)
    return out


def regions_meet(source_regions: tuple[str, ...], brief: set[str]) -> bool:
    """Does a board that posts in source_regions belong in a Brief reaching brief?"""
    src = set(source_regions)
    if "global" in src:
        return True
    if src & brief:
        return True
    # Europe as a whole, on either side.
    if "eu" in brief and src & EU_REGIONS:
        return True
    if "eu" in src and brief & EU_REGIONS:
        return True
    return False


# ---------------------------------------------------------------------------
# The founding 41 (job_alerts.SCRAPERS), by label: where each has been seen
# posting. Every one posts in the US or everywhere, so a US Brief keeps the
# founding universe whole. A board absent here is treated as global.
# ---------------------------------------------------------------------------
FOUNDING_REGIONS: dict[str, tuple[str, ...]] = {
    "Anthropic": ("us", "uk", "ie", "ch"),
    "OpenAI": ("us", "uk", "ie", "de", "fr"),
    "DeepMind": ("us", "uk"),
    "Mistral": ("fr", "uk", "us"),
    "Perplexity": ("us", "eu"),
    "xAI": ("us",),
    "ElevenLabs": ("us", "uk", "eu", "remote"),
    "Cohere": ("ca", "us", "uk"),
    "Scale AI": ("us",),
    "Runway": ("us",),
    "Netflix": ("global",),
    "Nvidia": ("global",),
    "Apple": ("global",),
    "Figma": ("us", "uk", "de"),
    "Airbnb": ("global",),
    "Spotify": ("us", "se", "uk", "eu"),
    "Snap": ("global",),
    "Canva": ("us", "uk", "eu", "remote"),
    "Adobe": ("global",),
    "GitHub": ("global",),
    "Cleo": ("uk", "us"),
    "Stripe": ("global",),
    "Duolingo": ("us", "uk", "de"),
    "Squarespace": ("us", "ie"),
    "Pinterest": ("global",),
    "Discord": ("us",),
    "Webflow": ("us", "remote"),
    "Synthesia": ("uk", "us", "eu", "remote"),
    "Suno": ("us",),
    "Harvey": ("us", "uk"),
    "Sierra": ("us", "uk"),
    "Decagon": ("us",),
    "Cursor": ("us", "remote"),
    "Cognition": ("us",),
    "Hugging Face": ("fr", "us", "remote"),
    "Preacher": ("us",),
    "Johannes Leonardo": ("us",),
    "Wolff Olins": ("uk", "us"),
    "DesignStudio": ("uk", "us"),
    "Koto": ("uk", "us"),
    "Framestore": ("uk", "us", "ca"),
}


@dataclass(frozen=True)
class Source:
    label: str
    ats: str                      # greenhouse | ashby | lever
    slug: str
    regions: tuple[str, ...]
    sectors: tuple[str, ...] = ()


def _s(label, ats, slug, regions, sectors=()):
    return Source(label, ats, slug, tuple(regions), tuple(sectors))


# Boards beyond the founding 41. Regions are what each board was posting
# when it was added (2026-09-03, read through the ATS APIs), most first.
REGISTRY: tuple[Source, ...] = (
    # --- Germany / Berlin ---------------------------------------------------
    _s("N26",            "greenhouse", "n26",          ("de", "es"),          ("fintech", "consumer")),
    _s("GetYourGuide",   "greenhouse", "getyourguide", ("de", "ch"),          ("consumer", "travel")),
    _s("Doctolib",       "greenhouse", "doctolib",     ("de", "fr", "it"),    ("health",)),
    _s("HelloFresh",     "greenhouse", "hellofresh",   ("de", "us", "ca", "global"), ("consumer",)),
    _s("SumUp",          "greenhouse", "sumup",        ("de", "uk", "bg", "global"), ("fintech",)),
    _s("Wolt",           "greenhouse", "wolt",         ("de", "fi", "eu"),    ("consumer",)),
    _s("Raisin",         "greenhouse", "raisin",       ("de", "uk"),          ("fintech",)),
    _s("Solaris",        "greenhouse", "solarisbank",  ("de",),               ("fintech",)),
    _s("Grover",         "greenhouse", "grover",       ("de", "remote"),      ("consumer",)),
    _s("Too Good To Go", "greenhouse", "toogoodtogo",  ("dk", "de", "eu"),    ("consumer",)),
    _s("Celonis",        "greenhouse", "celonis",      ("de", "es", "us"),    ("enterprise",)),
    _s("Enpal",          "ashby",      "enpal",        ("de", "it"),          ("consumer", "energy")),
    _s("Moss",           "ashby",      "moss",         ("de", "uk"),          ("fintech",)),
    _s("Forto",          "ashby",      "forto",        ("de", "remote"),      ("logistics",)),
    _s("Babbel",         "ashby",      "babbel",       ("de", "us"),          ("consumer", "education")),
    _s("Kittl",          "ashby",      "kittl",        ("de",),               ("creative tools",)),
    _s("FINN",           "lever",      "finn",         ("de",),               ("consumer",)),
    _s("Mentimeter",     "greenhouse", "mentimeter",   ("se", "de"),          ("creative tools",)),
    # --- Netherlands / Amsterdam -----------------------------------------
    _s("Adyen",          "greenhouse", "adyen",        ("nl", "us", "global"), ("fintech",)),
    _s("DEPT",           "greenhouse", "dept",         ("nl", "us", "uk", "eu"), ("agency",)),
    _s("Mollie",         "ashby",      "mollie",       ("nl", "pt"),          ("fintech",)),
    _s("Miro",           "ashby",      "miro",         ("nl", "us", "de"),    ("creative tools",)),
    # --- United Kingdom / London -------------------------------------------
    _s("Monzo",          "greenhouse", "monzo",        ("uk",),               ("fintech", "consumer")),
    _s("Algolia",        "greenhouse", "algolia",      ("uk", "fr", "us", "remote"), ("developer tools",)),
    _s("GoCardless",     "greenhouse", "gocardless",   ("uk", "pt", "lv"),    ("fintech",)),
    _s("Attio",          "ashby",      "attio",        ("uk", "us"),          ("saas",)),
    _s("Deliveroo",      "ashby",      "deliveroo",    ("uk",),               ("consumer",)),
    _s("Multiverse",     "ashby",      "multiverse",   ("uk",),               ("education",)),
    _s("Granola",        "ashby",      "granola",      ("uk", "us"),          ("creative tools", "ai")),
    _s("ZOE",            "ashby",      "zoe",          ("uk",),               ("health", "consumer")),
    _s("tldraw",         "ashby",      "tldraw",       ("uk",),               ("creative tools",)),
    _s("Beamery",        "ashby",      "beamery",      ("uk", "us"),          ("enterprise",)),
    _s("Paddle",         "ashby",      "paddle",       ("uk", "ca"),          ("fintech",)),
    _s("Zopa",           "lever",      "zopa",         ("uk",),               ("fintech", "consumer")),
    _s("Pipedrive",      "lever",      "pipedrive",    ("uk", "ee"),          ("saas",)),
    _s("Farfetch",       "lever",      "farfetch",     ("pt", "uk"),          ("consumer",)),
    # --- France / Paris -----------------------------------------------------
    _s("Alan",           "ashby",      "alan",         ("fr", "es", "be", "remote"), ("health",)),
    _s("Qonto",          "ashby",      "qonto",        ("fr", "it", "de", "es"), ("fintech",)),
    _s("Pennylane",      "ashby",      "pennylane",    ("fr",),               ("fintech",)),
    _s("Photoroom",      "ashby",      "photoroom",    ("fr", "us"),          ("creative tools", "ai")),
    _s("Back Market",    "ashby",      "backmarket",   ("fr",),               ("consumer",)),
    _s("Pigment",        "lever",      "pigment",      ("fr", "uk"),          ("saas",)),
    _s("Malt",           "lever",      "malt",         ("fr", "uk", "de"),    ("marketplace",)),
    _s("Aircall",        "lever",      "aircall",      ("es", "fr"),          ("saas",)),
    _s("BlaBlaCar",      "lever",      "blablacar",    ("fr",),               ("consumer",)),
    # --- Europe, remote-first ---------------------------------------------
    _s("Linear",         "ashby",      "linear",       ("remote", "us", "eu"), ("creative tools", "saas")),
    _s("Supabase",       "ashby",      "supabase",     ("remote",),           ("developer tools",)),
    _s("PostHog",        "ashby",      "posthog",      ("remote", "eu"),      ("developer tools",)),
    _s("Oyster",         "ashby",      "oyster",       ("remote", "eu", "pt"), ("saas",)),
    _s("Resend",         "ashby",      "resend",       ("remote", "eu"),      ("developer tools",)),
    _s("Lovable",        "ashby",      "lovable",      ("se", "uk"),          ("creative tools", "ai")),
    _s("Pleo",           "ashby",      "pleo",         ("pt", "uk", "dk"),    ("fintech",)),
    _s("GitLab",         "greenhouse", "gitlab",       ("remote",),           ("developer tools",)),
    _s("Typeform",       "greenhouse", "typeform",     ("es", "uk", "remote", "eu"), ("saas",)),
    _s("Trustpilot",     "greenhouse", "trustpilot",   ("dk", "us", "uk"),    ("consumer",)),
)

_REGISTRY_BY_LABEL = {s.label.lower(): s for s in REGISTRY}


def _fetcher(ja, ats: str):
    return {
        "greenhouse": getattr(ja, "fetch_greenhouse", None),
        "ashby": getattr(ja, "fetch_ashby", None),
        "lever": getattr(ja, "fetch_lever", None),
    }.get(ats)


def _entry(ja, s: Source):
    fn = _fetcher(ja, s.ats)
    if fn is None:
        return None
    return (s.label, fn, s.slug, s.label)


# ---------------------------------------------------------------------------
# Named companies. A Brief may name houses the person wants; if FOOUND has
# no board for one, it asks the three open ATS APIs with the obvious slugs.
# The probe is the fetcher itself: a board that answers with postings is a
# board. Nothing is written; a miss is a miss.
# ---------------------------------------------------------------------------
def slug_candidates(name: str) -> list[str]:
    base = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    if not base:
        return []
    words = base.split()
    out = ["".join(words), "-".join(words)]
    if words and words[-1] in ("inc", "ltd", "gmbh", "ag", "co", "llc", "labs", "ai"):
        core = words[:-1]
        out += ["".join(core), "-".join(core)]
    seen: list[str] = []
    for c in out:
        if c and c not in seen:
            seen.append(c)
    return seen


def probe_named_company(ja, name: str, fetch=None):
    """Find a readable board for a company the Brief names.
    Returns a scraper entry (label, fn, slug, label) or None.
    fetch(fn, slug, label) → list, injectable for tests; default calls fn."""
    call = fetch or (lambda fn, slug, label: fn(slug, label))
    for ats in ("greenhouse", "ashby", "lever"):
        fn = _fetcher(ja, ats)
        if fn is None:
            continue
        for slug in slug_candidates(name):
            try:
                jobs = call(fn, slug, name)
            except Exception:
                jobs = None
            if jobs:
                return (name, fn, slug, name)
    return None


def select_sources(compiled: dict, ja, *, probe=None) -> tuple[list, dict]:
    """The market universe for one Brief. Returns (entries, summary).

    entries: scraper tuples for live_collect, founding first, in SCRAPERS
    order; then registry boards; then named boards found by probe.
    summary: counts and labels, safe to log and to keep in the edition
    payload (company names only)."""
    regions = brief_regions(compiled)
    founding_all = list(getattr(ja, "SCRAPERS", []) or [])
    entries: list = []
    labels_seen: set[str] = set()
    founding: list[str] = []
    for entry in founding_all:
        try:
            label = entry[0]
        except (IndexError, TypeError):
            continue
        srcreg = FOUNDING_REGIONS.get(label, ("global",))
        if regions_meet(srcreg, regions):
            entries.append(entry)
            labels_seen.add(str(label).lower())
            founding.append(str(label))
    added: list[str] = []
    for s in REGISTRY:
        if s.label.lower() in labels_seen:
            continue
        if regions_meet(s.regions, regions):
            e = _entry(ja, s)
            if e is not None:
                entries.append(e)
                labels_seen.add(s.label.lower())
                added.append(s.label)
    named: list[str] = []
    probed: list[str] = []
    for name in compiled.get("priority_companies") or []:
        key = str(name).strip().lower()
        if not key or key in labels_seen:
            continue
        s = _REGISTRY_BY_LABEL.get(key)
        if s is not None:
            e = _entry(ja, s)
            if e is not None:
                entries.append(e)
                labels_seen.add(key)
                named.append(s.label)
            continue
        probed.append(str(name).strip())
        e = (probe or probe_named_company)(ja, str(name).strip())
        if e is not None:
            entries.append(e)
            labels_seen.add(key)
            named.append(str(name).strip())
    summary = {
        "regions": sorted(regions),
        "selected": len(entries),
        "founding": len(founding),
        "founding_total": len(founding_all),
        "added": added,
        "named": named,
        "probed": probed,
    }
    return entries, summary
