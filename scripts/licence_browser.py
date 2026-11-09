"""
Michigan and Ohio nursing home licence lists, read in the person's own
Chrome (Claude in Chrome) every two weeks, then diffed here like the other
licence lists (scraper/licence_lists.py).

    # Michigan: on https://statelicensing.apps.lara.state.mi.us/ paste
    #   scripts/mi_licence_browser.js (~15 min; poll window.__mil)
    #   -> ~/Downloads/mi_licence_<timestamp>.json
    # Ohio: on https://publicapps.odh.ohio.gov/eid/reports/EID_Report_Criteria.aspx
    #   paste scripts/oh_licence_browser.js (~10 s; poll window.__ohl)
    #   -> ~/Downloads/oh_licence_<timestamp>.json
    venv/bin/python3 scripts/licence_browser.py ingest ~/Downloads/mi_licence_*.json
    venv/bin/python3 scripts/licence_browser.py ingest --dry-run ~/Downloads/oh_licence_*.json

A state's first ingest compares with CMS's legal names, limited to licences
dated in the last year (Michigan has the date; Ohio doesn't, so its first
ingest only stores the baseline). Later ingests compare with the last one.
--dry-run prints what would be stored and writes nothing.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import psycopg2

import main
from config import get_config
from pipeline.same_sale import resolve_new_deals
from scraper.licence_lists import LISTS, licence_list_deals, parse_browser_file

# Fewer homes than this: a stopped or changed read, not worth diffing
MIN_ROWS = {"MI": 350, "OH": 700}


def ingest(conn, path: str, dry_run: bool) -> None:
    data = json.loads(Path(path).expanduser().read_text())
    state, rows = parse_browser_file(data)
    if data.get("stopped"):
        print(f"{path}: the read stopped early ({data['stopped']})")
    if len(rows) < MIN_ROWS[state]:
        sys.exit(f"{path}: only {len(rows)} {state} homes with a licensee (expected {MIN_ROWS[state]}+); not ingested")
    deals = licence_list_deals(state, conn, lambda url: main._article_exists(url, conn), rows=rows,
                               write_snapshot=not dry_run)
    print(f"{state}: {len(rows)} homes read, {sum(1 for r in rows if r['ccn'])} matched to CMS, "
          f"{len(deals)} new licensees")
    for d in deals:
        print(f"  {d['title']}  (was {d['seller_entity']}, {d['acquisition_date']})")
    if dry_run:
        conn.rollback()
        print("dry run: nothing written")
        return
    cfg = LISTS[state]
    source_id = main._ensure_source(
        type("S", (), {"name": cfg["source_name"], "url": cfg["page_url"], "source_type": "con"})(), conn)
    with conn.cursor() as cur:
        cur.execute("SELECT now()")
        started = cur.fetchone()[0]  # DB clock: the host's runs ahead of the container's
    stored = 0
    for deal in deals:
        deal["source_id"] = source_id
        stored += main.process_article(deal, conn)
        conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM deals WHERE created_at >= %s", (started,))
        merged, to_review = resolve_new_deals(conn, [r[0] for r in cur.fetchall()])
    conn.commit()
    print(f"stored {stored} deals; same-sale check: {merged} merged, {to_review} pairs to review")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] != "ingest":
        sys.exit(__doc__)
    dry = "--dry-run" in args
    conn = psycopg2.connect(get_config().database_url)
    for p in [a for a in args[1:] if a != "--dry-run"]:
        ingest(conn, p, dry)
