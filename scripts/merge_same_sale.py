"""
Find ownership deals that are one sale stored twice (pipeline/same_sale.py).

    venv/bin/python3 scripts/merge_same_sale.py            # list auto + review pairs
    venv/bin/python3 scripts/merge_same_sale.py --apply    # merge the auto pairs
    venv/bin/python3 scripts/merge_same_sale.py --merge ID_A,ID_B [ID_C,ID_D ...]
                                                           # merge review pairs a person approved

--apply appends each merge (both rows as they were) to
data/same_sale_merges.jsonl, so a wrong merge can be undone by hand: the
dropped row is only marked dismissed. Review pairs are never merged here.
"""
import sys

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2

from config import get_config
from pipeline.same_sale import apply_pairs, find_pairs

LOG = "/Users/kitty/Projects/nursing-home-live-feed/data/same_sale_merges.jsonl"


def _line(x, y, reason):
    side = lambda d: f"{d['id']}  {d['source_type']:6} {str(d['acquiring_entity'] or '-')[:30]:30} {d['acquisition_date']}  {d['url']}"
    return f"  {side(x)}\n  {side(y)}\n    -> {reason}"


if __name__ == "__main__":
    conn = psycopg2.connect(get_config().database_url)
    auto, review = find_pairs(conn)
    if "--merge" in sys.argv:
        wanted = {frozenset(a.split(",")) for a in sys.argv[sys.argv.index("--merge") + 1:]}
        pairs = [p for p in review + auto if frozenset((str(p[0]["id"]), str(p[1]["id"]))) in wanted]
        merged = apply_pairs(pairs, conn, LOG)
        conn.commit()
        print(f"{merged} merged of {len(wanted)} approved pairs ({len(pairs)} found)")
    elif "--apply" in sys.argv:
        merged = apply_pairs(auto, conn, LOG)
        conn.commit()
        print(f"{merged} merged ({len(auto)} auto pairs); {len(review)} pairs need review")
    else:
        for title, pairs in (("AUTO", auto), ("REVIEW", review)):
            print(f"== {title}: {len(pairs)}")
            for p in pairs:
                print(_line(*p))
