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
| NY | Debtor name — both Organization mode (known operator LLCs) and Individual mode (CMS owner names) | Fully automated, headless, AWS-ready | Separate form flows for org vs. individual debtors, routed by `ucc/ny_playwright.py`; individual search seeded from CMS ownership records, roughly doubles NY runtime (~40-75 min) |
| KY | Debtor name (seeded from CHOW CSV operator names) | Fully automated, headless, AWS-ready | `ucc/ky_playwright.py` |
| OH | Secured party | Automated locally, `headless=False` + hidden-window trick | `ucc/oh_playwright.py` — needs an Xvfb wrapper to run headless in the cloud |
| PA | Secured party | **Manual only** | `ucc/pa_playwright.py` requires a live Chrome CDP session — cannot run unattended without a persistent browser process |

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
| UCC-1 — NY | ✅ Working, automated | Org + individual debtor search |
| UCC-1 — KY | ✅ Working, automated | |
| UCC-1 — OH | ✅ Working, local only | Needs Xvfb wrapper for cloud |
| UCC-1 — PA | ⚠️ Manual only | Requires live CDP session |
| UCC-1 — NJ | ❌ Not integrated | Scratch script only, no `ucc/` module |
| CMS Ownership | ✅ Working | Provider Data Catalog, metastore-discovered URL |
| CMS Provider Info / Care Compare | ✅ Working | Provider Data Catalog, metastore-discovered URL |
| Google Alerts RSS feed (legacy) | ⚠️ Redundant/unverified | Registered in `scraper/sources.py`; Gmail OAuth path is primary |
| Google News queries | ❌ Not wired up | Registered in `scraper/sources.py` but no fetch handler in `discover_articles` |
| EDGAR RSS per ticker | ❌ Not used | Superseded by EDGAR full-text search |
