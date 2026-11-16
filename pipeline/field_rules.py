"""
The site's field rules, written once.

The extraction prompt (pipeline/extractor.py) and the audit judge
(evals/judge.py) both read these, and hand labels (evals/gold/) follow them,
so a rule changed here changes everywhere at once. Edit wording only with a
fresh measurement: tests/test_prompt_measured.py fails until
`python -m evals.score extract` and `python -m evals.judge gold` are rerun.
"""

# What each extracted field holds. Worded for the extractor; the judge reads
# the same text as the standard a card is held to.
FIELD_RULES = """- acquiring_entity, seller_entity and lender hold a party's NAME only. If the article describes a
  party without naming it ("unnamed", "undisclosed", "a family-owned operator", "an independent
  owner in Napa", "third-party debt", "a life insurance company"), use null and put the description
  in the rationale. A manager or operator hired to run the home is not the buyer, and a broker or
  arranger is not the lender
- Write a name without explanatory brackets or asides: "NHC/OP, L.P.", not "NHC/OP, L.P. (a
  subsidiary of National HealthCare)"; "CareTrust REIT", not "CareTrust REIT (the buyer)"
- facility_count, states, deal_value_m and financing_amount_m describe THIS deal only. Never use a
  company's whole portfolio, its total for the quarter, or the sum of several deals the article
  lists: an earnings release listing three acquisitions is three deals, each with its own homes,
  states and price
- acquisition_date: the closing or effective date the article gives for this deal ("consummated
  July 1", "effective Oct. 1", "as of November 1 ... has closed", a dated entry "October 27, 2026 -
  X bought Y"), in the publication year unless the text says otherwise. A month without a day ("the
  lease commenced in July 2026") is the 1st of that month. Null for a quarter or year alone, or no
  date
- When a loan pays for a purchase (acquisition loan, bridge-to-HUD acquisition financing, "to
  finance the acquisition of"), say "acquisition financing" in the rationale; when it pays off
  existing debt, say "refinancing"; a working-capital or revolving line on its own is neither"""

# How the site shows records. Code applies these at export
# (pipeline/deal_labels.py, scripts/export_deals.py); the judge needs them so
# it doesn't flag a site convention as an error.
SITE_RULES = """- A New Jersey UCC card's lender reads "Not available ..." (NJ's search never returns it).
- A CMS or state-filing card may carry the lender from a UCC filing on the same home near the sale
  date, so a lender missing from the CMS record is not wrong by itself.
- Filing boilerplate left on a correct lender ("its successors and assigns, as agent ...") or two
  secured parties of the same filing joined with ";" is minor, not wrong.
- deal_type values: ownership_change (a sale/purchase), acquisition_financing (a loan that funds a
  purchase: "acquisition loan", "bridge acquisition", or a UCC filing within about 6 months of a new
  owner in CMS records), refinancing (a loan on homes the borrower already owned), financing (a loan
  whose purpose the source doesn't give), lease, operations_transfer, control_change, amendment,
  non_deal_lien (a lien that isn't a deal). A working-capital line on its own is financing, not
  acquisition_financing.
- status values: reported (news says it happened), expected (announced, pending approval, or a
  planned sale), confirmed (a CMS record backs it), on_file (a UCC filing: a record of a loan, not of
  a sale). A planned or pending sale shown as reported is wrong, EXCEPT: once a pending sale's
  expected close date has passed (or 90 days after a filing, received or notice date when the source
  gives no expected close), the site assumes it closed and shows reported with assumed_closed: true.
- acquisition_date: the closing date the source gives, or the CMS record's date when CMS confirms
  the card. For a state filing (CON, licence list, PHHPC, CHOW notice) that gives no closing date,
  the site shows the filing, received, notice or meeting date. A quarter placeholder (2026-10-01 for
  "4Q26") is minor.
- deal_value_m, financing_amount_m: US dollars, millions. A foreign-currency figure converted to
  dollars, or a price the source says was not disclosed, is wrong.
- facility_type: skilled_nursing or assisted_living (memory care counts as assisted living).
- ccns: the CMS IDs of the homes."""
