# Nursing Home Acquisition Tracker

Live feed of PE acquisitions of nursing homes, surfaced via CMS CHOW records, SEC EDGAR filings, Google Alerts/news, and state UCC-1 filings.

**Live site:** https://yutingfan1209.github.io/nursing-home-live-feed/  
**Current DB:** ~1,252 deals (as of 2026-07-16)

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

| State | Search type | Deployment |
|---|---|---|
| NY | Debtor name — **both** Organization mode (known operator LLCs) **and** Individual mode (CMS owner names, see below) | headless=True, AWS-ready |
| KY | Debtor name (CHOW CSV seeds operator names) | headless=True, AWS-ready |
| OH | Secured party | headless=False + hidden window, local only (needs Xvfb for cloud) |
| PA | Secured party | requires live Chrome CDP session — **manual only** |

UCC articles bypass the 50/run article cap. Lender classifier pre-filters equipment/vendor/personal-financing filings (see below) before they enter the queue.

#### NY individual owner search (CMS ownership matching)
NY's UCC portal has separate Organization and Individual debtor search modes with different form fields — searching a person's name in Organization mode silently returns near-zero matches. `ucc/ny_playwright.py` routes correctly based on name type.

Individual search terms come from `cms_ownership_records` (loaded from CMS's Provider Data Catalog `NH_Ownership_*.csv`, not the raw enrollment "All Owners" API — that dataset is keyed by PECOS Enrollment ID and can't be joined to facility state at all), filtered to equity/control roles only (`main.py:_CMS_OWNERSHIP_RELEVANT_ROLES`) to keep runtime bounded. This roughly doubles NY's search volume and runtime (~40-75 min depending on role-filter width) but surfaces real acquisition signals — individual beneficial owners — that org-name search alone never finds.

`cms_ownership_records` and `cms_facilities` are loaded via `cms/fetch_cms.py` / `matcher/carecompare.py`, which discover the current CSV URL dynamically via CMS's metastore API rather than a hardcoded monthly link (the direct CSV URL rotates every release). No scheduled refresh is wired up yet — rerun `python3 -m cms.fetch_cms` periodically to keep ownership data current.

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
# One-off full run (no email digest)
venv/bin/python3 main.py --no-alerts

# Skip UCC scraping (RSS/EDGAR/CHOW/Gmail alerts only) — use when UCC
# already ran today and you just want news/alert ingestion (~1 min vs ~90 min)
venv/bin/python3 main.py --no-alerts --skip-ucc

# Cron-safe wrapper — cd's to repo root, starts the DB container if
# needed, runs main.py. No git/branch operations.
./run_pipeline.sh

# Runs daily at 8am via cron:
#   0 8 * * * PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin /path/to/run_pipeline.sh >> /tmp/nh-pipeline.log 2>&1
# Requires Full Disk Access granted to cron in System Settings > Privacy
# & Security (macOS TCC blocks cron's filesystem access otherwise).
```

Key behaviors:
- **Async extraction:** 8 concurrent Claude calls via `asyncio.gather`
- **UCC cap exemption:** UCC articles not counted against 50/run limit
- **Two-layer dedup:**
  - *Exact hash* (`pipeline/dedup.py:make_dedup_hash`) — `acquirer + states + date(YYYY-MM) + facility_count + deal_value`, operator names excluded (too variable across sources). Blocks identical re-inserts before they hit the DB.
  - *Fuzzy/semantic pass* (`pipeline/dedup.py:find_and_resolve_fuzzy_duplicate`) — runs after every insert. Catches the same deal reported by different articles with different state subsets, facility counts, or acquirer name variants ("Ensign Group" vs "The Ensign Group") that the exact hash misses. Matches on fuzzy acquirer name (≥85 token-sort ratio) + overlapping states (≥50%) + acquisition date (±30 days) + facility count (±20%), treating missing fields as non-contradictory rather than a hard mismatch. Keeps the more complete row, merges states, deletes the other.
- **Multi-facility merge:** Duplicate deals sharing the same sorted `facility_names` array + lender + date are collapsed; operators merged into array
- **Blank-lender guard:** UCC filings with no recoverable secured-party name (inactive/lapsed filings) don't seed new deals — logged as skipped rather than stored with no lender info

---

## Deployment (gh-pages)

Deploy is a **separate, manual step from the pipeline run** — `run_pipeline.sh` (cron) never touches git. This split exists because the old combined `run_and_deploy.sh` model (pipeline run + branch switch + deploy, all in one script that only lives on `gh-pages`) was fragile: it depended on the working directory being flipped to `gh-pages` at the exact moment it ran, with `main`'s Python source left behind as untracked files. When something instead left the working directory on `main`, cron found `run_and_deploy.sh` missing and automation silently stopped — this is why the daily run wasn't actually working for a while.

`main` and `gh-pages` each track their **own independent copies** of the Python source files — they're not shared via untracked leftovers, they genuinely diverge. Don't run any Python while checked out on `gh-pages`; it'll be a stale, different version of the pipeline.

```bash
# 1. On main: export deals.json from the DB
psql "$DATABASE_URL" -t -A -c "SELECT json_build_object('deals', json_agg(...), 'total', COUNT(*)) FROM deals ..." > /tmp/deals.json

# 2. Stash any unrelated pre-existing changes blocking the branch switch,
#    switch to gh-pages (git will show a diverged main.py etc. — expected, ignore it)
git stash
git checkout gh-pages

# 3. Copy in the fresh deals.json, commit, push
cp /tmp/deals.json deals.json
git add deals.json && git commit -m "Data refresh $(date '+%Y-%m-%d %H:%M')" && git push origin gh-pages

# 4. Return to main and restore
git checkout main
git stash pop
```

`run_and_deploy.sh` (still on `gh-pages` only) does the same export+push, plus a full pipeline re-run and frontend copy — don't invoke it directly unless you actually want another full run; for a data-only refresh, do the steps above instead.

**Branch rules:**
- `main` — source code only, never deploy artifacts
- `gh-pages` — `deals.json` + `index.html` + `assets/` only, plus its own (older, divergent) copy of the Python source

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

# Initialize the schema, then apply migrations — in this exact order.
# Only 3 of the 5 files in db/ are needed for a fresh database; the other
# two (migration_add_ucc_support.sql, migration_widen_ownership_unique.sql)
# were written against an older/different schema shape and error out on a
# fresh install (schema.sql already has their end state baked in). Verified
# against a scratch DB before writing this.
psql "$DATABASE_URL" -f db/schema.sql
psql "$DATABASE_URL" -f db/migration_add_ucc_confirmed.sql
psql "$DATABASE_URL" -f db/migration_ownership_associate_id.sql
psql "$DATABASE_URL" -f db/migration_ownership_switch_source.sql

# Python env
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install          # downloads browser binaries for the UCC scrapers (NY/KY/OH/PA)

# Credentials
cp .env.example .env      # add ANTHROPIC_API_KEY

# Gmail OAuth (Google Alerts source):
#   1. In Google Cloud Console, create a project (or use an existing one),
#      enable the Gmail API, and create an OAuth 2.0 Client ID
#      (Application type: Desktop app).
#   2. Download the client secret JSON and save it as gmail_credentials.json
#      in the repo root (path is configurable via GMAIL_CREDENTIALS_FILE).
#   3. Run any command that touches Gmail alerts once interactively
#      (e.g. `python3 main.py --no-alerts`) — it opens a browser for you to
#      authorize, then saves gmail_token.json for all future runs.
```

---

## Known limitations / next steps

- **PA UCC** requires a live Chrome CDP session — cannot be automated without a persistent browser process. Currently manual.
- **OH UCC** uses `headless=False` with a hidden window trick — works locally, needs Xvfb wrapper for cloud deployment.
- **AWS deployment** (Lambda + RDS + EventBridge) designed but not yet deployed.
- **Person-to-network mapping** partially addressed — individual CMS owner names now feed NY UCC search directly (see above), but there's still no aggregation linking an individual across multiple facilities into an operator network (e.g. recognizing that several individually-named owners all tie back to the same group like SentosaCare).
- **CHOW data lag** — CMS CSV is quarterly; deals confirmed only after the next drop.
- **`lender_classifier.py`'s default is permissive** — an unrecognized secured-party name defaults to `is_acquisition_relevant=True` rather than `False`. This is deliberate (surface maybe-relevant filings for review rather than silently drop real signals) but means new noise categories (personal loans, niche equipment financiers) only get filtered after someone notices them in the data and adds an exclusion pattern — there's no allowlist-only mode.
- **`cms_ownership_records`/`cms_facilities` have no scheduled refresh** — loaded once via `cms/fetch_cms.py`, not on a cron. Individual-owner search terms will go stale as CMS updates ownership data (released monthly) unless this is rerun periodically.
- **Fuzzy dedup only runs going forward** — `find_and_resolve_fuzzy_duplicate()` checks each newly-inserted deal against existing ones, so it self-heals new duplicates, but it was only manually swept once against pre-existing historical data (2026-07-02). No periodic full-table sweep is scheduled.
