"""
Scores verdicts against the world's known answers. The number that matters
is confident_wrong: an answer given without asking for a person, that is wrong.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from agent.simulator.world import Case


def grade(v: dict, case: Case) -> str:
    """right / abstain / flagged_wrong / confident_wrong"""
    cat, ccn, review = v.get("category"), v.get("ccn") or None, bool(v.get("needs_human_review"))
    ok = ccn in case.ccn_ok and cat in case.cat_ok and (review or not case.review_required)
    if ok:
        return "right"
    if cat == "unresolved":
        return "abstain"
    return "flagged_wrong" if review else "confident_wrong"


def summarize(results: list[tuple[Case, dict]]) -> dict:
    by_kind = defaultdict(Counter)
    for case, v in results:
        by_kind[case.kind][grade(v, case)] += 1
    total = sum(by_kind.values(), Counter())
    return {"total": dict(total), "by_kind": {k: dict(c) for k, c in sorted(by_kind.items())}}


def report(summary: dict) -> str:
    cols = ["right", "abstain", "flagged_wrong", "confident_wrong"]
    lines = [f"{'kind':<22}" + "".join(f"{c:>17}" for c in cols)]
    for kind, c in summary["by_kind"].items():
        lines.append(f"{kind:<22}" + "".join(f"{c.get(x, 0):>17}" for x in cols))
    t = summary["total"]
    lines.append(f"{'TOTAL':<22}" + "".join(f"{t.get(x, 0):>17}" for x in cols))
    return "\n".join(lines)
