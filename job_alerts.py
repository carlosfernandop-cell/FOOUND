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

# ======================================================================
# FILTERS — edit these lists to change what jobs you get
# ======================================================================

# A job passes if its title contains AT LEAST ONE of these phrases.
# Note: "creative director" also matches Senior/Group/Executive/Associate CD titles.
INCLUDE = [
    "creative director",
    "director of creative", "director, creative",
    "head of creative", "creative lead",
    "head of brand", "brand director",
    "director of brand", "director, brand", "brand lead",
    "vp of creative", "vp, creative",
    "vp of brand", "vp, brand",
    "brand marketing director",
    "executive creative",
    "brand experience",
]

# A job is dropped if its title contains ANY of these.
EXCLUDE_TYPE = ["intern", "internship", "part-time", "part time", "contractor"]

# Locations. Matching is word-boundary based ("us" will NOT match "Austin").
# A job with NO listed location passes automatically.
ACCEPTED_LOCATIONS = [
    # US — California
    "california", "san francisco", "bay area", "los angeles", "culver city",
    "santa monica", "burbank", "mountain view", "menlo park", "palo alto",
    "cupertino", "sunnyvale", "san jose", "santa clara", "los gatos",
    # US — other hubs
    "new york", "nyc", "brooklyn", "austin", "chicago",
    "seattle", "bellevue", "redmond",
    "boston", "cambridge", "pittsburgh", "miami", "denver", "boulder",
    "washington", "atlanta", "portland",
    # US — general / remote
    "united states", "usa", "us", "remote", "north america",
    # Canada (major AI hubs — Cohere is Toronto-based)
    "toronto", "montreal", "vancouver",
    # Europe
    "london", "paris", "dublin", "amsterdam", "berlin", "munich",
    "zurich", "geneva", "stockholm", "copenhagen", "oslo", "helsinki",
    "madrid", "barcelona", "lisbon", "milan", "vienna", "brussels",
    "united kingdom", "uk", "england", "france", "germany", "ireland",
    "netherlands", "spain", "portugal", "italy", "switzerland",
    "sweden", "denmark", "norway", "finland", "austria", "belgium",
    "europe", "emea",
]

# Search terms used by scrapers that require a query (Workday, Microsoft,
# Netflix, Apple, Spotify, GitHub). Keep these broad — the INCLUDE list
# above does the precise filtering afterwards.
SEARCH_QUERIES = ["creative director", "brand", "creative lead"]

# ======================================================================
# Filter helpers
# ======================================================================

def passes_title(title: str) -> bool:
    t = title.lower()
    if not any(k in t for k in INCLUDE):
        return False
    if any(k in t for k in EXCLUDE_TYPE):
        return False
    return True

def passes_location(location: str) -> bool:
    if not location:
        return True
    loc = location.lower()
    # Workday shows "3 Locations" instead of city names for multi-city
    # postings — let those through rather than silently dropping them.
    if re.search(r"\d+\s+locations", loc):
        return True
    return any(re.search(rf"\b{re.escape(a)}\b", loc) for a in ACCEPTED_LOCATIONS)

def matched_keywords(title: str) -> str:
    t = title.lower()
    return ", ".join(k for k in INCLUDE if k in t)[:120]

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

def get_existing_keys() -> set:
    """Read every (title, company) pair already saved in Notion."""
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
                existing.add(dedup_key(title, company))
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
        for q in SEARCH_QUERIES:
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
        for q in SEARCH_QUERIES:
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

def fetch_apple() -> list:
    """Apple jobs — EXPERIMENTAL. Parses the server-rendered search page.
    Apple has no stable public API; if this breaks, check manually at
    https://jobs.apple.com/en-us/search?search=creative%20director"""
    jobs = []
    seen = set()
    try:
        for q in ["creative director", "brand"]:
            r = requests.get(
                "https://jobs.apple.com/en-us/search",
                params={"search": q, "sort": "newest"},
                headers=HEADERS, timeout=20,
            )
            if not r.ok:
                print(f"[Apple] HTTP {r.status_code} for q={repr(q)}")
                continue
            html = r.text
            # Try embedded JSON state first
            m = re.search(r'window\.APP_STATE\s*=\s*(\{.*?\});\s*</script>', html, re.S)
            if m:
                try:
                    state = json.loads(m.group(1))
                    for j in state.get("searchResults", []):
                        title = j.get("postingTitle", "") or j.get("title", "")
                        key = title.lower().strip()
                        if not title or key in seen:
                            continue
                        seen.add(key)
                        locs = j.get("locations") or []
                        location = "; ".join(
                            l.get("name", "") for l in locs if isinstance(l, dict)
                        )
                        slug = j.get("positionId", "") or j.get("id", "")
                        transformed = j.get("transformedPostingTitle", "")
                        url = f"https://jobs.apple.com/en-us/details/{slug}/{transformed}" if slug else ""
                        jobs.append({
                            "title":     title,
                            "location":  location,
                            "url":       url,
                            "company":   "Apple",
                            "posted_at": parse_iso(j.get("postDateInGMT", "")),
                        })
                    continue
                except Exception:
                    pass
            # Fallback: pull job links out of the HTML
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
    print(f"Filters: keywords={len(INCLUDE)}, locations={len(ACCEPTED_LOCATIONS)}\n")

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

        title_pass  = [j for j in jobs if passes_title(j["title"])]
        loc_pass    = [j for j in title_pass if passes_location(j["location"])]
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

def send_email(new_jobs: list, notion_saved: int = 0):
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

        lines = [f"Hi Carlos,\n\nDaily job alert - {today}"]
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
            f"Hi Carlos,\n\nNo new matching roles found today ({today}).",
            "All companies were checked.\n",
        ]

    lines.append("\n---\nRead today's edition -> https://carlosfernandop-cell.github.io/job-alerts/")
    lines.append("Filters: Creative Director / Head of Brand / Creative & Brand leadership")
    lines.append("Locations: US hubs (CA, NYC, Austin, Chicago, Seattle, Boston, Miami...), Toronto, Europe + Remote")
    lines.append("Not auto-checked (visit manually): Meta, Google, Microsoft, Midjourney, Notion")

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

def load_profile() -> str:
    try:
        with open("profile.md") as f:
            return f.read()
    except Exception:
        return ""

def score_fit(profile: str, job: dict, jd_text: str):
    """Ask Claude for a 0-100 fit score + a one-line 'why' in the approved
    editorial voice. Returns (score, line) or (None, None) on failure."""
    try:
        prompt = (
            "You rank job openings for a specific candidate and write one line about each.\n\n"
            f"CANDIDATE PROFILE:\n{profile}\n\n"
            f"ROLE: {job['title']} at {job['company']} — {job.get('location','')}\n\n"
            f"JOB POSTING TEXT (may include page boilerplate — ignore navigation/footer noise):\n{jd_text or '(no description available — judge from title and company)'}\n\n"
            "Return ONLY a JSON object, no other text:\n"
            '{"score": <0-100 integer, how strong a fit this role is for THIS candidate — seniority match, craft match, brand-led scope, AI-era relevance>, '
            '"line": "<ONE sentence, max 130 chars, confident editorial voice, spoken DIRECTLY TO the candidate as you/your — never his name, never he/his/him — telling him why this role fits him, e.g. cities you already call home, the kind of blank canvas you build best on. Specific to this role. No emoji. No exclamation points.>"}'
        )
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        if not r.ok:
            print(f"  [Fit API {r.status_code}] {r.text[:150]}")
            return None, None
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0))
        score = max(0, min(100, int(data["score"])))
        line = str(data["line"]).strip()[:160]
        return score, line
    except Exception as e:
        print(f"  [Fit error] {job['title']}: {e}")
        return None, None

def rank_with_fit(matches: list, new_keys: set):
    """Return (ranked_matches, used_ai). Each match may gain 'fit' and 'ai_line'."""
    heuristic = lambda j: (
        _seniority_score(j["title"]) + _recency_score(j.get("posted_at"))
        + (2 if dedup_key(j["title"], j["company"]) in new_keys else 0)
    )
    profile = load_profile()
    if not ANTHROPIC_KEY or not profile:
        print("Fit engine: no API key or profile — using heuristic ranking.")
        return sorted(matches, key=heuristic, reverse=True), False

    candidates = sorted(matches, key=heuristic, reverse=True)[:MAX_CANDIDATES_TO_SCORE]
    scored_any = False
    for j in candidates:
        jd = fetch_jd_text(j.get("url", ""))
        score, line = score_fit(profile, j, jd)
        if score is not None:
            j["fit"] = score
            j["ai_line"] = line
            scored_any = True
            print(f"  fit {score:3d}  {j['company']} — {j['title']}")
    if not scored_any:
        print("Fit engine: all scoring failed — falling back to heuristic.")
        return sorted(matches, key=heuristic, reverse=True), False
    return sorted(candidates, key=lambda j: (j.get("fit", -1), heuristic(j)), reverse=True), True

def write_brief(n: int, total_fetched: int, n_companies: int, ranked: list, new_keys: set):
    """Ask Claude to write the one-line morning report from the run's real data.
    Returns the line, or None (caller falls back to the fixed template)."""
    if not ANTHROPIC_KEY or n == 0:
        return None
    try:
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
        word = COUNT_WORDS[n].lower() if n < len(COUNT_WORDS) else str(n)
        roles_word = "role" if n == 1 else "roles"
        prompt = (
            "You are the agent behind THE SHORTLIST, a daily job radar you run for one senior "
            "creative director. You just finished this morning's run. Write the single line of "
            "small monospace type that sits at the top of the page: your report of the job completed.\n\n"
            "RUN DATA (real, this morning):\n"
            f"- Scanned {total_fetched:,} openings at {n_companies} companies, 8:00 AM ET\n"
            f"- {n} made the cut, {n_new} of them newly listed today\n"
            f"- The ranked list:\n" + "\n".join(facts) + "\n\n"
            "RULES:\n"
            f'- Open with "I found {word} {roles_word} worth your time." then state the scan facts '
            f"({total_fetched:,} openings, {n_companies} companies, 8:00 AM ET) in your own compact phrasing.\n"
            "- Close with ONE short observation ONLY if the data genuinely offers one — a role that "
            "appeared today, a standout fit score, a strong lead that has been open a long time and "
            "may not last. If nothing stands out, close with: Nothing else made the cut.\n"
            "- Voice: utilitarian field report. Dry, specific, no hype, no emoji, no exclamation "
            "points, no adjectives that sell. Numbers stay as digits except the opening count word.\n"
            "- One line, max 240 characters total.\n\n"
            'Return ONLY JSON: {"line": "<the line>"}'
        )
        def _extract(text):
            # preferred: JSON object with "line"
            m = re.search(r'\{[^{}]*"line"[^{}]*\}', text, re.S)
            if m:
                try:
                    return str(json.loads(m.group(0))["line"]).strip()
                except Exception:
                    pass
            # tolerated: the model answered with the bare line
            t = text.strip().strip("`").strip()
            if t:
                t = t.splitlines()[0].strip().strip('"').strip()
                if t.lower().startswith("i found"):
                    return t
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
            if line and 40 < len(line) <= 300:
                print(f"Brief: {line}")
                return line
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
    "DeepMind":     "Google's AI vanguard — science-grade substance in need of story.",
    "Mistral":      "Own how Europe's frontier AI lab shows up in the world — brand, campaigns, culture.",
    "Perplexity":   "The challenger answer engine building a brand on speed and candor.",
    "xAI":          "Maximum velocity, maximum attention — a brand built in public.",
    "ElevenLabs":   "One identity stretched across voice, music, and agents — a brand-architecture brief.",
    "Cohere":       "Enterprise AI from Toronto that must feel trustworthy before it feels exciting.",
    "Scale AI":     "The data backbone of the AI boom, largely unbranded territory.",
    "Runway":       "Define the creative voice of the tool redefining filmmaking.",
    "Netflix":      "Shape how the world's biggest entertainment brand publishes culture.",
    "Nvidia":       "The most valuable company on earth, with an enterprise brand to grow into.",
    "Apple":        "The reference point. Craft as religion.",
    "Figma":        "Design's home field — an audience that judges every pixel professionally.",
    "Airbnb":       "Brand-led to its core; creative leadership reports to the very top.",
    "Spotify":      "The brand that turned data into pop culture. Wrapped, but all year.",
    "Snap":         "A global brand that still knows how to play.",
    "Canva":        "Design for 200M people, with a taste for big swings.",
    "Adobe":        "The tools creativity runs on, mid-reinvention for the AI era.",
    "GitHub":       "The home of 100M developers — brand, film, campaigns, and craft in-house.",
    "Cleo":         "Fintech with a voice — an AI that talks money like a friend.",
    "Stripe":       "Lead brand moments for the company that set the bar for craft in tech.",
    "Duolingo":     "The loudest, most awarded brand voice in consumer tech.",
    "Squarespace":  "An in-house agency with Super Bowl reps and design awards to defend.",
    "Pinterest":    "A visual-culture platform where inspiration is the product.",
    "Discord":      "Playful, distinctive brand craft for the internet's living room.",
    "Webflow":      "Design-native product, visual-first audience.",
    "Synthesia":    "The AI-video leader, London-built, making enterprise feel cinematic.",
    "Suno":         "Build the campaign language for AI-made music — a brand still wet on the canvas.",
    "Harvey":       "Make an $11B legal-AI company feel inevitable to the most skeptical audience in business.",
    "Sierra":       "Bret Taylor's $15B bet on conversational AI, polishing an enterprise identity.",
    "Decagon":      "AI agents for customer support — fast-growing, identity still forming.",
    "Cursor":       "The fastest-growing dev tool in history, whose editor is its brand.",
    "Cognition":    "Maker of Devin — foundational brand work, wide open.",
    "Hugging Face": "The beloved open-source home of AI, scrappy by design.",
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

SHORTLIST_ENTRY = """    <div class="item" data-key="__KEY__">
      <button class="row" aria-expanded="false">
        <span class="marker"><span__D2__>__NUM__</span></span><span class="co">__COMPANY__</span>__FRESH__<span class="anno">__ANNO__</span>
      </button>
      <div class="panel"><div class="panel-inner">
        <div class="role">__ROLE____NEWTAG__</div>
        <p class="desc">__DESC__</p>
        <div class="meta"><b>__LOC__</b><span class="sep">/</span><span>__SALARY__</span>__POSTED__</div>
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
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>THE SHORTLIST — __DATELONG__</title>
<style>
  :root{--ink:#000;--paper:#fff;--mute:#6b6b6b;}
  *{margin:0;padding:0;box-sizing:border-box;}
  html{-webkit-text-size-adjust:100%;}
  body{background:var(--paper);color:var(--ink);font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased;}
  .plate{padding:6vw 5vw 4vw;}
  .brief{
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
    font-size:12.5px;line-height:1.7;letter-spacing:.01em;
    margin:0 0 8vh 0;
  }
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
    .row:hover .marker{background:var(--ink);}
    .item.open .row:hover .marker{background:none;}
  }
  .panel{overflow:hidden;max-height:0;transition:max-height .35s ease;}
  .panel-inner{padding:14px 0 44px;max-width:640px;}
  .item.open .panel{max-height:560px;}
  .role{font-size:clamp(18px,3.4vw,24px);font-weight:400;letter-spacing:-.005em;}
  .desc{margin-top:14px;font-size:15px;line-height:1.5;color:var(--mute);max-width:36em;}
  .meta{margin-top:16px;font-size:13px;line-height:1.35;display:flex;flex-wrap:wrap;gap:6px 0;}
  .meta b{font-weight:700;}
  .meta .sep{color:var(--mute);padding:0 10px;}
  .meta .dim{color:var(--mute);}
  .actions{display:flex;align-items:baseline;gap:32px;margin-top:20px;}
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
  /* fresh — posted in the last 3 days */
  .fresh{font-size:.5em;align-self:flex-start;transform:translateY(.18em);margin-left:.08em;}
  /* applied — struck from the list, kept for the record */
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
  footer{
    display:flex;align-items:flex-end;
    padding:8vh 5vw 4vh;
    font-size:11px;font-weight:500;letter-spacing:.01em;line-height:1.45;
  }
  footer .col{margin-right:8vw;}
  footer .lab{text-transform:uppercase;}
  footer .val{font-weight:400;color:var(--mute);}
  footer .val a{color:inherit;text-decoration:none;}
  footer .val a:hover{color:var(--ink);}
  footer .num{margin-left:auto;font-weight:400;color:var(--mute);}
  @media (max-width:560px){
    .row{font-size:clamp(30px,10.5vw,64px);}
    footer{flex-wrap:wrap;gap:12px 0;}
    footer .col{margin-right:10vw;}
  }
</style>
</head>
<body>

  <div class="plate">
    <p class="brief">__BRIEF__</p>
__ENTRIES__  </div>

  <footer>
    <div class="col">
      <div class="lab">The Shortlist&mdash;Daily Selection</div>
      <div class="val">__DATELONG__</div>
    </div>
    <div class="col">
      <div class="lab">Edition</div>
      <div class="val"><a href="archive/">No. __EDITION__</a></div>
    </div>
    <div class="col">
      <div class="lab">Compiled</div>
      <div class="val">8:00 AM ET &middot; __NCOMPANIES__ companies</div>
    </div>
    <div class="col">
      <div class="lab">*</div>
      <div class="val">out of the oven &mdash; posted in the last 3 days</div>
    </div>
    <div class="num">__FRACTION__</div>
  </footer>

<script>
document.querySelectorAll(".item .row").forEach(function(btn){
  btn.addEventListener("click", function(){
    var item = btn.parentElement;
    var wasOpen = item.classList.contains("open");
    document.querySelectorAll(".item.open").forEach(function(o){
      o.classList.remove("open");
      o.querySelector(".row").setAttribute("aria-expanded","false");
    });
    if(!wasOpen){
      item.classList.add("open");
      btn.setAttribute("aria-expanded","true");
    }
  });
});

/* applied tracking — remembered by this browser across editions */
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
    if(btn) btn.textContent = on ? "Applied ✓ — undo" : "Mark applied";
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
</script>

</body>
</html>
"""

def build_shortlist(matches: list, new_keys: set, total_fetched: int):
    """Render THE SHORTLIST from today's matches and write docs/."""
    now = _et_now()
    datelong = now.strftime("%A, %B %d, %Y").replace(" 0", " ")

    ranked_all, used_ai = rank_with_fit(matches, new_keys)
    ranked = ranked_all[:11]

    n = len(ranked)

    scan_stats = f"{total_fetched:,} openings at {len(SCRAPERS)} companies, scanned this morning at 8:00 AM ET"
    if n == 0:
        brief = (f"I read all {total_fetched:,} openings at {len(SCRAPERS)} companies "
                 "this morning at 8:00 AM ET. Nothing cleared the bar today.")
    else:
        word = COUNT_WORDS[n].lower() if n < len(COUNT_WORDS) else str(n)
        roles_word = "role" if n == 1 else "roles"
        brief = (f"I found {word} {roles_word} worth your time. "
                 f"Hand-filtered from {scan_stats}. Nothing else made the cut.")
        ai_brief = write_brief(n, total_fetched, len(SCRAPERS), ranked, new_keys)
        if ai_brief:
            brief = ai_brief

    entries = []
    if n == 0:
        entries.append(
            '    <div class="item"><div class="row" style="cursor:default;">'
            '<span class="marker"></span>Nothing today.</div></div>\n'
        )
    for i, j in enumerate(ranked, 1):
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
        entry = (SHORTLIST_ENTRY
            .replace("__NUM__", str(i))
            .replace("__D2__", ' class="d2"' if i >= 10 else "")
            .replace("__KEY__", _html.escape(key))
            .replace("__FRESH__", '<span class="fresh">*</span>' if fresh else "")
            .replace("__COMPANY__", _html.escape(j["company"]))
            .replace("__ANNO__", anno)
            .replace("__NEWTAG__", '<span class="new">NEW</span>' if is_new else "")
            .replace("__ROLE__", _html.escape(j["title"]))
            .replace("__DESC__", _html.escape(j.get("ai_line") or BLURBS.get(j["company"], "A senior creative seat at a company worth watching.")))
            .replace("__LOC__", _html.escape(j.get("location", "") or "Location not listed"))
            .replace("__SALARY__", "Salary not posted")
            .replace("__POSTED__", f'<span class="sep">/</span><span class="dim">posted {posted}</span>' if posted else "")
            .replace("__URL__", _html.escape(j.get("url", "") or "#"))
        )
        entries.append(entry)

    os.makedirs("docs/archive", exist_ok=True)
    today_file = f"docs/archive/{now.strftime('%Y-%m-%d')}.html"
    prior = sorted(_glob.glob("docs/archive/????-??-??.html"))
    if today_file in prior:
        edition = prior.index(today_file) + 1   # same-day re-run reprints, no bump
    else:
        edition = len(prior) + 1

    page = (SHORTLIST_PAGE
        .replace("__BRIEF__", _html.escape(brief))
        .replace("__DATELONG__", datelong)
        .replace("__EDITION__", f"{edition:03d}")
        .replace("__ENTRIES__", "".join(entries))
        .replace("__NCOMPANIES__", str(len(SCRAPERS)))
        .replace("__FRACTION__", f"{n:03d}/{total_fetched:,}")
    )

    with open("docs/index.html", "w") as f:
        f.write(page)
    archive_page = page.replace('href="archive/"', 'href="./"')
    with open(today_file, "w") as f:
        f.write(archive_page)

    # simple archive index (newest first, numbered by chronological position)
    editions = sorted(_glob.glob("docs/archive/????-??-??.html"))
    links = "\n".join(
        f'<li style="padding:10px 0;border-top:1px solid #000;"><a style="color:#000;font-weight:700;text-decoration:none;" href="{os.path.basename(p)}">No. {i:03d}<span style="color:#6b6b6b;font-weight:400;"> &nbsp;&middot;&nbsp; {os.path.basename(p)[:-5]}</span></a></li>'
        for i, p in reversed(list(enumerate(editions, 1)))
    )
    with open("docs/archive/index.html", "w") as f:
        f.write(f'<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>THE SHORTLIST — Archive</title></head><body style="font-family:\'Helvetica Neue\',Helvetica,Arial,sans-serif;max-width:680px;margin:0 auto;padding:72px 24px;"><div style="font-size:13px;font-weight:700;letter-spacing:.14em;">THE SHORTLIST <span style="color:#6b6b6b;font-weight:400;">&middot; ARCHIVE</span></div><ol style="list-style:none;margin-top:48px;">{links}</ol><p style="margin-top:48px;font-size:13px;"><a href="../" style="color:#000;">&larr; Latest edition</a></p></body></html>')

    print(f"Shortlist: edition No. {edition:03d} built with {n} role(s).")
    return edition

def publish_shortlist():
    """Commit and push docs/ from inside GitHub Actions."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("Shortlist: not in GitHub Actions, skipping publish.")
        return
    try:
        _sub.run(["git", "config", "user.name", "shortlist-bot"], check=True)
        _sub.run(["git", "config", "user.email", "actions@users.noreply.github.com"], check=True)
        _sub.run(["git", "add", "docs/"], check=True)
        diff = _sub.run(["git", "diff", "--staged", "--quiet"])
        if diff.returncode == 0:
            print("Shortlist: no changes to publish.")
            return
        _sub.run(["git", "commit", "-m", f"The Shortlist — {date.today().isoformat()}"], check=True)
        _sub.run(["git", "push"], check=True)
        print("Shortlist: published.")
    except Exception as e:
        print(f"Shortlist publish failed: {e}")

# ======================================================================
# Main
# ======================================================================

def main():
    print(f"Job Alerts - {date.today()}")

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

    print(f"Total fetched: {len(raw)}")

    filtered = [j for j in raw if passes_title(j["title"]) and passes_location(j["location"])]
    print(f"After filters: {len(filtered)}")

    new_jobs = [j for j in filtered if dedup_key(j["title"], j["company"]) not in existing]
    print(f"New (not in Notion): {len(new_jobs)}")

    added = []
    for job in new_jobs:
        job["keywords"] = matched_keywords(job["title"])
        if add_to_notion(job):
            added.append(job)
            print(f"  + {job['title']} | {job['company']} | {job.get('location', '')}")

    try:
        send_email(new_jobs, len(added))
    except Exception as e:
        print(f"Email failed: {e}")

    try:
        new_keys = {dedup_key(j["title"], j["company"]) for j in new_jobs}
        build_shortlist(filtered, new_keys, len(raw))
        publish_shortlist()
    except Exception as e:
        print(f"Shortlist failed: {e}")

    print(f"\nDone - {len(added)}/{len(new_jobs)} new role(s) saved to Notion.")

if __name__ == "__main__":
    if TEST_MODE:
        run_test()
    else:
        main()
