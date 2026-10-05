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

### News RSS / Trade Press — working again as of 2026-09-23
**Sources:** Skilled Nursing News, McKnight's Long-Term Care News, Senior Housing News — registered in `scraper/sources.py`. Modern Healthcare (403 to every client) and Provider Magazine (feed removed, 404) are registered but `active=False`.
**Coverage:** Public and private deals, announced deals (may not close)
**Speed:** Same day as announcement
**Reliability:** Medium — announced deals sometimes fall through
**Status:** All five feeds are live and producing deals (no API key or subscription required)

### State UCC-1 Filings — primary acquisition signal
**Mechanism:** Headless-browser scrapers (Playwright), one per state portal, in `ucc/`. UCC-1 financing statement filings are an early signal — often filed before a deal is publicly announced or shows up in CHOW.
**Coverage:** NY, KY, OH, PA, NJ, CA (see table below). Research on every other state: `docs/ucc-feasibility.md`
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
| CA | Unified debtor + secured-party index (one search covers both) | Automated (enabled 2026-09-30), real Chrome over CDP, **fragile under volume** | `ucc/ca_playwright.py` — auto-launches Chrome and loads the search page itself, same as PA. Searches CA CHOW buyers plus operator names from deals involving CA (~440 names), not the national list. Incapsula returned **HTTP 429 after ~35 fast searches from 2 tabs** on 2026-09-30, and the first version read the block's JSON body as "0 filings"; any response without `rows` now raises, is recorded in run health, and stops the batch. Runs one tab with a 2.5 s pause (~20–25 min). Only financing statements (`RECORD_TYPE` "UCC") are kept, not state tax or judgment liens, and only filings whose debtor *is* the searched name (the portal matches loosely, and over secured parties too). The name list is sorted and each run resumes where the last one stopped (`cms_load_checkpoints` key `ucc_ca_next_offset`), because a block ends the run early. CMS individual owners aren't searched yet ("LAST, FIRST" returned nothing; other formats untested). No per-filing deep link; the site links the portal search page. Run on its own (`--ucc-states CA`), not twice in one day. |

**Running UCC states:** `main.py --ucc-states NY,KY,OH` (or any subset) restricts the UCC step to just those states. Prefer running states separately (one cron/launchd job per state) rather than bundled — nothing commits to the DB until the whole UCC fetch call returns, so one state hanging or getting blocked loses every other state's already-good work for that run too. This bit hours off a run on 2026-09-15 when NY's multi-hour individual-name phase was still going when OH hit its 429.

### State CON / Change-of-Ownership Notices — pre-closing signal (AL, OK, ME, MI, MS, NC, MD, NJ live since 2026-09-30)

Some states make a buyer file with a state agency *before* a nursing home changes hands. Research on which states publish usable filings is in `docs/con-feasibility.md`; build order there is AL → OK → ME → NY → MI → MS.

- **AL (live):** `scraper/con_al.py`. SHPDA posts every Notice of Change of Ownership (filed ≥20 days before closing) on [one index page](http://shpda.alabama.gov/Announcements/certificateofneed/chow/changeownershipnotice.aspx) as a PDF. Each run fetches the index and downloads only PDFs not already stored and posted in the last 365 days. The SHPDA facility ID in the letter (`017-N0003`) gives the facility type; only `N` (nursing home) notices go to Claude, the rest (home health `H`, hospice `P`, assisted living `S`, dialysis `D`…) are stored with an `extraction_error` skip reason so they're never downloaded again. Deals have `source_type = 'con'` ("State CON" on the site).
  - **One deal per notice:** the prepended header tells the extractor the buyer is the per-facility proposed licensee. Batch filings (CO2026-062…068, seven Genesis facilities → 101 W State Street Holdings) otherwise all extract the same parent, and the semantic-dedup index silently kept only the first.
  - **CMS matching:** the new owner isn't in CMS yet when a notice is filed, so most of these sit in `detected` until the re-check sees CMS catch up. That lag is the point of the source.
  - **Scanned PDFs** (7 of 90 on first backfill) have no text layer; `pipeline/pdf_text.py` sends their first pages to Claude as a PDF document and uses the transcription (no system OCR needed). One was a nursing home (CO2026-069, Arabella of Red Bay → Red Bay Opco / 106 10th Realty).
  - First backfill (2026-09-30): 90 notices in the last year → 32 nursing homes → 32 deals.
- **OK (live):** `scraper/con_ok.py`. OSDH's monthly "The Notice" is a 2-page PDF table of active CON projects (CN #, facility, received date, type, status), linked from the [Health Facility Systems page](https://oklahoma.gov/health/services/licensing-inspections/long-term-care-service/health-facility-systems.html) — filenames are inconsistent, so issues are found from that page, never guessed. Parsed without Claude (pre-extracted path, `extraction_model = 'con_direct'`), one deal per CN, newest issue first so each CN gets its latest status. Kept: acquisitions and `-372` change-of-ownership/stock-transfer exemptions (2025 issues say "Change *for* Ownership"). Skipped: `-812` management agreements, `-371` relocations, `-372B` bed expansions, new construction, standard review, and withdrawn/denied CNs (withdrawn ones get refiled under a new CN).
  - **No buyer or seller is published**, so these are facility-only deals; `acquisition_date` is the application received date (closing isn't published). The dedup hash uses `con|OK|<CN>` (`_con_id`) — without it every buyer-less OK deal in a month would share one state+month hash, the same collision UCC had.
  - Status changes after a CN is first stored (e.g. "Under Program Area Review" → "Issued") aren't written back.
  - First backfill (2026-09-30): 13 issues → 100 CNs → 52 ownership changes in the last year → 52 deals (11 already CMS-confirmed).
- **ME (live):** `scraper/con_me.py`. DHHS lists every open CON case on [Current Healthcare Reviews](https://www.maine.gov/dhhs/dlc/healthcare-oversight/current_healthcare_reviews) (plus `older-reviews/<year>-health-care-review` for the prior year). Only the "Nursing Facility Reviews" section is read. A case is keyed on its first document (normally the Letter of Intent, which names buyer, seller and every facility) and extracted once by Claude from that letter; later filings are listed in the header but not re-extracted. Scanned LOIs (Eagle Arc/Links) go through `pipeline/pdf_text.py`. ~4–6 nursing facility cases a year.
- **MI (live):** `scraper/con_mi.py`. MDHHS's monthly CON activity reports, [a page per year](https://www.michigan.gov/mdhhs/doing-business/providers/certificateofneed/reports/activity-reports); only the letter-of-intent report is read (earliest filing). XLSX from 2026-03, PDF before. Nursing homes are facility IDs `NN-4xxx`. Parsed without Claude. The description is the only detail: kept are acquisitions (`ACQ … NH BY <buyer>` — buyer taken from the `BY` clause), membership/ownership transfers, purchases, and new leases/new landlords (e.g. nine Optalis facilities with a new landlord on 2025-12-16 = a property sale); skipped are lease renewals, bed relocations/additions, replacements and new construction (`NEW NH W/20 BEDS … LEASE`). PDF-era rows have no column separators: they're split on the county (fixed list of 83) and the facility name is separated from the city with CMS's list of Michigan nursing-home cities. "NEW LEASE" rows may be propco re-leases rather than operator changes — the site shows the raw description in the title.
- **MS (live):** `scraper/con_ms.py`. MSDH's CON Weekly Reports ([2026 page](https://msdh.ms.gov/page/30,0,84,863.html); past years via the archive page) — the "Change of Ownership (CHOW) Applications" table. Items drop off 30 days after completion, so the first run backfills every weekly report in the last year and later runs read only the latest 6. Nursing homes = "Nursing Home" rows plus hospital long-term-care units ("Bolivar Medical Center Long Term Care"). No buyer is published. The extracted table text needs care: status cells from the previous row run into the next row's name (row start = first facility-type label after the last date/status cell), the state's own table has typos between weeks (records keyed on name's first word + place + date), and one row was missing its "Location:" label, which silently merged it into the next row and dropped Ruleville Community Care Center until fixed. A report with no CHOW table at all is normal (week ending 2025-11-28); only "no report has one" is flagged.
- **NC (live):** `scraper/con_nc.py`. DHSR's "No Reviews and Exemptions" tables ([current year](https://info.ncdhhs.gov/dhsr/coneed/reviews/index.html), `archive<YEAR>.html` for past years) — every CON exemption request, including acquisitions of existing facilities (G.S. 131E-184(a)(8)), with the **applicant (buyer)** named. Parsed without Claude. Kept: acquisition / change of owner or operator / indirect ownership rows (not equipment) whose facility is a nursing home — tested by name within the county against DHSR's licensed nursing home list (`Nhlist_a.xlsx`, current names), then CMS's NC names (lag renames, so they keep the pre-sale name: Sardis Oaks → Ignite), then a nursing-words test. A re-filing for the same facility within 180 days (Ignite filed each facility twice under different property LLCs) is collapsed to the first. Applicant strings sometimes carry an address, which is stripped.
- **MD (live):** `scraper/con_md.py`. MHCC's [Nursing Home Acquisition Applications](https://mhcc.maryland.gov/healthcare-communities/state-health-planning-and-certificate-need-con/acquisition-or-change-ownership/nursing-home-acquisition-applications) page — one `<h2>` per case with application PDFs. Same model as ME: keyed on the first document, extracted once by Claude with every facility in the case named in the header; multi-facility cases are told to name parents (CommuniCare, OHI/Omega) since only the first facility's application is read. No dates on the page: the upload month in the PDF path stands in, which runs late when documents are re-uploaded (CommuniCare posted 2026-02-09, re-uploaded in May).
- **NJ (live):** `scraper/con_nj.py`. DOH's [LTC Transfer of Ownership](https://www.nj.gov/health/healthfacilities/certificate-need/ltc-transfer-ownership) table (Claude extraction from each application's summary PDF, scans transcribed) and [Real Estate Transfer of Ownership](https://www.nj.gov/health/healthfacilities/certificate-need/real-estate-transfer-ownership) table ("<seller> to <buyer>", parsed without Claude). The operator page only lists recent applications, so its history builds up run by run.
- **Dates:** a text-extracted CON deal whose filing states no closing date gets the filing date as `acquisition_date` (`main._default_con_dates`), instead of falling back to the ingest date on the site.
- **All CON deals skip the name-based AL/MC scope filter** (`pipeline/al_mc_scope.py`): each source already confirms a nursing facility, and the filter dismisses a whole portfolio if any one facility name says "Assisted Living" — it dismissed the 18-facility Eagle Arc/Links deal over "Portland Center for Assisted Living". The filter still applies to news deals, where the same rule correctly drops senior-living portfolios.

---

## CMS Reference Datasets
These are used for matching and enrichment, not discovery. Both are pulled from
the **Provider Data Catalog** (`data.cms.gov/provider-data/...`), not the older
enrollment-system "All Owners" API — that dataset is keyed by PECOS Enrollment ID
and can't be joined to a facility's CCN or state at all.

### CMS Ownership Changes (free) — post-closing, all states
**Code:** `scraper/cms_owner_changes.py`, run from `main.py:discover_articles` right after CHOW. Deals are `source_type = 'chow'` ("Federal Record" on the site, linking the CMS Ownership dataset), `extraction_model = 'cms_owner_change'`.
**Mechanism:** each run reloads the CMS Ownership file (`cms.fetch_cms.load_ownership`) if the stored copy is more than 25 days old. It then turns new organization owners (5%+ direct, direct, or 5%+ indirect ownership) with a start date in the last 365 days, and no earlier association with that facility, into deals:
- Trusts, estates and ESOPs are skipped as buyers (estate planning, internal transfers).
- Facilities already linked to a tracker deal within 180 days of the change are skipped.
- New owners on the same facility within 45 days are one change; the same buyer on the same start date across facilities is one portfolio deal.
- The seller is an organization direct owner that dropped out of the latest refresh. It exists only for changes between two loaded refreshes (the first two were 2026-07-01 and 2026-09-16).
- Every candidate row goes into `cms_owner_change_seen` (`db/migration_add_cms_owner_change_seen.sql`), so each change is evaluated once.
**Why:** the Ownership file's newest start dates run about 1–2 months behind, versus the CHOW file's effective dates, which were still at 2026-02-01 in September 2026. Backtest in `docs/con-feasibility.md`.
**First run (2026-09-30):** 219 new owner rows → 102 facility changes, 45 already tracked, 61 trust/estate rows skipped → 34 deals, 33 stored (one was a same-buyer, same-date duplicate of a CHOW deal). Examples: Health Scholarships Inc (5 GA homes, 2025-11), One Equity Partners VII (SC), Kalesta Healthcare Group (OR), Texas hospital districts taking licenses.
**Limits:** some changes are internal restructurings, not sales (e.g. Select Health Care taking shares from its own ESOP). No deal value. Stage comes out `confirmed` because the source is CMS itself.

### CMS Ownership (free)
**Loader:** `cms/fetch_cms.py:load_ownership`
**Discovery mechanism:** Metastore endpoint (`.../metastore/schemas/dataset/items/y2hd-n93e`) resolves to the current month's `NH_Ownership_*.csv` download URL dynamically — the direct CSV URL embeds a rotating content hash and can't be hardcoded reliably. Falls back to a last-known-good URL if the metastore lookup fails.
**Coverage:** All current Medicare SNF ownership records, with a real CCN and facility state
**Refresh:** Monthly. Since 2026-09-30 the pipeline reloads it itself when the stored copy is more than 25 days old (`scraper/cms_owner_changes.refresh_ownership_if_stale`); Care Compare still needs `python3 -m cms.fetch_cms`
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
| News RSS (3 feeds) | ✅ Working since 2026-09-23 | SNN, McKnight's, Senior Housing News. Modern Healthcare and Provider Magazine inactive (dead feeds) |
| UCC-1 — NY | ✅ Working, automated, but Chrome-CDP-only | Not headless/AWS-ready — needs a real Chrome (2026-09-15 Cloudflare fix); org + individual debtor search, parallel tabs |
| UCC-1 — KY | ✅ Working, automated, headless | Don't run twice same-day (rate limit) |
| UCC-1 — OH | ⚠️ Working, local only, fragile under volume | Needs Xvfb wrapper for cloud; hit 429s under repeated same-day volume (2026-09-15) |
| UCC-1 — PA | ⚠️ Automated but fragile under volume (2026-09-16) | Real Chrome over CDP works for small/isolated queries, but a full 237-name batch got Incapsula-challenged partway through (same pattern as OH) — probe before trusting a big batch, no PA-specific search-name list; no deep link possible (copy-filing-# button instead) |
| UCC-1 — NJ | ✅ Working, automated, no lender name | Re-enabled 2026-09-22; parallel worker pool (8 workers), ~8 min for ~141 CHOW-derived names; deals get a "lender not available" placeholder |
| UCC-1 — CA | ⚠️ Working, blocks partway through | First run 2026-09-30: blocked (429) after 159 names in ~8 min at the throttled pace; 639 filings, of which 159 were tax/judgment liens or loose matches (removed, with their 127 deals) → 346 CA UCC deals, 6 existing deals UCC-confirmed. Resumes at the next name each run, so a full pass takes ~3 runs on separate days |
| CMS ownership changes | ✅ Working since 2026-09-30 | Monthly Ownership-file diff; 33 deals on first run, see the section above |
| State CON — AL (SHPDA ownership notices) | ✅ Working since 2026-09-30 | Pre-closing; nursing homes only (by SHPDA facility-ID type); scanned PDFs skipped |
| State CON — OK (OSDH "The Notice") | ✅ Working since 2026-09-30 | Monthly; facility only, no buyer/seller; parsed without Claude |
| State CON — ME (DHHS CON reviews) | ✅ Working since 2026-09-30 | Low volume; LOI extracted once per case; scanned LOIs transcribed by Claude |
| State CON — MI (MDHHS activity reports) | ✅ Working since 2026-09-30 | Monthly LOIs; buyer only when the description names one; parsed without Claude |
| State CON — MS (MSDH weekly reports) | ✅ Working since 2026-09-30 | Weekly; no buyer; items vanish 30 days after completion, so don't let runs lapse >6 weeks |
| State CON — NC (DHSR exemptions) | ✅ Working since 2026-09-30 | Named buyer; nursing homes by county + name against DHSR's licensed list and CMS |
| State CON — MD (MHCC acquisitions) | ✅ Working since 2026-09-30 | Few, large cases; one Claude extraction per case |
| State CON — NJ (DOH transfers) | ✅ Working since 2026-09-30 | Operator transfers (Claude) + real estate transfers (table) |
| CMS Ownership | ✅ Working | Provider Data Catalog, metastore-discovered URL |
| CMS Provider Info / Care Compare | ✅ Working | Provider Data Catalog, metastore-discovered URL |
| Google Alerts RSS feed (legacy) | ⚠️ Redundant/unverified | Registered in `scraper/sources.py`; Gmail OAuth path is primary |
| Google News queries | ❌ Not wired up | Registered in `scraper/sources.py` but no fetch handler in `discover_articles` |
| EDGAR RSS per ticker | ❌ Not used | Superseded by EDGAR full-text search |

---

## Known Issues / Backlog

- **UCC search-name blind spot (KY, NY, OH) — FIXED 2026-09-22, historical backlog cleared for KY/PA same day.** `main.py`'s `ky_names`/`ny_names`/`oh_names` come from `scraper.chow.get_chow_operator_names(state)` — a static snapshot of CMS's CHOW CSV (last meaningfully updated ~Jan 2026). Inside `scraper/ucc.py`, `known_operator_names` (pulled live from the `deals` table — includes entities discovered later via RSS/Gmail/EDGAR) previously was only used as a fallback when the CHOW list was empty, which it never was — so every automated UCC run for these three states only ever searched the same fixed ~Jan-2026 name list. Confirmed for KY on 2026-09-21: 34 of 129 distinct KY deal entity names had never been searched by any automated run; searching them directly turned up 4 real filings never seen before. **Fixed:** `scraper/ucc.py` now has a `_union_names()` helper and all three call sites (`ky_terms`/`ny_terms`/`oh_org_terms`) union the CHOW list with live `known_operator_names` instead of one taking exclusive precedence — this only prevents the gap from growing further, though, so the historical backlog still needed clearing by hand. Added `scripts/ucc_gap_check.py` (reusable CHOW-vs-deals fuzzy-match gap finder) and ran it for KY (39 gap names, 22 filings found, all confirming already-known deals), PA (34 gap names — PA's first-ever gap check, since it never had a state-specific search list at all — 5 new/updated deals + 4 newly-corroborated), and NY (needed fuzzy-matching against NY's separate CMS individual-owner list too, not just CHOW, or the naive check badly overcounts — 379 real gap names, 673 filings found, 48 new/updated + 47 newly-corroborated). See memory `ucc_chow_name_blind_spot`.
- **Direct RSS feeds never worked — FIXED 2026-09-23.** feedparser fetched through urllib, which uses the interpreter's CA store; this Python has none, so every feed failed `CERTIFICATE_VERIFY_FAILED`, and feedparser reports that as an empty feed. No direct-feed article had ever been stored (SNN's last was 2026-05-21, likely before a Python reinstall); all news came through Gmail alerts. `scraper/rss.py` now fetches with `requests` (certifi) and logs failures. `pipeline/source_health.py` now warns at the end of every run when an RSS/EDGAR/Gmail source goes quiet.
- **Article text was crude and truncated — FIXED 2026-09-23.** trafilatura had been failing to import, because lxml 5.2+ split `lxml.html.clean` into the separate `lxml_html_clean` package. `scraper/rss.py` swallowed the ImportError, so every article went through the BS4 fallback: navigation junk included and a 10,000-char cap, then only the first 8,000 chars reached extraction. Long dealbook roundups lost their later deals.
  - Fixed: `lxml_html_clean` is added to requirements, the ImportError is now logged, and both caps use `config.article_max_chars` (30,000). `claude_max_tokens` went from 2,000 to 4,000 for many-deal roundups.
  - `scripts/backfill_deal_amounts.py` re-fetches and re-extracts past articles, and only fills NULL amounts on clearly matching deals.
  - Also, `financing_amount_m` was extracted and stored but never exported or shown. It now appears on cards as "$XM financing".
- **736 deals headlined just "Ownership change recorded" — FIXED 2026-09-23.** 717 were UCC deals whose `facility_names` was only the debtor copied over, which the frontend hides. Four changes:
  - **Facility naming.** `scripts/enrich_ucc_facility_names.py` now runs in the daily pipeline, right after the CMS/UCC relink. It names the facility by exact normalized match against `cms_facilities` or a single-CCN org owner (`ucc_debtor_exact` / `ucc_debtor_owner_exact`), then by CHOW buyer → facility, keyed by buyer+state and used only when that gives exactly one facility (`ucc_debtor_chow_buyer`, preferring CMS's provider name over CHOW's free-text DBA). Each match writes one `cms_matches` row. `_store_cms_matches` preserves those rows on recheck. Deal stage is untouched, so enrichment doesn't flood the digest. The old LLC-suffix-strip phase is now opt-in (`--strip`). `--loose-report` lists role-word-stripped candidates for manual review only. Result: 464 deals named.
  - **Weak matches removed.** UCC deals now drop CMS matches scoring below `config.ucc_min_match_score` (90). Below that they were mostly noise: "PARK NURSING HOME" matched 28 facilities. Removed 10,697 existing weak rows on 390 deals and recomputed stage (34 confirmed → detected, and all 130 pending_cms → detected).
  - **Headlines.** UCC headlines are now "{facility or debtor} — UCC-1 financing". Anonymous trade-press roundup deals show "{n} facilities in {state} — financing / ownership change".
  - **Still open.** Those roundup deals' `deal_value_m` is blank even when the text states the amount (e.g. "$27 million"), an extraction gap. `pipeline/extractor.py` also truncates article text at 8,000 characters.
- **NJ UCC one-click filing lookup — added 2026-09-23.** NJ has no per-filing URL, but its search wizard has a Filing Number mode (only allowed with output = Copies Only), and its ASP.NET ViewState/EventValidation are not bound to a session or cookie. `scripts/export_deals.py::_fetch_nj_search_form` walks step 1 once per export and ships step 2's hidden fields in `deals.json` as `ucc_search_forms.NJ`. The frontend (`UccSource` in `App.jsx`) renders NJ source links as a form that POSTs those fields plus the filing number to the portal in a new tab, landing on the result row. "Include lapsed" is set, or lapsed filings return nothing. Tokens are refetched every export in case the portal's machine key rotates. If the fetch fails, the key is omitted and the link falls back to the portal homepage.
- **227 UCC deals showed as "News" with a dead `ucc://` link — FIXED 2026-09-23.** `scripts/ingest_manual_ucc.py` (every Claude-in-Chrome backfill since 2026-09-15) never set `articles.source_id`, so 377 UCC articles had a NULL `source_type`. The frontend labelled them "News", and `export_deals.py` skipped its UCC link rewrite for them. Both paths now use `main.ensure_ucc_source`, and the 377 rows were backfilled to the "State UCC-1 Filings" source. That also let 162 OH deals pick up their existing `detail_url` deep links.
- **OH UCC filing source link doesn't resolve to a functional page for viewers — FIXED at the code level 2026-09-22.** `scripts/export_deals.py` used to set OH UCC-sourced deals' `source_url` to the static portal URL `https://ucc.ohiosos.gov/search` (no per-filing `detail_url` existed for OH), which turned out to hang indefinitely for viewers — root cause confirmed via a real Chrome browser (Claude in Chrome, not curl): the page's own top-level document request to `/search` never completes, so the tab never reaches network-idle even though the Angular SPA shell does render. **Fix:** OH now has a real per-filing deep link, same as NY/KY. Investigated live and found the portal's own "View Profile" button navigates to `/company-profile/search/{entityId}` — a working, distinct detail page per filing — and `entityId` is already present in the `/api/ohiosearch` JSON response the search itself triggers, just never rendered into the DOM. `ucc/oh_playwright.py::_search_one` now intercepts that JSON response directly (also more reliable than the old rendered-HTML scraping it replaced) and `ucc/audit_log.py::_detail_url` builds the URL from it. **Backfilled 2026-09-22:** the fix only takes effect for filings scraped *after* it landed, so the 380 pre-existing OH `ucc_filings` rows still needed their `detail_url` filled in by hand. Re-ran all 157 distinct `query_name`s already on file via Claude in Chrome, capturing `entityId` straight from each `/api/ohiosearch` JSON response (XHR-patched in the page context) instead of clicking through 380 individual rows. 365/380 came back from the normal search; the other 15 had lapsed since first being scraped (the stored `status='active'` was stale, not live) and needed the portal's exact-filing-number "Number" tab search instead, which ignores the active/lapsed filter — recovered 12 more that way (2 filing numbers turned out to be genuinely gone from the live system). Final: **378/380 (99.5%)** now have a working `detail_url`. See memory `oh_ucc_ip_blocked` for the full technique writeup.
