# Manual UCC searches

States whose UCC search can't be automated within our rules (captcha, bot
check, robots.txt or terms of use) but can be searched by a person. Each has
a worklist CSV in the repo root; fill it in by hand, then ingest it.

| State | Search | Why manual | Worklist | Searches |
|---|---|---|---|---|
| AZ | [apps.azsos.gov/apps/ucc/search](https://apps.azsos.gov/apps/ucc/search/), secured party (results list only the lender; open each filing for the debtor) | robots.txt closes /apps/ to automated tools | `az_manual_search_worklist.csv` | 47 lenders |
| CO | [coloradosos.gov/ucc](https://www.coloradosos.gov/ucc/pages/home.xhtml) → advanced search, secured party | terms prohibit robots and data mining | `co_manual_search_worklist.csv` | 47 lenders |
| IN | [bsd.sos.in.gov/PublicUCCSearch](https://bsd.sos.in.gov/PublicUCCSearch), secured party | captcha on every search | `in_manual_search_worklist.csv` | 47 lenders |
| MD | [ucc.egov.maryland.gov](https://ucc.egov.maryland.gov/UCCFiling/UCCMainPage.aspx) → File / Search Now → Name Search, Secured Party, Organization, UCC-1 only | captcha on every search | `md_manual_search_worklist.csv` | 47 lenders |
| WV | [apps.wv.gov/SOS/UCC](https://apps.wv.gov/SOS/UCC/Search), secured party | reCAPTCHA before every search | `wv_manual_search_worklist.csv` | 47 lenders |
| ME | free search at www1.maine.gov (robots.txt) or InforME (paid) | robots.txt / paid | `me_manual_search_worklist.csv` | 8 debtor names |
| NH | QuickStart (bot check); paper UCC-11 requests are $10 | bot check | — | — |

Not worth a manual pass: IL and VT search debtor names only (IL ~700
names), and AK has ~18 homes.

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
venv/bin/python3 scripts/ingest_manual_ucc.py AZ az_manual_search_worklist.csv
```

Then export and deploy as usual (README, "Deployment (gh-pages)").
