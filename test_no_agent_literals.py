"""
ACCEPTANCE GATE — "zero Carlos literals in the per-agent execution path".

The architectural aspiration is that commissioning №002 creates rows and
touches no Python. That is an ASPIRATION until proven, and Carlos-specific
assumptions hide in exactly the places nobody greps: prompt text, file paths,
email subjects and salutations, output paths, greeting copy, scheduling, and
default arguments.

This test scans the functions that run PER AGENT and fails on any literal that
belongs to one person. Run it before declaring the system №002-ready.

    python3 test_no_agent_literals.py path/to/job_alerts.py

Expected TODAY: fail, loudly, with a to-do list. That is the point — it turns
a claim into a checklist.
"""
from __future__ import annotations

import ast
import re
import sys

# Functions that execute once per agent. Anything here must be parameterized
# by agent state. Market-layer functions (scrapers, adapters) are exempt —
# they are shared and company names there are data, not identity.
PER_AGENT_FUNCS = {
    "main", "build_shortlist", "build_edition", "rank_with_fit", "score_fit",
    "write_brief", "deep_look", "send_email", "load_profile", "publish_shortlist",
    "_entry", "_argument", "_evidence_links", "matched_keywords",
    "passes_title", "passes_location", "get_existing_keys", "add_to_notion",
}

# Literals that identify one person or one deployment.
PATTERNS = [
    (r"\bCarlos\b",                        "candidate first name"),
    (r"\bFernando\b|\bcarlosfernandop\b",  "candidate name / handle"),
    (r"profile\.md",                       "single-profile file path"),
    # Bot/no-reply addresses are infrastructure, not a person. Everything else
    # that looks like an address in the per-agent path is a delivery target and
    # must come from agent config.
    (r"(?!.*noreply)[\w.+-]+@[\w-]+\.[\w.]+",  "hardcoded email address"),
    (r"\bdocs/(?!archive)",                "hardcoded public output path"),
    (r"foound\.ai",                        "hardcoded host"),
    (r"\bAustin\b|\bNew York\b|\bNYC\b",   "candidate-specific location"),
    (r"\bAirbnb\b|\bApple\b",              "candidate-specific employer/priority"),
    (r"Hi Carlos|Good morning, Carlos",    "personalized copy"),
]

# Module-level config that must become per-agent rows before №002.
CONFIG_CONSTANTS = [
    "INCLUDE", "EXCLUDE_TYPE", "ACCEPTED_LOCATIONS", "SEARCH_QUERIES",
    "PRIORITY_COMPANIES", "MANUAL_JOBS",
]


def scan(path: str) -> int:
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    lines = src.splitlines()
    findings: list[tuple[str, int, str, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in PER_AGENT_FUNCS:
            continue
        start, end = node.lineno, (node.end_lineno or node.lineno)
        for i in range(start - 1, min(end, len(lines))):
            line = lines[i]
            if line.lstrip().startswith("#"):
                continue
            for pat, why in PATTERNS:
                m = re.search(pat, line)
                if m:
                    findings.append((node.name, i + 1, m.group(0)[:40], why))

    print("=" * 72)
    print("ACCEPTANCE GATE: zero Carlos literals in the per-agent execution path")
    print("=" * 72)

    if findings:
        print(f"\n{len(findings)} literal(s) found in per-agent functions:\n")
        by_fn: dict[str, list] = {}
        for fn, ln, txt, why in findings:
            by_fn.setdefault(fn, []).append((ln, txt, why))
        for fn in sorted(by_fn):
            print(f"  {fn}()")
            for ln, txt, why in by_fn[fn][:8]:
                print(f"      line {ln}: {txt!r}  — {why}")
            if len(by_fn[fn]) > 8:
                print(f"      … and {len(by_fn[fn]) - 8} more")
            print()
    else:
        print("\nNo per-agent literals found.\n")

    present = [c for c in CONFIG_CONSTANTS
               if re.search(rf"^{c}\s*=", src, re.M)]
    if present:
        print(f"{len(present)} module-level config constant(s) still hardcoded —")
        print("these must become per-agent rows before №002:\n")
        for c in present:
            print(f"      {c}")
        print()

    total = len(findings) + len(present)
    if total:
        print("-" * 72)
        print(f"GATE: FAIL — {total} item(s) block №002-readiness.")
        print("The system is not yet 'add a row, get an agent'.")
        return 1

    print("-" * 72)
    print("GATE: PASS — per-agent execution path is agent-parameterized.")
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "job_alerts.py"
    sys.exit(scan(target))
