# State UCC-1 Search Feasibility

Research date: **2026-09-30**. Research only: web research on each state's official UCC search plus **one plain GET per portal** (`curl_cffi`, Chrome impersonation) to see whether the search page loads and whether a bot wall or captcha is on it. No searches were run against any portal, so "no wall seen" means the page loaded, not that a batch of searches will get through (see the PA/OH history in `docs/data-sources.md`).

Scope: every state without a UCC adapter (all but NY, KY, OH, PA, NJ, CA, ME).

**Question:** where could we add a UCC-1 debtor or secured-party search the way we did for KY/NJ, and at what cost?

---

## One structural caveat first

A UCC-1 against a registered organization is filed in the **state where the debtor is organized**, not where the facility is (UCC §9-307). Our state adapters search the facility state, which works when the opco/propco LLC is formed in-state (most are). An LLC formed in **Delaware** has its UCC-1 in Delaware, and Delaware only lets **authorized searchers** query its UCC index ($25 per debtor plus a mandatory $25 expedite fee). So Delaware-formed borrowers are invisible to us in every state, and there is no cheap fix.

---

## Summary

Ratings: 🟢 free, no login, no bot wall seen on the search page · 🟡 free but a captcha or bot wall is on the page, or a cheap paid/flat-fee account · 🔴 paid per search, subscription-only, or authorized searchers only.

| State | Official search | Access / cost | Page check (2026-09-30) | Rating |
|---|---|---|---|---|
| **AL** | [arc-sos UCC name search](https://arc-sos.state.al.us/CGI/UCCNAME.MBR/INPUT) | Free | Loads, plain HTML (CGI) | 🟢 |
| **AK** | [DNR Recorder's Office UCC](https://dnr.alaska.gov/ssd/recoff/ucc) | Free | Loads | 🟢 (≈18 nursing homes) |
| **AZ** | [azsos UCC search](https://apps.azsos.gov/apps/ucc/search/) | Free; debtor **and** secured-party search with a date range (wildcard match needed); results list only the secured party, so each filing needs a second lookup for the debtor. Paid monthly index: $1,800/yr | Real Chrome works, no captcha (checked 2026-10-05) | 🟢 Not built |
| **AR** | [bcs.sos.arkansas.gov](https://bcs.sos.arkansas.gov/) | Free lookup ($6 for official UCC-11) | JS single-page app (shell only on GET) | 🟢 |
| **CO** | [SOS UCC standard search](https://www.sos.state.co.us/ucc/pages/search/standardSearch.xhtml) | Free; secured party via advanced search | Loads; Cloudflare script, no challenge | 🟢 |
| **CT** | [service.ct.gov lien search](https://service.ct.gov/business/s/onlineenquiry?language=en_US) | Free, no account | Salesforce app with **reCAPTCHA** on the page | 🟡 |
| **DE** | Authorized searchers only | $25/debtor + $25 expedite | n/a | 🔴 (see caveat above) |
| **DC** | Recorder of Deeds (CountyFusion) | Login | TLS error to plain clients | 🔴 (≈17 nursing homes) |
| **FL** | [floridaucc.com/search](https://floridaucc.com/search) | Free; debtor and secured party; **daily and full data downloads** | JS app shell, no wall | ✅ Built 2026-10-05 from the data downloads (`ucc/fl_download.py`) |
| **GA** | [GSCCCA UCC index](https://search.gsccca.org/UCC_Search/) | Subscription (~$15/month, unlimited) | Login | 🟡 (cheap flat fee) |
| **HI** | Bureau of Conveyances RecordEASE | Login | Login page | 🔴 (≈45 nursing homes) |
| **ID** | [sosbiz UCC search](https://sosbiz.idaho.gov/search/ucc) | Free | Cloudflare script | 🟢 |
| **IL** | [apps.ilsos.gov/uccsearch](https://apps.ilsos.gov/uccsearch/) | Free (image copies $20) | Page loads, but headless browsers get an Akamai 403 and every search submission from a real Chrome gets an Akamai challenge, then a **reCAPTCHA** (checked 2026-10-05) | 🟡 Not automatable — **on hold (2026-10-05)**, manual search only |
| **IN** | [INBiz UCC search](https://inbiz.in.gov/BOS/PublicSearch/Search) | Free; debtor and secured party | Loads. ⚠️ Some guides say an Access Indiana login is needed; unverified | 🟢 |
| **IA** | [filings.sos.iowa.gov UCC](https://filings.sos.iowa.gov/UCCSearch/UCC) | Free | **reCAPTCHA** + Cloudflare | 🟡 |
| **KS** | mykansas.ks.gov UCC | $10/debtor, subscription | n/a | 🔴 |
| **LA** | Parish clerks; SOS Direct Access | $400/year flat subscription | n/a | 🟡 (flat fee) |
| **MD** | [SDAT UCC search](https://egov.maryland.gov/sdat/uccfiling/uccmainpage.aspx) | Free | Loads, no wall | 🟢 |
| **MA** | [corp.sec.state.ma.us UCC](https://corp.sec.state.ma.us/corpweb/UCCSearch/UCCSearch.aspx) | Free | **Incapsula** (same vendor as PA/CA) | 🟡 |
| **MI** | [ucc.michigan.gov quick search](https://ucc.michigan.gov/ucc-search) | Free name quick search; $6 official search | Loads | 🟡 (quick search may only confirm names) |
| **MN** | mblsportal debtor name search | Account + paid per search/subscription (file-number search is free) | n/a | 🔴 |
| **MS** | [business.sos.ms.gov](https://business.sos.ms.gov/) | Free preliminary search | JS shell; the old portal had Akamai | 🟡 |
| **MO** | [bsd.sos.mo.gov UCC](https://bsd.sos.mo.gov/Loans/UCCSearch.aspx) | Free, no login | Cloudflare script; plain GET was redirected to an error page | 🟡 |
| **MT** | [biz.sosmt.gov UCC](https://biz.sosmt.gov/search/ucc) | $7 per debtor certificate or subscription | **Cloudflare challenge (403)** | 🔴 |
| **NE** | business.nebraska.gov | $4.50/search (subscription dropped Dec 2025) | n/a | 🟡 (cheap paid) |
| **NV** | ORION portal (UCC module since Dec 2025) | Free basic search | **Incapsula** | 🟡 |
| **NH** | QuickStart UCC | Free | Loads | 🟢 (small) |
| **NM** | [enterprise.sos.nm.gov](https://enterprise.sos.nm.gov/) | Free, no login; debtor and secured party | JS shell | 🟢 |
| **NC** | [sosnc.gov UCC search](https://www.sosnc.gov/online_services/search/UCC_Search) | Free; debtor and secured party. **Bulk data: weekly FTP feed, $750 setup + $250/yr** | **Cloudflare challenge (403 "Just a moment")**, the NY situation | 🟡 (NY's real-Chrome CDP approach should work) |
| **ND** | NDCIS | Subscription | n/a | 🔴 |
| **OK** | [okcc.online](https://www.okcc.online/) (Oklahoma County Clerk is the statewide central filing office) | Free, no login | Loads | 🟢 |
| **OR** | [secure.sos.state.or.us/ucc](https://secure.sos.state.or.us/ucc/searchHome.action) | Free; debtor and secured party, with a date range | F5 bot defense (TSPD) on real use: plain HTTP gets a JS challenge, Playwright a blank page; a person's own Chrome works | 🟡 Live sessions only (built 2026-10-05: `scripts/or_ucc_lender_search.js`) |
| **RI** | [business.sos.ri.gov UCC](https://business.sos.ri.gov/corpweb/uccsearch/uccsearch.aspx) | Free | Loads, no wall | 🟢 |
| **SC** | [ucconline.sc.gov](https://ucconline.sc.gov/UCCFiling/UCCMainPage.aspx) | Free, no subscription needed; debtor and secured party | Loads, no wall | 🟢 |
| **SD** | SOS UCC | $300/year search subscription | n/a | 🟡 (flat fee, small state) |
| **TN** | [tnbear UCC search](https://tnbear.tn.gov/UCC/Ecommerce/UCCSearch.aspx) | Free search by debtor or document no. (one guide quotes $15 for official searches) | Timed out from here | 🟡 (unverified) |
| **TX** | SOS Portal | **$1 per search**, account | n/a | 🟡 (cheap, but ~1,000 names = ~$1,000 a full run) |
| **UT** | ucc.utah.gov | $12/search | n/a | 🔴 |
| **VT** | [bizfilings UCC inquiry](https://bizfilings.vermont.gov/online/UCCInquire/) | Free | Loads | 🟢 (small) |
| **VA** | [SCC CIS UCC search](https://cis.scc.virginia.gov/UCCOnlineSearch/UCCSearch) | Free; results list secured parties | Cookie-consent redirect + **reCAPTCHA** | 🟡 |
| **WA** | [DOL UCC search](https://fortress.wa.gov/dol/ucc/search.aspx) | Free; debtor and secured party | **reCAPTCHA** (grecaptcha) | 🟡 |
| **WV** | [apps.wv.gov/SOS/UCC](https://apps.wv.gov/SOS/UCC/Search) | Free | The search button stays disabled until a **reCAPTCHA** is solved (checked 2026-10-05) | 🟡 Not automatable — **on hold (2026-10-05)**, manual search only |
| **WI** | [DFI lien search](https://dfi.wi.gov/Pages/BusinessServices/UCC/SearchLienFilings.aspx) | Free, no login | Loads | 🟢 |
| **WY** | wyobiz UCC | Basic search free; certified search needs subscription | Empty response to a plain GET | 🟡 |

**Tally:** 17 🟢, 18 🟡, 8 🔴, plus FL built and the 7 states built before it. A 🟢 here means the search *page* loaded cleanly; Illinois showed a captcha only after a search was submitted, so test a real search before trusting the rating.

---

## Recommendation (build order)

Weighted by nursing home count (CMS) and how easy the portal looked:

1. ~~FL~~ (built from the data downloads) and ~~IL~~ (captcha on every search; not automatable).
2. **IN**, **MO**, **WI**, **MD**, **SC**, **OK**, **OR**, **AL**, **WV**: free, mid-size, mostly plain pages. MO needs a look at why the plain GET bounced to an error page.
3. **NC**: free and rich (secured-party search), but behind the same Cloudflare challenge as NY, so it needs the `ucc/chrome_cdp.py` real-Chrome path. Worth it: NC CON already shows NC is active.
4. **TX**: the largest nursing home state, $1 per search. Affordable as a targeted monthly run over new deal names, not a full nightly sweep.
5. Captcha states (**CT**, **IA**, **VA**, **WA**) and Incapsula states (**MA**, **NV**): only if Claude in Chrome or manual review is acceptable, same as OH today.
6. Skip: **DE**, **KS**, **MN**, **ND**, **UT**, **MT**, **HI**, **DC** (paid per search, subscription-only, or login).

## Lender search (2026-10-05)

Searching by secured party for nursing home lenders, rather than by known borrower names, finds borrowers no name list contains. Pennsylvania's first run turned up 103 recent nursing home filings, 96 of them new to the tracker (`ucc/lender_search.py`). Of the states rated above, **FL, AZ, NC, OR and WV** also support a secured-party search, so lender search should be part of any adapter built for them. California's unified index covers secured parties too.

## Open items

- None of the 🟢 ratings are proven by a real search. Run a single-name probe per state before building, then a small batch; PA and OH both passed probes and then failed under volume.
- **IN:** whether the public UCC search needs an Access Indiana login.
- **TN:** free vs $15 search, and whether the portal is reachable (it timed out).
- **MI:** whether the free quick search returns filing detail (secured party, date) or only name matches.
- **WY / MS / NM / AR / FL:** single-page JS apps; the actual search API needs to be found in the browser's network tab before an adapter can be written.
