# Manual UCC searches

States whose UCC search can't be automated within our rules (captcha, bot
check, robots.txt or terms of use) but can be searched by a person. Each has
a worklist CSV in `Manual/`; fill it in by hand, then ingest it.

| State | Search | Why manual | Worklist | Searches |
|---|---|---|---|---|
| AZ | [apps.azsos.gov/apps/ucc/search](https://apps.azsos.gov/apps/ucc/search/), secured party (results list only the lender; open each filing for the debtor) | robots.txt closes /apps/ to automated tools | `Manual/az_manual_search_worklist.csv` | 47 lenders |
| CO | [coloradosos.gov/ucc](https://www.coloradosos.gov/ucc/pages/home.xhtml) → advanced search, secured party | terms prohibit robots and data mining | `Manual/co_manual_search_worklist.csv` | 47 lenders |
| IN | [inbiz.in.gov/ucc-browse](https://inbiz.in.gov/ucc-browse), secured party (moved from bsd.sos.in.gov by 2026-11) | invisible reCAPTCHA only; since 2026-11-11 searched in the person's own Chrome via Claude in Chrome, no hand step (see below) | `Manual/in_manual_search_worklist.csv` | 47 lenders |
| MD | [ucc.egov.maryland.gov](https://ucc.egov.maryland.gov/UCCFiling/UCCMainPage.aspx) → File / Search Now → Name Search, Secured Party, Organization, UCC-1 only | captcha on every search | `Manual/md_manual_search_worklist.csv` | 47 lenders |
| WV | [apps.wv.gov/SOS/UCC](https://apps.wv.gov/SOS/UCC/Search), secured party | reCAPTCHA before every search | `Manual/wv_manual_search_worklist.csv` | 47 lenders |
| ME | free search at www1.maine.gov (robots.txt) or InforME (paid) | robots.txt / paid | `Manual/me_manual_search_worklist.csv` | 9 debtor names |
| NH | [quickstart.sos.nh.gov](https://quickstart.sos.nh.gov/), UCC search, secured party if offered (not seen yet: the bot check stopped every visit so far); paper UCC-11 requests are $10 | bot check | `Manual/nh_manual_search_worklist.csv` | 13 lenders, priority-ordered (7 first) |

Not worth a manual pass: IL and VT search debtor names only (IL ~700
names), and AK has ~18 homes.

Dropped 2026-11-10: CT and IA search debtor names only (no secured-party
search), and WA's captcha made a hand search too slow. All three are
covered by licence lists instead (`scraper/licence_lists.py`; CT's roster
is downloaded monthly in Chrome). Their old worklists stay in `Manual/`.

## Filling in a worklist

Each row's `query_name` is one lender (or, for ME, one debtor name). Search
it, then for every filing that is

- from the **last two years**, and
- against a **nursing home** entity (an operator, a property company, or a
  holding company of one: "… Nursing and Rehabilitation", "… SNF Opco LLC",
  "… Healthcare Properties"; not hospitals, clinics, dentists or people),

fill one row: `found_y_n` = `y`, debtor name, secured party, filing number,
filing date and lapse date as the portal shows them (MM/DD/YYYY is fine).
For a lender with several filings, copy the row once per filing. Leave
`found_y_n` blank for lenders not searched yet, and `n` for no hits.

Filings made the same day by the same lender for a home's property company
and operator are one deal; list both, the pipeline groups them.

## Ingesting

```bash
venv/bin/python3 scripts/ingest_manual_ucc.py AZ Manual/az_manual_search_worklist.csv
```

Then export and deploy as usual (README, "Deployment (gh-pages)").

## Indiana in the person's Chrome (2026-11-11)

INBiz moved UCC Browse to https://inbiz.in.gov/ucc-browse. It has an invisible
reCAPTCHA, so no hand step is needed, and no robots.txt. Its "Filing Date
From/To" filter is broken (the server rejects the date format the page
sends), and results are sorted by lender name. Instead, show 100 per page,
page through, and keep filing numbers >= YYYYMMDD of the cutoff (IN numbers
start with the filing date). The results don't list debtors: look each new
filing up by number and read the Debtor tab. Use the page's own buttons
only; every search carries a fresh reCAPTCHA token, so never replay the API.
Load with `scripts/ingest_lender_rows.py IN <file>` (same nursing-home filter
as automated lender search). Hospital licensees (e.g. Pulaski Memorial
Hospital for Hickory Creek homes) are dismissed, as on 2026-10-29.
First run 2026-11-11: 47 lenders, 29 filings since 10/14, 5 already stored,
1 new nursing home loan (a duplicate of Majestic Care's 9/29 loan, hidden).
