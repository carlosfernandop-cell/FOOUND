"""
Job Alerts — senior creative & brand leadership roles at AI / top-tier tech companies.
Customized for Carlos Perez (Creative Director).

Runs daily via GitHub Actions:
  1. Scrapes each company's careers API
  2. Filters by title keywords + location
  3. Saves new matches to a Notion database
  4. Emails a daily digest via Gmail
  5. Publishes THE SHORTLIST — a daily-edition microsite (docs/) via GitHub Pages

Test mode (no Notion/email, prints diagnostics only):
  python job_alerts.py --test
"""

import os
import re
import json
import sys
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, datetime, timezone, timedelta

TEST_MODE = "--test" in sys.argv

# -- Config (env vars not required in test mode)
if not TEST_MODE:
    NOTION_TOKEN   = os.environ["NOTION_TOKEN"]
    NOTION_DB_ID   = os.environ["NOTION_DB_ID"]
    GMAIL_USER     = os.environ["GMAIL_USER"]
    GMAIL_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
    RECIPIENT      = os.environ["RECIPIENT_EMAIL"]
    NOTION_HEADERS = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
else:
    NOTION_TOKEN = NOTION_DB_ID = GMAIL_USER = GMAIL_PASSWORD = RECIPIENT = ""
    NOTION_HEADERS = {}

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}

# ---- priority watch: companies Carlos has flagged. Their matches are always
#      read in full, and a role that clears the bar is never trimmed by the cap.
# PRIORITY_COMPANIES moved to foound_agent.AgentConfig (per-agent data)

# ---- hand-added roles: the unscrapeable tail and direct requests.
#      Injected into every run as if fetched (deduped against the scrapers),
#      then judged like everything else — pinned into the reading, not the verdict.
# MANUAL_JOBS moved to foound_agent.AgentConfig (per-agent data)

# ======================================================================
# FILTERS — edit these lists to change what jobs you get
# ======================================================================

# A job passes if its title contains AT LEAST ONE of these phrases.
# Note: "creative director" also matches Senior/Group/Executive/Associate CD titles.
# INCLUDE moved to foound_agent.AgentConfig (per-agent data)

# A job is dropped if its title contains ANY of these.
# EXCLUDE_TYPE moved to foound_agent.AgentConfig (per-agent data)

# Locations. Matching is word-boundary based ("us" will NOT match "Austin").
# A job with NO listed location passes automatically.
# ACCEPTED_LOCATIONS moved to foound_agent.AgentConfig (per-agent data)

# Search terms used by scrapers that require a query (Workday, Microsoft,
# Netflix, Apple, Spotify, GitHub). Keep these broad — the INCLUDE list
# above does the precise filtering afterwards.
# SEARCH_QUERIES moved to foound_agent.AgentConfig (per-agent data)

# ======================================================================
# Filter helpers
# ======================================================================

def passes_title(agent, title: str) -> bool:
    t = title.lower()
    if not any(k in t for k in agent.include):
        return False
    if any(k in t for k in agent.exclude_type):
        return False
    return True

def passes_location(agent, location: str) -> bool:
    if not location:
        return True
    loc = location.lower()
    # Workday shows "3 Locations" instead of city names for multi-city
    # postings — let those through rather than silently dropping them.
    if re.search(r"\d+\s+locations", loc):
        return True
    return any(re.search(rf"\b{re.escape(a)}\b", loc) for a in agent.accepted_locations)

def matched_keywords(agent, title: str) -> str:
    t = title.lower()
    return ", ".join(k for k in agent.include if k in t)[:120]

# -- Date helpers
def parse_iso(s: str):
    """Parse ISO 8601 string to UTC-aware datetime, None on failure."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None

def parse_unix(ts, millis=True):
    """Parse Unix timestamp (ms or s) to UTC datetime, None on failure."""
    try:
        v = int(ts)
        if millis:
            v = v / 1000
        return datetime.fromtimestamp(v, tz=timezone.utc)
    except Exception:
        return None

def parse_workday_date(text: str):
    """Parse Workday's human-readable 'Posted X Days Ago' text to datetime."""
    if not text:
        return None
    t = text.lower()
    now = datetime.now(timezone.utc)
    if "today" in t or "just posted" in t or "0 days" in t:
        return now
    if "yesterday" in t or "1 day" in t:
        return now - timedelta(days=1)
    m = re.search(r'(\d+)\s+day', t)
    if m:
        return now - timedelta(days=int(m.group(1)))
    return None

def is_recent(posted_at) -> bool:
    """True if posted_at is within the last 24 hours."""
    if posted_at is None:
        return False
    now = datetime.now(timezone.utc)
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    return (now - posted_at) <= timedelta(hours=24)

def format_age(posted_at) -> str:
    """Return a short human-readable age string, e.g. '3h ago' or 'Apr 14'."""
    if posted_at is None:
        return ""
    now = datetime.now(timezone.utc)
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    delta = now - posted_at
    hours = int(delta.total_seconds() // 3600)
    if hours < 24:
        return f"{hours}h ago" if hours > 0 else "just posted"
    return posted_at.strftime("%b %d")

# ======================================================================
# Notion helpers
# ======================================================================

def dedup_key(title: str, company: str) -> str:
    return f"{title.lower().strip()}|{company.lower().strip()}"

from foound_agent import (AgentConfig, load_agent_config,
                          load_agent_config_from_db, market_query_union)
import foound_state as _fstate

# Market-layer query set. Populated per run from the active agent until the
# shared market lands, at which point it becomes the union across agents.
MARKET_QUERIES: list = []

# Private state lives OUTSIDE the published directory. foound_state refuses to
# write here if this ever resolves inside the public output path.
STATE_DIR = os.environ.get("FOOUND_STATE_DIR", ".foound-state")

FIRST_SEEN_DATES = {}  # dedup_key -> ISO date FOOUND first saved the role (Notion "Date Found")

def get_existing_keys() -> set:
    """Read every (title, company) pair already saved in Notion.
    Side effect: fills FIRST_SEEN_DATES so freshness can fall back to
    first-seen when a source exposes no posting date."""
    url = f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query"
    existing = set()
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(url, headers=NOTION_HEADERS, json=body, timeout=15)
        if not r.ok:
            print(f"[Notion READ ERROR {r.status_code}]: {r.text[:300]}")
            break
        data = r.json()
        for page in data.get("results", []):
            props = page.get("properties", {})
            parts = props.get("Job Title", {}).get("title", [])
            title = parts[0].get("plain_text", "") if parts else ""
            company = (props.get("Company", {}).get("select") or {}).get("name", "")
            if title:
                k = dedup_key(title, company)
                existing.add(k)
                found = ((props.get("Date Found", {}) or {}).get("date") or {}).get("start")
                if found:
                    FIRST_SEEN_DATES[k] = found[:10]
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return existing

def add_to_notion(job: dict) -> bool:
    r = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json={
            "parent": {"database_id": NOTION_DB_ID},
            "properties": {
                "Job Title":        {"title":     [{"text": {"content": job["title"]}}]},
                "Company":          {"select":    {"name": job["company"]}},
                "Location":         {"rich_text": [{"text": {"content": job.get("location", "")[:2000]}}]},
                "Link":             {"url":       job.get("url") or None},
                "Date Found":       {"date":      {"start": date.today().isoformat()}},
                "Keywords Matched": {"rich_text": [{"text": {"content": job.get("keywords", "")}}]},
                "Status":           {"select":    {"name": "New"}},
            },
        },
        timeout=15,
    )
    if not r.ok:
        print(f"  [Notion ERROR {r.status_code}] {job['title']}: {r.text[:200]}")
    return r.ok

# ======================================================================
# Company scrapers
# ======================================================================

def fetch_greenhouse(slug: str, company_label: str) -> list:
    """Greenhouse ATS public API."""
    jobs = []
    try:
        r = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            headers=HEADERS, timeout=20,
        )
        if r.ok:
            for j in r.json().get("jobs", []):
                jobs.append({
                    "title":     j.get("title", ""),
                    "location":  (j.get("location") or {}).get("name", ""),
                    "url":       j.get("absolute_url", ""),
                    "company":   company_label,
                    "posted_at": None,  # Greenhouse has no reliable publish date
                })
        else:
            print(f"[{company_label}] Greenhouse HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[{company_label}] Greenhouse error: {e}")
    return jobs

def fetch_ashby(slug: str, company_label: str) -> list:
    """Ashby ATS public API (no auth required)."""
    jobs = []
    try:
        r = requests.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            headers=HEADERS, timeout=20,
        )
        if r.ok:
            for j in r.json().get("jobs", []):
                loc = j.get("location", "") or j.get("locationName", "")
                secondary = j.get("secondaryLocations") or []
                if secondary:
                    loc += ", " + ", ".join(
                        s.get("location", "") for s in secondary if isinstance(s, dict)
                    )
                jobs.append({
                    "title":     j.get("title", ""),
                    "location":  loc if isinstance(loc, str) else ", ".join(loc),
                    "url":       j.get("jobUrl", "") or f"https://jobs.ashbyhq.com/{slug}/{j.get('id', '')}",
                    "company":   company_label,
                    "posted_at": parse_iso(j.get("publishedAt", "")),
                })
        else:
            print(f"[{company_label}] Ashby HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[{company_label}] Ashby error: {e}")
    return jobs

def fetch_lever(slug: str, company_label: str) -> list:
    """Lever ATS public API. Tries the US host, then the EU host
    (EU-based companies like Mistral are served from api.eu.lever.co,
    and the US host can silently return an empty list)."""
    for host in ("api.lever.co", "api.eu.lever.co"):
        jobs = _fetch_lever_host(host, slug, company_label)
        if jobs:
            return jobs
    print(f"[{company_label}] Lever returned 0 jobs on both US and EU hosts")
    return []

def _fetch_lever_host(host: str, slug: str, company_label: str) -> list:
    jobs = []
    try:
        r = requests.get(
            f"https://{host}/v0/postings/{slug}?mode=json",
            headers=HEADERS, timeout=20,
        )
        if r.ok:
            for j in r.json():
                cats = j.get("categories", {})
                loc = cats.get("location", "")
                if isinstance(cats.get("allLocations"), list) and cats["allLocations"]:
                    loc = ", ".join(cats["allLocations"])
                jobs.append({
                    "title":     j.get("text", ""),
                    "location":  loc,
                    "url":       j.get("hostedUrl", ""),
                    "company":   company_label,
                    "posted_at": parse_unix(j.get("createdAt"), millis=True),
                })
        else:
            print(f"[{company_label}] Lever ({host}) HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[{company_label}] Lever ({host}) error: {e}")
    return jobs

def fetch_workday(host: str, tenant: str, board: str, company_label: str) -> list:
    """Generic Workday public JSON API (Adobe, Nvidia, Snap, ...)."""
    jobs = []
    seen = set()
    base = f"https://{host}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"
    try:
        for q in MARKET_QUERIES:
            offset = 0
            while True:
                r = requests.post(
                    base,
                    headers={**HEADERS, "Content-Type": "application/json"},
                    json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": q},
                    timeout=20,
                )
                if not r.ok:
                    print(f"[{company_label}] Workday HTTP {r.status_code} for q={repr(q)}: {r.text[:150]}")
                    break
                data = r.json()
                postings = data.get("jobPostings", [])
                if not postings:
                    break
                for j in postings:
                    title = j.get("title", "")
                    key = title.lower().strip()
                    if not title or key in seen:
                        continue
                    seen.add(key)
                    path = j.get("externalPath", "")
                    url = f"https://{host}.myworkdayjobs.com/en-US/{board}{path}" if path else ""
                    jobs.append({
                        "title":     title,
                        "location":  j.get("locationsText", ""),
                        "url":       url,
                        "company":   company_label,
                        "posted_at": parse_workday_date(j.get("postedOn", "")),
                    })
                if len(postings) < 20 or offset > 400:
                    break
                offset += 20
    except Exception as e:
        print(f"[{company_label}] error: {e}")
    return jobs

def fetch_netflix() -> list:
    """Netflix via Eightfold public JSON API."""
    jobs = []
    seen = set()
    base = "https://explore.jobs.netflix.net/api/apply/v2/jobs"
    try:
        for q in MARKET_QUERIES:
            start = 0
            while start < 200:
                r = requests.get(
                    base,
                    params={"domain": "netflix.com", "query": q, "num": 20, "start": start},
                    headers=HEADERS, timeout=20,
                )
                if not r.ok:
                    print(f"[Netflix] HTTP {r.status_code} for q={repr(q)}")
                    break
                data = r.json()
                positions = data.get("positions", [])
                if not positions:
                    break
                for p in positions:
                    title = p.get("name", "")
                    key = title.lower().strip()
                    if not title or key in seen:
                        continue
                    seen.add(key)
                    locs = p.get("locations") or []
                    location = "; ".join(locs) if locs else (p.get("location", "") or "")
                    posted = p.get("t_create") or p.get("postedDate")
                    jobs.append({
                        "title":     title,
                        "location":  location,
                        "url":       p.get("canonicalPositionUrl", "")
                                     or f"https://explore.jobs.netflix.net/careers/job/{p.get('id','')}",
                        "company":   "Netflix",
                        "posted_at": parse_unix(posted, millis=False) if posted else None,
                    })
                start += 20
    except Exception as e:
        print(f"[Netflix] error: {e}")
    return jobs

def fetch_workable(slug: str, company_label: str) -> list:
    """Workable ATS public widget API (Hugging Face, ...)."""
    jobs = []
    try:
        r = requests.get(
            f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=false",
            headers=HEADERS, timeout=20,
        )
        if r.ok:
            for j in r.json().get("jobs", []):
                loc = ", ".join(filter(None, [j.get("city", ""), j.get("state", ""), j.get("country", "")]))
                if j.get("telecommuting"):
                    loc = (loc + " (Remote)").strip()
                jobs.append({
                    "title":     j.get("title", ""),
                    "location":  loc,
                    "url":       j.get("url", "") or f"https://apply.workable.com/{slug}/j/{j.get('shortcode', '')}/",
                    "company":   company_label,
                    "posted_at": parse_iso(j.get("published_on", "")),
                })
        else:
            print(f"[{company_label}] Workable HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[{company_label}] Workable error: {e}")
    return jobs

def fetch_recruitee(slug: str, company_label: str) -> list:
    """Recruitee ATS public offers API (Framestore, ...)."""
    jobs = []
    try:
        r = requests.get(
            f"https://{slug}.recruitee.com/api/offers/",
            headers=HEADERS, timeout=20,
        )
        if r.ok:
            for o in r.json().get("offers", []):
                loc = ", ".join(filter(None, [o.get("city", ""), o.get("country", "")]))
                created = (o.get("created_at", "") or "").replace(" UTC", "").replace(" ", "T")
                jobs.append({
                    "title":     o.get("title", ""),
                    "location":  loc,
                    "url":       o.get("careers_url", "")
                                 or f"https://{slug}.recruitee.com/o/{o.get('slug', '')}",
                    "company":   company_label,
                    "posted_at": parse_iso(created),
                })
        else:
            print(f"[{company_label}] Recruitee HTTP {r.status_code}")
    except Exception as e:
        print(f"[{company_label}] Recruitee error: {e}")
    return jobs

def fetch_koto() -> list:
    """Koto via Teamtailor careers site (server-rendered HTML).
    Titles reconstructed from URL slugs; locations unknown (unknown passes)."""
    jobs = []
    seen = set()
    try:
        for page in (1, 2, 3, 4):
            url = ("https://careers.koto.studio/jobs" if page == 1
                   else f"https://careers.koto.studio/jobs/show_more?page={page}")
            r = requests.get(url, headers=HEADERS, timeout=20)
            if not r.ok:
                break
            found_new = False
            for m in re.finditer(r'/jobs/(\d+)-([a-z0-9-]+)', r.text):
                jid, slug = m.groups()
                if jid in seen:
                    continue
                seen.add(jid)
                found_new = True
                title = slug.replace("-", " ").title()
                jobs.append({
                    "title":     title,
                    "location":  "",
                    "url":       f"https://careers.koto.studio/jobs/{jid}-{slug}",
                    "company":   "Koto",
                    "posted_at": None,
                })
            if not found_new:
                break
    except Exception as e:
        print(f"[Koto] error: {e}")
    return jobs

def fetch_cleo() -> list:
    """Cleo via RevolutPeople careers page (best-effort HTML parse).
    Job titles are reconstructed from URL slugs; locations are unknown
    (unknown locations pass the filter)."""
    jobs = []
    seen = set()
    try:
        r = requests.get(
            "https://revolutpeople.com/cleo/public/careers",
            headers=HEADERS, timeout=20,
        )
        if not r.ok:
            print(f"[Cleo] HTTP {r.status_code}")
            return jobs
        for slug in re.findall(r'/cleo/public/careers/position/([a-z0-9-]+)', r.text):
            # slug looks like "head-of-fraud-02671591-342f-4910-..." — strip the uuid
            words = [w for w in slug.split("-") if not re.fullmatch(r"[0-9a-f]{4,}", w)]
            title = " ".join(w.capitalize() for w in words)
            key = title.lower()
            if not title or key in seen:
                continue
            seen.add(key)
            jobs.append({
                "title":     title,
                "location":  "",
                "url":       f"https://revolutpeople.com/cleo/public/careers/position/{slug}",
                "company":   "Cleo",
                "posted_at": None,
            })
    except Exception as e:
        print(f"[Cleo] error: {e}")
    return jobs

def fetch_github_careers() -> list:
    """GitHub careers site (Radancy) public JSON API."""
    jobs = []
    seen = set()
    try:
        for pg in range(1, 15):
            r = requests.get(
                "https://www.github.careers/api/jobs",
                params={"page": pg},
                headers=HEADERS, timeout=20,
            )
            if not r.ok:
                print(f"[GitHub] HTTP {r.status_code} page {pg}")
                break
            postings = r.json().get("jobs", [])
            if not postings:
                break
            for item in postings:
                j = item.get("data", item) or {}
                title = j.get("title", "")
                key = title.lower().strip() + str(j.get("req_id", ""))
                if not title or key in seen:
                    continue
                seen.add(key)
                loc = j.get("full_location", "") or j.get("location_name", "")
                jobs.append({
                    "title":     title,
                    "location":  loc,
                    "url":       j.get("apply_url", "") or f"https://www.github.careers/careers-home/jobs/{j.get('req_id','')}",
                    "company":   "GitHub",
                    "posted_at": parse_iso(j.get("posted_date", "")),
                })
    except Exception as e:
        print(f"[GitHub] error: {e}")
    return jobs

def fetch_spotify() -> list:
    """Spotify via lifeatspotify.com public JSON API."""
    jobs = []
    seen = set()
    try:
        for q in ["creative", "brand"]:
            r = requests.get(
                f"https://api.lifeatspotify.com/wp-json/animal/v1/job/search",
                params={"q": q},
                headers=HEADERS, timeout=20,
            )
            if not r.ok:
                print(f"[Spotify] HTTP {r.status_code} for q={repr(q)}")
                continue
            for j in r.json().get("result", []):
                title = j.get("text", "")
                key = title.lower().strip()
                if not title or key in seen:
                    continue
                seen.add(key)
                locs = j.get("locations") or []
                location = ", ".join(
                    l.get("location", "") if isinstance(l, dict) else str(l) for l in locs
                )
                jobs.append({
                    "title":     title,
                    "location":  location,
                    "url":       f"https://www.lifeatspotify.com/jobs/{j.get('id','')}",
                    "company":   "Spotify",
                    "posted_at": None,
                })
    except Exception as e:
        print(f"[Spotify] error: {e}")
    return jobs

def fetch_canva() -> list:
    """Canva via SmartRecruiters public API."""
    jobs = []
    try:
        offset = 0
        while offset < 500:
            r = requests.get(
                "https://api.smartrecruiters.com/v1/companies/canva/postings",
                params={"limit": 100, "offset": offset},
                headers=HEADERS, timeout=20,
            )
            if not r.ok:
                print(f"[Canva] HTTP {r.status_code}")
                break
            data = r.json()
            content = data.get("content", [])
            if not content:
                break
            for j in content:
                loc = j.get("location") or {}
                city = loc.get("city", "")
                country = loc.get("country", "")
                remote = " (Remote)" if loc.get("remote") else ""
                jobs.append({
                    "title":     j.get("name", ""),
                    "location":  ", ".join(filter(None, [city, country])) + remote,
                    "url":       f"https://jobs.smartrecruiters.com/Canva/{j.get('id','')}",
                    "company":   "Canva",
                    "posted_at": parse_iso(j.get("releasedDate", "")),
                })
            offset += 100
            if offset >= data.get("totalFound", 0):
                break
    except Exception as e:
        print(f"[Canva] error: {e}")
    return jobs

def _apple_row(j: dict, seen: set) -> dict | None:
    """Normalize one Apple search result across their API/page variants."""
    title = (j.get("postingTitle") or j.get("title") or "").strip()
    key = title.lower()
    if not title or key in seen:
        return None
    seen.add(key)
    locs = j.get("locations") or []
    location = "; ".join(
        l.get("name", "") for l in locs if isinstance(l, dict) and l.get("name"))
    slug = j.get("positionId") or j.get("id") or j.get("reqId") or ""
    transformed = j.get("transformedPostingTitle") or ""
    url = (f"https://jobs.apple.com/en-us/details/{slug}/{transformed}"
           if slug else "")
    return {
        "title":     title,
        "location":  location,
        "url":       url,
        "company":   "Apple",
        "posted_at": parse_iso(j.get("postDateInGMT") or j.get("postingDate") or ""),
    }


def _apple_hydration(html: str) -> dict | None:
    """Extract the server-rendered React Router state from a search page.

    The page embeds everything the results list needs as
    window.__staticRouterHydrationData = JSON.parse("<escaped json>") —
    a JS string literal, so it decodes in two steps: once as a JSON string,
    then as JSON. Verified live against jobs.apple.com in a cookie-less
    fetch, which is exactly what this scraper is."""
    m = re.search(
        r'__staticRouterHydrationData\s*=\s*JSON\.parse\("((?:[^"\\]|\\.)*)"\)',
        html)
    if not m:
        return None
    try:
        return json.loads(json.loads('"' + m.group(1) + '"'))
    except Exception:
        return None


def fetch_apple() -> list:
    """Apple jobs.

    Two facts about Apple's search, learned the hard way and verified live:
      1. There is no background search API to call — results are
         server-rendered into the page as a hydration blob.
      2. An UNQUOTED query is near-useless: 'creative director' loose-matches
         ~1,500 postings (retail 'Creative', any 'Director') and relevance
         buries the real ones. A QUOTED phrase matches against full job
         descriptions and returns a tight, complete set — 14 roles, including
         the Creative Director postings the unquoted search never surfaced.

    So: GET the search page per quoted phrase, parse the hydration blob,
    normalize. The old page-scrape stays only as a last-resort fallback and
    says out loud that it is unfiltered.
    Check manually if broken:
    https://jobs.apple.com/en-us/search?search=%22creative%20director%22"""
    jobs = []
    seen = set()
    queries = ['"creative director"', '"brand marketing"']

    # ---- primary: quoted-phrase search, hydration blob ----
    try:
        s = requests.Session()
        s.headers.update(HEADERS)
        for q in queries:
            got_q = 0
            for page in (1, 2, 3):
                params = {"search": q}
                if page > 1:
                    params["page"] = str(page)
                r = s.get("https://jobs.apple.com/en-us/search",
                          params=params, timeout=20)
                if not r.ok:
                    print(f"[Apple] HTTP {r.status_code} for q={q!r} page {page}")
                    break
                data = _apple_hydration(r.text)
                search = (data or {}).get("loaderData", {}).get("search", {})
                results = search.get("searchResults") or []
                if not results:
                    break
                for item in results:
                    row = _apple_row(item, seen)
                    if row:
                        jobs.append(row)
                        got_q += 1
                total = search.get("totalRecords") or 0
                if page * 20 >= min(int(total), 60):
                    break
            print(f"[Apple] search q={q!r}: {got_q} role(s)")
        if jobs:
            return jobs
        print("[Apple] hydration search returned nothing — "
              "falling back to page parsing")
    except Exception as e:
        print(f"[Apple] search error: {e} — falling back to page parsing")

    # ---- fallbacks: the old server-rendered page (now UNFILTERED newest-20;
    # better than blindness, but logged for what it is) ----
    try:
        for q in queries:
            r = requests.get(
                "https://jobs.apple.com/en-us/search",
                params={"search": q, "sort": "newest"},
                headers=HEADERS, timeout=20,
            )
            if not r.ok:
                print(f"[Apple] HTTP {r.status_code} for q={q!r}")
                continue
            html = r.text
            m = re.search(r'window\.APP_STATE\s*=\s*(\{.*?\});\s*</script>', html, re.S)
            if m:
                try:
                    state = json.loads(m.group(1))
                    for item in state.get("searchResults", []):
                        row = _apple_row(item, seen)
                        if row:
                            jobs.append(row)
                    continue
                except Exception:
                    pass
            junk = {"see full role description", "where we're hiring",
                    "where we&#x27;re hiring", "learn more", "apply", "share"}
            for slug, title in re.findall(
                r'href="(/en-us/details/[^"]+)"[^>]*>([^<]{4,120})</a>', html
            ):
                title = title.strip()
                key = title.lower()
                if not title or key in seen or key in junk:
                    continue
                seen.add(key)
                jobs.append({
                    "title":     title,
                    "location":  "",
                    "url":       f"https://jobs.apple.com{slug}",
                    "company":   "Apple",
                    "posted_at": None,
                })
        if jobs:
            print(f"[Apple] page fallback: {len(jobs)} role(s) — NOTE: this "
                  "path is unfiltered newest postings, not a query match")
    except Exception as e:
        print(f"[Apple] error: {e}")
    return jobs

# ======================================================================
# Scraper registry: (label, function, *args)
#
# NOT scrapeable without a headless browser / paid service — check manually:
#   - Meta      -> https://www.metacareers.com/jobs  (GraphQL, login-gated)
#   - Google    -> https://www.google.com/about/careers/applications/jobs/results/?q=%22creative%20director%22
#     (Google's public careers API was shut down; DeepMind below covers its AI arm)
#   - Microsoft -> https://jobs.careers.microsoft.com/global/en/search?q=creative%20director
#     (their search API was shut down mid-2026 with a broken TLS cert; revisit later)
#   - Midjourney -> https://www.midjourney.com  (no public job board; hires quietly)
#   - Notion    -> https://www.notion.com/careers  (board not on standard ATS APIs)
# ======================================================================

SCRAPERS = [
    # AI-native
    ("Anthropic",   fetch_greenhouse, "anthropic",   "Anthropic"),
    ("OpenAI",      fetch_ashby,      "openai",      "OpenAI"),
    ("DeepMind",    fetch_greenhouse, "deepmind",    "DeepMind"),
    ("Mistral",     fetch_ashby,      "mistral.ai",  "Mistral"),
    ("Perplexity",  fetch_ashby,      "perplexity",  "Perplexity"),
    ("xAI",         fetch_greenhouse, "xai",         "xAI"),
    ("ElevenLabs",  fetch_ashby,      "elevenlabs",  "ElevenLabs"),
    ("Cohere",      fetch_ashby,      "cohere",      "Cohere"),
    ("Scale AI",    fetch_greenhouse, "scaleai",     "Scale AI"),
    ("Runway",      fetch_ashby,      "runway-ml",   "Runway"),
    # Big tech
    ("Netflix",     fetch_netflix),
    ("Nvidia",      fetch_workday,    "nvidia.wd5",   "nvidia",   "NVIDIAExternalCareerSite", "Nvidia"),
    ("Apple",       fetch_apple),
    # Design-forward tech
    ("Figma",       fetch_greenhouse, "figma",       "Figma"),
    ("Airbnb",      fetch_greenhouse, "airbnb",      "Airbnb"),
    ("Spotify",     fetch_spotify),
    ("Snap",        fetch_workday,    "snapchat.wd1", "snapchat", "snap",                 "Snap"),
    ("Canva",       fetch_canva),
    ("Adobe",       fetch_workday,    "adobe.wd5",   "adobe",  "external_experienced",    "Adobe"),
    # Applied-to set
    ("GitHub",      fetch_github_careers),
    ("Cleo",        fetch_cleo),
    # Design-led craft companies
    ("Stripe",      fetch_greenhouse, "stripe",      "Stripe"),
    ("Duolingo",    fetch_greenhouse, "duolingo",    "Duolingo"),
    ("Squarespace", fetch_greenhouse, "squarespace", "Squarespace"),
    ("Pinterest",   fetch_greenhouse, "pinterest",   "Pinterest"),
    ("Discord",     fetch_greenhouse, "discord",     "Discord"),
    ("Webflow",     fetch_greenhouse, "webflow",     "Webflow"),
    ("Synthesia",   fetch_ashby,      "synthesia",   "Synthesia"),
    ("Suno",        fetch_ashby,      "suno",        "Suno"),
    # AI-native, wave 2 (enterprise + dev tools with brand-building briefs)
    ("Harvey",      fetch_ashby,      "harvey",      "Harvey"),
    ("Sierra",      fetch_ashby,      "sierra",      "Sierra"),
    ("Decagon",     fetch_ashby,      "decagon",     "Decagon"),
    ("Cursor",      fetch_ashby,      "cursor",      "Cursor"),
    ("Cognition",   fetch_ashby,      "cognition",   "Cognition"),
    ("Hugging Face", fetch_workable,  "huggingface", "Hugging Face"),
    # Agencies, design studios & craft shops (added Aug 2026; Monks removed by request)
    ("Preacher",          fetch_greenhouse, "preacher",         "Preacher"),
    ("Johannes Leonardo", fetch_greenhouse, "johannesleonardo", "Johannes Leonardo"),
    ("Wolff Olins",       fetch_workable,   "wolff-olins",      "Wolff Olins"),
    ("DesignStudio",      fetch_workable,   "designstudio",     "DesignStudio"),
    ("Koto",              fetch_koto),
    ("Framestore",        fetch_recruitee,  "framestore",       "Framestore"),
]

# Workday host format: "{tenant}.{datacenter}" — e.g. adobe.wd5, nvidia.wd5,
# snapchat.wd1. fetch_workday builds "https://{host}.myworkdayjobs.com/...".

# ======================================================================
# Test mode
# ======================================================================

def run_test():
    print(f"\n{'='*60}")
    print(f"JOB ALERTS - TEST MODE - {date.today()}")
    print(f"{'='*60}")
    agent = load_agent_config("001")
    print(f"Filters: keywords={len(agent.include)}, locations={len(agent.accepted_locations)}\n")

    grand_total = 0
    grand_matches = 0
    for entry in SCRAPERS:
        label = entry[0]
        fn    = entry[1]
        args  = entry[2:]

        print(f"--- {label} ---")
        try:
            jobs = fn(*args)
        except Exception as e:
            print(f"  CRASHED: {e}\n")
            continue

        title_pass  = [j for j in jobs if passes_title(agent, j["title"])]
        loc_pass    = [j for j in title_pass if passes_location(agent, j["location"])]
        with_date   = [j for j in jobs if j.get("posted_at") is not None]
        recent      = [j for j in loc_pass if is_recent(j.get("posted_at"))]
        grand_total += len(jobs)
        grand_matches += len(loc_pass)

        print(f"  Fetched: {len(jobs)} | Title match: {len(title_pass)} | Location match: {len(loc_pass)} | Recent (<24h): {len(recent)}")
        print(f"  Date coverage: {len(with_date)}/{len(jobs)} jobs have a posting date")

        if loc_pass:
            print(f"  MATCHES:")
            for j in loc_pass[:10]:
                age = format_age(j.get("posted_at"))
                print(f"    [{j.get('location','no-loc')[:60]}] {j['title']}" + (f" ({age})" if age else ""))
        elif jobs:
            print(f"  Sample raw titles (first 3):")
            for j in jobs[:3]:
                print(f"    [{j.get('location','no-loc')[:60]}] {j['title']}")
        else:
            print(f"  WARNING: 0 jobs returned - scraper likely broken or slug wrong")

        if title_pass and not loc_pass:
            print(f"  NOTE: titles matched but all filtered out by location. Sample locations:")
            for j in title_pass[:3]:
                print(f"    '{j.get('location', '')}'")

        print()

    print(f"{'='*60}")
    print(f"Grand total fetched: {grand_total} | Total matches: {grand_matches}")
    print(f"{'='*60}\n")

# ======================================================================
# Email
# ======================================================================

def send_email(agent, new_jobs: list, notion_saved: int = 0):
    today = date.today().strftime("%B %d, %Y")
    subject = f"Job Alerts - {today}"

    def format_job(j):
        age = format_age(j.get("posted_at"))
        lines = [f"* {j['title']} - {j['company']} | {j.get('location', '-')}" + (f" | {age}" if age else "")]
        lines.append(f"  {j.get('url', '')}\n")
        return "\n".join(lines)

    if new_jobs:
        recent = [j for j in new_jobs if is_recent(j.get("posted_at"))]
        older  = [j for j in new_jobs if not is_recent(j.get("posted_at"))]

        lines = [f"Hi {agent.name},\n\nDaily job alert - {today}"]
        lines.append(f"{len(new_jobs)} new role(s) found (saved to Notion: {notion_saved})\n")

        if recent:
            lines.append(f"🔥 POSTED IN THE LAST 24H ({len(recent)} role(s))\n")
            for j in recent:
                lines.append(format_job(j))

        if older:
            lines.append(f"📋 OTHER NEW ROLES ({len(older)} role(s))\n")
            for j in older:
                lines.append(format_job(j))
    else:
        lines = [
            f"Hi {agent.name},\n\nNo new matching roles found today ({today}).",
            "All companies were checked.\n",
        ]

    lines.append(f"\n---\nRead today's edition -> {agent.edition_url}")
    for footer_line in agent.email_footer:
        lines.append(footer_line)

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = RECIPIENT
    msg.attach(MIMEText("\n".join(lines), "plain"))

    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(GMAIL_USER, GMAIL_PASSWORD)
        s.sendmail(GMAIL_USER, RECIPIENT, msg.as_string())
    print(f"Email sent to {RECIPIENT}")

# ======================================================================
# FIT ENGINE — ranks roles against Carlos's profile using the Claude API
# Requires: ANTHROPIC_API_KEY secret + profile.md in the repo.
# Degrades gracefully: no key / any failure -> heuristic ranking + blurbs.
# ======================================================================

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL  = "claude-sonnet-5"
MAX_JD_CHARS  = 6000
MAX_CANDIDATES_TO_SCORE = 25

def fetch_jd_text(url: str) -> str:
    """Best-effort: fetch a job posting page and strip it to readable text."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if not r.ok:
            return ""
        t = r.text
        t = re.sub(r"<script.*?</script>", " ", t, flags=re.S | re.I)
        t = re.sub(r"<style.*?</style>", " ", t, flags=re.S | re.I)
        t = re.sub(r"<[^>]+>", " ", t)
        t = _html.unescape(t)
        t = re.sub(r"\s+", " ", t).strip()
        return t[:MAX_JD_CHARS]
    except Exception:
        return ""

def load_profile(agent) -> str:
    try:
        with open(agent.profile_path) as f:
            return f.read()
    except Exception:
        return ""

def _cut(text: str, limit: int) -> str:
    """Truncate at a word boundary — never mid-word."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:—–-")
    return cut + "…"

def fit_tier(score) -> str:
    """Editorial label for a fit score."""
    if score is None:
        return ""
    if score >= 85:
        return "Exceptional fit"
    if score >= 75:
        return "Strong fit"
    if score >= 60:
        return "Worth considering"
    return "Wildcard"

class JudgeVoice:
    """How the three prompts name the person (Move 2 — Person from Memory).

    The originals were written for №001 and carried "one senior creative
    director" and he/him/his as literals. Those literals are now №001's
    AgentConfig values, so his prompts are byte-identical; any other client
    is "one client", referred to in the neutral third person, and the WHY
    guidance's pattern clause comes from the config or falls back to a
    person-neutral reading of the same idea."""

    def __init__(self, agent=None):
        persona = (getattr(agent, "persona", "") or "").strip() if agent is not None else "senior creative director"
        pr = tuple(getattr(agent, "pronouns", ()) or ()) if agent is not None else ("he", "him", "his")
        if len(pr) != 3:
            pr = ("they", "them", "their")
        self.one = f"one {persona}" if persona else "one client"
        self.subj, self.obj, self.poss = pr
        self.Subj = self.subj[:1].upper() + self.subj[1:]
        self.OBJ = self.obj.upper()
        self.rubric = ("seniority match, craft match, brand-led scope, AI-era relevance"
                       if persona == "senior creative director"
                       else "seniority match, craft match, scope match, and the direction the candidate has authorized")
        lenses = (getattr(agent, "judgment_lenses", "") or "").strip() if agent is not None else ""
        if agent is None:
            lenses = ("the companies he has built for, brands he entered before their identity "
                      "was fixed, cities he calls home, teams he built from zero")
        self.lenses = lenses or (f"the organisations {self.subj} {'has' if self.subj != 'they' else 'have'} built for, "
                                 f"the moments {self.subj} {'enters' if self.subj != 'they' else 'enter'} before they are settled, "
                                 f"the places {self.subj} {'calls' if self.subj != 'they' else 'call'} home, "
                                 f"what {self.subj} {'has' if self.subj != 'they' else 'have'} built")
        # grammatical helpers for the neutral case
        self.has = "has" if self.subj != "they" else "have"
        self.a_persona = f"a {persona}" if persona else ""


def score_fit(agent, profile: str, job: dict, jd_text: str):
    """Ask Claude to argue one role for this candidate: score + the case for,
    and the honest case against. Returns (score, why, pause) or (None, None, None)."""
    try:
        v = JudgeVoice(agent)
        prompt = (
            f"You are the personal career agent of {v.one}. Write plainly. Never use em dashes or long dashes anywhere; use commas, colons, or periods instead. "
            f"You are judging ONE role for {v.OBJ} specifically, and you will present "
            f"your reasoning to {v.obj} directly.\n\n"
            f"CANDIDATE PROFILE:\n{profile}\n\n"
            f"ROLE: {job['title']} at {job['company']} — {job.get('location','')}\n\n"
            + (f"NOTE: {v.Subj} {v.has} flagged {job['company']} as a priority target — {v.subj} asked {v.poss} agent to watch this company closely. "
               f"Weigh {v.poss} stated affinity as real fit-relevant information, but stay honest about the role itself.\n\n"
               if job.get("company") in agent.priority_companies else "")
            + f"JOB POSTING TEXT (may include page boilerplate — ignore navigation/footer noise):\n"
            f"{jd_text or '(no description available — judge from title and company)'}\n\n"
            "Return ONLY a JSON object, no other text:\n"
            f'{{"score": <0-100 integer — {v.rubric} for THIS candidate>, '
            f'"why": "<WHY I CHOSE IT: one or two sentences, max 200 chars, spoken to {v.obj} as you/your (never {v.poss} name, never {v.subj}/{v.poss}). '
            f"Connect THIS role to {v.poss} specific pattern — {v.lenses}. Perceptive and confident, not flattering. No emoji, no exclamation points.>\", "
            '"pause": "<WHAT GIVES ME PAUSE: one sentence, max 140 chars, the honest counterargument — remit too narrow, seniority ambiguity, '
            f"scope skewed to execution, thin posting, location friction. Every role has one; if genuinely nothing, name what {v.subj} should verify first. Same voice.>\"}}"
        )
        for attempt in (1, 2):
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": CLAUDE_MODEL,
                    "max_tokens": 600,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=60,
            )
            if not r.ok:
                print(f"  [Fit API {r.status_code}] {r.text[:150]}")
                return None, None, None
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            m = re.search(r'\{.*"score".*\}', text, re.S)
            if m:
                try:
                    data = json.loads(m.group(0))
                    score = max(0, min(100, int(data["score"])))
                    why = _cut(str(data.get("why", "")), 220)
                    pause = _cut(str(data.get("pause", "")), 160)
                    if why:
                        return score, why, pause
                except Exception:
                    pass
            print(f"  [Fit attempt {attempt}] unusable reply: {text[:100]!r}")
        return None, None, None
    except Exception as e:
        print(f"  [Fit error] {job['title']}: {e}")
        return None, None, None

def rank_with_fit(agent, matches: list, new_keys: set, second_look: set | None = None,
                  read_budget: int | None = None, key_fn=None):
    """Return (ranked_matches, used_ai). Each match may gain 'fit' and 'ai_line'.

    second_look: role_keys the person explicitly asked FOOUND to re-judge
    (the RECONSIDER verb). These are always read in full, exactly like
    priority companies — the person's push-back outranks the heuristic cut.

    read_budget: how many heuristic-ranked matches get a full read. Defaults
    to MAX_CANDIDATES_TO_SCORE — the public Shortlist never passes it, so its
    behaviour is unchanged. The private hunt passes its own budget.
    key_fn: identity used to test membership in new_keys / second_look.
    Defaults to the Shortlist's title|company dedup_key. The private hunt
    passes role_key. Both keywords are Move 1 additions (contract §2 stage 8)."""
    if read_budget is None:
        read_budget = MAX_CANDIDATES_TO_SCORE
    if key_fn is None:
        key_fn = lambda j: dedup_key(j["title"], j["company"])
    heuristic = lambda j: (
        _seniority_score(j["title"]) + _recency_score(j.get("posted_at"))
        + (2 if key_fn(j) in new_keys else 0)
    )
    profile = load_profile(agent)
    if not ANTHROPIC_KEY or not profile:
        print("Fit engine: no API key or profile — using heuristic ranking.")
        return sorted(matches, key=heuristic, reverse=True), False

    candidates = sorted(matches, key=heuristic, reverse=True)[:read_budget]
    for j in matches:
        if j["company"] in agent.priority_companies and j not in candidates:
            candidates.append(j)   # priority watch: always read in full
    for j in matches:
        if (second_look
                and key_fn(j) in second_look
                and j not in candidates):
            candidates.append(j)   # the person asked: always read in full
            print(f"  [second look] forced full read: {j['company']} — {j['title']}")
    scored_any = False
    for j in candidates:
        jd = fetch_jd_text(j.get("url", ""))
        score, why, pause = score_fit(agent, profile, j, jd)
        if score is not None:
            j["fit"] = score
            j["ai_why"] = why
            j["ai_pause"] = pause
            j["ai_line"] = why  # back-compat (email etc.)
            scored_any = True
            print(f"  fit {score:3d} ({fit_tier(score)})  {j['company']} — {j['title']}")
    if not scored_any:
        print("Fit engine: all scoring failed — falling back to heuristic.")
        return sorted(matches, key=heuristic, reverse=True), False
    return sorted(candidates, key=lambda j: (j.get("fit", -1), heuristic(j)), reverse=True), True

DEEP_LOOK_MAX_TOKENS = 4000   # was 1,400: a five-search turn needs room to finish its JSON
DEEP_LOOK_MAX_TURNS = 3       # continuations after a server-side pause_turn


def _deep_look_json(blocks):
    """The deep look's JSON object, wherever the reply put it.

    Joins every text block in order and returns the last brace-balanced
    object that names a verdict and parses as JSON. None if there is none
    (truncated reply, no JSON, or prose only)."""
    texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
    joined = "\n".join(t for t in texts if t)
    found = None
    for m in re.finditer(r"\{", joined):
        depth = 0
        for i in range(m.start(), len(joined)):
            ch = joined[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    span = joined[m.start():i + 1]
                    if '"verdict"' in span:
                        try:
                            cand = json.loads(span)
                            if isinstance(cand, dict):
                                found = cand
                        except Exception:
                            pass
                    break
    return found


def deep_look(job, profile: str, agent=None):
    """Second pass on the day's lead: investigate with web search.
    The research is allowed to lower the score. Returns dict or None; never raises.
    `agent` (Move 2) sets the voice; None keeps №001's original wording."""
    if not ANTHROPIC_KEY:
        return None
    try:
        v = JudgeVoice(agent)
        prompt = (
            f"You are FOOUND, the personal career agent of {v.one}. Write plainly. Never use em dashes or long dashes anywhere; use commas, colons, or periods instead. "
            "Today your lead recommendation is:\n"
            f"ROLE: {job['title']} at {job['company']} — {job.get('location','')}\n"
            f"URL: {job.get('url','')}\n"
            f"Your current fit score: {job.get('fit')}/100. Your reasoning so far: {job.get('ai_why','')}\n"
            f"Your stated concern: {job.get('ai_pause','')}\n\n"
            f"CANDIDATE PROFILE (judge against {v.OBJ}):\n{profile[:5000]}\n\n"
            "Use web search to investigate: is this role new or a succession? What is the company's "
            "brand/creative moment right now? Who would this likely report to? What recent hiring or "
            "investment signals exist? What is the biggest unresolved risk?\n\n"
            "Then return STRICT JSON only — one object, no prose before or after:\n"
            '{"role": "finding, max 130 chars", "moment": "finding, max 130 chars", '
            '"leadership": "finding, max 130 chars", "signal": "finding, max 130 chars", '
            '"question": "the unresolved risk, max 130 chars", '
            '"fit_after": 0, "verdict": "max 55 chars, e.g. My view changed: 82 to 86. or Still 82. The risk is real."}\n'
            "fit_after is your revised integer score after research — it MAY be lower than the current score. "
            "Be honest; the research earns nothing if it can only agree."
        )
        # Move 1 v1.4: the same call, made reliable. The reply of a web-search
        # turn is several text blocks around tool blocks, the JSON may sit in
        # any of them, may contain a nested brace, and a long research turn
        # may exhaust the budget or pause; the original parser read only the
        # last block with a flat-brace regex under a 1,400-token cap and lost
        # about half of all deep looks silently.
        headers = {
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        messages = [{"role": "user", "content": prompt}]
        blocks = []
        stop_reason = ""
        for _turn in range(DEEP_LOOK_MAX_TURNS):
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json={
                    "model": CLAUDE_MODEL,
                    "max_tokens": DEEP_LOOK_MAX_TOKENS,
                    "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
                    "messages": messages,
                },
                timeout=240,
            )
            if not r.ok:
                print(f"[deep look skipped: HTTP {r.status_code} {r.text[:200]}]")
                return None
            body = r.json()
            turn_blocks = body.get("content", []) or []
            blocks.extend(turn_blocks)
            stop_reason = str(body.get("stop_reason") or "")
            if stop_reason != "pause_turn":
                break
            # The server paused a long tool-use turn: hand its content back and let it finish.
            messages = messages + [{"role": "assistant", "content": turn_blocks}]
        d = _deep_look_json(blocks)
        if d is None:
            n_text = sum(1 for b in blocks if b.get("type") == "text")
            print(f"[deep look skipped: no JSON in reply; stop_reason={stop_reason or 'none'} text_blocks={n_text}]")
            return None
        out = {}
        for k in ("role", "moment", "leadership", "signal", "question"):
            v = str(d.get(k, "")).strip()
            if v:
                out[k] = _cut(v, 150)
        verdict = str(d.get("verdict", "")).strip()
        if verdict:
            out["verdict"] = _cut(verdict, 70)
        try:
            fa = int(d.get("fit_after"))
            if 0 <= fa <= 100:
                out["fit_after"] = fa
        except Exception:
            pass
        return out if out.get("verdict") and len(out) >= 4 else None
    except Exception as e:
        print(f"[deep look skipped: {e}]")
        return None

def write_brief(n: int, total_fetched: int, n_companies: int, ranked: list, new_keys: set,
                agent=None, as_of: str | None = None):
    """Ask Claude to write the one-line morning report from the run's real data.
    Returns the line, or None (caller falls back to the fixed template).
    `agent` sets the voice and `as_of` the clock named in the run data (Move 2);
    both default to №001's original wording."""
    if not ANTHROPIC_KEY or n == 0:
        return None
    try:
        v = JudgeVoice(agent)
        from datetime import datetime as _dt, timezone as _tz
        now = _dt.now(_tz.utc)
        facts = []
        for i, j in enumerate(ranked, 1):
            is_new = dedup_key(j["title"], j["company"]) in new_keys
            age = ""
            pa = j.get("posted_at")
            if pa is not None:
                if pa.tzinfo is None:
                    pa = pa.replace(tzinfo=_tz.utc)
                age = f", posted {(now - pa).days}d ago"
            facts.append(
                f'{i}. {j["company"]} — {j["title"]} '
                f'(fit {j.get("fit", "?")}, {j.get("location", "?")}{age}'
                f'{", NEW TODAY" if is_new else ""})'
            )
        n_new = sum(1 for j in ranked if dedup_key(j["title"], j["company"]) in new_keys)
        prompt = (
            "Write plainly. Never use em dashes or long dashes anywhere; use commas, colons, or periods instead. You are the personal career agent behind THE SHORTLIST. You just finished this "
            f"morning's run for your one client{', ' + v.a_persona if v.a_persona else ''}. The page already "
            "reports the numbers — your job is ONE short observation, if the data earns it.\n\n"
            "RUN DATA (real, this morning):\n"
            f"- Scanned {total_fetched:,} openings at {n_companies} companies, {as_of or '8:00 AM ET'}\n"
            f"- {n} made the cut, {n_new} newly listed today\n"
            f"- The ranked list:\n" + "\n".join(facts) + "\n\n"
            "RULES:\n"
            "- ONE sentence, max 120 characters: the single most notable fact — a lead clear of "
            "the field, a cluster from one company, a role that appeared overnight, a strong role "
            "open so long it may not last.\n"
            "- Dry, specific, first person allowed. No hype, no emoji, no exclamation points.\n"
            '- If nothing genuinely stands out, return {"line": ""}.\n\n'
            'Return ONLY JSON: {"line": "<the observation or empty>"}'
        )
        def _extract(text):
            m = re.search(r'\{[^{}]*"line"[^{}]*\}', text, re.S)
            if m:
                try:
                    return str(json.loads(m.group(0))["line"]).strip()
                except Exception:
                    pass
            t = text.strip().strip("`").strip()
            if t.startswith("{") or '"line"' in t:
                return None   # truncated JSON — never let a fragment reach the page
            if t and "\n" not in t and len(t) < 200:
                return t.strip('"').strip()
            return None

        for attempt in (1, 2):
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": CLAUDE_MODEL,
                    "max_tokens": 500,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=60,
            )
            if not r.ok:
                print(f"  [Brief API {r.status_code}] {r.text[:150]}")
                return None
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            line = _extract(text)
            if line is not None:
                line = _cut(line, 140)
                if line:
                    print(f"Observation: {line}")
                return line or None
            print(f"  [Brief attempt {attempt}] unusable reply: {text[:120]!r}")
        return None
    except Exception as e:
        print(f"  [Brief error] {e}")
        return None

# ======================================================================
# THE SHORTLIST — daily-edition microsite (approved design, Aug 2026)
# ======================================================================

import html as _html
import glob as _glob
import subprocess as _sub

COUNT_WORDS = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven",
               "Eight", "Nine", "Ten", "Eleven"]

# One-line blurbs in the approved editorial voice. v1 is company-level;
# v1.1 replaces these with AI-written role-specific lines.
BLURBS = {
    "Anthropic":    "Shape how the most safety-minded frontier lab speaks to the world.",
    "OpenAI":       "The most-watched brand in technology, still deciding what it looks like.",
    "DeepMind":     "Google's AI vanguard: science-grade substance in need of story.",
    "Mistral":      "Own how Europe's frontier AI lab shows up in the world: brand, campaigns, culture.",
    "Perplexity":   "The challenger answer engine building a brand on speed and candor.",
    "xAI":          "Maximum velocity, maximum attention. A brand built in public.",
    "ElevenLabs":   "One identity stretched across voice, music, and agents: a brand-architecture brief.",
    "Cohere":       "Enterprise AI from Toronto that must feel trustworthy before it feels exciting.",
    "Scale AI":     "The data backbone of the AI boom, largely unbranded territory.",
    "Runway":       "Define the creative voice of the tool redefining filmmaking.",
    "Netflix":      "Shape how the world's biggest entertainment brand publishes culture.",
    "Nvidia":       "The most valuable company on earth, with an enterprise brand to grow into.",
    "Apple":        "The reference point. Craft as religion.",
    "Figma":        "Design's home field: an audience that judges every pixel professionally.",
    "Airbnb":       "Brand-led to its core; creative leadership reports to the very top.",
    "Spotify":      "The brand that turned data into pop culture. Wrapped, but all year.",
    "Snap":         "A global brand that still knows how to play.",
    "Canva":        "Design for 200M people, with a taste for big swings.",
    "Adobe":        "The tools creativity runs on, mid-reinvention for the AI era.",
    "GitHub":       "The home of 100M developers: brand, film, campaigns, and craft in-house.",
    "Cleo":         "Fintech with a voice: an AI that talks money like a friend.",
    "Stripe":       "Lead brand moments for the company that set the bar for craft in tech.",
    "Duolingo":     "The loudest, most awarded brand voice in consumer tech.",
    "Squarespace":  "An in-house agency with Super Bowl reps and design awards to defend.",
    "Pinterest":    "A visual-culture platform where inspiration is the product.",
    "Discord":      "Playful, distinctive brand craft for the internet's living room.",
    "Webflow":      "Design-native product, visual-first audience.",
    "Synthesia":    "The AI-video leader, London-built, making enterprise feel cinematic.",
    "Suno":         "Build the campaign language for AI-made music, a brand still wet on the canvas.",
    "Harvey":       "Make an $11B legal-AI company feel inevitable to the most skeptical audience in business.",
    "Sierra":       "Bret Taylor's $15B bet on conversational AI, polishing an enterprise identity.",
    "Decagon":      "AI agents for customer support. Fast-growing, identity still forming.",
    "Cursor":       "The fastest-growing dev tool in history, whose editor is its brand.",
    "Cognition":    "Maker of Devin. Foundational brand work, wide open.",
    "Hugging Face": "The beloved open-source home of AI, scrappy by design.",
    "Preacher":     "Austin's most decorated creative shop. Brand-led, craft-obsessed, hometown advantage.",
    "Johannes Leonardo": "The agency that made adidas and Volkswagen feel inevitable again.",
    "Wolff Olins":  "The identity house whose rebrands the rest of the industry studies.",
    "DesignStudio": "Where Airbnb's Bélo was born. Rebrands that become case studies.",
    "Koto":         "Joy and rigor for modern tech brands, London to LA.",
    "Framestore":   "Oscar-winning craft: the people who make the impossible photoreal.",
}

def _seniority_score(title: str) -> int:
    t = title.lower()
    if any(k in t for k in ["executive creative", "head of", "vp", "group creative"]):
        return 4
    if "director" in t:
        return 3
    if "lead" in t:
        return 2
    return 1

def _recency_score(posted_at) -> int:
    if posted_at is None:
        return 2  # unknown (e.g. Greenhouse) — assume moderately fresh
    now = datetime.now(timezone.utc)
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    days = (now - posted_at).days
    if days <= 1:
        return 4
    if days <= 7:
        return 3
    if days <= 30:
        return 2
    return 1

def _fmt_posted(posted_at) -> str:
    if posted_at is None:
        return ""
    return posted_at.strftime("%b %d").replace(" 0", " ")

def _et_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now()

SHORTLIST_ENTRY = """    <div class="item__LEAD____OPEN__" data-key="__KEY__" data-title="__DT_TITLE__" data-company="__DT_CO__" data-url="__DT_URL__" data-location="__DT_LOC__" data-fit="__DT_FIT__" data-why="__DT_WHY__" data-pause="__DT_PAUSE__">
      <button class="row" aria-expanded="__EXP__">
        <span class="marker"><span__D2__>__NUM__</span></span><span class="co">__COMPANY__</span>__FRESH__<span class="anno">__ANNO__</span>
      </button>
      <div class="panel"><div class="panel-inner">
        <div class="role">__ROLE____NEWTAG__</div>
__ARGUMENT__        <div class="meta"><b>__LOC__</b><span class="sep">/</span><span>__SALARY__</span>__POSTED__</div>
        <div class="actions">
          <a class="apply" href="__URL__" target="_blank" rel="noopener">Apply &#8599;</a>
          <button class="mark" type="button">Mark applied</button>
        </div>
      </div></div>
    </div>
"""

SHORTLIST_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>FOOUND · __DATELONG__</title>
<script>try{if(location.pathname==="/"&&!localStorage.getItem("foound_seen")&&location.search.indexOf("me")===-1&&location.hash.indexOf("access_token")===-1&&location.hash.indexOf("error")===-1){localStorage.setItem("foound_seen","1");location.replace("/foound/");}}catch(e){}</script>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 32 32%27%3E%3Crect width=%2732%27 height=%2732%27 fill=%27white%27/%3E%3Ccircle cx=%2710%27 cy=%2716%27 r=%276%27 fill=%27black%27/%3E%3Ccircle cx=%2723.5%27 cy=%2716%27 r=%275.4%27 fill=%27none%27 stroke=%27black%27 stroke-width=%271.2%27/%3E%3C/svg%3E">
<meta property="og:title" content="FOOUND">
<meta property="og:description" content="To find what matters. A career agent that works for one person. New edition every weekday.">
<meta property="og:url" content="https://foound.ai/">
<meta property="og:type" content="website">
<meta property="og:image" content="https://foound.ai/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="description" content="To find what matters. A career agent that works for one person. New edition every weekday.">
<script data-goatcounter="https://foound.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
<style>
  :root{--ink:#000;--paper:#fff;--mute:#6b6b6b;}
  *{margin:0;padding:0;box-sizing:border-box;}
  html{-webkit-text-size-adjust:100%;}
  body{background:var(--paper);color:var(--ink);font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased;}
  .plate{padding:4.5vw 5vw 4vw;}
  /* ---- masthead: the object's nameplate + quiet furniture nav ---- */
  .mast{
    display:flex;justify-content:space-between;align-items:baseline;gap:14px;
    padding:22px 5vw 0;
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;
  }
  .mast .id{font-weight:500;white-space:nowrap;color:var(--ink);text-decoration:none;}
  .mast nav{display:flex;gap:24px;flex-wrap:wrap;}
  .mast nav a{color:var(--mute);text-decoration:none;white-space:nowrap;letter-spacing:.12em;}
  .mast nav a:hover{color:var(--ink);}
  .mast nav a.here{color:var(--ink);}
  @media (max-width:640px){.mast{flex-direction:column;gap:9px;}.mast nav{gap:18px;}}
  /* career-evidence links inside the argument */
  a.ev{color:inherit;text-decoration:none;border-bottom:1px dotted #b9b9b9;}
  a.ev:hover{border-bottom:1px solid var(--ink);}
  .brief{
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:12.5px;line-height:1.7;letter-spacing:.01em;
    margin:0 0 4vh 0;
  }
  /* ---- the overnight briefing: the agent's telegram, one quiet voice ---- */
  .cascade{
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:12.5px;line-height:1.7;letter-spacing:.01em;
    margin:0 0 2.5vh 0;
  }
  .statline{
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:12.5px;line-height:1.7;letter-spacing:.01em;color:var(--mute);
    margin:0;max-width:58em;
  }
  /* ---- section labels (I'd start with… / unusually strong / worth your attention) ---- */
  .seclabel{
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:11px;letter-spacing:.14em;text-transform:uppercase;
    margin:9vh 0 2vh;
    display:flex;align-items:center;gap:10px;
  }
  .seclabel::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--ink);}
  .seclabel.pass{margin-top:13vh;}
  .item{border:none;}
  .row{
    display:flex;align-items:center;gap:.38em;width:100%;
    background:none;border:none;cursor:pointer;text-align:left;
    color:var(--ink);font-family:inherit;
    font-size:clamp(40px,8.6vw,124px);
    font-weight:500;letter-spacing:-.035em;line-height:1.08;
    padding:.03em 0;white-space:nowrap;
  }
  .row:focus{outline:none;}
  .row:focus-visible{outline:2px solid var(--ink);outline-offset:6px;}
  .marker{
    flex:none;width:.62em;height:.62em;border-radius:50%;
    background:var(--ink);
    position:relative;
  }
  .marker span{
    display:none;
    position:absolute;top:50%;left:50%;
    transform:translate(-50%,-52%);
    font-size:.34em;line-height:1;font-weight:500;letter-spacing:-.02em;
  }
  .marker span.d2{font-size:.29em;}
  .marker::after{
    content:"";display:none;
    position:absolute;top:50%;left:50%;
    transform:translate(-50%,-50%);
    width:.21em;height:.21em;
    border:.012em solid var(--ink);border-radius:50%;
  }
  .item.open .marker{background:none;}
  .item.open .marker span{display:block;}
  .item.open .marker::after{display:block;}
  .anno{
    font-size:.42em;font-weight:500;letter-spacing:-.02em;
    align-self:flex-start;transform:translateY(.28em);margin-left:.12em;
    opacity:0;transition:opacity .25s ease .1s;
  }
  .item.open .anno{opacity:1;}
  @media (hover:hover){
    .item:not(.open) .row:hover .marker{background:none;}
    .item:not(.open) .row:hover .marker::after{display:block;}
  }
  .panel{overflow:hidden;max-height:0;transition:max-height .35s ease;}
  .panel-inner{padding:14px 0 40px;max-width:640px;}
  .item.open .panel{max-height:1100px;}
  .item.lead.open .panel{max-height:1500px;}
  .role{font-size:clamp(18px,3.4vw,24px);font-weight:400;letter-spacing:-.005em;}
  .desc{margin-top:14px;font-size:15px;line-height:1.5;color:var(--mute);max-width:36em;}
  /* ---- the argument: score line + why / pause / why now ---- */
  .scoreline{
    margin-top:10px;
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:12.5px;letter-spacing:.01em;
  }
  .plabel{
    margin-top:26px;
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;
  }
  .ptext{margin-top:6px;font-size:15px;line-height:1.55;color:var(--mute);max-width:35em;}
  .meta{margin-top:18px;font-size:13px;line-height:1.35;display:flex;flex-wrap:wrap;gap:6px 0;}
  .meta b{font-weight:700;}
  .meta .sep{color:var(--mute);padding:0 10px;}
  .meta .dim{color:var(--mute);}
  .actions{display:flex;align-items:baseline;gap:32px;margin-top:22px;}
  a.apply{
    display:inline-block;font-size:13px;font-weight:700;
    letter-spacing:.1em;text-transform:uppercase;color:var(--ink);
    text-decoration:none;border-bottom:2px solid var(--ink);padding-bottom:2px;
  }
  a.apply:hover{background:var(--ink);color:var(--paper);}
  button.mark{
    background:none;border:none;cursor:pointer;font-family:inherit;
    font-size:13px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
    color:var(--mute);padding:0 0 2px;border-bottom:2px solid transparent;
  }
  button.mark:hover{color:var(--ink);}
  /* fresh: posted in the last 3 days */
  .fresh{font-size:.5em;align-self:flex-start;transform:translateY(.18em);margin-left:.08em;}
  /* applied: struck from the list, kept for the record */
  .item.applied .co{color:#b9b9b9;text-decoration:line-through;text-decoration-thickness:.045em;}
  .item.applied:not(.open) .marker{background:none;border:.028em solid #b9b9b9;}
  .item.applied .marker span{color:#b9b9b9;}
  .item.applied .marker::after{border-color:#b9b9b9;}
  .item.applied .anno,.item.applied .fresh{color:#b9b9b9;}
  .item.applied button.mark{color:var(--ink);}
  .new{
    display:inline-block;margin-left:12px;font-size:11px;font-weight:700;
    letter-spacing:.12em;border:1px solid var(--ink);padding:2px 7px 1px;
    vertical-align:middle;transform:translateY(-2px);
  }
  /* ---- what I passed on: taste shown by refusal, set at footnote scale ---- */
  .seclabel.pass::before{background:none;border:1px solid var(--ink);}
  .passintro{
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:12.5px;line-height:1.7;letter-spacing:.01em;color:var(--mute);
    margin:0 0 3vh 0;
  }
  .passed{max-width:640px;}
  .pitem{margin:0;}
  .prow{
    display:flex;align-items:center;gap:10px;width:100%;
    background:none;border:none;cursor:pointer;text-align:left;
    color:var(--ink);font-family:inherit;
    font-size:15px;font-weight:500;letter-spacing:-.005em;
    padding:7px 0;
  }
  .prow:focus{outline:none;}
  .prow:focus-visible{outline:2px solid var(--ink);outline-offset:4px;}
  .pdot{flex:none;width:7px;height:7px;border-radius:50%;border:1px solid var(--ink);background:none;}
  @media (hover:hover){
    .pitem:not(.open) .prow:hover .pdot{background:var(--ink);}
  }
  .ppanel{overflow:hidden;max-height:0;transition:max-height .3s ease;}
  .pitem.open .ppanel{max-height:240px;}
  .ppanel-inner{padding:2px 0 16px 17px;}
  .pline{font-size:14.5px;font-weight:400;letter-spacing:-.005em;}
  .pline .pfit{
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:11px;font-weight:400;color:var(--mute);margin-left:10px;letter-spacing:.01em;
  }
  .preason{display:block;margin-top:5px;font-size:14px;line-height:1.5;color:var(--mute);max-width:36em;}
  footer{
    padding:12vh 5vw 6vh;
  }
  .colophon{
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:11.5px;letter-spacing:.02em;line-height:1.9;color:var(--mute);
  }
  .colophon .nw{white-space:nowrap;}
  .colophon a{color:inherit;text-decoration:none;}
  .colophon a:hover{color:var(--ink);}
  .colophon .backed{display:inline-block;margin-top:.5em;}
  .colophon .cstar{font-size:15px;line-height:0;vertical-align:-.15em;}
  @media (max-width:560px){
    .row{font-size:clamp(30px,10.5vw,64px);}
    .panel-inner{padding:12px 0 34px;}
    .seclabel{margin:7.5vh 0 2vh;}
    .seclabel.pass{margin-top:10vh;}
    footer{flex-wrap:wrap;gap:14px 0;padding:9vh 5vw 5vh;}
    footer .col{margin-right:9vw;}
  }

  /* ---- the owner's hands: verdicts. invisible until signed in ---- */
  .vpass{display:none;}
  body.owner .vpass{display:inline-block;}
  /* the persuade verb, on FOUND NOT FOOUND rows */
  .vlook{display:none;}
  body.owner .vlook{
    display:inline-block;background:none;border:none;cursor:pointer;
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:11px;font-weight:500;letter-spacing:.14em;text-transform:uppercase;
    color:var(--mute);padding:10px 0 2px;
  }
  @media (hover:hover){body.owner .vlook:hover{color:var(--ink);}}
  .pitem .vword{
    display:block;margin-top:8px;
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink);
  }
  .pitem .vundo{margin-top:8px;}
  .pitem.vlooked .pdot{background:var(--ink);}
  .relook-tag{font-weight:700;color:var(--ink);}
  .pitem.open .ppanel{max-height:340px;}
  .vwrap{max-width:640px;}
  .vchips{display:none;margin-top:18px;flex-wrap:wrap;align-items:baseline;gap:10px 18px;}
  .vchips.on{display:flex;}
  .vchips-label{
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--mute);
  }
  .vchip{
    background:none;border:none;cursor:pointer;font-family:inherit;
    font-size:12.5px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
    color:var(--mute);padding:0 0 2px;border-bottom:2px solid transparent;
  }
  @media (hover:hover){.vchip:hover{color:var(--ink);border-bottom-color:var(--ink);}}
  .vchip-just{color:var(--ink);}
  .vchip-cancel{font-weight:400;letter-spacing:.08em;}
  .vstate{display:none;margin-top:22px;align-items:baseline;gap:24px;flex-wrap:wrap;}
  .vstate.on{display:flex;}
  .vword{
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:11px;letter-spacing:.14em;text-transform:uppercase;
  }
  .verr{color:var(--mute);}
  .vundo{
    background:none;border:none;cursor:pointer;font-family:inherit;
    font-size:12.5px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
    color:var(--ink);padding:0 0 2px;border-bottom:2px solid var(--ink);
  }
  @media (hover:hover){.vundo:hover{background:var(--ink);color:var(--paper);}}
  .actions.vhidden{display:none;}
  /* passed: ghosted, distinct from applied's strikethrough */
  .item.vpassed .co{color:#b9b9b9;}
  .item.vpassed:not(.open) .marker{background:none;border:.028em solid #b9b9b9;}
  .item.vpassed .marker span{color:#b9b9b9;}
  .item.vpassed .marker::after{border-color:#b9b9b9;}
  .item.vpassed .anno,.item.vpassed .fresh{color:#b9b9b9;}
  .item.vsaving .vword{color:var(--mute);}
  /* the owner's door, ?me */
  #vsheet{position:fixed;left:0;right:0;bottom:0;z-index:80;background:var(--paper);
    border-top:1px solid var(--ink);padding:22px 5vw calc(26px + env(safe-area-inset-bottom));}
  .vsheet-inner{max-width:640px;display:flex;flex-wrap:wrap;align-items:baseline;gap:14px 22px;}
  .vsheet-label{flex-basis:100%;
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--mute);}
  #vemail{flex:1 1 220px;background:none;border:none;border-bottom:1px solid var(--ink);
    font-family:inherit;font-size:16px;padding:6px 0;border-radius:0;color:var(--ink);}
  #vemail:focus{outline:none;border-bottom-width:2px;}
  .vsend{background:none;border:none;cursor:pointer;font-family:inherit;
    font-size:13px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
    color:var(--ink);padding:0 0 2px;border-bottom:2px solid var(--ink);}
  @media (hover:hover){.vsend:hover{background:var(--ink);color:var(--paper);}}
  .vsend:disabled{color:var(--mute);border-color:var(--mute);}
  .vsheet-msg{flex-basis:100%;
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:11px;letter-spacing:.06em;color:var(--mute);min-height:1em;}
  @media (max-width:700px){
    .vchips{gap:0;display:none;}
    .vchips.on{display:block;}
    .vchips-label{display:flex;align-items:center;min-height:44px;border-top:1px solid var(--ink);}
    .vchip{display:flex;align-items:center;width:100%;min-height:48px;
      border-top:1px solid #e3e3e3;padding:0;text-align:left;}
    .vstate.on{display:block;}
    .vstate .vword{display:flex;align-items:center;min-height:48px;border-top:1px solid var(--ink);}
    .vstate .vundo{display:flex;align-items:center;width:100%;min-height:52px;
      border-top:1px solid var(--ink);border-bottom:none;margin:0;padding:0;text-align:left;}
  }
  /* ---- mobile: the room system ---- */
  @view-transition{navigation:auto;}
  .mast-act{display:none;}
  .roombar{display:none;}
  html.restoring .panel,html.restoring .ppanel{transition:none!important;}
  @media (prefers-reduced-motion:reduce){.panel,.ppanel{transition:none!important;}}
  @media (max-width:700px){
    .mast{flex-direction:row;align-items:baseline;gap:12px;padding:14px 20px 0;}
    .mast nav{display:none;}
    .mast-act{display:inline;color:var(--mute);text-decoration:none;white-space:nowrap;letter-spacing:.12em;}
    body{padding-bottom:calc(60px + env(safe-area-inset-bottom));}
    .roombar{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:50;
      background:var(--paper);border-top:1px solid var(--ink);
      padding-bottom:env(safe-area-inset-bottom);}
    .roombar a{flex:1;display:flex;align-items:center;justify-content:center;gap:7px;
      height:52px;color:var(--mute);text-decoration:none;
      font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
      font-size:11px;letter-spacing:.14em;text-transform:uppercase;}
    .roombar a .rdot{width:7px;height:7px;border-radius:50%;background:var(--ink);display:none;}
    .roombar a.here{color:var(--ink);}
    .roombar a.here .rdot{display:block;}
    /* the voice steps up; the furniture recedes */
    .plate{padding:20px 20px 0;}
    .brief{font-size:15px;line-height:1.6;}
    .cascade{font-size:15px;line-height:1.6;}
    .statline{font-size:12px;line-height:1.6;}
    .seclabel{margin:56px 0 20px;}
    .role{font-size:19px;}
    .scoreline{font-size:13px;}
    .plabel{font-size:12px;}
    .ptext{font-size:16px;line-height:1.5;}
    .meta{font-size:13px;}
    .panel-inner{padding:12px 0 8px;}
    /* the hands: actions become rows, type stays delicate */
    .actions{display:block;margin-top:20px;}
    .actions .apply,.actions .mark{display:flex;align-items:center;justify-content:flex-start;
      width:100%;min-height:52px;border-top:1px solid var(--ink);margin:0;padding:0;text-align:left;}
    footer{padding:56px 20px 28px;}
  }
  @media (max-width:360px){.roombar a{font-size:10px;letter-spacing:.1em;gap:6px;}}
</style>
</head>
<body>

  <div class="mast">
    <a class="id" href="/foound/">FOOUND</a>
    <nav>
      <a class="here" href="/">At work</a>
      <a href="/candidate/">Candidate</a>
      <a href="/memory/">Memory</a>
      <a href="/me/">FOOUND for me &rarr;</a>
    </nav>
    <a class="mast-act" href="/me/">FOOUND for me &rarr;</a>
  </div>

  <div class="plate">
    <p class="brief">__GREETING__</p>
    <div class="cascade">__CASCADE__</div>
    <p class="statline">__STATLINE__</p>
__ENTRIES____PASSED__  </div>

  <footer>
    <div class="colophon">
      <div>FOOUND AT WORK &middot; <a href="/archive/">Edition __EDITION__</a> &middot; <span class="nw">__DATELONG__</span></div>
      <div>Compiled 8:00 AM ET &middot; __NCOMPANIES__ companies watched &middot; <span class="nw"><span class="cstar">*</span> posted in the last 3 days</span></div>
      <div><a class="backed" href="/archive/">Back editions &rarr;</a></div>
    </div>
  </footer>


  <nav class="roombar" aria-label="Rooms">
    <a href="/" class="here"><span class="rdot"></span>At work</a>
    <a href="/candidate/"><span class="rdot"></span>Candidate</a>
    <a href="/memory/"><span class="rdot"></span>Memory</a>
  </nav>

<script>
/* the tapped row holds its place while panels trade height */
function pinRow(btn){
  var y0 = btn.getBoundingClientRect().top, t0 = performance.now();
  function hold(now){
    var d = btn.getBoundingClientRect().top - y0;
    if (d) scrollBy(0, d);
    if (now - t0 < 430) requestAnimationFrame(hold);
  }
  requestAnimationFrame(hold);
}
document.querySelectorAll(".item .row").forEach(function(btn){
  btn.addEventListener("click", function(){
    var item = btn.parentElement;
    var wasOpen = item.classList.contains("open");
    var others = document.querySelectorAll(".item.open");
    var hadOther = others.length > 1 || (others.length === 1 && others[0] !== item);
    others.forEach(function(o){
      o.classList.remove("open");
      o.querySelector(".row").setAttribute("aria-expanded","false");
    });
    if(!wasOpen){
      item.classList.add("open");
      btn.setAttribute("aria-expanded","true");
    }
    if(hadOther) pinRow(btn);
  });
});

document.querySelectorAll(".pitem .prow").forEach(function(btn){
  btn.addEventListener("click", function(){
    var it = btn.parentElement;
    var was = it.classList.contains("open");
    document.querySelectorAll(".pitem.open").forEach(function(o){
      o.classList.remove("open");
      o.querySelector(".prow").setAttribute("aria-expanded","false");
    });
    if(!was){ it.classList.add("open"); btn.setAttribute("aria-expanded","true"); }
  });
});

/* long-name guard: a row that cannot fit steps down deliberately, alone */
function fitRows(){
  document.querySelectorAll(".row, .srow").forEach(function(r){
    r.style.fontSize = "";
    var guard = 0;
    while (r.scrollWidth > r.clientWidth + 1 && guard < 4){
      var cur = parseFloat(getComputedStyle(r).fontSize);
      r.style.fontSize = (cur * 0.92) + "px";
      guard++;
    }
  });
}
var fitT; window.addEventListener("resize", function(){ clearTimeout(fitT); fitT = setTimeout(fitRows, 120); });
fitRows();

/* applied tracking: remembered by this browser across editions */
var AP_KEY = "shortlist_applied";
function apLoad(){ try{ return JSON.parse(localStorage.getItem(AP_KEY) || "[]"); }catch(e){ return []; } }
function apSave(a){ try{ localStorage.setItem(AP_KEY, JSON.stringify(a)); }catch(e){} }
var apList = apLoad();
document.querySelectorAll(".item[data-key]").forEach(function(it){
  var k = it.getAttribute("data-key");
  var btn = it.querySelector("button.mark");
  function sync(){
    var on = apList.indexOf(k) >= 0;
    it.classList.toggle("applied", on);
    if(btn) btn.textContent = on ? "Applied ✓ · undo" : "Mark applied";
  }
  sync();
  if(btn) btn.addEventListener("click", function(e){
    e.stopPropagation();
    var i = apList.indexOf(k);
    if(i >= 0) apList.splice(i, 1); else apList.push(k);
    apSave(apList);
    sync();
  });
});

/* the room remembers: which entry was open, and where you were */
document.documentElement.classList.add("restoring");
var OPEN_KEY = "foound-open:" + location.pathname;
function saveOpen(){ var o = document.querySelector(".item.open");
  try{ sessionStorage.setItem(OPEN_KEY, o ? (o.getAttribute("data-key") || "") : ""); }catch(e){} }
document.querySelectorAll(".item .row").forEach(function(btn){
  btn.addEventListener("click", function(){ setTimeout(saveOpen, 0); });
});
try{
  var want = sessionStorage.getItem(OPEN_KEY);
  if (want !== null){
    var cur = document.querySelector(".item.open");
    var target = null;
    if (want){
      document.querySelectorAll(".item[data-key]").forEach(function(it){
        if (it.getAttribute("data-key") === want) target = it;
      });
    }
    if (cur && target !== cur){ cur.classList.remove("open"); cur.querySelector(".row").setAttribute("aria-expanded","false"); }
    if (target && target !== cur){ target.classList.add("open"); target.querySelector(".row").setAttribute("aria-expanded","true"); }
  }
}catch(e){}
var SCROLL_KEY = "foound-scroll:" + location.pathname;
addEventListener("pagehide", function(){ try{ sessionStorage.setItem(SCROLL_KEY, String(Math.round(scrollY))); }catch(e){} });
try{ var sv = sessionStorage.getItem(SCROLL_KEY);
  if (sv && !location.hash) scrollTo(0, parseInt(sv, 10) || 0); }catch(e){}
requestAnimationFrame(function(){ requestAnimationFrame(function(){
  document.documentElement.classList.remove("restoring"); }); });
</script>

<script>window.FOOUND_CFG={url:"https://axlncpmrmsqbomlhhgkh.supabase.co",key:"sb_publishable_BMupqqH8BFuoRENLafPudg_pidmD8-a",edition:"__ISODATE__"};</script>
<script defer src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/dist/umd/supabase.min.js"></script>
<script defer src="/verdicts.js"></script>

</body>
</html>
"""

def why_now_text(job: dict, is_new: bool, now=None, as_of: str | None = None) -> str:
    """Existing Shortlist why-now: new vs posted_at vs still-open.

    Extracted from build_shortlist._argument so hunt seats reuse this
    argument. Not a new model. `is_new` is Shortlist new_keys membership,
    or hunt new_or_resurfaced == "new". `as_of` is the clock named in
    "still open as of …"; the Shortlist passes nothing and keeps its
    8:00 AM ET, the private hunt passes its real compile clock (Move 1 v1.3).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    pa = job.get("posted_at")
    if isinstance(pa, str):
        pa = parse_iso(pa)
    parts = []
    if is_new:
        parts.append("Surfaced for the first time this morning")
    if pa is not None:
        if getattr(pa, "tzinfo", None) is None:
            pa = pa.replace(tzinfo=timezone.utc)
        days = (now - pa).days
        if days <= 0:
            parts.append("posted today")
        elif days == 1:
            parts.append("posted yesterday")
        else:
            parts.append(f"posted {days} days ago")
    parts.append(f"still open as of {as_of or '8:00 AM ET'}")
    whynow = " &middot; ".join(parts)
    return whynow[0].upper() + whynow[1:]


def seat_edition(agent, ranked_all: list, used_ai: bool,
                 second_look: set | None = None, key_fn=None, cap: int = 11) -> dict:
    """The seating rules, lifted verbatim out of build_shortlist (Move 1).

    floor 60 on AI days · cap 11 (the Brief may set 1..20) · a priority-house role that cleared the
    floor displaces the lowest non-priority seat · seats re-sorted by fit ·
    rejects = judged-and-unseated, deduped on (company, title), in ranked
    order · shown = the near misses the edition renders: second looks
    first, never trimmed, then up to five.

    Returns {"ranked", "rejects", "shown", "relooked", "n_strong",
    "has_standout"}. Pure: no I/O, no rendering, no state writes. Both the
    public Shortlist and the private hunt call this, so the seating law
    exists once.
    """
    second_look = second_look or set()
    if key_fn is None:
        key_fn = lambda j: dedup_key(j["title"], j["company"])
    FOOUND_FLOOR = 60
    if used_ai:
        cleared = [j for j in ranked_all if (j.get("fit") or 0) >= FOOUND_FLOOR]
    else:
        cleared = list(ranked_all)   # heuristic day: no scores, no floor
    ranked = cleared[:cap]
    for j in cleared[cap:]:
        if j["company"] in agent.priority_companies and j not in ranked:
            for k in range(len(ranked) - 1, -1, -1):
                if ranked[k]["company"] not in agent.priority_companies:
                    ranked[k] = j
                    break
    ranked.sort(key=lambda j: (j.get("fit") or -1), reverse=True)

    n = len(ranked)
    top = ranked[0].get("fit") if n else None
    second = ranked[1].get("fit") if n > 1 else None
    n_strong = sum(1 for j in ranked if (j.get("fit") or 0) >= 80)
    has_standout = (top is not None and top >= 88
                    and (second is None or top - second >= 5))

    seen_pass = set()
    shown_ids = {id(j) for j in ranked}
    rejects = []
    for j in ranked_all:
        if id(j) in shown_ids:
            continue
        k = (j["company"], j["title"])
        if k in seen_pass:
            continue
        seen_pass.add(k)
        rejects.append(j)

    shown: list = []
    relooked: list = []
    if n > 0 and rejects:
        with_reason = [j for j in rejects if j.get("ai_pause")]
        relooked = [j for j in with_reason if key_fn(j) in second_look]
        others = [j for j in with_reason if j not in relooked]
        shown = (relooked + others)[:max(5, len(relooked))]

    return {
        "ranked": ranked,
        "rejects": rejects,
        "shown": shown,
        "relooked": relooked,
        "n_strong": n_strong,
        "has_standout": has_standout,
    }


def build_shortlist(agent, matches: list, new_keys: set, total_fetched: int,
                    state=None, report=None):
    """Render THE SHORTLIST from today's matches into the agent's output dir."""
    now = _et_now()
    datelong = now.strftime("%A, %B %d, %Y").replace(" 0", " ")

    second_look = state.second_look_keys if state is not None else set()
    ranked_all, used_ai = rank_with_fit(agent, matches, new_keys,
                                        second_look=second_look)

    # Heuristic is a DEGRADED success, never a normal one. Recorded every run
    # so 'ran on rules for five days' can never read as healthy.
    engine = "ai" if used_ai else "heuristic"
    if report is not None:
        report.engine = engine
    try:
        _fstate.record_engine_run(STATE_DIR, agent.agent_id, engine)
    except Exception as _e:
        print(f"[health] engine health not recorded (non-fatal): {_e}")
    seating = seat_edition(agent, ranked_all, used_ai, second_look)
    ranked = seating["ranked"]
    rejects = seating["rejects"]
    n_strong = seating["n_strong"]
    has_standout = seating["has_standout"]

    n = len(ranked)

    # ---- the overnight briefing: greeting, cascade, statline ----
    hour = now.hour
    daypart = "morning" if hour < 12 else ("afternoon" if hour < 18 else "evening")
    greeting = f"Good {daypart}, {agent.name}."

    cascade_lines = []
    if n == 0:
        cascade_lines.append(f"I searched {total_fetched:,} jobs overnight.")
        cascade_lines.append("Nothing cleared the bar today.")
    else:
        cascade_lines.append(f"I searched {total_fetched:,} jobs overnight.")
        cascade_lines.append(f"FOOUND {n} for you.")
        if n_strong >= 2:
            cascade_lines.append(f"{n_strong} are unusually strong.")
        if has_standout:
            cascade_lines.append("1 stands apart.")
    cascade = "<br>".join(cascade_lines)

    # ---- deep look: when the lead clears the bar, the agent keeps looking ----
    if used_ai and n > 0 and ((ranked[0].get("fit") or 0) >= 80 or ranked[0].get("company") in agent.priority_companies):
        _dl = deep_look(ranked[0], load_profile(agent))
        if _dl:
            ranked[0]["deep"] = _dl
            print(f"Deep Look: {ranked[0]['company']} — {_dl.get('verdict','')}")

    read_closely = sum(1 for j in matches if j.get("fit") is not None) if used_ai else len(matches)
    statline = (f"{read_closely} read in full &middot; "
                "everything else dismissed on sight.")
    if n > 0:
        obs = write_brief(n, total_fetched, len(SCRAPERS), ranked, new_keys)
        if obs:
            statline += " " + _html.escape(obs)

    # ---- entries, in three editorial ranks ----
    _EV_MAP = agent.evidence_map

    def _evidence_links(escaped_text: str) -> str:
        """Wrap the first mention of each career anchor in a quiet evidence link."""
        out = escaped_text
        for word, href in _EV_MAP:
            out = re.sub(r"\b(%s)\b" % re.escape(word),
                         r'<a class="ev" href="%s">\1</a>' % href, out, count=1)
        return out

    def _argument(j) -> str:
        blocks = []
        fit = j.get("fit")
        if fit is not None:
            blocks.append(f'        <div class="scoreline">{fit} &middot; {fit_tier(fit)}</div>\n')
        why = j.get("ai_why") or BLURBS.get(j["company"], "A senior creative seat at a company worth watching.")
        blocks.append('        <div class="plabel">Why I chose it</div>\n')
        blocks.append(f'        <p class="ptext">{_evidence_links(_html.escape(why))}</p>\n')
        pause = j.get("ai_pause")
        if pause:
            blocks.append('        <div class="plabel">What gives me pause</div>\n')
            blocks.append(f'        <p class="ptext">{_html.escape(pause)}</p>\n')
        # why now — existing Shortlist argument (why_now_text)
        key = dedup_key(j["title"], j["company"])
        whynow = why_now_text(j, key in new_keys)
        blocks.append('        <div class="plabel">Why now</div>\n')
        blocks.append(f'        <p class="ptext">{whynow}</p>\n')
        dl = j.get("deep")
        if dl:
            blocks.append('        <div class="plabel">I kept looking</div>\n')
            rows = []
            for k, lab in (("role", "Role"), ("moment", "Moment"), ("leadership", "Leadership"),
                           ("signal", "Signal"), ("question", "Question")):
                v = dl.get(k)
                if v:
                    rows.append(f"<b>{lab}</b> &middot; {_html.escape(v)}")
            blocks.append('        <p class="ptext">' + "<br>".join(rows) + '</p>\n')
            v = dl.get("verdict")
            if v:
                blocks.append(f'        <p class="ptext" style="color:var(--ink);font-weight:500;">{_html.escape(v)}</p>\n')
        return "".join(blocks)

    def _entry(i, j, lead=False):
        key = dedup_key(j["title"], j["company"])
        is_new = key in new_keys
        posted = _fmt_posted(j.get("posted_at"))
        fit = j.get("fit")
        anno = f"{{fit&nbsp;{fit}}}" if fit is not None else ""
        pa = j.get("posted_at")
        fresh = False
        if pa is not None:
            if pa.tzinfo is None:
                pa = pa.replace(tzinfo=timezone.utc)
            fresh = (datetime.now(timezone.utc) - pa).days <= 3
        else:
            # No posting date on the source (Apple, Greenhouse boards).
            # FOOUND reads these companies every weekday, so first-seen
            # is the honest proxy for freshly posted.
            if key in new_keys:
                fresh = True
            else:
                seen_on = FIRST_SEEN_DATES.get(key)
                if seen_on:
                    try:
                        fresh = (date.today() - date.fromisoformat(seen_on)).days <= 3
                    except ValueError:
                        pass
        return (SHORTLIST_ENTRY
            .replace("__LEAD__", " lead" if lead else "")
            .replace("__OPEN__", " open" if lead else "")
            .replace("__EXP__", "true" if lead else "false")
            .replace("__NUM__", str(i))
            .replace("__D2__", ' class="d2"' if i >= 10 else "")
            .replace("__KEY__", _html.escape(key))
            .replace("__FRESH__", '<span class="fresh">*</span>' if fresh else "")
            .replace("__COMPANY__", _html.escape(j["company"]))
            .replace("__ANNO__", anno)
            .replace("__NEWTAG__", '<span class="new">NEW</span>' if is_new else "")
            .replace("__ROLE__", _html.escape(j["title"]))
            .replace("__ARGUMENT__", _argument(j))
            .replace("__LOC__", _html.escape(j.get("location", "") or "Location not listed"))
            .replace("__SALARY__", "Salary not posted")
            .replace("__POSTED__", f'<span class="sep">/</span><span class="dim">posted {posted}</span>' if posted else "")
            .replace("__URL__", _html.escape(j.get("url", "") or "#"))
            .replace("__DT_TITLE__", _html.escape(j["title"]))
            .replace("__DT_CO__", _html.escape(j["company"]))
            .replace("__DT_URL__", _html.escape(j.get("url", "") or ""))
            .replace("__DT_LOC__", _html.escape(j.get("location", "") or ""))
            .replace("__DT_FIT__", str(fit) if fit is not None else "")
            .replace("__DT_WHY__", _html.escape(_cut(j.get("ai_why", "") or "", 500)))
            .replace("__DT_PAUSE__", _html.escape(_cut(j.get("ai_pause", "") or "", 500)))
        )

    entries = []
    if n == 0:
        entries.append(
            '    <div class="item"><div class="row" style="cursor:default;">'
            '<span class="marker"></span>Nothing today.</div></div>\n'
        )
    else:
        lead_job = ranked[0]
        strong = [j for j in ranked[1:] if (j.get("fit") or 0) >= 80]
        rest = [j for j in ranked[1:] if (j.get("fit") or 0) < 80]
        entries.append(f'    <div class="seclabel" style="margin-top:5vh;">I&rsquo;d start with {_html.escape(lead_job["company"])}</div>\n')
        entries.append(_entry(1, lead_job))
        idx = 2
        if strong:
            entries.append('    <div class="seclabel">Unusually strong</div>\n')
            for j in strong:
                entries.append(_entry(idx, j)); idx += 1
        if rest:
            entries.append('    <div class="seclabel">Worth your attention</div>\n')
            for j in rest:
                entries.append(_entry(idx, j)); idx += 1

    # ---- what I passed on: the judged-and-declined, at footnote scale ----
    # rejects / shown / relooked come from seat_edition (the seating law,
    # lifted once). Belt and braces: exclusions were applied before ranking;
    # this refuses to render if any user-excluded role reached the PUBLIC
    # near-miss set anyway.
    passed_html = ""
    if state is not None:
        _fstate.assert_no_private_leak(
            rejects, state, key_fn=lambda j: dedup_key(j["title"], j["company"]))

    if n > 0 and rejects:
        # The person's second-look requests are answered FIRST, and are never
        # trimmed by the five-row cap: a question asked must be answered.
        relooked = seating["relooked"]
        shown = seating["shown"]
        if shown:
            word = "misses" if len(shown) > 1 else "miss"
            items = []
            for j in shown:
                key = dedup_key(j["title"], j["company"])
                is_relook = j in relooked
                fit = j.get("fit")
                pfit = f'<span class="pfit">{{fit&nbsp;{fit}}}</span>' if fit is not None else ""
                reason = _html.escape(j["ai_pause"])
                if is_relook:
                    reason = ('<b class="relook-tag">Looked again, as you asked.</b> '
                              + reason)
                items.append(
                    f'      <div class="pitem{" relook" if is_relook else ""}"'
                    f' data-key="{_html.escape(key)}"'
                    f' data-title="{_html.escape(j["title"])}"'
                    f' data-company="{_html.escape(j["company"])}"'
                    f' data-url="{_html.escape(j.get("url", "") or "")}"'
                    f' data-location="{_html.escape(j.get("location", "") or "")}"'
                    f' data-fit="{fit if fit is not None else ""}"'
                    f' data-pause="{_html.escape(j.get("ai_pause", "") or "")}">\n'
                    '        <button class="prow" aria-expanded="false">'
                    f'<span class="pdot"></span>{_html.escape(j["company"])}</button>\n'
                    '        <div class="ppanel"><div class="ppanel-inner">\n'
                    f'          <div class="pline">{_html.escape(j["title"])}{pfit}</div>\n'
                    f'          <span class="preason">{reason}</span>\n'
                    '        </div></div>\n'
                    '      </div>\n')
            passed_html = (
                '\n    <div class="seclabel pass">Found, not FOOUND</div>\n'
                f'    <p class="passintro">{len(rejects)} more read in full and declined. '
                f'The {COUNT_WORDS[min(len(shown), len(COUNT_WORDS) - 1)].lower()} nearest {word}, and why they failed:</p>\n'
                '    <div class="passed">\n' + "".join(items) + '    </div>\n')

    # A second look is ANSWERED when today's edition responded visibly:
    # promoted into the shortlist, or re-declined with a fresh argument above.
    # An edition with no shortlist rendered answers nothing.
    if state is not None and second_look and n > 0:
        answered = {dedup_key(j["title"], j["company"]) for j in ranked}
        answered |= {dedup_key(j["title"], j["company"])
                     for j in rejects if j.get("ai_pause")
                     and dedup_key(j["title"], j["company"]) in second_look}
        state.answered_second_looks = answered & second_look

    os.makedirs(f"{agent.output_dir}/archive", exist_ok=True)
    today_file = f"{agent.output_dir}/archive/{now.strftime('%Y-%m-%d')}.html"
    prior = sorted(_glob.glob(f"{agent.output_dir}/archive/????-??-??.html"))
    if today_file in prior:
        edition = prior.index(today_file) + 1   # same-day re-run reprints, no bump
    else:
        edition = len(prior) + 1

    page = (SHORTLIST_PAGE
        .replace("__GREETING__", _html.escape(greeting))
        .replace("__CASCADE__", cascade)
        .replace("__STATLINE__", statline)
        .replace("__DATELONG__", datelong)
        .replace("__EDITION__", f"{edition:03d}")
        .replace("__ENTRIES__", "".join(entries))
        .replace("__PASSED__", passed_html)
        .replace("__ISODATE__", now.strftime("%Y-%m-%d"))
        .replace("__NCOMPANIES__", str(len(SCRAPERS)))
        .replace("__FRACTION__", f"{n:03d}/{total_fetched:,}")
    )

    with open(f"{agent.output_dir}/index.html", "w") as f:
        f.write(page)
    archive_page = page.replace('href="/archive/"', 'href="./"')
    with open(today_file, "w") as f:
        f.write(archive_page)

    # simple archive index (newest first, numbered by chronological position)
    editions = sorted(_glob.glob(f"{agent.output_dir}/archive/????-??-??.html"))
    links = "\n".join(
        f'<li style="padding:10px 0;border-top:1px solid #000;"><a style="color:#000;font-weight:700;text-decoration:none;" href="{os.path.basename(p)}">{i:03d}<span style="color:#6b6b6b;font-weight:400;"> &nbsp;&middot;&nbsp; {os.path.basename(p)[:-5]}</span></a></li>'
        for i, p in reversed(list(enumerate(editions, 1)))
    )
    with open(f"{agent.output_dir}/archive/index.html", "w") as f:
        f.write(f'<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>THE SHORTLIST · Archive</title></head><body style="font-family:\'Helvetica Neue\',Helvetica,Arial,sans-serif;max-width:680px;margin:0 auto;padding:72px 24px;"><div style="font-size:13px;font-weight:700;letter-spacing:.14em;">THE SHORTLIST <span style="color:#6b6b6b;font-weight:400;">&middot; ARCHIVE</span></div><ol style="list-style:none;margin-top:48px;">{links}</ol><p style="margin-top:48px;font-size:13px;"><a href="../" style="color:#000;">&larr; Latest edition</a></p></body></html>')

    print(f"Shortlist: edition No. {edition:03d} built with {n} role(s).")
    return edition

def _sb_patch_signal(sig_id: str, patch: dict) -> None:
    """Operator-side write to one signal row (service key, bypasses RLS).
    Used to settle answered RECONSIDER signals; raises on any failure so the
    caller can log-and-continue — a patch must never break an edition."""
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise RuntimeError("Supabase not configured")
    r = requests.patch(
        f"{url}/rest/v1/signals",
        params={"id": f"eq.{sig_id}"},
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Prefer": "return=minimal"},
        json=patch, timeout=15)
    r.raise_for_status()


def publish_shortlist(agent) -> bool:
    """Publish the public showroom edition from inside GitHub Actions.

    Returns True only if the intended edition is actually in place — freshly
    pushed, or already identical. Returns False on any publish failure, and
    the caller turns that into a RED run: green must mean delivered.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("Shortlist: not in GitHub Actions, skipping publish.")
        return True          # local/dev: nothing to publish was the correct outcome
    try:
        _sub.run(["git", "config", "user.name", "shortlist-bot"], check=True)
        _sub.run(["git", "config", "user.email", "actions@users.noreply.github.com"], check=True)
        _sub.run(["git", "add", f"{agent.output_dir}/"], check=True)
        diff = _sub.run(["git", "diff", "--staged", "--quiet"])
        if diff.returncode == 0:
            print("Shortlist: no changes to publish.")
            return True      # the intended artifact is already published
        _sub.run(["git", "commit", "-m", f"The Shortlist — {date.today().isoformat()}"], check=True)
        _sub.run(["git", "push"], check=True)
        print("Shortlist: published.")
        return True
    except Exception as e:
        print(f"Shortlist publish failed: {e}")
        return False

# ======================================================================
# Main
# ======================================================================

def active_agents() -> list:
    """Every agent the collector must gather for. One today; a query later.

    Kept as its own function so the market layer never reaches for "the"
    agent — there is no such thing once №002 exists.
    """
    return [load_agent_config("001")]


def main():
    print(f"Job Alerts - {date.today()}")

    agents = active_agents()

    # ---- MARKET LAYER -----------------------------------------------------
    # The collector runs the UNION of every active agent's search terms, not
    # any single agent's. This is a recall guarantee: if one agent's vocabulary
    # decided what was fetched, every other agent could only ever judge roles
    # that agent caused to be retrieved. With one agent the union is identical
    # to today's behaviour; with two it is the difference between №002 having
    # a market and not having one.
    global MARKET_QUERIES
    MARKET_QUERIES = market_query_union(agents)
    print(f"Market: {len(MARKET_QUERIES)} query term(s) across "
          f"{len(agents)} agent(s)")

    # ---- AGENT LAYER (one pass per agent; failures isolated) ---------------
    agent = agents[0]

    # ---- private state: live, else last-known-valid, else fail THIS agent ----
    try:
        state = _fstate.load_private_state(
            agent.agent_id, snapshot_dir=STATE_DIR, agent_no=agent.agent_no,
            published_dir=agent.output_dir)
    except _fstate.PrivateStateUnavailable as e:
        # One agent's failure is one agent's failure. Yesterday's edition stays
        # readable; we do not publish one that contradicts stored decisions.
        print(f"[state] ABORT for agent {agent.agent_id}: {e}")
        _fstate.AgentRunReport(agent_no=agent.agent_no, agent_id=agent.agent_id,
                               state="unavailable", edition="skipped",
                               detail=str(e)[:120]).emit()
        # A skipped edition must never look like a healthy morning. Red run;
        # in the future per-agent matrix this reddens one leg, not the fleet.
        sys.exit(1)
    report = _fstate.AgentRunReport.from_state(state)

    existing = get_existing_keys()
    print(f"Existing in Notion: {len(existing)}")

    raw = []
    for entry in SCRAPERS:
        label = entry[0]
        fn    = entry[1]
        args  = entry[2:]
        results = fn(*args)
        print(f"  {label}: {len(results)} jobs fetched")
        raw += results

    seen_keys = {dedup_key(j["title"], j["company"]) for j in raw}
    for mj in agent.manual_jobs:
        if dedup_key(mj["title"], mj["company"]) in seen_keys:
            print(f"  Manual: already fetched — {mj['company']} — {mj['title']}")
        else:
            raw.append(dict(mj))
            print(f"  Manual: pinned — {mj['company']} — {mj['title']}")

    print(f"Total fetched: {len(raw)}")

    filtered = [j for j in raw
                if passes_title(agent, j["title"])
                and passes_location(agent, j["location"])]
    print(f"After filters: {len(filtered)}")

    # THE CHOKE POINT. Applied here — after collection, before ranking, before
    # any stage that can produce public output. A user-passed role that reaches
    # rank_with_fit lands in the reject set and is published under NEARLY FOOUND
    # with FOOUND's written reason. Filtering downstream would be too late.
    filtered = _fstate.apply_private_exclusions(
        filtered, state, key_fn=lambda j: dedup_key(j["title"], j["company"]))

    new_jobs = [j for j in filtered if dedup_key(j["title"], j["company"]) not in existing]
    print(f"New (not in Notion): {len(new_jobs)}")

    added = []
    for job in new_jobs:
        job["keywords"] = matched_keywords(agent, job["title"])
        if add_to_notion(job):
            added.append(job)
            print(f"  + {job['title']} | {job['company']} | {job.get('location', '')}")

    try:
        send_email(agent, new_jobs, len(added))
    except Exception as e:
        print(f"Email failed: {e}")

    try:
        new_keys = {dedup_key(j["title"], j["company"]) for j in new_jobs}
        build_shortlist(agent, filtered, new_keys, len(raw),
                        state=state, report=report)
        report.edition = "built"
        report.delivered = publish_shortlist(agent)
        # Settle answered second looks ONLY once the answer is truly published.
        # A failed settle is non-fatal: the signal stays active and the same
        # question is answered again tomorrow — honest, and self-healing.
        if report.delivered and state.reconsider:
            try:
                _fstate.mark_reconsiders_answered(
                    state, state.answered_second_looks, patch_row=_sb_patch_signal)
            except Exception as _e:
                print(f"[reconsider] settling failed (non-fatal): {_e}")
    except Exception as e:
        report.edition = "failed"
        report.detail = str(e)[:120]
        print(f"Shortlist failed: {e}")

    report.market_fetched = len(raw)
    report.foound = len(filtered)
    report.emit()

    print(f"\nDone - {len(added)}/{len(new_jobs)} new role(s) saved to Notion.")

    # GREEN MUST MEAN DELIVERED. A run that built nothing, or built an edition
    # and failed to publish it, exits red so the failure is visible in the
    # Actions list — never only in a log nobody reads. Degraded-but-delivered
    # states (stale private state, heuristic engine) remain green: they are
    # logged loudly above and the person still got their morning.
    if report.outcome() in ("failed", "skipped"):
        print("[operator] RED: edition was not published — "
              f"outcome={report.outcome()} edition={report.edition} "
              f"delivered={report.delivered}")
        sys.exit(1)

if __name__ == "__main__":
    if TEST_MODE:
        run_test()
    else:
        main()
