# State CON / Ownership-Approval Feasibility

Research date: **2026-09-28**. Research only: single page and document fetches, no bulk scraping.
Scope: the 10 states where NCSL lists nursing home ownership transfer as a CON trigger (NY, ME, MI, MO, MS, AR, OK, MA, CT, AL), plus KY as an expansion-signal comparison.

**Question:** can a state's public CON or ownership-approval record give the tracker an earlier or more complete nursing home ownership-change signal than news, UCC-1 filings and CMS CHOW?

---

## Summary

| State | Transfer needs state approval? | Where published | Format / cadence | Buyer / seller named? | CCN join | Rating | Confidence |
|---|---|---|---|---|---|---|---|
| **NY** | ✅ Yes, PHHPC establishment approval | health.ny.gov PHHPC meeting pages | Agenda PDFs (100–700 pp) with per-project exhibits; ~6 decision meetings/yr | ✅ Both, plus member % and often price | ✅ 10/10 by name + city | 🟢 Green | High |
| **AL** | ⚠️ Notice ≥20 days pre-close (Rule 410-1-7-.04). Transfers that fit the notice categories skip full CON | SHPDA "Notices of Change of Ownership" | HTML table + one PDF per filing; posted per filing | ✅ Both, plus APA and closing dates | ✅ Name + state facility no. + address | 🟢 Green | High |
| **OK** | ✅ Yes, 63 O.S. §1-852 (CON to acquire an LTC facility) | OSDH "The Notice" | 2-page monthly PDF table; ~1–2 month posting lag | ❌ Facility only (sometimes the applicant entity) | ⚠️ Name only, no city | 🟢 Green | High |
| **ME** | ✅ Yes, 22 M.R.S. §329(1) | DHHS "Current Healthcare Reviews" page | HTML list of documents per case (LOI → notice → analysis → decision); updated per event | ✅ In the LOI and decision PDFs; some are scanned images | ✅ Names + addresses in LOI exhibits | 🟢 Green (low volume) | High |
| **MI** | ✅ Yes, MCL 333.22209 (acquisition of existing NH/HLTCU) | MDHHS CON "Activity Reports" | Monthly **XLSX** files (LOIs, applications, decisions) | ❌ Facility, city, county and cost only; description is cryptic ("NEW LEASE [35 YEARS]") | ⚠️ Name + city; state Facility ID ≠ CCN | 🟡 Yellow | Medium |
| **MS** | ✅ CHOW application approved by MSDH (§41-7-191; CHOW rules) | MSDH "CON Weekly Reports" | Weekly PDF; CHOW section; **items drop off 30 days after completion** | ❌ Facility + transaction type (buyer rarely named) | ⚠️ Name + city/county | 🟡 Yellow | Medium |
| **KY** *(comparison)* | ❌ Acquisition is notice-only (KRS 216B.065); CON covers new or relocated beds | CHFS "CON Newsletter" | Monthly DOCX | n/a for ownership changes; shows bed relocations and new NFs | ✅ Applicant + city/county | 🟡 Yellow (expansion only) | Medium |
| **MO** | ❌ **No.** RSMo 197.315.14 exempts transfer of a whole facility | n/a | n/a | n/a | n/a | 🔴 Red | High |
| **AR** | ❌ **No.** POA rule §IV.C.4: acquiring an existing facility needs no Permit of Approval | n/a (DHS OLTC licensure notice, not published) | n/a | n/a | n/a | 🔴 Red | High |
| **MA** | ⚠️ Licensure, not CON: 90-day Notice of Intent to Acquire (MGL c.111 §71; 105 CMR 153.022). DoN transfer rule covers hospitals and clinics only | Not published (found nowhere public) | n/a | n/a | n/a | 🔴 Red | Medium |
| **CT** | ⚠️ DSS CON covers transfers only *before initial licensure*; existing homes need DPH CHOW approval (PA 23-122, 120 days) | DSS page lists closures only; no public DPH CHOW list found | n/a | n/a | n/a | 🔴 Red | Medium |

**NCSL's list overstates it.** In 4 of the 10 states (MO, AR, MA, CT), buying an existing nursing home either needs no CON or goes through a licensure process that isn't publicly posted. AL is technically a notice rather than a CON review, but it's the most useful public record of the group.

### Recommendation (build order)

1. **AL:** an HTML table plus one PDF per filing, filed ≥20 days before closing, naming buyer, seller and closing date. Cheapest and richest.
2. **OK:** a two-page monthly PDF with ~9 nursing home acquisitions in one issue. Easy to parse. It names facilities but not buyers, so it needs CMS matching to be useful.
3. **ME:** one HTML page to diff. Low volume (~4–6 nursing facility cases a year), but the LOI appears months before anything else (see the lead-time example below).
4. **NY:** the richest data, but hard to use. It means parsing 100–700-page PDFs, some decisions appear only in *later* meetings' minutes, and the files are behind Cloudflare. Its value is completeness and detail more than earliness (see the backtest).
5. **MI:** a monthly XLSX, simple to ingest, but the thin fields make it a "something is happening at facility X" flag, not a deal record.
6. **MS:** only if the others are done. Low nursing home volume, and it needs a weekly scrape to avoid missing items in the 30-day window.
7. **Skip:** MO, AR, MA, CT.

---

## NY: 12-month backtest

### Where approvals appear

- Meeting index: <https://www.health.ny.gov/facilities/public_health_and_health_planning_council/> (returns 403 to plain HTTP clients, including WebFetch; `curl_cffi` with Chrome impersonation works).
- Each Full Council agenda PDF lists "Residential Healthcare Facilities – Establishment" items (application numbers ending in `E`). Each item has an exhibit with the buyer, seller, member percentages, APA date and price.
- Committee (EPRC) agendas come about 2–4 weeks before the Full Council vote. **That's the earliest public NY signal.** Applications are "acknowledged" 1–3 years earlier, but that date only becomes public in the exhibit.
- **Trap:** the 2026-05-07 Full Council meeting has **no agenda posted**. Its page only has EPRC documents. Its approvals (Rockville, Our Lady of Peace) are recorded only in the minutes inside the **2026-06-24** agenda packet. A scraper has to read minutes as well as agendas.
- Outcomes were confirmed from minutes (for 2025-12-04, 2026-05-07 and 2026-06-24) or transcripts (for 2026-02-19 and 2026-09-17). All 10 were approved (contingent approval).

### Nursing home ownership approvals, 2025-10 to 2026-09

| # | Decision | Facility (CCN) | Buyer (new operator) | Seller (old operator) | Notes |
|---|---|---|---|---|---|
| 231002 | 2025-12-04 | Sands Point Center for Health & Rehab, Port Washington (335022), 180 beds | JCH Operations LLC (Allen Stein 85%, Israel Nachfolger 15%) | AGMA, Inc. (Marvin Ostreicher, Agnes Zitter) | OpCo $500K; real estate $52.5M to S&N Acquisitions |
| 241018 | 2025-12-04 | Kirkhaven, Rochester (335668), 147 beds | Kirkhaven SNF OpCo LLC (Pesach Brown, Fayga Chapler, Bernadette Roesch) | Genesee Valley Presbyterian Nursing Center (NFP) | Same members as Our Lady of Peace |
| 252050 | 2025-12-04 | Maplewood Nursing Home, Webster (335572), 72 beds | Maplewood Senior Care, Inc. (NFP; Glen Cooper / FSL, Inc. d/b/a Friendly Senior Living) | Maplewood Nursing Home, Inc. (Gregory Chambery) | For-profit → nonprofit |
| 251167 | 2025-12-04 | ArchCare at Eger, Staten Island (335332), 378 beds | Catholic Health Care System d/b/a ArchCare (becomes active parent) | same (was passive parent) | ⚠️ **Not a sale.** Control change within the same system; excluded from the backtest count |
| 242307 | 2026-02-19 | Woodbury Heights (fka Cold Spring Hills), Woodbury (335555), 588 beds | 378SYWOOD LLC (Eliezer "Jay" Zelman 100%) | Cold Spring Acquisition LLC (Ch. 11) | Buyer was already the court-approved receiver from 2025-04-22 |
| 211102 | 2026-05-07 | Rockville Skilled Nursing & Rehab, Rockville Centre (335747), 66 beds | Rockville Holdings Operating LLC (Akiva Rudner, Steven Sax) | Rockville SN&R Center LLC (Teddy Lichtschein, Benjamin Landa, Mitchell Teller) | Filed 2021; real estate sold 2020 for $13.88M |
| 232239 | 2026-05-07 | Our Lady of Peace Nursing Care Residence, Lewiston (335843), 250 beds | OLP SNF OpCo LLC (Brown / Chapler / Roesch) | Our Lady of Peace, Inc. (Ascension Living affiliate) | OpCo $1.83M; real estate $12.24M to Lewiston SNF PropCo |
| 252210 | 2026-06-24 | Wilkinson RHCF, Amsterdam (335857), 160 beds | Amsterdam SNF LLC (David Lichtschein) | St. Mary's Healthcare | APA 2025-10-16 |
| 251186 | 2026-09-17 | Highland Park Rehab & Nursing, Wellsville (335210), 80 beds | Highland Operations LLC / HPRC OpCo (Rosenwasser, Geldzahler, Ausch, Siegfried) | HRNC LLC (Efraim Steif, Uri Koenig, David Camerota) | APA dates back to 2018 |
| 252238 | 2026-09-17 | The Pearl Nursing Center of Rochester → Astoria at Rochester (335439), 120 beds | TPRC OpCo LLC (Geldzahler, Ausch, Siegfried) | The Pearl Nursing Center of Rochester LLC (Shapiro, Abramczyk ×2, Scott Wheeler) | OTA 2025-08-07; same buyer group as Highland Park |

Sources: [2025-12-04 agenda](https://www.health.ny.gov/facilities/public_health_and_health_planning_council/meetings/2025-12-04/docs/agenda.pdf) · [2026-02-19 agenda](https://www.health.ny.gov/facilities/public_health_and_health_planning_council/meetings/2026-02-19/docs/full_council_agenda.pdf) · [2026-04-23 committee agenda (exhibits for the 5/7 items)](https://www.health.ny.gov/facilities/public_health_and_health_planning_council/meetings/2026-04-23/docs/Agenda.pdf) · [2026-06-24 agenda (includes the 5/7 minutes)](https://www.health.ny.gov/facilities/public_health_and_health_planning_council/meetings/2026-06-24/docs/council_agenda.pdf) · [2026-09-17 agenda](https://www.health.ny.gov/facilities/public_health_and_health_planning_council/meetings/2026-09-17/docs/full_council_agenda.pdf)

### Against the tracker (`deals` table, checked 2026-09-28)

I matched on facility names, old names, buyer and seller entities, and member names. I also checked `articles.raw_text`, `chow_seen_records`, and CMS CCNs.

| Facility | In tracker? | Source | First seen vs NY decision |
|---|---|---|---|
| The Pearl / Astoria at Rochester | ✅ Deal `030b7462` (NY) | Google Alerts → Skilled Nursing News dealbook, 2026-06-24 ("120-bed NY facility sold") | **85 days *before*** the 2026-09-17 decision |
| Sands Point | ❌ | — | — |
| Kirkhaven | ❌ | — | — |
| Maplewood | ❌ | — | — |
| Woodbury Heights | ❌ | — | — |
| Rockville | ❌ | — | — |
| Our Lady of Peace | ❌ | — | — |
| Wilkinson | ❌ | — | — |
| Highland Park | ❌ | — | — |
| *(Eger: not a sale)* | n/a | — | — |

**The tracker had 1 of 9 real ownership changes.** False positives I ruled out:
- **UCC-1 hits** (many rows for Rockville, Sands Point, Woodbury Heights, Pearl and Highland Park). These are old financing filings by the *existing* owners (Landa, Lichtschein, Ostreicher, Zitter, Steif, Koenig and others), matched because the UCC deal's `facility_names` holds that owner's whole CMS portfolio. None of them is this transaction.
- **"Wilkinson"** matched an unrelated NC CHOW (Wilkinson Blvd Operating Co., Gastonia).
- **CMS CHOW:** no record for any of the 10 CCNs. Our CHOW data runs through effective date 2026-02-01, and NY operator changes only close *after* PHHPC approval. So CHOW will show these late or not yet. That's the gap this source would fill.

Earliness: in the one case we caught, news beat the NY decision by 85 days. NY's earliest public artifact is the committee agenda, 2–4 weeks before the vote. **NY's value is coverage (8 of 9 were invisible to us) and detail (members, prices), not speed.**

**Tracker bug found on the way:** the extractor copied "The Pearl Nursing Center of Rochester" into `facility_names` for **all 6 deals** from that dealbook article. Those include the AZ, NV and CA refinancings, the Selectis/Black Pearl merger (AR, OK) and the MN sale (deal IDs `8c418654`, `9563f012`, `a4ac6b1a`, `1ff441ec`, `cb03404f`). Worth fixing in multi-deal article extraction.

---

## Per-state detail

### New York 🟢 (high confidence)
1. **Trigger:** PHL §2801-a. Any new operator of a residential health care facility (RHCF) must be "established" by PHHPC. Confirmed from the agenda exhibits ("requests approval to be established as the new operator…").
2. **Published:** free and public. Meeting pages on health.ny.gov (behind Cloudflare; needs browser TLS impersonation).
3. **Format / cadence:** Full Council and committee agenda PDFs, about 6 decision meetings a year. Agendas post before meetings; minutes post with the *next* meeting's packet.
4. **Fields:** application no., buyer entity + d/b/a, seller, members with ownership %, county, beds, address, APA/OTA dates, prices, recommendation, decision date (from minutes or transcript).
5. **Most recent 3:** [Highland Park #251186 and Astoria at Rochester #252238 (2026-09-17)](https://www.health.ny.gov/facilities/public_health_and_health_planning_council/meetings/2026-09-17/docs/full_council_agenda.pdf); [Wilkinson #252210 (2026-06-24)](https://www.health.ny.gov/facilities/public_health_and_health_planning_council/meetings/2026-06-24/docs/council_agenda.pdf).
6. **CCN join:** yes, 10/10 by name + city. One needed manual care: CMS already lists Woodbury Heights under its new name, so the old name (Cold Spring Hills) doesn't match.
7. **Rating:** Green / high. Main cost is PDF parsing and chasing minutes.

### Alabama 🟢 (high confidence)
1. **Trigger:** Ala. Admin. Code r. 410-1-7-.04 / Ala. Code §22-21-270: a Notice of Change of Ownership/Control, filed ≥20 days before the transaction. "Any transaction other than those above-described requires an application for a Certificate of Need" (quoted from the filing form). It's technically a notice, not a CON review.
2. **Published:** free and public. [SHPDA Notices of Change of Ownership](http://shpda.alabama.gov/Announcements/certificateofneed/chow/changeownershipnotice.aspx).
3. **Format / cadence:** HTML table (CO number, facility, date, PDF link) grouped by fiscal year; posted per filing (182 notices listed, 76 in FY2026).
4. **Fields (in the PDF):** proposed licensee, current licensee, parent entities, APA date, expected closing, SHPDA facility no., address, counsel, org charts.
5. **Most recent 3:** [CO2026-080 Specialty Care at Danberry at Inverness (2026-09-23)](http://shpda.alabama.gov/Announcements/certificateofneed/chow/FY2026/CO2026-080%20Complete%209.23.2026.pdf); [CO2026-076 Lafayette Nursing Home (2026-07-30)](http://shpda.alabama.gov/Announcements/certificateofneed/chow/FY2026/CO2026-076%20Lafayette%20Nursing%20Home%20017-N0003%20-%20CHOW%20App%207.23.2026.pdf); [CO2026-062…068, 7 Genesis → WSSH facilities incl. Magnolia Ridge (2026-07-01)](http://shpda.alabama.gov/Announcements/certificateofneed/chow/FY2026/CO2026-062%20Complete%206.29.2026.pdf).
6. **CCN join:** yes. Name + street address + state facility no.
7. **Rating:** Green / high. Tracker check: none of Danberry, Lafayette, Cloverdale, Magnolia Ridge, or Arabella Red Bay/Vernon is in `deals` (the tracker has *other* Arabella facilities via CMS CHOW).

### Oklahoma 🟢 (high confidence)
1. **Trigger:** 63 O.S. §1-852: "Every entity desiring to establish or to acquire an existing long-term care facility shall make application … for a certificate of need." There's an exemption path for changes of ownership by operation of law, plus "CN Exemption – Change of Ownership or Stock Transfer" filings.
2. **Published:** free and public. OSDH "The Notice", required monthly by 63 O.S. §1-857(B).
3. **Format / cadence:** 2-page monthly PDF. URLs aren't predictable, e.g. [`…/the-notice/2026/July Notice 2026.pdf`](https://aem-prod.oklahoma.gov/content/dam/ok/en/health/health2/aem-documents/protective-health/hrds/health-facility-systems/the-notice/2026/July%20Notice%202026.pdf). The August and September 2026 issues were **not posted** as of 2026-09-28, so the lag is ~1–2 months. ⚠️ Unverified: where the index page is. I found issues by search, not from a listing page.
4. **Fields:** CN #, facility name, application received date, type (CN Acquisition / Change of Ownership or Stock Transfer / Management Agreement / new construction), status or decision date. **No buyer or seller.**
5. **Most recent 3 (July 2026 issue):** 26-048…053, a 6-facility acquisition (McAlester, Marlow, Lindsay, Lakeview, Gracewood, Anadarko Nursing & Rehab; received 2026-07-09, with management-agreement exemptions issued 7/14–7/16); 26-038/039 Hensley and Hennessey Nursing & Rehab (2026-06-17); 26-032 Lexington Nursing Home (2026-05-20).
6. **CCN join:** name only (no city). OK facility names are mostly distinctive, but expect some ambiguity.
7. **Rating:** Green / high for signal, low detail. Tracker check: **none** of these 9 facilities are in `deals`.

### Maine 🟢 (high confidence, low volume)
1. **Trigger:** 22 M.R.S. §329(1): "Any transfer of ownership or acquisition under lease … or any acquisition of control of a health care facility" is subject to CON review (intra-family corporate exemption). Nursing facility rules: 10-149 CMR ch. 5 §71.
2. **Published:** free and public. [DHHS Current Healthcare Reviews](https://www.maine.gov/dhhs/dlc/healthcare-oversight/current_healthcare_reviews), with yearly archives such as [2025](https://www.maine.gov/dhhs/dlc/healthcare-oversight/older-reviews/2025-health-care-review).
3. **Format / cadence:** one HTML page, each case a list of dated PDFs (LOI → legal notice → preliminary analysis → briefing memo → decision letter). Updated per event.
4. **Fields:** LOIs and decisions name buyer, seller, facilities and addresses. ⚠️ Some PDFs are **scanned images with no text layer** (the Eagle Arc/Links LOI, the WSSH legal notice), so they'd need OCR.
5. **Most recent 3:** [Eagle Arc Acquisitions / Links Healthcare Group, change of ownership, LOI received 2026-08-12](https://www.maine.gov/dhhs/sites/maine.gov.dhhs/files/inline-files/LOI%20EagleLink.pdf); [WSSH / Genesis, 11 Maine nursing facilities, LOI 2026-03-20, application 2026-07-17](https://www.maine.gov/dhhs/sites/maine.gov.dhhs/files/inline-files/NewGen%20-%20Maine%20CON%20LOI%20%283.19.26%29.pdf); [Lakewood Continuing Care Community, decision 2025-09-26](https://www.maine.gov/dhhs/sites/maine.gov.dhhs/files/inline-files/Decision%20Letter%20%281%29.pdf).
6. **CCN join:** yes, via facility names and addresses in the LOI exhibits (medium effort: exhibit tables).
7. **Rating:** Green / high. **Lead-time example:** the Eagle Arc/Links LOI was received 2026-08-12, and the tracker first saw Links Healthcare ← First Atlantic via Google Alerts on 2026-09-16, **35 days later**. Genesis → WSSH/NewGen reached the tracker on 2026-06-27 (news); Maine's LOI was 99 days earlier, but the deal was already public through the bankruptcy docket.

### Michigan 🟡 (medium confidence)
1. **Trigger:** MCL 333.22209. Acquisition of an existing nursing home/HLTCU (purchase, lease, donation or comparable arrangement) needs CON. MDHHS advisory: "Any change in licensee does require Certificate of Need review/approval."
2. **Published:** free and public. [CON Activity Reports](https://www.michigan.gov/mdhhs/doing-business/providers/certificateofneed/reports/activity-reports). CON e-Serve (application detail) requires a MILogin account.
3. **Format / cadence:** monthly **XLSX** files for LOIs, applications/status and decisions (PDF until Feb 2026, XLSX from Mar 2026). Columns: date, CON ID, Facility ID, facility name, city, county, project description, cost, decision.
4. **Fields:** no buyer or seller. Descriptions are abbreviated ("NEW LEASE [35 YEARS]", "MEMBERSHIP INT TFER & BUILDING PURCHASE"). Nursing homes appear to use Facility IDs `NN-4xxx` (⚠️ inferred from the data, not documented).
5. **Most recent 3:** [The Oaks at Battle Creek, membership interest transfer + building purchase, LOI 2026-08-21, waived 2026-08-27](https://www.michigan.gov/mdhhs/-/media/Project/Websites/mdhhs/Doing-Business-with-MDHHS/Health-Care-Providers/Certificate-of-Need/CON-Eval/Activity-Reports/2026/Aug-2026/August-2026-LOIs.xlsx); [Woodward Hills, Evergreen and Shelby Health & Rehab, new 35-year leases ($104M / $117M / $158M), LOIs 2026-07-06](https://www.michigan.gov/mdhhs/-/media/Project/Websites/mdhhs/Doing-Business-with-MDHHS/Health-Care-Providers/Certificate-of-Need/CON-Eval/Activity-Reports/2026/July-2026/July-2026-LOIs.xlsx). ⚠️ Whether the three "new lease" items are an operator change or a propco refinance/re-lease isn't stated.
6. **CCN join:** name + city, probably reliable. The state Facility ID is not a CCN.
7. **Rating:** Yellow / medium. Trivial to ingest and a good early "something's happening" flag, but it needs another source to find out who the buyer is. Tracker check: none of these 4 facilities are in `deals`.

### Mississippi 🟡 (medium confidence)
1. **Trigger:** Miss. Code §41-7-191 and MSDH CHOW rules: a written Notice of Intent / CHOW application approved by MSDH. For nursing homes, Medicaid (DOM) must certify no cost increase.
2. **Published:** free and public. [CON Weekly Reports](https://msdh.ms.gov/page/30,0,84,863.html), section "Change of Ownership (CHOW) Applications".
3. **Format / cadence:** weekly PDF. **Items stay only 30 days after completion**, so a monthly scrape would miss some.
4. **Fields:** facility (sometimes current lessee), transaction type (purchase/lease/donation), city/county, received date, DOM letter dates, approved date. The buyer is usually **not** named.
5. **Most recent (only 2 nursing-home items in the 5 reports sampled):** [Academy Health Center (leased to Lamar Health and Rehabilitation Center), Madison, lease, received 2026-09-10](https://msdh.ms.gov/page/resources/21998.pdf); [Bolivar Medical Center Long Term Care, PHC-Cleveland LLC, purchase/lease, received 2026-04-28](https://msdh.ms.gov/page/resources/21878.pdf). ⚠️ Unverified: whether other nursing home CHOWs happened between the sampled weeks.
6. **CCN join:** name + city/county.
7. **Rating:** Yellow / medium. Low nursing home volume in the sample, thin fields, and it needs weekly polling.

### Kentucky (comparison) 🟡 expansion signal only (medium confidence)
1. **Trigger:** ownership change is **not** a CON trigger. KRS 216B.061 covers establishing a facility, bed-capacity changes, capital expenditure and relocation; acquiring a facility is notification only ([KRS 216B.065](https://apps.legislature.ky.gov/law/Statutes/statute.aspx?id=54959)). ⚠️ I read the 216B.061 list from a search summary and the 216B.065 title, not the full statute text.
2. **Published:** free and public. [CHFS CON page](https://www.chfs.ky.gov/agencies/os/oig/dcn/Pages/cn.aspx), monthly newsletter.
3. **Format / cadence:** monthly DOCX (Chart A formal review, Chart C actions/approvals).
4. **Fields:** applicant, city/county, description, cost, decision date.
5. **Recent nursing-related example:** [Sept 2026 newsletter](https://www.chfs.ky.gov/agencies/os/oig/dcn/Certificate%20of%20Need%20Newsletter/September2026.docx): Shelby Farms Senior Living gets a new 44-bed NF by relocating beds from The Springs at Oldham Reserve (approved 2026-08-19, $9.5M).
6. **CCN join:** yes.
7. **Rating:** useful for expansion and bed-relocation signals (and bed moves often come with an ownership reshuffle), not for ownership changes. It complements our KY UCC coverage.

### Missouri 🔴 (high confidence)
- [RSMo 197.315.14](https://revisor.mo.gov/main/OneSection.aspx?section=197.315&bid=10387): "A certificate of need shall not be required for the transfer of ownership of an existing and operational health facility in its entirety." The revisor still shows the 2014 version.
- 2025's SB733 would have required CON for LTC transfers. ⚠️ Its final status is unverified (the bill page 404'd), but the revisor text hasn't changed, which suggests it didn't pass.

### Arkansas 🔴 (high confidence)
- HSPA Permit of Approval rule §IV.C.4 ([LII](https://www.law.cornell.edu/regulations/arkansas/049-00-05-Ark-Code-R-003)): "The obligation of a capital expenditure to acquire an existing health care facility shall not require a Permit of Approval."
- Ownership changes go through a 30-day DHS Office of Long Term Care licensure notice, which isn't published. [HSPA](https://healthy.arkansas.gov/boards-commissions/commissions/arkansas-health-services-permit-agency/) agendas and decisions cover bed additions and new facilities only (checked the Sept 2026 applications-under-review list and the June 2026 decisions).

### Massachusetts 🔴 (medium confidence)
- DoN transfer of ownership ([105 CMR 100.735](https://www.mass.gov/doc/105-cmr-100-determination-of-need/download)) is defined for a **Hospital or Clinic**. The [DoN pending list](https://www.mass.gov/info-details/don-pending-projects-and-applications) (as of 2026-09-17) has no nursing home transfers.
- Nursing home transfers need a Notice of Intent to Acquire ≥90 days before transfer plus DPH suitability review ([105 CMR 153.022](https://www.mass.gov/doc/105-cmr-153-licensure-procedure-and-suitability-requirements-for-long-term-care-facilities/download); MGL c.111 §71). I found no public posting of those notices. The Jan 2026 proposed amendments add notice to staff and unions, not the public (per [Nixon Peabody](https://www.nixonpeabody.com/insights/alerts/2026/02/11/massachusetts-proposes-significant-revisions-to-long-term-care-facility-regulations)). ⚠️ Unverified: whether those amendments were adopted, and whether a public-records request could get NOIAs.

### Connecticut 🔴 (medium confidence)
- [DSS CON](https://portal.ct.gov/DSS/Health-And-Home-Care/Reimbursement-and-Certificate-of-Need/Certificate-of-Need) (CGS §17b-352) covers transfers of ownership or control **before initial licensure**, closures, and bed changes. Its [public hearing page](https://portal.ct.gov/dss/health-and-home-care/reimbursement-and-certificate-of-need/certificate-of-need/con-public-hearing) lists closures only.
- Existing homes need DPH change-of-ownership approval ≥120 days ahead ([PA 23-122](https://hallrender.com/2023/10/20/connecticut-long-term-care-law-update-changes-to-nursing-home-change-of-ownership-process/), superseding CGS §19a-493). ⚠️ Unverified: whether DPH publishes these applications anywhere. I didn't find a public list.

---

## Unverified / open items

- **OK:** location of "The Notice" index page; how long the Aug/Sep 2026 issues take to post.
- **MI:** meaning of "NEW LEASE" rows (operator change vs. real-estate re-lease); the Facility ID `NN-4xxx` = nursing home convention.
- **MS:** nursing home CHOW volume outside the 5 sampled weekly reports.
- **MO:** SB733 (2025) final status.
- **MA:** adoption status of the Jan 2026 105 CMR 153 amendments; whether NOIAs can be obtained by records request.
- **CT:** whether DPH publishes CHOW applications.
- **KY:** full KRS 216B.061 text (read via search summary).
- **ME:** OCR needed for scanned LOIs and notices.

## Technical notes for building scrapers

- health.ny.gov, maine.gov, michigan.gov, msdh.ms.gov and oklahoma.gov all returned 200 to `curl_cffi` with `impersonate="chrome"` (the same fallback now in `scraper/rss.py`). health.ny.gov blocks plain clients.
- The NY agenda PDFs are huge (up to 25 MB, 700 pages). pypdf text extraction works; parse the "Residential Healthcare Facilit…" section headings, then each project's "Project # NNNNNN-E Exhibit Page 1" block.
- The research files (downloaded PDFs, extracted text) are in the session scratchpad and weren't committed.
