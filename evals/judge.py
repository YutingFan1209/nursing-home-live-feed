"""
Claude pre-labels site cards for the monthly audit; a person confirms.

The judge reads one card at a time with the evidence a human labeller used
(the source text, CMS facility and owner rows, CHOW records for its homes,
and other site cards that may be the same deal) and returns a label in the
gold-set format (evals/gold/labels.jsonl).

  gold     Check the judge against the 150 hand labels. Run once, and again
           after any change to the prompt or model; its agreement is what
           says how far a pre-label can be trusted.
           venv/bin/python3 -m evals.judge gold <export.json> [--yes] [--limit N]

  sample   Pre-label a fresh sample for review (never the gold cards).
           venv/bin/python3 -m evals.judge sample <export.json> --seed 20261221 [--yes]

Both print a cost estimate and stop unless --yes is given. Output goes to
data/evals/ (gitignored: cards and summaries can name people).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "evals"
MODEL = "claude-opus-5-5"
PRICE_IN, PRICE_OUT = 4.00, 20.00          # $ per million tokens, Opus 5.5
SOURCE_CHARS = 15000

SHOWN = ["deal_type", "buyer", "seller", "facility_names", "ccns", "states", "facility_count",
         "acquisition_date", "deal_value_m", "financing_amount_m", "lender", "status", "assumed_closed", "facility_type",
         "headline", "summary", "source_type", "source_url", "source_title", "published_at"]
FIELDS = ["deal_type", "buyer", "seller", "facility_count", "states", "acquisition_date",
          "deal_value_m", "financing_amount_m", "lender", "status", "facility_type", "ccns"]
VERDICTS = ["right", "wrong", "minor", "duplicate", "noise", "uncertain"]

SYSTEM = """You audit cards on a public feed of US nursing home ownership changes. Each card was \
built by a pipeline from one source: a news article or SEC filing, a state notice or licence list \
(source_type con), a UCC-1 lien filing (ucc), or a CMS record (chow: CMS change-of-ownership file or \
CMS owner-file change). You get the card's shown fields and the evidence. Decide whether the card is \
right, field by field, the way a careful human labeller would. Judge only from the evidence given; \
when it does not settle a field, say so (unsure_fields) rather than guess.

Verdict (one per card):
- right: every scored field matches the evidence, or is blank where the source gives nothing.
- wrong: at least one fact is wrong: a number, date, party, state, home count, deal type or status \
the source contradicts or does not contain (an invented price or date is wrong, even if plausible).
- minor: facts right, but a name is cut short ("National Health" for National Health Corporation; \
"...of Bessemer" for "...of Bessemer Propco LLC"), a description sits in a name field ("An unnamed \
family operator", "third-party debt"), or lender text has address junk or two names run together.
- duplicate: the same deal is already on the site as another card listed under OTHER CARDS (same \
homes, same buyer side, closing within a few weeks). Name that card's id in the note.
- noise: true, but not a nursing home deal: an equipment vendor's or landlord's lien shown as a loan, \
a consumer loan, a merchant cash advance, a non-US or non-nursing-home business.
- uncertain: the evidence can't settle whether the card is right.

Field rules:
- buyer, seller, lender: the legal or reported name of the party. A broker or arranger (e.g. one that \
"arranged" or "placed" the loan) is not the lender. A manager is not the buyer. The home's own \
operating company is not a lender. Blank is right when the source names nobody. Site conventions \
that are right: a New Jersey UCC card's lender reads "Not available ..." (NJ's search never returns \
it); a CMS or state-filing card may carry the lender from a UCC filing on the same home near the sale \
date, so a lender missing from the CMS record is not wrong by itself. Filing boilerplate left on a \
correct lender ("its successors and assigns, as agent ...") or two secured parties of the same \
filing joined with ";" is minor, not wrong.
- deal_type values: ownership_change (a sale/purchase), acquisition_financing (a loan that funds a \
purchase: "acquisition loan", "bridge acquisition", or a UCC filing within about 6 months of a new \
owner in CMS records), refinancing (a loan on homes the borrower already owned), financing (a loan \
whose purpose the source doesn't give), lease, operations_transfer, control_change, amendment, \
non_deal_lien (a lien that isn't a deal). A working-capital line on its own is financing, not \
acquisition_financing.
- status values: reported (news says it happened), expected (announced, pending approval, or a \
planned sale), confirmed (a CMS record backs it), on_file (a UCC filing: a record of a loan, not of a \
sale). A planned or pending sale shown as reported is wrong, EXCEPT the site's rule: once a pending \
sale's expected close date has passed (or 90 days after a filing, received or notice date when the \
source gives no expected close), the site assumes it closed and shows reported with \
assumed_closed: true. That is right, not wrong.
- acquisition_date: the closing date the source gives, or the CMS record's date when CMS confirms \
the card. For a state filing (CON, licence list, PHHPC, CHOW notice) that gives no closing date, \
the site shows the filing, received, notice or meeting date: that is right. Any other date that is \
none of these is wrong; a quarter placeholder (2026-10-01 for "4Q26") is minor.
- facility_count and states: the homes in THIS deal, not the buyer's whole portfolio or a separate \
deal in the same article.
- deal_value_m, financing_amount_m: US dollars, millions. A foreign-currency figure converted to \
dollars, or a price the source says was not disclosed, is wrong.
- facility_type: skilled_nursing or assisted_living (memory care counts as assisted living).
- ccns: the CMS IDs of the homes; check them against the CMS FACILITY rows given.

In gold, give the correct value of every field (the shown value when it is right; null where the \
source gives nothing). wrong_fields lists the fields shown wrong (for minor, the field with the \
formatting problem). note: one or two sentences a reviewer can check, citing the evidence. quote: \
the shortest source passage that decides the verdict, copied exactly ("" if none)."""

_NULLABLE = lambda t: {"anyOf": [{"type": t}, {"type": "null"}]}
SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": VERDICTS},
        "gold": {
            "type": "object",
            "properties": {
                "deal_type": _NULLABLE("string"), "buyer": _NULLABLE("string"),
                "seller": _NULLABLE("string"), "facility_count": _NULLABLE("integer"),
                "states": {"type": "array", "items": {"type": "string"}},
                "acquisition_date": _NULLABLE("string"), "deal_value_m": _NULLABLE("number"),
                "financing_amount_m": _NULLABLE("number"), "lender": _NULLABLE("string"),
                "status": _NULLABLE("string"), "facility_type": _NULLABLE("string"),
                "ccns": {"type": "array", "items": {"type": "string"}},
            },
            "required": FIELDS,
            "additionalProperties": False,
        },
        "wrong_fields": {"type": "array", "items": {"type": "string", "enum": FIELDS + ["facility_names"]}},
        "unsure_fields": {"type": "array", "items": {"type": "string", "enum": FIELDS + ["facility_names"]}},
        "duplicate_of": _NULLABLE("string"),
        "note": {"type": "string"},
        "quote": {"type": "string"},
    },
    "required": ["verdict", "gold", "wrong_fields", "unsure_fields", "duplicate_of", "note", "quote"],
    "additionalProperties": False,
}


def _norm(v) -> str:
    s = re.sub(r"\b(llc|inc|corp|co|lp|ltd|the|company|holdings?)\b", " ", str(v or "").lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def neighbours(card: dict, deals: list[dict], k: int = 8) -> list[dict]:
    """Other cards that may be the same deal: a shared home, the same buyer
    or seller, or the same state and home count within 90 days."""
    ccns, b, s = set(card.get("ccns") or []), _norm(card.get("buyer")), _norm(card.get("seller"))
    st, d0 = set(card.get("states") or []), str(card.get("acquisition_date") or card.get("published_at") or "")[:10]

    def days(x):
        try:
            return abs((date.fromisoformat(str(x)[:10]) - date.fromisoformat(d0)).days)
        except ValueError:
            return 10 ** 6

    out = []
    for d in deals:
        if d["id"] == card["id"] or d.get("individual_filing"):
            continue
        score = 3 * bool(ccns & set(d.get("ccns") or []))
        score += 2 * bool(b and _norm(d.get("buyer")) == b) + 2 * bool(s and _norm(d.get("seller")) == s)
        score += bool(st & set(d.get("states") or []) and d.get("facility_count") == card.get("facility_count")
                      and days(d.get("acquisition_date") or d.get("published_at")) <= 90)
        if score >= 2:
            out.append((score, d))
    out.sort(key=lambda x: -x[0])
    return [{k2: d.get(k2) for k2 in ("id", "source_type", "deal_type", "buyer", "seller", "states",
                                       "facility_count", "acquisition_date", "deal_value_m", "headline")}
            for _, d in out[:k]]


def evidence(card: dict, deals: list[dict], cur) -> str:
    cur.execute("""select a.url, a.title, a.published_at, a.raw_text from deals d
                   left join articles a on a.id = d.article_id where d.id = %s""", (card["id"],))
    url, title, pub, text = cur.fetchone() or (None, None, None, None)
    parts = [f"SOURCE {url}\nTITLE {title}\nPUBLISHED {pub}\n{(text or '(no stored text)')[:SOURCE_CHARS]}"]
    for ccn in (card.get("ccns") or [])[:6]:
        cur.execute("""select provider_name, provider_city, provider_state, legal_business_name
                       from cms_facilities where ccn = %s""", (ccn,))
        f = cur.fetchone()
        parts.append(f"CMS FACILITY {ccn}: " + (" | ".join(str(x) for x in f) if f else "not in CMS file"))
        cur.execute("""select owner_name, owner_role, ownership_percentage, ownership_start_date
                       from cms_ownership_records where ccn = %s
                       order by ownership_start_date desc nulls last limit 20""", (ccn,))
        parts += [f"  CMS OWNER {r[0]} | {r[1]} | {r[2]}% | since {r[3]}" for r in cur.fetchall()]
        cur.execute("select buyer_name, effective_date from chow_seen_records where ccn = %s", (ccn,))
        parts += [f"  CMS CHOW buyer {r[0]} effective {r[1]}" for r in cur.fetchall()]
    nb = neighbours(card, deals)
    parts.append("OTHER CARDS on the site that may be the same deal:\n"
                 + ("\n".join(json.dumps(n, default=str) for n in nb) if nb else "(none)"))
    return "\n\n".join(parts)


def prompt(card: dict, ev: str) -> str:
    shown = "".join(f"{k}: {json.dumps(card.get(k), default=str)}\n" for k in SHOWN)
    return f"CARD {card['id']}\n{shown}\nEVIDENCE\n{ev}"


def call(client, text: str) -> tuple[dict, object]:
    r = client.beta.messages.create(
        model=MODEL, max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": text}],
    )
    if r.stop_reason == "refusal":
        raise RuntimeError(f"refused: {getattr(r.stop_details, 'category', None)}")
    return json.loads(next(b.text for b in r.content if b.type == "text")), r.usage


def run(cards: list[dict], deals: list[dict], out_path: Path, yes: bool, group_of) -> list[dict]:
    import psycopg2
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    db = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = db.cursor()
    texts = [(c, prompt(c, evidence(c, deals, cur))) for c in cards]
    tok_in = sum(len(t) for _, t in texts) / 3.5 + len(SYSTEM) / 3.5
    est = tok_in / 1e6 * PRICE_IN + len(texts) * 2500 / 1e6 * PRICE_OUT   # output incl. thinking
    print(f"{len(texts)} cards, ~{tok_in / 1e3:.0f}k input tokens, estimated ${est:.2f} ({MODEL}, effort high)")
    if not yes:
        print("Not run. Add --yes to spend it.")
        return []

    import anthropic
    client = anthropic.Anthropic(max_retries=4)
    done = {}
    if out_path.exists():                      # resume an interrupted run
        done = {json.loads(l)["id"]: json.loads(l) for l in open(out_path) if l.strip()}
    lock, spent = threading.Lock(), [0, 0]

    def one(item):
        c, t = item
        if c["id"] in done:
            return done[c["id"]]
        try:
            lab, u = call(client, t)
        except Exception as e:                 # keep going; the card stays unlabelled
            print(f"  {c['id'][:8]} failed: {e}", file=sys.stderr)
            return None
        rec = {"id": c["id"], "group": group_of(c), "source_type": c.get("source_type"), **lab}
        with lock:
            spent[0] += u.input_tokens + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0)
            spent[1] += u.output_tokens
            with open(out_path, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        print(f"  {c['id'][:8]} {lab['verdict']}", file=sys.stderr)
        return rec

    with ThreadPoolExecutor(max_workers=6) as ex:
        res = [r for r in ex.map(one, texts) if r]
    cost = spent[0] / 1e6 * PRICE_IN + spent[1] / 1e6 * PRICE_OUT
    print(f"labelled {len(res)}/{len(texts)}; this run used {spent[0] / 1e3:.0f}k in / {spent[1] / 1e3:.0f}k out"
          f" tokens, about ${cost:.2f} (cache reads priced as full input: an upper bound)")
    return res


# ---- agreement with the hand labels --------------------------------------

def hand_verdict(g: dict, card: dict | None) -> str:
    """The hand label as it applies to the card shown now: a wrong or minor
    card whose fields have since been fixed (2026-11-16 lender and grounding
    fixes) is right now."""
    from evals.score import same
    if card is None or g["verdict"] not in ("wrong", "minor"):
        return g["verdict"]
    fixed = all(same(f, card.get(f), g["gold"].get(f)) for f in FIELDS if f not in g["unsure_fields"])
    if g["wrong_fields"] == ["facility_names"]:
        fixed = False                              # not a scored field: can't tell
    return "right" if fixed else g["verdict"]


def agreement(gold: list[dict], judged: dict[str, dict], cards: dict[str, dict] | None = None) -> dict:
    """How often the judge's label matches the hand label.

    ok/not-ok: right vs everything else (what decides whether a person needs
    to look). Field: of the fields the person marked wrong, how many the
    judge also marked (recall), and of the judge's, how many the person
    marked (precision)."""
    from evals.score import same
    ok = lambda v: v == "right"
    rows, tp, fn, fp = [], 0, 0, 0
    conf = {(a, b): 0 for a in (True, False) for b in (True, False)}
    fld_hit = fld_n = 0
    for g in gold:
        j = judged.get(g["id"])
        if not j:
            continue
        g = {**g, "verdict": hand_verdict(g, (cards or {}).get(g["id"]))}
        conf[(ok(g["verdict"]), ok(j["verdict"]))] += 1
        gw = set(g["wrong_fields"]) if g["verdict"] != "right" else set()
        jw = set(j["wrong_fields"]) if j["verdict"] != "right" else set()
        tp, fn, fp = tp + len(gw & jw), fn + len(gw - jw), fp + len(jw - gw)
        for f in FIELDS:
            if f in g["unsure_fields"] or f in j["unsure_fields"]:
                continue
            fld_n += 1
            fld_hit += same(f, j["gold"].get(f), g["gold"].get(f))
        if ok(g["verdict"]) != ok(j["verdict"]) or g["verdict"] != j["verdict"]:
            rows.append((g["id"][:8], g["group"], g["verdict"], j["verdict"], g["note"][:70], j["note"][:90]))
    n = sum(conf.values())
    return {"n": n, "conf": conf, "verdict_same": n - len(rows),
            "field_recall": tp / max(tp + fn, 1), "field_precision": tp / max(tp + fp, 1),
            "gold_value_agree": fld_hit / max(fld_n, 1), "disagree": rows}


def cmd_gold(a) -> None:
    from evals.score import load_gold, wilson
    gold = load_gold()
    deals = json.load(open(a.export))["deals"]
    by_id = {d["id"]: d for d in deals}
    cards = [by_id[g["id"]] for g in gold if g["id"] in by_id]
    groups = {g["id"]: g["group"] for g in gold}
    if a.limit:
        cards = cards[:a.limit]
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"judge_gold_{date.today()}.jsonl"
    res = run(cards, deals, out, a.yes, lambda c: groups[c["id"]])
    if not res:
        return
    ag = agreement(gold, {r["id"]: r for r in res}, by_id)
    c = ag["conf"]
    print(f"\nJudge vs hand labels ({ag['n']} cards; {len(gold) - len(cards)} gold cards no longer on the site)")
    print(f"  same verdict           {ag['verdict_same']}/{ag['n']}")
    print(f"  right/not-right agree  {c[(True, True)] + c[(False, False)]}/{ag['n']}")
    k, n = c[(False, False)], c[(False, False)] + c[(False, True)]
    lo, hi = wilson(k, n)
    print(f"  problems caught        {k}/{n} hand-flagged cards also flagged ({lo:.0%}-{hi:.0%})"
          "  <- a miss here is a bad card waved through")
    print(f"  false alarms           {c[(True, False)]}/{c[(True, True)] + c[(True, False)]} right cards flagged")
    print(f"  wrong-field recall     {ag['field_recall']:.0%}   precision {ag['field_precision']:.0%}")
    print(f"  correct-value agree    {ag['gold_value_agree']:.0%} of fields")
    print("\nDisagreements (hand -> judge):")
    for r in ag["disagree"]:
        print(f"  {r[0]} {r[1]:5} {r[2]:>9} -> {r[3]:9} hand: {r[4]}\n{'':26}judge: {r[5]}")


def cmd_sample(a) -> None:
    from evals.sample import GROUP, draw
    from evals.score import load_gold
    deals = json.load(open(a.export))["deals"]
    gold_ids = {g["id"] for g in load_gold()}
    cards = [c for c in draw([d for d in deals if d["id"] not in gold_ids], a.seed)]
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"judge_sample_{a.seed}.jsonl"
    res = run(cards, deals, out, a.yes, lambda c: c.get("group") or GROUP.get(c.get("source_type")))
    if res:
        print(f"\nPre-labels in {out}. Next: publish them to the review page.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gold")
    g.add_argument("export")
    g.add_argument("--yes", action="store_true")
    g.add_argument("--limit", type=int)
    s = sub.add_parser("sample")
    s.add_argument("export")
    s.add_argument("--seed", type=int, required=True)
    s.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    {"gold": cmd_gold, "sample": cmd_sample}[a.cmd](a)


if __name__ == "__main__":
    main()
