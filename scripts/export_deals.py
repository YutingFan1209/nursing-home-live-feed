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
per-filing deep link -- NY only for now, via lienId) or else the state's
search portal homepage, with the filing number in the title so a viewer
can search for it themselves.
"""
import sys
import json

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2
from config import get_config

UCC_PORTAL_URLS = {
    "NY": "https://ucc-efiling.dos.ny.gov/OnlineUCCSearch/OnlineUCCSearch",
    "KY": "https://web.sos.ky.gov/ftucc/search.aspx",
    "OH": "https://ucc.ohiosos.gov/search",
    "PA": "https://file.dos.pa.gov/search/ucc",
    "CA": "https://bizfileonline.sos.ca.gov/search/ucc",
}


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
                d.facility_count, d.deal_value_m, d.acquisition_date,
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
                deal["source_url"], deal["source_title"] = _ucc_display_fields(
                    deal["source_url"], deal["source_title"], deal.pop("ucc_detail_url")
                )
            else:
                deal.pop("ucc_detail_url", None)
            deals.append(deal)

    conn.close()

    out_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/deals.json"
    with open(out_path, "w") as f:
        json.dump({"deals": deals, "total": len(deals)}, f, default=str)

    print(f"Exported {len(deals)} deals to {out_path}")


if __name__ == "__main__":
    main()
