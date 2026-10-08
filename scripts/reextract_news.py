"""
Re-extract news articles whose deals came out as bare skeletons, and give
news articles their real headlines.

Roundup articles ("Skilled Nursing Dealbook") list several deals with
anonymous parties ("a regional operator"); earlier extractions kept only a
state and a facility count. The current prompt keeps the lender, amounts,
beds and a one-line summary (deals.summary). Google Alert titles are text
fragments ("buyouts and ..."); the page's own headline replaces them.

    venv/bin/python3 scripts/reextract_news.py --titles     # headlines only, all news deals' articles
    venv/bin/python3 scripts/reextract_news.py --bare       # re-extract articles behind bare deals
    venv/bin/python3 scripts/reextract_news.py --summaries  # re-extract articles whose deals have no summary

--bare writes every replaced deal to data/reextract_<date>.jsonl first, and
skips articles with a deal in data/same_sale_merges.jsonl, so a merge made
earlier isn't undone.
"""
import json
import sys
from datetime import date

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2
import psycopg2.extras

import main
from config import get_config
from pipeline.extractor import extract_deals
from scraper.rss import fetch_article, looks_like_snippet

ROOT = "/Users/kitty/Projects/nursing-home-live-feed/data"

_NEWS_ARTICLES = """
    SELECT DISTINCT a.id, a.url, a.title, a.published_at, a.raw_text, a.source_id
    FROM deals d JOIN articles a ON a.id = d.article_id JOIN sources s ON s.id = a.source_id
    WHERE d.stage <> 'dismissed' AND s.source_type = 'rss' {extra}
"""
_BARE = "AND COALESCE(d.acquiring_entity, '') = '' AND COALESCE(array_length(d.facility_names, 1), 0) = 0"
_NO_SUMMARY = "AND d.summary IS NULL"


def _rows(conn, extra=""):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(_NEWS_ARTICLES.format(extra=extra))
        return cur.fetchall()


def fix_titles(conn) -> int:
    fixed = 0
    for a in _rows(conn):
        if not looks_like_snippet(a["title"], a["url"]):
            continue
        _, headline = fetch_article(a["url"])
        if headline and not looks_like_snippet(headline, a["url"]):
            with conn.cursor() as cur:
                cur.execute("UPDATE articles SET title = %s WHERE id = %s", (headline, a["id"]))
            conn.commit()
            fixed += 1
            print(f"  {a['title'][:50]!r} -> {headline[:70]!r}")
    return fixed


def reextract_bare(conn, which: str = _BARE, keep_if_fewer: bool = False) -> tuple[int, int, int]:
    merged = set()
    try:
        for line in open(f"{ROOT}/same_sale_merges.jsonl"):
            m = json.loads(line)
            merged |= {m["keep"]["id"], m["drop"]["id"]}
    except FileNotFoundError:
        pass
    backup = open(f"{ROOT}/reextract_{date.today()}.jsonl", "a")
    done = before = after = 0
    for a in _rows(conn, which):
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM deals WHERE article_id = %s", (a["id"],))
            old = cur.fetchall()
        if any(str(d["id"]) in merged for d in old):
            print(f"  skip (part of a same-sale merge): {a['url']}")
            continue
        deals = extract_deals(a["raw_text"], a["url"], a["published_at"])
        if not deals:
            print(f"  kept as is (re-extraction found no deals): {a['url']}")
            continue
        live = [d for d in old if d["stage"] != "dismissed"]
        if keep_if_fewer and len(deals) < len(live):
            print(f"  kept as is (re-extraction found {len(deals)} deals, {len(live)} stored): {a['url']}")
            continue
        for d in old:
            backup.write(json.dumps(d, default=str) + "\n")
        with conn.cursor() as cur:
            cur.execute("DELETE FROM deals WHERE article_id = %s", (a["id"],))
        article = {**a, "source_type": "rss"}
        after += main._store_article_result(article, a["raw_text"], deals, conn)
        conn.commit()
        before += len(old)
        done += 1
    backup.close()
    return done, before, after


if __name__ == "__main__":
    conn = psycopg2.connect(get_config().database_url)
    if "--titles" in sys.argv:
        print(f"{fix_titles(conn)} titles replaced with the page headline")
    if "--bare" in sys.argv:
        done, before, after = reextract_bare(conn)
        print(f"{done} articles re-extracted: {before} old deals -> {after} new deals")
    if "--summaries" in sys.argv:
        done, before, after = reextract_bare(conn, _NO_SUMMARY, keep_if_fewer=True)
        print(f"{done} articles re-extracted: {before} old deals -> {after} new deals")
