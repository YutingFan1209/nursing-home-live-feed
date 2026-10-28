"""
scripts/export_deals.py

Exports deals.json for the gh-pages static frontend. Run from repo root
on `main`, then copy the output to deals.json on `gh-pages` and push --
see README's Deployment section for the full step-by-step.

Excludes dismissed-stage deals.

UCC-1 sourced deals don't have a real per-filing URL to link to -- their
articles.url is `ucc://{state}/{filing_number}`, an internal dedup key
only, not a navigable page (confirmed 2026-09-15: the frontend was
silently hiding the whole Source section for these rather than render a
broken ucc:// link). This rewrites source_url/source_title for UCC deals
to point at ucc_filings.detail_url when one exists (a real, one-click
per-filing deep link -- NY via lienId, KY via filing param, OH via
entityId as of 2026-09-22, see ucc/audit_log.py:_detail_url) or else the state's
search portal homepage, with the filing number in the title so a viewer
can search for it themselves.

NJ is the exception to "portal homepage" (added 2026-09-23): its search
wizard's ASP.NET ViewState/EventValidation turned out not to be bound to a
session or cookie, so a replayed POST of the wizard's step-2 hidden fields
plus a filing number lands directly on that filing's result row. This
fetches fresh step-2 tokens once per export (_fetch_nj_search_form) and
ships them in deals.json as `ucc_search_forms.NJ`. The frontend submits them
as a cross-site form POST in a new tab. Tokens are re-fetched every export
in case the portal's machine key rotates, and if fetching fails the key is
just omitted and the frontend falls back to the plain portal link.
"""
import os
import re
import sys
import json
from urllib.parse import quote
from xml.sax.saxutils import escape
from datetime import datetime, timezone

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2
import requests
from config import get_config
from ucc.lender_classifier import LenderCategory, classify_secured_party
from pipeline.ucc_category import categorize, collapse_repeats, complete_through, owner_start_dates
from pipeline.deal_labels import label, is_individual_filing
from pipeline import individual_owners
from pipeline.cms_confirm import CmsIndex, facility_names_agree, same_facility_name
from pipeline.deal_groups import group

# NY UCC filings against a person name a private individual. Tyler approved
# showing the names (2026-10-12). False drops them from deals.json entirely
# -- hiding them only in the frontend would still publish them in the JSON.
SHOW_INDIVIDUAL_NAMES = True

UCC_PORTAL_URLS = {
    "NY": "https://ucc-efiling.dos.ny.gov/OnlineUCCSearch/OnlineUCCSearch",
    "KY": "https://web.sos.ky.gov/ftucc/search.aspx",
    "OH": "https://ucc.ohiosos.gov/search",
    "PA": "https://file.dos.pa.gov/search/ucc",
    "NJ": "https://www.njportal.com/ucc/search/noncertifiedsearch.aspx",  # was missing entirely -- confirmed 2026-09-22 that every NJ deal (337) was falling through to the raw ucc://NJ/{filing_number} scheme URL untouched, which isn't navigable at all (no browser handles the ucc:// protocol), worse than every other state's fallback
    "CA": "https://bizfileonline.sos.ca.gov/search/ucc",
    "FL": "https://floridaucc.com/search",
    "OR": "https://secure.sos.state.or.us/ucc/searchHome.action",  # was missing -- OR's 9 deals shipped the raw ucc://OR/... key (2026-10-15)
    # AR/NM shipped ucc://AR/... too until 2026-10-21. RI's filing history
    # page only opens inside a search session, so this is the search page
    "AR": "https://bcs.sos.arkansas.gov/search/ucc",
    "NM": "https://enterprise.sos.nm.gov/search/ucc",
    "RI": "https://business.sos.ri.gov/CorpWeb/UCCSearch/UCCSearch.aspx",
    "ID": "https://sosbiz.idaho.gov/search/ucc",
    # Searched by hand (robots.txt, terms or a captcha keep automated tools
    # off; a person opening the search page is fine). Shipped ucc://AZ/...
    # and ucc://CO/... until 2026-10-21
    "AZ": "https://apps.azsos.gov/apps/ucc/search/",
    "CO": "https://www.coloradosos.gov/ucc/pages/home.xhtml",
    "MD": "https://ucc.egov.maryland.gov/UCCFiling/UCCMainPage.aspx",
}

# Per-filing pages that can be built from the filing number alone, so no
# ucc_filings.detail_url is needed. FL's detail page loads straight from its
# URL (verified 2026-10-15; a first-time visitor clicks through the Terms of
# Use box first). CA, PA and OR have none: CA's filing panel opens without
# changing the URL, so those link to the search page and the frontend copies
# the filing number on click.
UCC_FILING_URLS = {
    "FL": "https://floridaucc.com/search/details?text={n}&searchOptionType=DocumentNumber&searchCategory=Standard&uccNumber={n}",
}

NJ_WIZARD = "ctl00$mainContent$DebtorSearch1$Wizard1$"
NJ_TOKEN_FIELDS = ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")


def _nj_hidden_fields(html: str) -> dict:
    fields = {}
    for name in NJ_TOKEN_FIELDS:
        m = re.search(rf'id="{name}" value="([^"]*)"', html)
        if not m:
            raise ValueError(f"NJ portal page missing {name}")
        fields[name] = m.group(1)
    return fields


def _fetch_nj_search_form() -> dict | None:
    """Walk the NJ wizard's step 1 (search type = Filing Number, which the
    portal only allows with output = Copies Only) and return step 2's hidden
    fields, ready for the frontend to POST with a filing number filled in.
    None on any failure, and the frontend falls back to the plain portal link."""
    url = UCC_PORTAL_URLS["NJ"]
    try:
        s = requests.Session()
        s.headers["User-Agent"] = "Mozilla/5.0"
        step1 = s.get(url, timeout=30)
        step1.raise_for_status()
        step2 = s.post(url, timeout=30, data={
            "__EVENTTARGET": "", "__EVENTARGUMENT": "", "__LASTFOCUS": "",
            "__VIEWSTATEENCRYPTED": "",
            **_nj_hidden_fields(step1.text),
            NJ_WIZARD + "radioSwitchOrgPerson": "FilingNumber",
            NJ_WIZARD + "radioOutputList": "PhotoCopies",
            NJ_WIZARD + "StartNavigationTemplateContainerID$btnContinue": "Continue",
        })
        step2.raise_for_status()
        if NJ_WIZARD + "txtFilingNumber1" not in step2.text:
            raise ValueError("NJ wizard didn't advance to the filing-number step")
        return {
            "action": url,
            "filing_number_field": NJ_WIZARD + "txtFilingNumber1",
            "fields": {
                "__EVENTTARGET": "", "__EVENTARGUMENT": "", "__LASTFOCUS": "",
                "__VIEWSTATEENCRYPTED": "",
                **_nj_hidden_fields(step2.text),
                # without this a lapsed filing comes back "no results"
                NJ_WIZARD + "cbIncludeLapsedFiling": "on",
                NJ_WIZARD + "StepNavigationTemplateContainerID$btnContinue": "Search",
            },
        }
    except Exception as e:
        print(f"WARNING: couldn't fetch NJ UCC search form tokens ({e}) -- NJ links fall back to the portal homepage")
        return None


def _chow_file_date() -> str | None:
    """Date of the CMS CHOW file the pipeline is currently reading, from its
    discovered filename (e.g. SNF_CHOW_2026.07.17.csv -> 2026-07-17). The
    frontend's freshness card used to hardcode this and went stale."""
    try:
        from scraper.chow import _discover_chow_csv_url
        m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})\.csv", _discover_chow_csv_url() or "")
        return "-".join(m.groups()) if m else None
    except Exception as e:
        print(f"WARNING: couldn't determine CHOW file date ({e})")
        return None


# How often each source publishes, and how long a check stays current before
# the next one is due. Runs are manual (no cron), so the site shows when each
# check is due rather than a scheduled run. Intervals follow each source's
# cadence: PA's licence notices are a rolling window and MS's weekly tables
# drop items 30 days after completion, so those two are weekly.
SOURCE_SCHEDULE = {
    "chow":  ("Quarterly file", None),
    "cms_ownership": ("Monthly file", None),
    "ucc":   ("Filed daily", 7),
    "edgar": ("Filed as events happen", 7),
    "rss":   ("Published daily", 1),
    "con":   ("Posted as filed", 14),
}
CON_SCHEDULE = {
    "Mississippi": ("Weekly reports", 7),
    "Pennsylvania": ("Rolling notice window", 7),
    "Michigan": ("Monthly reports", 30),
    "Oklahoma": ("Monthly notice", 30),
    "New York": ("Committee agendas, about monthly", 14),
    "Hawaii": ("Posted as filed", 30),
}


def _source_freshness(cur, chow_file_date: str | None) -> dict:
    """Last check of every active source, when the next is due, and for
    UCC the last new filing found in each state (all states share one
    source row, and run separately)."""
    from datetime import date, timedelta
    cur.execute("SELECT name, source_type, last_fetched_at FROM sources WHERE active ORDER BY source_type, name")
    rows = []
    for name, stype, last in cur.fetchall():
        cadence, every = SOURCE_SCHEDULE.get(stype, ("", None))
        if stype == "con":
            cadence, every = next((v for k, v in CON_SCHEDULE.items() if name.startswith(k)), (cadence, every))
        due = (last + timedelta(days=every)).isoformat() if last and every else None
        if name == "CMS SNF Change of Ownership" and chow_file_date:
            # CMS posts the CHOW file quarterly; the next is due ~3 months on
            due = (date.fromisoformat(chow_file_date) + timedelta(days=91)).isoformat()
        rows.append({"name": name, "type": stype, "cadence": cadence,
                     "last_checked": last.isoformat() if last else None, "next_due": due})
    # The CMS Ownership file reloads when the stored copy is 25+ days old
    cur.execute("SELECT MAX(cms_refreshed_at) FROM cms_ownership_records")
    loaded = cur.fetchone()[0]
    for r in rows:
        if r["name"] == "CMS Nursing Home Ownership Changes":
            r["cadence"] = "Monthly file"
            r["file_loaded"] = loaded.isoformat() if loaded else None
            r["next_due"] = (loaded + timedelta(days=25)).isoformat() if loaded else None
    cur.execute("""
        SELECT d.states[1], MAX(d.created_at)
        FROM deals d JOIN articles a ON a.id = d.article_id JOIN sources s ON s.id = a.source_id
        WHERE s.source_type = 'ucc' AND d.states[1] IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC""")
    ucc = {st: ts.isoformat() for st, ts in cur.fetchall()}
    return {"sources": rows, "ucc_last_new_filing": ucc}


def _ucc_display_fields(source_url: str, source_title: str, detail_url: str | None):
    """source_url for a UCC deal is 'ucc://STATE/FILING_NUMBER' -- parse
    it and swap in a real, clickable URL: detail_url (a genuine per-filing
    deep link, from ucc_filings.detail_url) when one exists, else the
    portal's search page with the filing number in the title so a viewer
    can search for it themselves."""
    state, _, filing_number = source_url.removeprefix("ucc://").partition("/")
    if filing_number.startswith("NYX-") and not detail_url:
        return UCC_PORTAL_URLS["NY"], f"NY UCC-1 filing - search the debtor's name on the state portal"
    if not detail_url and state.upper() in UCC_FILING_URLS:
        detail_url = UCC_FILING_URLS[state.upper()].format(n=quote(filing_number))
    if detail_url:
        return detail_url, f"{state} UCC-1 filing #{filing_number}"
    portal = UCC_PORTAL_URLS.get(state.upper())
    if not portal:
        return source_url, source_title
    title = f"{state} UCC-1 filing #{filing_number} - search this number on the state portal"
    return portal, title


def _ny_spreadsheet_links(cur) -> dict:
    """(debtor, filing date) -> [(secured party, filing number, detail_url)]
    for NY filings with a real per-filing link. The June 2026 NY spreadsheet
    backfill (ucc/ny.py) had no filing numbers, so its 826 deals carry a
    made-up 'NYX-<hash>' key that the portal can't find; many of the same
    filings were scraped later with real numbers (2026-10-15)."""
    cur.execute("""
        SELECT UPPER(debtor_name), LEFT(filing_date, 10), UPPER(COALESCE(secured_party, '')),
               filing_number, detail_url
        FROM ucc_filings
        WHERE state = 'NY' AND detail_url IS NOT NULL AND filing_date ~ '^\\d{4}-'""")
    index = {}
    for debtor, filed, sp, number, url in cur.fetchall():
        row = (sp.strip(), number, url)
        index.setdefault((debtor.strip(), filed), []).append(row)
        index.setdefault((_first_last(debtor), filed), []).append(row)
    return index


def _first_last(name: str) -> tuple:
    """'RONALD A. STERN' -> ('RONALD', 'STERN'): the portal lists one person
    under middle-initial variants the spreadsheet left out (2026-10-15)."""
    parts = name.upper().replace(",", "").replace(".", "").split()
    return (parts[0], parts[-1]) if len(parts) > 1 else tuple(parts)


# OH lists every secured party on a filing, one per paragraph, and some
# are the homes' own opcos and property companies (a tenant's or landlord's
# interest), not lenders: "OTTAWA SNF OPCO LLC" on Autumn Court's loan from
# Metropolitan Commercial Bank (fresh sample 2026-10-21)
_HOME_ENTITY_RE = re.compile(r"\b(OPCO|PROPCO|PROPERTY HOLDINGS|REHAB\w*|HEALTH ?CARE,? (LLC|INC)|NURSING|SNF)\b", re.I)
_LENDER_WORD_RE = re.compile(r"BANK|TRUST|CAPITAL|FUND|FINANC|MORTGAGE|LENDING|CREDIT|HOUSING|HUD\b|AGENT|SPE\b", re.I)


def clean_lender(lender: str | None) -> str | None:
    if not lender or "\n" not in lender:
        return lender
    parts = list(dict.fromkeys(p.strip() for p in lender.split("\n") if p.strip()))
    lenders = [p for p in parts if _LENDER_WORD_RE.search(p) or not _HOME_ENTITY_RE.search(p)]
    return "; ".join(lenders or parts)


def _match_ny_spreadsheet(index: dict, debtor: str | None, filed: str | None, lender: str | None):
    """The real filing behind an NYX deal: the person's only NY filing that
    day, or else the one whose secured party is the deal's lender -- by the
    exact name first, then by first and last name. None when it's still
    ambiguous."""
    for key in ((debtor or "").upper().strip(), _first_last(debtor or "")):
        candidates = index.get((key, filed or ""), [])
        if len(candidates) > 1:
            candidates = [c for c in candidates if c[0] == (lender or "").upper().strip()]
        if len(candidates) == 1:
            return candidates[0][1:]
    return None


LENDER_CATEGORY_LABELS = {
    LenderCategory.REAL_ESTATE: "Healthcare RE / HUD lender",
    LenderCategory.PRIVATE_EQUITY: "Private credit fund",
    LenderCategory.BANK_GENERAL: "Bank",
    LenderCategory.EQUIPMENT_VENDOR: "Equipment / non-deal lien",
}

UCC_TITLE_RE = re.compile(r"^UCC-1 filing: (.+?) / .* \([A-Z]{2}\)$", re.S)


def _ucc_fields(deal: dict, debtor: str | None, filing_type: str | None) -> dict:
    """What a UCC filing says in plain terms: who borrowed, what kind of
    lender, and whether it's an original financing statement or a UCC-3
    amendment. About half of NY's deals (the individual-owner track) never
    got a ucc_filings row, so the debtor falls back to the article title
    main.py wrote ("UCC-1 filing: DEBTOR / LENDER (ST)")."""
    if not debtor:
        m = UCC_TITLE_RE.match(deal.get("source_title") or "")
        debtor = m.group(1) if m else None
    lender = deal.get("lender") or ""
    category = (None if lender.startswith("Not available")
                else classify_secured_party(lender, deal.get("ucc_state")).category)
    is_amendment = (filing_type or "").upper().replace("-", "") == "UCC3"
    if is_amendment:
        deal_type = "amendment"
    elif category == LenderCategory.EQUIPMENT_VENDOR:
        deal_type = "non_deal_lien"
    else:
        deal_type = "financing"
    return {
        "ucc_debtor": debtor,
        "ucc_filing_type": "UCC-3 amendment" if is_amendment else "UCC-1",
        "lender_category": LENDER_CATEGORY_LABELS.get(category),
        "deal_type": deal_type,
    }


REFINANCING_RE = re.compile(r"\brefinanc", re.I)


def _news_deal_type(deal: dict) -> str:
    """News extraction pulls parties and amounts but no deal type, and
    stored article titles are often a snippet rather than the headline, so
    keyword-matching them misfires. Only call it financing when the
    extracted fields say so: a loan amount with no buyer and no price.
    Claude's summary (not the title) is reliable enough to split off
    refinancings ("closed a $53M cash-out refinancing", "to refinance
    existing 2019 bonds"), which the site hides by default like UCC ones."""
    if deal.get("financing_amount_m") and not deal.get("deal_value_m") and not deal.get("acquiring_entity"):
        return "refinancing" if REFINANCING_RE.search(deal.get("summary") or "") else "financing"
    # A lender with no amount, buyer or price: a portfolio loan whose total
    # wasn't split per deal (MonticelloAM's three Virginia SNFs, 2026-10-21).
    # First sentence only: the Indiana half of that loan mentions "a Virginia
    # refinancing" later on
    first = re.split(r"(?<=\.)\s", deal.get("summary") or "", maxsplit=1)[0]
    if (deal.get("lender") and not deal.get("deal_value_m") and not deal.get("acquiring_entity")
            and REFINANCING_RE.search(first)):
        return "refinancing"
    return "ownership_change"


def _facility_pages(cur, deals: list[dict]) -> dict:
    """CMS details for every facility a record points to, for the site's
    facility pages (?ccn=): name, city, state, certified beds, and its
    direct owners in CMS ownership data with start dates."""
    ccns = set()
    for d in deals:
        ccns |= set(d.get("ccns") or []) | {h["ccn"] for h in d.get("possible_homes") or []}
    ccns = sorted(c for c in ccns if c)
    out = {}
    cur.execute("SELECT ccn, provider_name, provider_city, provider_state, bed_count FROM cms_facilities WHERE ccn = ANY(%s)",
                (ccns,))
    for ccn, name, city, state, beds in cur.fetchall():
        out[ccn] = {"name": name, "city": city, "state": state, "beds": beds, "owners": []}
    cur.execute("""
        SELECT DISTINCT ccn, owner_name, ownership_start_date FROM cms_ownership_records
        WHERE ccn = ANY(%s) AND owner_role ILIKE '%%DIRECT OWNERSHIP%%' AND owner_role NOT ILIKE '%%INDIRECT%%'
        ORDER BY ccn, ownership_start_date DESC NULLS LAST
    """, (list(out),))
    for ccn, owner, start in cur.fetchall():
        if len(out[ccn]["owners"]) < 12:
            out[ccn]["owners"].append([owner, start.isoformat() if start else None])
    return out


def _resolved_facilities(deal: dict) -> list[str]:
    """UCC deals seeded from a person's name get facility_names equal to
    operator_names (main.py's NEW_SIGNAL builder) -- that's the person, not
    a facility."""
    names, ops = deal.get("facility_names") or [], deal.get("operator_names") or []
    return [] if names and names == ops else names


def _add_labels(deal: dict, cms, homes: dict) -> None:
    """Labels from pipeline/deal_labels.py, plus where the record matched:
    match_type is single_home / multi_home / unmatched for NY filings
    against a person (ucc_individual_matches), else ccn_matched / no_ccn."""
    individual = is_individual_filing(deal)
    deal["individual_filing"] = individual
    if individual:
        matched = homes.get(deal["id"], [])
        deal["possible_homes"] = [{"ccn": c, "name": n} for c, n in matched]
        # The CMS matcher's fuzzy facility matches for these are noise; only
        # the owner matches count
        deal["ccns"] = [c for c, _ in matched] or None
        deal["match_type"] = individual_owners.match_type(len(matched))
        facilities = [n for _, n in matched]
        deal["owner_line"] = individual_owners.owner_line(facilities)
        deal["facility_names"] = facilities or None
    else:
        facilities = _resolved_facilities(deal)
        if cms and deal["source_type"] not in ("chow", "ucc") and deal.get("ccns"):
            # The matcher's fuzzy name matches can land in other states (four UK
            # homes matched US facilities in six states): keep only facilities
            # in one of the record's states
            deal["ccns"] = [c for c in deal["ccns"] if cms.facility.get(c, (None, None))[1] in (deal.get("states") or [])] or None
        deal["ccn_match"] = "cms_matcher" if deal.get("ccns") else None
        if not deal.get("ccns") and cms and deal["source_type"] != "ucc":
            # A facility name equal to exactly one CMS facility in the state
            deal["ccns"] = cms.ccns_by_name(facilities, deal.get("states")) or None
            deal["ccn_match"] = "exact_facility_name" if deal["ccns"] else None
        if not deal.get("ccns") and cms and deal["source_type"] == "con":
            # A renamed home, or a notice that gives only the address
            title = deal.get("source_title") or ""
            names = list(facilities or []) + ([title.split(":", 1)[1].strip()] if ":" in title else [])
            ccns, basis = cms.ccns_for_notice(names, deal.get("states"), f"{title} {deal.get('summary') or ''}")
            if ccns:
                deal["ccns"], deal["ccn_match"] = ccns, f"notice_{basis}"
        deal["match_type"] = "ccn_matched" if deal.get("ccns") else "no_ccn"
        if cms and deal["source_type"] != "ucc":
            deal["beds"] = cms.beds(deal.get("ccns"), deal.get("facility_count"))
    deal.update(label(deal, cms, facilities, show_individual_names=SHOW_INDIVIDUAL_NAMES))
    deal["found_date"] = (deal.get("created_at") or "")[:10] or None
    if individual and not SHOW_INDIVIDUAL_NAMES:
        deal["ucc_debtor"] = None
        deal["operator_names"] = None
        deal["source_title"] = f"{deal.get('ucc_state')} UCC-1 filing #{deal.get('ucc_filing_number')}"
        deal.pop("ucc_search_text", None)


SITE_URL = "https://yutingfan1209.github.io/nursing-home-live-feed/"
FEED_SIZE = 50


def _feed_title(d: dict) -> str:
    states = f" ({', '.join(d['states'])})" if d.get("states") else ""
    return "".join(text for text, _ in d["headline"]) + states


def write_atom_feed(deals: list[dict], path: str, updated: str) -> None:
    """Newest-first Atom feed of what the pipeline found, so people can
    follow the site in a feed reader. Static hosting can't send per-view
    email alerts; this is the closest thing. Ordered by when a deal was
    found (created_at), not its effective date, and skips non-deal liens and
    refinancings and anything that isn't skilled-nursing related, like the
    site's default view."""
    recent = sorted((d for d in deals if d.get("deal_type") not in ("non_deal_lien", "refinancing")
                     and d.get("facility_type") == "skilled_nursing"),
                    key=lambda d: d.get("created_at") or "", reverse=True)[:FEED_SIZE]
    entries = []
    for d in recent:
        link = d["source_url"] if (d.get("source_url") or "").startswith("http") else SITE_URL
        summary = "; ".join(filter(None, [
            d.get("acquisition_date") and f"Effective {d['acquisition_date']}",
            d.get("facility_names") and f"Facilities: {', '.join(d['facility_names'][:5])}",
            d.get("deal_value_m") and f"${d['deal_value_m']}M",
            d.get("financing_amount_m") and f"${d['financing_amount_m']}M financing",
            d.get("source_name") and f"Source: {d['source_name']}",
        ]))
        entries.append(f"""  <entry>
    <id>urn:uuid:{d['id']}</id>
    <title>{escape(_feed_title(d))}</title>
    <link href="{escape(link, {'"': '&quot;'})}"/>
    <updated>{d.get('created_at') or updated}</updated>
    <summary>{escape(summary)}</summary>
  </entry>""")
    with open(path, "w") as f:
        f.write(f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Nursing Home Ownership Feed</title>
  <link href="{SITE_URL}"/>
  <link rel="self" href="{SITE_URL}feed.xml"/>
  <id>{SITE_URL}</id>
  <updated>{updated}</updated>
{chr(10).join(entries)}
</feed>
""")


def main():
    config = get_config()
    conn = psycopg2.connect(config.database_url)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                d.id, d.acquiring_entity, d.seller_entity, d.states,
                d.facility_count, d.deal_value_m, d.financing_amount_m, d.acquisition_date,
                d.operator_names, d.facility_names, d.created_at,
                d.lender, d.ucc_confirmed, d.stage, d.summary,
                s.source_type, s.name AS source_name,
                a.url AS source_url, a.title AS source_title, a.published_at::date::text AS published_at,
                uf.detail_url AS ucc_detail_url,
                uf.debtor_name AS ucc_debtor, uf.filing_type AS ucc_filing_type,
                uf.collateral_text AS ucc_collateral, uf.collateral_source AS ucc_collateral_source,
                -- Weak fuzzy facility-name matches on news and state filings are
                -- mostly wrong homes (Golden Madera -> Bay Marina Post Acute,
                -- Stephenson County -> Serenity Estates of Lena; 2026-10-12);
                -- those records fall back to exact-name matching below
                ARRAY_AGG(DISTINCT m.ccn) FILTER (WHERE m.ccn IS NOT NULL AND NOT (
                    s.source_type IN ('con', 'rss', 'edgar') AND m.match_method = 'facility_name_fuzzy'
                    AND m.match_score < 80)) AS ccns
            FROM deals d
            JOIN articles a ON a.id = d.article_id
            LEFT JOIN sources s ON s.id = a.source_id
            LEFT JOIN cms_matches m ON m.deal_id = d.id
            LEFT JOIN ucc_filings uf
                ON uf.state = split_part(a.url, '/', 3)
                AND uf.filing_number = split_part(a.url, '/', 4)
                AND s.source_type = 'ucc'
            WHERE d.stage NOT IN ('dismissed')
            GROUP BY d.id, a.url, a.title, a.published_at, s.source_type, s.name, uf.detail_url, uf.debtor_name, uf.filing_type,
                     uf.collateral_text, uf.collateral_source
            ORDER BY COALESCE(d.acquisition_date, d.created_at::date) DESC, d.created_at DESC
        """)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
        ny_links = _ny_spreadsheet_links(cur)
        deals = []
        for row in rows:
            deal = dict(zip(cols, row))
            deal["id"] = str(deal["id"])
            deal["lender"] = clean_lender(deal["lender"])
            deal["acquisition_date"] = str(deal["acquisition_date"]) if deal["acquisition_date"] else None
            deal["created_at"] = deal["created_at"].isoformat() if deal["created_at"] else None
            if deal["source_type"] == "ucc" and deal["source_url"]:
                ucc_state, _, deal["ucc_filing_number"] = deal["source_url"].removeprefix("ucc://").partition("/")
                deal["ucc_state"] = ucc_state.upper()
                deal.update(_ucc_fields(deal, deal.pop("ucc_debtor"), deal.pop("ucc_filing_type")))
                if deal["ucc_filing_number"].startswith("NYX-") and not deal["ucc_detail_url"]:
                    real = _match_ny_spreadsheet(ny_links, deal["ucc_debtor"], deal["published_at"], deal.get("lender"))
                    if real:
                        deal["ucc_filing_number"], deal["ucc_detail_url"] = real
                        deal["source_url"] = f"ucc://NY/{real[0]}"
                    else:
                        # No real number to search for: copy the debtor's name
                        deal["ucc_search_text"] = deal["ucc_debtor"]
                deal["source_url"], deal["source_title"] = _ucc_display_fields(
                    deal["source_url"], deal["source_title"], deal.pop("ucc_detail_url")
                )
                # The link only reaches a search page: the frontend copies
                # the filing number on click so the viewer can paste it
                deal["ucc_search_page"] = deal["source_url"] == UCC_PORTAL_URLS.get(ucc_state.upper())
            else:
                for k in ("ucc_detail_url", "ucc_debtor", "ucc_filing_type", "ucc_collateral", "ucc_collateral_source"):
                    deal.pop(k, None)
                deal["deal_type"] = "ownership_change" if deal["source_type"] in ("chow", "con") else _news_deal_type(deal)
            deals.append(deal)

        cur.execute("SELECT MAX(last_fetched_at) FROM sources")
        pipeline_ran_at = cur.fetchone()[0]
        chow_file_date = _chow_file_date()
        source_freshness = _source_freshness(cur, chow_file_date)

        # Fuzzy facility-name matches count only when the names agree once
        # generic words are set aside (pipeline/cms_confirm.same_facility_name):
        # "The Ridge Post Acute" had been linked to Ocean, Pelican and
        # Chestnut Ridge Post Acute. Debtor and owner matches aren't name-based.
        cur.execute("""
            SELECT m.deal_id::text, m.ccn, m.provider_name, d.facility_names, d.operator_names, d.acquiring_entity
            FROM cms_matches m JOIN deals d ON d.id = m.deal_id
            WHERE m.match_method = 'facility_name_fuzzy' AND d.stage <> 'dismissed'
        """)
        weak = set()
        for deal_id, ccn, provider, facs, ops, buyer in cur.fetchall():
            # A home renamed after its sale carries the buyer's name in CMS
            # (Oak Hill -> "Accolade Healthcare of Waterloo")
            names = (facs or []) + (ops or []) + ([buyer] if buyer else [])
            if not any(same_facility_name(n, provider or "") for n in names):
                weak.add((deal_id, ccn))
        strong, exact, fuzzy = {}, {}, set()
        cur.execute("""SELECT deal_id::text, ccn, match_method FROM cms_matches WHERE ccn IS NOT NULL""")
        for deal_id, ccn, method in cur.fetchall():
            if method != "facility_name_fuzzy":
                strong.setdefault(deal_id, set()).add(ccn)
            if "fuzzy" in method:
                fuzzy.add((deal_id, ccn))
            else:
                exact.setdefault(deal_id, set()).add(ccn)
        dropped = 0
        for deal in deals:
            if deal.get("ccns") and deal["source_type"] != "chow":
                keep = [c for c in deal["ccns"] if (deal["id"], c) not in weak or c in strong.get(deal["id"], ())]
                # A record matched exactly (its debtor or CHOW buyer is the
                # facility) is about that facility: a fuzzy name match can't
                # add another (Apple Valley Care Center had picked up Apple
                # Valley Post-Acute Rehab in Sebastopol, 2026-10-13)
                if exact.get(deal["id"]):
                    keep = [c for c in keep if c in exact[deal["id"]] or (deal["id"], c) not in fuzzy]
                dropped += len(deal["ccns"]) - len(keep)
                deal["ccns"] = keep or None
        print(f"Dropped {dropped} fuzzy facility matches whose names don't agree")

        # A news story or state notice that names its homes is about those
        # homes: a linked home must agree with one of the names (or the
        # seller's, a notice's current operator), or sit in a town the record
        # mentions (a home renamed after its sale). Owner and buyer-name
        # matches had linked Ensign's St. Elizabeth Care Center to two
        # Providence hospitals and an 18-home Maryland portfolio to Snow Hill,
        # which isn't in it (audit 2026-10-21)
        # agent_deal_verified: tied by rule in agent/deals.py (a named party is the
        # home's owner or CHOW buyer/seller), e.g. Valley Convalescent renamed Manresa
        cur.execute("SELECT deal_id::text, ccn FROM cms_matches WHERE match_method IN "
                    "('chow_ccn_direct', 'cms_ownership_direct', 'agent_deal_verified')")
        direct = set(cur.fetchall())
        cur.execute("SELECT ccn, provider_name, provider_city FROM cms_facilities")
        home = {ccn: (name, city) for ccn, name, city in cur.fetchall()}
        unnamed = 0
        for deal in deals:
            if deal["source_type"] in ("chow", "ucc") or not deal.get("facility_names") or not deal.get("ccns"):
                continue
            names = deal["facility_names"] + ([deal["seller_entity"]] if deal.get("seller_entity") else [])
            text = " ".join([deal.get("summary") or "", deal.get("source_title") or ""] + deal["facility_names"]).upper()
            keep = [c for c in deal["ccns"] if (deal["id"], c) in direct or c not in home
                    or any(facility_names_agree(n, home[c][0]) for n in names)
                    or (home[c][1] and re.search(rf"\b{re.escape(home[c][1].upper())}\b", text))]
            unnamed += len(deal["ccns"]) - len(keep)
            deal["ccns"] = keep or None
        print(f"Dropped {unnamed} links to homes a record doesn't name")

        # UCC financing -> acquisition financing / refinancing, and one row
        # per repeated loan (pipeline/ucc_category.py). Holding companies
        # count too: a sale above the licensed LLC changes only its
        # indirect owners (Valley View, CA; Maple, KY; audit 2026-10-21)
        cur.execute("""
            SELECT DISTINCT ccn, owner_name, ownership_start_date, owner_role NOT ILIKE '%INDIRECT%'
            FROM cms_ownership_records
            WHERE ownership_start_date IS NOT NULL
              AND owner_role ILIKE '%DIRECT OWNERSHIP%'
              AND (owner_role NOT ILIKE '%INDIRECT%' OR owner_type ILIKE 'ORG%')
        """)
        rows = cur.fetchall()
        owner_starts = owner_start_dates(r[:3] for r in rows)
        # How far CMS's owner data is complete is judged on direct owners
        # alone: counting holding companies moved it from 2025-12 to 2026-03
        known_through = complete_through(owner_start_dates(r[:3] for r in rows if r[3]))
        cur.execute("""
            SELECT LEFT(ccn, 6), TO_DATE(effective_date, 'MM/DD/YYYY') FROM chow_seen_records
            WHERE effective_date ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$'
        """)
        for ccn, start in cur.fetchall():
            owner_starts.setdefault(ccn, set()).add(start)
        # State notices' dates count for the homes they name exactly
        cms = CmsIndex(conn)
        notices = [{**d, "ccns": cms.ccns_by_name(_resolved_facilities(d), d.get("states"))}
                   for d in deals if d["source_type"] == "con"]
        categorize(deals, owner_starts, notices=notices, known_through=known_through)
        deals = collapse_repeats(deals)

        cur.execute("SELECT deal_id::text, ccn, provider_name, match_type FROM ucc_individual_matches ORDER BY provider_name")
        homes = {}
        for deal_id, ccn, provider, kind in cur.fetchall():
            homes.setdefault(deal_id, []).append((ccn, provider))
        for deal in deals:
            _add_labels(deal, cms, homes)

        # Rows pipeline/same_sale.py merged away still count as sources of
        # the deal they went into (following merges of merges)
        cur.execute("""
            SELECT d.id::text, d.merged_into::text, d.acquisition_date, d.created_at, s.source_type, s.name, a.url, a.title
            FROM deals d JOIN articles a ON a.id = d.article_id LEFT JOIN sources s ON s.id = a.source_id
            WHERE d.merged_into IS NOT NULL
        """)
        rows = cur.fetchall()
        into = {r[0]: r[1] for r in rows}
        live = {d["id"] for d in deals}
        merged = {}
        for did, target, acq, created, stype, sname, url, title in rows:
            seen = set()
            while target not in live and target in into and target not in seen:
                seen.add(target)
                target = into[target]
            if target in live and not (url or "").startswith("ucc://"):
                merged.setdefault(target, []).append({
                    "id": did, "source_type": stype, "source_name": sname, "source_url": url, "source_title": title,
                    "date": str(acq) if acq else created.date().isoformat(), "found_date": created.date().isoformat(),
                    "status": None, "headline": title})
        # Public corrections log: every correction note, newest first
        cur.execute("""
            SELECT n.created_at, n.deal_id::text, n.note, d.stage, a.title
            FROM annotations n JOIN deals d ON d.id = n.deal_id JOIN articles a ON a.id = d.article_id
            WHERE n.tag = 'correction' ORDER BY n.created_at DESC
        """)
        live_ids = {d["id"] for d in deals}
        corrections = [{"date": c.date().isoformat(), "id": did, "note": note,
                        "on_site": did in live_ids, "title": title}
                       for c, did, note, stage, title in cur.fetchall()]

        facilities = _facility_pages(cur, deals)

        counts = group(deals, merged)
        for deal in deals:
            if deal.get("group_ccns") and deal["source_type"] != "ucc":
                deal["beds"] = cms.beds(deal["group_ccns"], deal.get("facility_count"))
        print(f"Grouped {counts['grouped_records']} records into {counts['groups']} multi-source deals")

    conn.close()

    freshness = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_ran_at": pipeline_ran_at.isoformat() if pipeline_ran_at else None,
        "chow_file_date": chow_file_date,
        **source_freshness,
    }

    ucc_search_forms = {}
    nj_form = _fetch_nj_search_form()
    if nj_form:
        ucc_search_forms["NJ"] = nj_form

    out_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/deals.json"
    with open(out_path, "w") as f:
        json.dump({"deals": deals, "total": len(deals), "ucc_search_forms": ucc_search_forms, "freshness": freshness,
                   "corrections": corrections, "facilities": facilities}, f, default=str)

    feed_path = os.path.join(os.path.dirname(os.path.abspath(out_path)), "feed.xml")
    write_atom_feed(deals, feed_path, freshness["exported_at"])

    print(f"Exported {len(deals)} deals to {out_path} and {feed_path} (UCC search forms: {', '.join(ucc_search_forms) or 'none'})")


if __name__ == "__main__":
    main()
