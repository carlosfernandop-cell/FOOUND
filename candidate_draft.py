"""
FOOUND — Candidate draft (Move 4: the public face).

    confirmed Memory  →  a DRAFT Candidate page  →  the person confirms it

The Candidate page is FOOUND's most shared artefact: the dossier a hiring
leader receives at foound.ai/candidate/<serial>. FOOUND writes it once,
from confirmed Memory only, in the page's own shape (migration 015); the
person edits the draft in their Candidate room and publishes it through
publish_candidate. Nothing unconfirmed is ever public, and nothing here
publishes: a draft is inert until the person acts.

Rules the drafter must keep (they are what the seal promises):
  · Every chapter and every "trusted with" is grounded in confirmed
    statement ids. No ground, no line.
  · Nothing is invented. No references, no quotes in the person's voice,
    no links, no name unless a confirmed statement carries them. Those
    fields are left empty for the person to fill.
  · The page says where the person is based, never where they would go:
    the Brief's WHERE is private. `open_to` is never written.
  · No em dashes anywhere (house voice).

Model text never reaches logs; the draft is stored as candidates.page with
a provenance block the page renderer ignores.
"""
from __future__ import annotations

import json
import re

DRAFT_FORMAT = 1
MAX_CHAPTERS = 6
MAX_TRUSTED = 3
MAX_LINE_CHARS = 360
MAX_NARRATIVE_CHARS = 700
MAX_WORD_CHARS = 40
_URL = re.compile(r"https?://\S+", re.I)


def draft_candidate_prompt(context_text: str, rows: list[dict]) -> str:
    grounded = "\n".join(
        f"[{r['id']}] ({r.get('layer', '')}{(' · ' + str(r.get('handle'))) if r.get('handle') else ''}) {r['statement']}"
        for r in rows)
    return (
        "You are FOOUND, the personal career agent of one client. Write plainly, in the third person. "
        "Never use em dashes or long dashes anywhere; use commas, colons, or periods instead.\n\n"
        "Draft this client's CANDIDATE PAGE: the public dossier a hiring leader receives about them. "
        "It is representation, not a profile: you select, you edit, you state the pattern across their career. "
        "It must be extraordinarily easy to scan and rewarding to read.\n\n"
        "Write it ONLY from the confirmed statements below. Every chapter and every trusted-with item must cite the "
        "statement ids that support it in `grounds`. Do not invent anything: no references, no quotes in the client's "
        "voice, no links, no name, no employer, no date, no number that a statement does not give you. "
        "Leave a field empty rather than guess. Never write where they would like to work; write only where they are based, "
        "and only if a statement says so.\n\n"
        f"CANDIDATE CONTEXT (what they confirmed):\n{context_text}\n\n"
        f"CONFIRMED STATEMENTS WITH IDS:\n{grounded}\n\n"
        "THE SHAPE (fixed):\n"
        "- line: one paragraph, at most three sentences, beginning exactly \"This is the candidate I work for.\" "
        "Then the span of the career and the pattern you see across it. This is your judgment, stated with confidence.\n"
        "- now: their current seat and company, as a short phrase, if a statement gives it. based: the city, if a statement gives it. "
        "since: the year their career began, if statements give it.\n"
        "- chapters: the career as houses, newest first, at most six. Each: company (the name only), years (like \"{2022–}\" or \"{2018–22}\"), "
        "at_rest (the title and one short clause, under 120 characters), narrative (two or three sentences, under 70 words: what they were trusted with and what happened), "
        "meta (city and years, like \"Berlin · 2022–present\"), grounds.\n"
        "- trusted_with: at most three. Each: word (a short phrase of two to four words, like \"The first hire\"), line (one sentence of evidence), grounds. "
        "Specific to this person; never generic skills.\n"
        "- languages: like \"EN / SV / DE\", only if statements give them.\n\n"
        "Return ONLY JSON, no other text:\n"
        '{"line": "This is the candidate I work for. ...", "now": "...", "based": "...", "since": "2012", '
        '"chapters": [{"company": "...", "years": "{2022–}", "at_rest": "...", "narrative": "...", "meta": "...", "grounds": ["<id>"]}], '
        '"trusted_with": [{"word": "...", "line": "...", "grounds": ["<id>"]}], "languages": "..."}'
    )


def _balanced_json_object(text: str) -> dict | None:
    found = None
    for m in re.finditer(r"\{", text or ""):
        depth = 0
        for i in range(m.start(), len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    span = text[m.start():i + 1]
                    if '"chapters"' in span:
                        try:
                            cand = json.loads(span)
                            if isinstance(cand, dict) and "chapters" in cand:
                                return cand          # the outermost page object wins
                        except Exception:
                            pass
                    break
    return found


def _clean(value, limit: int) -> str:
    s = re.sub(r"\s+", " ", str(value or "")).strip()
    s = s.replace("—", ",").replace("–", "-") if limit < 60 else s.replace("—", ",")
    return s[:limit]


def _years(value) -> str:
    s = re.sub(r"\s+", "", str(value or ""))
    s = s.strip("{}")
    return "{" + s[:12] + "}" if s else ""


def parse_candidate_draft(text: str, valid_ids) -> tuple[dict | None, str]:
    """Strict parse → page in the 015 shape, or (None, reason). Chapters and
    trusted-withs without a valid ground are dropped; links, references,
    quotes and names are never taken from the model."""
    obj = _balanced_json_object(text or "")
    if obj is None:
        # Name the failure so a beat log can tell an empty reply (key, model,
        # network) from a reply that began JSON and never closed it (cut off
        # at the token budget) from prose with no object at all.
        t = (text or "").strip()
        if not t:
            return None, "empty_reply"
        if "{" in t and t.count("{") > t.count("}"):
            return None, "unbalanced_json"
        return None, "no_json"
    valid = {str(v) for v in (valid_ids or [])}
    line = _clean(obj.get("line"), MAX_LINE_CHARS)
    if not line.startswith("This is the candidate I work for"):
        line = ("This is the candidate I work for. " + line).strip() if line else ""
    if _URL.search(line):
        line = _URL.sub("", line).strip()
    chapters = []
    for ch in (obj.get("chapters") or [])[:MAX_CHAPTERS]:
        if not isinstance(ch, dict):
            continue
        grounds = [str(g) for g in (ch.get("grounds") or []) if str(g) in valid]
        company = _clean(ch.get("company"), MAX_WORD_CHARS)
        if not company or not grounds:
            continue
        chapters.append({
            "company": company,
            "years": _years(ch.get("years")),
            "at_rest": _clean(ch.get("at_rest"), 120),
            "narrative": _clean(ch.get("narrative"), MAX_NARRATIVE_CHARS),
            "meta": _clean(ch.get("meta"), 80),
            "grounds": grounds,
        })
    if not chapters:
        return None, "no_grounded_chapters"
    trusted = []
    for t in (obj.get("trusted_with") or [])[:MAX_TRUSTED]:
        if not isinstance(t, dict):
            continue
        grounds = [str(g) for g in (t.get("grounds") or []) if str(g) in valid]
        word = _clean(t.get("word"), MAX_WORD_CHARS)
        ln = _clean(t.get("line"), 200)
        if word and ln and grounds:
            trusted.append({"word": word, "line": ln, "grounds": grounds})
    page = {
        "name": [],                      # the person's to type; the door requires it
        "line": line,
        "now": _clean(obj.get("now"), 80),
        "based": _clean(obj.get("based"), 40),
        "since": re.sub(r"[^0-9]", "", str(obj.get("since") or ""))[:4],
        "chapters": chapters,
        "trusted_with": trusted,
        "own_words": "",                 # the person's voice, never the model's
        "work": [],
        "references": [],
        "links": {},
        "languages": _clean(obj.get("languages"), 30),
    }
    if not page["line"]:
        return None, "no_line"
    return page, ""


def provenance(engine_sha: str, context_hash: str, model: str, attempts: int) -> dict:
    return {"drafted_by": "engine", "format": DRAFT_FORMAT, "engine_sha": engine_sha,
            "candidate_context_hash": context_hash, "model": model, "attempts": attempts}
