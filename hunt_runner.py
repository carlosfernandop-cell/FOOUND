"""FOOUND Hunt Runner — Move 1: ONE JUDGE.

Contract (ratified 2026-09-02, plan v1.1):
  · The Working Brief defines the eligible universe. The original
    job_alerts judgment loop decides quality inside it. One engine writes
    one edition. Nothing else ranks.
  · Brief → AgentConfig: role families through ROLE_SYNONYMS (broad
    substring phrases, as the original include list was written);
    places through LOCATION_GAZETTEER (ordinary meaning, engine data);
    exclusions = Brief exclusions ∪ engine defaults; priority houses and
    seat cap from the Brief only. No concept mining. CONTEXT / MANDATE do
    not exist as deterministic objects.
  · Eligibility = job_alerts.passes_title + passes_location on that
    AgentConfig, dedup on role_key. Nothing else gates.
  · The person's PASS / APPLIED verdicts leave the universe before judgment
    (foound_state). RECONSIDER forces a full read. Identity is role_key;
    legacy title|company keys are a read-only compatibility fallback.
  · Judgment = job_alerts.rank_with_fit with the private read budget, then
    job_alerts.seat_edition (floor 60 · cap · lead · refusals), deep look on
    the lead, write_brief for the statline, why_now_text per seat.
  · A client with no Candidate Context is refused with no_candidate_context
    before any model call. №001's interim context is profile.md.
  · Confirmed Memory is never search authority. Do not read memory.
  · Never write agent_config. Never write Candidate. Never publish public.
  · Zero eligible seats is SUCCESS: an honest empty edition.
  · v1 market memory is personal: this agent's prior private
    editions.payload only. Do not use public.market_seen.
  · Isolation: C1/C2 are met by this module's compilation and read budget
    only — never by changing MAX_CANDIDATES_TO_SCORE, FOOUND_FLOOR, the cap,
    or any public job_alerts behaviour.

Privacy: the engine repo is PUBLIC and GitHub Actions logs are public.
Logging is ids / counts / enums / timings ONLY. Never Brief copy, seat
titles, URLs, prompts, or model output. Adapter and scorer stdout is
swallowed. The refusal ledger lives only in editions.payload.
"""

from __future__ import annotations

import contextlib
import hashlib
import html
import io
import json
import logging
import os
import re
import sys

import market_sources
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

log = logging.getLogger("hunt_runner")

# Seat cap: the original Shortlist's eleven, unless the Brief says otherwise.
DEFAULT_SEAT_CAP = 11
MAX_SEAT_CAP = 20
HUNT_JOB_TYPES = ("compile_brief", "refresh_readiness", "first_edition", "propose_brief", "draft_candidate")
MAX_JOBS_PER_RUN = 10

# Move 1 read budget for the private hunt. Fixed for Stage 1 by contract:
# do not raise it to satisfy C2 — report the contract result instead.
# The public Shortlist keeps job_alerts.MAX_CANDIDATES_TO_SCORE untouched.
PRIVATE_READ_BUDGET = 40

# Engine-default exclusions (not client data): the original Shortlist's
# exclude_type, applied to every agent on top of the Brief's own.
ENGINE_DEFAULT_EXCLUDES = ("intern", "internship", "part-time", "part time", "contractor")

# Fallback market queries for the query-driven adapters (Workday, Netflix),
# used only for a Brief that names no seat family at all. Since Move 3 the
# queries are the Brief's own words (search_queries_for); these never ride
# along on someone else's hunt.
ENGINE_DEFAULT_SEARCH_QUERIES = ("creative director", "brand", "creative lead")

# Temporary v1 kind hints — NOT permanent subject architecture.
# Any non-skipped subject title may authorize hunt; these only bucket text.
_ROLE_HINTS = ("role", "craft", "seat", "space", "title")
_PLACE_HINTS = ("where", "geo", "location", "place")
_MOVE_HINTS = ("move", "ambition", "direction")
# MARKET / STILL LEARNING / READINESS have no hunt subject yet. AVOID is off.
_SKIP_HINTS = ("market", "readiness", "learning", "avoid")

READINESS_ARCHITECTURE_NOTE = (
    "temporary: briefs.readiness stores ready|not_ready only; "
    "BLOCKED reasons live in compiled_config.readiness_reasons. "
    "Not the permanent readiness representation."
)

NAMED_ERRORS = {
    "no_active_brief",
    "readiness_blocked",
    "compile_failed",
    "hunt_adapter_failed",
    "edition_persist_failed",
    "no_candidate_context",
    "candidate_draft_failed",
    "no_role_families",
    "proposal_failed",
    "job_type_unknown",
}


class HuntError(Exception):
    """Named technical failure. Safe to persist on jobs.error."""

    def __init__(self, name: str):
        if name not in NAMED_ERRORS:
            name = "compile_failed"
        super().__init__(name)
        self.name = name


# ---------------------------------------------------------------------------
# ROLE_SYNONYMS — engine data. A Brief family expands to the substring
# phrases the original include list used. Matching is job_alerts.passes_title
# (lowercase substring), so "creative director" already covers Senior /
# Group / Executive / Associate / Technical CD.
# ---------------------------------------------------------------------------

ROLE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "cd": ("creative director", "director of creative", "director, creative"),
    "creative director": ("creative director", "director of creative", "director, creative"),
    "ecd": ("executive creative",),
    "executive cd": ("executive creative",),
    "executive creative director": ("executive creative",),
    "gcd": ("group creative",),
    "group cd": ("group creative",),
    "group creative director": ("group creative",),
    "head of creative": ("head of creative", "creative lead"),
    "head of brand": ("head of brand", "brand director", "director of brand",
                      "director, brand", "brand lead"),
    "vp brand/creative": ("vp of creative", "vp, creative", "vp creative",
                          "vp of brand", "vp, brand", "vp brand"),
    "vp brand": ("vp of brand", "vp, brand", "vp brand"),
    "vp of brand": ("vp of brand", "vp, brand", "vp brand"),
    "vp creative": ("vp of creative", "vp, creative", "vp creative"),
    "vp of creative": ("vp of creative", "vp, creative", "vp creative"),
    "brand marketing director": ("brand marketing director",),
    "design director": ("design director",),
    "chief creative officer": ("chief creative", "cco"),
    "cco": ("chief creative", "cco"),
    "chief brand officer": ("chief brand",),
    "cbo": ("chief brand",),
}


def _generic_variants(f: str) -> list[str]:
    """Move 2: the same shapes ROLE_SYNONYMS hand-writes for №001's seats,
    derived for any seat — so "vp design" also reads "vp, design" and
    "vp of design", "design director" also reads "director of design" and
    "director, design", "head of design" also reads "head, design".
    Substring matching (job_alerts.passes_title) does the rest."""
    words = f.split()
    out = [f]
    if len(words) >= 2 and words[0] in ("vp", "svp", "evp", "head"):
        rank = words[0]
        craft = " ".join(w for w in words[1:] if w not in ("of", "the"))
        if craft:
            if rank == "head":
                out.append(f"head, {craft}")
            else:
                out += [f"{rank} {craft}", f"{rank} of {craft}", f"{rank}, {craft}"]
    if len(words) >= 2 and words[-1] == "director":
        craft = " ".join(words[:-1])
        out += [f"director of {craft}", f"director, {craft}"]
    seen, uniq = set(), []
    for v in out:
        v = _norm_phrase(v)
        if v and v not in seen and v not in _BARE_RANKS:
            seen.add(v); uniq.append(v)
    return uniq


MAX_SEARCH_QUERIES = 8


_QUERY_STOPWORDS = frozenset({
    "of", "and", "or", "the", "a", "an", "in", "for", "to", "at", "with",
    "head", "director", "vp", "svp", "evp", "chief", "lead", "leader", "officer",
    "manager", "senior", "sr", "principal", "staff", "group", "executive",
    "global", "associate", "assistant", "junior", "jr",
})


def search_queries_for(families) -> list[str]:
    """What the search-based adapters should ask for, for THIS Brief (Move 3).

    The Brief's own words, nothing else: each family as a quoted title
    (precise), and each family's craft noun unquoted (recall — "design",
    "brand", "creative"), rank words stripped. The engine defaults are a
    fallback for a Brief with no families at all, never an addition to
    someone else's. Capped, order-stable, first-seen first."""
    fams = [_norm_phrase(f) for f in (families or []) if _norm_phrase(f)]
    if not fams:
        return list(ENGINE_DEFAULT_SEARCH_QUERIES)
    out: list[str] = []
    seen: set[str] = set()
    # recall first: the craft nouns, bare ("creative", "brand", "design")
    for f in fams:
        for w in re.split(r"[^a-z0-9]+", f):
            if (len(w) >= 3 and w not in _QUERY_STOPWORDS and w not in seen
                    and w not in fams and len(out) < MAX_SEARCH_QUERIES):
                seen.add(w)
                out.append(w)
    # then precision: each family as a quoted title
    for f in fams:
        if f in seen or len(out) >= MAX_SEARCH_QUERIES:
            continue
        seen.add(f)
        out.append(f'"{f}"')
    return out


def expand_role_family(family: str) -> list[str]:
    """One Brief family → its include phrases. Known seats use the hand rows;
    any other seat gets the same shapes derived (Move 2)."""
    f = _norm_phrase(family)
    if not f:
        return []
    hand = list(ROLE_SYNONYMS.get(f, ()))
    if hand and hand != [f]:
        return hand                        # a written row is authoritative (№001's proven gate)
    # No row, or a row that only names itself: derive the same shapes.
    out = list(hand)
    for v in _generic_variants(f):
        if v not in out:
            out.append(v)
    return out


# ---------------------------------------------------------------------------
# LOCATION_GAZETTEER — engine data, organised by ordinary meaning.
# Design test for every row: does the expansion reflect what the phrase
# means for ANY client? Never "does it recover one client's missing jobs".
# Expansions are unions. Matching stays job_alerts.passes_location
# (word-boundary regex); empty location passes; "N locations" passes.
# ---------------------------------------------------------------------------

_GAZ_NEW_YORK = ("new york", "nyc", "brooklyn", "manhattan", "new york city")
_GAZ_LA = ("los angeles", "culver city", "santa monica", "burbank", "venice",
           "playa vista", "el segundo", "pasadena")
_GAZ_BAY = ("san francisco", "bay area", "oakland", "mountain view", "menlo park",
            "palo alto", "cupertino", "sunnyvale", "san jose", "santa clara",
            "los gatos", "redwood city", "san mateo")
_GAZ_CALIFORNIA = _GAZ_LA + _GAZ_BAY + ("california", "san diego", "sacramento")
_GAZ_US_HUBS = _GAZ_NEW_YORK + _GAZ_LA + _GAZ_BAY + (
    "chicago", "seattle", "bellevue", "redmond", "boston", "cambridge", "austin",
    "miami", "washington", "denver", "boulder", "atlanta", "portland", "pittsburgh",
    "philadelphia", "dallas", "houston", "minneapolis", "nashville", "phoenix",
    "san diego",
)
_GAZ_US = ("united states", "usa", "us", "north america") + _GAZ_US_HUBS
_GAZ_REMOTE = ("remote", "work from home", "wfh", "distributed")
_GAZ_CANADA = ("canada", "toronto", "montreal", "vancouver", "ottawa", "calgary")
_GAZ_UK = ("london", "united kingdom", "uk", "england", "manchester", "edinburgh",
           "bristol", "scotland", "wales")
_GAZ_FRANCE = ("paris", "france", "lyon")
_GAZ_EU_CITIES = ("london", "paris", "berlin", "amsterdam", "dublin", "madrid",
                  "lisbon", "rome", "milan", "stockholm", "copenhagen", "oslo",
                  "helsinki", "zurich", "vienna", "brussels", "warsaw", "prague",
                  "athens")
_GAZ_EU_COUNTRIES = ("europe", "emea", "united kingdom", "uk", "england", "ireland",
                     "france", "germany", "netherlands", "spain", "portugal", "italy",
                     "switzerland", "sweden", "denmark", "norway", "finland",
                     "austria", "belgium", "poland", "czech republic", "greece")
_GAZ_EUROPE = _GAZ_EU_CITIES + _GAZ_EU_COUNTRIES

# phrase → (meaning, tokens). A phrase absent here maps to itself and is
# reported as unmapped_location_phrase (non-blocking). Grow by adding rows.
LOCATION_GAZETTEER: dict[str, tuple[str, tuple[str, ...]]] = {}


def _gaz(meaning: str, tokens: tuple[str, ...], *phrases: str) -> None:
    for p in phrases:
        LOCATION_GAZETTEER[p] = (meaning, tokens)


_gaz("the New York metro", _GAZ_NEW_YORK,
     "nyc", "new york", "new york city", "manhattan", "brooklyn", "new york, ny")
_gaz("the Los Angeles metro", _GAZ_LA,
     "los angeles", "la", "socal", "southern california")
_gaz("the Bay Area", _GAZ_BAY,
     "san francisco", "sf", "bay area", "silicon valley")
_gaz("the state of California", _GAZ_CALIFORNIA,
     "california", "ca")
_gaz("the largest US job markets", _GAZ_US_HUBS,
     "major us hubs", "us hubs", "major cities", "big us cities", "major us cities",
     "us major hubs")
_gaz("the United States", _GAZ_US,
     "united states", "usa", "us", "anywhere in the us", "north america", "u.s.",
     "u.s.a.")
_gaz("remote work", _GAZ_REMOTE,
     "remote", "work from home", "wfh", "distributed")
_gaz("remote work in the United States (a posting located simply 'US' is this)",
     _GAZ_REMOTE + ("united states", "usa", "us"),
     "remote us", "remote (us)", "us remote", "remote-us", "remote, us",
     "remote usa", "remote in the us")
_gaz("remote work in Europe", _GAZ_REMOTE + _GAZ_EUROPE,
     "remote europe", "remote (europe)", "remote in europe", "europe remote",
     "remote eu", "remote (eu)", "eu remote", "remote-europe", "remote, europe",
     "remote emea", "emea remote")
_gaz("Toronto", ("toronto",), "toronto")
_gaz("Montreal", ("montreal",), "montreal")
_gaz("Vancouver", ("vancouver",), "vancouver")
_gaz("Canada", _GAZ_CANADA, "canada")
_gaz("London", ("london",), "london")
_gaz("the United Kingdom", _GAZ_UK,
     "uk", "united kingdom", "england", "britain", "great britain")
_gaz("Paris", ("paris",), "paris")
_gaz("France", _GAZ_FRANCE, "france")
for _city in ("berlin", "amsterdam", "dublin", "madrid", "barcelona", "lisbon",
              "milan", "rome", "stockholm", "copenhagen", "oslo", "helsinki",
              "zurich", "geneva", "vienna", "brussels", "munich", "warsaw",
              "prague", "athens"):
    _gaz(_city.title(), (_city,), _city)
_gaz("Europe's capital and major cities", _GAZ_EUROPE,
     "major european capitals", "european capitals", "europe", "eu", "emea",
     "major european cities", "the major european capitals")


def expand_location_phrase(phrase: str) -> tuple[list[str], bool]:
    """One Brief place phrase → gazetteer tokens. (tokens, mapped)."""
    p = _norm_phrase(phrase).strip(" .")
    p = re.sub(r"^(?:and|or|plus|also)\s+", "", p)
    p = re.sub(r"^the\s+", "", p)
    if not p:
        return [], True
    hit = LOCATION_GAZETTEER.get(p) or LOCATION_GAZETTEER.get("the " + p)
    if hit:
        return list(hit[1]), True
    return [p], False


# ---------------------------------------------------------------------------
# Brief → compiled_config (pure). Content only. No Memory. No agent_config.
# ---------------------------------------------------------------------------

def _norm_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip().lower()


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        v = _norm_phrase(value)
        return [v] if v else []
    if isinstance(value, (list, tuple)):
        out, seen = [], set()
        for item in value:
            if not isinstance(item, str):
                continue
            v = _norm_phrase(item)
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out
    return []


def _title_of(unit: dict) -> str:
    for key in ("title", "handle", "name", "id"):
        v = unit.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _text_of(unit: dict) -> str:
    parts: list[str] = []
    for key in ("body", "text", "content", "statement", "line"):
        v = unit.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    lines = unit.get("lines")
    if isinstance(lines, list):
        parts.extend(x.strip() for x in lines if isinstance(x, str) and x.strip())
    return "\n".join(parts)


def _kind_hint(title: str) -> str | None:
    """Bucket a subject title. Hints only — titles are not a closed enum."""
    t = _norm_phrase(title)
    if not t:
        return None
    if any(h in t for h in _SKIP_HINTS):
        return "skip"
    if any(h in t for h in _PLACE_HINTS) or t == "where":
        return "place"
    if any(h in t for h in _ROLE_HINTS):
        return "role"
    if any(h in t for h in _MOVE_HINTS):
        return "move"
    return "other"


def _kind_of_subject(sub: dict) -> str:
    """Bucket from chapter title first, then unit title. Handles are not keys —
    with one exception: a subject whose own handle says it is not settled
    ("Still learning", "Avoid") is skipped whatever chapter it sits in. A
    WHERE line FOOUND wrote as "Still learning" must never compile into a
    place to hunt."""
    if _kind_hint(sub.get("title") or "") == "skip":
        return "skip"
    for title in (sub.get("context_title"), sub.get("title")):
        kind = _kind_hint(title or "")
        if kind in ("skip", "place", "role", "move"):
            return kind
    return _kind_hint(sub.get("title") or "") or "other"


def _balanced_punct(phrase: str) -> bool:
    if phrase.count("(") != phrase.count(")"):
        return False
    if phrase.count("[") != phrase.count("]"):
        return False
    if phrase.startswith(("(", ")", "/", ",", ";", "]")):
        return False
    if ")" in phrase and "(" not in phrase:
        return False
    if phrase.endswith("("):
        return False
    return True


def _split_phrases(text: str) -> list[str]:
    """Split authorized text into intact phrases.

    Sentence / comma / semicolon / newline only. Does not cut on '/' or '('
    so compounds like 'VP Product/Design' and 'design leadership (CPO / Design)'
    stay whole.
    """
    if not text:
        return []
    chunks: list[str] = []
    for sentence in re.split(r"[.\n!?]+", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if ":" in sentence:
            sentence = sentence.split(":", 1)[-1]
        for part in re.split(r"\s*[,;]\s*", sentence):
            v = _norm_phrase(part)
            if v:
                chunks.append(v)
    return chunks


def _usable_term(phrase: str) -> bool:
    if not phrase or len(phrase) > 60:
        return False
    if not _balanced_punct(phrase):
        return False
    words = [w for w in phrase.split() if w not in {"/", "-", "–", "—"}]
    return 1 <= len(words) <= 7


# Role-title grammar — generic seat language, not a catalog of any Brief.
_ROLE_ABBREV = frozenset({
    "cd", "ecd", "gcd", "vp", "svp", "evp",
    "cdo", "cmo", "cto", "ceo", "cfo", "coo", "cpo", "cro", "cxo",
    "cio", "cco", "cao",
})
_ROLE_NUCLEI = frozenset({
    "director", "designer", "engineer", "manager", "lead", "head",
    "officer", "president", "partner", "architect", "producer",
    "writer", "strategist", "editor", "curator", "researcher",
    "specialist", "consultant", "founder", "chair", "chief",
    "scientist", "analyst", "developer", "copywriter", "recruiter",
})
_ROLE_RANKS = frozenset({
    "staff", "senior", "principal", "executive", "group", "global",
    "associate", "junior", "vice", "assistant", "head", "vp",
})
_CRAFT_ADJECTIVES = frozenset({
    "creative", "brand", "product", "design", "marketing", "experience",
    "content", "art", "growth", "digital", "visual", "ux", "ui",
    "research", "data", "software", "engineering", "sales", "account",
    "media", "communications", "comms", "strategy", "operations",
    "people", "talent", "studio", "editorial",
})
_TITLE_NOISE = frozenset({
    "the", "a", "an", "is", "are", "as", "for", "to", "our", "their",
    "my", "your", "and", "or",
})
_CLAUSE_MARKERS = ("that function", "finished one", "the function")
_CLAUSE_STARTS = (
    "lead the", "build ", "transform ", "inherit ", "not ",
    "own the", "create ", "want ", "do not", "don't",
    "across ", "for a ", "for the ", "with ", "from ", "into ",
    "among ",
)
_CONJ_LEAD = re.compile(r"^(and|or|but|nor)\s+", re.I)
_PRONOUN_TAILS = frozenset({
    "themselves", "itself", "himself", "herself", "ourselves",
    "myself", "them", "they", "it",
})
_SEAT_PREFIX = re.compile(
    r"^(?:the\s+)?seat\s+is\s+|^seat\s*:?\s+|^roles?\s*:?\s+|^titles?\s*:?\s+",
    re.I,
)
_HEAD_OF = re.compile(
    r"^(?:head|director|vp|vice president|chief|lead) of "
    r"[a-z][a-z&'-]*(?: [a-z][a-z&'-]*){0,2}$"
)


def _is_clause_fragment(phrase: str) -> bool:
    p = _norm_phrase(phrase)
    if any(p.startswith(s) for s in _CLAUSE_STARTS):
        return True
    if any(m in p for m in _CLAUSE_MARKERS):
        return True
    return bool(re.search(r"\b(build|transform|inherit)\b", p))


def _is_prose_scrap(phrase: str) -> bool:
    """Conjunction-led leftovers, punctuation junk, residual sentence tails."""
    p = _norm_phrase(phrase)
    if not p or not _balanced_punct(p):
        return True
    if p.startswith(("and ", "or ", "but ", "nor ", "not ")):
        return True
    if _is_clause_fragment(p):
        return True
    words = [w for w in p.split() if w not in {"/", "-", "–", "—"}]
    if not words:
        return True
    if words[0] in {"and", "or", "but", "nor", "not"}:
        return True
    if words[-1] in _PRONOUN_TAILS:
        return True
    return False


def _looks_like_role_title(phrase: str) -> bool:
    """True for a whole seat/role title. Rejects ambition clauses and junk."""
    p = _norm_phrase(phrase)
    if not _usable_term(p) or _is_clause_fragment(p):
        return False
    words = p.split()
    if words and words[0] in _TITLE_NOISE:
        return False
    compact = p.replace(".", "")
    if compact in _ROLE_ABBREV:
        return True
    if "/" in compact:
        head = compact.split()[0]
        if head in _ROLE_ABBREV or head in {"vp", "head", "director", "chief"}:
            return 1 <= len(words) <= 4
        return False
    if _HEAD_OF.match(compact):
        return True
    if len(words) == 2 and words[0] in _ROLE_RANKS and words[1] in _ROLE_ABBREV:
        return True
    if len(words) >= 2 and words[-1] in _ROLE_NUCLEI:
        # "chief <craft> officer" carries two nuclei by construction (Move 2)
        if sum(1 for w in words if w in _ROLE_NUCLEI) > 1 and not (words[0] == "chief" and words[-1] == "officer"):
            return False
        return all(
            w in _ROLE_RANKS or w in _CRAFT_ADJECTIVES or w in _ROLE_NUCLEI
            or w in _ROLE_ABBREV or w == "chief"
            for w in words
        )
    # Move 2: seats outside №001's vocabulary. "VP Design", "VP of Marketing",
    # "SVP Brand", "Chief Design Officer" are whole titles: a rank or
    # abbreviation followed by the craft it leads. Without this, "VP Design"
    # collapsed to the bare abbreviation "vp" and matched every VP posting.
    if 2 <= len(words) <= 4 and words[0] in _ROLE_ABBREV | {"head", "director", "chief"}:
        rest = [w for w in words[1:] if w not in ("of", "the")]
        if words[0] == "chief":
            return len(rest) >= 1 and rest[-1] == "officer" and all(w in _CRAFT_ADJECTIVES or w == "officer" for w in rest)
        return bool(rest) and all(w in _CRAFT_ADJECTIVES for w in rest)
    return False


def _as_family(phrase: str) -> str:
    """Bare CD is Creative Director. Group/Executive CD and ECD stay as written.
    "VP of Design" and "VP Design" are one family (Move 2); "Head of X" keeps
    its conventional form."""
    p = _norm_phrase(phrase)
    if p == "cd":
        return "creative director"
    m = re.match(r"^(vp|svp|evp) of (.+)$", p)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return p


def _split_list_outside_parens(text: str) -> list[str]:
    """Comma/semicolon split that does not cut inside '(...)'."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in text or "":
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch in ",;" and depth == 0:
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
        else:
            buf.append(ch)
    part = "".join(buf).strip()
    if part:
        parts.append(part)
    return parts


def _maybe_split_slash_titles(part: str) -> list[str]:
    raw = (part or "").strip()
    if not raw:
        return []
    if "(" in raw and ")" in raw:
        return [raw]
    pieces = [p.strip() for p in re.split(r"\s*/\s*", raw) if p.strip()]
    if len(pieces) >= 2 and all(_looks_like_role_title(p) for p in pieces):
        return pieces
    return [raw]


def _expand_part(part: str) -> list[str]:
    raw = (part or "").strip()
    if not raw:
        return []
    out = _maybe_split_slash_titles(raw)
    for inner in re.findall(r"\(([^)]*)\)", raw):
        for piece in _split_list_outside_parens(inner):
            out.extend(_maybe_split_slash_titles(piece))
    return out


def _list_items(text: str) -> list[str]:
    items: list[str] = []
    for sentence in re.split(r"[.\n!?]+", text or ""):
        sentence = sentence.strip()
        if not sentence:
            continue
        if ":" in sentence:
            sentence = sentence.split(":", 1)[-1].strip()
        sentence = _SEAT_PREFIX.sub("", sentence).strip()
        if not sentence:
            continue
        for part in _split_list_outside_parens(sentence):
            items.extend(_expand_part(part))
    return items


def _iter_title_spans(text: str) -> list[str]:
    stripped = re.sub(r"\([^)]*\)", " ", text or "")
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9'/&-]*", stripped)
    n = len(tokens)
    used = [False] * n
    hits: list[tuple[int, int, str]] = []
    for length in range(min(6, n), 0, -1):
        for i in range(0, n - length + 1):
            if any(used[i:i + length]):
                continue
            phrase = " ".join(tokens[i:i + length])
            if _looks_like_role_title(phrase):
                hits.append((i, length, phrase))
                for j in range(i, i + length):
                    used[j] = True
    hits.sort()
    return [p for _, _, p in hits]


def _add_unique(dest: list[str], raw: str) -> bool:
    v = _norm_phrase(raw)
    if not v or v in dest:
        return False
    dest.append(v)
    return True


# Rank words that are never a family on their own. The creative-seat
# abbreviations (cd, ecd, gcd) and C-level abbreviations are whole titles
# and stay.
_BARE_RANKS = _ROLE_RANKS | {"vp", "svp", "evp", "director", "head", "chief", "lead", "officer", "manager"}


def _extract_role_families(text: str) -> list[str]:
    found: list[str] = []
    for item in _list_items(text):
        fam = _as_family(item)
        if _looks_like_role_title(item) or _looks_like_role_title(fam):
            _add_unique(found, fam)
            continue
        # Mine titles inside this item only — never stitch across commas.
        for span in _iter_title_spans(item):
            inner = _as_family(span)
            if _looks_like_role_title(span) or _looks_like_role_title(inner):
                _add_unique(found, inner)
    # A bare rank ("vp", "director") is never a family: as an include term it
    # would admit every posting with that word.
    return [f for f in found if f not in _BARE_RANKS]




def _extract_locations(text: str) -> list[str]:
    found: list[str] = []
    for phrase in _split_phrases(text):
        for part in re.split(r"\s+\band\b\s+", phrase):
            v = _norm_phrase(part)
            if not v or not _usable_term(v):
                continue
            if _is_clause_fragment(v) or _looks_like_role_title(v):
                continue
            _add_unique(found, v)
    return found

def _collect_structured(node, into: dict) -> None:
    if not isinstance(node, dict):
        return
    for key in ("include", "exclude_type", "accepted_locations", "search_queries"):
        if key in node:
            for item in _as_str_list(node.get(key)):
                if item not in into[key]:
                    into[key].append(item)
    # Priority houses keep their casing: they are matched against adapter
    # labels (job_alerts SCRAPERS) at AgentConfig time.
    pri = node.get("priority_companies")
    if isinstance(pri, str):
        pri = [pri]
    if isinstance(pri, (list, tuple)):
        for item in pri:
            if isinstance(item, str) and item.strip() and item.strip() not in into["priority_companies"]:
                into["priority_companies"].append(item.strip())
    cap = node.get("seat_cap")
    if isinstance(cap, int) and 1 <= cap <= MAX_SEAT_CAP:
        into["seat_cap"] = cap
    elif isinstance(cap, str) and cap.isdigit():
        n = int(cap)
        if 1 <= n <= MAX_SEAT_CAP:
            into["seat_cap"] = n


def extract_subjects(content: dict) -> list[dict]:
    """Walk unstructured Brief.content for titled units. Shape-tolerant.

    Accepts chapters[].subjects[], subjects[], or titled dict values.
    Does not require THE MOVE / ROLE SPACE / WHERE.
    """
    found: list[dict] = []

    def add(unit: dict, fallback_title: str = "") -> None:
        title = _title_of(unit) or fallback_title
        text = _text_of(unit)
        if not title and not text and not any(
            k in unit for k in ("include", "accepted_locations", "search_queries")
        ):
            return
        found.append({
            "title": title,
            "context_title": fallback_title,
            "text": text,
            "fields": unit,
        })

    chapters = content.get("chapters")
    if isinstance(chapters, list):
        for ch in chapters:
            if not isinstance(ch, dict):
                continue
            inner = ch.get("subjects")
            if isinstance(inner, list) and inner:
                for sub in inner:
                    if isinstance(sub, dict):
                        add(sub, fallback_title=_title_of(ch))
            else:
                add(ch)

    subjects = content.get("subjects")
    if isinstance(subjects, list):
        for sub in subjects:
            if isinstance(sub, dict):
                add(sub)

    # Top-level titled objects (not reserved hunt-array keys).
    reserved = {
        "chapters", "subjects", "include", "exclude_type",
        "accepted_locations", "search_queries", "seat_cap",
    }
    for key, val in content.items():
        if key in reserved:
            continue
        if isinstance(val, dict):
            add(val, fallback_title=str(key))
        elif isinstance(val, str) and val.strip():
            add({"title": str(key), "text": val})
        elif isinstance(val, list) and val and all(isinstance(x, str) for x in val):
            add({"title": str(key), "lines": val})
    return found

def compile_from_content(content, compiled_at: str | None = None,
                         engine_sha: str | None = None) -> dict:
    """Derive compiled_config from Brief.content only (Move 1).

    Structured keys win (search_queries / include / exclude_type /
    accepted_locations / priority_companies / seat_cap). Prose subjects
    contribute TITLES (role families) and PLACES only. No concept mining:
    CONTEXT and MANDATE are the model's job, from the JD and the
    Candidate Context, never a substring bag.

    families      = Brief role families as written (for the receipt)
    include       = families expanded through ROLE_SYNONYMS (substring gate)
    location_phrases = Brief places as written
    accepted_locations = places expanded through LOCATION_GAZETTEER
    exclude_type  = Brief exclusions ∪ ENGINE_DEFAULT_EXCLUDES
    search_queries = engine default market queries (Workday / Netflix)
    priority_companies = structured only

    READY iff ≥1 include phrase and ≥1 accepted location. Never 'limited'.
    """
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            content = {}
    if not isinstance(content, dict):
        content = {}

    bags = {
        "include": [],
        "exclude_type": [],
        "accepted_locations": [],
        "search_queries": [],
        "priority_companies": [],
        "seat_cap": DEFAULT_SEAT_CAP,
    }
    _collect_structured(content, bags)

    families: list[str] = []
    for item in bags["search_queries"]:
        _add_unique(families, _as_family(item))
    for item in bags["include"]:
        fam = _as_family(item)
        if _looks_like_role_title(item) or _looks_like_role_title(fam):
            _add_unique(families, fam)
        elif item and not _is_prose_scrap(item) and _norm_phrase(item) != "cd":
            # A structured include phrase that is not a recognisable title is
            # still an authorized substring (the Brief said so explicitly).
            _add_unique(families, _norm_phrase(item))

    location_phrases: list[str] = list(bags["accepted_locations"])
    subjects_used: list[str] = []
    for sub in extract_subjects(content):
        kind = _kind_of_subject(sub)
        if kind == "skip":
            continue
        _collect_structured(sub["fields"], bags)
        contributed = False
        if kind == "place":
            for loc in _extract_locations(sub["text"]):
                if _add_unique(location_phrases, loc):
                    contributed = True
        else:
            for fam in _extract_role_families(sub["text"]):
                if _add_unique(families, fam):
                    contributed = True
        if sub["title"] and (contributed or sub["text"] or kind in ("role", "place", "move")):
            for label in (sub.get("context_title"), sub["title"]):
                if label and label not in subjects_used:
                    subjects_used.append(label)
    for loc in bags["accepted_locations"]:
        _add_unique(location_phrases, loc)

    include: list[str] = []
    for fam in families:
        for phrase in expand_role_family(fam):
            _add_unique(include, phrase)

    accepted: list[str] = []
    unmapped: list[str] = []
    for phrase in location_phrases:
        tokens, mapped = expand_location_phrase(phrase)
        for t in tokens:
            _add_unique(accepted, t)
        if not mapped:
            _add_unique(unmapped, _norm_phrase(phrase))

    exclude: list[str] = []
    for x in list(bags["exclude_type"]) + list(ENGINE_DEFAULT_EXCLUDES):
        _add_unique(exclude, x)

    priority: list[str] = []
    for p in bags["priority_companies"]:
        if isinstance(p, str) and p.strip() and p.strip() not in priority:
            priority.append(p.strip())

    reasons: list[str] = []
    has_families = bool(include)
    has_locs = bool(accepted)
    if not has_families and not has_locs:
        reasons.append("no_usable_hunt_authority")
    if not has_families:
        reasons.append("no_role_families")
    if not has_locs:
        reasons.append("no_accepted_locations")
    for u in unmapped:
        reasons.append(f"unmapped_location_phrase:{u}")

    executable = has_families and has_locs
    readiness = "ready" if executable else "not_ready"
    if executable:
        # Unmapped phrases are informational only; they never block.
        reasons = [r for r in reasons if r.startswith("unmapped_location_phrase:")]

    compiled_at = compiled_at or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    cfg = {
        "subjects_used": subjects_used,
        "families": families,
        "include": include,
        "exclude_type": exclude,
        "location_phrases": location_phrases,
        "accepted_locations": accepted,
        "search_queries": search_queries_for(families),
        "priority_companies": priority,
        "seat_cap": bags["seat_cap"],
        "compiled_at": compiled_at,
        "engine_sha": engine_sha or current_engine_sha(),
        "readiness_reasons": reasons,
        "readiness_architecture": READINESS_ARCHITECTURE_NOTE,
    }
    cfg["_readiness"] = readiness  # stripped before persist
    return cfg


def readiness_of(compiled: dict) -> str:
    if compiled.get("_readiness") in ("ready", "not_ready"):
        return compiled["_readiness"]
    if compiled.get("include") and compiled.get("accepted_locations"):
        return "ready"
    return "not_ready"


def persistable_compiled(compiled: dict) -> dict:
    return {k: v for k, v in compiled.items() if k != "_readiness"}


def compiled_config_hash(compiled: dict) -> str:
    body = {
        "families": compiled.get("families") or [],
        "include": compiled.get("include") or [],
        "exclude_type": compiled.get("exclude_type") or [],
        "accepted_locations": compiled.get("accepted_locations") or [],
        "priority_companies": compiled.get("priority_companies") or [],
        "seat_cap": compiled.get("seat_cap"),
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


PROPOSAL_QUIET_MINUTES = 20


def _quiet_since(stamp: str, now: datetime) -> bool:
    """True when `stamp` (ISO, from the database) is older than
    PROPOSAL_QUIET_MINUTES relative to `now`. Unparseable → not quiet."""
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t) >= timedelta(minutes=PROPOSAL_QUIET_MINUTES)


def _proposal_context_hash(brief: dict) -> str:
    """The Candidate Context hash a FOOUND-written proposal was drafted
    from (content.provenance.candidate_context_hash), or "" for a Brief
    written by hand."""
    content = brief.get("content") or {}
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return ""
    prov = content.get("provenance") if isinstance(content, dict) else None
    return str((prov or {}).get("candidate_context_hash") or "") if isinstance(prov, dict) else ""


def current_engine_sha() -> str:
    return (
        os.environ.get("GITHUB_SHA")
        or os.environ.get("ENGINE_SHA")
        or "unknown"
    )


# ---------------------------------------------------------------------------
# role_key — durable NEW/RESURFACED identity. Precedence (first wins):
#   1. source-qualified provider posting ID → id:{source}:{posting_id}
#   2. canonical job/apply URL (lowercase, no trailing slash; strip tracking
#      junk; keep ATS job-id query params such as gh_jid)
#   3. normalized fallback tcl:{title}|{company}|{location}
# Never a bare id:{posting_id}: two ATSs/boards can share a numeric id.
# title|company alone is not durable enough: two openings can collapse, and
# a title tweak on the same posting must not look new.
# ---------------------------------------------------------------------------

_PROVIDER_ID_KEYS = (
    "posting_id", "provider_id", "external_id", "job_id",
    "requisition_id", "req_id", "position_id", "positionId",
    "gh_id", "ashby_id", "lever_id",
)
# Hunt-runner tags on a row, not an ATS/board namespace.
_HUNT_SOURCE_TAGS = frozenset({"adapter", "hunt"})
_SOURCE_FIELD_KEYS = ("provider", "ats")
_ID_KEY_SOURCE = {
    "gh_id": "greenhouse",
    "ashby_id": "ashby",
    "lever_id": "lever",
}
# Tracking / attribution only. Never put an ATS job-id key here.
_JUNK_QUERY = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "fbclid", "mc_cid", "mc_eid", "ref", "source",
    "gh_src",
}
# Identity-bearing ATS query keys found in this repo. Must survive canonicalize.
# gh_jid is the Greenhouse job id (e.g. stripe.com/jobs/search?gh_jid=…,
# careers.duolingo.com/jobs/…?gh_jid=…). No other ATS job-id query key
# appears in this repo; gh_src stays junk (source attribution).
_IDENTITY_QUERY = frozenset({"gh_jid"})


def canonical_job_url(url: str) -> str:
    if not url or not isinstance(url, str):
        return ""
    raw = url.strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.netloc and not parsed.path:
        return ""
    scheme = (parsed.scheme or "https").lower()
    if scheme not in ("http", "https"):
        scheme = "https"
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = (parsed.path or "").rstrip("/")
    kept = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=True):
        lk = k.lower()
        if lk in _IDENTITY_QUERY:
            kept.append((k, v))
            continue
        if lk in _JUNK_QUERY or lk.startswith("utm_"):
            continue
        kept.append((k, v))
    query = urlencode(kept, doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def _norm_source(value) -> str:
    if value is None or isinstance(value, bool):
        return ""
    text = _norm_phrase(str(value)).replace(" ", "-").replace(":", "-")
    return text.strip("-")


def _provider_posting_id(row: dict) -> tuple[str, str]:
    """Return (normalized posting id, field name that supplied it)."""
    for key in _PROVIDER_ID_KEYS:
        val = row.get(key)
        if val is None or isinstance(val, bool):
            continue
        text = str(val).strip()
        if text:
            return _norm_phrase(text), key
    return "", ""


def _provider_source(row: dict, id_key: str = "") -> str:
    """ATS/board namespace for id:{source}:{posting_id}. Never a hunt tag."""
    for key in _SOURCE_FIELD_KEYS:
        src = _norm_source(row.get(key))
        if src:
            return src
    src = _norm_source(row.get("source"))
    if src and src not in _HUNT_SOURCE_TAGS:
        return src
    return _ID_KEY_SOURCE.get(id_key, "")


def role_key(row, company: str | None = None) -> str:
    """Durable seat identity from an adapter row. Single definition.

    company, if given, overrides row company on the tcl: fallback only.
    A string first argument is not a key (the two-arg title/company form
    is gone).
    """
    if not isinstance(row, dict):
        return ""
    pid, id_key = _provider_posting_id(row)
    src = _provider_source(row, id_key)
    if pid and src:
        return f"id:{src}:{pid}"
    url = canonical_job_url(row.get("url") or "")
    if url:
        return f"url:{url}"
    return _fallback_role_key(
        row.get("title") or "",
        row.get("company") if company is None else company,
        row.get("location") or "",
    )


def _fallback_role_key(title: str, company: str, location: str) -> str:
    return "tcl:" + "|".join((
        _norm_phrase(title),
        _norm_phrase(company),
        _norm_phrase(location),
    ))


# ---------------------------------------------------------------------------
# Personal market memory — prior private editions.payload only.
# ---------------------------------------------------------------------------

def personal_history(prior_payloads: list[dict]) -> dict[str, str]:
    """role_key → earliest first_seen date (ISO). Never reads market_seen."""
    seen: dict[str, str] = {}
    for payload in prior_payloads:
        if not isinstance(payload, dict):
            continue
        seats = payload.get("seats")
        if not isinstance(seats, list):
            continue
        for seat in seats:
            if not isinstance(seat, dict):
                continue
            key = seat.get("role_key")
            if not isinstance(key, str) or not key:
                continue
            fs = seat.get("first_seen")
            if not isinstance(fs, str) or not fs:
                fs = ""
            if key not in seen or (fs and (not seen[key] or fs < seen[key])):
                seen[key] = fs
    return seen


def attach_market_fields(seats: list[dict], history: dict[str, str],
                         today: date) -> list[dict]:
    today_iso = today.isoformat()
    out = []
    for seat in seats:
        key = seat["role_key"]
        prev = key in history
        first = history.get(key) or today_iso
        if not first:
            first = today_iso
        row = dict(seat)
        row["first_seen"] = first
        row["previously_seen"] = prev
        row["new_or_resurfaced"] = "resurfaced" if prev else "new"
        row.setdefault("survived_because", [])
        row.setdefault("source", "hunt")
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Machine edition HTML — locked At Work seat shape {id, handle, line}.
# Original editorial slots (.plabel / .ptext) sit on the visible list item.
# ---------------------------------------------------------------------------

def coerce_fit(value) -> Optional[int]:
    """score_fit returns int 0–100 or None. Never treat bool as a score."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return max(0, min(100, value))
    return None


def assign_editorial_labels(seats: list[dict]) -> list[dict]:
    """Shortlist seclabels. Lead = highest fit among final seats.

    Original job_alerts contract after seats have fit:
    ranked.sort(key=lambda j: (j.get("fit") or -1), reverse=True)
    lead_job = ranked[0] → I'd start with {company}
    remaining fit>=80 → Unusually strong; else Worth your attention.
    Stable on ties. Does not re-sort the raw market.
    """
    ordered = sorted(seats, key=lambda j: (j.get("fit") or -1), reverse=True)
    out = []
    for i, seat in enumerate(ordered):
        row = dict(seat)
        if i == 0:
            row["lead"] = True
            company = row.get("company") or row.get("handle") or ""
            row["seclabel"] = f"I'd start with {company}"
        else:
            row["lead"] = False
            fit = coerce_fit(row.get("fit"))
            row["seclabel"] = (
                "Unusually strong" if (fit or 0) >= 80
                else "Worth your attention"
            )
        out.append(row)
    return out


def _fit_tier_label(score) -> str:
    """Reuse job_alerts.fit_tier. Do not rewrite the tiers."""
    return _import_job_alerts_adapters().fit_tier(score)


def _posted_mon_d(posted_at) -> str:
    """Original _entry posted span: `posted {Mon D}` via job_alerts._fmt_posted."""
    if posted_at in (None, ""):
        return ""
    ja = _import_job_alerts_adapters()
    pa = posted_at
    if isinstance(pa, str):
        pa = ja.parse_iso(pa)
        if pa is None:
            return ""
    return ja._fmt_posted(pa) or ""


def _seclabel_html(seat: dict) -> str:
    if seat.get("lead"):
        company = html.escape(seat.get("company") or seat.get("handle") or "")
        return (
            '<div class="seclabel" style="margin-top:5vh;">'
            f"I&rsquo;d start with {company}</div>"
        )
    label = html.escape(seat.get("seclabel") or "Worth your attention")
    return f'<div class="seclabel">{label}</div>'


def _meta_html(seat: dict) -> str:
    loc = html.escape((seat.get("location") or "").strip() or "Location not listed")
    posted = _posted_mon_d(seat.get("posted_at"))
    posted_html = (
        f'<span class="sep">/</span><span class="dim">posted {html.escape(posted)}</span>'
        if posted else ""
    )
    return (
        f'<div class="meta"><b>{loc}</b>'
        f'<span class="sep">/</span><span>Salary not posted</span>'
        f"{posted_html}</div>"
    )


def _plabel(label: str, text: str, escape: bool = True) -> str:
    body = html.escape(text) if escape else text
    return f'<div class="plabel">{label}</div><p class="ptext">{body}</p>'


def render_edition_html(seats: list[dict], context: dict | None = None) -> str:
    """The private edition, one artifact, engine-authored (Move 1).

    Locked for the At Work bind: #foound-seats stays [{id, handle, line}];
    every item carries data-id = role_key plus the original entry's data-*
    fields, .anno, .role, .scoreline, the three plabel/ptext slots (pause
    only when present), .meta and a.apply. Added for Move 3 (ignored by
    today's app): the voice (p.brief / .cascade / .statline), seclabels,
    "I kept looking", Found, not FOOUND, and the colophon.
    No dummy seats. No 'DUMMY ROLE' string.
    """
    context = context or {}
    seats = assign_editorial_labels(seats)
    picture = [
        {
            "id": s["role_key"],
            "handle": s.get("handle") or s.get("company") or s.get("title") or "",
            "line": s.get("line") or _seat_line(s),
        }
        for s in seats
    ]
    payload = json.dumps(picture, ensure_ascii=False, separators=(",", ":"))

    def esc(v) -> str:
        return html.escape(str(v or ""), quote=True)

    items = []
    prev_label = None
    for i, (s, pic) in enumerate(zip(seats, picture), start=1):
        label = s.get("seclabel") or ""
        if label and label != prev_label:
            items.append(_seclabel_html(s))
            prev_label = label
        fit = coerce_fit(s.get("fit"))
        anno = f'<span class="anno">{{fit&nbsp;{fit}}}</span>' if fit is not None else ""
        scoreline = ""
        if fit is not None:
            tier = html.escape(_fit_tier_label(fit))
            scoreline = f'<div class="scoreline">{fit} &middot; {tier}</div>'
        blocks = [_plabel("Why I chose it", s.get("ai_why") or "")]
        pause = (s.get("ai_pause") or "").strip()
        if pause:
            blocks.append(_plabel("What gives me pause", pause))
        blocks.append(_plabel("Why now", s.get("why_now") or "", escape=False))
        dl = s.get("deep") if isinstance(s.get("deep"), dict) else None
        if dl:
            rows = []
            for k, lab in (("role", "Role"), ("moment", "Moment"), ("leadership", "Leadership"),
                           ("signal", "Signal"), ("question", "Question")):
                v = dl.get(k)
                if v:
                    rows.append(f"<b>{lab}</b> &middot; {html.escape(str(v))}")
            blocks.append('<div class="plabel">I kept looking</div>'
                          f'<p class="ptext">{"<br>".join(rows)}</p>')
            if dl.get("verdict"):
                blocks.append('<p class="ptext" style="color:var(--ink);font-weight:500;">'
                              f'{html.escape(str(dl["verdict"]))}</p>')
        url = s.get("url") or ""
        is_new = (s.get("new_or_resurfaced") or "") == "new"
        newtag = '<span class="new">NEW</span>' if is_new and s.get("previously_seen") is False else ""
        items.append(
            f'<li class="item{" lead" if s.get("lead") else ""}" data-id="{esc(pic["id"])}"'
            f' data-handle="{esc(pic["handle"])}" data-line="{esc(pic["line"])}"'
            f' data-company="{esc(s.get("company"))}" data-title="{esc(s.get("title"))}"'
            f' data-location="{esc(s.get("location"))}" data-url="{esc(url)}"'
            f' data-fit="{fit if fit is not None else ""}"'
            f' data-posted-at="{esc(iso_posted_at(s.get("posted_at")))}"'
            f' data-why="{esc(s.get("ai_why"))}" data-pause="{esc(s.get("ai_pause"))}"'
            f' data-why-now="{esc(s.get("why_now"))}" data-new="{"1" if is_new else "0"}">'
            f'<button class="row" aria-expanded="false"><span class="marker"><span>{i}</span></span>'
            f'<span class="co">{esc(s.get("company"))}</span>{anno}</button>'
            f'<div class="panel"><div class="panel-inner">'
            f'<div class="role">{esc(s.get("title"))}{newtag}</div>'
            f'{scoreline}{"".join(blocks)}{_meta_html(s)}'
            f'<div class="actions"><a class="apply" href="{esc(url or "#")}" target="_blank" rel="noopener">Apply &#8599;</a></div>'
            f'</div></div></li>'
        )

    outcome = "empty" if not seats else "seats"
    voice = ""
    if context.get("greeting") or context.get("cascade") or context.get("statline"):
        voice = (
            f'<p class="brief">{html.escape(context.get("greeting") or "")}</p>'
            f'<div class="cascade">{"<br>".join(html.escape(x) for x in (context.get("cascade") or []))}</div>'
            f'<p class="statline">{context.get("statline") or ""}</p>'
        )
    if not seats:
        items.append('<li class="item"><div class="row" style="cursor:default;">'
                     '<span class="marker"></span>Nothing today.</div></li>')

    passed = ""
    shown = context.get("refused_shown") or []
    if seats and shown:
        word = "misses" if len(shown) > 1 else "miss"
        rows = []
        for r in shown:
            rfit = coerce_fit(r.get("fit"))
            pfit = f'<span class="pfit">{{fit&nbsp;{rfit}}}</span>' if rfit is not None else ""
            reason = html.escape(r.get("ai_pause") or "")
            if r.get("relook"):
                reason = '<b class="relook-tag">Looked again, as you asked.</b> ' + reason
            rows.append(
                f'<div class="pitem{" relook" if r.get("relook") else ""}" data-id="{esc(r.get("role_key"))}"'
                f' data-company="{esc(r.get("company"))}" data-title="{esc(r.get("title"))}"'
                f' data-fit="{rfit if rfit is not None else ""}">'
                f'<button class="prow" aria-expanded="false"><span class="pdot"></span>{esc(r.get("company"))}</button>'
                f'<div class="ppanel"><div class="ppanel-inner"><div class="pline">{esc(r.get("title"))}{pfit}</div>'
                f'<span class="preason">{reason}</span></div></div></div>'
            )
        total = int(context.get("refused_total") or len(shown))
        count_word = _count_word(len(shown))
        passed = (
            '<div class="seclabel pass">Found, not FOOUND</div>'
            f'<p class="passintro">{total} more read in full and declined. '
            f'The {count_word} nearest {word}, and why they failed:</p>'
            f'<div class="passed">{"".join(rows)}</div>'
        )

    colophon = ""
    if context.get("colophon"):
        c = context["colophon"]
        colophon = (
            '<footer><div class="colophon">'
            f'<div>FOOUND AT WORK &middot; Edition {html.escape(str(c.get("edition") or ""))}'
            f' &middot; {html.escape(str(c.get("datelong") or ""))}</div>'
            f'<div>Compiled {html.escape(str(c.get("compiled") or ""))}'
            f' &middot; {int(c.get("sources") or 0)} companies watched</div>'
            '</div></footer>'
        )

    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<title>FOOUND edition</title></head><body>"
        f"<script type=\"application/json\" id=\"foound-seats\">{payload}</script>"
        f"<article data-edition=\"{outcome}\" data-seat-count=\"{len(seats)}\">"
        f"{voice}<ol>{''.join(items)}</ol>{passed}"
        f"</article>{colophon}</body></html>"
    )


_COUNT_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten", "eleven")


def _count_word(n: int) -> str:
    return _COUNT_WORDS[min(max(n, 0), len(_COUNT_WORDS) - 1)]


def _seat_line(seat: dict) -> str:
    title = (seat.get("title") or "").strip()
    loc = (seat.get("location") or "").strip()
    if title and loc:
        return f"{title} — {loc}"
    return title or loc


def iso_posted_at(value) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def seat_payload(s: dict) -> dict:
    return {
        "role_key": s["role_key"],
        "title": s.get("title") or "",
        "company": s.get("company") or "",
        "location": s.get("location") or "",
        "url": s.get("url") or "",
        "first_seen": s.get("first_seen") or "",
        "previously_seen": bool(s.get("previously_seen")),
        "source": s.get("source") or "hunt",
        "new_or_resurfaced": s.get("new_or_resurfaced") or "new",
        "survived_because": list(s.get("survived_because") or []),
        "posted_at": iso_posted_at(s.get("posted_at")),
        "ai_why": s.get("ai_why") or "",
        "ai_pause": s.get("ai_pause") or "",
        "why_now": s.get("why_now") or "",
        "fit": coerce_fit(s.get("fit")),
        "tier": _fit_tier_label(coerce_fit(s.get("fit"))) if coerce_fit(s.get("fit")) is not None else "",
        "lead": bool(s.get("lead")),
        "seclabel": s.get("seclabel") or "",
    }


def refusal_payload(r: dict, relook: bool = False) -> dict:
    return {
        "role_key": r.get("role_key") or "",
        "company": r.get("company") or "",
        "title": r.get("title") or "",
        "location": r.get("location") or "",
        "fit": coerce_fit(r.get("fit")),
        "pause": r.get("ai_pause") or "",
        "why": r.get("ai_why") or "",
        "relook": bool(relook),
    }


def build_payload(seats: list[dict], compiled: dict, engine_sha: str,
                  ledger: dict | None = None) -> dict:
    """editions.payload. `ledger` (Move 1) carries counts, the complete
    refusal set, the ≤5 shown, unread role_keys, engine, brief_line, deep,
    read_budget. Lives only in the payload: owner-read, never logged."""
    labeled = assign_editorial_labels(seats)
    out = {
        "engine_sha": engine_sha,
        "compiled_config_hash": compiled_config_hash(compiled),
        "seats": [seat_payload(s) for s in labeled],
    }
    if ledger:
        out.update({
            "counts": dict(ledger.get("counts") or {}),
            "refused": list(ledger.get("refused") or []),
            "refused_shown": list(ledger.get("refused_shown") or []),
            "unread": list(ledger.get("unread") or []),
            "engine": ledger.get("engine") or "",
            "engine_reason": ledger.get("engine_reason") or "",
            "authority": dict(ledger.get("authority") or {}),
            "brief_line": ledger.get("brief_line") or "",
            "deep": ledger.get("deep"),
            "intelligence": dict(ledger.get("intelligence") or {}),
            "read_budget": ledger.get("read_budget"),
            "candidate_context": ledger.get("candidate_context") or {},
        })
    return out


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _silent_stdio():
    """Swallow score_fit / fetch prints. Public Actions logs must not
    contain titles, URLs, prompts, or model output."""
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        yield

@contextlib.contextmanager
def _captured_stdio():
    """Like _silent_stdio, but yields the buffer so the caller can classify
    the original loop's own failure report. The text may contain model
    output: classify it, then drop it. Never log or persist it."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield buf


DEEP_LOOK_THRESHOLD = 80   # the original loop's trigger; unchanged

DEEP_REASONS = ("ok", "not_run", "not_triggered", "http_4xx", "http_5xx",
                "no_json", "truncated", "paused", "thin_reply", "error")
STATLINE_REASONS = ("ok", "not_run", "empty", "http_4xx", "http_5xx",
                    "unusable_reply", "error")


def _http_class(text: str, marker: str) -> str:
    m = re.search(marker + r"\s*(\d{3})", text)
    if not m:
        return ""
    return "http_4xx" if m.group(1).startswith("4") else "http_5xx"


def classify_deep_look(out, captured: str) -> str:
    """deep_look → enum. `out` is its return value; `captured` its stdout."""
    if out:
        return "ok"
    t = captured or ""
    if "[deep look skipped: HTTP" in t:
        return _http_class(t, r"\[deep look skipped: HTTP") or "error"
    if "no JSON in reply" in t:
        if "stop_reason=max_tokens" in t:
            return "truncated"   # the research turn ran out of output budget
        if "stop_reason=pause_turn" in t:
            return "paused"      # server paused the turn and it was not resumed
        return "no_json"
    if "[deep look skipped:" in t:
        return "error"
    return "thin_reply"          # returned None without a report: reply too thin


def classify_brief_line(line, captured: str) -> str:
    """write_brief → enum. Empty is a legitimate answer (nothing notable)."""
    if line:
        return "ok"
    t = captured or ""
    if "[Brief API" in t:
        return _http_class(t, r"\[Brief API") or "error"
    if "unusable reply" in t:
        return "unusable_reply"
    if "[Brief error]" in t:
        return "error"
    return "empty"


def _has_anthropic_key(ja) -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or getattr(ja, "ANTHROPIC_KEY", ""))

def _lookup_agent_no(db, agent_id) -> Optional[int]:
    if not agent_id or db is None:
        return None
    fn = getattr(db, "agent_no", None)
    if not callable(fn):
        return None
    try:
        n = fn(agent_id)
    except Exception:
        return None
    try:
        return int(n) if n is not None else None
    except (TypeError, ValueError):
        return None

# ---------------------------------------------------------------------------
# Brief → AgentConfig (the original vocabulary contract), verdict exclusion,
# Candidate Context. The Brief is authority; the AgentConfig is how the
# original judgment loop consumes it.
# ---------------------------------------------------------------------------

def _adapter_labels(ja) -> dict[str, str]:
    """lowercase label → adapter label, from job_alerts.SCRAPERS."""
    out: dict[str, str] = {}
    for entry in getattr(ja, "SCRAPERS", []) or []:
        try:
            label = entry[0]
        except (IndexError, TypeError):
            continue
        if isinstance(label, str) and label:
            out[label.lower()] = label
    return out


def agent_config_from_brief(ja, compiled: dict, *, agent_id: str,
                            agent_no: int | None, name: str = "",
                            evidence_map=None, profile_path: str = "",
                            voice=None):
    """Build the AgentConfig the original loop judges with.

    Everything here is Brief-derived or engine-default. Nothing is copied
    from BOOTSTRAP_001 except, for №001, the profile path and the evidence
    map of its interim Candidate Context (Move 2 replaces both).
    """
    cfg_cls = getattr(ja, "AgentConfig")
    labels = _adapter_labels(ja)
    priority = set()
    for p in compiled.get("priority_companies") or []:
        priority.add(labels.get(str(p).lower(), str(p)))
    return cfg_cls(
        agent_no=int(agent_no or 0),
        agent_id=str(agent_id),
        name=name or "",
        recipient_email="",
        profile_path=profile_path or "",
        output_dir="editions",
        edition_url="",
        publish_public=False,
        include=list(compiled.get("include") or []),
        exclude_type=list(compiled.get("exclude_type") or []),
        accepted_locations=list(compiled.get("accepted_locations") or []),
        priority_companies=priority,
        search_queries=list(compiled.get("search_queries") or ENGINE_DEFAULT_SEARCH_QUERIES),
        market_sources=[],
        manual_jobs=[],
        evidence_map=list(evidence_map or []),
        email_footer=[],
        # Judge voice (Move 2): №001 keeps the original prompt literals via his
        # bootstrap; everyone else is "one client", neutral third person.
        persona=getattr(voice, "persona", "") if voice is not None else "",
        pronouns=tuple(getattr(voice, "pronouns", ()) or ("they", "them", "their")) if voice is not None else ("they", "them", "their"),
        judgment_lenses=getattr(voice, "judgment_lenses", "") if voice is not None else "",
    )


_ROLE_KEY_PREFIXES = ("id:", "url:", "tcl:")


def is_role_key(value: str) -> bool:
    """A key produced by role_key(). Anything else is legacy-shaped."""
    v = str(value or "")
    return v.startswith(_ROLE_KEY_PREFIXES)


def split_verdict_keys(keys) -> tuple[set[str], set[str]]:
    """(exact role_keys, legacy title|company keys)."""
    exact, legacy = set(), set()
    for k in keys or ():
        s = str(k or "")
        if not s:
            continue
        (exact if is_role_key(s) else legacy).add(s)
    return exact, legacy


def _legacy_key(ja, job: dict) -> str:
    return ja.dedup_key(job.get("title") or "", job.get("company") or "")


def verdict_matches(ja, job: dict, exact: set[str], legacy: set[str]) -> bool:
    """Exact role_key wins. Legacy title|company is a read-only
    compatibility fallback for signals that predate the identity scheme.
    A url:/id:/tcl: key never falls back to the legacy match."""
    rk = role_key(job)
    if rk and rk in exact:
        return True
    if legacy and _legacy_key(ja, job) in legacy:
        return True
    return False


def apply_verdicts(ja, jobs: list[dict], state) -> tuple[list[dict], set[str], dict]:
    """Remove PASS / APPLIED roles; return (kept, second_look_role_keys, counts).

    state: foound_state.PrivateState (or None = no verdict loop configured).
    """
    if state is None:
        return list(jobs), set(), {"excluded": 0, "second_look": 0, "legacy_hits": 0}
    ex_exact, ex_legacy = split_verdict_keys(getattr(state, "excluded_keys", set()))
    sl_exact, sl_legacy = split_verdict_keys(getattr(state, "second_look_keys", set()))
    kept: list[dict] = []
    second_look: set[str] = set()
    excluded = 0
    legacy_hits = 0
    for job in jobs:
        rk = role_key(job)
        if verdict_matches(ja, job, ex_exact, ex_legacy):
            excluded += 1
            if rk not in ex_exact:
                legacy_hits += 1
            continue
        if verdict_matches(ja, job, sl_exact, sl_legacy):
            second_look.add(rk)
            if rk not in sl_exact:
                legacy_hits += 1
        kept.append(job)
    return kept, second_look, {
        "excluded": excluded, "second_look": len(second_look),
        "legacy_hits": legacy_hits,
    }


def load_verdict_state(agent_id: str, agent_no: int | None):
    """foound_state.load_private_state for a UUID agent. Fail closed on
    'unreachable' (raises); 'unconfigured' returns an empty state."""
    import foound_state as _fstate  # local import: job_alerts also imports it
    snapshot_dir = os.environ.get("FOOUND_STATE_DIR", ".foound-state")
    # agent_id is already the database UUID here; never resolve by number.
    return _fstate.load_private_state(agent_id, snapshot_dir=snapshot_dir,
                                      agent_no=None, published_dir="docs")


class CandidateContext:
    """What the judge will read about this person, and where it came from.

    kind      "memory"      compiled from confirmed Memory (Move 2, any client)
              "profile.md"  №001's interim document, used only while he has
                            confirmed nothing yet
              ""            none — the hunt refuses before any model call
    """
    __slots__ = ("kind", "text", "hash", "statements", "layers", "sources",
                 "format", "base", "evidence_map", "name")

    def __init__(self, kind="", text="", hash="", statements=0, layers=None,
                 sources=None, format=0, base=None, evidence_map=None, name=""):
        self.kind = kind; self.text = text; self.hash = hash
        self.statements = statements; self.layers = dict(layers or {})
        self.sources = list(sources or []); self.format = format
        self.base = base; self.evidence_map = list(evidence_map or []); self.name = name

    def receipt(self) -> dict:
        """Ledger entry: enums, counts and a hash. Never the text."""
        return {"kind": self.kind, "hash": self.hash, "statements": self.statements,
                "layers": self.layers, "sources": self.sources, "format": self.format}


def candidate_context(ja, agent_id: str, agent_no: int | None,
                      memory_rows=None, brief_content=None, name: str = "") -> CandidateContext:
    """Confirmed Memory → Candidate Context, for any client.

    Move 2: the document the original judge reads is compiled from the
    rows the client confirmed, plus the active Brief's own words. A client
    who has confirmed nothing has no context → HuntError('no_candidate_context')
    is raised by the caller before collection and before any model call.
    №001 alone keeps `profile.md` as an interim while his confirmed set is
    empty; the moment he confirms, Memory wins, like everyone else.
    """
    import candidate_context as cc
    compiled = cc.compile_candidate_context(name=name, rows=memory_rows or [],
                                            brief_content=brief_content)
    if compiled["text"]:
        base = None
        if agent_no == 1 or str(agent_id or "") in ("001", "1"):
            base = _bootstrap_001(ja)          # evidence links for the public candidate page
        return CandidateContext(kind="memory", text=compiled["text"], hash=compiled["hash"],
                                statements=compiled["statements"], layers=compiled["layers"],
                                sources=compiled["sources"], format=compiled["format"],
                                base=base, evidence_map=getattr(base, "evidence_map", []) if base else [],
                                name=name or (getattr(base, "name", "") if base else ""))
    if agent_no == 1 or str(agent_id or "") in ("001", "1"):
        base = _bootstrap_001(ja)
        if base is not None and getattr(base, "profile_path", ""):
            with _silent_stdio():
                profile = ja.load_profile(base) or ""
            if profile:
                return CandidateContext(kind="profile.md", text=profile,
                                        hash=hashlib.sha256(profile.encode("utf-8")).hexdigest(),
                                        statements=0, base=base,
                                        evidence_map=list(getattr(base, "evidence_map", []) or []),
                                        name=getattr(base, "name", ""))
    return CandidateContext()


def _bootstrap_001(ja):
    loader = getattr(ja, "load_agent_config", None)
    if not callable(loader):
        return None
    try:
        return loader("001")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# The hunt — one judge. Brief defines the universe; job_alerts judges it.
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _judgment_hooks(ja, *, fetch_jd=None, score=None, profile=None,
                    deep=None, brief=None, wrap_score=None):
    """Test seam. Temporarily point the original loop's model calls at
    injected callables. `score` is the CALLER's scorer: None means the live
    path, and on the live path deep_look / write_brief stay the originals.
    `wrap_score` decorates whichever scorer is active (live or injected) —
    the read counters use it — without changing that live/test decision.
    (v1.2 passed the counting wrapper as `score`, which made every live run
    look injected and silently disabled the deep look and the statline.)"""
    saved = {}

    def _set(name, value):
        saved.setdefault(name, getattr(ja, name))   # first value wins: restore the original
        setattr(ja, name, value)

    try:
        if fetch_jd is not None:
            _set("fetch_jd_text", fetch_jd)
        if score is not None:
            _set("score_fit", score)
            if not getattr(ja, "ANTHROPIC_KEY", ""):
                _set("ANTHROPIC_KEY", "injected")
        if wrap_score is not None:
            _set("score_fit", wrap_score(getattr(ja, "score_fit")))
        if profile is not None:
            _set("load_profile", lambda _agent: profile)
        _set("deep_look", deep if deep is not None else (getattr(ja, "deep_look") if score is None else (lambda *_a, **_k: None)))
        _set("write_brief", brief if brief is not None else (getattr(ja, "write_brief") if score is None else (lambda *_a, **_k: None)))
        yield
    finally:
        for name, value in saved.items():
            setattr(ja, name, value)


def _daypart(hour: int) -> str:
    return "morning" if hour < 12 else ("afternoon" if hour < 18 else "evening")


def agent_timezone(agent_no: int | None) -> str:
    """Move 1: №001 keeps the original edition's Eastern clock; everyone
    else reads UTC until Move 2 carries a timezone per agent."""
    return "America/New_York" if agent_no == 1 else "UTC"


def local_now(now: datetime, agent_no: int | None) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo(agent_timezone(agent_no)))
    except Exception:
        return now


def compiled_clock(now: datetime, agent_no: int | None) -> str:
    """Colophon 'Compiled …' — the actual run time in the agent's timezone."""
    ln = local_now(now, agent_no)
    return ln.strftime("%-I:%M %p ") + (ln.tzname() or "UTC")


def brief_content_hash(content) -> str:
    """Fingerprint of the authority the hunt compiled from (Brief.content)."""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            content = {"_raw": content}
    blob = json.dumps(content if content is not None else {}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


ENGINE_REASONS = ("ai", "no_key", "authentication_failed",
                  "all_model_reads_failed", "no_candidate_context")


def classify_model_failure(ja) -> str:
    """After AI judgment has already failed: one minimal probe call to name
    the class. Returns an ENGINE_REASONS value. Never logs the response."""
    key = getattr(ja, "ANTHROPIC_KEY", "") or ""
    if not key:
        return "no_key"
    try:
        import requests
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": getattr(ja, "CLAUDE_MODEL", "claude-sonnet-5"),
                  "max_tokens": 1, "messages": [{"role": "user", "content": "OK"}]},
            timeout=30,
        )
        if r.status_code in (401, 403):
            return "authentication_failed"
        if r.status_code == 400:
            # Identity-linked keys without a workspace header answer 400 here.
            try:
                msg = str((r.json().get("error") or {}).get("message") or "")
            except Exception:
                msg = ""
            if "workspace" in msg.lower() or "authenticat" in msg.lower():
                return "authentication_failed"
        return "all_model_reads_failed"
    except Exception:
        return "all_model_reads_failed"


def run_hunt(ja, *, agent_id: str, agent_no: int | None, brief: dict,
             compiled: dict, raw: list[dict], prior_payloads: list[dict],
             state, today: date, now: datetime, profile: str | None = None,
             memory_rows=None,
             fetch_jd=None, score=None, deep=None, brief_line_fn=None,
             name: str = "", edition_no: int | None = None,
             sources: int | None = None, read_budget: int = PRIVATE_READ_BUDGET,
             model_probe=None) -> dict:
    """Stages 2–11 of the Move 1 loop, pure with respect to the database.

    Returns {seats, html, payload, outcome, counts, engine}. Raises
    HuntError('no_candidate_context') before any model call when the client
    has no Candidate Context (Move 1: only №001 has one, via profile.md).
    """
    # 2. authority → AgentConfig ------------------------------------------
    ctx = candidate_context(ja, agent_id, agent_no, memory_rows=memory_rows,
                            brief_content=brief.get("content"), name=name)
    if profile is not None:               # test seam: an injected document
        ctx = CandidateContext(kind="injected", text=profile, base=ctx.base,
                               evidence_map=ctx.evidence_map, name=ctx.name)
    ctx_profile = ctx.text
    base = ctx.base
    if not ctx_profile:
        raise HuntError("no_candidate_context")
    agent = agent_config_from_brief(
        ja, compiled, agent_id=agent_id, agent_no=agent_no,
        name=name or ctx.name,
        evidence_map=ctx.evidence_map,
        profile_path=getattr(base, "profile_path", "") if base is not None else "",
        voice=base,
    )
    if not agent.include:
        raise HuntError("no_role_families")
    seat_cap = int(compiled.get("seat_cap") or DEFAULT_SEAT_CAP)
    seat_cap = max(1, min(seat_cap, MAX_SEAT_CAP))

    # 4. eligibility: ROLE + LOCATION, dedup on role_key ---------------------
    eligible: list[dict] = []
    seen: set[str] = set()
    for job in raw or []:
        if not isinstance(job, dict):
            continue
        title = job.get("title") or ""
        loc = job.get("location") or ""
        if not ja.passes_title(agent, title):
            continue
        if not ja.passes_location(agent, loc):
            continue
        key = role_key(job)
        if not key or key in seen:
            continue
        seen.add(key)
        row = dict(job)
        row["role_key"] = key
        row.setdefault("source", "hunt")
        eligible.append(row)
    n_eligible = len(eligible)

    # 5. the person's verdicts leave the universe ----------------------------
    eligible, second_look, vcounts = apply_verdicts(ja, eligible, state)

    # 6. personal history ---------------------------------------------------
    history = personal_history(prior_payloads or [])
    eligible = attach_market_fields(eligible, history, today)
    new_keys = {j["role_key"] for j in eligible if j.get("new_or_resurfaced") == "new"}
    key_fn = lambda j: j.get("role_key") or role_key(j)

    # 8. judgment: the original loop, private budget -------------------------
    model_reads = {"attempted": 0, "failed": 0}

    def counted(real_score):
        def counted_score(a, p, job, jd):
            model_reads["attempted"] += 1
            out = real_score(a, p, job, jd)
            if not out or out[0] is None:
                model_reads["failed"] += 1
            return out
        return counted_score

    had_key = bool(getattr(ja, "ANTHROPIC_KEY", ""))
    with _judgment_hooks(ja, fetch_jd=fetch_jd, score=score, profile=ctx_profile,
                         deep=deep, brief=brief_line_fn, wrap_score=counted):
        if score is None and not had_key:
            ja.ANTHROPIC_KEY = ""   # keep the live no-key path honest under the hook
        with _silent_stdio():
            ranked_all, used_ai = ja.rank_with_fit(
                agent, eligible, new_keys, second_look,
                read_budget=read_budget, key_fn=key_fn,
            )
        read_ids = {id(j) for j in ranked_all}
        unread = [j["role_key"] for j in eligible if id(j) not in read_ids]

        # 9. seating: floor · cap · priority · lead · refusals -----------------
        seating = ja.seat_edition(agent, ranked_all, used_ai, second_look,
                                  key_fn=key_fn, cap=seat_cap)
        ranked = seating["ranked"]
        rejects = seating["rejects"]
        relook_ids = {id(j) for j in ranked_all if key_fn(j) in (second_look or set())}
        shown = seating["shown"]

        # 10. deep look on the lead; the statline observation ------------------
        # Both are the original loop's calls. Each reports its failure class
        # to stdout, which the private path swallows; the captured text is
        # classified into an enum and discarded — never logged, never stored.
        deep_out = None
        n = len(ranked)
        deep_reason = "not_run"
        if used_ai and n > 0:
            deep_reason = "not_triggered"
            if ((ranked[0].get("fit") or 0) >= DEEP_LOOK_THRESHOLD
                    or ranked[0].get("company") in agent.priority_companies):
                with _captured_stdio() as buf:
                    deep_out = ja.deep_look(ranked[0], ctx_profile, agent=agent)
                deep_reason = classify_deep_look(deep_out, buf.getvalue())
                if deep_out:
                    ranked[0]["deep"] = deep_out
        brief_line = None
        brief_reason = "not_run"
        if n > 0 and used_ai:
            # write_brief tests newness with the Shortlist's title|company key.
            legacy_new = {ja.dedup_key(j.get("title") or "", j.get("company") or "")
                          for j in ranked if j.get("role_key") in new_keys}
            with _captured_stdio() as buf:
                brief_line = ja.write_brief(n, len(raw or []), sources or 0, ranked, legacy_new,
                                            agent=agent, as_of=compiled_clock(now, agent_no))
            brief_reason = classify_brief_line(brief_line, buf.getvalue())

        # 11. why now, labels -------------------------------------------------
        as_of = compiled_clock(now, agent_no)
        for j in ranked_all:
            j["why_now"] = ja.why_now_text(j, j["role_key"] in new_keys, now=now, as_of=as_of)

    seats = assign_editorial_labels(ranked)
    n_read = sum(1 for j in ranked_all if j.get("fit") is not None) if used_ai else len(ranked_all)

    # engine reason (enum only) ----------------------------------------------
    if used_ai:
        engine_reason = "ai"
    elif not had_key and score is None:
        engine_reason = "no_key"
    elif model_reads["attempted"] and model_reads["failed"] >= model_reads["attempted"]:
        probe = model_probe if model_probe is not None else classify_model_failure
        engine_reason = probe(ja) if model_probe is None else model_probe()
        if engine_reason not in ENGINE_REASONS or engine_reason == "ai":
            engine_reason = "all_model_reads_failed"
    else:
        engine_reason = "all_model_reads_failed"

    # voice -------------------------------------------------------------------
    voice_name = name or (getattr(agent, "name", "") or "")
    daypart = _daypart(local_now(now, agent_no).hour)
    greeting = f"Good {daypart}, {voice_name}." if voice_name else f"Good {daypart}."
    cascade = [f"I searched {len(raw or []):,} jobs overnight."]
    if n == 0:
        cascade.append("Nothing cleared the bar today.")
    else:
        cascade.append(f"FOOUND {n} for you.")
        if seating["n_strong"] >= 2:
            cascade.append(f"{seating['n_strong']} are unusually strong.")
        if seating["has_standout"]:
            cascade.append("1 stands apart.")
    statline = f"{n_read} read in full &middot; everything else dismissed on sight."
    if brief_line:
        statline += " " + html.escape(brief_line)

    counts = {
        "market_fetched": len(raw or []),
        "eligible": n_eligible,
        "excluded": vcounts["excluded"],
        "second_look": vcounts["second_look"],
        "legacy_hits": vcounts["legacy_hits"],
        "read": n_read,
        "unread": len(unread),
        "seated": n,
        "refused": len(rejects),
        "model_reads_attempted": model_reads["attempted"],
        "model_reads_failed": model_reads["failed"],
    }
    ledger = {
        "counts": counts,
        "refused": [refusal_payload(r, relook=id(r) in relook_ids) for r in rejects],
        "refused_shown": [r["role_key"] for r in shown],
        "unread": unread,
        "engine": "ai" if used_ai else "heuristic",
        "engine_reason": engine_reason,
        "authority": {
            "brief_id": str(brief.get("id") or ""),
            "brief_version": brief.get("version"),
            "brief_content_hash": brief_content_hash(brief.get("content")),
            "compiled_config_hash": compiled_config_hash(compiled),
            "compiled_at_hunt": True,
            "stored_readiness": brief.get("readiness"),
            "hunt_readiness": readiness_of(compiled),
            "compiler_engine_sha": current_engine_sha(),
        },
        "brief_line": brief_line or "",
        "deep": deep_out,
        "intelligence": {"deep": deep_reason, "statline": brief_reason},
        "read_budget": read_budget,
        "candidate_context": ctx.receipt(),
    }
    context = {
        "greeting": greeting,
        "cascade": cascade,
        "statline": statline,
        "refused_shown": [dict(refusal_payload(r, relook=id(r) in relook_ids),
                               ai_pause=r.get("ai_pause") or "") for r in shown],
        "refused_total": len(rejects),
        "colophon": {
            "edition": f"{edition_no:03d}" if edition_no else "",
            "datelong": today.strftime("%A, %B %d, %Y").replace(" 0", " "),
            "compiled": compiled_clock(now, agent_no),
            "sources": sources or 0,
        },
    }
    html_doc = render_edition_html(seats, context)
    payload = build_payload(seats, compiled, current_engine_sha(), ledger)
    return {
        "seats": seats,
        "html": html_doc,
        "payload": payload,
        "outcome": "empty" if not seats else "seats",
        "counts": counts,
        "engine": ledger["engine"],
        "engine_reason": engine_reason,
        "intelligence": ledger["intelligence"],
        "authority": ledger["authority"],
    }


def draft_with_model(ja, prompt: str) -> str:
    """One Messages call for the Brief draft. Returns the reply text ('' on
    any failure). Nothing about the reply is logged here."""
    key = getattr(ja, "ANTHROPIC_KEY", "") or ""
    if not key:
        return ""
    try:
        import requests
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": getattr(ja, "CLAUDE_MODEL", "claude-sonnet-5"),
                  "max_tokens": 2500, "messages": [{"role": "user", "content": prompt}]},
            timeout=180,
        )
        if not r.ok:
            return ""
        blocks = (r.json() or {}).get("content", []) or []
        return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    except Exception:
        return ""


def operator_line(agent_no, counts: dict, engine: str, outcome: str,
                  engine_reason: str = "", authority: dict | None = None,
                  intelligence: dict | None = None) -> str:
    """One public-safe line: numbers, enums and hashes only."""
    c = counts or {}
    a = authority or {}
    tail = ""
    if engine_reason:
        tail += f" reason={engine_reason}"
    if intelligence:
        tail += (f" deep={intelligence.get('deep', '')}"
                 f" statline={intelligence.get('statline', '')}")
    if a:
        tail += (f" brief={str(a.get('brief_content_hash') or '')[:8]}"
                 f" compile={str(a.get('compiled_config_hash') or '')[:8]}"
                 f" v={a.get('brief_version')}")
    return (
        f"[operator] agent=№{int(agent_no or 0):03d} "
        f"market={c.get('market_fetched', 0)} eligible={c.get('eligible', 0)} "
        f"excluded={c.get('excluded', 0)} read={c.get('read', 0)} "
        f"unread={c.get('unread', 0)} seated={c.get('seated', 0)} "
        f"refused={c.get('refused', 0)} engine={engine} outcome={outcome}{tail}"
    )


HUNT_PUBLISH_PUBLIC = False  # this engine never publishes


def _import_job_alerts_adapters():
    """Lazy import. job_alerts.py requires publisher secrets at import unless
    they are present; hunt.yml does not have those secrets. Stub empties so
    the module can load. Never call publish_shortlist / main / send_email."""
    for key in (
        "NOTION_TOKEN", "NOTION_DB_ID", "GMAIL_USER",
        "GMAIL_APP_PASSWORD", "RECIPIENT_EMAIL",
    ):
        os.environ.setdefault(key, "")
    import job_alerts as ja  # local import: tests never take this path
    return ja

def live_collect(compiled: dict, scraper_entries=None) -> list[dict]:
    """Reuse job_alerts scrapers as adapters. publish_public stays false.

    Individual source failures are skipped. A completed collect returning
    zero rows is an empty market, not a technical failure.
    scraper_entries: optional override (tests / isolation smoke). When
    omitted, uses job_alerts.SCRAPERS after a lazy import.
    """
    assert HUNT_PUBLISH_PUBLIC is False
    ja = _import_job_alerts_adapters()
    ja.MARKET_QUERIES = list(compiled.get("search_queries") or [])
    sources = scraper_entries if scraper_entries is not None else getattr(ja, "SCRAPERS", [])
    raw: list[dict] = []
    log.info("collect sources=%d queries=%d", len(sources), len(ja.MARKET_QUERIES))
    for i, entry in enumerate(sources):
        fn = entry[1]
        args = entry[2:]
        try:
            with _silent_stdio():
                jobs = fn(*args)
        except Exception:
            log.info("adapter source_failed i=%d", i)
            continue
        n = 0
        for j in jobs or []:
            if not isinstance(j, dict):
                continue
            row = dict(j)
            row.setdefault("source", "adapter")
            raw.append(row)
            n += 1
        log.info("adapter source_ok i=%d n=%d", i, n)
    log.info("collect raw=%d", len(raw))
    return raw


# ---------------------------------------------------------------------------
# Database transports
# ---------------------------------------------------------------------------

class HuntDb:
    def oldest_queued_hunt_jobs(self, limit: int) -> list[dict]:
        raise NotImplementedError

    def claim(self, job_id: str) -> Optional[dict]:
        raise NotImplementedError

    def complete(self, job_id: str) -> None:
        raise NotImplementedError

    def fail(self, job_id: str, error: str) -> None:
        raise NotImplementedError

    def active_brief(self, agent_id: str) -> Optional[dict]:
        raise NotImplementedError

    def write_compile(self, brief_id: str, compiled: dict, readiness: str) -> None:
        raise NotImplementedError

    def editions_for_day(self, agent_id: str, day: date) -> list[dict]:
        raise NotImplementedError

    def prior_edition_payloads(self, agent_id: str) -> list[dict]:
        raise NotImplementedError

    def insert_edition(self, row: dict) -> None:
        raise NotImplementedError

    def replace_edition(self, edition_id: str, row: dict) -> None:
        """The day's edition, rewritten from a Brief that came into force
        after it was made. One edition per day is a schema fact; the newer
        Brief's hunt is the day's record."""
        raise NotImplementedError

    def edition_by_id(self, edition_id: str) -> Optional[dict]:
        raise NotImplementedError


    def agent_no(self, agent_id: str) -> Optional[int]:
        raise NotImplementedError

    def agent_id_for_no(self, agent_no: int) -> Optional[str]:
        """Read-only: the agent UUID for a public number (№001 → uuid)."""
        raise NotImplementedError

    def confirmed_memory(self, agent_id: str) -> list[dict]:
        """Read-only: this agent's active, client-confirmed memory rows
        (id, layer, statement, source, provenance, status, created_at)."""
        raise NotImplementedError

    def at_work_agents(self) -> list[dict]:
        """Read-only: agents in state at_work (id, agent_no)."""
        raise NotImplementedError

    def enqueue_job(self, agent_id: str, job_type: str, payload: dict) -> bool:
        """Insert a queued job; False when one is already queued for
        (agent, type) — the partial unique index makes this idempotent."""
        raise NotImplementedError

    def next_brief_version(self, agent_id: str) -> int:
        raise NotImplementedError

    def abandon_proposed_briefs(self, agent_id: str) -> int:
        """Engine-written proposals that were never confirmed: proposed → abandoned."""
        raise NotImplementedError

    def insert_brief(self, row: dict) -> None:
        raise NotImplementedError

    # -- Move 2: FOOUND proposes on its own (sweep_proposals) -----------------
    def live_agents(self) -> list[dict]:
        """Read-only: every agent not archived (id, agent_no, state)."""
        raise NotImplementedError

    def briefs_in_force(self, agent_id: str) -> list[dict]:
        """Read-only: this agent's proposed and active Brief rows
        (id, version, state, content, readiness), newest version first."""
        raise NotImplementedError

    def open_mirror_count(self, agent_id: str) -> int:
        """Read-only: how many active memory rows the client can still see
        awaiting their verdict (provenance stated/extracted/inferred, with a
        handle, in layers record/self/model — exactly what the Mirror
        renders). 0 = Mirror settled."""
        raise NotImplementedError

    def last_job(self, agent_id: str, job_type: str) -> Optional[dict]:
        """Read-only: the most recent job of this type for this agent
        (id, status, requested_at, payload), or None."""
        raise NotImplementedError

    # -- Move 4: the Candidate page ---------------------------------------
    def candidate_count(self, agent_id: str) -> int:
        """Read-only: how many candidates rows this agent has (any state)."""
        raise NotImplementedError

    def next_candidate_version(self, agent_id: str) -> int:
        raise NotImplementedError

    def retire_candidate_drafts(self, agent_id: str) -> int:
        """Engine-written drafts the person never published: draft → unpublished."""
        raise NotImplementedError

    def insert_candidate(self, row: dict) -> None:
        raise NotImplementedError


class RestDb(HuntDb):
    """Production transport: Supabase PostgREST with the service key."""

    def __init__(self, base_url: str, service_key: str):
        import requests

        self._requests = requests
        self.base = base_url.rstrip("/")
        self.headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def _get(self, path: str) -> list[dict]:
        r = self._requests.get(
            f"{self.base}/rest/v1/{path}", headers=self.headers, timeout=60,
        )
        r.raise_for_status()
        return r.json()

    def _patch(self, path: str, body: dict) -> list[dict]:
        r = self._requests.patch(
            f"{self.base}/rest/v1/{path}", headers=self.headers,
            data=json.dumps(body), timeout=60,
        )
        r.raise_for_status()
        if not r.content:
            return []
        return r.json()

    def _post(self, path: str, body: dict) -> list[dict]:
        r = self._requests.post(
            f"{self.base}/rest/v1/{path}", headers=self.headers,
            data=json.dumps(body), timeout=60,
        )
        r.raise_for_status()
        if not r.content:
            return []
        return r.json()

    def oldest_queued_hunt_jobs(self, limit: int) -> list[dict]:
        types = ",".join(HUNT_JOB_TYPES)
        return self._get(
            f"jobs?type=in.({types})&status=eq.queued"
            f"&select=id,agent_id,type,payload,requested_at"
            f"&order=requested_at.asc&limit={int(limit)}"
        )

    def claim(self, job_id: str) -> Optional[dict]:
        started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self._patch(
            f"jobs?id=eq.{job_id}&status=eq.queued",
            {"status": "running", "started_at": started},
        )
        return rows[0] if rows else None

    def complete(self, job_id: str) -> None:
        done = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._patch(f"jobs?id=eq.{job_id}", {
            "status": "done", "completed_at": done, "error": None,
        })

    def fail(self, job_id: str, error: str) -> None:
        done = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._patch(f"jobs?id=eq.{job_id}", {
            "status": "failed", "completed_at": done, "error": error,
        })

    def active_brief(self, agent_id: str) -> Optional[dict]:
        rows = self._get(
            f"briefs?agent_id=eq.{agent_id}&state=eq.active"
            "&select=id,agent_id,version,state,content,compiled_config,"
            "readiness,confirmed_at&limit=1"
        )
        return rows[0] if rows else None

    def write_compile(self, brief_id: str, compiled: dict, readiness: str) -> None:
        if readiness not in ("ready", "not_ready"):
            raise HuntError("compile_failed")
        self._patch(f"briefs?id=eq.{brief_id}", {
            "compiled_config": persistable_compiled(compiled),
            "readiness": readiness,
        })

    def editions_for_day(self, agent_id: str, day: date) -> list[dict]:
        return self._get(
            f"editions?agent_id=eq.{agent_id}&edition_date=eq.{day.isoformat()}"
            "&select=id,agent_id,edition_date,brief_version,payload,html,outcome"
        )

    def prior_edition_payloads(self, agent_id: str) -> list[dict]:
        rows = self._get(
            f"editions?agent_id=eq.{agent_id}"
            "&select=payload,edition_date&order=edition_date.asc"
        )
        return [r.get("payload") or {} for r in rows]

    def insert_edition(self, row: dict) -> None:
        self._post("editions", row)

    def replace_edition(self, edition_id: str, row: dict) -> None:
        body = {k: v for k, v in row.items() if k not in ("agent_id", "edition_date")}
        self._patch(f"editions?id=eq.{edition_id}", body)

    def edition_by_id(self, edition_id: str) -> Optional[dict]:
        cols = "id,agent_id,edition_date,brief_version,html,payload,outcome"
        eid = (edition_id or "").strip()
        if len(eid) >= 36:
            rows = self._get(f"editions?id=eq.{eid}&select={cols}&limit=2")
            return rows[0] if len(rows) == 1 else None
        bounds = _uuid_prefix_bounds(eid)
        if not bounds:
            return None
        lo, hi = bounds
        rows = self._get(
            f"editions?id=gte.{lo}&id=lt.{hi}&select={cols}&limit=2"
        )
        return rows[0] if len(rows) == 1 else None


    def agent_no(self, agent_id: str) -> Optional[int]:
        if not agent_id:
            return None
        rows = self._get(
            f"agents?id=eq.{agent_id}&select=agent_no&limit=1"
        )
        if not rows:
            return None
        try:
            return int(rows[0]["agent_no"])
        except (KeyError, TypeError, ValueError):
            return None

    def agent_id_for_no(self, agent_no: int) -> Optional[str]:
        rows = self._get(f"agents?agent_no=eq.{int(agent_no)}&select=id&limit=1")
        if not rows:
            return None
        return str(rows[0].get("id") or "") or None

    def confirmed_memory(self, agent_id: str) -> list[dict]:
        rows = self._get(
            f"memory?agent_id=eq.{agent_id}&status=eq.active&provenance=eq.confirmed"
            "&select=id,layer,statement,handle,source,provenance,status,created_at"
            "&order=created_at.asc&limit=1000"
        )
        return list(rows or [])

    def at_work_agents(self) -> list[dict]:
        return list(self._get("agents?state=eq.at_work&select=id,agent_no&order=agent_no.asc&limit=500") or [])

    def enqueue_job(self, agent_id: str, job_type: str, payload: dict) -> bool:
        try:
            self._post("jobs", {"agent_id": agent_id, "type": job_type, "payload": payload})
            return True
        except Exception as e:
            # 409 from the partial unique index = already queued; a check
            # violation on jobs.type means the database does not know this
            # job type yet (migration not applied) — named, so the caller can
            # stand down instead of failing every beat; anything else surfaces
            resp = getattr(e, "response", None)
            code = getattr(resp, "status_code", None) if resp is not None else None
            if code == 409:
                return False
            if code == 400 and ("23514" in (getattr(resp, "text", "") or "")
                                or "jobs_type_check" in (getattr(resp, "text", "") or "")):
                raise HuntError("job_type_unknown") from e
            raise

    def next_brief_version(self, agent_id: str) -> int:
        rows = self._get(f"briefs?agent_id=eq.{agent_id}&select=version&order=version.desc&limit=1")
        try:
            return int(rows[0]["version"]) + 1 if rows else 1
        except (KeyError, TypeError, ValueError):
            return 1

    def abandon_proposed_briefs(self, agent_id: str) -> int:
        rows = self._patch(f"briefs?agent_id=eq.{agent_id}&state=eq.proposed", {"state": "abandoned"})
        return len(rows or [])

    def insert_brief(self, row: dict) -> None:
        self._post("briefs", row)

    def live_agents(self) -> list[dict]:
        return list(self._get("agents?state=neq.archived&select=id,agent_no,state"
                              "&order=agent_no.asc&limit=500") or [])

    def briefs_in_force(self, agent_id: str) -> list[dict]:
        return list(self._get(
            f"briefs?agent_id=eq.{agent_id}&state=in.(proposed,active)"
            "&select=id,version,state,content,readiness&order=version.desc&limit=10") or [])

    def open_mirror_count(self, agent_id: str) -> int:
        # "Open" means the person can still see something awaiting a verdict.
        # The Mirror shows only rows that carry a handle, in the three hunt
        # layers; a row without a handle is invisible to them and must not
        # hold the Brief hostage. Same criterion as the app (mirror.ts).
        rows = self._get(
            f"memory?agent_id=eq.{agent_id}&status=eq.active"
            "&provenance=in.(stated,extracted,inferred)"
            "&handle=not.is.null&layer=in.(record,self,model)&select=id&limit=50")
        return len(rows or [])

    def last_job(self, agent_id: str, job_type: str) -> Optional[dict]:
        rows = self._get(
            f"jobs?agent_id=eq.{agent_id}&type=eq.{job_type}"
            "&select=id,status,requested_at,payload&order=requested_at.desc&limit=1")
        return rows[0] if rows else None

    def candidate_count(self, agent_id: str) -> int:
        rows = self._get(f"candidates?agent_id=eq.{agent_id}&select=id&limit=50")
        return len(rows or [])

    def next_candidate_version(self, agent_id: str) -> int:
        rows = self._get(f"candidates?agent_id=eq.{agent_id}&select=version&order=version.desc&limit=1")
        try:
            return int(rows[0]["version"]) + 1 if rows else 1
        except (KeyError, TypeError, ValueError):
            return 1

    def retire_candidate_drafts(self, agent_id: str) -> int:
        rows = self._patch(f"candidates?agent_id=eq.{agent_id}&state=eq.draft", {"state": "unpublished"})
        return len(rows or [])

    def insert_candidate(self, row: dict) -> None:
        self._post("candidates", row)


# ---------------------------------------------------------------------------
# Processor
# ---------------------------------------------------------------------------

Collector = Callable[[dict], list[dict]]


@dataclass
class RunReport:
    action: str = "none"          # none|compiled|refreshed|edition|noop|failed|error
    job_id: Optional[str] = None
    job_type: Optional[str] = None
    readiness: Optional[str] = None
    seats: Optional[int] = None
    detail: dict = field(default_factory=dict)


class Runner:
    def __init__(self, db: HuntDb, collector: Collector | None = None,
                 today: date | None = None, fetch_jd=None, score=None,
                 profile: str | None = None, deep=None, brief_line_fn=None,
                 state_loader=None, read_budget: int = PRIVATE_READ_BUDGET,
                 model_probe=None, drafter=None, probe=None):
        self.db = db
        # Default collector is live adapters. Tests inject a local collector.
        self.collector = collector
        # Named-company board probe (tests inject; live path asks the ATS APIs).
        # Tests with a local collector get a silent probe: no network, ever.
        self.probe = probe if probe is not None else (
            (lambda ja, name: None) if collector is not None else None)
        self.today = today or date.today()
        # Judgment hooks (tests only). Live path uses job_alerts itself.
        self.fetch_jd = fetch_jd
        self.score = score
        self.profile = profile
        self.deep = deep
        self.brief_line_fn = brief_line_fn
        # Verdict state loader (tests inject). Live path: foound_state.
        self.state_loader = state_loader
        self.read_budget = read_budget
        # Model-failure classifier (tests inject). Live path: one probe call.
        self.model_probe = model_probe
        # Brief drafter (tests inject). Live path: draft_with_model.
        self.drafter = drafter

    def run(self, limit: int = MAX_JOBS_PER_RUN) -> list[RunReport]:
        reports = []
        queued = self.db.oldest_queued_hunt_jobs(limit)
        log.info("queued hunt jobs=%d", len(queued))
        for job in queued:
            reports.append(self._run_one(job))
        if not reports:
            log.info("no queued hunt jobs")
        return reports

    def _run_one(self, job: dict) -> RunReport:
        report = RunReport(job_id=job.get("id"), job_type=job.get("type"))
        claimed = self.db.claim(job["id"])
        if claimed is None:
            log.info("lost claim race job=%s", job["id"])
            report.action = "race"
            return report
        job = {**job, **claimed}
        log.info("claimed job=%s type=%s agent=%s",
                 job["id"], job.get("type"), job.get("agent_id"))
        try:
            return self._process(job, report)
        except HuntError as e:
            self.db.fail(job["id"], e.name)
            log.info("failed job=%s error=%s", job["id"], e.name)
            report.action = "failed"
            report.detail["error"] = e.name
            return report
        except Exception as _e:
            log.info("processing error job=%s class=%s", job["id"], type(_e).__name__)
            try:
                self.db.fail(job["id"], "compile_failed")
            except Exception:
                log.error("fail write failed job=%s", job["id"])
            report.action = "error"
            return report

    def _process(self, job: dict, report: RunReport) -> RunReport:
        kind = job.get("type")
        if kind == "compile_brief":
            return self._compile(job, report)
        if kind == "refresh_readiness":
            return self._refresh(job, report)
        if kind == "first_edition":
            return self._first_edition(job, report)
        if kind == "propose_brief":
            return self._propose_brief(job, report)
        if kind == "draft_candidate":
            return self._draft_candidate(job, report)
        raise HuntError("compile_failed")

    def _load_active(self, agent_id: str) -> dict:
        brief = self.db.active_brief(agent_id)
        if not brief:
            raise HuntError("no_active_brief")
        return brief

    def _with_person(self, agent_id: str, brief: dict, compiled: dict) -> tuple[dict, str]:
        """Move 2: the stored readiness the app shows must also say whether
        FOOUND can judge for this person yet. A Brief can be complete while
        nothing is confirmed in Memory — the hunt would refuse with
        no_candidate_context, so readiness says so first, as a named reason
        (`no_candidate_context`), instead of letting a commission fail later.
        №001's interim profile.md counts as a context until he confirms."""
        readiness = readiness_of(compiled)
        try:
            ja = _import_job_alerts_adapters()
            agent_no = _lookup_agent_no(self.db, agent_id)
            ctx = candidate_context(ja, agent_id, agent_no,
                                    memory_rows=self.db.confirmed_memory(agent_id),
                                    brief_content=brief.get("content"))
            has_context = bool(ctx.text)
        except Exception as e:
            log.info("candidate context check failed agent=%s class=%s", agent_id, type(e).__name__)
            has_context = False
        reasons = list(compiled.get("readiness_reasons") or [])
        if not has_context and "no_candidate_context" not in reasons:
            reasons.append("no_candidate_context")
        compiled = dict(compiled, readiness_reasons=reasons)
        if not has_context:
            readiness = "not_ready"
        return compiled, readiness

    def _compile(self, job: dict, report: RunReport) -> RunReport:
        brief = self._load_active(job["agent_id"])
        compiled = compile_from_content(brief.get("content") or {})
        compiled, readiness = self._with_person(job["agent_id"], brief, compiled)
        if readiness not in ("ready", "not_ready"):
            raise HuntError("compile_failed")
        self.db.write_compile(brief["id"], compiled, readiness)
        self.db.complete(job["id"])
        # A Brief that comes into force while FOOUND is already at work earns
        # its own edition: "FOOUND is at work from it" must be true today, not
        # tomorrow. One queued job per (agent, type); the daily beat would
        # otherwise skip a day that already has an edition from the old Brief.
        if readiness == "ready" and self._is_at_work(job["agent_id"]):
            if self.db.enqueue_job(job["agent_id"], "first_edition",
                                   {"brief_version": brief.get("version"), "reason": "brief_in_force"}):
                report.detail["edition_queued"] = True
        log.info(
            "compiled job=%s readiness=%s subjects=%d include=%d locations=%d reasons=%d",
            job["id"], readiness, len(compiled.get("subjects_used") or []),
            len(compiled.get("include") or []),
            len(compiled.get("accepted_locations") or []),
            len(compiled.get("readiness_reasons") or []),
        )
        report.action = "compiled"
        report.readiness = readiness
        report.detail["reasons"] = len(compiled.get("readiness_reasons") or [])
        return report

    def _is_at_work(self, agent_id: str) -> bool:
        try:
            return any(str(a.get("id")) == str(agent_id) for a in self.db.at_work_agents())
        except Exception as e:
            log.info("at_work read failed agent=%s class=%s", agent_id, type(e).__name__)
            return False

    def _refresh(self, job: dict, report: RunReport) -> RunReport:
        brief = self._load_active(job["agent_id"])
        # Recompute from current Brief.content (authority), never stale config.
        compiled = compile_from_content(brief.get("content") or {})
        compiled, readiness = self._with_person(job["agent_id"], brief, compiled)
        self.db.write_compile(brief["id"], compiled, readiness)
        self.db.complete(job["id"])
        log.info("refreshed job=%s readiness=%s", job["id"], readiness)
        report.action = "refreshed"
        report.readiness = readiness
        return report


    def enqueue_daily(self) -> dict:
        """The heartbeat's other half (Move 2): every at_work agent with an
        active, ready Brief and no edition today gets one queued edition job
        (type first_edition — the daily edition; the handler is a no-op
        when today's edition already exists). Idempotent: the jobs table
        allows one queued job per (agent, type). Counts only."""
        out = {"at_work": 0, "queued": 0, "already_queued": 0, "has_edition": 0,
               "no_brief": 0, "not_ready": 0}
        for a in self.db.at_work_agents():
            out["at_work"] += 1
            aid = a.get("id")
            brief = self.db.active_brief(aid)
            if not brief:
                out["no_brief"] += 1
                continue
            if brief.get("readiness") != "ready":
                out["not_ready"] += 1
                continue
            if self.db.editions_for_day(aid, self.today):
                out["has_edition"] += 1
                continue
            if self.db.enqueue_job(aid, "first_edition", {"brief_version": brief.get("version"), "daily": True}):
                out["queued"] += 1
            else:
                out["already_queued"] += 1
        log.info("enqueue_daily " + " ".join(f"{k}={v}" for k, v in out.items()))
        return out

    def sweep_proposals(self, now: datetime | None = None) -> dict:
        """Move 2: FOOUND proposes on its own. Nobody should have to ask
        FOOUND to write their Brief. Once something is confirmed and no
        Brief is in force, one propose_brief job is queued as soon as the
        Mirror is settled (nothing left awaiting a verdict) — or, when the
        client left statements unanswered, once they have been quiet for
        PROPOSAL_QUIET_MINUTES (people confirm what matters and walk away;
        that must be enough). A pending proposal drafted from an older
        understanding (its Candidate Context hash no longer matches) is
        redrafted the same way. An ACTIVE Brief is never touched: learning
        never rewrites authority. A draft that already failed on this exact
        understanding is not retried until the client confirms something
        new. Counts only."""
        import candidate_context as cc
        now = now or datetime.now(timezone.utc)
        out = {"agents": 0, "queued": 0, "has_active": 0, "no_confirmed": 0, "mirror_open": 0,
               "current": 0, "in_flight": 0, "failed_on_this": 0, "already_queued": 0}
        for a in self.db.live_agents():
            out["agents"] += 1
            aid = a.get("id")
            force = self.db.briefs_in_force(aid)
            if any(b.get("state") == "active" for b in force):
                out["has_active"] += 1
                continue
            rows = cc.confirmed_rows(self.db.confirmed_memory(aid))
            if not rows:
                out["no_confirmed"] += 1
                continue
            newest = max((str(r.get("created_at") or "") for r in rows), default="")
            if self.db.open_mirror_count(aid) > 0 and not _quiet_since(newest, now):
                out["mirror_open"] += 1
                continue
            h = cc.context_hash(rows, None)
            proposed = [b for b in force if b.get("state") == "proposed"]
            if proposed and _proposal_context_hash(proposed[0]) == h:
                out["current"] += 1
                continue
            last = self.db.last_job(aid, "propose_brief")
            if last and last.get("status") in ("queued", "running"):
                out["in_flight"] += 1
                continue
            if last and last.get("status") == "failed" and str(last.get("requested_at") or "") >= newest:
                out["failed_on_this"] += 1
                continue
            try:
                queued = self.db.enqueue_job(aid, "propose_brief", {"auto": True, "context_hash": h})
            except HuntError as e:
                if e.name != "job_type_unknown":
                    raise
                # The door (migration 013) is not in this database yet: say so
                # once and stand down; the beat is not broken, the door is shut.
                out["door_closed"] = 1
                break
            if queued:
                out["queued"] += 1
            else:
                out["already_queued"] += 1
        log.info("sweep_proposals " + " ".join(f"{k}={v}" for k, v in out.items()))
        return out

    def _propose_brief(self, job: dict, report: RunReport) -> RunReport:
        """Move 2: FOOUND drafts a PROPOSED Working Brief from confirmed
        Memory for the client to confirm (activate_brief). Inert until then.
        Console and logs carry counts and enums only."""
        import brief_proposal as bp
        agent_id = job["agent_id"]
        ja = _import_job_alerts_adapters()
        agent_no = _lookup_agent_no(self.db, agent_id)
        rows = self.db.confirmed_memory(agent_id)
        ctx = candidate_context(ja, agent_id, agent_no, memory_rows=rows, brief_content=None)
        if ctx.kind != "memory" or not rows:
            raise HuntError("no_candidate_context")   # profile.md is not a source of intent
        drafter = self.drafter if self.drafter is not None else (lambda prompt: draft_with_model(ja, prompt))
        valid_ids = [str(r.get("id")) for r in rows]
        content, feedback, reason, attempts = None, "", "", 0
        # The client marked subjects of the previous proposal wrong (app payload
        # {"wrong": [{"chapter": ..., "handle": ...}, ...]}): the redraft is told.
        payload = job.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        wrong = payload.get("wrong") if isinstance(payload, dict) else None
        if isinstance(wrong, list) and wrong:
            named = "; ".join(f"{str(w.get('chapter') or '').strip()} / {str(w.get('handle') or '').strip()}"
                              for w in wrong if isinstance(w, dict))[:400]
            feedback = f"The client marked these subjects of the previous draft as wrong: {named}. Do not repeat them; write from the confirmed statements only."
        for attempt in (1, 2):
            attempts = attempt
            with _silent_stdio():
                text = drafter(bp.draft_brief_prompt(ctx.text, rows, feedback=feedback))
            content, reason = bp.parse_brief_draft(text or "", valid_ids)
            if content is None:
                continue
            ok, feedback = bp.check_proposal(content, compile_from_content, readiness_of)
            if ok:
                break
        if content is None:
            log.info("propose_brief job=%s outcome=failed reason=%s attempts=%d", job["id"], reason or "no_json", attempts)
            raise HuntError("proposal_failed")
        executable, reasons = bp.check_proposal(content, compile_from_content, readiness_of)
        content = dict(content, provenance=dict(
            bp.provenance(current_engine_sha(), ctx.hash, getattr(ja, "CLAUDE_MODEL", ""), attempts),
            executable=executable, compiler_reasons=[] if executable else [reasons]))
        # The proposal carries its own readiness receipt, so the room can say
        # "FOOUND cannot hunt from this yet" instead of offering to confirm it.
        compiled = compile_from_content(content)
        readiness = readiness_of(compiled)
        abandoned = self.db.abandon_proposed_briefs(agent_id)
        version = self.db.next_brief_version(agent_id)
        self.db.insert_brief({"agent_id": agent_id, "version": version, "state": "proposed", "content": content,
                              "compiled_config": persistable_compiled(compiled), "readiness": readiness})
        self.db.complete(job["id"])
        n_subjects = sum(len(c["subjects"]) for c in content["chapters"])
        log.info("propose_brief job=%s version=%d chapters=%d subjects=%d executable=%s attempts=%d abandoned=%d",
                 job["id"], version, len(content["chapters"]), n_subjects, executable, attempts, abandoned)
        report.action = "proposed"
        report.detail.update({"version": version, "chapters": len(content["chapters"]),
                              "subjects": n_subjects, "executable": executable, "attempts": attempts})
        return report

    def _draft_candidate(self, job: dict, report: RunReport) -> RunReport:
        """Move 4: FOOUND drafts the person's Candidate page from confirmed
        Memory, once. A draft is inert: the person edits it in their room and
        publishes it through publish_candidate (migration 015). Counts only."""
        import candidate_draft as cd
        agent_id = job["agent_id"]
        ja = _import_job_alerts_adapters()
        agent_no = _lookup_agent_no(self.db, agent_id)
        rows = self.db.confirmed_memory(agent_id)
        ctx = candidate_context(ja, agent_id, agent_no, memory_rows=rows, brief_content=None)
        if ctx.kind != "memory" or not rows:
            raise HuntError("no_candidate_context")
        drafter = self.drafter if self.drafter is not None else (lambda prompt: draft_with_model(ja, prompt))
        valid_ids = [str(r.get("id")) for r in rows]
        page, reason, attempts = None, "", 0
        for attempt in (1, 2):
            attempts = attempt
            with _silent_stdio():
                text = drafter(cd.draft_candidate_prompt(ctx.text, rows))
            page, reason = cd.parse_candidate_draft(text or "", valid_ids)
            if page is not None:
                break
        if page is None:
            log.info("draft_candidate job=%s outcome=failed reason=%s attempts=%d", job["id"], reason or "no_json", attempts)
            raise HuntError("candidate_draft_failed")
        page = dict(page, provenance=cd.provenance(current_engine_sha(), ctx.hash, getattr(ja, "CLAUDE_MODEL", ""), attempts))
        retired = self.db.retire_candidate_drafts(agent_id)
        version = self.db.next_candidate_version(agent_id)
        self.db.insert_candidate({"agent_id": agent_id, "version": version, "state": "draft",
                                  "content": "", "page": page})
        self.db.complete(job["id"])
        log.info("draft_candidate job=%s version=%d chapters=%d trusted=%d attempts=%d retired=%d",
                 job["id"], version, len(page["chapters"]), len(page["trusted_with"]), attempts, retired)
        report.action = "drafted"
        report.detail.update({"version": version, "chapters": len(page["chapters"]),
                              "trusted": len(page["trusted_with"]), "attempts": attempts})
        return report

    def sweep_candidates(self, now: datetime | None = None) -> dict:
        """Move 4: once a person has confirmed their record and the Mirror is
        settled (or quiet), FOOUND drafts their Candidate page, once. Never
        redrafted on its own: a public page must not change under them, and a
        draft they are editing must not be overwritten. Counts only."""
        import candidate_context as cc
        now = now or datetime.now(timezone.utc)
        out = {"agents": 0, "queued": 0, "has_page": 0, "no_confirmed": 0, "mirror_open": 0,
               "in_flight": 0, "failed_on_this": 0, "already_queued": 0}
        for a in self.db.live_agents():
            out["agents"] += 1
            aid = a.get("id")
            if self.db.candidate_count(aid) > 0:
                out["has_page"] += 1
                continue
            rows = cc.confirmed_rows(self.db.confirmed_memory(aid))
            if not rows:
                out["no_confirmed"] += 1
                continue
            newest = max((str(r.get("created_at") or "") for r in rows), default="")
            if self.db.open_mirror_count(aid) > 0 and not _quiet_since(newest, now):
                out["mirror_open"] += 1
                continue
            last = self.db.last_job(aid, "draft_candidate")
            if last and last.get("status") in ("queued", "running"):
                out["in_flight"] += 1
                continue
            if last and last.get("status") == "failed" and str(last.get("requested_at") or "") >= newest:
                out["failed_on_this"] += 1
                continue
            try:
                queued = self.db.enqueue_job(aid, "draft_candidate", {"auto": True})
            except HuntError as e:
                if e.name != "job_type_unknown":
                    raise
                out["door_closed"] = 1
                break
            if queued:
                out["queued"] += 1
            else:
                out["already_queued"] += 1
        log.info("sweep_candidates " + " ".join(f"{k}={v}" for k, v in out.items()))
        return out

    @staticmethod
    def _compile_for_hunt(brief: dict) -> dict:
        """v1.2: the hunt's authority is the active Brief.content, compiled
        now, every run. The stored `compiled_config` is a receipt written by
        the compile/readiness jobs; it is never an input to eligibility.
        Readiness is judged on this fresh compilation, not the stored column.
        """
        compiled = compile_from_content(brief.get("content") or {})
        if readiness_of(compiled) != "ready":
            raise HuntError("readiness_blocked")
        return compiled

    def _first_edition(self, job: dict, report: RunReport) -> RunReport:
        existing = self.db.editions_for_day(job["agent_id"], self.today)
        brief = self._load_active(job["agent_id"])
        # One edition per day. A day that already has an edition from this
        # Brief is done; an edition from an older Brief is rewritten, because
        # the Brief now in force is the authority and the day's record must
        # come from it.
        stale = [e for e in existing
                 if brief.get("version") is not None and e.get("brief_version") != brief.get("version")]
        if existing and not stale:
            self.db.complete(job["id"])
            log.info("first_edition noop job=%s existing=%d", job["id"], len(existing))
            report.action = "noop"
            report.seats = None
            report.detail["reason"] = "same_day_edition_exists"
            return report

        compiled = self._compile_for_hunt(brief)
        result = self._hunt(job["agent_id"], brief, compiled, job_id=job["id"])
        version = brief.get("version")
        job_payload = job.get("payload") or {}
        if isinstance(job_payload, str):
            try:
                job_payload = json.loads(job_payload)
            except json.JSONDecodeError:
                job_payload = {}
        if version is None and isinstance(job_payload, dict):
            version = job_payload.get("brief_version")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = {
            "agent_id": job["agent_id"],
            "edition_date": self.today.isoformat(),
            "brief_version": version,
            "html": result["html"],
            "payload": result["payload"],
            "outcome": result["outcome"],
            "delivered_at": now,
        }
        replaced_version = stale[0].get("brief_version") if stale else None
        try:
            if stale:
                self.db.replace_edition(stale[0]["id"], row)
            else:
                self.db.insert_edition(row)
        except Exception as e:
            log.info("edition persist error job=%s class=%s", job["id"], type(e).__name__)
            raise HuntError("edition_persist_failed")
        self.db.complete(job["id"])
        log.info("first_edition job=%s outcome=%s seats=%d replaced=%s", job["id"],
                 result["outcome"], len(result["seats"]), bool(stale))
        print(operator_line(result.get("agent_no"), result["counts"],
                            result["engine"], result["outcome"],
                            engine_reason=result.get("engine_reason", ""),
                            authority=result.get("authority"),
                            intelligence=result.get("intelligence")))
        report.action = "edition"
        report.seats = len(result["seats"])
        report.detail["outcome"] = result["outcome"]
        if stale:
            report.detail["replaced_brief_version"] = replaced_version
        report.detail["counts"] = dict(result["counts"])
        return report

    def _hunt(self, agent_id: str, brief: dict, compiled: dict,
              job_id: str = "", dry_run: bool = False) -> dict:
        """Collect, then run the one-judge loop. No writes here."""
        ja = _import_job_alerts_adapters()
        agent_no = _lookup_agent_no(self.db, agent_id)
        # Candidate Context is checked before collection so a client without
        # one costs nothing and reaches no adapter and no model.
        memory_rows = self.db.confirmed_memory(agent_id)
        ctx = candidate_context(ja, agent_id, agent_no, memory_rows=memory_rows,
                                brief_content=brief.get("content"))
        if self.profile is None and not ctx.text:
            raise HuntError("no_candidate_context")
        # Move 3: the market universe follows the Brief (regions, named houses).
        entries, source_summary = market_sources.select_sources(compiled, ja, probe=self.probe)
        log.info("sources selected=%d founding=%d/%d added=%d named=%d regions=%s",
                 source_summary["selected"], source_summary["founding"],
                 source_summary["founding_total"], len(source_summary["added"]),
                 len(source_summary["named"]), ",".join(source_summary["regions"]))
        try:
            if self.collector is not None:
                raw = self.collector(compiled)
            else:
                raw = live_collect(compiled, scraper_entries=entries)
        except HuntError:
            raise
        except Exception as e:
            log.info("collector error job=%s class=%s", job_id, type(e).__name__)
            raise HuntError("hunt_adapter_failed")
        state = None
        if self.state_loader is not None:
            state = self.state_loader(agent_id, agent_no)
        elif not dry_run or os.environ.get("SUPABASE_URL"):
            state = load_verdict_state(agent_id, agent_no)
        prior = self.db.prior_edition_payloads(agent_id)
        edition_no = len(prior) + 1
        sources = source_summary["selected"]
        now = datetime.now(timezone.utc)
        result = run_hunt(
            ja, agent_id=agent_id, agent_no=agent_no, brief=brief,
            compiled=compiled, raw=raw or [], prior_payloads=prior,
            state=state, today=self.today, now=now,
            profile=self.profile, fetch_jd=self.fetch_jd, score=self.score,
            deep=self.deep, brief_line_fn=self.brief_line_fn,
            memory_rows=memory_rows,
            edition_no=edition_no, sources=sources,
            read_budget=self.read_budget, model_probe=self.model_probe,
        )
        if "DUMMY ROLE" in result["html"]:
            raise HuntError("edition_persist_failed")
        result["agent_no"] = agent_no
        # The universe this edition was hunted in: counts and company names
        # only, kept with the edition so the person can be told the truth
        # about where FOOUND looked. Never logged beyond the counts above.
        result["payload"]["sources"] = dict(source_summary)
        result["payload"].setdefault("counts", {})["sources"] = source_summary["selected"]
        return result

    def dry_run(self, agent_id: str, fixture_path: str | None = None) -> dict:
        """Stages 1–11 for one agent. Writes nothing to the database.

        Console: counts and enums only. The private fixture table (role_key ·
        fit · seated / refused / unread, plus the compiled authority) goes to
        `fixture_path` — a local file outside the repo — for the operator only.
        """
        brief = self._load_active(agent_id)
        compiled = self._compile_for_hunt(brief)
        result = self._hunt(agent_id, brief, compiled, job_id="dry-run", dry_run=True)
        line = operator_line(result.get("agent_no"), result["counts"],
                             result["engine"], result["outcome"],
                             engine_reason=result.get("engine_reason", ""),
                             authority=result.get("authority"),
                             intelligence=result.get("intelligence"))
        print(line)
        if fixture_path:
            rows = []
            for s in result["payload"]["seats"]:
                rows.append({"role_key": s["role_key"], "company": s["company"],
                             "title": s["title"], "location": s["location"],
                             "fit": s["fit"], "status": "seated"})
            for r in result["payload"].get("refused", []):
                rows.append({"role_key": r["role_key"], "company": r["company"],
                             "title": r["title"], "location": r["location"],
                             "fit": r["fit"], "status": "refused",
                             "shown": r["role_key"] in result["payload"].get("refused_shown", [])})
            for k in result["payload"].get("unread", []):
                rows.append({"role_key": k, "status": "unread"})
            with open(fixture_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "agent_no": result.get("agent_no"),
                    "date": self.today.isoformat(),
                    "counts": result["counts"],
                    "engine": result["engine"],
                    "engine_reason": result.get("engine_reason"),
                    "intelligence": result.get("intelligence"),
                    "authority": result.get("authority"),
                    "compiled": {k: compiled.get(k) for k in (
                        "families", "include", "location_phrases",
                        "accepted_locations", "exclude_type",
                        "priority_companies", "seat_cap", "readiness_reasons")},
                    "rows": rows,
                    "html": result["html"],
                }, fh, ensure_ascii=False, indent=1)
            print(f"[dry-run] fixture table written: {len(rows)} rows (local file, not logged)")
        return result


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    for noisy in ("httpx", "httpcore", "anthropic", "urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _configure_logging()
    if argv and argv[0] == "--dry-run":
        if len(argv) < 2 or not argv[1].strip():
            log.info("dry-run missing agent id")
            return 2
        agent_id = argv[1].strip()
        fixture = argv[2].strip() if len(argv) > 2 and argv[2].strip() else None
        base = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        db = RestDb(base, key)
        if re.fullmatch(r"\d{1,4}", agent_id):
            # A public agent number (001) instead of a UUID: resolve it, read-only.
            resolved = db.agent_id_for_no(int(agent_id))
            if not resolved:
                log.info("dry-run unknown agent number")
                return 1
            agent_id = resolved
        runner = Runner(db=db)
        try:
            runner.dry_run(agent_id, fixture)
        except HuntError as e:
            log.info("dry-run failed error=%s", e.name)
            return 1
        return 0
    if argv and argv[0] == "--enqueue-daily":
        base = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        Runner(db=RestDb(base, key)).enqueue_daily()
        return 0
    if argv and argv[0] == "--sweep-proposals":
        base = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        runner = Runner(db=RestDb(base, key))
        runner.sweep_proposals()
        runner.sweep_candidates()
        return 0
    if argv:
        log.info("unknown args")
        return 2
    base = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    runner = Runner(db=RestDb(base, key))
    reports = runner.run()
    actions = ",".join(r.action for r in reports) or "none"
    log.info("done jobs=%d actions=%s", len(reports), actions)
    return 0 if all(r.action != "error" for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
