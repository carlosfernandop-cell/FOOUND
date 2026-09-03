"""
FOOUND — Brief proposal (Move 2: Person from Memory).

    confirmed Memory  →  a PROPOSED Working Brief  →  the client confirms

The Working Brief is authorization. Today only №001 has one, written by
hand. For anyone else FOOUND drafts one — in the Brief's own grammar
(THE MOVE / ROLE SPACE / WHERE / AVOID), every line grounded in a
statement the client confirmed — and the client confirms it through the
activate_brief door (migration 013). A proposal is inert until then:
nothing here makes Memory authority.

Two halves:
  · draft_brief_prompt / parse_brief_draft — the model writes the chapters;
    the parse is strict (JSON only, known chapter titles, short lines,
    grounds that point at real confirmed statement ids).
  · check_proposal — the engine's own compiler (hunt_runner.compile_from_content)
    must read the draft as executable: at least one ROLE family and one
    accepted location. If not, the draft is sent back once with what the
    compiler could not read, so FOOUND writes within what it can hunt.

Model text never reaches logs; the proposal is stored as briefs.content
with a provenance block (engine sha, context hash, model) the compiler
ignores.
"""
from __future__ import annotations

import json
import re

CHAPTERS = ("THE MOVE", "ROLE SPACE", "WHERE", "AVOID")
REQUIRED_CHAPTERS = ("THE MOVE", "ROLE SPACE", "WHERE")
MAX_SUBJECTS_PER_CHAPTER = 4
MAX_LINES_PER_SUBJECT = 3
MAX_LINE_CHARS = 220
MAX_HANDLE_CHARS = 24
PROPOSAL_FORMAT = 1


def draft_brief_prompt(context_text: str, rows: list[dict], feedback: str = "") -> str:
    """The drafting prompt. `rows` are the confirmed statements with ids so
    the model can ground every line; `feedback` is the compiler's reading of
    a previous draft, when a second pass is needed."""
    grounded = "\n".join(f"[{r['id']}] ({r['layer']}) {r['statement']}" for r in rows)
    return (
        "You are FOOUND, the personal career agent of one client. Write plainly. Never use em dashes or long dashes anywhere; use commas, colons, or periods instead.\n\n"
        "Draft this client's WORKING BRIEF: the authorization that tells you what to hunt for them. "
        "It is not a profile and not a summary. It is intent, in their own terms, that they will read and confirm or correct.\n\n"
        "Write it ONLY from the confirmed statements below. Every line must be supportable by at least one of them; "
        "cite the statement ids in `grounds`. If the statements do not say where they want to work or what seat they want, "
        "do not invent it: write the WHERE or ROLE SPACE line you can support and add a subject with handle \"Still learning\" "
        "naming what you could not find.\n\n"
        f"CANDIDATE CONTEXT (what they confirmed):\n{context_text}\n\n"
        f"CONFIRMED STATEMENTS WITH IDS:\n{grounded}\n\n"
        "THE GRAMMAR (fixed):\n"
        "- THE MOVE: what they want to do next, as 2 or 3 short subjects. Handles like \"Lead\", \"Build\", \"The seat\". "
        "Each line one sentence, second person is fine (\"Lead design for a product still being defined.\").\n"
        "- ROLE SPACE: the seats, as titles, one subject. Handle \"Craft\". The line is a comma-separated list of job titles "
        "exactly as postings would name them (e.g. \"Head of Design, VP Design, Design Director.\"). No adjectives, no sentences.\n"
        "- WHERE: geography, one subject. Handle \"Geography\". The line is a comma-separated list of cities, countries, regions, "
        "or \"remote\" phrases exactly as postings name them (e.g. \"Berlin, London, Amsterdam, remote Europe.\").\n"
        "- AVOID (optional): what they do not want, one subject, one or two lines, only if a confirmed statement says so.\n\n"
        + (("YOUR PREVIOUS DRAFT COULD NOT BE HUNTED. The compiler reported: " + feedback + "\n"
            "Rewrite ROLE SPACE as plain job titles and WHERE as plain place names so they can be read mechanically.\n\n")
           if feedback and not feedback.startswith("The client marked") else
           (feedback + "\n\n" if feedback else ""))
        + "Return ONLY JSON, no other text:\n"
        '{"chapters": [{"title": "THE MOVE", "subjects": [{"handle": "Lead", "lines": ["..."], "grounds": ["<id>"]}]}, '
        '{"title": "ROLE SPACE", "subjects": [{"handle": "Craft", "lines": ["Title, Title, Title."], "grounds": ["<id>"]}]}, '
        '{"title": "WHERE", "subjects": [{"handle": "Geography", "lines": ["Place, Place, remote Region."], "grounds": ["<id>"]}]}]}'
    )


def _balanced_json_object(text: str) -> dict | None:
    """The last brace-balanced object in `text` that names "chapters"."""
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
                            if isinstance(cand, dict):
                                found = cand
                        except Exception:
                            pass
                    break
    return found


def parse_brief_draft(text: str, valid_ids) -> tuple[dict | None, str]:
    """Strict parse of the model's reply → Brief content in the app's shape,
    or (None, reason). Grounds are kept only when they name a confirmed id;
    a subject with no valid ground is dropped (the client must not confirm
    a line FOOUND cannot point at)."""
    obj = _balanced_json_object(text or "")
    if obj is None:
        return None, "no_json"
    valid = {str(v) for v in (valid_ids or [])}
    chapters_out = []
    for ch in obj.get("chapters") or []:
        if not isinstance(ch, dict):
            continue
        title = str(ch.get("title") or "").strip().upper()
        if title not in CHAPTERS:
            continue
        subjects_out = []
        for sub in (ch.get("subjects") or [])[:MAX_SUBJECTS_PER_CHAPTER]:
            if not isinstance(sub, dict):
                continue
            handle = str(sub.get("handle") or "").strip()[:MAX_HANDLE_CHARS]
            lines = []
            for ln in (sub.get("lines") or [])[:MAX_LINES_PER_SUBJECT]:
                ln = re.sub(r"\s+", " ", str(ln or "")).strip()
                if ln:
                    lines.append(ln[:MAX_LINE_CHARS])
            grounds = [str(g) for g in (sub.get("grounds") or []) if str(g) in valid]
            if not handle or not lines:
                continue
            if handle.lower() != "still learning" and not grounds:
                continue
            subjects_out.append({"handle": handle, "lines": lines, "grounds": grounds})
        if subjects_out:
            chapters_out.append({"title": title, "subjects": subjects_out})
    titles = [c["title"] for c in chapters_out]
    missing = [t for t in REQUIRED_CHAPTERS if t not in titles]
    if missing:
        return None, "missing_chapters:" + ",".join(missing)
    order = {t: i for i, t in enumerate(CHAPTERS)}
    chapters_out.sort(key=lambda c: order[c["title"]])
    return {"chapters": chapters_out}, ""


def check_proposal(content: dict, compile_fn, readiness_fn) -> tuple[bool, str]:
    """The engine's own compiler must read the draft as executable."""
    compiled = compile_fn(content)
    if readiness_fn(compiled) == "ready":
        return True, ""
    reasons = [r for r in (compiled.get("readiness_reasons") or [])
               if not r.startswith("unmapped_location_phrase:")]
    if not compiled.get("families"):
        reasons.append("no ROLE family could be read from ROLE SPACE (write plain job titles)")
    if not compiled.get("accepted_locations"):
        reasons.append("no place could be read from WHERE (write plain city, country or region names)")
    return False, "; ".join(reasons) or "not_ready"


def provenance(engine_sha: str, context_hash: str, model: str, attempts: int) -> dict:
    return {"proposed_by": "engine", "format": PROPOSAL_FORMAT, "engine_sha": engine_sha,
            "candidate_context_hash": context_hash, "model": model, "attempts": attempts}
