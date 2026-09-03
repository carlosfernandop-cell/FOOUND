"""Move 3 — sources follow the Brief. No network: fetchers are stand-ins."""
from __future__ import annotations

import types

import hunt_runner as hr
import market_sources as ms


def _ja():
    """A job_alerts stand-in: the real SCRAPERS shape, fetchers that never fetch."""
    real = hr._import_job_alerts_adapters()
    ja = types.SimpleNamespace()
    ja.SCRAPERS = list(real.SCRAPERS)
    ja.fetch_greenhouse = lambda slug, label: []
    ja.fetch_ashby = lambda slug, label: []
    ja.fetch_lever = lambda slug, label: []
    return ja


def _compiled(*phrases, priority=()):
    tokens: list[str] = []
    for p in phrases:
        toks, _ = hr.expand_location_phrase(p)
        tokens.extend(toks)
    return {"accepted_locations": tokens, "priority_companies": list(priority)}


US = ("nyc", "california", "remote us")
BERLIN = ("berlin", "london", "amsterdam", "remote europe")


def test_every_founding_board_has_a_region_row():
    ja = _ja()
    labels = {e[0] for e in ja.SCRAPERS}
    assert labels == set(ms.FOUNDING_REGIONS), labels ^ set(ms.FOUNDING_REGIONS)
    # and every one posts in the US or everywhere: a US Brief keeps the founding universe whole
    for label, regions in ms.FOUNDING_REGIONS.items():
        assert "us" in regions or "global" in regions, label


def test_us_brief_keeps_the_founding_universe_whole_and_in_order():
    ja = _ja()
    entries, summary = ms.select_sources(_compiled(*US), ja, probe=lambda ja, n: None)
    n = len(ja.SCRAPERS)
    assert summary["founding"] == summary["founding_total"] == n
    assert entries[:n] == ja.SCRAPERS            # same tuples, same order
    assert summary["regions"] == ["remote", "us"]
    assert summary["named"] == [] and summary["probed"] == []
    # boards that post in the US join; boards that do not, do not
    assert "HelloFresh" in summary["added"] and "Miro" in summary["added"]
    assert "Kittl" not in summary["added"] and "N26" not in summary["added"]


def test_berlin_brief_reads_berlin_boards_not_us_only_agencies():
    ja = _ja()
    entries, summary = ms.select_sources(_compiled(*BERLIN), ja, probe=lambda ja, n: None)
    labels = [e[0] for e in entries]
    assert {"de", "uk", "nl", "remote", "eu"} <= set(summary["regions"])
    for berlin in ("N26", "Kittl", "Doctolib", "GetYourGuide", "Babbel"):
        assert berlin in summary["added"], berlin
    for amsterdam in ("Mollie", "Miro", "Adyen"):
        assert amsterdam in summary["added"], amsterdam
    # founding boards that post in Europe stay; US-only ones do not
    assert "Figma" in labels and "Spotify" in labels and "Koto" in labels
    for us_only in ("Preacher", "Johannes Leonardo", "Discord", "Suno", "xAI"):
        assert us_only not in labels, us_only
    assert summary["founding"] < summary["founding_total"]
    assert summary["selected"] == len(entries) == len(set(labels))   # no duplicates


def test_europe_as_a_whole_meets_any_european_board():
    assert ms.regions_meet(("de",), {"eu"})
    assert ms.regions_meet(("eu",), {"de"})
    assert ms.regions_meet(("global",), set())
    assert not ms.regions_meet(("us",), {"de"})
    assert not ms.regions_meet(("de",), set())


def test_unmapped_geography_reads_only_the_global_houses():
    ja = _ja()
    entries, summary = ms.select_sources({"accepted_locations": ["lagos"]}, ja, probe=lambda ja, n: None)
    labels = [e[0] for e in entries]
    assert summary["regions"] == []
    assert "Netflix" in labels and "Apple" in labels and "Nvidia" in labels
    assert "Preacher" not in labels and "N26" not in labels


def test_named_company_in_the_registry_is_read_without_a_probe():
    ja = _ja()
    calls = []
    entries, summary = ms.select_sources(_compiled(*US, priority=["Kittl"]), ja,
                                         probe=lambda ja, n: calls.append(n))
    assert summary["named"] == ["Kittl"] and calls == [] and summary["probed"] == []
    assert entries[-1][0] == "Kittl" and entries[-1][1] is ja.fetch_ashby and entries[-1][2] == "kittl"


def test_named_company_already_selected_is_not_read_twice():
    ja = _ja()
    entries, summary = ms.select_sources(_compiled(*US, priority=["Apple", "figma"]), ja,
                                         probe=lambda ja, n: (_ for _ in ()).throw(AssertionError("probed")))
    labels = [e[0] for e in entries]
    assert labels.count("Apple") == 1 and labels.count("Figma") == 1
    assert summary["named"] == []


def test_unknown_named_company_is_probed_and_read_when_its_board_answers():
    ja = _ja()
    found = ("Acme Studio", ja.fetch_ashby, "acmestudio", "Acme Studio")
    entries, summary = ms.select_sources(_compiled(*US, priority=["Acme Studio", "Nowhere Inc"]), ja,
                                         probe=lambda ja, n: found if n == "Acme Studio" else None)
    assert summary["probed"] == ["Acme Studio", "Nowhere Inc"]
    assert summary["named"] == ["Acme Studio"]
    assert entries[-1] == found


def test_slug_candidates():
    assert ms.slug_candidates("Hugging Face") == ["huggingface", "hugging-face"]
    assert ms.slug_candidates("Koto Ltd")[:2] == ["kotoltd", "koto-ltd"] and "koto" in ms.slug_candidates("Koto Ltd")
    assert ms.slug_candidates("  ") == []


def test_probe_named_company_asks_each_ats_and_takes_the_first_that_answers():
    ja = _ja()
    asked = []

    def fetch(fn, slug, label):
        asked.append((fn.__name__ if hasattr(fn, "__name__") else "fn", slug))
        return [{"title": "Head of Design"}] if (fn is ja.fetch_ashby and slug == "acme-studio") else []

    entry = ms.probe_named_company(ja, "Acme Studio", fetch=fetch)
    assert entry == ("Acme Studio", ja.fetch_ashby, "acme-studio", "Acme Studio")
    # greenhouse asked both slugs first, then ashby found it on its second slug
    assert asked[:2] == [("<lambda>", "acmestudio"), ("<lambda>", "acme-studio")]
    assert ms.probe_named_company(ja, "Nowhere", fetch=lambda fn, s, l: []) is None
    assert ms.probe_named_company(ja, "Boom", fetch=lambda fn, s, l: (_ for _ in ()).throw(RuntimeError())) is None


def test_registry_rows_are_well_formed_and_unique():
    labels = [s.label for s in ms.REGISTRY]
    assert len(labels) == len(set(labels))
    founding = {l.lower() for l in ms.FOUNDING_REGIONS}
    for s in ms.REGISTRY:
        assert s.ats in ("greenhouse", "ashby", "lever"), s
        assert s.slug and s.regions, s
        assert s.label.lower() not in founding, s   # the founding 41 are not repeated here
        for r in s.regions:
            assert r == "global" or r == "remote" or r == "us" or r == "ca" or r in ms.EU_REGIONS, (s.label, r)


def test_remote_europe_is_a_place_the_gazetteer_knows():
    toks, mapped = hr.expand_location_phrase("remote Europe")
    assert mapped and "remote" in toks and "berlin" in toks and "europe" in toks
    assert ms.brief_regions({"accepted_locations": toks}) >= {"remote", "eu", "de", "uk"}


def test_search_queries_are_the_briefs_own_words():
    assert hr.search_queries_for(["head of design", "vp design", "design director"]) == [
        "design", '"head of design"', '"vp design"', '"design director"']
    # no one else's vocabulary rides along
    for q in hr.search_queries_for(["head of design"]):
        assert q not in hr.ENGINE_DEFAULT_SEARCH_QUERIES
    # a Brief with no families at all falls back to the engine defaults
    assert hr.search_queries_for([]) == list(hr.ENGINE_DEFAULT_SEARCH_QUERIES)
    # capped, recall nouns first
    many = hr.search_queries_for([f"head of craft{i}" for i in range(10)])
    assert len(many) == hr.MAX_SEARCH_QUERIES and many[0] == "craft0"


if __name__ == "__main__":   # CI runs this without pytest
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {e!r}")
    print(f"GATE: {'PASS' if not failed else 'FAIL'} — {len(fns) - failed}/{len(fns)} Move 3 source tests.")
    sys.exit(1 if failed else 0)
