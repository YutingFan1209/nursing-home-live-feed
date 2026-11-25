# Questions for T: hospice & rehab v1

Repo copy of the ranked decision list (shared doc: https://claude.ai/artifact/KDd53c44A4TCdWWi7LuujV).
As of 2026-11-25. Each has a default the site uses until Tyler answers.

| # | Question | Options | Default | What waits on it |
|---|---|---|---|---|
| 1 | Should "rehab" also cover outpatient therapy chains? | (a) inpatient rehab hospitals only (b) add outpatient therapy chains | (a) | extractor scope for therapy-clinic roll-ups |
| 2 | Does a pure home health deal count? | (a) only with a hospice in it (b) home health alone too | (a) | extractor scope; homehealthcarenews.com stays excluded |
| 3 | Show hospice changes that add a new parent above an owner who stays (150: PE recaps, holdcos)? | (a) hidden (b) "control change" cards | (a) | PE recaps invisible under (a) |
| 4 | Count hospital sales that include an inpatient rehab unit (848 units)? | (a) no, freestanding rehab hospitals only (b) yes | (a) | hospital CHOW file; general hospital sales. CMS provider file, 12 months to 2026-11-25: 1 freestanding rehab hospital sold (Southern Kentucky, on the site) vs 5 general hospitals with rehab units (e.g. Good Samaritan University Hospital NY, 66 rehab beds; Our Lady of Fatima RI, 24), so (b) is where most rehab volume is |
| 5 | LTACHs in or out? | (a) out (b) in | (a) | LTACH deals stay off the site |
| 6 | Read state CON / licence records for hospice too (NC, NY, AL, MS...)? | (a) CMS + news only (b) state by state | (b), built 11-25 | earlier hospice signal; CA started 2026-11-24 (licence list + OHCA notices). 11-25: NC exemptions, TN agenda CHOWs and WA CN exemption requests now keep hospice + rehab hospital filings (NC Liberty/ECU, WA Heart to Heart Hospice so far); NY has ~1 hospice filing a year, MD posts no hospice notices. Still a yes/no for T: keep or drop |
| 7 | First-load lookback | (a) 12 months (b) new only (c) 24 months | (a) 43 hospice + 1 rehab, plus 22 hospice sales from CMS enrollment changes (2026-11-24) | the backfill |
| 8 | Name a person seller (21 of 43 hospice sales)? | (a) blank (b) CMS name | (a) | seller completeness |
| 9 | Health system taking over its own affiliate's hospice: a sale? | (a) show (b) hide | (a) | ~4 cards a year |
| 10 | Site default view | (a) nursing homes & AL default, filter for the rest (b) all mixed | (a) | nothing |
| 11 | Tyler said hospice and rehab are "under the same umbrella": which one? | (a) post-acute care as CMS uses it (SNF, IRF, LTACH, home health, hospice) (b) hospice + home health (same companies) (c) other | none: hospice and IRFs as separate types | settles Q2 and Q5; home health is cheap to add (same CMS file format) |

Code: scraper/pac_owner_changes.py (detector), pipeline/deal_labels.py (facility types), the care-type filter in dashboard/frontend/src/App.jsx.
