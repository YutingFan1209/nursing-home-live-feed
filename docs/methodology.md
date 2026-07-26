# Methodology

Technical reference for developers who want to understand or replicate this pipeline. For source-by-source status and rationale, see [`docs/data-sources.md`](data-sources.md); for a plain-English overview, see [`docs/how-it-works.md`](how-it-works.md).

---

## CMS Dataset IDs and API Endpoints

CMS ownership and quality data come from the **Provider Data Catalog** (`data.cms.gov/provider-data/...`), not the older enrollment-system "All Owners" API — that dataset is keyed by PECOS Enrollment ID and can't be joined to a facility's CCN or state.

### Ownership (`cms_ownership_records`)
- Metastore dataset item: **`y2hd-n93e`**
- Discovery endpoint: `https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/y2hd-n93e` — resolves to the current month's `NH_Ownership_*.csv` `downloadURL`. The direct CSV URL embeds a rotating content hash and can't be hardcoded reliably.
- Fallback: a last-known-good URL constant (`OWNERSHIP_CSV_FALLBACK_URL`) if the metastore lookup fails.
- Loader: `cms/fetch_cms.py:load_ownership`

### Provider Information / Care Compare (`cms_facilities`)
- Metastore dataset item: **`4pq5-n9py`**
- Discovery endpoint: `https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/4pq5-n9py` — same rotating-URL pattern, resolves to the current `NH_ProviderInfo_*.csv`.
- Loader (active path): `matcher/carecompare.py:load_care_compare`
- **Dead code warning:** `cms/fetch_cms.py` also defines a `load_care_compare` that hits the old SODA API (`config.cms_api_base` = `https://data.cms.gov/resource`, dataset `4pq5-n9py`, i.e. `https://data.cms.gov/resource/4pq5-n9py.json`). This function is always shadowed by the `matcher.carecompare` import inside `cms/fetch_cms.py:fetch_and_load_all()` and is never actually called. Likewise, `config.cms_ownership_dataset = "qhpq-qrm6"` in `config.py` is unreferenced anywhere in the codebase — the real ownership dataset ID is `y2hd-n93e`, discovered via the metastore endpoint above, not this config field.

### CHOW (`scraper/chow.py`)
No metastore lookup — CHOW files are published as static quarterly CSVs with no stable discovery endpoint, so the URLs are hardcoded in `CHOW_URLS` (most recent first, loader tries each until one succeeds) and must be updated manually every quarter:
```
https://data.cms.gov/sites/default/files/2026-01/900cec56-f1c8-40cb-9f8a-bf54cae53b90/SNF_CHOW_2026.01.02.csv
```
Check for the current file at: `https://catalog.data.gov/dataset/skilled-nursing-facility-change-of-ownership`

---

## Per-State UCC Search Logic

UCC-1 search terms come from two sources, both used to seed searches with names likely to belong to nursing home operators or owners, since the state portals require a debtor/secured-party name rather than supporting a keyword search:

- **`scraper/chow.py:get_chow_operator_names(state)`** — pulls all unique CHOW buyer names (`ORGANIZATION NAME - BUYER`) for a given state, no date filter. Used to seed states where CHOW data predates the pipeline's own `deals` table.
- **`main.py:_get_cms_individual_owner_names(conn, state)`** — pulls individual (non-organization) owner names from `cms_ownership_records` for a state, filtered to ownership/control roles only (`main.py:_CMS_OWNERSHIP_RELEVANT_ROLES`: 5%+ direct/indirect ownership, direct/indirect ownership, general/limited partnership interest, operational/managerial control, managing control - governing body). This is the only source that surfaces *individual* beneficial owners — CHOW and deal-derived names are almost always entity names.
- **`main.py:_get_known_operator_names(conn)`** — pulls entity-style names (matching an `LLC|INC|CORP|LTD|LP|LLP|HOLDINGS|GROUP|CARE|HEALTH|MANAGEMENT|ASSOCIATES|SERVICES|CENTER|PARTNERS|TRUST|ACQUISITION|OPERATING` regex) from the `deals` table itself (`operator_names` and `acquiring_entity`).

| State | Search field | Term sources | Module |
|---|---|---|---|
| NY | Debtor name — **Organization mode** and **Individual mode** (separate form flows) | Org: `_get_known_operator_names()`. Individual: `_get_cms_individual_owner_names(conn, "NY")` | `ucc/ny_playwright.py` — portal: `ucc-efiling.dos.ny.gov/OnlineUCCSearch` |
| KY | Debtor name | `get_chow_operator_names("KY")` | `ucc/ky_playwright.py` — portal: `web.sos.ky.gov/ftucc/search.aspx` (ASP.NET WebForms) |
| OH | Secured party (+ individual debtor mode) | `get_chow_operator_names("OH")` for org search; `_get_cms_individual_owner_names(conn, "OH")` for individual mode (names come as `"LAST, FIRST"` from CMS and are split for the portal's separate first/last fields) | `ucc/oh_playwright.py` — portal: `ucc.ohiosos.gov` (Angular Material); `headless=False` + hidden-window trick locally, needs an Xvfb wrapper in the cloud |
| PA | Secured party, via JSON API (`POST /api/Records/uccsearch`) | Manual — requires a live Chrome instance with `--remote-debugging-port=9222`, connected to via CDP (`connect_over_cdp`) to bypass Cloudflare | `ucc/pa_playwright.py` — portal: `file.dos.pa.gov` |

Every filing that reaches the DB has first been classified by `ucc/lender_classifier.py` (see below), then routed by `ucc/integrator.py:route_filing()` — a fuzzy match (`rapidfuzz.fuzz.token_sort_ratio` ≥ `NAME_MATCH_THRESHOLD = 85`, within `DATE_WINDOW_DAYS = 180` of the filing date) against operator/facility names of existing deals decides whether the filing **confirms** an existing deal (sets `deals.ucc_confirmed = true`) or seeds a **new** preliminary deal.

---

## Lender Classifier

`ucc/lender_classifier.py:classify_secured_party(name)` scores a UCC secured-party name in four layered passes, returning a `LenderClassification(category, confidence, matched_signal, is_acquisition_relevant)`:

1. **Known-entity lookup** (confidence 0.9–0.95) — `KNOWN_HEALTHCARE_REITS` (CareTrust, Omega, Sabra, Ventas, Welltower, HUD/FHA, etc.) and `KNOWN_PE_SPONSORS` (Formation Capital, Portopiccolo Group, Genesis Healthcare, etc.)
2. **`EXCLUDE_PATTERNS`** (confidence 0.85, checked *before* generic keyword matching) — regex patterns for near-certain non-acquisition filers: equipment/vendor finance (Stryker, Karl Storz, Zimmer, Olympus, Xerox, Ricoh, Canon Financial, Dell/HP Financial, etc.), pharmacy/dialysis suppliers, registered agents (CT Corporation System, Corporation Service Company), personal/consumer lenders (credit unions, "FCU"), tax authorities (IRS, state tax departments), farm/construction equipment financiers (Kubota, AGCO, New Holland), and mortgage/bond entities unrelated to healthcare RE (MERS, master trustees). The list has been extended twice via a "runtime patch" block appended after the main list — see the bottom of the file.
3. **Keyword/suffix heuristics** (confidence 0.6) — `RE_SUFFIX_PATTERNS` (`reit`, `real estate`, `realty`, `properties`, `property (holdings|trust|group)`) and `PE_SUFFIX_PATTERNS` (`capital partners`, `equity partners`, `capital management`, `private equity`, `investment partners`, `holdings, lp`, `fund [ivx]+`).
4. **Generic bank fallback** (confidence 0.3) — matches `bank|banking|n\.?a\.?` — flagged as `BANK_GENERAL` for human review rather than auto-classified either way.
5. Anything unmatched falls through to `UNKNOWN` with `is_acquisition_relevant = True` by design — **the classifier is permissive by default**: an unrecognized lender is treated as a possible acquisition signal rather than silently dropped, so new noise categories only get filtered once someone notices them and adds an exclude pattern.

`to_confidence_label()` maps a classification to the `HIGH`/`MEDIUM`/`LOW` string stored in `ucc_filings.confidence`.

---

## Fuzzy Matching

`matcher/ownership.py` matches an extracted deal against `cms_ownership_records` using a stdlib-only token-set scorer (`_difflib_score`), weighted 70% word-intersection / 30% character-sequence ratio, computed after stripping trailing commas (so CMS's `"LAST, FIRST"` owner format tokenizes the same as `"FIRST LAST"`) and generic facility-type words.

**Thresholds** (`config.py`):
- `fuzzy_match_threshold = 70` — minimum score for a match to be recorded at all; also the floor for `stage = 'pending_cms'`.
- `85` — hardcoded in `determine_stage()`: a top match score ≥ 85 promotes a deal to `stage = 'confirmed'`, `confidence = 'high'`.
- `OWNER_ONLY_MATCH_FACILITY_FLOOR = 40` — an owner-name-only match (no corroborating facility-name evidence against the *same* CMS record) is discarded unless that record's facility-name score is at least 40. Prevents an owner with many facilities from being pinned to an arbitrary one of them.

**`FACILITY_STOPWORDS`** (stripped from both sides before scoring, so two unrelated facilities don't score high purely on shared boilerplate):
```python
{
    "nursing", "home", "rehabilitation", "rehab", "center",
    "centre", "senior", "living", "health", "care", "facility",
    "services", "and", "of", "the", "at", "manor", "house",
    "residence", "community", "communities", "skilled",
}
```

Candidate CMS records are pre-filtered by state and, for non-UCC deals, a ±180-day window around the deal's `acquisition_date` (`matcher/ownership.py:_date_window`). UCC-sourced deals skip the date filter entirely — a UCC-1 filing date has no fixed relationship to CMS's recorded `ownership_start_date` (renewals/continuations happen independently of CMS's own recording), so UCC candidates are filtered on state alone, with no `LIMIT`/`ORDER BY` to avoid dropping older records in high-volume states.

---

## Pipeline Execution Order

`main.py:run()`, invoked via `python3 main.py`:

1. **Discover** (`discover_articles`) — in order: RSS feeds → EDGAR full-text search → CHOW CSV → Gmail alerts (OAuth, lookback auto-scaled from `sources.last_fetched_at`, capped at 30 days) → UCC-1 filings (NY/KY/OH/PA, cap-exempt). `--skip-ucc` and `--gmail-only` short-circuit parts of this step.
2. **Extract & store**, in three sub-phases:
   - 2a. UCC filings — serial, no Claude call, routed via `ucc/integrator.py:route_filing()` (confirmation vs. new signal vs. excluded).
   - 2b. CHOW deals — serial, pre-extracted from the CSV directly (no Claude call).
   - 2c. Text articles (RSS/EDGAR/Gmail) — fetched and run through Claude extraction (`pipeline/extractor.py:extract_deals`) concurrently (`asyncio.gather`, semaphore-limited to 8), with DB writes done serially afterward since `psycopg2` isn't async-safe. Capped at `max_articles_per_run` (default 50); UCC articles are exempt from this cap.

   Every stored deal is deduplicated (`pipeline/dedup.py`, exact hash + fuzzy semantic pass) and checked against `pipeline/al_mc_scope.py:is_out_of_scope()` — AL/MC deals are auto-dismissed (`stage = 'dismissed'`) before CMS matching runs.
3. **Re-check pending deals** (`recheck_pending`) — deals in `stage IN ('detected', 'pending_cms')` past their `recheck_after` date and under `recheck_max_attempts` (12) get re-matched against current CMS data.
4. **Send digest** (`alerts/digest.py:send_daily_digest`) — skipped with `--no-alerts`.

CMS matching itself (`main.py:_run_cms_matching`) is shared across steps 2 and 3: CHOW deals get a direct CCN match (`_build_known_ccn_match`, since CHOW already reports the exact CCN) instead of fuzzy matching; everything else goes through `matcher/ownership.py:match_deal`, then `matcher/carecompare.py:enrich_matches` + `flag_policy_risks`, then `determine_stage`.

---

## Database Tables

Full schema: `db/schema.sql` (+ migrations in `db/migration_*.sql`, applied in the order listed in the README). Key tables:

| Table | Purpose |
|---|---|
| `sources` | Registered discovery sources (RSS/EDGAR/CHOW/UCC/manual) and their last-fetch timestamps. |
| `articles` | Raw scraped articles/filings prior to deal extraction — one row per source URL. |
| `deals` | The core entity: one extracted acquisition/financing event, with `stage` (`detected` → `pending_cms` → `confirmed`/`unresolved`/`dismissed`, plus `verified`), `confidence`, `dedup_hash`, and recheck bookkeeping. One article can yield multiple deals. |
| `cms_facilities` | Normalized CMS Care Compare provider records (quality ratings, bed count, SFF flags) — refreshed from the Provider Information dataset. |
| `cms_ownership_records` | Raw CMS ownership records (owner name/type/role, CCN, state, association date) — the fuzzy-matching target for `matcher/ownership.py` and the individual-owner-name source for NY/OH UCC search seeding. |
| `cms_matches` | Join table linking a `deals` row to the `cms_ownership_records`/`cms_facilities` rows it matched, with `match_score`/`match_method`/`matched_on_field`. One deal can have multiple matches (multi-facility portfolios). |
| `ucc_filings` | Raw UCC-1 filings pulled from state portals, with the lender classifier's `confidence` label and the `query_name` (search term) that surfaced each filing. |
| `annotations` | Free-text researcher notes on a deal, with an optional `tag`. |
| `alert_log` | Tracks which deals have already been included in an email digest, to prevent duplicate alerts. |

Notable views: `deal_summary` (deal + article + source + match/annotation counts, used by the dashboard), `pending_recheck`, `unalerted_confirmed`.

---

## Environment Variables

See `.env.example` for the authoritative template. Required/relevant variables:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string (`postgresql://user:password@host:5432/dbname`). |
| `ANTHROPIC_API_KEY` | Claude API key, used by `pipeline/extractor.py` for deal extraction from article text. |
| `SENDGRID_API_KEY`, `ALERT_FROM_EMAIL`, `ALERT_TO_EMAILS` | Email digest delivery (`alerts/digest.py`). |
| `ARCHIVE_BUCKET` | Optional S3/GCS bucket for raw article archival; leave blank to skip archiving. |
| `EDGAR_CONTACT_EMAIL` | Required by SEC EDGAR's fair-use policy — requests without a real contact email in the User-Agent get blocked. |
| `ALLOWED_ORIGINS` | CORS allowlist for the dashboard API. |
| `VITE_FACILITY_BASE_URL` | Frontend build-time var — deals with a CCN link to `{VITE_FACILITY_BASE_URL}/{CCN}`. |
| `FUZZY_MATCH_THRESHOLD`, `RECHECK_INTERVAL_DAYS`, `MAX_ARTICLE_AGE_DAYS` | Optional overrides for `config.py` defaults (commented out by default in `.env.example`). |

Gmail OAuth (`gmail_credentials.json` / `gmail_token.json`) and the Anthropic API key are read directly by their respective modules rather than through additional env vars — see the README's Setup section for the one-time interactive OAuth flow.

---

## Running Locally

```bash
# 1. Start Postgres (Docker, Postgres 15)
docker start nh-test-db
# or: docker run --name nh-test-db -e POSTGRES_PASSWORD=testpass -p 5432:5432 -d postgres:15

# 2. Schema + migrations, in this exact order
psql "$DATABASE_URL" -f db/schema.sql
psql "$DATABASE_URL" -f db/migration_add_ucc_confirmed.sql
psql "$DATABASE_URL" -f db/migration_ownership_associate_id.sql
psql "$DATABASE_URL" -f db/migration_ownership_switch_source.sql

# 3. Python environment
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install          # browser binaries for the UCC scrapers

# 4. Credentials
cp .env.example .env        # fill in DATABASE_URL, ANTHROPIC_API_KEY, etc.
# Gmail OAuth: create a Google Cloud OAuth client (Desktop app type), save the
# client secret as gmail_credentials.json, then run any command that touches
# Gmail alerts once interactively to generate gmail_token.json.

# 5. Load CMS reference data (ownership + Care Compare) — no scheduled
#    refresh is wired up, so rerun periodically to keep it current
venv/bin/python3 -m cms.fetch_cms

# 6. Run the pipeline
venv/bin/python3 main.py --no-alerts              # full run
venv/bin/python3 main.py --no-alerts --skip-ucc    # skip UCC (~1 min vs ~90 min)
venv/bin/python3 main.py --test-article <URL>      # single-article dry run, prints extraction + matches, writes nothing
```

`run_pipeline.sh` is the cron-safe wrapper (starts the DB container if needed, runs `main.py`, never touches git) — see `CLAUDE.md` for the branch model and deploy steps (`main` holds source only; `gh-pages` is a separate, manually-updated deploy target with its own divergent copy of the Python source).

AWS deployment (Lambda + RDS + EventBridge) is designed but not yet deployed — see `infra/eventbridge.tf` for the Terraform config (daily EventBridge Scheduler trigger, containerized Lambda, 15-minute timeout) and `infra/cloudrun.yaml` for an alternative GCP Cloud Run Jobs config.
