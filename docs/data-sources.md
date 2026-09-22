# Data Sources

## Overview

The pipeline uses two categories of data sources: **discovery** sources that find
new deals to process, and **CMS reference datasets** used for matching and
enrichment, not discovery. Each discovery source has different coverage, speed,
and reliability characteristics.

---

## Discovery Sources

### SEC EDGAR Full-Text Search (free)
**URL:** `https://efts.sec.gov/LATEST/search-index`
**Coverage:** Public companies only (REITs, large operators like Ensign, Genesis)
**Speed:** Days before close — companies must file 8-K within 4 business days
**Reliability:** High — legally required filing
**Best for:** Welltower, Sabra, CareTrust, Omega, Ensign, Genesis portfolio deals
**Limitation:** Private operators (family-owned, small regional chains) never file with SEC

### SNF CHOW Dataset (free)
**URL:** `https://data.cms.gov/sites/default/files/...SNF_CHOW_YYYY.MM.DD.csv`
**Coverage:** ALL Medicare-certified SNFs — public and private
**Speed:** Quarterly updates (Jan, Apr, Jul, Oct)
**Reliability:** Highest — federally required, CMS-verified completed transactions
**Best for:** Private operator deals, comprehensive confirmed ownership record
**Limitation:** Quarterly cadence means up to 3 month lag on new deals

### Google Alerts (Gmail OAuth) — working
**Mechanism:** Google Alerts emails → dedicated Gmail inbox → read via Gmail API (OAuth), parsed in `scraper/gmail_alerts.py`
**Coverage:** Whatever news/blog/press coverage Google's alert matching surfaces — public and private deals
**Speed:** Same day as announcement, often faster than RSS
**Reliability:** Medium — announced deals sometimes fall through; depends on Google's alert matching
**Lookback window:** Auto-scales from `sources.last_fetched_at` (capped at 30 days) to cover any gap since the last run, so a missed cron day doesn't silently drop emails — see `main.py:discover_articles`. Override with `--gmail-days-back N` for manual backfill.
**Setup:** Requires a Google Cloud OAuth client (`gmail_credentials.json`) and a one-time interactive auth to generate `gmail_token.json` — see README setup section.
**Note:** `scraper/sources.py` also registers the Google Alerts *RSS* feed export URL directly as an `rss`-type source — this is a second, redundant path to the same alerts and may be stale/unreliable since Google's public RSS export for Alerts is not the supported integration point. The Gmail OAuth path is the primary, actively-maintained mechanism.

### News RSS / Trade Press — working
**Sources:** Skilled Nursing News, McKnight's Long-Term Care News, Modern Healthcare (Post-Acute), Provider Magazine, Senior Housing News — registered in `scraper/sources.py`
**Coverage:** Public and private deals, announced deals (may not close)
**Speed:** Same day as announcement
**Reliability:** Medium — announced deals sometimes fall through
**Status:** All five feeds are live and producing deals (no API key or subscription required)

### State UCC-1 Filings — primary acquisition signal
**Mechanism:** Headless-browser scrapers (Playwright), one per state portal, in `ucc/`. UCC-1 financing statement filings are an early signal — often filed before a deal is publicly announced or shows up in CHOW.
**Coverage:** NY, KY, OH, PA (see table below)
**Speed:** Fastest of any source — filings can precede public announcement entirely
**Reliability:** High signal, but noisy — the `lender_classifier.py` module pre-filters equipment/vendor/personal-financing filings before they enter the extraction queue (permissive by default; unrecognized lenders are treated as relevant rather than dropped)
**Cap-exempt:** UCC articles bypass the standard 50-articles/run cap since they're fast (no Claude call needed)

| State | Search type | Automation | Notes |
|---|---|---|---|
| NY | Debtor name — both Organization mode (org search seeded from CHOW-derived NY facility LLCs, `ny_search_names`) and Individual mode (CMS owner names) | Automated, but **not headless / not AWS-ready** | As of ~2026-09 the portal added a Cloudflare Turnstile challenge that headless Chrome never passes (confirmed 2026-09-15). `ucc/ny_playwright.py:search_ny_batch_cdp` works around it by driving a real, non-headless Chrome over CDP (`_ensure_chrome_cdp` auto-launches one with a persistent profile if needed) — same technique as PA/CA below, so it needs a real machine, not a plain Lambda/headless container. Runs org + individual terms across parallel tabs sharing that Chrome's Cloudflare clearance cookie (`max_workers`, default 4; 8 tested clean). Individual-name list alone can be 1,000+ names — full runtime still multi-hour even parallelized. Also confirmed 2026-09-15: relying on the generic national operator list instead of a NY-specific facility list silently missed nearly all real hits — fixed via `ny_search_names`. |
| KY | Debtor name (seeded from CHOW CSV operator names) | Fully automated, headless | `ucc/ky_playwright.py`. Confirmed same-day rate limit: a second full-volume run within a few hours of the first gets TLS-reset-blocked — don't run KY twice in one day. |
| OH | Debtor + secured party | Automated locally, `headless=False` + hidden-window trick | `ucc/oh_playwright.py` — needs an Xvfb wrapper to run headless in the cloud. Fragile under sustained volume: hit a full IP ban in 2026-09 (later lifted) and a server-side 429 rate limit on 2026-09-15 after repeated same-day batches — re-verify with a single-name probe before trusting a big batch, and don't stack multiple large OH runs in one day. |
| PA | Secured party | Automated, but fragile under volume | Confirmed 2026-09-16: doesn't need a human-driven browser session (`ensure_chrome_cdp` + self-navigate, same fix as NY) — but a 237-name/4-worker batch got Incapsula-challenged partway through (17 successes then mass JSON-parse failures as the API started returning an HTML challenge page instead), same pattern as OH. Single-name probes succeeding doesn't mean a batch will get through. Still uses the generic national operator list rather than a PA-specific facility name list, and has no known per-filing deep link yet. |

**Running UCC states:** `main.py --ucc-states NY,KY,OH` (or any subset) restricts the UCC step to just those states. Prefer running states separately (one cron/launchd job per state) rather than bundled — nothing commits to the DB until the whole UCC fetch call returns, so one state hanging or getting blocked loses every other state's already-good work for that run too. This bit hours off a run on 2026-09-15 when NY's multi-hour individual-name phase was still going when OH hit its 429.

**Not yet integrated:** NJ was explored (`test_nj_ucc.py`, a root-level scratch script) but has no `ucc/nj_*.py` module and isn't wired into `main.py`.

---

## CMS Reference Datasets
These are used for matching and enrichment, not discovery. Both are pulled from
the **Provider Data Catalog** (`data.cms.gov/provider-data/...`), not the older
enrollment-system "All Owners" API — that dataset is keyed by PECOS Enrollment ID
and can't be joined to a facility's CCN or state at all.

### CMS Ownership (free)
**Loader:** `cms/fetch_cms.py:load_ownership`
**Discovery mechanism:** Metastore endpoint (`.../metastore/schemas/dataset/items/y2hd-n93e`) resolves to the current month's `NH_Ownership_*.csv` download URL dynamically — the direct CSV URL embeds a rotating content hash and can't be hardcoded reliably. Falls back to a last-known-good URL if the metastore lookup fails.
**Coverage:** All current Medicare SNF ownership records, with a real CCN and facility state
**Refresh:** Monthly, but **no scheduled refresh is wired up** — rerun `python3 -m cms.fetch_cms` periodically to keep it current
**Used for:** Fuzzy matching discovered deals against confirmed ownership records; individual owner names also seed NY's UCC individual-mode search (filtered to equity/control roles — see `main.py:_CMS_OWNERSHIP_RELEVANT_ROLES`)

### CMS Provider Information / Care Compare (free)
**Loader:** `matcher/carecompare.py:load_care_compare`
**Discovery mechanism:** Same metastore pattern (`.../metastore/schemas/dataset/items/4pq5-n9py`) resolving to the current `NH_ProviderInfo_*.csv`
**Coverage:** All active Medicare SNFs
**Refresh:** Monthly
**Used for:** Enriching matched deals with quality data
**Key fields:** 5-star rating, staffing rating, health inspection rating, SFF flag, SFF candidate flag, bed count, ownership type
**Note:** `cms/fetch_cms.py` also defines a `load_care_compare` function that hits the old SODA `data.cms.gov/resource/{id}.json` API (`config.cms_carecompare_dataset`, etc.) — this is dead code, always shadowed by the `matcher.carecompare` import inside `fetch_and_load_all()`, and not the active code path.

---

## Source Priority

For a given deal, sources are processed in roughly this priority order:

```
1. State UCC-1      — earliest signal, can precede public announcement
2. SNF CHOW         — confirmed, covers everything, quarterly
3. EDGAR            — fast, public companies only, daily
4. Gmail Alerts     — fast, all deals, same-day
5. News RSS         — fast, all deals, same-day
6. CMS Ownership /
   Provider Info    — confirmation/enrichment layer, monthly
```

---

## Adding New Sources

To add a new RSS/EDGAR/CHOW source, register it in `scraper/sources.py`:

```python
Source(
    name="My New Source",
    url="https://example.com/feed/",
    source_type="rss",  # rss | edgar | googlenews | manual | ucc | chow
    active=True,
)
```

The pipeline picks it up automatically on the next run. Note: `source_type="googlenews"` entries are registered in `scraper/sources.py` (`GOOGLE_NEWS_QUERIES`) but `main.py:discover_articles` has no handler for that type — they're not actually fetched. A new discovery source that isn't RSS/EDGAR/CHOW/Gmail/UCC needs a new block in `discover_articles`, not just a `Source` registration.

Gmail Alerts and UCC-1 filings aren't registered via `scraper/sources.py` at all — they're wired directly into `main.py:discover_articles` (Gmail via `scraper/gmail_alerts.py`, UCC via `scraper/ucc.py` + the `ucc/` state modules).

---

## Source Status

| Source | Status | Notes |
|---|---|---|
| SNF CHOW dataset | ✅ Working | Quarterly CSV, direct download |
| EDGAR full-text search | ✅ Working | |
| Gmail Alerts (OAuth) | ✅ Working | Auto-scaling lookback window |
| News RSS (5 feeds) | ✅ Working | SNN, McKnight's, Modern Healthcare, Provider Magazine, Senior Housing News |
| UCC-1 — NY | ✅ Working, automated, but Chrome-CDP-only | Not headless/AWS-ready — needs a real Chrome (2026-09-15 Cloudflare fix); org + individual debtor search, parallel tabs |
| UCC-1 — KY | ✅ Working, automated, headless | Don't run twice same-day (rate limit) |
| UCC-1 — OH | ⚠️ Working, local only, fragile under volume | Needs Xvfb wrapper for cloud; hit 429s under repeated same-day volume (2026-09-15) |
| UCC-1 — PA | ⚠️ Automated but fragile under volume (2026-09-16) | Real Chrome over CDP works for small/isolated queries, but a full 237-name batch got Incapsula-challenged partway through (same pattern as OH) — probe before trusting a big batch, no PA-specific search-name list or deep link yet |
| UCC-1 — NJ | ❌ Not integrated | Scratch script only, no `ucc/` module |
| CMS Ownership | ✅ Working | Provider Data Catalog, metastore-discovered URL |
| CMS Provider Info / Care Compare | ✅ Working | Provider Data Catalog, metastore-discovered URL |
| Google Alerts RSS feed (legacy) | ⚠️ Redundant/unverified | Registered in `scraper/sources.py`; Gmail OAuth path is primary |
| Google News queries | ❌ Not wired up | Registered in `scraper/sources.py` but no fetch handler in `discover_articles` |
| EDGAR RSS per ticker | ❌ Not used | Superseded by EDGAR full-text search |

---

## Known Issues / Backlog

- **UCC search-name blind spot (KY, NY, OH) — FIXED 2026-09-22.** `main.py`'s `ky_names`/`ny_names`/`oh_names` come from `scraper.chow.get_chow_operator_names(state)` — a static snapshot of CMS's CHOW CSV (last meaningfully updated ~Jan 2026). Inside `scraper/ucc.py`, `known_operator_names` (pulled live from the `deals` table — includes entities discovered later via RSS/Gmail/EDGAR) previously was only used as a fallback when the CHOW list was empty, which it never was — so every automated UCC run for these three states only ever searched the same fixed ~Jan-2026 name list. Confirmed for KY on 2026-09-21: 34 of 129 distinct KY deal entity names had never been searched by any automated run; searching them directly turned up 4 real filings never seen before. **Fixed:** `scraper/ucc.py` now has a `_union_names()` helper and all three call sites (`ky_terms`/`ny_terms`/`oh_org_terms`) union the CHOW list with live `known_operator_names` instead of one taking exclusive precedence. Not yet verified end-to-end on a live run (all three states are rate-limited/blocked from same-day re-runs — confirm on the next scheduled run). See memory `ucc_chow_name_blind_spot`.
- **OH UCC filing source link doesn't resolve to a functional page for viewers — root-caused 2026-09-22, not fixable from our code.** `scripts/export_deals.py` sets OH UCC-sourced deals' `source_url` to the static portal URL `https://ucc.ohiosos.gov/search` (OH has no per-filing `detail_url` the way NY does). Reported 2026-09-21 that clicking through from the live site doesn't land on a working https page. Root cause confirmed via a real Chrome browser (Claude in Chrome, not curl — curl gets a false-signal Cloudflare 403 regardless of actual site status, see memory `oh_ucc_ip_blocked`): the page's own top-level document request to `/search` never completes — it stays in `pending` state indefinitely (confirmed across 2 separate fresh navigations, 20+ seconds each), which means the browser tab never reaches network-idle and page scripts never finish initializing, even though the Angular SPA shell does render (tab title correctly becomes "UCC 11 Search Form - UCC Filing Portal" and the underlying API calls like `/api/configuration/states` return 200). This reproduced consistently and is specific to this page — a control page (example.com) loaded and screenshotted normally in the same browser session. This looks like a server/portal-side issue on Ohio SOS's end (the connection for the main document isn't being closed), not something we can fix from `export_deals.py`. **Options going forward, not yet decided:** (a) leave as-is — the page eventually shows the search form even if the tab spinner never resolves, so a patient viewer can probably still search it by hand; (b) have `oh_playwright.py` capture a per-filing deep link the way NY's `detail_url` does (nontrivial — OH's results are an Angular Material table with no per-row href, would need to click each row and read back a resulting URL, and OH's portal is already the most fragile-under-volume of the automated states); (c) drop the clickable link for OH deals entirely rather than point viewers at a page that may hang for them too.
