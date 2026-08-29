"""FOOUND Hunt Runner v1 — first-real-hunt vertical slice.

Path: active Brief → compile → readiness → commission recovery →
one manual first_edition → one private editions row.

This module claims compile_brief, refresh_readiness, and first_edition.
It does not claim synthesize jobs (see synthesis_runner.py).

Contract:
  · Working Brief is the only hunt authority. Compile only Brief.content.
  · Confirmed Memory is never search authority. Do not read memory.
  · Never write agent_config. Never write Candidate.
  · Never call the public Shortlist publisher (publish_shortlist / docs /
    GitHub Pages). publish_public is always false here.
  · Readiness is temporary architecture: briefs.readiness = ready|not_ready;
    BLOCKED reasons live in compiled_config.readiness_reasons. Never write
    'limited'. Never infer READY from agents.state = at_work.
  · v1 may see subjects titled THE MOVE / ROLE SPACE / WHERE on №001's
    Brief. Those labels are not permanent engine architecture.
  · Zero seats is SUCCESS: an honest empty edition. jobs.error is technical
    failure only.
  · v1 market memory is personal: this agent's prior private editions.payload
    only. Do not use public.market_seen.

Privacy: the engine repo is PUBLIC and GitHub Actions logs are public.
Logging is ids / counts / enums / timings ONLY. Never Brief copy, seat
titles, URLs, prompts, or model output.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

log = logging.getLogger("hunt_runner")

DEFAULT_SEAT_CAP = 5
MAX_SEAT_CAP = 20
HUNT_JOB_TYPES = ("compile_brief", "refresh_readiness", "first_edition")
MAX_JOBS_PER_RUN = 10

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
    "no_compiled_config",
    "readiness_blocked",
    "compile_failed",
    "hunt_adapter_failed",
    "edition_persist_failed",
}


class HuntError(Exception):
    """Named technical failure. Safe to persist on jobs.error."""

    def __init__(self, name: str):
        if name not in NAMED_ERRORS:
            name = "compile_failed"
        super().__init__(name)
        self.name = name


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
    """Bucket from chapter title first, then unit title. Handles are not keys."""
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


def _normalize_concept(raw: str) -> str | None:
    """Keep a coherent hunt concept; drop scraps. Do not invent terms."""
    p = _norm_phrase(raw)
    if not p:
        return None
    if _CONJ_LEAD.match(p):
        p = _CONJ_LEAD.sub("", p, count=1).strip()
    if not p or not _usable_term(p):
        return None
    if _looks_like_role_title(p) or _looks_like_role_title(_as_family(p)):
        return None
    if _is_prose_scrap(p):
        return None
    if not re.search(r"[a-z]", p):
        return None
    return p


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
        if sum(1 for w in words if w in _ROLE_NUCLEI) > 1:
            return False
        return all(
            w in _ROLE_RANKS or w in _CRAFT_ADJECTIVES or w in _ROLE_NUCLEI
            or w in _ROLE_ABBREV
            for w in words
        )
    return False


def _as_family(phrase: str) -> str:
    """Bare CD is Creative Director. Group/Executive CD and ECD stay as written."""
    p = _norm_phrase(phrase)
    if p == "cd":
        return "creative director"
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
    return found


def _extract_concepts(text: str) -> list[str]:
    found: list[str] = []
    candidates: list[str] = []
    for item in _list_items(text):
        candidates.append(item)
        if "(" in item:
            pre = item.split("(", 1)[0].strip()
            if pre:
                candidates.append(pre)
    for raw in candidates:
        v = _normalize_concept(raw)
        if v:
            _add_unique(found, v)
    return found


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
    """Derive compiled_config from Brief.content only.

    Role-family titles are extracted as whole seat names. search_queries is
    that family set (usable market queries), never ambition prose.
    include holds role families plus coherent hunt concepts only —
    no conjunction-led scraps or residual sentence tails.
    accepted_locations stay geography.
    Move-kind text is intent: only exact seat titles found there authorize.

    READY iff the Brief authorized executable hunt input (role families and
    accepted locations). Otherwise not_ready with reasons. Never 'limited'.
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
    bags["include"] = [
        x for x in bags["include"]
        if _norm_phrase(x) != "cd" and not _is_prose_scrap(x)
    ]

    concepts: list[str] = []
    subjects_used: list[str] = []
    for sub in extract_subjects(content):
        kind = _kind_of_subject(sub)
        if kind == "skip":
            continue
        _collect_structured(sub["fields"], bags)
        contributed = False
        if kind == "place":
            for loc in _extract_locations(sub["text"]):
                if _add_unique(bags["accepted_locations"], loc):
                    contributed = True
        else:
            for fam in _extract_role_families(sub["text"]):
                if _add_unique(families, fam):
                    contributed = True
            if kind != "move":
                for concept in _extract_concepts(sub["text"]):
                    if concept in families:
                        continue
                    if _add_unique(concepts, concept):
                        contributed = True
        if sub["title"] and (contributed or sub["text"] or kind in ("role", "place", "move")):
            for label in (sub.get("context_title"), sub["title"]):
                if label and label not in subjects_used:
                    subjects_used.append(label)

    for fam in families:
        _add_unique(bags["include"], fam)
    for concept in concepts:
        _add_unique(bags["include"], concept)

    bags["search_queries"] = list(families)

    reasons: list[str] = []
    has_families = bool(bags["search_queries"])
    has_locs = bool(bags["accepted_locations"])
    if not has_families and not has_locs and not bags["include"]:
        reasons.append("no_usable_hunt_authority")
    if not has_families:
        reasons.append("no_include_terms")
    if not has_locs:
        reasons.append("no_accepted_locations")

    executable = has_families and has_locs
    readiness = "ready" if executable else "not_ready"
    if executable:
        reasons = []

    compiled_at = compiled_at or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    cfg = {
        "subjects_used": subjects_used,
        "include": bags["include"],
        "exclude_type": bags["exclude_type"],
        "accepted_locations": bags["accepted_locations"],
        "search_queries": bags["search_queries"],
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
    if compiled.get("search_queries") and compiled.get("accepted_locations"):
        return "ready"
    return "not_ready"


def persistable_compiled(compiled: dict) -> dict:
    return {k: v for k, v in compiled.items() if k != "_readiness"}


def compiled_config_hash(compiled: dict) -> str:
    body = {
        k: compiled.get(k)
        for k in (
            "subjects_used", "include", "exclude_type",
            "accepted_locations", "search_queries", "seat_cap",
        )
    }
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


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
# Eligibility gates (not judgment). Local copies of job_alerts.passes_*
# so tests never import job_alerts.py (that module requires publisher secrets).
# ---------------------------------------------------------------------------


def passes_title(compiled: dict, title: str) -> bool:
    t = (title or "").lower()
    include = compiled.get("include") or []
    exclude = compiled.get("exclude_type") or []
    if not include or not any(k in t for k in include):
        return False
    if any(k in t for k in exclude):
        return False
    return True


def passes_location(compiled: dict, location: str) -> bool:
    if not location:
        return True
    loc = location.lower()
    if re.search(r"\d+\s+locations", loc):
        return True
    accepted = compiled.get("accepted_locations") or []
    return any(re.search(rf"\b{re.escape(a)}\b", loc) for a in accepted)


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
# ---------------------------------------------------------------------------

def render_edition_html(seats: list[dict]) -> str:
    """Machine artifact. No dummy seats. No 'DUMMY ROLE' string."""
    picture = [
        {
            "id": s["role_key"],
            "handle": s.get("handle") or s.get("company") or s.get("title") or "",
            "line": s.get("line") or _seat_line(s),
        }
        for s in seats
    ]
    payload = json.dumps(picture, ensure_ascii=False, separators=(",", ":"))
    items = []
    for s in picture:
        items.append(
            "<li data-id=\"{id}\" data-handle=\"{handle}\" data-line=\"{line}\"></li>".format(
                id=html.escape(s["id"], quote=True),
                handle=html.escape(s["handle"], quote=True),
                line=html.escape(s["line"], quote=True),
            )
        )
    outcome = "empty" if not seats else "seats"
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<title>FOOUND edition</title></head><body>"
        f"<script type=\"application/json\" id=\"foound-seats\">{payload}</script>"
        f"<article data-edition=\"{outcome}\" data-seat-count=\"{len(seats)}\">"
        f"<ol>{''.join(items)}</ol>"
        "</article></body></html>"
    )


def _seat_line(seat: dict) -> str:
    title = (seat.get("title") or "").strip()
    loc = (seat.get("location") or "").strip()
    if title and loc:
        return f"{title} — {loc}"
    return title or loc


def build_payload(seats: list[dict], compiled: dict,
                  engine_sha: str) -> dict:
    return {
        "engine_sha": engine_sha,
        "compiled_config_hash": compiled_config_hash(compiled),
        "seats": [
            {
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
            }
            for s in seats
        ],
    }


# ---------------------------------------------------------------------------
# Judgment — compiled Brief authority, not Shortlist rank_with_fit.
# Eligible candidates are scored (title fit, location fit, exclude-cleared)
# then ranked. The seat set is the top of that ranking, not first-N after
# a filter. rank_with_fit is intentionally not reused: it needs Candidate
# profile.md, AgentConfig, JD fetches, and Anthropic, and it logs titles.
# ---------------------------------------------------------------------------

Collector = Callable[[dict], list[dict]]

HUNT_PUBLISH_PUBLIC = False  # this slice never publishes

_JUNIOR_TOKENS = ("junior", "associate", "assistant", "coordinator")
_SENIOR_TOKENS = ("head of", "vp ", "vp,", "vice president", "executive", "group ")


def title_fit(title: str, include: list[str]) -> tuple[int, list[str]]:
    t = (title or "").lower()
    matched = [k for k in include if k and k in t]
    if not matched:
        return 0, []
    longest = max(len(k) for k in matched)
    reasons = ["title_fit"]
    score = longest + 3 * len(matched)
    compact = _norm_phrase(t)
    if compact in include or any(compact == k for k in include):
        score += 12
        reasons.append("title_exact_authority")
    elif any(compact.startswith(k) for k in matched):
        score += 6
        reasons.append("title_leads_with_authority")
    if any(tok in t for tok in _SENIOR_TOKENS):
        score += 5
        reasons.append("title_seniority")
    if any(tok in t for tok in _JUNIOR_TOKENS):
        score -= 8
        reasons.append("title_juniority")
    return score, reasons


def location_fit(location: str, accepted: list[str]) -> tuple[int, list[str]]:
    if not location:
        return 1, ["location_unspecified"]
    loc = location.lower()
    if re.search(r"\d+\s+locations", loc):
        return 2, ["location_multi"]
    matched = [a for a in accepted if a and re.search(rf"\b{re.escape(a)}\b", loc)]
    if not matched:
        return 0, []
    longest = max(len(a) for a in matched)
    reasons = ["location_fit"]
    if any(a != "remote" for a in matched):
        score = 4 + longest
        reasons.append("location_specific")
    else:
        score = 3
        reasons.append("location_remote")
    return score, reasons


def judge_seats(raw: list[dict], compiled: dict) -> list[dict]:
    """Eligibility gates, then rank by Brief-authority scores, then cap.

    survived_because names judgment reasons (title_fit / location_fit /
    exclude_cleared / ranked_above_peers), not filter-only labels.
    """
    cap = int(compiled.get("seat_cap") or DEFAULT_SEAT_CAP)
    cap = max(1, min(cap, MAX_SEAT_CAP))
    include = list(compiled.get("include") or [])
    accepted = list(compiled.get("accepted_locations") or [])
    eligible, seen = [], set()
    for job in raw:
        title = job.get("title") or ""
        loc = job.get("location") or ""
        if not passes_title(compiled, title):
            continue
        if not passes_location(compiled, loc):
            continue
        key = role_key(job)
        if not key or key in seen:
            continue
        seen.add(key)
        t_score, t_reasons = title_fit(title, include)
        l_score, l_reasons = location_fit(loc, accepted)
        if t_score <= 0:
            continue
        reasons = t_reasons + l_reasons + ["exclude_cleared"]
        eligible.append({
            "role_key": key,
            "title": title,
            "company": job.get("company") or "",
            "location": loc,
            "url": job.get("url") or "",
            "handle": (job.get("company") or title),
            "line": _seat_line({"title": title, "location": loc}),
            "source": job.get("source") or "hunt",
            "survived_because": reasons,
            "_title_score": t_score,
            "_location_score": l_score,
        })
    eligible.sort(
        key=lambda s: (-s["_title_score"], -s["_location_score"], s["role_key"])
    )
    ranked_out = len(eligible) > cap
    seats = []
    for s in eligible[:cap]:
        reasons = list(s["survived_because"])
        if ranked_out:
            reasons.append("ranked_above_peers")
        seats.append({k: v for k, v in s.items() if not k.startswith("_")} | {
            "survived_because": reasons,
        })
    return seats


def filter_and_cap(raw: list[dict], compiled: dict) -> list[dict]:
    """Deprecated name: judgment, not first-N after a filter."""
    return judge_seats(raw, compiled)


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
            "&select=id,agent_id,edition_date,payload,html,outcome"
        )

    def prior_edition_payloads(self, agent_id: str) -> list[dict]:
        rows = self._get(
            f"editions?agent_id=eq.{agent_id}"
            "&select=payload,edition_date&order=edition_date.asc"
        )
        return [r.get("payload") or {} for r in rows]

    def insert_edition(self, row: dict) -> None:
        self._post("editions", row)


# ---------------------------------------------------------------------------
# Processor
# ---------------------------------------------------------------------------

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
                 today: date | None = None):
        self.db = db
        # Default collector is live adapters. Tests inject a local collector.
        self.collector = collector
        self.today = today or date.today()

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
        except Exception:
            log.exception("processing error job=%s (exception class only above)",
                          job["id"])
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
        raise HuntError("compile_failed")

    def _load_active(self, agent_id: str) -> dict:
        brief = self.db.active_brief(agent_id)
        if not brief:
            raise HuntError("no_active_brief")
        return brief

    def _compile(self, job: dict, report: RunReport) -> RunReport:
        brief = self._load_active(job["agent_id"])
        compiled = compile_from_content(brief.get("content") or {})
        readiness = readiness_of(compiled)
        if readiness not in ("ready", "not_ready"):
            raise HuntError("compile_failed")
        self.db.write_compile(brief["id"], compiled, readiness)
        self.db.complete(job["id"])
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

    def _refresh(self, job: dict, report: RunReport) -> RunReport:
        brief = self._load_active(job["agent_id"])
        existing = brief.get("compiled_config")
        if not existing:
            compiled = compile_from_content(brief.get("content") or {})
        else:
            # Recompute from current Brief.content (authority), not stale config.
            compiled = compile_from_content(brief.get("content") or {})
        readiness = readiness_of(compiled)
        self.db.write_compile(brief["id"], compiled, readiness)
        self.db.complete(job["id"])
        log.info("refreshed job=%s readiness=%s", job["id"], readiness)
        report.action = "refreshed"
        report.readiness = readiness
        return report

    def _first_edition(self, job: dict, report: RunReport) -> RunReport:
        existing = self.db.editions_for_day(job["agent_id"], self.today)
        if existing:
            self.db.complete(job["id"])
            log.info("first_edition noop job=%s existing=%d", job["id"], len(existing))
            report.action = "noop"
            report.seats = None
            report.detail["reason"] = "same_day_edition_exists"
            return report

        brief = self._load_active(job["agent_id"])
        compiled = brief.get("compiled_config")
        if not compiled:
            raise HuntError("no_compiled_config")
        readiness = brief.get("readiness")
        if readiness != "ready":
            raise HuntError("readiness_blocked")

        try:
            collector = self.collector if self.collector is not None else live_collect
            raw = collector(compiled)
        except HuntError:
            raise
        except Exception:
            log.exception("collector error job=%s (exception class only above)",
                          job["id"])
            raise HuntError("hunt_adapter_failed")

        seats = judge_seats(raw or [], compiled)
        history = personal_history(self.db.prior_edition_payloads(job["agent_id"]))
        seats = attach_market_fields(seats, history, self.today)
        sha = current_engine_sha()
        payload = build_payload(seats, compiled, sha)
        html_doc = render_edition_html(seats)
        if "DUMMY ROLE" in html_doc:
            raise HuntError("edition_persist_failed")
        outcome = "empty" if not seats else "seats"
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
        try:
            self.db.insert_edition({
                "agent_id": job["agent_id"],
                "edition_date": self.today.isoformat(),
                "brief_version": version,
                "html": html_doc,
                "payload": payload,
                "outcome": outcome,
                "delivered_at": now,
            })
        except Exception:
            log.exception("edition persist error job=%s", job["id"])
            raise HuntError("edition_persist_failed")
        self.db.complete(job["id"])
        log.info("first_edition job=%s outcome=%s seats=%d", job["id"], outcome, len(seats))
        report.action = "edition"
        report.seats = len(seats)
        report.detail["outcome"] = outcome
        return report


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    for noisy in ("httpx", "httpcore", "anthropic", "urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    base = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    runner = Runner(db=RestDb(base, key))
    reports = runner.run()
    actions = ",".join(r.action for r in reports) or "none"
    log.info("done jobs=%d actions=%s", len(reports), actions)
    return 0 if all(r.action != "error" for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
