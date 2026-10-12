# Questions for Tyler

Decisions that change what the tracker counts or shows, waiting on Tyler.
Add new ones under **Open**. When he answers, move the item to **Answered**
with the date and what was done about it.

## Open

### 1. Keep the OK, MI and MS CON sources, and how to label deals with no buyer?
*Raised 2026-10-06. Approval question added 2026-10-12.*

OK, MI and MS were built and put live before Tyler approved them (86 deals
on the site as of 2026-10-12: OK 52, MI 22, MS 12; 19 confirmed by CMS).
They publish the facility but not the buyer, so those CON deals have no
acquiring entity on the site.

- **Question:** keep these states at all? If yes, how should the site label
  the missing buyer?
- **If no:** take them off the site.

### 2. Does a same-owner control change count as a deal?
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

### 3. What are NY's UCC filings against individual owners?
*Raised 2026-10-09.*

845 of NY's 943 live UCC deals are filed against a person (an owner found
through the individual-owner search), not a facility or company: e.g.
Jeffrey Rubin / Yardville National Bank, Robert Butler / a federal credit
union. Many look like personal loans (credit unions, a storage company,
farm-equipment lenders) rather than nursing home financing. They can't be
split into acquisition vs refinancing, because there's no facility to
check against CMS.

- **Recommendation:** give them their own "owner's personal filing" group,
  hidden by default, rather than calling them refinancings.
- **For now:** they stay as plain "Financing", visible by default.

## Answered

- **Do old loans count as deals?** (2026-10-09) Keep refinancing UCC
  filings, but as their own category, and don't show repeats of the same
  loan. Built in `pipeline/ucc_category.py`, applied when `deals.json` is
  written: UCC financing is split into acquisition financing / refinancing /
  financing by CMS owner dates and the borrower's earlier lenders, and one
  borrower + lender shows as one row. The site hides refinancings by
  default. The parked CMS lender rows can now be used as each facility's
  lender history.
- **Build the NY CON source?** Built 2026-10-09 when the user gave the
  go-ahead (option A). First run found 9 of 9 known sales 14–28 days before
  the council vote.
- **Paid UCC data, browser-session cadence, CDPH call:** decided by the user
  2026-10-06: no paid data; browser-only sessions every two weeks; no CDPH
  call.
