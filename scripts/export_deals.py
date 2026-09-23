"""
scripts/export_deals.py

Exports deals.json for the gh-pages static frontend. Run from repo root
on `main`, then copy the output to deals.json on `gh-pages` and push --
see README's Deployment section for the full step-by-step.

Excludes dismissed-stage deals (matches the `stage NOT IN ('dismissed')`
filter main_api.py's live queries already use).

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
from xml.sax.saxutils import escape
from datetime import datetime, timezone

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2
import requests
from config import get_config
from ucc.lender_classifier import LenderCategory, classify_secured_party

UCC_PORTAL_URLS = {
    "NY": "https://ucc-efiling.dos.ny.gov/OnlineUCCSearch/OnlineUCCSearch",
    "KY": "https://web.sos.ky.gov/ftucc/search.aspx",
    "OH": "https://ucc.ohiosos.gov/search",
    "PA": "https://file.dos.pa.gov/search/ucc",
    "NJ": "https://www.njportal.com/ucc/search/noncertifiedsearch.aspx",  # was missing entirely -- confirmed 2026-09-22 that every NJ deal (337) was falling through to the raw ucc://NJ/{filing_number} scheme URL untouched, which isn't navigable at all (no browser handles the ucc:// protocol), worse than every other state's fallback
    "CA": "https://bizfileonline.sos.ca.gov/search/ucc",
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


def _ucc_display_fields(source_url: str, source_title: str, detail_url: str | None):
    """source_url for a UCC deal is 'ucc://STATE/FILING_NUMBER' -- parse
    it and swap in a real, clickable URL: detail_url (a genuine per-filing
    deep link, from ucc_filings.detail_url) when one exists, else the
    portal's search page with the filing number in the title so a viewer
    can search for it themselves."""
    state, _, filing_number = source_url.removeprefix("ucc://").partition("/")
    if detail_url:
        return detail_url, f"{state} UCC-1 filing #{filing_number}"
    portal = UCC_PORTAL_URLS.get(state.upper())
    if not portal:
        return source_url, source_title
    title = f"{state} UCC-1 filing #{filing_number} — search this number on the state portal"
    return portal, title


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


def _news_deal_type(deal: dict) -> str:
    """News extraction pulls parties and amounts but no deal type, and
    stored article titles are often a snippet rather than the headline, so
    keyword-matching them misfires. Only call it financing when the
    extracted fields say so: a loan amount with no buyer and no price."""
    if deal.get("financing_amount_m") and not deal.get("deal_value_m") and not deal.get("acquiring_entity"):
        return "financing"
    return "ownership_change"


SITE_URL = "https://yutingfan1209.github.io/nursing-home-live-feed/"
FEED_SIZE = 50


def _feed_title(d: dict) -> str:
    states = f" ({', '.join(d['states'])})" if d.get("states") else ""
    if d["source_type"] == "ucc":
        subject = d.get("ucc_debtor") or (d.get("facility_names") or ["?"])[0]
        lender = d.get("lender") or ""
        via = "" if not lender or lender.startswith("Not available") else f" — {lender}"
        return f"UCC financing: {subject}{via}{states}"
    parties = " acquires from ".join(p for p in (d.get("acquiring_entity"), d.get("seller_entity")) if p)
    kind = "Financing" if d.get("deal_type") == "financing" else "Ownership change"
    return f"{kind}: {parties or d.get('source_title') or 'deal reported'}{states}"


def write_atom_feed(deals: list[dict], path: str, updated: str) -> None:
    """Newest-first Atom feed of what the pipeline found, so people can
    follow the site in a feed reader. Static hosting can't send per-view
    email alerts; this is the closest thing. Ordered by when a deal was
    found (created_at), not its effective date, and skips non-deal liens."""
    recent = sorted((d for d in deals if d.get("deal_type") != "non_deal_lien"),
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
                d.lender, d.ucc_confirmed, d.stage,
                s.source_type, s.name AS source_name,
                a.url AS source_url, a.title AS source_title,
                uf.detail_url AS ucc_detail_url,
                uf.debtor_name AS ucc_debtor, uf.filing_type AS ucc_filing_type,
                ARRAY_AGG(DISTINCT m.ccn) FILTER (WHERE m.ccn IS NOT NULL) AS ccns
            FROM deals d
            JOIN articles a ON a.id = d.article_id
            LEFT JOIN sources s ON s.id = a.source_id
            LEFT JOIN cms_matches m ON m.deal_id = d.id
            LEFT JOIN ucc_filings uf
                ON uf.state = split_part(a.url, '/', 3)
                AND uf.filing_number = split_part(a.url, '/', 4)
                AND s.source_type = 'ucc'
            WHERE d.stage NOT IN ('dismissed')
            GROUP BY d.id, a.url, a.title, s.source_type, s.name, uf.detail_url, uf.debtor_name, uf.filing_type
            ORDER BY COALESCE(d.acquisition_date, d.created_at::date) DESC, d.created_at DESC
        """)
        cols = [c[0] for c in cur.description]
        deals = []
        for row in cur.fetchall():
            deal = dict(zip(cols, row))
            deal["id"] = str(deal["id"])
            deal["acquisition_date"] = str(deal["acquisition_date"]) if deal["acquisition_date"] else None
            deal["created_at"] = deal["created_at"].isoformat() if deal["created_at"] else None
            if deal["source_type"] == "ucc" and deal["source_url"]:
                ucc_state, _, deal["ucc_filing_number"] = deal["source_url"].removeprefix("ucc://").partition("/")
                deal["ucc_state"] = ucc_state.upper()
                deal.update(_ucc_fields(deal, deal.pop("ucc_debtor"), deal.pop("ucc_filing_type")))
                deal["source_url"], deal["source_title"] = _ucc_display_fields(
                    deal["source_url"], deal["source_title"], deal.pop("ucc_detail_url")
                )
            else:
                for k in ("ucc_detail_url", "ucc_debtor", "ucc_filing_type"):
                    deal.pop(k, None)
                deal["deal_type"] = "ownership_change" if deal["source_type"] == "chow" else _news_deal_type(deal)
            deals.append(deal)

        cur.execute("SELECT MAX(last_fetched_at) FROM sources")
        pipeline_ran_at = cur.fetchone()[0]

    conn.close()

    freshness = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_ran_at": pipeline_ran_at.isoformat() if pipeline_ran_at else None,
        "chow_file_date": _chow_file_date(),
    }

    ucc_search_forms = {}
    nj_form = _fetch_nj_search_form()
    if nj_form:
        ucc_search_forms["NJ"] = nj_form

    out_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/deals.json"
    with open(out_path, "w") as f:
        json.dump({"deals": deals, "total": len(deals), "ucc_search_forms": ucc_search_forms, "freshness": freshness}, f, default=str)

    feed_path = os.path.join(os.path.dirname(os.path.abspath(out_path)), "feed.xml")
    write_atom_feed(deals, feed_path, freshness["exported_at"])

    print(f"Exported {len(deals)} deals to {out_path} and {feed_path} (UCC search forms: {', '.join(ucc_search_forms) or 'none'})")


if __name__ == "__main__":
    main()
