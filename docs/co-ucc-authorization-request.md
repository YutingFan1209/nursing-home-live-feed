# Colorado UCC: Authorization Request for Automated Data Collection (draft)

The Colorado Secretary of State's terms of use prohibit "computerized robots
or data mining" on its website without permission, but accept this request
form: https://www.coloradosos.gov/pubs/info_center/files/dataAuthorization.pdf
(rev. 05/02/2024). Fill the PDF with the answers below, save it, and email
it to business@coloradosos.gov. Fields in [brackets] need the requester's
own details.

Two conditions on the form itself:
- Access only outside 8:00 AM – 5:00 PM Monday–Friday.
- Up to 5 **static IP addresses** to allow. A home connection usually has a
  changing IP; ask the provider for a static one, or run the search from a
  small cloud server with a fixed address.

---

**Certification checkbox:** tick it. We read the terms, and the free public
search does not meet the need. It is meant for a person looking up one name
at a time, and the business need below is a recurring check of a fixed
list of lenders. The paid bulk data covers far more than the need.

**Organization:** [organization name]

**Your name:** [name]

**Phone:** [phone]

**Email (affiliated with the organization):** [email]

**Business need for this request:**

We run a free, public tracker of nursing home ownership changes
(https://yutingfan1209.github.io/nursing-home-live-feed/). It is built from
public records: CMS change-of-ownership data, state certificate-of-need
notices and state UCC-1 filings. A UCC-1 filed by a nursing home lender is
often the first public sign that a facility is being sold or refinanced,
weeks before federal records show the change. We would like to check
Colorado's UCC index for new filings by about 27 lenders that specialize in
nursing home financing (for example the U.S. Department of Housing and Urban
Development, Capital Funding, Dwight Capital, Greystone, CIBC, Omega
Healthcare). Each check is one secured-party name search; we keep only
filings from the last two years whose debtor is a nursing home entity. We
don't download images or documents, resell data, or collect personal
information about individuals.

**Areas of data needed (specific URLs):**

- https://www.coloradosos.gov/ucc/pages/home.xhtml → Advanced search,
  secured party name (organization), results list only
- Filing detail pages only for filings whose debtor is a nursing home
  entity (a handful per week)

**Manually or automated scripts?** Automated scripts

**Scheduling of access:**

Once a week, Saturday between 1:00 AM and 3:00 AM Mountain Time. About 27
searches per run, one at a time, at least 10 seconds apart (under 10
minutes in total), plus up to about 20 filing detail lookups. We can adjust
the day, time or pace to whatever the office prefers.

**Static IP address(es):** [static IP, see note above]
