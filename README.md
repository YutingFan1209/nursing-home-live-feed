# Nursing Home Acquisition Tracker

A live feed of U.S. nursing home (skilled nursing facility) ownership changes. It combines sources that are early but unconfirmed (state pre-closing filings, UCC-1 financing statements, trade press) with sources that are slow but authoritative (CMS ownership records), and matches everything against CMS facility and ownership data.

**Live site:** https://yutingfan1209.github.io/nursing-home-live-feed/
**As of 2026-10-21:** 4,117 live deals across 51 states and territories (UCC-1 3,003 · CMS CHOW 700 · CMS ownership file 49 · state filings 197 · news 159 · SEC 9)

| Doc | What's in it |
|---|---|
| [`docs/how-it-works.md`](docs/how-it-works.md) | Plain-English overview for non-developers |
| [`docs/data-sources.md`](docs/data-sources.md) | Every source: how it's fetched, status, known issues |
| [`docs/methodology.md`](docs/methodology.md) | CMS dataset IDs, matching and dedup internals |
| [`docs/stages-and-tags.md`](docs/stages-and-tags.md) | Deal stages and researcher tags |
| [`docs/con-feasibility.md`](docs/con-feasibility.md) | Research behind the state-filing sources, state by state, incl. the NY test |
| [`docs/ucc-feasibility.md`](docs/ucc-feasibility.md) | Every state's UCC search: cost, robots.txt, terms, captcha, what's live |
| [`docs/manual-ucc-searches.md`](docs/manual-ucc-searches.md) | States searched by hand, with the worklists in `Manual/` |

---

## Architecture

```
DISCOVERY                        PROCESSING                      STORAGE      FRONTEND
───────────────────────          ─────────────────────────       ─────────    ──────────────
State pre-closing filings  ─┐    Claude extraction (news,        Postgres  →  deals.json +
  (13 states + Philadelphia)│      filings, scanned PDFs)          │          feed.xml on
State UCC-1 filings        ─┤    Pre-extracted fast path         export_      GitHub Pages
  (12 automated, 7 by hand) │      (CHOW, UCC, table-based CON) → deals.py ─→ (static React,
CMS CHOW CSV (quarterly)   ─┼──→ Dedup: exact hash + fuzzy                     Vite build)
CMS ownership file (monthly)┤      + same-sale merge
Google Alerts (Gmail)      ─┤    CMS ownership matcher → stage
RSS trade press            ─┤    Lender classifier + lender
SEC EDGAR full-text search ─┘      search filter (UCC)
```

Deal stages: `detected` → `pending_cms` → `confirmed`, plus `unresolved`, `verified` and `dismissed` (see [`docs/stages-and-tags.md`](docs/stages-and-tags.md)). Pending deals are re-matched against CMS on every run until they confirm or hit the recheck limit.

---

## Data sources

Summary only — [`docs/data-sources.md`](docs/data-sources.md) has the detail and current status of each.

### State pre-closing filings (`source_type='con'`, since 2026-09-30)
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
| PA `scraper/con_pa.py` | DOH licence application, 10-day public comment | Yes | HTML form fields, no Claude |
| PA (Philadelphia) `scraper/con_phl.py` | City notice before a transfer | Yes | PDF → Claude, many scanned |
| CA `scraper/con_ca.py` | OHCA material change notice, ≥90 days pre-close | Yes | HTML table + PDF → Claude; SNF deals only |
| NY `scraper/con_ny.py` | PHHPC establishment exhibit, 2–4 weeks before the council vote | Yes | Agenda PDF → Claude (`ENABLE_CON_NY`) |
| IL `scraper/con_il.py` | HFSRB county-home sale (most IL sales need no CON) | Yes | Scanned application → Claude |
| HI `scraper/con_hi.py` | SHPDA CON application to acquire | Yes (address, not name) | HTML table, no Claude |
| RI `scraper/con_ri.py` | OHSD change-in-effective-control request, months pre-close | Yes | Public Google Sheet (CSV), no Claude |
| MT `scraper/con_mt.py` | DPHHS monthly CON report, CHOW rows | No | PDF table, no Claude |
| TN `scraper/con_tn.py` | HFC monthly agenda CHOWs, ratified after closing; applications name the licensee | Upcoming meeting only | Real Chrome (CDP), scanned application → Claude |
| MO, CA `scraper/licence_lists.py` | New licensee on a CMS-listed home in the state licence list (diffed against the last copy) | Yes (licensee) | Open-data CSV, no Claude |

Scanned PDFs are transcribed by Claude (`pipeline/pdf_text.py`) — there's no system OCR on the pipeline machine. Research and build order for the other states: [`docs/con-feasibility.md`](docs/con-feasibility.md).

### State UCC-1 financing statements
One module per state portal (`ucc/`), orchestrated by `scraper/ucc.py`. A UCC-1 is financing, not a sale, so it either corroborates an existing deal or seeds a new "signal" deal, after the lender classifier (`ucc/lender_classifier.py`) filters out equipment, vendor and personal liens.

Two ways in, depending on what the portal allows:
- **Debtor search:** look up names we already know (CHOW buyers, live deal names, CMS owners).
- **Lender search:** look up the 47 lenders that mostly finance nursing homes (`LENDER_TERMS` in `ucc/lender_search.py`). This finds borrowers we've never heard of. `filter_lender_hits` keeps filings from the last 2 years whose debtor looks like a nursing home entity, plus that lender's other filings the same day (a portfolio's property companies).

| State | How | Notes |
|---|---|---|
| KY | Headless debtor search | Most reliable. Rate-limits a second full run on the same day |
| NY | Debtor search in a real Chrome over CDP (`ucc/chrome_cdp.py`) | Cloudflare blocks headless. Org + individual-owner searches; person filings keep the last 2 years and drop consumer lenders |
| NJ | Headless debtor search, parallel workers | Portal never returns the lender; deals show "not available" |
| PA | Lender search, then debtor search, in a real Chrome | Incapsula-challenged under volume; big debtor passes run in the person's own Chrome (`scripts/pa_ucc_browser.py`) |
| OH | The person's own Chrome only | Playwright is IP-blocked; `scripts/oh_ucc_browser.py` (debtor), `scripts/oh_ucc_lender_search.js` (lender) |
| CA | SOS weekly bulk data (`scripts/ca_ucc_import.py`) | Name search throttles at ~55 searches, so it's a 30-name backup |
| FL | Registry's official daily data files (`ucc/fl_download.py`) | No scraping |
| OR | Lender search in the person's own Chrome (`scripts/or_ucc_lender_search.js`) | Loaded with `scripts/ingest_lender_rows.py` |
| AR, NM | Headless lender search over a JSON API (`ucc/lien_portal.py`) | NM refuses after ~20 quick searches: 30 s apart, resumes where it stopped |
| RI | Headless lender search (`ucc/ri.py`) | Borrower read from each filing's history page |
| ID | Headless debtor search (`ucc/id.py`) | No lender mode; ~80 buyer and owner names |

**By hand:** AZ, CO, IN, MD, WV and NH (the 47 lenders) and ME (owner names) can't be automated within our rules: captcha, bot check, robots.txt or terms of use. Worklists are in `Manual/`; fill one in and load it with `scripts/ingest_manual_ucc.py` ([`docs/manual-ucc-searches.md`](docs/manual-ucc-searches.md)). IL and VT (captcha, debtor search only) and AK (robots.txt) are off. Every other state is in [`docs/ucc-feasibility.md`](docs/ucc-feasibility.md).

Run states separately (`--ucc-states KY`, or `--ucc-only --ucc-states AR,RI`); nothing commits until the whole UCC step returns. See [`docs/data-sources.md`](docs/data-sources.md) for search-name sourcing.

### CMS CHOW (confirmation)
Quarterly SNF change-of-ownership CSV. The download URL comes from CMS's data API, and `scraper/chow.py` also reads `data.cms.gov/data.json` and takes the newer file, since the API lags new releases. Every row is recorded in `chow_seen_records`, so each run only considers new rows. A buyer taking over several homes on the same day becomes one multi-facility deal; `scripts/chow_restore_lost.py` re-links records left without a deal. Latest file: `SNF_CHOW_2026.09.29.csv` (posted 2026-10-19); the next is due around January 2027.

### CMS ownership changes (confirmation, all states)
`scraper/cms_owner_changes.py` reloads the monthly CMS ownership file when it's more than 25 days old and turns new organization owners into deals (`extraction_model='cms_owner_change'`, shown as "Federal Record"). These catch closed sales about 1–2 months after closing.

### News and filings
- **Google Alerts** → dedicated Gmail inbox → Gmail API (`scraper/gmail_alerts.py`). Lookback widens automatically after a gap (up to 30 days).
- **RSS**: Skilled Nursing News, McKnight's (news feed, with a browser-impersonation fallback for 403s), Senior Housing News. Modern Healthcare's and Provider Magazine's feeds are dead (`active=False`).
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

# UCC only (skips Gmail/RSS/EDGAR/CHOW/state filings)
venv/bin/python3 main.py --no-alerts --ucc-only --ucc-states AR,NM,RI,ID

# Gmail alerts only; or override the Gmail lookback after a long gap
venv/bin/python3 main.py --no-alerts --gmail-only
venv/bin/python3 main.py --no-alerts --gmail-days-back 8

# Cron-safe wrapper: starts the DB container if needed, runs main.py, never touches git
./run_pipeline.sh
```

**Scheduling:** there is currently no scheduled job — the daily launchd job is intentionally disabled (plist kept in `.disabled-launchagents/`) and runs are manual. Three sources depend on regular runs: PA's licence applications are only listed during their comment window (runs ≤ a week apart), MS removes items 30 days after they complete (≤ ~6 weeks apart), and NJ's operator page only lists recent applications.

**Run health:** each run ends with a `RUN HEALTH` summary (`pipeline/run_health.py`) and exits with code **2** if a source failed outright or too many fetches/extractions failed; `SOURCE HEALTH` warnings (`pipeline/source_health.py`) flag sources that have gone quiet.

### Key behaviors
- **Extraction:** text articles go to Claude 8 at a time, capped at 50 per run (the cap bounds Claude cost; CHOW, UCC and table-based state filings don't count against it).
- **Two-layer dedup** (`pipeline/dedup.py`):
  - *Exact hash* — acquirer + states + month + facility count + value. Records with no buyer hash on their own ID instead (UCC filing number, state CON record ID); otherwise every buyer-less record in a state and month collided into one.
  - *Fuzzy pass* after each insert — fuzzy acquirer (≥85) + overlapping states + date within 30 days + similar facility count + a shared facility name. Keeps the more complete row and merges the other into it.
- **Same-sale merge** (`pipeline/same_sale.py`): one sale reported by two sources (say CHOW and the CMS ownership file) is merged automatically when the match is clear. Each merge is logged to `data/same_sale_merges.jsonl` and the dropped row is only marked dismissed (`merged_into`). Unclear pairs are left for review with `scripts/merge_same_sale.py`.
- **CMS matching** (`matcher/ownership.py`) sets the stage. A state-filing deal only counts as `confirmed` by a CMS ownership record starting no earlier than 30 days before the filing; older records belong to the seller.
- **Scope:** assisted living / memory care news deals are auto-dismissed (`pipeline/al_mc_scope.py`); state-filing deals skip that check since each source already restricts to nursing facilities.
- **Names** are cleaned for display by `pipeline/normalizer.py` (legal suffixes dropped; "Opco" kept on street-number names like "813 Keller Lane Opco").

---

## Deployment (gh-pages)

Deploy is a **separate, manual step from the pipeline run** — `run_pipeline.sh` never touches git.

`gh-pages` holds only the website (`index.html`, `assets/`, `classic/`, `deals.json`, `feed.xml`, `.nojekyll`); its `.gitignore` is an allowlist, so nothing else can be committed there. While it's checked out, the pipeline source isn't in the working copy, so don't run any Python until you're back on `main`.

```bash
# 1. On main: export deals.json (also rewrites UCC source links, fetches NJ portal
#    search tokens and derives deal types -- a raw psql export skips all of that)
venv/bin/python3 scripts/export_deals.py /tmp/deals.json   # also writes /tmp/feed.xml

# 1b. Compare with the live site; stops on a suspicious batch (new consumer
#     loans, filings against people, mass status/type/facility changes).
#     Review the report; --force only after you have
venv/bin/python3 scripts/predeploy_check.py /tmp/deals.json

# 2. Stash unrelated changes if they block the switch, then switch
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
# The build has a CSS file too since 2026-10-14 (src/tokens.css, src/app.css): copy both.
# Leave dist/feed.xml out -- gh-pages keeps its own from the data refresh.
git rm -q --ignore-unmatch assets/*.js assets/*.css && mkdir -p assets && cp /tmp/nh-dist/assets/*.js /tmp/nh-dist/assets/*.css assets/   # mkdir: removing the only bundle removes the folder
git add -A index.html assets/ && git commit -m "Frontend: <what changed>" && git push origin gh-pages
git checkout main
```

- Every push to `gh-pages` is a Pages deployment. Batch a frontend change with a data refresh into one push where you can.
- Before a frontend deploy, `cd dashboard/frontend && npm test` (Playwright + axe against a fixture, plus pixel comparisons; see `tests/`).
- After a frontend deploy, hard-refresh the site (Cmd+Shift+R) — browsers keep the old bundle cached.

**Branch rules:** `main` has the source and never deploy artifacts; `gh-pages` has only the site files above (its pipeline-code copy and `run_and_deploy.sh` were removed 2026-10-21; they're in its history). `data/` (local working files, some with personal names) and the credential files never go on either branch.

---

## Useful scripts

| Script | What it does |
|---|---|
| `scripts/export_deals.py <path>` | Export `deals.json` + `feed.xml` (positional path; `feed.xml` goes next to it) |
| `scripts/predeploy_check.py <export>` | Compare an export with the live site; stops on suspicious batches |
| `scripts/ingest_manual_ucc.py <STATE> <csv>` | Load a hand-filled UCC worklist from `Manual/` |
| `scripts/ingest_lender_rows.py` | Load lender-search results gathered in a browser (OR, OH) |
| `scripts/ca_ucc_import.py data/DataRequest*.zip` | Import California's weekly UCC bulk data |
| `scripts/{pa,oh,ca}_ucc_browser.py` | UCC debtor searches in the person's own Chrome |
| `scripts/merge_same_sale.py` | List or merge one sale stored twice |
| `scripts/chow_restore_lost.py` | Re-link CHOW records left without a deal |
| `scripts/reextract_news.py` | Re-extract news articles (headlines, bare deals, missing summaries) |
| `scripts/relink_cms_ucc.py` | Re-run UCC↔CMS matching from stored filings, no portal hits |
| `scripts/ucc_gap_check.py` | Find a state's deal names never covered by the UCC search lists |
| `scripts/enrich_ucc_facility_names.py` | Fill in facility names on UCC deals that only carry the debtor name |

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
psql "$DATABASE_URL" -f db/migration_add_cms_owner_change_seen.sql
psql "$DATABASE_URL" -f db/migration_add_deal_summary.sql
psql "$DATABASE_URL" -f db/migration_add_ucc_individual_matches.sql
psql "$DATABASE_URL" -f db/migration_add_deal_merged_into.sql
psql "$DATABASE_URL" -f db/migration_add_ca_entity_watch.sql
psql "$DATABASE_URL" -f db/migration_add_legal_names.sql
psql "$DATABASE_URL" -f db/migration_add_provider_address.sql
psql "$DATABASE_URL" -f db/migration_add_licence_list_snapshot.sql

# Python env (3.11+; runs on 3.14 — Google's API libraries dropped 3.10)
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
cd dashboard/frontend && npm test     # frontend: Playwright + axe + pixel comparisons
```

---

## Known limitations

- **No scheduled runs** (see Scheduling above) — the feed only updates when someone runs the pipeline.
- **UCC automation is fragile:** NY and PA need a real Chrome (not cloud-ready as-is); PA, OH and CA get bot-challenged or throttled under volume; OH runs only in the person's own Chrome; KY and OH rate-limit repeat same-day runs; seven states are searched by hand.
- **State-filing deals:** OK, MS and most MI records name no buyer; OK statuses and ME/MD cases aren't refreshed after they're first stored; MD dates come from upload month (late when documents are re-uploaded).
- **One sale, several entries:** a sale reported by news and by a state filing (or a UCC-1) can still appear as separate deals when the buyer names differ; fuzzy dedup and the same-sale merge only act on a clear match.
- **CMS data:** CHOW is quarterly and lags real closings by months; `cms_ownership_records` reloads when it's over 25 days old, but `cms_facilities` has no scheduled refresh (rerun `python3 -m cms.fetch_cms`).
- **Lender classifier is permissive by default:** unrecognized secured parties count as relevant, so new noise categories are only filtered once someone adds a pattern.
- **Fuzzy dedup only runs on new inserts;** there's no periodic full-table sweep.
- **Operator networks:** individual CMS owners feed NY/OH UCC searches, but nothing links an individual across facilities into an operator group.
- **Deployment** is manual, and every data refresh commits a ~9 MB `deals.json` to `gh-pages` history. Moving to GitHub Actions Pages deploys would fix the growth.
