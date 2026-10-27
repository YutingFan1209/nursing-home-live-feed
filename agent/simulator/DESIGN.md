# UCC resolver simulator — design sketch (2026-10-27)

A synthetic world with known answers, so the resolver can be stress-tested
on the cases we know are hard without hand-labelling real filings.
**Scores from it are stress tests, never accuracy claims**: we wrote the
cases, so they only cover failure modes we already know about.

## How it works

```
world.py  seed → 600 homes, owners, CHOW rows, UCC filings + ~36 cases with answers
load.py   nh_sim DB (real schema via pg_dump -s) → the real ToolBox, unchanged
resolve   rules_resolver.py (free)  or  agent.loop.resolve_filing (--agent, paid)
score.py  right / abstain / flagged_wrong / confident_wrong, per case kind
```

- **The real tools run their real SQL.** Only the data is synthetic. The
  CHOW CSV is swapped in via `ToolBox._chow_rows`; the county property
  lookup is stubbed out.
- **The names are made up** ("Quorohaven Op LLC"), so the model can't
  answer from what it already knows. Lender names are real, because the
  lender classifier is real code.
- **CMS lag is modeled.** CMS records sales after 2026-05 less and less
  often, so `complete_through` and the `cms_lag` case behave as they do in
  real data.
- **Twelve case kinds:**
  - acq_exact, refi_abbrev, address_propco, seller_debtor, renamed_home
  - twin_homes (must stay unresolved)
  - cms_lag (must stay `financing`)
  - vendor_lien, person_debtor (must flag review), operations_transfer
  - decoy (no nursing home), holdco (portfolio, no single CCN)

```
venv/bin/python3 -m agent.simulator.run [--seed N] [--per-kind N] [--agent]
venv/bin/python3 -m pytest agent/tests/test_simulator.py
```

## First results (rules resolver, free; seeds 1, 2, 3, 7)

- **Right:** 31–32 of 35–36 cases. The 3 operations_transfer cases are
  always confident_wrong; the rules don't know that kind.
- **Seed 1:** CMS was read as complete through 2026-09 when it isn't, so
  2 cms_lag cases became confident refinancing. The world is too small:
  ~4 sales a month against ~50 in real data, so the median rule is noisy.
  Scale the world up before trusting the timing cases.
- **The world is too easy.** The rules get nearly everything, because one
  author wrote both the cases and the rules. This is the honest limit of a
  hand-written simulator.

## Should it be multi-agent? (Strands spike)

`strands_spike.py` builds the loop as a Strands Graph and runs it offline,
with a scripted stub model in place of Claude:

```
red_team (LLM) → materialize → resolve → score → critic (LLM) ─┐
      ↑────────────────────── "again" ───────────────────────────┘
```

**Verdict: not for v1. Maybe one LLM role in v2, run offline.**

- **The answers must come from code, not from an agent.** In the spike,
  the red team may only rename a debtor; the variant keeps its base case's
  answer. An agent that also wrote the answers would grade its own homework.
- **Graph nodes pass only text.** Cases, verdicts and scores had to travel
  in a shared dict (`invocation_state`) around the framework. Three of the
  five nodes are plain code anyway.
- **The critic adds nothing yet.** "Another round?" is a cost/coverage rule,
  not a judgement.
- **Swarm doesn't fit.** Free hand-offs make runs unrepeatable, and a fixed
  seed is the point of a simulator.
- **Claude writing hard cases for Claude** finds the blind spots both share
  and misses others. Use it to widen coverage, not to measure accuracy.
- **Where an LLM does help:** varying surface forms. Even scripted, round 2
  found a miss: "Ithenridge-Snf Opco LLC" (hyphen glued to the core) made
  the rules abstain. That is the red team's job. It can run as a batch
  script that writes variants to a file for a person to review and freeze
  into the suite. No orchestration framework needed.
- **What Strands gives:** hooks, timeouts, OTEL traces, sessions. Nice to
  have, but they cut against the resolver's "own loop, every decision
  visible" choice (agent/README.md). Strands stays out of requirements.txt.

## Next, if wanted

1. Scale the world up (~3,000 homes, ~20 sales a month) so the timing cases
   are trustworthy.
2. One paid agent run on a single seed (36 cases × ~5 calls). **Ask first.**
3. Red-team batch: Claude proposes debtor-name variants offline → a person
   reviews them → they're frozen as fixtures.
