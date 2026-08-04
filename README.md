# Job Alerts — Creative & Brand Leadership

Automated daily job search for senior creative roles (Creative Director, Head of Brand,
etc.) at AI-native and top-tier tech companies. Runs free on GitHub Actions every
weekday at 8 AM Eastern, saves new matches to Notion, and emails a daily digest.

## Companies watched (22)

**AI-native:** Anthropic, OpenAI, DeepMind, Mistral, Perplexity, xAI, ElevenLabs, Cohere, Scale AI, Runway
**Big tech:** Netflix, Microsoft, Nvidia, Apple
**Design-forward:** Figma, Airbnb, Spotify, Snap, Canva, Adobe
**Applied-to:** GitHub, Cleo

**Not automatable (check manually ~weekly):**
- Meta → https://www.metacareers.com/jobs (search "creative director")
- Google → https://www.google.com/about/careers/applications/jobs/results/?q=%22creative+director%22

## What it looks for

- **Titles containing:** creative director (incl. Group/Executive/Senior), director of creative,
  head of creative, creative lead, head of brand, brand director, director of brand, brand lead,
  VP of creative/brand, brand marketing director
- **Excludes:** internships, part-time, contractor roles
- **Locations:** California, NYC, Austin, Chicago, Seattle, London, Paris + major European
  cities, remote US/Europe. Jobs with no listed location pass automatically.

All filters live at the top of `job_alerts.py` (`INCLUDE`, `EXCLUDE_TYPE`,
`ACCEPTED_LOCATIONS`, `SEARCH_QUERIES`) — edit the lists and commit.

## Setup (one time)

### 1. GitHub
1. Create a **private** repo and upload these files, keeping `.github/workflows/` intact.

### 2. Notion
1. Create a database (table) with these exact properties:
   `Job Title` (Title), `Company` (Select), `Location` (Text), `Link` (URL),
   `Date Found` (Date), `Keywords Matched` (Text), `Status` (Select)
2. Create an integration at https://www.notion.so/my-integrations → copy the token.
3. On the database page: ••• menu → Connections → add your integration.
4. Database ID = the 32-character string in the database URL.

### 3. Gmail
1. Enable 2-Step Verification on your Google account.
2. Create an App Password at https://myaccount.google.com/apppasswords (16 characters).

### 4. Repo secrets
GitHub repo → Settings → Secrets and variables → Actions → New repository secret:

| Name | Value |
|------|-------|
| `NOTION_TOKEN` | Notion integration token |
| `NOTION_DB_ID` | 32-char database ID |
| `GMAIL_USER` | your Gmail address |
| `GMAIL_APP_PASSWORD` | 16-char app password |
| `RECIPIENT_EMAIL` | where alerts go |

### 5. First run
Actions tab → **Job Alerts** → Run workflow → set `test_mode: true` → check the logs
(each company should show fetched/matched counts). Then run again with test mode off.

## Schedule

Cron is UTC: `0 12 * * 1-5` = 8 AM EDT weekdays. In winter change to `0 13 * * 1-5`.
GitHub pauses schedules after 60 days of repo inactivity — it emails you; one click re-enables.

## Testing locally

```bash
pip install requests
python job_alerts.py --test   # no Notion/email needed, prints diagnostics
```

## Notes

- The Apple scraper is experimental (Apple has no public API; it parses the search page).
  If it starts returning 0, check https://jobs.apple.com/en-us/search?search=creative%20director manually.
- Greenhouse companies (Anthropic, DeepMind, xAI, Figma, Airbnb, Scale AI, Cleo) don't
  expose posting dates, so their jobs never appear in the "last 24h" email section —
  they still appear under "other new roles" the first day they're seen.
- Duplicates are keyed on title + company, so the same title at two companies is fine.
