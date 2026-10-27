"""
Builds a synthetic world, loads it into nh_sim, resolves every case and
scores the answers. A stress test of known failure modes, not an accuracy claim.

  venv/bin/python3 -m agent.simulator.run                 # rules resolver, free
  venv/bin/python3 -m agent.simulator.run --agent         # the real agent loop: API calls, costs money
  options: --seed N  --per-kind N  --keep (reuse nh_sim as loaded; skip rebuild)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.simulator import rules_resolver
from agent.simulator.load import create_sim_db, load_world, sim_toolbox, _with_db, SIM_DB
from agent.simulator.score import grade, report, summarize
from agent.simulator.world import build_world

OUT = Path("agent/traces/simulator")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--per-kind", type=int, default=3)
    ap.add_argument("--agent", action="store_true")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args(argv)
    from config import get_config
    real_url = get_config().database_url

    w = build_world(a.seed, per_kind=a.per_kind)
    if a.keep:
        sim_url = _with_db(real_url, SIM_DB)
    else:
        sim_url = create_sim_db(real_url)
        load_world(sim_url, w)
    tb = sim_toolbox(sim_url, w)
    print(f"world seed {a.seed}: {len(w.homes)} homes, {len(w.cases)} cases, "
          f"CMS complete through {tb._cms_complete_through()}")

    results = []
    for case in w.cases:
        if a.agent:
            from agent.loop import resolve_filing
            verdict, _ = resolve_filing(case.filing, tb)
        else:
            verdict = rules_resolver.resolve(case.filing, tb)
        results.append((case, verdict))

    s = summarize(results)
    print(report(s))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{'agent' if a.agent else 'rules'}_seed{a.seed}.json"
    path.write_text(json.dumps({"summary": s, "cases": [
        {"case": c.case_id, "kind": c.kind, "grade": grade(v, c), "verdict": v, "debtor": c.filing["debtor_name"],
         "ccn_ok": sorted(map(str, c.ccn_ok)), "cat_ok": sorted(c.cat_ok), "note": c.note}
        for c, v in results]}, indent=1, default=str))
    print(f"-> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
