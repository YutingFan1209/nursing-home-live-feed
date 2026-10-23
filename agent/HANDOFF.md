# Handoff to Claude Code: finish validating the UCC resolver

You are continuing work on `agent/` (agentic UCC filing resolver) in this repo. The code is built and
unit-tested. What is left is running it on the real database and labeling results. The user (Yuting) is
preparing a demo for the NYU Agentic AI Workshop (judged on Ingenuity and Ownership: she must be able to
explain and defend every choice). Demo goes public before Nov 1, 2026.

## How to work with her
- She wants to answer only yes / no. Ask one short question at a time, give your recommendation, and
  show the evidence she needs to decide. Use AskUserQuestion if available.
- Ask before: spending API money (any `--sample` or `--known` run), deleting files, or committing. Do not commit unless asked.
- Never write to the database. The agent's connection is read-only; keep it that way.
- Labels are ground truth for scoring. Never fill a label yourself from a guess. Only a "yes" from her
  makes a label. "Not sure" means leave `human_ccn` blank. Do not research private individuals (e.g. the person-named RI debtor in batch 1).
- Explain results in plain words. She needs to be able to defend them.

## Steps (stop and report after each)
1. Run `python3 -m pytest agent/tests -q`. Expect 32 passed.
2. Run `venv/bin/python3 -m agent.run --selftest`. This is the first run of the NEW search SQL
   (word_similarity + ILIKE, replacing whole-string similarity) against the real DB, including a new
   "search recall" check. If it fails, show her the error and fix `agent/tools.py`.
3. Re-run the North Dade filing: `venv/bin/python3 -m agent.run --state FL --filing-number 202600040755 --audit`
   Before the fix it abstained because "North Dade" could not match the long CMS name.
   Expected now: CCN 106133 (North Dade Nursing and Rehabilitation Center, North Miami), with
   link_basis = name_similarity, so needs_human_review = true, confidence capped at medium.
   Show her the evidence and ask: "Is 106133 right? yes / no". Public-source notes: operator is
   North Dade SNF Operating Company, LLC; parent Ventura Opco Holdco, LLC; the debtor (the real-estate
   company) is tied only by the shared "North Dade SNF" name, so it is a probable, not confirmed, match.
4. Ask yes/no, then delete `agent/traces/SYNTHETIC_viewer_check.json` and `agent/traces/SYNTHETIC_audit_check.json` (fake test traces).
5. Read-only DB check: where did the filing `NJ-2024-99001` (debtor "Sunset Care Group LLC") come from?
   Look at `ucc_filings` for created_at / query_name / source. Tell her whether it looks like a real
   scrape or a test row. Do not delete or modify it.
6. Labeling batch 1. Open `agent/traces/sample_summary.csv` (7 earlier runs). For each row, show her the
   agent's answer and evidence and ask yes / no / not sure: "Is this CCN correct?". Write her answers to
   `agent/traces/labeled_batch1.csv` in the `human_ccn` column (keep leading zeros, 6 digits; use NONE only
   if she says the filing is not a nursing-home deal). Known candidates to put to her as questions:
   Epworth Realty (PA) -> 395393 Cedarwood Rehabilitation & Healthcare Center, Tyrone PA (legal name
   Epworth Rehabilitation and Healthcare LLC, CHOW 2023-05-01; the Realty -> facility tie is by name);
   North Dade -> 106133 (step 3). Leave SEMA LLC (CA) and Sunset Care Group (NJ) blank unless she knows.
7. With her yes (this costs API money): `venv/bin/python3 -m agent.run --sample 20 --audit --out agent/traces/batch2`
   Then repeat the yes/no labeling for those rows, then
   `venv/bin/python3 -m agent.score agent/traces/batch2/sample_summary.csv` (check `agent.score --help` for args).
8. Report: accuracy, coverage, precision when it answers, calibration, accepted-without-review, confident-wrong.
   Say plainly which numbers are too small to trust.
9. Only after the numbers, propose (do not build) next steps: web-search tool, property-company/operator
   rule, lender-classifier fix. OPRO prompt optimization needs 30+ labeled filings first.

## Key facts
- Design rule: "model proposes, rules decide". `agent/verify.py` checks grounding, evidence, timing, and
  link_basis; `agent/audit.py` is a second fresh-context reader. "Verification passed" does not mean correct.
- Trace viewer: open `agent/viewer/index.html` in a browser and drag a trace JSON from `agent/traces/` onto it.
- Baseline: 3,306 UCC-only deals; 2,716 matched; 590 unmatched; 565 eligible for sampling.

---

# Round 2: FINISH UCC (nothing new; no new sources)

Scope rule: UCC only. No CON, no news, no multi-agent. Same working style as above: one yes/no question at a time, ask before spending API money, deleting, committing or pushing, and never write to the database.

## Order (stop and report after each)
1. **Florida tax-roll scan for holdout #9.** Let it finish, then update that line in the holdout doc. Re-check the one county whose file has a different header. Report what you find, with the source link, before any label is written.
2. **Verify the numbers the doc quotes.** Re-run `venv/bin/python3 -m agent.holdout_eval --cost-ratio 10` with no model calls. Confirm 23/24, 21/24 strict, 9-0 p=0.004 vs both rules, and 19/20 ranking. Re-derive the claim "any ratio above about 4 beats review-everything". If it cannot be shown from the doc's own table, remove or reword it to: "with zero errors observed, the current rule costs about half of reviewing everything".
3. **Put the legal-name rule into the real matcher** (matcher/ or ucc/integrator.py), not only the baseline script: debtor name equals the CMS legal business name of exactly one home in the state. Show me the diff and the test, then run it WITHOUT writing to the database and report how many of the ~604 unmatched filings it would newly link. Ask before applying it for real.
4. **Deed lookup tool (the one known gap).** Design first, build only after my yes. Goal: link a property-company debtor to a facility via a recorded deed or assessor record. Start with a tool that takes a facility address and returns the recorded owner for the two counties already solved by hand (Boulder CO, South Kingstown RI). Report what public sources exist per state before writing code. It must be read-only, and every output must carry its source URL so verify.py can ground it.
5. **Re-score the two hard-half filings (#5 CO, #28 RI) with the new tool**, plus any that have independent sources. Report as "n filings", never as a rate.
6. **Freeze and tidy.** Update the doc's limits section to match. Update `agent/README.md`. List the exact commands to reproduce every number. Do not push until I say.
7. **Rehearsal pack:** a one-page Q&A for a judge (why this design, what failed, what is not shown), using only numbers that appear in the doc.

## Do not
- Claim a rate from fewer than 20 labeled filings.
- Tune any prompt or tool against the 24 holdout filings. New work gets a NEW holdout (draw it with a new seed, record it in the manifest).
- Put labels, traces or private-individual debtors in the public repo.
