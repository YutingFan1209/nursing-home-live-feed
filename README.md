# Nursing Home Acquisition Tracker

Live feed of PE acquisitions of nursing homes, surfaced via CMS CHOW records, SEC EDGAR filings, Google Alerts/news, and state UCC-1 filings.

**Live site:** https://yutingfan1209.github.io/nursing-home-live-feed/  
**Current DB:** ~1,041 deals

---

## Architecture

```
DISCOVERY                 PROCESSING                STORAGE       FRONTEND
─────────────────         ──────────────────        ───────       ────────
CMS CHOW CSV        →                          →    Postgres  →   GitHub Pages
SEC EDGAR 8-Ks      →   Claude extraction      →    deals.json    (static React)
Google Alerts email →   CMS ownership matcher  →
State UCC-1 filings →   Dedup + stage tracker  →
RSS feeds           →   Lender classifier       →
```

Deal stages: `detected` → `pending_cms` → `confirmed` | `unresolved`

---

## Data sources

### CMS CHOW (primary — confirmed ownership changes)
- Quarterly CSV from CMS; last updated Jan 2026, next drop April 2026
- Matched against DB deals to upgrade `stage` to `confirmed`

### State UCC-1 filings (acquisition signals)
Headless browser scrapers — one per state portal:

| State | Filings | Search type | Deployment |
|---|---|---|---|
| NY | ~801 | Debtor name | headless=True, AWS-ready |
| KY | ~434 | Debtor name (CHOW CSV seeds operator names) | headless=True, AWS-ready |
| OH | ~103 | Secured party | headless=False + hidden window, local only (needs Xvfb for cloud) |
| PA | ~380 | Secured party | requires live Chrome CDP session — **manual only** |

UCC articles bypass the 50/run article cap. Lender classifier pre-filters equipment/vendor filings before they enter the queue.

### Google Alerts (Gmail OAuth)
- Google Alerts → dedicated Gmail inbox → OAuth via `gmail_token.json`
- ~22 URLs extracted per run

### SEC EDGAR
- 8-K filings for major operators: Welltower, Sabra, CareTrust, Ensign, etc.

### RSS feeds
- Skilled Nursing News, McKnight's, Modern Healthcare, Senior Housing News

---

## Pipeline

```bash
# One-off run (no email digest)
source .env && venv/bin/python3 main.py --no-alerts

# Runs automatically at 8am via cron — logs to /tmp/nh-pipeline.log
```

Key behaviors:
- **Async extraction:** 8 concurrent Claude calls via `asyncio.gather`
- **UCC cap exemption:** UCC articles not counted against 50/run limit
- **Dedup hash:** `acquirer + states + date(YYYY-MM) + facility_count + deal_value` — operator names excluded (too variable across sources)
- **Multi-facility merge:** Duplicate deals sharing the same sorted `facility_names` array + lender + date are collapsed; operators merged into array

---

## Deployment (gh-pages)

```bash
# Export DB → deals.json → push to gh-pages
# Must be on gh-pages branch (run_and_deploy.sh has a branch guard)
git checkout gh-pages
# copy deals.json + rebuilt dist/ then commit + push
```

**Branch rules:**
- `main` — source code only, never deploy artifacts
- `gh-pages` — `deals.json` + `index.html` + `assets/` only, never Python source

Frontend is built with Vite/React in `dashboard/frontend/`. Rebuild:
```bash
cd dashboard/frontend && npm run build
# copy dist/ files to repo root on gh-pages branch
```

---

## Setup

```bash
# DB: Docker, Postgres 15, port 5432
docker start nh-test-db
# or: docker run --name nh-test-db -e POSTGRES_PASSWORD=testpass -p 5432:5432 -d postgres:15

# Python env
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Credentials
cp .env.example .env      # add ANTHROPIC_API_KEY
# Gmail OAuth: run once interactively to generate gmail_token.json
```

---

## Known limitations / next steps

- **PA UCC** requires a live Chrome CDP session — cannot be automated without a persistent browser process. Currently manual.
- **OH UCC** uses `headless=False` with a hidden window trick — works locally, needs Xvfb wrapper for cloud deployment.
- **AWS deployment** (Lambda + RDS + EventBridge) designed but not yet deployed.
- **Person-to-entity mapping** for NY UCC operators not built — individual debtor names (e.g. Teddy Lichtschein, Eliezer Scheiner) are not yet linked to their operator network (e.g. SentosaCare).
- **CHOW data lag** — CMS CSV is quarterly; deals confirmed only after the next drop.
