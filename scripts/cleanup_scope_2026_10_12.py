"""
scripts/cleanup_scope_2026_10_12.py

One-off cleanup of news deals the new guards would have stopped (see
pipeline/normalizer.py:looks_non_us, pipeline/al_mc_scope.py:is_pace):
  - non-US deals that came in with no state (UK, Germany, Canada, Malaysia)
  - PACE programs (One Senior Care / PACE North)
plus buyers whose names read as street addresses because the normalizer
stripped "LLC" ("101 W. State Street" acquired Genesis) -- restored to the
full legal name their own source document gives.

Dry run by default; --apply writes. Dismissing only sets stage='dismissed',
so it's reversible.

    venv/bin/python3 scripts/cleanup_scope_2026_10_12.py [--apply]
"""
import sys

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2
from config import get_config
from pipeline.al_mc_scope import is_pace
from pipeline.normalizer import looks_non_us

# Non-US deals the place-name check can't see: the summary names only a
# town (Drybrook, Gloucestershire, from the Ross Gazette)
MANUAL_NON_US = {
    "c0768ac6-998a-4b15-8873-a50147e1dc3f": "Euroclydon Nursing Home, Drybrook, England",
}

# Full legal names, each checked against the deal's own source text
LEGAL_NAMES = {
    "a341faca-8207-4cb4-b508-b3f819ae5d61": "101 W State Street Holdings LLC",  # AL SHPDA CO2026-067 names the buyer
    "d02c8194": "101 W State Street Holdings LLC",
    "27700b28": "1338 20th Street LLC",
    "d78607a6": "3902 Katella Avenue LLC",
    "2b9b79a4": "4401 Haverford Avenue LLC",
    "d3507036": "6530 Democracy Blvd LLC",
}


def main(apply: bool):
    conn = psycopg2.connect(get_config().database_url)
    with conn, conn.cursor() as cur:
        cur.execute("""
            SELECT d.id::text, d.acquiring_entity, d.seller_entity, d.operator_names,
                   d.facility_names, d.states, d.summary
            FROM deals d JOIN articles a ON a.id = d.article_id JOIN sources s ON s.id = a.source_id
            WHERE d.stage <> 'dismissed' AND s.source_type IN ('rss', 'edgar')
        """)
        cols = [c[0] for c in cur.description]
        dismiss = {}
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            if looks_non_us(d):
                dismiss[d["id"]] = ("non-US", d)
            elif is_pace(d):
                dismiss[d["id"]] = ("PACE program", d)
        for deal_id, why in MANUAL_NON_US.items():
            cur.execute("SELECT id::text, acquiring_entity, summary FROM deals WHERE id = %s AND stage <> 'dismissed'", (deal_id,))
            r = cur.fetchone()
            if r:
                dismiss[r[0]] = (f"non-US ({why})", {"acquiring_entity": r[1], "summary": r[2]})

        for deal_id, (why, d) in sorted(dismiss.items(), key=lambda kv: kv[1][0]):
            print(f"DISMISS {deal_id[:8]} [{why}] {d.get('acquiring_entity')}: {(d.get('summary') or '')[:110]}")
            if apply:
                cur.execute("UPDATE deals SET stage = 'dismissed' WHERE id = %s", (deal_id,))

        for prefix, name in LEGAL_NAMES.items():
            cur.execute("SELECT id::text, acquiring_entity FROM deals WHERE id::text LIKE %s", (prefix + "%",))
            rows = cur.fetchall()
            if len(rows) != 1:
                print(f"SKIP rename {prefix}: {len(rows)} matches")
                continue
            deal_id, old = rows[0]
            if old == name:
                continue
            print(f"RENAME  {deal_id[:8]} {old!r} -> {name!r}")
            if apply:
                cur.execute("UPDATE deals SET acquiring_entity = %s WHERE id = %s", (name, deal_id))

        if not apply:
            conn.rollback()
            print("\nDry run -- rerun with --apply to write.")


if __name__ == "__main__":
    main("--apply" in sys.argv)
