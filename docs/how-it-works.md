# How the Nursing Home Acquisition Tracker Works

## Overview

This tracker follows ownership changes at skilled nursing facilities (nursing homes) across the United States. Its purpose is to close a timing gap: federal ownership records are accurate but slow, often taking months to reflect who actually owns a given nursing home. This project combines several public records and news sources — some fast but unconfirmed, others slow but authoritative — to build a more current picture of nursing home ownership than any single source provides on its own.

As of 2026-09-30, the tracker covers **2,549 live deals across 53 states and territories**, drawing on state pre-closing ownership filings (8 states), state lending records, federal ownership filings, SEC disclosures, and trade press coverage. The live feed is published at [yutingfan1209.github.io/nursing-home-live-feed](https://yutingfan1209.github.io/nursing-home-live-feed/).

---

## Data Sources

### State Pre-Closing Ownership Filings

In several states, whoever is buying a nursing home has to tell a state agency *before* the sale closes: a certificate-of-need application or exemption, a change-of-ownership notice, or an acquisition approval request. Those filings are public, and because they come first, they are usually the earliest record of a sale anywhere: weeks to months ahead of news coverage and CMS. North Carolina's filings, for example, came 73 to 95 days before the news, and Maryland posted one 18-home sale about 170 days before the tracker first saw it elsewhere. The tracker reads these filings in eight states: Alabama, Oklahoma, Maine, Michigan, Mississippi, North Carolina, Maryland and New Jersey. They appear on the site labeled "State CON". What each state publishes differs: Alabama, Maine, North Carolina, Maryland and New Jersey name the buyer, while Oklahoma, Mississippi and most Michigan filings name only the facility, so those deals say that a facility is changing hands without saying to whom. A filing is also a plan, not a completed sale: a few are withdrawn or delayed, and each deal is confirmed only once CMS's own records show the new owner. New York publishes the richest filings of all (named owners with ownership percentages and prices); it has been tested but isn't in the feed yet. The research behind the choice of states is in [`con-feasibility.md`](con-feasibility.md).

### UCC-1 Filings (State Financing Statements)

When a lender provides financing secured by a nursing home's assets — commonly the case when that financing funds an acquisition — the lender is required to file a public notice with the state, called a UCC-1 financing statement, to establish its legal claim to those assets. These filings are a matter of public record and are often filed around the time a deal closes, sometimes before the transaction is ever announced publicly or shows up in federal records. This makes them one of the earliest signals the tracker has, alongside state pre-closing filings. The tracker monitors UCC-1 filings in five states: New York, Kentucky, Ohio, Pennsylvania and New Jersey. Several of these state websites actively block automated searches, so coverage in some states (Ohio in particular) depends on searches being run through a regular browser. New Jersey's site never shows who the lender is, so New Jersey deals show the lender as "not available". The key limitation is that a UCC-1 filing shows that financing occurred against a facility's assets — it is a strong signal of a likely ownership change, not direct proof of one, since not every secured loan is tied to an acquisition. The tracker filters out filings from equipment lenders, pharmacy suppliers, and other non-acquisition-related creditors before they're counted as a signal.

### CMS Change of Ownership Records (CHOW)

The Centers for Medicare & Medicaid Services (CMS) requires every Medicare-certified nursing home to formally report a change of ownership. This dataset is the closest thing to a legally authoritative record of nursing home ownership changes, and it covers all Medicare-certified facilities, public and private alike. It tells us, with high confidence, that an ownership change was completed and recorded with the federal government. The tradeoff is timeliness: CMS publishes this dataset quarterly (most recently July 2026), and each release trails real closings by months: the July 2026 file's newest ownership change took effect in February 2026.

### CMS Provider Ownership Records

Separately from CHOW, CMS maintains an ongoing public register of who owns and controls each Medicare-certified nursing home — including individual owners, holding companies, and their roles (e.g., majority owner, managing member). This dataset is refreshed monthly. The tracker uses it in two ways: to confirm that an ownership change reported elsewhere (in the news, or in a UCC-1 filing) matches what CMS has on record, and to identify the names of known owners so the tracker knows what to watch for in state filings. Its limitation is that it functions as a reference and confirmation layer rather than a discovery source in its own right — it tells us who currently owns a facility, not when or why that changed, and in this system it is refreshed periodically rather than continuously.

### CMS Provider Information (Care Compare)

This is CMS's public quality-ratings dataset for nursing homes — the same data that powers the consumer-facing Medicare Care Compare website. It includes each facility's 5-star overall rating, staffing rating, health inspection rating, and whether the facility is flagged as a "Special Focus Facility" (CMS's designation for homes with a documented history of serious, persistent quality problems). It's refreshed monthly. The tracker uses it to add quality context to a tracked deal — for instance, flagging when a poorly-rated or Special Focus facility changes hands, which can be a relevant signal for oversight and policy purposes. It does not itself contain any ownership information.

### News and Trade Press

The tracker also monitors industry news: trade publications that cover skilled nursing and senior living (Skilled Nursing News, McKnight's Long-Term Care News, Senior Housing News) via their RSS feeds, along with Google Alerts email notifications for relevant coverage across the broader web. This source often surfaces information — deal price, portfolio size, the parties' stated rationale — that regulatory filings never disclose, and it does so the same day a deal is announced. Its limitation is that it only captures deals that get publicly announced or covered; quiet transactions between private parties may never appear here, and some announced deals fall through and never actually close.

### SEC EDGAR (Securities and Exchange Commission Filings)

Publicly traded companies are legally required to disclose material events — including nursing home acquisitions — to the SEC, typically via an 8-K filing within four business days of the event. The tracker monitors these filings for major publicly traded owners and real estate investment trusts (REITs) active in the nursing home sector, such as Welltower, Sabra, and Ensign. This is a fast and highly reliable source for the subset of the industry it covers. Its central limitation is coverage: the large majority of nursing home owners are privately held companies that have no SEC reporting obligation at all, so this source only ever captures a fraction of overall deal activity.

---

## Scope: Skilled Nursing Facilities Only

This tracker is deliberately scoped to **Skilled Nursing Facilities (SNFs)** — the segment of long-term care that is Medicare/Medicaid-certified to provide short-term rehabilitative care and long-term nursing care, commonly referred to as "nursing homes."

It excludes **Assisted Living (AL) and Memory Care (MC)** facilities. These serve a different population (residents who need less medical support than skilled nursing provides), and critically, they are regulated differently — typically licensed at the state level rather than federally certified through Medicare/Medicaid the way SNFs are. Because AL/MC facilities fall outside CMS's certification and ownership-reporting framework, the tracker's core verification method (matching against CMS records) doesn't apply to them, and mixing them in would blur the ownership picture this tool is built to provide.

In practice, this means: deals involving operators that run assisted living or memory care exclusively are automatically excluded from the feed. For operators that run both nursing homes and assisted living/memory care communities, a deal is only excluded if it specifically names an assisted-living or memory-care facility — their legitimate skilled nursing deals are still tracked. State pre-closing filings are filtered at the source instead: each state's reader keeps only nursing facility filings (by the state's own facility type or its licensed nursing home list), so a large portfolio sale that happens to include one assisted living community is still tracked.

---

## Signal Confidence

Every deal in the tracker is labeled with one of three confidence levels, shown directly on the live feed:

| Label | What it means |
|---|---|
| **UCC Signal** | An early warning sign. A lender has filed a state financing statement tied to this operator or facility, suggesting a deal may be underway. This has not yet been confirmed against federal (CMS) ownership records. |
| **UCC Confirmed** | A deal identified through another source (news coverage, an SEC filing, etc.) has since been corroborated by a matching state UCC-1 financing filing — two independent sources now point to the same transaction. |
| **CMS Confirmed** | The deal has been verified against CMS's official nursing home ownership records — the highest level of confidence the tracker assigns. |

A deal with no badge shown is still under review and hasn't yet reached one of these confidence tiers. Most State CON deals start out this way, because the filing comes before CMS has recorded anything: a State CON deal is only marked CMS Confirmed when CMS shows an ownership record dated around or after the filing (an older record would just be the owner who is selling). A "+N other sources" tag means the same deal was also reported by other sources.

---

## CCN Coverage by Source

A CMS Certification Number (CCN) is the unique federal ID assigned to every Medicare-certified nursing home. When a tracked deal can be matched to a specific CCN, it means the tracker has pinned the deal to one exact, identifiable facility rather than just a company name. Match rates vary substantially by source, largely because CMS's own CHOW filings already include the CCN directly, while a state UCC-1 filing or a news article has to be matched to one indirectly.

| Source | Deals Tracked | Matched to a Specific Facility (CCN) | Match Rate |
|---|---:|---:|---:|
| State UCC-1 Filings | 1,653 | 1,361 | 82% |
| CMS CHOW | 572 | 572 | 100% |
| News / Trade Press (RSS + Google Alerts) | 162 | 24 | 15% |
| State Pre-Closing Filings | 150 | 32 | 21% |
| SEC EDGAR | 12 | 2 | 17% |

CMS CHOW deals reach 100% because the CMS filing itself already reports the exact CCN of the facility that changed hands — no matching is required. News and EDGAR sources have low match rates in part because those deals often involve multi-facility portfolios without individually named facilities, or facility names not yet reflected in CMS's ownership data. State pre-closing filings match at a low rate for a different reason: they arrive before the sale shows up in CMS, so their match rate rises as CMS catches up.

---

## Summary Statistics

| Metric | Value |
|---|---:|
| Deals on the live feed | 2,549 |
| Confirmed (matched with high confidence to CMS records) | 1,971 |
| Detected (early signal, not yet confirmed) | 570 |
| Pending CMS confirmation | 8 |
| Dismissed (out of scope or duplicate; not shown) | 59 |
| States and territories covered | 53 |
| Distinct facilities (CCNs) matched to a deal | 1,276 |
| Deal date range | 2001–2026; about 42% dated October 2024 or later |
| Data current as of | September 30, 2026 |
