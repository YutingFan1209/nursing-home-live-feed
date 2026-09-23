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
import re
import sys
import json
from datetime import datetime, timezone

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2
import requests
from config import get_config

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
            GROUP BY d.id, a.url, a.title, s.source_type, s.name, uf.detail_url
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
                deal["source_url"], deal["source_title"] = _ucc_display_fields(
                    deal["source_url"], deal["source_title"], deal.pop("ucc_detail_url")
                )
            else:
                deal.pop("ucc_detail_url", None)
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

    print(f"Exported {len(deals)} deals to {out_path} (UCC search forms: {', '.join(ucc_search_forms) or 'none'})")


if __name__ == "__main__":
    main()
