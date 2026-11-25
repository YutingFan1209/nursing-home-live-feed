"""
Claude pre-labels site cards for the monthly audit; a person confirms.

The judge reads one card at a time with the evidence a human labeller used
(the source text, CMS facility and owner rows, CHOW records for its homes,
and other site cards that may be the same deal) and returns a label in the
gold-set format (evals/gold/labels.jsonl).

  gold     Check the judge against the 150 hand labels. Run once, and again
           after any change to the prompt or model; its agreement is what
           says how far a pre-label can be trusted.
           venv/bin/python3 -m evals.judge gold <export.json> [--yes] [--limit N] [--only IDS] [--batch]
           While tuning, --only the cards a change can affect; one full run at the end.

  sample   Pre-label a fresh sample for review (never the gold cards).
           venv/bin/python3 -m evals.judge sample <export.json> --seed 20261221 [--yes] [--batch]

--batch sends the requests through the Message Batches API at half price;
results usually take under an hour. Not for the deploy-time check.

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.field_rules import FIELD_RULES, SITE_RULES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "evals"
MODEL = "claude-opus-5-5"
PRICE_IN, PRICE_OUT = 4.00, 20.00          # $ per million tokens, Opus 5.5
PRICE_CACHE_READ, PRICE_CACHE_WRITE = 0.20, 5.00   # batch: half of all four
# The estimate, calibrated on the five full gold runs of 2026-11-16/17
# (4.90M input / 0.52M output tokens over 749 cards): input is ~3.6x the old
# chars/3.5 guess (evidence is JSON, plus the schema), output ~700 tokens a
# card, not 2,500. The old estimate said ~290k input for runs that used ~1.1M.
# Fitted to those runs: the ~2.1k-token instructions count like prose (4
# chars a token); the per-card evidence, mostly JSON and ids, ~0.78 tokens a char.
SYSTEM_TOKENS_PER_CHAR = 1 / 4
CARD_TOKENS_PER_CHAR = 0.78
OUT_PER_CARD = 700
SOURCE_CHARS = 15000

SHOWN = ["deal_type", "buyer", "seller", "facility_names", "ccns", "states", "facility_count",
         "acquisition_date", "deal_value_m", "financing_amount_m", "lender", "status", "assumed_closed", "date_basis", "facility_type",
         "headline", "summary", "source_type", "source_url", "source_title", "published_at"]
FIELDS = ["deal_type", "buyer", "seller", "facility_count", "states", "acquisition_date",
          "deal_value_m", "financing_amount_m", "lender", "status", "facility_type", "ccns"]
VERDICTS = ["right", "wrong", "minor", "duplicate", "noise", "uncertain"]

SYSTEM = """You audit cards on a public feed of US ownership changes at nursing homes, assisted \
living / memory care communities, hospices and inpatient rehabilitation hospitals. Each card was \
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
homes, same buyer side, closing within a few weeks), or one whose homes include all of this card's \
(a portfolio card and a card for part of it). Name that card's id in the note.
- noise: true, but not a deal the feed covers: an equipment vendor's or landlord's lien shown as a loan, \
a consumer loan, a merchant cash advance, a non-US business, or a business outside the feed's care \
types (home health only, outpatient therapy, a general hospital, a physician group). Hospices and \
inpatient rehabilitation hospitals are in scope.
- uncertain: only when the evidence can't tell what kind of deal this is, or whether it happened \
at all (a nonprofit parent added as an owner, a landlord or REIT as secured party on its tenant's \
filing, a seller that may be the buyer's own company). Not uncertain: a field the source simply \
doesn't state (judge the fields shown), or a doubt you can settle from the evidence given.

Field rules (the same rules the extractor follows; a card is right when it meets them;\
acquiring_entity is the card's buyer, seller_entity its seller, rationale its summary):
""" + FIELD_RULES + """

Site conventions (applied when the site is built; these are right, not errors):
""" + SITE_RULES + """
- The home's own operating company is not a lender, and neither is its landlord or property owner (a \
real-estate company, or a company in the CMS OWNER rows): a card showing one as lender is wrong.
- Check ccns against the CMS FACILITY rows given: a CCN for a home the source doesn't name is wrong \
(a skilled nursing CCN on an independent or assisted living community), and so is a card naming more \
homes than its ccns cover when the source names them. A UCC card's ccns need the debtor to be that \
home's operator or owner in the CMS rows; when nothing given links the debtor to the home, uncertain.
- Check facility_type against the source: a community it calls independent living, assisted living, \
memory care or behavioral health shown as skilled_nursing is wrong.
- Party fields follow the field rules above for every deal type: a tenant or incoming operator that \
leases or runs the homes is not the buyer, and a landlord that keeps the buildings is not the seller, \
so a lease or operations-transfer card naming them as buyer or seller is wrong.
- A sale shown as reported, or assumed closed, while the evidence or a card under OTHER CARDS says it \
is still pending after that date, has the wrong status.
- Two UCC cards for the same home and the same secured party filed within a few weeks are one loan: \
the one being judged is a duplicate of the other.
- Your verdict must agree with your note: if the note says a shown field is contradicted by, or \
absent from, the evidence, the card is wrong (minor for a formatting problem), not right.

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


_FAC_WORDS = re.compile(r"\b(and|of|the|at|post acute|rehabilitation|rehab|nursing|center|centre|"
                        r"healthcare|health|care|skilled|snf|dba|operations|llc|inc)\b")


def _fac(name) -> str:
    """A home's name without the words every home shares, and without the
    "X Operations LLC DBA" prefix a licence list puts before it."""
    s = _norm(name)
    s = s.split(" dba ", 1)[-1]
    s = " ".join(_FAC_WORDS.sub(" ", s).split())
    return s if len(s) >= 5 else ""


def _brand(buyer) -> str:
    """The buyer name squeezed together, for prefix matching ("advinia care" ->
    "adviniacare"); empty when too short to mean anything."""
    s = _norm(buyer).replace(" ", "")
    return s if len(s) >= 6 else ""


def neighbours(card: dict, deals: list[dict], k: int = 8) -> list[dict]:
    """Other cards that may be the same deal: a shared home, the same buyer
    or seller, or the same state and home count within 90 days."""
    ccns, b, s = set(card.get("ccns") or []), _norm(card.get("buyer")), _norm(card.get("seller"))
    st = set(card.get("states") or [])
    lender = _norm(card.get("lender"))
    fac = {_fac(n) for n in card.get("facility_names") or []} - {""}
    brand = _brand(card.get("buyer"))

    def when(x):
        return str(x.get("acquisition_date") or x.get("published_at") or x.get("created_at") or "")[:10]
    d0 = when(card)

    def money(x):
        return x.get("deal_value_m") or x.get("financing_amount_m")

    def close(a, b):
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            return False
        return abs(a - b) <= 0.05 * max(a, b)

    def days(x):
        try:
            return abs((date.fromisoformat(str(x)[:10]) - date.fromisoformat(d0)).days)
        except ValueError:
            return 10 ** 6

    out = []
    for d in deals:
        if d["id"] == card["id"] or d.get("individual_filing"):
            continue
        # already shown on one card with this one: not a separate duplicate
        lead = card.get("grouped_into") or card["id"]
        if (d.get("grouped_into") or d["id"]) == lead:
            continue
        score = 3 * bool(ccns & set(d.get("ccns") or []))
        score += 2 * bool(b and _norm(d.get("buyer")) == b) + 2 * bool(s and _norm(d.get("seller")) == s)
        shared = st & set(d.get("states") or [])
        gap = days(when(d))
        # a missing closing date is unknown, not far apart (Ensign's undated CO copy)
        undated = not (card.get("acquisition_date") and d.get("acquisition_date"))
        score += bool(shared and d.get("facility_count") is not None
                      and d.get("facility_count") == card.get("facility_count") and (gap <= 90 or undated))
        # the same lender in the same state: buyerless loan stories (MONTICELLOAM's $195.7M twice)
        score += 2 * bool(lender and shared and _norm(d.get("lender")) == lender)
        score += bool(close(money(card), money(d)))
        # same state, same closing date: a seller-side card and a buyer-side one (Saber OH)
        score += bool(shared and card.get("acquisition_date") and gap == 0)
        # a home this card names is on the other card: a licence-list row inside a
        # news portfolio card (Bay Park in Ensign's 8 Florida homes, holdout 2026-11-19)
        score += 3 * bool(shared and fac & {_fac(n) for n in d.get("facility_names") or []} - {""})
        # the same brand buying in the same state: "AdviniaCare Respiratory at
        # Coventry, LLC" on a state filing vs "Advinia Care" in the news
        ob = _brand(d.get("buyer"))
        score += 2 * bool(shared and brand and ob and (brand.startswith(ob) or ob.startswith(brand)))
        if score >= 2:
            out.append((score, len(shared), -gap, d))
    # ties, e.g. a buyer with many cards: more shared states, then closer dates first (LTC)
    out.sort(key=lambda x: x[:3], reverse=True)
    return [{k2: d.get(k2) for k2 in ("id", "source_type", "deal_type", "buyer", "seller", "states",
                                       "facility_count", "facility_names", "acquisition_date", "status",
                                       "deal_value_m", "headline")}
            for *_, d in out[:k]]


_CHOW: dict | None = None


def _chow_by_ccn() -> dict:
    """CHOW file rows by CCN (buyer or seller side), read once."""
    global _CHOW
    if _CHOW is None:
        from scraper.chow import load_chow_rows
        _CHOW = {}
        for r in load_chow_rows(download=False):
            for c in {(r.get("CCN - BUYER") or "").strip(), (r.get("CCN - SELLER") or "").strip()} - {""}:
                _CHOW.setdefault(c, []).append(r)
    return _CHOW


def evidence(card: dict, deals: list[dict], cur) -> str:
    cur.execute("""select a.url, a.title, a.published_at, a.raw_text from deals d
                   left join articles a on a.id = d.article_id where d.id = %s""", (card["id"],))
    url, title, pub, text = cur.fetchone() or (None, None, None, None)
    parts = [f"SOURCE {url}\nTITLE {title}\nPUBLISHED {pub}\n{(text or '(no stored text)')[:SOURCE_CHARS]}"]
    # records merged into this card are part of it (Cliveden + Tucker House: two notices, one sale)
    cur.execute("""select a.url, a.raw_text from deals d join articles a on a.id = d.article_id
                   where d.merged_into = %s and a.url <> %s limit 3""", (card["id"], url or ""))
    parts += [f"ALSO MERGED INTO THIS CARD: {u}\n{(t or '(no stored text)')[:SOURCE_CHARS // 3]}"
              for u, t in cur.fetchall()]
    for ccn in (card.get("ccns") or [])[:6]:
        cur.execute("""select provider_name, provider_city, provider_state, legal_business_name
                       from cms_facilities where ccn = %s""", (ccn,))
        f = cur.fetchone()
        parts.append(f"CMS FACILITY {ccn}: " + (" | ".join(str(x) for x in f) if f else "not in CMS file"))
        cur.execute("""select owner_name, owner_role, ownership_percentage, ownership_start_date
                       from cms_ownership_records where ccn = %s
                       order by ownership_start_date desc nulls last limit 20""", (ccn,))
        parts += [f"  CMS OWNER {r[0]} | {r[1]} | {r[2]}% | since {r[3]}" for r in cur.fetchall()]
        # buyer and seller from the CHOW file itself: chow_seen_records keeps no
        # seller, so CHOW-filled sellers looked unsupported (adjudication 2026-11-19)
        rows = _chow_by_ccn().get(ccn, [])
        parts += [f"  CMS CHOW effective {r['EFFECTIVE DATE']}: buyer {r['ORGANIZATION NAME - BUYER']}"
                  f" | seller {r['ORGANIZATION NAME - SELLER']}" for r in rows]
        if not rows:
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


def run(cards: list[dict], deals: list[dict], out_path: Path, yes: bool, group_of,
        batch: bool = False) -> list[dict]:
    """batch: through the Message Batches API at half price (up to ~1 h; never
    for the deploy-time check, which someone is waiting on)."""
    import psycopg2
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    db = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = db.cursor()
    texts = [(c, prompt(c, evidence(c, deals, cur))) for c in cards]
    sys_tok = len(SYSTEM) * SYSTEM_TOKENS_PER_CHAR
    tok_in = sum(len(t) for _, t in texts) * CARD_TOKENS_PER_CHAR     # per-card part, never cached
    est = (tok_in * PRICE_IN + len(texts) * sys_tok * PRICE_CACHE_READ + sys_tok * PRICE_CACHE_WRITE
           + len(texts) * OUT_PER_CARD * PRICE_OUT) / 1e6
    if batch:
        est /= 2
    print(f"{len(texts)} cards, ~{(tok_in + len(texts) * sys_tok) / 1e3:.0f}k input tokens, estimated ${est:.2f}"
          f"{' at batch price' if batch else ''} ({MODEL}, effort high)")
    if not yes:
        print("Not run. Add --yes to spend it.")
        return []

    import anthropic
    client = anthropic.Anthropic(max_retries=4)
    done = {}
    if out_path.exists():                      # resume an interrupted run
        done = {json.loads(l)["id"]: json.loads(l) for l in open(out_path) if l.strip()}
    lock, spent = threading.Lock(), {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0}

    def add(u):
        spent["input"] += u.input_tokens or 0
        spent["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        spent["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        spent["output"] += u.output_tokens or 0

    def save(c, lab):
        rec = {"id": c["id"], "group": group_of(c), "source_type": c.get("source_type"), **lab}
        with open(out_path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        return rec

    def one(item):
        c, t = item
        if c["id"] in done:
            return done[c["id"]]
        try:
            lab, u = call(client, t)
        except Exception as e:                 # keep going; the card stays unlabelled
            print(f"  {c['id'][:8]} failed: {e}", file=sys.stderr)
            return None
        with lock:
            add(u)
            rec = save(c, lab)
        print(f"  {c['id'][:8]} {lab['verdict']}", file=sys.stderr)
        return rec

    if batch:
        res = _run_batch(client, texts, done, out_path, add, save)
    else:
        with ThreadPoolExecutor(max_workers=6) as ex:
            res = [r for r in ex.map(one, texts) if r]
    cost = (spent["input"] * PRICE_IN + spent["cache_read"] * PRICE_CACHE_READ
            + spent["cache_write"] * PRICE_CACHE_WRITE + spent["output"] * PRICE_OUT) / 1e6 / (2 if batch else 1)
    print(f"labelled {len(res)}/{len(texts)}; this run used {spent['input'] / 1e3:.0f}k input, "
          f"{spent['cache_read'] / 1e3:.0f}k cache-read, {spent['cache_write'] / 1e3:.0f}k cache-write, "
          f"{spent['output'] / 1e3:.0f}k output tokens = ${cost:.2f} (measured{', batch price' if batch else ''})")
    return res


def request(text: str) -> dict:
    """call()'s request for a Message Batches request (batches take no fallbacks)."""
    return dict(model=MODEL, max_tokens=16000,
                system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": text}])


def _run_batch(client, texts, done, out_path, add, save) -> list[dict]:
    from evals.runs import run_batch
    todo = {c["id"]: (c, t) for c, t in texts if c["id"] not in done}
    msgs = run_batch(client, {cid: request(t) for cid, (_, t) in todo.items()}, out_path.with_suffix(".batch"))
    res = [done[c["id"]] for c, _ in texts if c["id"] in done]
    for cid, m in msgs.items():
        add(m.usage)
        if m.stop_reason == "refusal":
            print(f"  {cid[:8]} refused", file=sys.stderr)
            continue
        res.append(save(todo[cid][0], json.loads(next(b.text for b in m.content if b.type == "text"))))
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
    if a.only:
        from evals.runs import pick, read_ids
        cards = pick(cards, read_ids(a.only))
    OUT.mkdir(parents=True, exist_ok=True)
    # The prompt's fingerprint in the name: a run resumes from its file, and a
    # same-day run after a prompt change must not reuse the old answers
    from evals import fingerprint
    part = "_only" if (a.only or a.limit) else ""
    out = OUT / f"judge_gold_{date.today()}_{fingerprint('judge')[:8]}{part}.jsonl"
    if part and out.exists() and not a.yes:
        print(f"(a partial run for this prompt already exists: {out.name}; its cards are reused)")
    res = run(cards, deals, out, a.yes, lambda c: groups[c["id"]], batch=a.batch)
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
    if not a.limit and not a.only and len(res) == len(cards):   # a full run measures this prompt
        from evals import record_measurement
        record_measurement("judge", f"caught {k}/{n}, false alarms {c[(True, False)]}/"
                           f"{c[(True, True)] + c[(True, False)]}", out.name)
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
    res = run(cards, deals, out, a.yes, lambda c: c.get("group") or GROUP.get(c.get("source_type")),
              batch=a.batch)
    if res:
        print(f"\nPre-labels in {out}. Next: publish them to the review page.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gold")
    g.add_argument("export")
    g.add_argument("--yes", action="store_true")
    g.add_argument("--limit", type=int)
    g.add_argument("--only", help="file of card ids (or 8-char prefixes): judge just these while tuning; "
                                  "a full run at the end records the measurement")
    g.add_argument("--batch", action="store_true", help="Message Batches API: half price, up to ~1 h")
    s = sub.add_parser("sample")
    s.add_argument("export")
    s.add_argument("--seed", type=int, required=True)
    s.add_argument("--yes", action="store_true")
    s.add_argument("--batch", action="store_true", help="Message Batches API: half price, up to ~1 h")
    a = ap.parse_args()
    {"gold": cmd_gold, "sample": cmd_sample}[a.cmd](a)


if __name__ == "__main__":
    main()
