"""
Homes with an early sign of a sale (a UCC-1 to new holding companies, say)
that CMS hasn't recorded yet. Each pipeline run checks them against the CMS
Ownership file and the CHOW records and logs a WATCH: line when one shows a
new owner, or every run when nothing has turned up yet, so the list doesn't
get forgotten. Remove an entry once its sale is confirmed and on the site.

Run on its own: venv/bin/python3 -m pipeline.ccn_watch
"""

import logging

logger = logging.getLogger(__name__)

# ccn, or name (searched in CHOW buyer and CMS provider/owner names) for a
# home CMS doesn't list under its new name yet; since = earliest date a new
# owner can start for it to count
WATCH = [
    {"ccn": "065331", "label": "Larchwood Health and Rehab, Grand Junction CO", "since": "2026-05-01",
     "why": "DMT SPE I loans 2026-06-01 to Larchwood Opco/Propco Holdings, American Fork UT "
            "(the group that took over 8 CO homes 2024-09-01); CMS owner since 2014"},
    {"ccn": "065355", "label": "High Plains Post Acute, Pueblo CO", "since": "2026-05-01",
     "why": "DMT SPE I loans 2026-06-01 to High Plains SNF/ALF/ILF Opco Holdings, American Fork UT; "
            "CMS owner since 1995"},
    {"ccn": "065213", "label": "Health Center at Franklin Park, Denver CO", "since": "2026-08-01",
     "why": "probably Franklin Park Health and Rehab LLC: DMT SPE I loans 2026-09-01, American Fork UT"},
    {"name": "GRANDVIEW HEALTH AND REHAB", "label": "Grandview Health and Rehab (CO, home unknown)", "since": "2026-08-01",
     "why": "DMT SPE I loans 2026-09-01 to Grandview SNF/ALF/ILF Opco Holdings, American Fork UT; "
            "no CMS home by that name yet"},
]


def check_watch(conn) -> list[str]:
    lines = []
    with conn.cursor() as cur:
        for w in WATCH:
            if w.get("ccn"):
                cur.execute("""
                    SELECT owner_name, ownership_start_date FROM cms_ownership_records
                    WHERE ccn = %(ccn)s AND ownership_start_date >= %(since)s
                      AND owner_role ILIKE '%%DIRECT OWNERSHIP%%' AND owner_role NOT ILIKE '%%INDIRECT%%'
                    UNION
                    SELECT buyer_name, TO_DATE(effective_date, 'MM/DD/YYYY') FROM chow_seen_records
                    WHERE ccn = %(ccn)s AND effective_date ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$'
                      AND TO_DATE(effective_date, 'MM/DD/YYYY') >= %(since)s
                """, w)
            else:
                pattern = f"%{w['name']}%"
                cur.execute("""
                    SELECT buyer_name || ' (CHOW, CCN ' || ccn || ')', TO_DATE(effective_date, 'MM/DD/YYYY')
                    FROM chow_seen_records
                    WHERE buyer_name ILIKE %(p)s AND effective_date ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$'
                    UNION
                    SELECT owner_name || ' -> ' || provider_name || ' (CCN ' || ccn || ')', ownership_start_date
                    FROM cms_ownership_records WHERE provider_name ILIKE %(p)s OR owner_name ILIKE %(p)s
                """, {"p": pattern})
            hits = sorted(cur.fetchall(), key=lambda r: str(r[1]))
            if hits:
                found = "; ".join(f"{name} from {start}" for name, start in hits)
                lines.append(f"{w['label']}: NEW OWNER RECORDED: {found}")
            else:
                lines.append(f"{w['label']}: nothing in CMS/CHOW yet ({w['why']})")
    return lines


def log_watch(conn) -> None:
    try:
        lines = check_watch(conn)
    except Exception as e:
        logger.warning(f"Watch list check failed: {e}")
        return
    for line in lines:
        logger.info(f"WATCH: {line}")


if __name__ == "__main__":
    import psycopg2
    from config import get_config
    conn = psycopg2.connect(get_config().database_url)
    for line in check_watch(conn):
        print(line)
