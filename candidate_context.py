"""
FOOUND — Candidate Context (Move 2: Person from Memory).

The original judge reads one document about the person (`profile.md` for
№001) and argues every role against it. This module writes that document
for ANY client from what FOOUND is allowed to believe about them:

    confirmed Memory  →  Candidate Context  →  the original judge

Contract (unchanged by this module):
  · Confirmed Memory is understanding. Only rows the client confirmed
    (status active, provenance confirmed) enter the context. Stated,
    extracted and inferred rows are FOOUND's beliefs awaiting a verdict
    and are excluded — the client has not yet said "that is me".
  · The Working Brief is authorization. It is rendered into the context so
    the judge knows what the client is looking for, but eligibility was
    decided by the Brief BEFORE judgment; the context can never admit a
    role the Brief did not.
  · Statements are verbatim. The compiler arranges; it never paraphrases,
    merges, ranks, or infers — the same rule the Mirror lives by.

Pure and deterministic: same rows + same Brief → same text and same hash.
The hash is the context's version; every edition records the one it used.
"""
from __future__ import annotations

import hashlib
import json

CONTEXT_FORMAT = 1

LAYER_ORDER = ("record", "self", "model", "behavior")
LAYER_TITLES = {
    "record": "Record",
    "self": "In their own words",
    "model": "FOOUND's read",
    "behavior": "What FOOUND has noticed",
}
STATEMENT_CAP = 1000        # matches memory.statement's check constraint
MAX_STATEMENTS = 400        # a context is a portrait, not an archive


def confirmed_rows(rows) -> list[dict]:
    """The rows the client has confirmed, in a stable order.

    Order: layer (record → self → model → behavior), then created_at, then
    id — every key is a contract field; nothing reads the statement."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        if (r.get("status") or "") != "active":
            continue
        if (r.get("provenance") or "") != "confirmed":
            continue
        if (r.get("layer") or "") not in LAYER_TITLES:
            continue
        stmt = str(r.get("statement") or "").strip()
        if not stmt:
            continue
        out.append({
            "id": str(r.get("id") or ""),
            "layer": r["layer"],
            "statement": stmt[:STATEMENT_CAP],
            "source": str(r.get("source") or "").strip(),
            "created_at": str(r.get("created_at") or ""),
        })
    out.sort(key=lambda r: (LAYER_ORDER.index(r["layer"]), r["created_at"], r["id"]))
    return out[:MAX_STATEMENTS]


def _brief_text(content) -> list[str]:
    """The Working Brief's chapters as plain lines — verbatim subject lines,
    one paragraph per chapter. Nothing is interpreted here; the compiler that
    decides eligibility reads the same content separately."""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return []
    if not isinstance(content, dict):
        return []
    lines = []
    for ch in content.get("chapters") or []:
        if not isinstance(ch, dict):
            continue
        title = str(ch.get("title") or "").strip()
        parts = []
        for sub in ch.get("subjects") or []:
            if not isinstance(sub, dict):
                continue
            for ln in sub.get("lines") or []:
                ln = str(ln or "").strip()
                if ln:
                    parts.append(ln)
        if title and parts:
            lines.append(f"{title}: " + " ".join(parts))
    return lines


def context_hash(rows: list[dict], brief_content) -> str:
    """sha256 over what the judge will actually read: the confirmed
    statements (layer, statement, source) in order, the Brief text lines,
    and the format number."""
    canon = {
        "format": CONTEXT_FORMAT,
        "statements": [(r["layer"], r["statement"], r["source"]) for r in rows],
        "brief": _brief_text(brief_content),
    }
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compile_candidate_context(*, name: str = "", rows=None, brief_content=None) -> dict:
    """confirmed Memory + active Brief → the judge's document.

    Returns {"text", "hash", "statements", "layers", "sources", "format"}.
    An empty `text` (no confirmed statements) means there is no Candidate
    Context; callers refuse to judge rather than judge against nobody."""
    keep = confirmed_rows(rows)
    layers = {k: 0 for k in LAYER_ORDER}
    sources: set[str] = set()
    for r in keep:
        layers[r["layer"]] += 1
        if r["source"]:
            sources.add(r["source"])
    if not keep:
        return {"text": "", "hash": "", "statements": 0, "layers": layers,
                "sources": [], "format": CONTEXT_FORMAT}

    who = name.strip() if name else "the client"
    n = len(keep)
    lines = [
        f"# Candidate Context — {who}",
        "",
        f"Everything below is a statement {who if name else 'the client'} confirmed as true "
        f"({n} statement{'s' if n != 1 else ''}). Read it as their record and their own account; "
        "do not add beliefs that are not here.",
    ]
    for layer in LAYER_ORDER:
        group = [r for r in keep if r["layer"] == layer]
        if not group:
            continue
        lines += ["", f"## {LAYER_TITLES[layer]} (confirmed)"]
        for r in group:
            lines.append(f"- {r['statement']}")
        srcs = sorted({r["source"] for r in group if r["source"]})
        if srcs:
            lines.append(f"  Sources: {', '.join(srcs)}")
    brief_lines = _brief_text(brief_content)
    if brief_lines:
        lines += ["", "## What they are looking for (the active Working Brief, authorized by them)"]
        for ln in brief_lines:
            lines.append(f"- {ln}")
        lines += ["",
                  "Eligibility was decided by the Working Brief before this judgment. Use this "
                  "context to judge how good an eligible role is for this person; never to admit "
                  "a role the Brief did not authorize."]
    text = "\n".join(lines).rstrip() + "\n"
    return {
        "text": text,
        "hash": context_hash(keep, brief_content),
        "statements": n,
        "layers": layers,
        "sources": sorted(sources),
        "format": CONTEXT_FORMAT,
    }
