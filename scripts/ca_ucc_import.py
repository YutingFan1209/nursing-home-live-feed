"""
Load California's weekly bulk data files: UCC filings (ucc/ca_download.py)
and new business entities (ucc/ca_entities.py).

A person requests each week in bizfile Online (Data Requests -> + New Data
Request) twice -- "UCC Bulk Order - Weekly Data & Images" (Data) and "BE Bulk
Order - Weekly Data" -- and drops the DataRequest0x....zip files in data/.
The two kinds are told apart by their contents. Entity files go first: new
acquisition-style entities ('watch' tier) count as nursing home debtors in
the same run's UCC files.

    venv/bin/python3 scripts/ca_ucc_import.py data/DataRequest0x*.zip

Filings and entities already loaded are skipped, so re-running over the same
files is safe. First load 2026-10-14: nine UCC weeks (2026-08-17 to 10-11),
eight entity weeks.
"""
import sys

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2

import main
from config import get_config
from scraper.chow import get_chow_operator_names
from scraper.ucc import _filing_to_article
from ucc import ca_entities
from ucc.ca_download import load_week
from ucc.lender_search import norm_name


def load_entities(conn, paths: list[str]) -> None:
    new = ca_entities.save(conn, [e for p in paths for e in ca_entities.read_week(p)])
    print(f"{len(paths)} entity files -> {len(new)} new acquisition-style entities")
    for e in sorted(new, key=lambda e: (e["tier"], e["filed_on"] or "")):
        print(f"  {e['tier']:6} {e['filed_on']} {e['entity_name']} | {e['type_of_business'] or '-'} | "
              f"{e['principal_city']}, {e['principal_state']} | {e['managers'] or '-'}")


def run(conn, paths: list[str]) -> None:
    be = [p for p in paths if ca_entities.is_be_zip(p)]
    if be:
        load_entities(conn, be)
    paths = [p for p in paths if p not in be]
    if not paths:
        return
    names = main._cms_healthcare_names(conn, "CA") | {
        norm_name(n) for n in get_chow_operator_names("CA") + main._get_known_operator_names(conn, "CA")
        + ca_entities.watch_names(conn)}
    source_id = main.ensure_ucc_source(conn)
    filings = [f for p in paths for f in load_week(p, names)]
    articles = deals = 0
    for f in filings:
        art = _filing_to_article(f)
        if main._article_exists(art["url"], conn):
            continue
        art["source_id"] = source_id
        articles += 1
        deals += main.process_article(art, conn)
        conn.commit()
    print(f"{len(paths)} UCC files -> {len(filings)} nursing home filings -> {articles} new -> {deals} deals")


if __name__ == "__main__":
    run(psycopg2.connect(get_config().database_url), sys.argv[1:])
