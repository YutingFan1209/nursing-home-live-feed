"""
Deploy a data refresh to gh-pages in one command.

    venv/bin/python3 scripts/deploy.py            # asks before pushing
    venv/bin/python3 scripts/deploy.py --yes      # the person already said deploy
    venv/bin/python3 scripts/deploy.py --dry-run  # export + checks only

Steps (README "Deployment (gh-pages)"):
  1. export deals.json + feed.xml to a temp folder
  2. scripts/predeploy_check.py: any failed check stops the deploy. There is
     no --force here on purpose: review the report, fix or put the record
     on the reviewed list, then rerun
  3. gold-set score (evals/score.py) against the last deploy's: fewer cards
     right in any group stops the deploy (--accept-drop "<why>" when a gold
     label was corrected, kept in the score history)
  4. Claude check of new cards (evals/holds.py): cards not on the live site
     yet go to the judge; flagged ones are held out of the export until a
     person releases them. Capped by --judge-max (default $5)
  5. stash main's tracked changes, switch to gh-pages, copy, commit, push,
     switch back and unstash -- back on main even if a step fails

Data only. A frontend deploy still follows the README steps.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
HISTORY = ROOT / "data" / "evals" / "score_history.jsonl"

sys.path.insert(0, str(ROOT))
from evals.score import score_export, summary  # noqa: E402  (imported before leaving main)


def run(*cmd, check=True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, check=check, text=True, **kw)


def git(*args, check=True) -> str:
    return run("git", *args, check=check, capture_output=True).stdout.strip()


def last_score() -> dict | None:
    try:
        return json.loads(HISTORY.read_text().splitlines()[-1])
    except (OSError, IndexError, ValueError):
        return None


def show_score(now: dict, before: dict | None) -> list[str]:
    """Lines comparing this export's gold score with the last deploy's.
    Returns the groups whose score went down."""
    down = []
    print("\nGold-set score (cards fully right):")
    prev = (before or {}).get("score", {})
    for g in sorted(now, key=lambda g: (g == "all", g)):
        k, n = now[g]
        line = f"  {g:6} {k:3}/{n:<3} {k / n:5.0%}"
        if g in prev:
            pk = prev[g][0]
            line += f"   last deploy {pk}/{prev[g][1]}" + (f"  ({k - pk:+d})" if k != pk else "")
            if k < pk:
                down.append(g)
        print(line)
    if before:
        print(f"  (last deploy {before['at']}, gh-pages {before.get('commit', '?')})")
    return down


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)   # keep our lines in order with the subprocesses'
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="push without asking (only when the person said deploy)")
    ap.add_argument("--dry-run", action="store_true", help="export and check, don't touch git")
    ap.add_argument("--judge-max", type=float, default=5.0, metavar="USD",
                    help="most to spend checking new cards (default $5); over it, they're held unchecked")
    ap.add_argument("--accept-drop", metavar="WHY", help="deploy although the gold score went down; WHY is kept in the score history")
    ap.add_argument("-m", "--message", default="", help="what changed, added to the commit message")
    a = ap.parse_args()

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        sys.exit(f"On {branch!r}: run this from main.")

    out = Path(tempfile.mkdtemp(prefix="nh-deploy-"))
    deals = out / "deals.json"
    print("1/5 Exporting...")
    run(PY, "scripts/export_deals.py", str(deals))

    print("\n2/5 Predeploy check...")
    if run(PY, "scripts/predeploy_check.py", str(deals), check=False).returncode != 0:
        sys.exit("\nStopped: predeploy check failed. Nothing was deployed.")

    print("\n3/5 Claude check of new cards...")
    from evals import holds as H
    export = json.loads(deals.read_text())
    live = json.loads(git("show", "gh-pages:deals.json"))
    live_ids = {d["id"] for d in live["deals"]}
    prior = H.load()                       # held at an earlier deploy: not in this export
    cards = H.new_cards(export, live_ids)
    if not cards:
        print("  No new cards.")
    elif a.dry_run:
        H.check(cards, export["deals"], a.judge_max, spend=False)
        print("  Dry run: not checked. A real deploy checks them, or holds them unchecked.")
    else:
        new = H.check(cards, export["deals"], a.judge_max, spend=True)
        for i, h in new.items():
            print(f"  held {i[:8]} {h['verdict']:9} {(h.get('headline') or '')[:70]}")
        print(f"  {len(cards) - len(new)} of {len(cards)} new card(s) passed, {len(new)} held")
        if new:
            H.save({**prior, **new})
            print("  Re-exporting without the held cards...")
            run(PY, "scripts/export_deals.py", str(deals))
            if run(PY, "scripts/predeploy_check.py", str(deals), check=False).returncode != 0:
                sys.exit("\nStopped: predeploy check failed. Nothing was deployed.")
    if prior:
        print(f"  {len(prior)} card(s) still held from earlier deploys: python -m evals.holds")

    print("\n4/5 Gold-set score...")
    now = summary(score_export(deals))
    down = show_score(now, last_score())
    if down and not a.accept_drop:
        sys.exit(f"\nStopped: fewer gold cards right than at the last deploy in: {', '.join(down)}."
                 " Run `python -m evals.score stored` on the export to see which. If the drop is"
                 " expected (a gold label was corrected), rerun with --accept-drop \"<why>\".")

    if a.dry_run:
        print(f"\nDry run: export kept in {out}. Nothing deployed.")
        return
    if not a.yes:
        if not sys.stdin.isatty() or input("\nPush to gh-pages? [y/N] ").strip().lower() != "y":
            sys.exit("Not deployed.")

    print("\n5/5 Deploying...")
    stashed = git("stash", "push", "-m", "deploy.py") != "No local changes to save"
    commit = None
    try:
        git("checkout", "-q", "gh-pages")
        shutil.copy(deals, ROOT / "deals.json")
        shutil.copy(out / "feed.xml", ROOT / "feed.xml")
        git("add", "deals.json", "feed.xml")
        msg = f"Data refresh {datetime.now():%Y-%m-%d %H:%M}" + (f": {a.message}" if a.message else "")
        if run("git", "diff", "--cached", "--quiet", check=False).returncode == 0:
            print("No change from the live site; nothing to push.")
        else:
            git("commit", "-q", "-m", msg)
            git("push", "-q", "origin", "gh-pages")
            commit = git("rev-parse", "--short", "HEAD")
    finally:
        git("checkout", "-q", "main")
        if stashed:
            git("stash", "pop", "-q")
    if commit:
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY, "a") as f:
            f.write(json.dumps({"at": f"{datetime.now():%Y-%m-%d %H:%M}", "commit": commit, "score": now,
                                **({"accepted_drop": a.accept_drop} if a.accept_drop else {})}) + "\n")
        print(f"Deployed gh-pages {commit}. Back on main.")


if __name__ == "__main__":
    main()
