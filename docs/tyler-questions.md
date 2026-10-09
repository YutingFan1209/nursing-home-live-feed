# Questions for Tyler

Decisions that change what the tracker counts or shows, waiting on Tyler.
Add new ones under **Open**. When he answers, move the item to **Answered**
with the date and what was done about it.

## Open

### 1. Do old loans count as deals?
*Raised 2026-10-06. Still open after two cancelled check-ins (10-02, 10-09).*

About two thirds of UCC entries are financing filed more than two years ago,
mostly the seller's old loans rather than a new sale. The CMS ownership file
also has ~3,600 free mortgage/security-interest rows (TX: 211 facilities)
that are parked until this is decided.

- **Recommendation:** keep old financing as each facility's history and
  leave it out of the feed and the counts.
- **Depends on it:** turning the CMS lender rows into a "known lenders" list
  for each facility (fills in TX, where UCC data costs money).

### 2. How should deals with no buyer be labelled?
*Raised 2026-10-06.*

OK, MI and MS publish the facility but not the buyer, so those CON deals
have no acquiring entity on the site.

- **Question:** how the site should label them.

### 3. Does a same-owner control change count as a deal?
*Raised 2026-10-09. Ask the week of 2026-10-12.*

The first NY run (`scraper/con_ny.py`) flagged **ArchCare at Eger**
(Staten Island, 378 beds, CCN 335332, PHHPC project 251167-E, approved
2025-12-04). ArchCare (Catholic Health Care System, the Archdiocese of New
York's health care network) went from passive to active parent of a home it
already owned. There's no buyer, seller or price, but New York requires the
same council approval as a sale.

- **Recommendation:** hide these (dismiss), since the tracker follows
  ownership changing hands.
- **For now:** the deal stays in the feed as "detected".
- **Also applies to:** any similar case from another state's CON source.

## Answered

- **Build the NY CON source?** Built 2026-10-09 when the user gave the
  go-ahead (option A). First run found 9 of 9 known sales 14–28 days before
  the council vote.
- **Paid UCC data, browser-session cadence, CDPH call:** decided by the user
  2026-10-06: no paid data; browser-only sessions every two weeks; no CDPH
  call.
