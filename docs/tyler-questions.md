# Questions for Tyler

Decisions that change what the tracker counts or shows, waiting on Tyler.
Add new ones under **Open**. When he answers, move the item to **Answered**
with the date and what was done about it.

## Open

Nothing open as of 2026-10-12.

## Answered

- **Keep the OK, MI and MS CON sources?** (relayed 2026-10-12) Yes. They
  stay live as built (86 deals as of 2026-10-12: OK 52, MI 22, MS 12); their
  missing buyer reads "Buyer not disclosed".
- **Can the site show private individuals' names?** (relayed 2026-10-12)
  Yes. `SHOW_INDIVIDUAL_NAMES` in `scripts/export_deals.py` stays `True`.

- **NY UCC filings against individuals: main feed or a separate group?**
  (2026-10-12) Main feed, with a different label to mark the difference.
  Built: they appear in the Financing tab with an "Individual filing" tag;
  matched to the NY homes the person owns in CMS (`ucc_individual_matches`):
  816 of 845 match, 229 to one home (the card links to it), the rest list
  "Owner of X, Y, Z +N more" as possible homes, never one firm link; 29
  unmatched say so. The names question is still open (Open, 2).
- **Same-owner control changes (ArchCare at Eger)?** (relayed 2026-10-12)
  Don't hide them: show with a "Control change" tag and keep them out of
  sale counts. Built in `pipeline/deal_labels.py` for every state; Eger reads
  "ArchCare becomes active parent of Eger Health Care and Rehabilitation
  Center".
- **Labeling deals with no buyer?** (relayed 2026-10-12) "Buyer not
  disclosed" as the buyer. Built narrower, for accuracy: only when the
  source says the buyer is undisclosed or a state filing has no buyer field
  (OK/MI/MS); a buyer the source describes shows the description ("a
  national owner"), and leases get their own type.

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
