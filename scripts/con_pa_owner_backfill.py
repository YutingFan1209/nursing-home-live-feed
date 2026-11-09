"""
Add the 5%+ owner list to Pennsylvania licence-application deals stored
before scraper/con_pa.py read it (2026-11-09): appends the owners sentence
to the summary and organization owners to operator_names. Skips deals that
already have it.

    venv/bin/python3 scripts/con_pa_owner_backfill.py [--dry-run]
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
from curl_cffi import requests as cffi_requests

from config import get_config
from scraper.con_pa import _parse_application, app_record_url, fetch_owners, owners_sentence


def main(dry_run: bool) -> None:
    conn = psycopg2.connect(get_config().database_url)
    with conn, conn.cursor() as cur:
        cur.execute("""
            SELECT d.id, a.url, d.summary, d.operator_names FROM deals d JOIN articles a ON a.id = d.article_id
            WHERE a.url LIKE '%%InitialApplication.aspx?ipaappid=%%'
        """)
        for deal_id, url, summary, operators in cur.fetchall():
            if "Owners with 5% or more" in (summary or ""):
                continue
            app_id = int(re.search(r"ipaappid=(\d+)", url).group(1))
            session = cffi_requests.Session(impersonate="chrome")
            resp = session.get(app_record_url(app_id), timeout=60)
            resp.raise_for_status()
            app = _parse_application(resp.text)
            owners = fetch_owners(session, app_id, app)
            print(f"{app_id} {app['facility']}: {[(o['name'], o['kind']) for o in owners]}")
            if not owners or dry_run:
                continue
            new_ops = list(dict.fromkeys((operators or []) + [o["name"] for o in owners if o["kind"] == "organization"]))
            cur.execute("UPDATE deals SET summary = %s, operator_names = %s, updated_at = NOW() WHERE id = %s",
                        ((summary or "").rstrip() + owners_sentence(owners), new_ops, deal_id))


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
