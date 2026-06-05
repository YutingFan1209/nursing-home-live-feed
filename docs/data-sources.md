# Data Sources

## Overview

The pipeline uses three categories of data sources. Each has different coverage,
speed, and reliability characteristics.

---

## Discovery Sources
These find new deals to process.

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

### News RSS / Trade Press (requires subscription or API)
**Sources:** Skilled Nursing News, McKnight's Long-Term Care News, Modern Healthcare  
**Coverage:** Public and private deals, announced deals (may not close)  
**Speed:** Same day as announcement  
**Reliability:** Medium — announced deals sometimes fall through  
**Status:** Currently blocked by bot protection. Requires either:
  - Org subscription + session cookie scraping
  - SerpAPI (~$50/mo)
  - NewsAPI (~$50/mo)

---

## CMS Reference Datasets
These are used for matching and enrichment, not discovery.

### CMS All Owners (free)
**Dataset ID:** `128fb95f-427c-4df9-bce4-8db0ee8ec6ad`  
**API:** `https://data.cms.gov/data-api/v1/dataset/{id}/data`  
**Coverage:** All current Medicare SNF ownership records  
**Refresh:** Monthly  
**Used for:** Fuzzy matching discovered deals against confirmed ownership records  
**Key columns:** ENROLLMENT ID, ORGANIZATION NAME, ORGANIZATION NAME - OWNER,
  ASSOCIATION DATE - OWNER, STATE - OWNER, REIT flag, PE flag

### CMS Care Compare / Provider Info (free)
**Coverage:** All active Medicare SNFs  
**Refresh:** Monthly (was paused Jul-Sep 2025 during iQIES migration)  
**Used for:** Enriching matched deals with quality data  
**Key fields:** 5-star rating, staffing rating, health inspection rating,
  SFF flag, SFF candidate flag, bed count, ownership type

---

## Source Priority

For a given deal, sources are processed in this priority order:

```
1. SNF CHOW    — confirmed, covers everything, quarterly
2. EDGAR       — fast, public companies only, daily
3. News        — fast, all deals, requires paid access
4. CMS All Owners — confirmation/enrichment layer, monthly
```

---

## Adding New Sources

To add a new source, register it in `scraper/sources.py`:

```python
Source(
    name="My New Source",
    url="https://example.com/feed/",
    source_type="rss",  # rss | edgar | googlenews | chow | manual
    active=True,
)
```

The pipeline picks it up automatically on the next run.

---

## Source Status

| Source | Status | Notes |
|---|---|---|
| EDGAR full-text search | ✅ Working | New endpoint found May 2026 |
| SNF CHOW dataset | ✅ Working | Quarterly CSV, direct download |
| CMS All Owners | ✅ Working | Migrated to new API May 2026 |
| SNN RSS feed | ❌ Blocked | Bot protection |
| McKnight's RSS | ❌ Blocked | Bot protection |
| Google News RSS | ❌ Blocked | Bot protection |
| Care Compare | ⚠️ Needs migration | Old SODA API deprecated |
| EDGAR RSS per ticker | ❌ Broken | URL format changed |
