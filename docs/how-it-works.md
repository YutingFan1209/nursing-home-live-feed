# How the Nursing Home Acquisition Tracker Works

## Overview

This tracker follows ownership changes at skilled nursing facilities (nursing homes) across the United States. Its purpose is to close a timing gap: federal ownership records are accurate but slow, often taking months to reflect who actually owns a given nursing home. This project combines several public records and news sources — some fast but unconfirmed, others slow but authoritative — to build a more current picture of nursing home ownership than any single source provides on its own.

As of this writing, the tracker covers **1,245 tracked deals across 40 states**, drawing on state lending records, federal ownership filings, SEC disclosures, and trade press coverage. The live feed is published at [yutingfan1209.github.io/nursing-home-live-feed](https://yutingfan1209.github.io/nursing-home-live-feed/).

---

## Data Sources

### UCC-1 Filings (State Financing Statements)

When a lender provides financing secured by a nursing home's assets — commonly the case when that financing funds an acquisition — the lender is required to file a public notice with the state, called a UCC-1 financing statement, to establish its legal claim to those assets. These filings are a matter of public record and are often filed around the time a deal closes, sometimes before the transaction is ever announced publicly or shows up in federal records. This makes them the earliest signal the tracker has access to. Currently, the tracker monitors UCC-1 filings in four states: New York, Kentucky, Ohio, and Pennsylvania (Pennsylvania requires a manual search rather than an automated one). The key limitation is that a UCC-1 filing shows that financing occurred against a facility's assets — it is a strong signal of a likely ownership change, not direct proof of one, since not every secured loan is tied to an acquisition. The tracker filters out filings from equipment lenders, pharmacy suppliers, and other non-acquisition-related creditors before they're counted as a signal.

### CMS Change of Ownership Records (CHOW)

The Centers for Medicare & Medicaid Services (CMS) requires every Medicare-certified nursing home to formally report a change of ownership. This dataset is the closest thing to a legally authoritative record of nursing home ownership changes, and it covers all Medicare-certified facilities, public and private alike. It tells us, with high confidence, that an ownership change was completed and recorded with the federal government. The tradeoff is timeliness: CMS publishes this dataset quarterly (most recently January 2026, with the next release expected April 2026), so a deal that closed today may not be reflected here for up to three months.

### CMS Provider Ownership Records

Separately from CHOW, CMS maintains an ongoing public register of who owns and controls each Medicare-certified nursing home — including individual owners, holding companies, and their roles (e.g., majority owner, managing member). This dataset is refreshed monthly. The tracker uses it in two ways: to confirm that an ownership change reported elsewhere (in the news, or in a UCC-1 filing) matches what CMS has on record, and to identify the names of known owners so the tracker knows what to watch for in state filings. Its limitation is that it functions as a reference and confirmation layer rather than a discovery source in its own right — it tells us who currently owns a facility, not when or why that changed, and in this system it is refreshed periodically rather than continuously.

### CMS Provider Information (Care Compare)

This is CMS's public quality-ratings dataset for nursing homes — the same data that powers the consumer-facing Medicare Care Compare website. It includes each facility's 5-star overall rating, staffing rating, health inspection rating, and whether the facility is flagged as a "Special Focus Facility" (CMS's designation for homes with a documented history of serious, persistent quality problems). It's refreshed monthly. The tracker uses it to add quality context to a tracked deal — for instance, flagging when a poorly-rated or Special Focus facility changes hands, which can be a relevant signal for oversight and policy purposes. It does not itself contain any ownership information.

### News and Trade Press

The tracker also monitors industry news: trade publications that cover skilled nursing and senior living (Skilled Nursing News, McKnight's, Modern Healthcare, Provider Magazine, Senior Housing News) via their RSS feeds, along with Google Alerts email notifications for relevant coverage across the broader web. This source often surfaces information — deal price, portfolio size, the parties' stated rationale — that regulatory filings never disclose, and it does so the same day a deal is announced. Its limitation is that it only captures deals that get publicly announced or covered; quiet transactions between private parties may never appear here, and some announced deals fall through and never actually close.

### SEC EDGAR (Securities and Exchange Commission Filings)

Publicly traded companies are legally required to disclose material events — including nursing home acquisitions — to the SEC, typically via an 8-K filing within four business days of the event. The tracker monitors these filings for major publicly traded owners and real estate investment trusts (REITs) active in the nursing home sector, such as Welltower, Sabra, and Ensign. This is a fast and highly reliable source for the subset of the industry it covers. Its central limitation is coverage: the large majority of nursing home owners are privately held companies that have no SEC reporting obligation at all, so this source only ever captures a fraction of overall deal activity.

---

## Scope: Skilled Nursing Facilities Only

This tracker is deliberately scoped to **Skilled Nursing Facilities (SNFs)** — the segment of long-term care that is Medicare/Medicaid-certified to provide short-term rehabilitative care and long-term nursing care, commonly referred to as "nursing homes."

It excludes **Assisted Living (AL) and Memory Care (MC)** facilities. These serve a different population (residents who need less medical support than skilled nursing provides), and critically, they are regulated differently — typically licensed at the state level rather than federally certified through Medicare/Medicaid the way SNFs are. Because AL/MC facilities fall outside CMS's certification and ownership-reporting framework, the tracker's core verification method (matching against CMS records) doesn't apply to them, and mixing them in would blur the ownership picture this tool is built to provide.

In practice, this means: deals involving operators that run assisted living or memory care exclusively are automatically excluded from the feed. For operators that run both nursing homes and assisted living/memory care communities, a deal is only excluded if it specifically names an assisted-living or memory-care facility — their legitimate skilled nursing deals are still tracked.

---

## Signal Confidence

Every deal in the tracker is labeled with one of three confidence levels, shown directly on the live feed:

| Label | What it means |
|---|---|
| **UCC Signal** | An early warning sign. A lender has filed a state financing statement tied to this operator or facility, suggesting a deal may be underway. This has not yet been confirmed against federal (CMS) ownership records. |
| **UCC Confirmed** | A deal identified through another source (news coverage, an SEC filing, etc.) has since been corroborated by a matching state UCC-1 financing filing — two independent sources now point to the same transaction. |
| **CMS Confirmed** | The deal has been verified against CMS's official nursing home ownership records — the highest level of confidence the tracker assigns. |

A deal with no badge shown is still under review and hasn't yet reached one of these confidence tiers.

---

## CCN Coverage by Source

A CMS Certification Number (CCN) is the unique federal ID assigned to every Medicare-certified nursing home. When a tracked deal can be matched to a specific CCN, it means the tracker has pinned the deal to one exact, identifiable facility rather than just a company name. Match rates vary substantially by source, largely because CMS's own CHOW filings already include the CCN directly, while a state UCC-1 filing or a news article has to be matched to one indirectly.

| Source | Deals Tracked | Matched to a Specific Facility (CCN) | Match Rate |
|---|---:|---:|---:|
| State UCC-1 Filings | 1,103 | 936 | 85% |
| News / Trade Press (RSS + Google Alerts) | 102 | 8 | 8% |
| CMS CHOW | 37 | 37 | 100% |
| SEC EDGAR | 3 | 0 | 0% |

CMS CHOW deals reach 100% because the CMS filing itself already reports the exact CCN of the facility that changed hands — no matching is required. News and EDGAR sources have low match rates in part because those deals often involve multi-facility portfolios without individually named facilities, or facility names not yet reflected in CMS's ownership data.

---

## Summary Statistics

| Metric | Value |
|---|---:|
| Total deals tracked | 1,245 |
| Confirmed (matched with high confidence to CMS records) | 958 |
| Detected (early signal, not yet confirmed) | 247 |
| Pending CMS confirmation | 21 |
| Dismissed (out of scope or duplicate) | 19 |
| States covered | 40 |
| Distinct facilities (CCNs) matched to a deal | 521 |
| Deal date range | 2001–2026 (the large majority from the last two years) |
| Data current as of | July 23, 2026 |
