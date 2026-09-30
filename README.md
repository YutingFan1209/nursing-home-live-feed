# Nursing Home Acquisition Tracker

A live feed of U.S. nursing home (skilled nursing facility) ownership changes. It combines sources that are early but unconfirmed (state pre-closing filings, UCC-1 financing statements, trade press) with sources that are slow but authoritative (CMS ownership records), and matches everything against CMS facility and ownership data.

**Live site:** https://yutingfan1209.github.io/nursing-home-live-feed/
**As of 2026-09-30:** 2,549 live deals across 53 states and territories (UCC-1 1,653 · CMS CHOW 572 · news 162 · state filings 150 · SEC 12)

| Doc | What's in it |
|---|---|
| [`docs/how-it-works.md`](docs/how-it-works.md) | Plain-English overview for non-developers |
| [`docs/data-sources.md`](docs/data-sources.md) | Every source: how it's fetched, status, known issues |
| [`docs/methodology.md`](docs/methodology.md) | CMS dataset IDs, matching and dedup internals |
| [`docs/stages-and-tags.md`](docs/stages-and-tags.md) | Deal stages and researcher tags |
| [`docs/con-feasibility.md`](docs/con-feasibility.md) | Research behind the state-filing sources, state by state, incl. the NY test |

---

## Architecture

```
DISCOVERY                        PROCESSING                      STORAGE      FRONTEND
───────────────────────          ─────────────────────────       ─────────    ──────────────
State pre-closing filings  ─┐    Claude extraction (news,        Postgres  →  deals.json +
  (8 states, source 'con')  │      filings, scanned PDFs)          │          feed.xml on
State UCC-1 filings (5)    ─┤    Pre-extracted fast path         export_      GitHub Pages
CMS CHOW CSV (quarterly)   ─┼──→   (CHOW, UCC, table-based CON) → deals.py ─→ (static React,
Google Alerts (Gmail)      ─┤    Dedup: exact hash + fuzzy                     Vite build)
RSS trade press            ─┤    CMS ownership matcher → stage
SEC EDGAR full-text search ─┘    Lender classifier (UCC)
```

Deal stages: `detected` → `pending_cms` → `confirmed`, plus `unresolved`, `verified` and `dismissed` (see [`docs/stages-and-tags.md`](docs/stages-and-tags.md)). Pending deals are re-matched against CMS on every run until they confirm or hit the recheck limit.

---

## Data sources

Summary only — [`docs/data-sources.md`](docs/data-sources.md) has the detail and current status of each.

### State pre-closing filings (`source_type='con'`, added 2026-09-30)
Filings a buyer makes with a state *before* a nursing home changes hands — usually weeks to months ahead of news and CMS. One module per state:

| State | Filing | Buyer named? | How it's read |
|---|---|---|---|
| AL `scraper/con_al.py` | SHPDA change-of-ownership notice, ≥20 days pre-close | Yes | PDF → Claude; nursing homes by SHPDA facility-ID type |
| OK `scraper/con_ok.py` | OSDH monthly CON "Notice" | No | PDF table, no Claude |
| ME `scraper/con_me.py` | DHHS CON review (letter of intent) | Yes | PDF → Claude, one per case |
| MI `scraper/con_mi.py` | MDHHS monthly letter-of-intent report | Sometimes | XLSX/PDF table, no Claude |
| MS `scraper/con_ms.py` | MSDH weekly CHOW applications | No | PDF table, no Claude |
| NC `scraper/con_nc.py` | DHSR CON exemption to acquire a facility | Yes | HTML table, no Claude |
| MD `scraper/con_md.py` | MHCC acquisition application, ≥60 days pre-close | Yes | PDF → Claude, one per case |
| NJ `scraper/con_nj.py` | DOH transfer-of-ownership + real estate transfer | Yes | PDF → Claude / HTML table |

Scanned PDFs are transcribed by Claude (`pipeline/pdf_text.py`) — there's no system OCR on the pipeline machine. NY has been tested (found 9 of 9 recent sales in committee agendas 14–28 days before the vote) but isn't built yet.

### State UCC-1 financing statements
Browser scrapers per state portal (`ucc/`), orchestrated by `scraper/ucc.py`. A UCC-1 is financing, not a sale, so it either corroborates an existing deal or seeds a new "signal" deal, after the lender classifier (`ucc/lender_classifier.py`) filters out equipment, vendor and personal liens.

| State | Automation | Notes |
|---|---|---|
| KY | Headless | Most reliable. Rate-limits a second full run on the same day |
| NY | Real Chrome over CDP (`ucc/chrome_cdp.py`) | Cloudflare blocks headless. Org + individual-owner searches, multi-hour |
| NJ | Headless, parallel workers | Portal never returns the lender; deals show "not available" |
| PA | Real Chrome over CDP | Automated but gets Incapsula-challenged under volume |
| OH | Blocked for Playwright since 2026-09 | Searches have been run through a real Chrome session instead |

Run states separately (`--ucc-states KY`); nothing commits until the whole UCC step returns. See [`docs/data-sources.md`](docs/data-sources.md) for search-name sourcing (CHOW + live deal names + CMS individual owners).

### CMS CHOW (confirmation)
Quarterly SNF change-of-ownership CSV. The download URL is discovered through CMS's data API, and every row is recorded in `chow_seen_records` so each run only considers genuinely new rows (until 2026-09-22 a date-window bug meant no CHOW deal had ever been created). Latest file: 2026-07-17.

### News and filings
- **Google Alerts** → dedicated Gmail inbox → Gmail API (`scraper/gmail_alerts.py`). Lookback widens automatically after a gap.
- **RSS**: Skilled Nursing News, McKnight's (news feed, with a browser-impersonation fallback for 403s), Senior Housing News.
- **SEC EDGAR** full-text search for operator/REIT 8-Ks.

### CMS reference data
`cms_facilities` and `cms_ownership_records` (Provider Data Catalog, CCN-keyed), loaded by `cms/fetch_cms.py`; used for matching and for NY/OH individual-owner UCC searches.

---

## Running the pipeline

```bash
# Everything except the email digest
venv/bin/python3 main.py --no-alerts

# Skip UCC (news, EDGAR, CHOW, Gmail and the state filings only): ~1 min vs hours
venv/bin/python3 main.py --no-alerts --skip-ucc

# UCC for specific states only, one state per run
venv/bin/python3 main.py --no-alerts --ucc-states KY

# Gmail alerts only; or override the Gmail lookback after a long gap
venv/bin/python3 main.py --no-alerts --gmail-only
venv/bin/python3 main.py --no-alerts --gmail-days-back 8

# Cron-safe wrapper: starts the DB container if needed, runs main.py, never touches git
./run_pipeline.sh
```

**Scheduling:** there is currently no scheduled job — the daily launchd job is intentionally disabled (plist kept in `.disabled-launchagents/`) and runs are manual. Two sources depend on regular runs: MS removes items 30 days after they complete (runs must be ≤ ~6 weeks apart), and NJ's operator page only lists recent applications.

**Run health:** each run ends with a `RUN HEALTH` summary (`pipeline/run_health.py`) and exits with code **2** if a source failed outright or too many fetches/extractions failed; `SOURCE HEALTH` warnings (`pipeline/source_health.py`) flag sources that have gone quiet.

### Key behaviors
- **Extraction:** text articles go to Claude 8 at a time, capped at 50 per run (the cap bounds Claude cost; CHOW, UCC and table-based state filings don't count against it).
- **Two-layer dedup** (`pipeline/dedup.py`):
  - *Exact hash* — acquirer + states + month + facility count + value. Records with no buyer hash on their own ID instead (UCC filing number, state CON record ID); otherwise every buyer-less record in a state and month collided into one.
  - *Fuzzy pass* after each insert — fuzzy acquirer (≥85) + overlapping states + date within 30 days + similar facility count + a shared facility name. Keeps the more complete row and merges the other into it.
- **CMS matching** (`matcher/ownership.py`) sets the stage. A state-filing deal only counts as `confirmed` by a CMS ownership record starting no earlier than 30 days before the filing; older records belong to the seller.
- **Scope:** assisted living / memory care news deals are auto-dismissed (`pipeline/al_mc_scope.py`); state-filing deals skip that check since each source already restricts to nursing facilities.
- **Names** are cleaned for display by `pipeline/normalizer.py` (legal suffixes dropped; "Opco" kept on street-number names like "813 Keller Lane Opco").

---

## Deployment (gh-pages)

Deploy is a **separate, manual step from the pipeline run** — `run_pipeline.sh` never touches git. (The old combined `run_and_deploy.sh`, which only lives on `gh-pages`, silently stopped automation whenever the working directory was left on `main`.)

`main` and `gh-pages` each track their **own copies** of the Python source; they genuinely diverge. Don't run any Python while checked out on `gh-pages`.

```bash
# 1. On main: export deals.json (also rewrites UCC source links, fetches NJ portal
#    search tokens and derives deal types -- a raw psql export skips all of that)
venv/bin/python3 scripts/export_deals.py /tmp/deals.json   # also writes /tmp/feed.xml

# 2. Stash unrelated changes if they block the switch, then switch
#    (git will show a diverged main.py etc. -- expected)
git stash
git checkout gh-pages

# 3. Copy in the data, commit, push
cp /tmp/deals.json /tmp/feed.xml .
git add deals.json feed.xml && git commit -m "Data refresh $(date '+%Y-%m-%d %H:%M')" && git push origin gh-pages

# 4. Back to main
git checkout main
git stash pop
```

Frontend (Vite/React in `dashboard/frontend/`) — rebuild and deploy:
```bash
cd dashboard/frontend && npm run build && cd ../..
cp -r dashboard/frontend/dist /tmp/nh-dist      # dist/ isn't on gh-pages
git checkout gh-pages
cp /tmp/nh-dist/index.html index.html
# Replace the bundle rather than adding to it: each build gets a new hashed
# name, and copying alongside old ones had left 20 unused bundles in assets/
git rm -q assets/*.js && cp /tmp/nh-dist/assets/*.js assets/
git add -A index.html assets/ && git commit -m "Frontend: <what changed>" && git push origin gh-pages
git checkout main
```

- Every push to `gh-pages` is a Pages deployment. Batch a frontend change with a data refresh into one push where you can.
- After a frontend deploy, hard-refresh the site (Cmd+Shift+R) — browsers keep the old bundle cached.
- `run_and_deploy.sh` still does a full pipeline run plus deploy, and exports `deals.json` with a raw query rather than `export_deals.py`. Don't use it for data refreshes.

**Branch rules:** `main` has the source and never deploy artifacts; `gh-pages` has `deals.json`, `feed.xml`, `index.html` and `assets/`, plus its own older copy of the Python source.

---

## Setup

```bash
# DB: Docker, Postgres 15, port 5432
docker start nh-test-db
# or: docker run --name nh-test-db -e POSTGRES_PASSWORD=testpass -p 5432:5432 -d postgres:15

# Initialize the schema, then apply migrations — in this exact order.
psql "$DATABASE_URL" -f db/schema.sql
psql "$DATABASE_URL" -f db/migration_add_ucc_confirmed.sql
psql "$DATABASE_URL" -f db/migration_ownership_associate_id.sql
psql "$DATABASE_URL" -f db/migration_ownership_switch_source.sql
psql "$DATABASE_URL" -f db/migration_add_ucc_detail_url.sql
psql "$DATABASE_URL" -f db/migration_add_con_source_type.sql
psql "$DATABASE_URL" -f db/migration_add_chow_seen_records.sql

# Python env (3.10)
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install          # browser binaries for the UCC scrapers

# Credentials
cp .env.example .env        # DATABASE_URL, ANTHROPIC_API_KEY

# Gmail OAuth (Google Alerts source):
#   1. In Google Cloud Console, create a project, enable the Gmail API, and
#      create an OAuth 2.0 Client ID (Application type: Desktop app).
#   2. Save the client secret JSON as gmail_credentials.json in the repo root
#      (path configurable via GMAIL_CREDENTIALS_FILE).
#   3. Run any command that reads Gmail alerts once interactively
#      (e.g. `python3 main.py --no-alerts --gmail-only`) to authorize;
#      it saves gmail_token.json for future runs.

# Tests
venv/bin/python3 -m pytest -q tests
```

---

## Known limitations

- **No scheduled runs** (see Scheduling above) — the feed only updates when someone runs the pipeline.
- **UCC automation is fragile:** NY and PA need a real Chrome (not cloud-ready as-is); PA and OH get bot-challenged under volume; OH is blocked for Playwright; KY and OH rate-limit repeat same-day runs.
- **State-filing deals:** OK, MS and most MI records name no buyer; OK statuses and ME/MD cases aren't refreshed after they're first stored; MD dates come from upload month (late when documents are re-uploaded).
- **One sale, several entries:** a sale reported by news and by a state filing (or a UCC-1) can appear as separate deals when the buyer names differ; fuzzy dedup only merges on a close buyer-name match.
- **CMS data:** CHOW is quarterly and lags real closings by months; `cms_ownership_records` / `cms_facilities` have no scheduled refresh (rerun `python3 -m cms.fetch_cms`).
- **Lender classifier is permissive by default:** unrecognized secured parties count as relevant, so new noise categories are only filtered once someone adds a pattern.
- **Fuzzy dedup only runs on new inserts;** there's no periodic full-table sweep.
- **Operator networks:** individual CMS owners feed NY/OH UCC searches, but nothing links an individual across facilities into an operator group.
- **Deployment** is manual, and every data refresh commits a ~2.3 MB `deals.json` to `gh-pages` history. Moving to GitHub Actions Pages deploys would fix the growth.
- **AWS deployment** (Lambda + RDS + EventBridge) was designed but never deployed.
