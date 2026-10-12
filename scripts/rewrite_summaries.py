"""
scripts/rewrite_summaries.py

Rewrite the one-line summaries of news and SEC deals from the article text,
under the rules the extraction prompt now gives (2026-10-12): full legal
names, pending vs closed said plainly, the buyer's description or
"undisclosed" when unnamed, and nothing the article doesn't say. Records
the article doesn't support as a current deal are flagged for review, never
dismissed here.

The site renders summaries verbatim, and the older ones contradicted their
own labels: Genesis' said "101 W. State Street acquired Genesis HealthCare"
for a sale still awaiting court approval.

    venv/bin/python3 scripts/rewrite_summaries.py --limit 3     # dry run, print
    venv/bin/python3 scripts/rewrite_summaries.py --apply       # all; backup first

--apply writes every old summary to data/summary_rewrite_<date>.jsonl before
changing it, and flagged records to data/review_not_a_deal_<date>.tsv.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import anthropic
import psycopg2
import psycopg2.extras

from config import get_config

MODEL = "claude-opus-5-5"
ROOT = "/Users/kitty/Projects/nursing-home-live-feed/data"

SYSTEM = """You write the one-line summary shown on a public database of US nursing home ownership \
changes, read by researchers and legislators. A wrong statement costs more than a vague one.

You get one record extracted from an article, and the article text. Write the summary of that \
record's transaction only (the article may cover several).

Rules:
- Only facts the article states. No inference, no outside knowledge.
- Use parties' full legal names when the article gives them (e.g. "101 W State Street Holdings LLC", \
not "101 W. State Street"). If the buyer isn't named, use the article's description ("a national REIT") \
or say the buyer was undisclosed if the article says so.
- Say plainly whether the deal is closed, pending (agreed, announced, awaiting approval), a lease, a \
loan (and whether it finances a purchase or refinances), or control passing without a sale. Never say \
"acquired" or "sold" for a deal the article describes as pending.
- Name the kind of facility as the article does (skilled nursing facility, nursing home, assisted \
living, memory care, CCRC...).
- Include the price, facility count, beds and location when the article gives them.
- If the buyer isn't named, keep the article's description of them ("a first-time buyer", "a regional \
buyer"); write "undisclosed" only when the article itself says the buyer wasn't disclosed.
- One or two sentences, under 60 words, plain English.

Set verdict to "not_a_deal" (with a short reason) if the article doesn't describe this record as a \
current transaction: it only mentions an old deal in passing, it's a management contract or profile, \
it's outside the US, or the record's facts aren't in the article."""

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["deal", "not_a_deal"]},
        "summary": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "summary", "reason"],
    "additionalProperties": False,
}


def records(conn, limit=None):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT d.id::text, d.acquiring_entity, d.seller_entity, d.facility_names, d.states,
                   d.facility_count, d.deal_value_m, d.financing_amount_m, d.lender, d.acquisition_date,
                   d.summary, a.title, a.url, a.published_at::date AS published, a.raw_text, s.source_type
            FROM deals d JOIN articles a ON a.id = d.article_id JOIN sources s ON s.id = a.source_id
            WHERE d.stage <> 'dismissed' AND s.source_type IN ('rss', 'edgar') AND a.raw_text IS NOT NULL
              -- summaries corrected by hand stay as reviewed
              AND NOT EXISTS (SELECT 1 FROM annotations n WHERE n.deal_id = d.id AND n.tag = 'correction')
            ORDER BY d.created_at DESC
        """ + (f" LIMIT {int(limit)}" if limit else ""))
        return cur.fetchall()


def rewrite(client, r) -> dict:
    record = {k: r[k] for k in ("acquiring_entity", "seller_entity", "facility_names", "states", "facility_count",
                                "deal_value_m", "financing_amount_m", "lender", "acquisition_date", "summary")}
    prompt = (f"Record (current summary included):\n{json.dumps(record, default=str, indent=1)}\n\n"
              f"Article: {r['title']}\nPublished: {r['published'] or 'unknown'}\nURL: {r['url']}\n\n"
              f"<article>\n{r['raw_text']}\n</article>")
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIStatusError as e:
        return {"id": r["id"], "error": f"{e.status_code}: {e.message}"}
    except anthropic.APIConnectionError as e:
        return {"id": r["id"], "error": f"connection: {e}"}
    if resp.stop_reason == "refusal":
        return {"id": r["id"], "error": "refused"}
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        return {"id": r["id"], "error": f"unparseable: {text[:200]}"}
    return {"id": r["id"], **out}


def main(apply: bool, limit=None):
    conn = psycopg2.connect(get_config().database_url)
    rows = records(conn, limit)
    # Resume: skip records already rewritten (a run can stop partway, e.g.
    # when API credit runs out)
    done = set()
    for path in __import__("glob").glob(f"{ROOT}/summary_rewrite_*.jsonl"):
        with open(path) as f:
            done |= {json.loads(line)["id"] for line in f}
    rows = [r for r in rows if r["id"] not in done]
    print(f"{len(rows)} records to rewrite ({len(done)} already done)")
    client = anthropic.Anthropic()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda r: rewrite(client, r), rows))
    by_id = {r["id"]: r for r in rows}
    errors = [x for x in results if "error" in x]
    flagged = [x for x in results if x.get("verdict") == "not_a_deal"]
    good = [x for x in results if x.get("verdict") == "deal" and x.get("summary", "").strip()]
    for x in results:
        old = by_id[x["id"]]["summary"]
        print(f"\n{x['id'][:8]} [{x.get('verdict') or 'ERROR'}] {by_id[x['id']]['title'][:80]}")
        print(f"  old: {old}")
        print(f"  new: {x.get('summary') or x.get('error')}")
        if x.get("verdict") == "not_a_deal":
            print(f"  why: {x.get('reason')}")
    print(f"\n{len(good)} rewritten, {len(flagged)} flagged not a deal, {len(errors)} errors")
    if not apply:
        print("Dry run -- rerun with --apply to write.")
        return
    stamp = date.today().isoformat()
    with open(f"{ROOT}/summary_rewrite_{stamp}.jsonl", "a") as f:
        for x in good:
            f.write(json.dumps({"id": x["id"], "old": by_id[x["id"]]["summary"], "new": x["summary"]}) + "\n")
    review = f"{ROOT}/review_not_a_deal_{stamp}.tsv"
    new_file = not __import__("os").path.exists(review)
    with open(review, "a") as f:
        if new_file:
            f.write("id\ttitle\turl\treason\n")
        for x in flagged:
            r = by_id[x["id"]]
            f.write(f"{x['id']}\t{r['title']}\t{r['url']}\t{x.get('reason', '')}\n")
    with conn, conn.cursor() as cur:
        for x in good:
            cur.execute("UPDATE deals SET summary = %s, updated_at = NOW() WHERE id = %s", (x["summary"].strip(), x["id"]))
    print(f"Updated {len(good)} summaries; backup in data/summary_rewrite_{stamp}.jsonl")


if __name__ == "__main__":
    lim = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--limit=")), None)
    if "--limit" in sys.argv:
        lim = int(sys.argv[sys.argv.index("--limit") + 1])
    main("--apply" in sys.argv, lim)
