"""
Score the frozen holdout. No model calls: everything comes from saved traces.

  venv/bin/python3 -m agent.holdout_eval [--cost-ratio 10]

Systems, all on the same labeled filings:
  matcher, name_sim,      agent/traces/holdout/baselines.csv (agent.holdout_baselines)
  legal_exact
  agent                   run_full_a: the full setup (verify + audit)
  agent_repeat            run_full_b: the same setup run again (run-to-run noise)
  no_audit                run_full_a traces re-verified without the audit (offline: the audit
                          runs after the agent answers, so the agent's path is unchanged)
  no_link_rule            run_full_a traces re-verified with the propco/opco rule off, audit
                          answer re-applied (offline, same reason)
  no_chow                 run_nochow: search_chow_records and its prompt line removed

A filing labeled NONE is right when the system names no facility.
Wrong-answer rate = named a facility, and it's the wrong one (or the label is NONE).
Writes agent/traces/holdout/per_filing.csv and results.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from unittest import mock

import agent.verify as verify_mod
from agent.audit import apply_audit
from agent.score import norm_ccn
from agent.trace import Step, Trace

H = Path("agent/traces/holdout")


def wilson(k: int, n: int, z: float = 1.96):
    if not n:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 3), round(c + h, 3))


def mcnemar_exact(a: list, b: list):
    """Two-sided exact McNemar on paired right/wrong lists: only filings where
    the two systems disagree count."""
    b01 = sum(1 for x, y in zip(a, b) if x and not y)
    b10 = sum(1 for x, y in zip(a, b) if y and not x)
    n = b01 + b10
    if not n:
        return {"a_only": 0, "b_only": 0, "p": 1.0}
    k = min(b01, b10)
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
    return {"a_only": b01, "b_only": b10, "p": round(p, 4)}


def load_trace(path: Path) -> Trace:
    d = json.loads(path.read_text())
    t = Trace(filing=d["filing"], model=d["model"])
    t.steps = [Step(**s) for s in d["steps"]]
    t.verdict, t.raw_verdict, t.audit_answer = d.get("verdict"), d.get("raw_verdict"), d.get("audit_answer")
    t.usage = d.get("usage") or t.usage
    return t


def trace_path(run: str, r: dict) -> Path:
    import re
    return H / run / (re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{r['state']}_{r['filing_number']}") + ".json")


def reverify(t: Trace, link_rule: bool, audit: bool) -> dict:
    raw = dict(t.raw_verdict)
    if link_rule:
        v = verify_mod.verify_verdict(raw, t)
    else:
        with mock.patch.object(verify_mod, "propco_opco_pair", lambda *a, **k: False):
            v = verify_mod.verify_verdict(raw, t)
    if audit and "audit" in (t.verdict or {}):      # the audit ran (or failed) on this filing
        v = apply_audit(v, t.audit_answer)
    return v


def candidates(t: Trace) -> set:
    """Every CCN any tool result showed the agent."""
    found = set()

    def walk(x):
        if isinstance(x, dict):
            for k, val in x.items():
                if k == "ccn" and val:
                    found.add(norm_ccn(val))
                walk(val)
        elif isinstance(x, list):
            for val in x:
                walk(val)
    for s in t.tool_steps():
        walk(s.output)
    return found


def summarize(name, rows, key):
    n = len(rows)
    right = [r[f"{key}_right"] for r in rows]
    answered = [bool(r[f"{key}_ccn"]) for r in rows]
    wrong_ans = [a and not c for a, c in zip(answered, right)]
    out = {"system": name, "n": n, "right": sum(right), "accuracy": round(sum(right) / n, 3),
           "accuracy_ci": wilson(sum(right), n), "coverage": round(sum(answered) / n, 3),
           "wrong_answers": sum(wrong_ans), "wrong_rate": round(sum(wrong_ans) / n, 3),
           "wrong_ci": wilson(sum(wrong_ans), n),
           "precision": round(sum(c for a, c in zip(answered, right) if a) / sum(answered), 3) if any(answered) else None}
    if f"{key}_accepted" in rows[0]:
        acc = [r for r in rows if r[f"{key}_accepted"]]
        out["accepted_without_review"] = len(acc)
        out["accepted_right"] = sum(r[f"{key}_right"] for r in acc)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cost-ratio", type=float, default=10.0,
                    help="cost of one wrong answer published without review, in units of one manual review")
    args = ap.parse_args(argv)

    labels = [r for r in csv.DictReader((H / "holdout.csv").open(newline="")) if r["true_ccn"].strip()]
    base = {r["draw"]: r for r in csv.DictReader((H / "baselines.csv").open(newline=""))}
    rows = []
    for lab in labels:
        truth = norm_ccn(lab["true_ccn"])
        row = {"draw": lab["draw"], "state": lab["state"], "filing_number": lab["filing_number"],
               "debtor": lab["debtor_name"][:60], "lender_group": lab["lender_group"], "truth": truth}

        def put(key, ccn, verdict=None):
            c = norm_ccn(ccn)
            row[f"{key}_ccn"] = c
            row[f"{key}_right"] = (c == "") if truth == "NONE" else (c == truth)
            if verdict is not None:
                ver = verdict.get("verification") or {}
                row[f"{key}_conf"] = verdict.get("confidence")
                row[f"{key}_category"] = verdict.get("category")
                row[f"{key}_accepted"] = bool(ver.get("passed")) and not verdict.get("needs_human_review")
        put("matcher", base[lab["draw"]]["matcher_ccn"])
        put("name_sim", base[lab["draw"]]["name_sim_ccn"])
        put("legal_exact", base[lab["draw"]]["legal_exact_ccn"])

        ta = load_trace(trace_path("run_full_a", lab))
        put("agent", ta.verdict.get("ccn"), ta.verdict)
        put("no_audit", (v := reverify(ta, link_rule=True, audit=False)).get("ccn"), v)
        put("no_link_rule", (v := reverify(ta, link_rule=False, audit=True)).get("ccn"), v)
        # sanity: re-verifying with everything on must reproduce the saved verdict
        same = reverify(ta, link_rule=True, audit=True)
        row["reverify_matches_saved"] = (norm_ccn(same.get("ccn")) == row["agent_ccn"]
                                         and same.get("needs_human_review") == ta.verdict.get("needs_human_review"))
        tb = load_trace(trace_path("run_full_b", lab))
        put("agent_repeat", tb.verdict.get("ccn"), tb.verdict)
        tn = load_trace(trace_path("run_nochow", lab))
        put("no_chow", tn.verdict.get("ccn"), tn.verdict)

        cand = candidates(ta)
        row["proposed_ccn"] = norm_ccn((ta.raw_verdict or {}).get("ccn"))
        row["truth_in_candidates"] = (truth in cand) if truth != "NONE" else None
        row["n_candidates"] = len(cand)
        row["tool_calls"] = len(ta.tool_steps())
        row["tools_used"] = " ".join(s.name for s in ta.tool_steps())
        row["issues"] = " | ".join((ta.verdict.get("verification") or {}).get("issues") or [])
        row["tokens_in"], row["tokens_out"] = ta.usage["input_tokens"], ta.usage["output_tokens"]
        rows.append(row)

    systems = [("existing matcher", "matcher"), ("name similarity >= 0.5", "name_sim"),
               ("exact CMS legal name", "legal_exact"), ("agent (full)", "agent"),
               ("agent, repeat run", "agent_repeat"), ("agent, audit off", "no_audit"),
               ("agent, link rule off", "no_link_rule"), ("agent, CHOW tool off", "no_chow")]
    res = {"n_labeled": len(rows), "systems": [summarize(n, rows, k) for n, k in systems]}
    col = lambda k: [r[f"{k}_right"] for r in rows]
    res["paired"] = {f"agent vs {k}": mcnemar_exact(col("agent"), col(k))
                     for k in ("matcher", "name_sim", "legal_exact", "agent_repeat", "no_audit", "no_link_rule", "no_chow")}

    fac = [r for r in rows if r["truth"] != "NONE"]
    in_c = [r for r in fac if r["truth_in_candidates"]]
    res["error_split"] = {
        "facility_labels": len(fac),
        "candidate_recall": {"k": len(in_c), "n": len(fac), "ci": wilson(len(in_c), len(fac))},
        "ranking_given_found": {"k": sum(r["agent_right"] for r in in_c), "n": len(in_c),
                                "ci": wilson(sum(r["agent_right"] for r in in_c), len(in_c))},
        "proposed_right_given_found": sum(r["proposed_ccn"] == r["truth"] for r in in_c),
    }
    cal = {}
    for level in ("high", "medium", "low"):
        sel = [r for r in rows if r["agent_ccn"] and r["agent_conf"] == level]
        cal[level] = {"n": len(sel), "right": sum(r["agent_right"] for r in sel), "ci": wilson(sum(r["agent_right"] for r in sel), len(sel))}
    sel = [r for r in rows if not r["agent_ccn"]]
    cal["abstained"] = {"n": len(sel), "right": sum(r["agent_right"] for r in sel)}
    res["calibration"] = cal

    # Review policies: a filing is either published as-is or sent to a person (cost 1).
    # A person is assumed to fix it. A wrong answer published costs --cost-ratio.
    def policy(name, publish):
        pub = [r for r in rows if publish(r)]
        wrong_pub = sum(1 for r in pub if not r["agent_right"])
        reviewed = len(rows) - len(pub)
        return {"policy": name, "published": len(pub), "wrong_published": wrong_pub, "reviewed": reviewed,
                "cost_per_filing": round((reviewed + args.cost_ratio * wrong_pub) / len(rows), 3)}
    res["cost_ratio"] = args.cost_ratio
    res["policies"] = [
        policy("review everything", lambda r: False),
        policy("publish verified, no review flag (current)", lambda r: r["agent_accepted"]),
        policy("publish any high-confidence answer", lambda r: r["agent_conf"] == "high"),
        policy("publish high or medium", lambda r: r["agent_conf"] in ("high", "medium")),
        policy("publish every answer incl. abstentions", lambda r: True),
    ]
    res["reverify_reproduces_saved"] = sum(r["reverify_matches_saved"] for r in rows)
    res["tokens"] = {"in": sum(r["tokens_in"] for r in rows), "out": sum(r["tokens_out"] for r in rows)}

    with (H / "per_filing.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (H / "results.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
