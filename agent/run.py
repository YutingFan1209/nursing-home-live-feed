"""
CLI for the resolver. Run from the project root with the project venv:

  venv/bin/python3 -m agent.run --selftest
  venv/bin/python3 -m agent.run --state PA --filing-number 20240001234
  venv/bin/python3 -m agent.run --sample 5 --state PA     # NEW_SIGNAL deals CMS never matched (label by hand)
  venv/bin/python3 -m agent.run --known 20 --state PA     # filings the pipeline already matched (auto-labeled, easy)
  venv/bin/python3 -m agent.score agent/traces/sample_summary.csv

Each run prints the trace and saves it to agent/traces/<state>_<filing>.json
(the file the demo UI replays). Uses DATABASE_URL and ANTHROPIC_API_KEY from .env.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from agent.loop import resolve_filing
from agent.tools import ToolBox


def load_filing(conn, state: str, filing_number: str) -> dict:
    from psycopg2.extras import RealDictCursor
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT state, filing_number, debtor_name, secured_party, filing_date, filing_type,
                   collateral_description, status, detail_url
            FROM ucc_filings WHERE state = %s AND filing_number = %s
        """, (state, filing_number))
        row = cur.fetchone()
    if not row:
        sys.exit(f"No ucc_filings row for {state} {filing_number}")
    return dict(row)


def sample_filings(conn, n: int, state: str | None) -> list:
    """Filings whose UCC-only deal the existing pipeline never tied to CMS."""
    from psycopg2.extras import RealDictCursor
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT uf.state, uf.filing_number, uf.debtor_name, uf.secured_party, uf.filing_date,
                   uf.filing_type, uf.collateral_description, uf.status, uf.detail_url
            FROM deals d
            JOIN ucc_filings uf ON uf.state = d.states[1]
                               AND uf.filing_date = d.acquisition_date::text
                               AND upper(uf.debtor_name) = upper(d.operator_names[1])
            WHERE d.extraction_model = 'ucc_filing' AND d.stage <> 'dismissed'
              AND NOT EXISTS (SELECT 1 FROM cms_matches m WHERE m.deal_id = d.id)
              AND (%(st)s::text IS NULL OR uf.state = %(st)s)
            ORDER BY random() LIMIT %(n)s
        """, {"st": state, "n": n})
        return [dict(r) for r in cur.fetchall()]


def known_filings(conn, n: int, state: str | None) -> list:
    """Filings the existing matcher already tied to exactly one CMS facility
    (score at or above the pipeline's UCC threshold). Returns (filing, ccn).
    The CCN is the label; it is kept out of the filing the agent sees."""
    from psycopg2.extras import RealDictCursor
    from config import get_config
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT uf.state, uf.filing_number, uf.debtor_name, uf.secured_party, uf.filing_date,
                   uf.filing_type, uf.collateral_description, uf.status, uf.detail_url,
                   MIN(m.ccn) AS known_ccn
            FROM deals d
            JOIN ucc_filings uf ON uf.state = d.states[1]
                               AND uf.filing_date = d.acquisition_date::text
                               AND upper(uf.debtor_name) = upper(d.operator_names[1])
            JOIN cms_matches m ON m.deal_id = d.id AND m.ccn IS NOT NULL AND m.match_score >= %(min)s
            WHERE d.extraction_model = 'ucc_filing' AND d.stage <> 'dismissed'
              AND (%(st)s::text IS NULL OR uf.state = %(st)s)
            GROUP BY uf.id, uf.state, uf.filing_number, uf.debtor_name, uf.secured_party, uf.filing_date,
                     uf.filing_type, uf.collateral_description, uf.status, uf.detail_url
            HAVING COUNT(DISTINCT m.ccn) = 1
            ORDER BY random() LIMIT %(n)s
        """, {"st": state, "n": n, "min": get_config().ucc_min_match_score})
        rows = [dict(r) for r in cur.fetchall()]
    return [({k: v for k, v in r.items() if k != "known_ccn"}, r["known_ccn"]) for r in rows]


def selftest(tb: ToolBox) -> int:
    """Run every tool once with a harmless query. Prints OK/FAIL per tool."""
    fails = 0
    ccn = None

    def check(label, fn):
        nonlocal fails
        try:
            out = fn()
            size = {k: len(v) for k, v in out.items() if isinstance(v, list)} or "ok"
            print(f"  OK    {label}: {size}")
            return out
        except Exception as e:
            fails += 1
            print(f"  FAIL  {label}: {type(e).__name__}: {e}")

    print("Tool self-test (read-only):")
    fac = check("search_cms_facilities", lambda: tb.call("search_cms_facilities", {"name": "Manor", "state": "NY"}))
    if fac and fac["matches"]:
        ccn = fac["matches"][0]["ccn"]

        def recall():
            # a two-word prefix of a real facility name must find that facility
            core = " ".join(fac["matches"][0]["provider_name"].split()[:2])
            got = tb.call("search_cms_facilities", {"name": core, "state": fac["matches"][0]["provider_state"]})
            assert ccn in [m["ccn"] for m in got["matches"]], f"{core!r} did not find CCN {ccn}"
            return got
        check("search recall (2-word prefix of a real name)", recall)
    check("search_cms_owners", lambda: tb.call("search_cms_owners", {"owner_name": "Healthcare", "state": "NY"}))
    check("search_chow_records", lambda: tb.call("search_chow_records", {"name": "Healthcare", "state": "NY"}))
    if fac and fac["matches"]:
        def by_address():
            # a real facility's own street address must find it
            row = tb.call("get_facility_ownership", {"ccn": ccn})["facility"]
            addr = tb._rows("SELECT provider_address FROM cms_facilities WHERE ccn = %s", (ccn,))[0]["provider_address"]
            got = tb.call("search_cms_by_address", {"address": addr, "state": row["provider_state"]})
            assert ccn in [m["ccn"] for m in got["matches"]], f"{addr!r} did not find CCN {ccn}"
            return got
        check("search_cms_by_address (a real facility's address)", by_address)
    if ccn:
        check("get_facility_ownership (no date)", lambda: tb.call("get_facility_ownership", {"ccn": ccn}))
        check("get_facility_ownership (timing)", lambda: tb.call("get_facility_ownership", {"ccn": ccn, "filing_date": "2025-06-01"}))
    else:
        print("  SKIP  get_facility_ownership (no CCN found)")
    check("get_related_ucc_filings", lambda: tb.call("get_related_ucc_filings", {"debtor_name": "Nursing Home LLC"}))
    check("search_deals", lambda: tb.call("search_deals", {"name": "Healthcare"}))
    check("classify_lender", lambda: tb.call("classify_lender", {"name": "Greystone Funding Company LLC"}))
    print("FAILED" if fails else "All tools OK")
    return 1 if fails else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--state")
    p.add_argument("--filing-number")
    p.add_argument("--sample", type=int, help="run on N random unresolved UCC-only deals")
    p.add_argument("--known", type=int, help="run on N filings the pipeline already matched to a facility (labels auto-filled; the easy cases)")
    p.add_argument("--max-steps", type=int, default=8)
    p.add_argument("--audit", action="store_true", help="add the independent audit call (agent/audit.py)")
    p.add_argument("--model")
    p.add_argument("--out", default="agent/traces")
    args = p.parse_args(argv)

    tb = ToolBox.from_config()
    if args.selftest:
        return selftest(tb)

    labels = {}   # (state, filing_number) -> (ccn, source)
    if args.known:
        pairs = known_filings(tb.conn, args.known, args.state)
        if not pairs:
            sys.exit("No pipeline-matched UCC filings found for that filter")
        filings = [f for f, _ in pairs]
        labels = {(f["state"], f["filing_number"]): (ccn, "pipeline_match") for f, ccn in pairs}
    elif args.sample:
        filings = sample_filings(tb.conn, args.sample, args.state)
        if not filings:
            sys.exit("No unresolved UCC-only deals found for that filter")
    elif args.state and args.filing_number:
        filings = [load_filing(tb.conn, args.state, args.filing_number)]
    else:
        p.error("give --selftest, --known N, --sample N, or --state and --filing-number")

    rows = []
    for f in filings:
        verdict, trace = resolve_filing(f, tb, model=args.model, max_steps=args.max_steps, audit=args.audit)
        path = trace.save(args.out)
        print(trace.render_text(), f"\n  saved: {path}\n" + "-" * 70)
        rows.append({
            "state": f["state"], "filing_number": f["filing_number"], "debtor": f["debtor_name"],
            "secured_party": f["secured_party"], "category": verdict.get("category"),
            "facility": verdict.get("facility_name"), "ccn": verdict.get("ccn"),
            "owner": verdict.get("owner_entity"), "confidence": verdict.get("confidence"),
            "needs_review": verdict.get("needs_human_review"),
            "verified": (verdict.get("verification") or {}).get("passed"),
            "model_calls": trace.usage["model_calls"], "tool_calls": len(trace.tool_steps()),
            "input_tokens": trace.usage["input_tokens"], "output_tokens": trace.usage["output_tokens"],
            # --known fills these from the pipeline's own match; otherwise fill human_ccn by hand
            # (6-char CCN, or NONE) and set label_source to human
            "human_ccn": labels.get((f["state"], f["filing_number"]), ("", ""))[0],
            "label_source": labels.get((f["state"], f["filing_number"]), ("", "human"))[1],
        })
    if len(rows) > 1 or labels:
        out = Path(args.out) / "sample_summary.csv"
        with out.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"summary: {out}\nscore it:  venv/bin/python3 -m agent.score {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
