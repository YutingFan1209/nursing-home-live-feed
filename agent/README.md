# agent/ -- UCC filing resolver

One UCC-1 filing in; a verified verdict and a full execution trace out.
Separate from the production pipeline (nothing in `main.py` imports it).

## The request journey (what to walk through)

1. **Input**: one row from `ucc_filings` (debtor, secured party, date).
2. **Loop** (`loop.py`): Claude is offered the tools below plus `submit_verdict`,
   and a system prompt with the domain rules. It picks a tool, we run it, the
   result goes back with a `step_id`. Repeat until `submit_verdict` or `max_steps`.
3. **Tools** (`tools.py`, read-only): `search_cms_facilities`, `search_cms_owners`,
   `get_facility_ownership`, `get_related_ucc_filings`, `search_deals`, `classify_lender`.
   Dates are never left to the model: `get_facility_ownership` runs the same
   `_cms_signal()` rule `scripts/export_deals.py` uses.
4. **Verify** (`verify.py`, no model): names/CCN must appear in the filing or a tool
   result (reuses `pipeline/grounding.py`); evidence must cite real steps; a
   timing category must agree with the deterministic rule. Failures blank the
   field, lower confidence, and force `needs_human_review`.
5. **Audit** (`audit.py`, opt-in `--audit`): a second, fresh-context model call sees only
   the filing, the category and each evidence claim next to the exact output of the step
   it cites, and answers per claim. Code then gates the result: an unsupported claim lowers
   confidence and forces review; an incomplete audit is itself a reason for review.
6. **Trace** (`trace.py`): every thought, call, result and timing, saved to
   `agent/traces/<state>_<filing>.json`.

## Design choices (be ready to defend)

- **Own loop, not a framework**: every decision point is visible in ~100 lines.
- **Model proposes, rules decide**: the LLM does the fuzzy entity work the
  matcher couldn't (operator names are often PE personnel, not legal entities);
  date math and grounding stay deterministic.
- **"I can't tell" is a first-class answer** (`unresolved`): the feed's readers
  are scholars and senators, so accuracy beats volume (Tyler's call).
- **Read-only DB connection** (`set_session(readonly=True)`); the agent cannot write.
- **Null timing != refinancing**: CMS owner data lags sales by months, so absence
  of an owner change is not evidence of a refinancing.

## Measuring it (`score.py`)

`agent.run` writes `agent/traces/sample_summary.csv`; `agent.score` reads it. Labels come from:
- `--sample N`: unresolved UCC-only deals. **You label them** (`human_ccn` = 6-char CCN or `NONE`).
  These are the cases the agent exists for, so these are the numbers to show.
- `--known N`: filings the existing matcher already tied to one facility. Auto-labeled, but
  they are the *easy* cases (the matcher found them by name), so treat as a floor / regression check.

It reports accuracy, coverage, precision, **accuracy by confidence** (is "high" really right more
often than "low"?), the share the feed could **accept without review** and how often those are
right, and any **confident-wrong** filing. Tokens, not dollars.

## Same tools as an MCP server (`mcp_server.py`)

`venv/bin/python3 -m agent.mcp_server` serves the six tools (+ `get_ucc_filing`) and two docs
as read-only MCP tools/resources, so a researcher can use them from Claude Desktop (config
snippet in the file). The agent and a person see identical tool results; the difference is who owns
the loop (here, the client), so there is no trace or verification step. `pip install -r agent/requirements.txt`.

## Run

```bash
venv/bin/python3 -m agent.run --selftest                       # every tool once, read-only
venv/bin/python3 -m agent.run --state PA --filing-number <n>   # one filing
venv/bin/python3 -m agent.run --sample 5 --state PA            # 5 UCC-only deals CMS never matched (label by hand)
venv/bin/python3 -m agent.run --known 20 --state PA --audit    # auto-labeled easy cases, with the audit pass
venv/bin/python3 -m agent.score agent/traces/sample_summary.csv
venv/bin/python3 -m pytest tests/test_agent.py tests/test_agent_extras.py   # offline, no DB or API key
```

## Next: make the prompt the experiment (OPRO-style), once there are labels

`SYSTEM_PROMPT` in `loop.py` is the editable artifact. With ~30+ hand-labeled filings frozen as
the eval set: propose prompt variants, run each on the same filings, score with `agent.score`
(accuracy and accepted-precision), keep the best, and check the winner on filings the optimizer
never saw. Keep `tools.py`, `verify.py`, `audit.py` and the labels outside what it may edit, and
freeze the evidence snapshot so scores stay comparable. Not built: it is only meaningful with labels.

Not yet verified against the live DB / API: the SQL in `tools.py` (run `--selftest`)
and real model behavior (run one filing). Tests use a scripted fake model.
