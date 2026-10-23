"""
Baselines on the frozen holdout, no model calls. Read-only.

  venv/bin/python3 -m agent.holdout_baselines

  matcher     the pipeline's existing CMS matcher: whatever cms_matches holds for
              the filing's deal (the holdout was drawn from deals it left unmatched,
              so this is checked, not assumed)
  name_sim    best pg_trgm similarity of the debtor name to a CMS provider name or
              legal business name in the filing's state; answer if >= 0.5, else
              abstain. Threshold fixed before any result was seen; never tuned.
  legal_exact the debtor equals the CMS legal business name of exactly one
              facility in the state, after dropping punctuation and LLC/Inc-type
              suffixes. The matcher never reads legal names, so this is the
              simplest fix to it (added 2026-10-22, after the agent was scored).

Writes agent/traces/holdout/baselines.csv (draw, matcher_ccn, name_sim_ccn, name_sim_score, legal_exact_ccn).
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from agent.tools import ToolBox

HOLDOUT = Path("agent/traces/holdout/holdout.csv")
NAME_SIM_THRESHOLD = 0.5


def legal_key(name: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper())
    s = re.sub(r"\b(L L C|LLC|INC|LP|L P|CORP|CORPORATION|CO|LTD|THE)\b", " ", s)
    return " ".join(s.split())


def main() -> int:
    tb = ToolBox.from_config()
    rows = [r for r in csv.DictReader(HOLDOUT.open(newline="")) if r["true_ccn"].strip()]
    legal = {}
    for f in tb._rows("SELECT ccn, provider_state, legal_business_name FROM cms_facilities "
                      "WHERE legal_business_name IS NOT NULL", ()):
        legal.setdefault((f["provider_state"], legal_key(f["legal_business_name"])), set()).add(f["ccn"])
    out = []
    for r in rows:
        m = tb._rows("""
            SELECT DISTINCT m.ccn FROM deals d
            JOIN cms_matches m ON m.deal_id = d.id AND m.ccn IS NOT NULL
            WHERE d.extraction_model = 'ucc_filing' AND d.states[1] = %s
              AND d.acquisition_date::text = %s AND upper(d.operator_names[1]) = upper(%s)
        """, (r["state"], r["filing_date"], r["debtor_name"]))
        s = tb._rows("""
            SELECT ccn, GREATEST(similarity(upper(provider_name), upper(%(n)s)),
                                 similarity(upper(coalesce(legal_business_name, '')), upper(%(n)s))) AS sim
            FROM cms_facilities WHERE provider_state = %(st)s
            ORDER BY sim DESC, ccn LIMIT 1
        """, {"n": r["debtor_name"], "st": r["state"]})
        best = s[0] if s else {"ccn": None, "sim": 0}
        out.append({"draw": r["draw"], "matcher_ccn": m[0]["ccn"] if len(m) == 1 else "",
                    "name_sim_ccn": best["ccn"] if best["sim"] >= NAME_SIM_THRESHOLD else "",
                    "name_sim_top_ccn": best["ccn"], "name_sim_score": round(float(best["sim"]), 3),
                    "legal_exact_ccn": next(iter(hit)) if len(hit := legal.get((r["state"], legal_key(r["debtor_name"])), set())) == 1 else ""})
    path = HOLDOUT.parent / "baselines.csv"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print(f"{len(out)} labeled filings -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
