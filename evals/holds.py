"""
New cards wait for Claude's check before they go on the site.

At each deploy, scripts/deploy.py sends every card that isn't on the live
site yet to the judge (evals/judge.py, the same prompt measured against the
gold set). A card the judge calls right or minor ships; wrong, duplicate,
noise or uncertain -- or one the judge couldn't check -- is held: left out
of the export until a person looks at it.

News and SEC cards are always held, even when the judge passes them
(user's goal 2026-11-17: news as close to 100% as possible): they wait as
"awaiting_approval" until the person approves them on the Feed Audit Review
page; evals/approval.py seeds the page and applies the decisions.

    venv/bin/python3 -m evals.holds                 # list held cards with the judge's note
    venv/bin/python3 -m evals.holds release <id>    # ship it as is (after a fix, or judge was wrong)

Files (data/evals/, gitignored: notes can name people):
  judged_new.jsonl  every new card's verdict; a card is judged once
  holds.json        the current hold list, read by scripts/export_deals.py
  released.jsonl    cards a person cleared
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "evals"
JUDGED = OUT / "judged_new.jsonl"
HOLDS = OUT / "holds.json"
RELEASED = OUT / "released.jsonl"
SHIP = {"right", "minor"}
APPROVE_SOURCES = {"rss", "edgar"}   # a person approves every one of these
COST_PER_CARD = 0.040          # measured: 5 gold runs 11-16/17, $24-30 for 749 cards (Opus 5.5, effort high); upper end, it is a cap


def _jsonl(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {r["id"]: r for r in map(json.loads, filter(str.strip, path.read_text().splitlines()))}


def load() -> dict[str, dict]:
    """The hold list. Missing file = nothing held."""
    try:
        return json.loads(HOLDS.read_text())
    except (OSError, ValueError):
        return {}


def held_ids() -> set[str]:
    """Read by the export."""
    return set(load())


def new_cards(export: dict, live_ids: set[str]) -> list[dict]:
    """Visible cards the live site doesn't have yet (grouped-away records ride on their lead)."""
    return [d for d in export["deals"] if d["id"] not in live_ids and not d.get("grouped_into")]


def check(cards: list[dict], deals: list[dict], max_cost: float, spend: bool) -> dict[str, dict]:
    """Judge the cards not judged before; return the hold list {id: reason}.
    Doesn't spend unless spend is set and the estimate is under max_cost:
    unchecked cards are held."""
    from evals.judge import run
    from evals.sample import GROUP
    judged, released = _jsonl(JUDGED), _jsonl(RELEASED)
    todo = [c for c in cards if c["id"] not in judged and c["id"] not in released]
    est = len(todo) * COST_PER_CARD
    if todo:
        print(f"  {len(todo)} new card(s) to check, about ${est:.2f}")
        if spend and est <= max_cost:
            OUT.mkdir(parents=True, exist_ok=True)
            run(todo, deals, JUDGED, True, lambda c: GROUP.get(c.get("source_type")))
            judged = _jsonl(JUDGED)
        elif spend:
            print(f"  Over the --judge-max ${max_cost:.2f} cap: not checked, so held.")
    holds = {}
    for c in cards:
        if c["id"] in released:
            continue
        j = judged.get(c["id"])
        if not j:
            holds[c["id"]] = {"verdict": "unchecked", "headline": c.get("headline")}
        elif j["verdict"] not in SHIP or c.get("source_type") in APPROVE_SOURCES:
            holds[c["id"]] = {"verdict": j["verdict"] if j["verdict"] not in SHIP else "awaiting_approval",
                              "judge_verdict": j["verdict"], "headline": c.get("headline"),
                              "source_type": c.get("source_type"),
                              "wrong_fields": j.get("wrong_fields"), "note": j.get("note"),
                              "duplicate_of": j.get("duplicate_of")}
    return holds


def save(holds: dict[str, dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    HOLDS.write_text(json.dumps(holds, indent=1, default=str) + "\n")


def _find(prefix: str, pool) -> str:
    hits = [i for i in pool if i.startswith(prefix)]
    if len(hits) != 1:
        sys.exit(f"{prefix}: {'no' if not hits else 'more than one'} held card matches")
    return hits[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("release")
    r.add_argument("id")
    r.add_argument("--note", default="")
    a = ap.parse_args()
    holds = load()
    if a.cmd == "release":
        cid = _find(a.id, holds)
        with open(RELEASED, "a") as f:
            f.write(json.dumps({"id": cid, "at": f"{datetime.now():%Y-%m-%d %H:%M}", "note": a.note}) + "\n")
        holds.pop(cid)
        save(holds)
        print(f"Released {cid[:8]}; it ships with the next deploy.")
        return
    if not holds:
        print("Nothing held.")
    for cid, h in holds.items():
        print(f"{cid[:8]} {h['verdict']:9} {h.get('headline') or ''}")
        if h.get("note"):
            print(f"{'':19}{h['note']}" + (f"  (wrong: {', '.join(h['wrong_fields'])})" if h.get("wrong_fields") else ""))


if __name__ == "__main__":
    main()
