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
- Dataset UUID **`1e1afa09-5699-46a5-ae9e-47017397c55c`** on CMS's newer data platform. Discovery endpoint: `https://data.cms.gov/data-api/v1/dataset/1e1afa09-5699-46a5-ae9e-47017397c55c/resources` — its `Primary` resource's `file_url` is the current `SNF_CHOW_YYYY.MM.DD.csv` (2026-07-17 as of this writing).
- Fallback: `CHOW_URLS`, a hand-maintained list of last-known-good CSV URLs, only used if the discovery API fails.
- **Freshness:** every row's `(ccn, buyer_name, effective_date)` key is recorded in `chow_seen_records` whether or not it becomes a deal, and each run only considers keys not seen before; of those, rows with an effective date within `CHOW_RECENCY_DAYS` (730) become deals. This replaced a 90-day effective-date window that never matched anything, because a CHOW file's newest effective date trails its release by months (the July 2026 file tops out at 2026-02-01) — CHOW had produced zero deals for the pipeline's whole life until 2026-09-22.

---

## Per-State UCC Search Logic

UCC-1 search terms come from two sources, both used to seed searches with names likely to belong to nursing home operators or owners, since the state portals require a debtor/secured-party name rather than supporting a keyword search:

- **`scraper/chow.py:get_chow_operator_names(state)`** — pulls all unique CHOW buyer names (`ORGANIZATION NAME - BUYER`) for a given state, no date filter. Used to seed states where CHOW data predates the pipeline's own `deals` table.
- **`main.py:_get_cms_individual_owner_names(conn, state)`** — pulls individual (non-organization) owner names from `cms_ownership_records` for a state, filtered to ownership/control roles only (`main.py:_CMS_OWNERSHIP_RELEVANT_ROLES`: 5%+ direct/indirect ownership, direct/indirect ownership, general/limited partnership interest, operational/managerial control, managing control - governing body). This is the only source that surfaces *individual* beneficial owners — CHOW and deal-derived names are almost always entity names.
- **`main.py:_get_known_operator_names(conn)`** — pulls entity-style names (matching an `LLC|INC|CORP|LTD|LP|LLP|HOLDINGS|GROUP|CARE|HEALTH|MANAGEMENT|ASSOCIATES|SERVICES|CENTER|PARTNERS|TRUST|ACQUISITION|OPERATING` regex) from the `deals` table itself (`operator_names` and `acquiring_entity`).

| State | Search field | Term sources | Module |
|---|---|---|---|
| NY | Debtor name — **Organization mode** and **Individual mode** (separate form flows) | Org: `get_chow_operator_names("NY")` unioned with `_get_known_operator_names()`. Individual: `_get_cms_individual_owner_names(conn, "NY")` | `ucc/ny_playwright.py:search_ny_batch_cdp` — portal: `ucc-efiling.dos.ny.gov/OnlineUCCSearch`. Cloudflare Turnstile blocks headless browsers, so it drives a real Chrome over CDP (`ucc/chrome_cdp.py` launches one if needed), parallel tabs |
| KY | Debtor name | `get_chow_operator_names("KY")` unioned with `_get_known_operator_names()` | `ucc/ky_playwright.py` — portal: `web.sos.ky.gov/ftucc/search.aspx` (ASP.NET WebForms). Headless; rate-limits a second full run on the same day |
| OH | Secured party (+ individual debtor mode) | `get_chow_operator_names("OH")` for org search; `_get_cms_individual_owner_names(conn, "OH")` for individual mode (names come as `"LAST, FIRST"` from CMS and are split for the portal's separate first/last fields) | `ucc/oh_playwright.py` — portal: `ucc.ohiosos.gov` (Angular Material). Blocked for Playwright since 2026-09 (IP block, then 429s under volume); searches have been run through a real Chrome session instead |
| PA | Secured party, via JSON API (`POST /api/Records/uccsearch`) | `_get_known_operator_names()` (no PA-specific list) | `ucc/pa_playwright.py` — portal: `file.dos.pa.gov`. Real Chrome over CDP, auto-launched by `ucc/chrome_cdp.py` (no longer manual); Incapsula challenges large batches partway through |
| NJ | Debtor name only (the portal never returns the secured party) | `get_chow_operator_names("NJ")` (~141 names), falling back to `_get_known_operator_names()` | `ucc/nj_playwright.py:search_nj_batch` — headless, 8 parallel workers. Lender set to an explicit "not available" placeholder; `classify_secured_party` treats NJ's blank secured party as maybe-relevant |

Every filing that reaches the DB has first been classified by `ucc/lender_classifier.py` (see below), then routed by `ucc/integrator.py:route_filing()` — a fuzzy match (`rapidfuzz.fuzz.token_sort_ratio` ≥ `NAME_MATCH_THRESHOLD = 85`, within `DATE_WINDOW_DAYS = 180` of the filing date) against operator/facility names of existing deals decides whether the filing **confirms** an existing deal (sets `deals.ucc_confirmed = true`) or seeds a **new** preliminary deal.

---

## State Pre-Closing Filings (`scraper/con_*.py`)

Eight state readers, all producing articles with `source_type = 'con'`. Two paths, like the rest of the pipeline: **table-based** states are parsed deterministically into pre-extracted deals (no Claude call), and **document-based** states put the filing's text into `raw_text` for the regular Claude extractor, with a header telling it the filing's scope (e.g. "one facility; buyer = the proposed licensee"). Per-state detail and quirks are in [`data-sources.md`](data-sources.md); the research is in [`con-feasibility.md`](con-feasibility.md).

| State | Index | Format | Nursing-home test | Record key | Claude? |
|---|---|---|---|---|---|
| AL | SHPDA change-of-ownership notices page | One PDF per notice | SHPDA facility ID type letter `N` (`017-N0003`) | PDF URL | Yes |
| OK | OSDH Health Facility Systems page → monthly "Notice" PDFs | PDF table | All are nursing/specialized facility CNs; keeps acquisition and `-372` ownership types | CN number | No |
| ME | DHHS Current Healthcare Reviews (+ prior year) | HTML case list → PDFs | "Nursing Facility Reviews" section | First document URL | Yes, once per case |
| MI | MDHHS CON activity reports, page per year | XLSX (from 2026-03) / PDF | Facility ID `NN-4xxx`; description filter | CON ID | No |
| MS | MSDH CON weekly reports (+ archive) | PDF table | "Nursing Home" type or long-term-care name | Name's first word + place + date | No |
| NC | DHSR No Reviews and Exemptions (+ `archive<YEAR>.html`) | HTML table | County + name vs DHSR licensed NH list, then CMS names, then name words | Exemption number | No |
| MD | MHCC Nursing Home Acquisition Applications | HTML case list → PDFs | Nursing-home-only page | First document URL | Yes, once per case |
| NJ | DOH LTC transfer + real estate transfer pages | HTML tables → PDFs | Nursing-home-only pages | Application no. / facility + date | Operator transfers yes; real estate no |

Shared pieces:
- **Scanned PDFs** — `pipeline/pdf_text.py:pdf_text` uses the PDF's text layer when it has at least 1,500 characters in the first pages, otherwise sends those pages to Claude as a base64 PDF document block and uses the transcription.
- **Dedup key** — pre-extracted CON deals carry `_con_id`, and `make_dedup_hash` hashes `con|<state>|<id>` instead of acquirer/state/month (buyer-less records would otherwise collide by month).
- **Dates** — a document-based deal whose filing states no closing date gets the filing date as `acquisition_date` (`main._default_con_dates`).
- **Scope** — CON deals skip `is_out_of_scope()`; each reader already restricts to nursing facilities.
- **Confirmation** — see the CON rule under Fuzzy Matching.

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

**State-filing (CON) deals:** `main._con_confirming_matches` passes only matches whose `ownership_start_date` is no earlier than 30 days before the filing date to `determine_stage()`. All matches are still stored in `cms_matches` (so the facility link works), but a CMS record that predates the filing belongs to the seller and can't confirm the sale. (Audit 2026-09-30: 7 of 28 "confirmed" CON deals rested only on such records.)

Candidate CMS records are pre-filtered by state and, for non-UCC deals, a ±180-day window around the deal's `acquisition_date` (`matcher/ownership.py:_date_window`). UCC-sourced deals skip the date filter entirely — a UCC-1 filing date has no fixed relationship to CMS's recorded `ownership_start_date` (renewals/continuations happen independently of CMS's own recording), so UCC candidates are filtered on state alone, with no `LIMIT`/`ORDER BY` to avoid dropping older records in high-volume states.

---

## Pipeline Execution Order

`main.py:run()`, invoked via `python3 main.py`:

1. **Discover** (`discover_articles`) — in order: RSS feeds → EDGAR full-text search → CHOW CSV → state pre-closing filings (AL, OK, ME, MI, MS, NC, MD, NJ) → Gmail alerts (OAuth, lookback auto-scaled from `sources.last_fetched_at`, capped at 30 days) → UCC-1 filings (NY/KY/OH/PA/NJ, cap-exempt; `--ucc-states` restricts which). `--skip-ucc` and `--gmail-only` short-circuit parts of this step.
2. **Extract & store**, in three sub-phases:
   - 2a. UCC filings — serial, no Claude call, routed via `ucc/integrator.py:route_filing()` (confirmation vs. new signal vs. excluded).
   - 2b. Pre-extracted deals — CHOW rows and table-based state filings (OK, MI, MS, NC, NJ real estate), serial, no Claude call. A semantic-dedup unique-index collision is rolled back to a savepoint and skipped rather than aborting the run.
   - 2c. Text articles (RSS/EDGAR/Gmail, plus document-based state filings: AL, ME, MD, NJ operator transfers) — fetched and run through Claude extraction (`pipeline/extractor.py:extract_deals`) concurrently (`asyncio.gather`, semaphore-limited to 8), with DB writes done serially afterward since `psycopg2` isn't async-safe. Capped at `max_articles_per_run` (default 50); UCC and pre-extracted articles are exempt from this cap.

   Every stored deal is deduplicated (`pipeline/dedup.py`, exact hash + fuzzy semantic pass) and, unless it's a state filing, checked against `pipeline/al_mc_scope.py:is_out_of_scope()` — AL/MC deals are auto-dismissed (`stage = 'dismissed'`) before CMS matching runs.
3. **Re-check pending deals** (`recheck_pending`) — deals in `stage IN ('detected', 'pending_cms')` past their `recheck_after` date and under `recheck_max_attempts` (12) get re-matched against current CMS data.
4. **Send digest** (`alerts/digest.py:send_daily_digest`) — skipped with `--no-alerts`.
5. **Health report** — `pipeline/source_health.py` warns about sources that have gone quiet; `pipeline/run_health.py` logs a `RUN HEALTH` summary and makes the process exit with code 2 if a source failed or too many fetches/extractions failed.

CMS matching itself (`main.py:_run_cms_matching`) is shared across steps 2 and 3: CHOW deals get a direct CCN match (`_build_known_ccn_match`, since CHOW already reports the exact CCN) instead of fuzzy matching; everything else goes through `matcher/ownership.py:match_deal`, then `matcher/carecompare.py:enrich_matches` + `flag_policy_risks`, then `determine_stage`.

---

## Database Tables

Full schema: `db/schema.sql` (+ migrations in `db/migration_*.sql`, applied in the order listed in the README). Key tables:

| Table | Purpose |
|---|---|
| `sources` | Registered discovery sources (`source_type` rss / edgar / googlenews / manual / ucc / chow / con) and their last-fetch timestamps. |
| `articles` | Raw scraped articles/filings prior to deal extraction — one row per source URL. |
| `deals` | The core entity: one extracted acquisition/financing event, with `stage` (`detected` → `pending_cms` → `confirmed`/`unresolved`/`dismissed`, plus `verified`), `confidence`, `dedup_hash`, and recheck bookkeeping. One article can yield multiple deals. |
| `cms_facilities` | Normalized CMS Care Compare provider records (quality ratings, bed count, SFF flags) — refreshed from the Provider Information dataset. |
| `cms_ownership_records` | Raw CMS ownership records (owner name/type/role, CCN, state, association date) — the fuzzy-matching target for `matcher/ownership.py` and the individual-owner-name source for NY/OH UCC search seeding. |
| `cms_matches` | Join table linking a `deals` row to the `cms_ownership_records`/`cms_facilities` rows it matched, with `match_score`/`match_method`/`matched_on_field`. One deal can have multiple matches (multi-facility portfolios). |
| `ucc_filings` | Raw UCC-1 filings pulled from state portals, with the lender classifier's `confidence` label, the `query_name` (search term) that surfaced each filing, and `detail_url` where the portal supports a deep link. |
| `chow_seen_records` | Every `(ccn, buyer_name, effective_date)` key ever seen in a CHOW CSV, so each run only considers new rows (see CHOW above). |
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

# 2. Schema + migrations: run db/schema.sql, then every db/migration_*.sql
#    in the order listed in the README's Setup section

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
venv/bin/python3 main.py --no-alerts --skip-ucc    # skip UCC (~1 min vs hours)
venv/bin/python3 main.py --no-alerts --ucc-states KY   # UCC for one state
venv/bin/python3 main.py --test-article <URL>      # single-article dry run, prints extraction + matches, writes nothing
```

`run_pipeline.sh` is the cron-safe wrapper (starts the DB container if needed, runs `main.py`, never touches git; no scheduled job currently runs it) — see `CLAUDE.md` for the branch model and deploy steps (`main` holds source only; `gh-pages` is a separate, manually-updated deploy target with its own divergent copy of the Python source).

AWS deployment (Lambda + RDS + EventBridge) is designed but not yet deployed — see `infra/eventbridge.tf` for the Terraform config (daily EventBridge Scheduler trigger, containerized Lambda, 15-minute timeout) and `infra/cloudrun.yaml` for an alternative GCP Cloud Run Jobs config.
