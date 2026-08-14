"""
Behavior-preservation harness for the agent-parameterization refactor.

Renders a full edition offline from fixed mock data, with a frozen clock and
no network, so the HTML produced BEFORE and AFTER the refactor can be diffed
byte-for-byte. If the diff is empty, the refactor changed structure only.

Network is avoided by leaving ANTHROPIC_KEY empty: rank_with_fit returns the
heuristic ordering immediately, before any JD fetch.

    python3 render_edition.py <path-to-job_alerts.py> <output.html>
"""
import datetime
import os
import shutil
import sys
import tempfile
import types


def main(target: str, out_path: str, normalize: bool = False) -> int:
    target = os.path.abspath(target)
    out_path = os.path.abspath(out_path)
    src_dir = os.path.dirname(target)

    os.environ.pop("ANTHROPIC_API_KEY", None)      # force heuristic: no network
    os.environ["NOTION_TOKEN"] = ""
    os.environ["NOTION_DB_ID"] = ""
    os.environ["GMAIL_USER"] = ""
    os.environ["GMAIL_APP_PASSWORD"] = ""
    os.environ["RECIPIENT_EMAIL"] = ""

    work = tempfile.mkdtemp()
    # profile.md must exist for the pre-refactor version to load a profile.
    shutil.copy(os.path.join(src_dir, "profile.md"), os.path.join(work, "profile.md"))
    os.makedirs(os.path.join(work, "docs", "archive"), exist_ok=True)

    sys.path.insert(0, src_dir)
    sys.argv = ["job_alerts.py"]                   # avoid TEST_MODE

    spec = types.ModuleType("ja")
    code = open(target).read()
    exec(compile(code, target, "exec"), spec.__dict__)

    # Frozen clock so date strings are stable across runs.
    frozen = datetime.datetime(2026, 8, 14, 8, 3, 0)
    spec._et_now = lambda: frozen

    utc = datetime.timezone.utc
    def when(d):
        return datetime.datetime(2026, 8, d, 12, 0, 0, tzinfo=utc)

    matches = [
        {"title": "Head of Brand", "company": "Suno", "location": "New York, NY",
         "url": "https://example.com/1", "posted_at": when(13)},
        {"title": "Creative Director", "company": "Anthropic", "location": "San Francisco, CA",
         "url": "https://example.com/2", "posted_at": when(12)},
        {"title": "VP of Brand", "company": "Ramp", "location": "New York, NY",
         "url": "https://example.com/3", "posted_at": when(11)},
        {"title": "Brand Director", "company": "Spotify", "location": "Remote",
         "url": "https://example.com/4", "posted_at": when(10)},
        {"title": "Design Director", "company": "Apple", "location": "Cupertino, CA",
         "url": "https://example.com/5", "posted_at": when(9)},
    ]
    new_keys = {"head of brand|suno", "creative director|anthropic"}

    cwd = os.getcwd()
    os.chdir(work)
    try:
        build = spec["build_shortlist"] if isinstance(spec, dict) else spec.build_shortlist
        try:
            build(matches, new_keys, 6204)
        except TypeError:
            # post-refactor signature takes an agent config.
            # FOOUND_DB_ROW lets the equivalence test render the SAME edition
            # from a database row instead of the seeded registry.
            db_row = os.environ.get("FOOUND_DB_ROW")
            if db_row:
                import json as _json
                import foound_agent as _fa
                agent = _fa.load_agent_config_from_db(
                    "001", fetch_row=lambda _i: dict(_json.loads(db_row), agent_no=1))
            else:
                agent = spec.load_agent_config("001")
            build(agent, matches, new_keys, 6204)
        rendered = open(os.path.join(work, "docs", "index.html")).read()
    finally:
        os.chdir(cwd)

    if normalize:
        # Structural fingerprint: survives legitimate per-user/per-date
        # variation once exact bytes stop being possible. The principle the
        # harness protects is not "identical bytes" but "refactoring
        # infrastructure did not silently redesign FOOUND".
        import re as _re
        rendered = _re.sub(r"\d{4}-\d{2}-\d{2}", "<DATE>", rendered)
        rendered = _re.sub(r"(?:January|February|March|April|May|June|July|"
                           r"August|September|October|November|December)\s+\d{1,2},\s+\d{4}",
                           "<DATELONG>", rendered)
        rendered = _re.sub(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|"
                           r"Saturday|Sunday)", "<DAY>", rendered)
        rendered = _re.sub(r"Good (?:morning|afternoon|evening), [^.<]+\.",
                           "<GREETING>", rendered)
        rendered = _re.sub(r"\b\d{1,3}(?:,\d{3})+\b", "<COUNT>", rendered)
        # Relative ages drift with the real clock even under a frozen edition
        # clock — a fixture made Monday would fail Tuesday. Normalize them.
        rendered = _re.sub(r"posted (?:today|yesterday|\d+ days? ago)", "posted <AGE>", rendered)
        rendered = _re.sub(r"Posted (?:today|yesterday|\d+ days? ago)", "Posted <AGE>", rendered)

    with open(out_path, "w") as f:
        f.write(rendered)
    print(f"rendered {len(rendered):,} bytes -> {out_path}")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2],
                  normalize="--normalize" in sys.argv))
