"""
Score a run against labels. The point is not one accuracy number but
whether the agent's own signals can be trusted (session 2: a "90% confident"
bucket should be right about 90% of the time).

Input: the CSV agent.run writes (sample_summary.csv). A row is labeled when
`human_ccn` is filled in: a 6-character CCN, or NONE when the right answer
is "no nursing home facility". `label_source` says where a label came from:

  human            you filled it in (the real test; use this for demo numbers)
  pipeline_match   the existing matcher already tied this filing to a CMS
                   facility (agent.run --known). Easier than the cases the
                   agent exists for, so read it as a floor/regression check.

Reports, per label source:
  accuracy          right CCN (or right NONE) over labeled rows
  coverage          how often the agent named a facility at all
  precision         right, among rows where it named one
  by confidence     accuracy at high / medium / low  (calibration)
  accepted          rows that passed verification and needed no review: how many,
                    and how often right. This is the operating point that matters:
                    what the feed could publish without a person looking.
  confident-wrong   high confidence and wrong: the failure to hunt for
Tokens are reported, not dollars (prices change).

  venv/bin/python3 -m agent.score agent/traces/sample_summary.csv [--json out.json]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict


def norm_ccn(x) -> str:
    """6-char CCN. Spreadsheets drop leading zeros (056215 -> 56215), so pad numeric ones back."""
    t = str(x or "").strip().upper()
    if t.endswith(".0"):
        t = t[:-2]
    if t.isdigit() and len(t) < 6:
        t = t.zfill(6)
    return t[:6]


def _truthy(x) -> bool:
    return str(x).strip().lower() in ("true", "1", "yes", "y")


def is_correct(agent_ccn, label) -> bool:
    lab, got = norm_ccn(label), norm_ccn(agent_ccn)
    return (got == "") if lab == "NONE" else (got == lab)


def _rate(num: int, den: int):
    return round(num / den, 3) if den else None


def _summarize(rows: list) -> dict:
    n = len(rows)
    correct = [is_correct(r.get("ccn"), r["human_ccn"]) for r in rows]
    answered = [bool(norm_ccn(r.get("ccn"))) for r in rows]
    out = {
        "n": n,
        "accuracy": _rate(sum(correct), n),
        "coverage": _rate(sum(answered), n),
        "precision": _rate(sum(c for c, a in zip(correct, answered) if a), sum(answered)),
    }
    by_conf = {}
    for level in ("high", "medium", "low"):
        sel = [c for c, r, a in zip(correct, rows, answered) if a and (r.get("confidence") or "").lower() == level]
        by_conf[level] = {"n": len(sel), "accuracy": _rate(sum(sel), len(sel))}
    out["by_confidence"] = by_conf
    out["abstained"] = {"n": n - sum(answered), "share": _rate(n - sum(answered), n)}

    acc = [(c, r) for c, r in zip(correct, rows)
           if _truthy(r.get("verified")) and not _truthy(r.get("needs_review"))]
    out["accepted"] = {"n": len(acc), "share": _rate(len(acc), n), "accuracy": _rate(sum(c for c, _ in acc), len(acc))}
    out["confident_wrong"] = [
        {"state": r.get("state"), "filing_number": r.get("filing_number"), "agent_ccn": r.get("ccn"), "label": r["human_ccn"]}
        for c, r in zip(correct, rows) if not c and (r.get("confidence") or "").lower() == "high"
    ]
    out["needs_review_share"] = _rate(sum(_truthy(r.get("needs_review")) for r in rows), n)

    def avg(key):
        vals = [float(r[key]) for r in rows if str(r.get(key, "")).strip() != ""]
        return round(sum(vals) / len(vals), 1) if vals else None
    out["avg_tool_calls"], out["avg_input_tokens"], out["avg_output_tokens"] = avg("tool_calls"), avg("input_tokens"), avg("output_tokens")
    return out


def score_rows(rows: list) -> dict:
    labeled = [r for r in rows if (r.get("human_ccn") or "").strip()]
    groups = defaultdict(list)
    for r in labeled:
        groups[(r.get("label_source") or "human").strip() or "human"].append(r)
    return {"rows": len(rows), "labeled": len(labeled),
            "by_label_source": {src: _summarize(rs) for src, rs in sorted(groups.items())}}


def _pct(x) -> str:
    return "  n/a" if x is None else f"{x * 100:4.0f}%"


def render(result: dict) -> str:
    lines = [f"{result['labeled']} labeled of {result['rows']} rows"]
    if not result["labeled"]:
        lines.append("Fill in human_ccn (6-char CCN, or NONE) for some rows, then re-run.")
    for src, s in result["by_label_source"].items():
        lines += ["", f"== labels: {src}  (n={s['n']})",
                  f"  accuracy {_pct(s['accuracy'])}   coverage {_pct(s['coverage'])}   precision when it answers {_pct(s['precision'])}",
                  f"  abstained (named no facility): {s['abstained']['n']} ({_pct(s['abstained']['share'])})",
                  "  calibration, answered rows only (is 'high' really right more often than 'low'?):"]
        for level, v in s["by_confidence"].items():
            lines.append(f"    {level:<7} n={v['n']:<4} accuracy {_pct(v['accuracy'])}")
        a = s["accepted"]
        lines.append(f"  accepted without review: {a['n']} ({_pct(a['share'])} of rows), right {_pct(a['accuracy'])} of the time")
        lines.append(f"  sent to a person: {_pct(s['needs_review_share'])}")
        lines.append(f"  confident-wrong: {len(s['confident_wrong'])}")
        for cw in s["confident_wrong"][:10]:
            lines.append(f"    ! {cw['state']} {cw['filing_number']}: agent {cw['agent_ccn']} vs label {cw['label']}")
        lines.append(f"  avg per filing: {s['avg_tool_calls']} tool calls, {s['avg_input_tokens']} in / {s['avg_output_tokens']} out tokens")
        if src == "pipeline_match":
            lines.append("  note: these labels come from the existing matcher, so they are the easy cases.")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv_path")
    p.add_argument("--json", help="also write the result here")
    args = p.parse_args(argv)
    with open(args.csv_path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    result = score_rows(rows)
    print(render(result))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(result, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
