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
**URL discovery:** `scraper/chow.py::_discover_chow_csv_url()` — self-discovering via `data.cms.gov/data-api/v1/dataset/{CHOW_DATASET_ID}/resources`, same pattern `matcher/carecompare.py` and `cms/fetch_cms.py` already used for the other two CMS datasets. `CHOW_URLS` is now only a last-resort fallback (was the sole hardcoded source until 2026-09-22, and had gone stale).
**Coverage:** ALL Medicare-certified SNFs — public and private
**Speed:** Quarterly updates (Jan, Apr, Jul, Oct)
**Reliability:** Highest in principle (federally required, CMS-verified) — see the freshness-tracking history below, since that principle didn't hold in practice for a long time
**Best for:** Private operator deals, comprehensive confirmed ownership record
**Limitation:** `EFFECTIVE DATE` lags real filing/publication time significantly — a file published 2026-07-17 still had a newest effective date of only 2026-02-01. Don't use it as a proxy for "how recent is this data."
**Freshness-tracking history (2026-09-22):** `fetch_chow_deals()` used to treat a record as "new" if its `EFFECTIVE DATE` was after a rolling 90-day cutoff — because of the lag above, that filter could never match anything once "today" drifted far enough past CHOW's laggy dates, so **CHOW-sourced deal discovery had produced zero deals (`extraction_model='chow_direct'`) for the entire life of this pipeline** until fixed the same day. Now tracked via a `chow_seen_records` table (every `(ccn, buyer_name, effective_date)` key ever seen, independent of date) plus a `CHOW_RECENCY_DAYS` (730) filter on which never-before-seen rows actually become deal candidates, so first activation doesn't flood the tracker with a decade of historical M&A. Also found and fixed along the way: `main.py`'s per-run Claude-cost cap used to apply to `pre_extracted` CHOW articles too, even though — like UCC filings — they never call Claude; combined with the seen-records change marking every found row as seen regardless of whether it got processed, this nearly caused real data loss on the first real run (609 found, cap silently kept 50, the other 559 would never have resurfaced) — recovered by hand that same session. CHOW is now cap-exempt like UCC. See memory `chow_freshness_tracking_broken_2026_09_22` for the full incident writeup.

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
| PA | Secured party | Automated, but fragile under volume | Confirmed 2026-09-16: doesn't need a human-driven browser session (`ensure_chrome_cdp` + self-navigate, same fix as NY) — but a 237-name/4-worker batch got Incapsula-challenged partway through (17 successes then mass JSON-parse failures as the API started returning an HTML challenge page instead), same pattern as OH. Single-name probes succeeding doesn't mean a batch will get through. Falls back to Claude in Chrome for names that get challenged (confirmed working 2026-09-22, same as OH). Still uses the generic national operator list rather than a PA-specific facility name list. No per-filing deep link is possible (verified 2026-09-23: `/recordDetails/ucc/{id}` requires Keystone login, `/search/ucc/{id}` never loads results from the URL), so the site shows a "Copy filing #" button next to the portal link instead. |
| NJ | Debtor name only — portal structurally never returns secured party (would need a paid per-filing lookup) | Automated, no bot-detection observed | `ucc/nj_playwright.py` — re-enabled 2026-09-22 (disabled 2026-06-23 for the missing-lender-name issue below). Now uses a parallel worker pool (`search_nj_batch`, `max_workers=8`, same pattern as KY — plain headless Chromiums, no shared-CDP-Chrome workaround needed) — est. ~8 min for the CHOW-derived NJ-specific list (`nj_search_names`, ~141 names), down from ~25 min sequential at launch. Kept to the CHOW list rather than the 446-name national list — a separate decision from the parallelism fix. Because there's no lender name, `ucc/lender_classifier.py::classify_secured_party` special-cases NJ's empty-secured-party case to maybe-relevant (state-aware override, not a blanket change — every other state still excludes on missing name) and `main.py`'s NEW_SIGNAL deal-creation gate is unblocked for NJ specifically, with the resulting deal's `lender` field set to an explicit "not available" placeholder so it's visible to viewers rather than silently blank. See memory `nj_ucc_reenabled_2026_09_22` — also documents a real bug found along the way (the classifier check was duplicated across 4 files with no shared state, so patching one didn't work end-to-end). |

**Running UCC states:** `main.py --ucc-states NY,KY,OH` (or any subset) restricts the UCC step to just those states. Prefer running states separately (one cron/launchd job per state) rather than bundled — nothing commits to the DB until the whole UCC fetch call returns, so one state hanging or getting blocked loses every other state's already-good work for that run too. This bit hours off a run on 2026-09-15 when NY's multi-hour individual-name phase was still going when OH hit its 429.

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
| SNF CHOW dataset | ✅ Working (fixed 2026-09-22, was silently producing 0 deals since launch) | Self-discovering URL + seen-records freshness tracking, see the detailed section above |
| EDGAR full-text search | ✅ Working | |
| Gmail Alerts (OAuth) | ✅ Working | Auto-scaling lookback window |
| News RSS (5 feeds) | ✅ Working | SNN, McKnight's, Modern Healthcare, Provider Magazine, Senior Housing News |
| UCC-1 — NY | ✅ Working, automated, but Chrome-CDP-only | Not headless/AWS-ready — needs a real Chrome (2026-09-15 Cloudflare fix); org + individual debtor search, parallel tabs |
| UCC-1 — KY | ✅ Working, automated, headless | Don't run twice same-day (rate limit) |
| UCC-1 — OH | ⚠️ Working, local only, fragile under volume | Needs Xvfb wrapper for cloud; hit 429s under repeated same-day volume (2026-09-15) |
| UCC-1 — PA | ⚠️ Automated but fragile under volume (2026-09-16) | Real Chrome over CDP works for small/isolated queries, but a full 237-name batch got Incapsula-challenged partway through (same pattern as OH) — probe before trusting a big batch, no PA-specific search-name list; no deep link possible (copy-filing-# button instead) |
| UCC-1 — NJ | ✅ Working, automated, no lender name | Re-enabled 2026-09-22; parallel worker pool (8 workers), ~8 min for ~141 CHOW-derived names; deals get a "lender not available" placeholder |
| CMS Ownership | ✅ Working | Provider Data Catalog, metastore-discovered URL |
| CMS Provider Info / Care Compare | ✅ Working | Provider Data Catalog, metastore-discovered URL |
| Google Alerts RSS feed (legacy) | ⚠️ Redundant/unverified | Registered in `scraper/sources.py`; Gmail OAuth path is primary |
| Google News queries | ❌ Not wired up | Registered in `scraper/sources.py` but no fetch handler in `discover_articles` |
| EDGAR RSS per ticker | ❌ Not used | Superseded by EDGAR full-text search |

---

## Known Issues / Backlog

- **UCC search-name blind spot (KY, NY, OH) — FIXED 2026-09-22, historical backlog cleared for KY/PA same day.** `main.py`'s `ky_names`/`ny_names`/`oh_names` come from `scraper.chow.get_chow_operator_names(state)` — a static snapshot of CMS's CHOW CSV (last meaningfully updated ~Jan 2026). Inside `scraper/ucc.py`, `known_operator_names` (pulled live from the `deals` table — includes entities discovered later via RSS/Gmail/EDGAR) previously was only used as a fallback when the CHOW list was empty, which it never was — so every automated UCC run for these three states only ever searched the same fixed ~Jan-2026 name list. Confirmed for KY on 2026-09-21: 34 of 129 distinct KY deal entity names had never been searched by any automated run; searching them directly turned up 4 real filings never seen before. **Fixed:** `scraper/ucc.py` now has a `_union_names()` helper and all three call sites (`ky_terms`/`ny_terms`/`oh_org_terms`) union the CHOW list with live `known_operator_names` instead of one taking exclusive precedence — this only prevents the gap from growing further, though, so the historical backlog still needed clearing by hand. Added `scripts/ucc_gap_check.py` (reusable CHOW-vs-deals fuzzy-match gap finder) and ran it for KY (39 gap names, 22 filings found, all confirming already-known deals), PA (34 gap names — PA's first-ever gap check, since it never had a state-specific search list at all — 5 new/updated deals + 4 newly-corroborated), and NY (needed fuzzy-matching against NY's separate CMS individual-owner list too, not just CHOW, or the naive check badly overcounts — 379 real gap names, 673 filings found, 48 new/updated + 47 newly-corroborated). See memory `ucc_chow_name_blind_spot`.
- **NJ UCC one-click filing lookup — added 2026-09-23.** NJ has no per-filing URL, but its search wizard has a Filing Number mode (only allowed with output = Copies Only), and its ASP.NET ViewState/EventValidation are not bound to a session or cookie. `scripts/export_deals.py::_fetch_nj_search_form` walks step 1 once per export and ships step 2's hidden fields in `deals.json` as `ucc_search_forms.NJ`. The frontend (`UccSource` in `App.jsx`) renders NJ source links as a form that POSTs those fields plus the filing number to the portal in a new tab, landing on the result row. "Include lapsed" is set, or lapsed filings return nothing. Tokens are refetched every export in case the portal's machine key rotates. If the fetch fails, the key is omitted and the link falls back to the portal homepage.
- **227 UCC deals showed as "News" with a dead `ucc://` link — FIXED 2026-09-23.** `scripts/ingest_manual_ucc.py` (every Claude-in-Chrome backfill since 2026-09-15) never set `articles.source_id`, so 377 UCC articles had a NULL `source_type`. The frontend labelled them "News", and `export_deals.py` skipped its UCC link rewrite for them. Both paths now use `main.ensure_ucc_source`, and the 377 rows were backfilled to the "State UCC-1 Filings" source. That also let 162 OH deals pick up their existing `detail_url` deep links.
- **OH UCC filing source link doesn't resolve to a functional page for viewers — FIXED at the code level 2026-09-22.** `scripts/export_deals.py` used to set OH UCC-sourced deals' `source_url` to the static portal URL `https://ucc.ohiosos.gov/search` (no per-filing `detail_url` existed for OH), which turned out to hang indefinitely for viewers — root cause confirmed via a real Chrome browser (Claude in Chrome, not curl): the page's own top-level document request to `/search` never completes, so the tab never reaches network-idle even though the Angular SPA shell does render. **Fix:** OH now has a real per-filing deep link, same as NY/KY. Investigated live and found the portal's own "View Profile" button navigates to `/company-profile/search/{entityId}` — a working, distinct detail page per filing — and `entityId` is already present in the `/api/ohiosearch` JSON response the search itself triggers, just never rendered into the DOM. `ucc/oh_playwright.py::_search_one` now intercepts that JSON response directly (also more reliable than the old rendered-HTML scraping it replaced) and `ucc/audit_log.py::_detail_url` builds the URL from it. **Backfilled 2026-09-22:** the fix only takes effect for filings scraped *after* it landed, so the 380 pre-existing OH `ucc_filings` rows still needed their `detail_url` filled in by hand. Re-ran all 157 distinct `query_name`s already on file via Claude in Chrome, capturing `entityId` straight from each `/api/ohiosearch` JSON response (XHR-patched in the page context) instead of clicking through 380 individual rows. 365/380 came back from the normal search; the other 15 had lapsed since first being scraped (the stored `status='active'` was stale, not live) and needed the portal's exact-filing-number "Number" tab search instead, which ignores the active/lapsed filter — recovered 12 more that way (2 filing numbers turned out to be genuinely gone from the live system). Final: **378/380 (99.5%)** now have a working `detail_url`. See memory `oh_ucc_ip_blocked` for the full technique writeup.
