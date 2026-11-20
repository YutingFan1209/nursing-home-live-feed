# DeepSeek as judge: pre-registration (draft, 2026-11-19)

Status: **draft, not approved. No DeepSeek call has been made.** Nothing below runs until the
thresholds in §3 and the decisions in §6 are approved. Once they are, this file is frozen: later
changes are appended as dated amendments, never edited in place.

## 1. What Phase 0 found (free, local)

### 1.1 The "human gold" labels are not human

- All 150 labels in `evals/gold/labels.jsonl` were written by **Claude (Opus, Claude Code
  session d9b419ae)** on 2026-11-16 between 15:19 and 15:24 UTC, about 2 seconds a card. The
  session transcript shows them written to the scratchpad `labels.tsv` with `cat >>` commands.
  In that stretch the person sent only two messages: a question about which source is weakest,
  and "ok". Raw copy: `data/evals/labels_2026-11-16.tsv`.
- **2 labels were changed by Claude on 2026-11-17 (15:03 UTC), after reading the judge's
  disagreements**, between runs v2 and v3:
  `297f5411` wrong → right (date removed) and `3c788dcd` wrong → minor. In v1 and v2 the judge had
  said right and minor on these two cards. In both cases the label moved toward the judge.
- 5 more cards change verdict through `hand_verdict()` (a wrong/minor card whose shown fields now
  match the gold values counts as right): `c832712a`, `cd7e2343`, `d81addce`, `06235bfc`,
  `7321b855`.
- The only human review on record is `data/evals/reviews_2026-11-17.tsv`: 54 explicit rows, with
  "unmarked = agree" for the other 96. It covers the **seed-20261117 sample, which shares no cards
  with the gold 150**, and the person reviewed Opus's pre-labels, so that set is anchored on Opus.

Consequence: any comparison against these labels measures agreement with an Opus-written
reference, which favours Opus. It is not a measurement against human ground truth.

### 1.2 Contamination

The judge prompt was tuned on these 150 cards in 5 full runs (v1–v5). Every run used a different
prompt:

| Run | When (UTC) | File | Prompt change before it |
|---|---|---|---|
| v1 | 11-16 15:53 | `judge_gold_2026-11-16_v1.jsonl` | first prompt |
| v2 | 11-16 18:03 | `judge_gold_2026-11-16.jsonl` (149 cards, deploy export, not kept) | shared `field_rules.py`, site conventions |
| v3 | 11-17 16:02 | `judge_gold_2026-11-17_v3.jsonl` | duplicate/uncertain rules, neighbour scoring, 2 labels changed |
| v4 | 11-17 16:17 | `judge_gold_2026-11-17_v4.jsonl` | role definitions in `field_rules.py` |
| v5 | 11-17 16:27 | `judge_gold_2026-11-17.jsonl` | "uncertain" loosened (current prompt, fingerprint `8afd433c`) |

Changes were made by reading the disagreements on these same cards. **The 150 are contaminated
for testing Opus**: its numbers on them are training-set numbers. For DeepSeek they are less
contaminated, but the prompt was still fitted to Opus's errors on these cards and the labels came
from Opus, so the comparison is still tilted toward Opus.

### 1.3 Raw per-call logs do not exist

The 5 runs saved only the parsed JSON label for each card. Nothing recorded request ids, model id
served, token usage per call, latency, raw response text, or whether a server-side fallback fired.
The `.log` files hold one verdict line per card, and the `.txt` files hold totals only.
Everything below is computed from the per-card JSONL files. It is the rawest record that exists.

### 1.4 Opus vs reference labels (computed from the JSONL, measured)

Gate decision as `deploy.py` uses it: pass = `right` or `minor`; held = `wrong`, `duplicate`,
`noise`, `uncertain`. Reference "bad" = `wrong`, `duplicate`, `noise`, `uncertain` after
`hand_verdict()` (24 cards; 17 if reference-`uncertain` is left out).

| Run | False pass (bad card shipped) | False fail (good card held) | κ | AC1 | Same 6-class verdict |
|---|---|---|---|---|---|
| v1 | 6/24 = 25% (12–45%) | 37/126 = 29% (22–38%) | 0.30 | 0.53 | 92/150 |
| v2 | 6/23 = 26% (13–46%) | 20/126 = 16% (11–23%) | 0.46 | 0.74 | 107/149 |
| v3 | 0/24 = 0% (0–14%) | 29/126 = 23% (17–31%) | 0.52 | 0.69 | 98/150 |
| v4 | 0/24 = 0% (0–14%) | 35/126 = 28% (21–36%) | 0.45 | 0.61 | 95/150 |
| v5 | 1/24 = 4% (1–20%) | 26/126 = 21% (14–29%) | 0.53 | 0.72 | 101/150 |

Wilson 95% intervals in brackets. v5 by group: news false pass 1/15, false fail 11/55; UCC 0/4,
9/31; CON 0/3, 5/22; CHOW 0/2, 1/18. With the 7 reference-`uncertain` cards left out, v3–v5 have
0/17 false passes (upper bound 18%).

### 1.5 Opus vs Opus (the noise floor, measured, but an upper bound)

No two runs used the same prompt, so **there is no pure repeat**. Agreement between runs is
prompt change plus sampling noise, which makes it an upper bound on noise:

| Pair | Same gate decision | κ (gate) | Same 6-class verdict |
|---|---|---|---|
| v3–v4 (smallest prompt change) | 142/150 (95%) | 0.89 | 138/150 |
| v3–v5 | 136/150 (91%) | 0.79 | 133/150 |
| v4–v5 | 136/150 (91%) | 0.80 | 133/150 |
| v1–v2 … v2–v5 | 116–128 (77–86%) | 0.51–0.66 | 102–120 |

18 of 149 cards flipped gate decision across v3–v5. Working noise floor: **about 5% of cards
(8/150) change gate decision between near-identical Opus runs.** Phase 2's 3 same-prompt Opus
repeats will replace this with a real figure.

### 1.6 What one gold set can show

There are 24 bad cards. Zero misses out of 24 still leaves a Wilson upper bound of **13.8%** on
the false-pass rate. Bringing that bound to 5% or less takes 73 bad cards with 0 misses, 110 with
1 miss, or 142 with 2. **No model can be cleared for the deploy gate on this set, Opus included.**
The current Opus gate is not certified at 5% either (its bound is 14–20%).

### 1.7 DeepSeek API (docs read 2026-11-19, api-docs.deepseek.com)

- Base URL: `https://api.deepseek.com` (OpenAI format), `https://api.deepseek.com/beta` (needed
  for strict tool schemas), `https://api.deepseek.com/anthropic` (Anthropic format).
- Models: `deepseek-flash` (serves DeepSeek-V4.1-Flash-1017; the old `deepseek-v4-flash` name is
  an alias to the same thing) and `deepseek-v4-pro` (DeepSeek-V4-Pro-0813). Context 1M, max output
  384K. **No dated, immutable model id is offered.** "Pinned" can only mean: send the id, record
  the `model` and `system_fingerprint` fields in every response, and stop if they change mid-run.
- Thinking is on by default at effort `high` (`thinking: {type: enabled|disabled}`,
  `reasoning_effort: low|high|max`). `temperature` has no effect when thinking is on.
  `reasoning_content` is returned alongside `content`.
- JSON: `response_format: {type: json_object}` only, with no `json_schema` mode. The docs warn that
  `content` can come back empty, and the prompt must contain the word "json". Schema enforcement
  only through **strict function calling** on the beta endpoint, which supports object, string,
  number, integer, boolean, array, enum and anyOf. Every property must be required and
  `additionalProperties: false`. No minLength/maxLength/minItems/maxItems.
- Prices per 1M tokens, off-peak (peak is double): Flash $0.003 cache-hit input / $0.15 miss /
  $0.60 output. Pro $0.022 / $0.66 / $1.98. Peak hours 01:00–04:00 and 06:00–10:00 UTC, Monday to
  Friday (Chinese public holidays are off-peak). Prompt caching is automatic by prefix, with no
  `cache_control`.
- The docs don't say whether a request-id header is returned. The response `id` field will be
  logged, plus any `x-request-id`-style header if present.

### 1.8 Adaptations to our prompt / SCHEMA

1. `SCHEMA` already meets DeepSeek strict rules: every object lists all properties as `required`
   and `additionalProperties: false`, and nullable fields use `anyOf` (supported). No keyword
   changes are needed. It becomes the `parameters` of one tool `submit_label` with `strict: true`.
2. `tool_choice`: force `{"type":"function","function":{"name":"submit_label"}}` if DeepSeek
   accepts it with thinking on. If not, use `auto` plus one added line, "Call submit_label once
   with your label." That line changes DeepSeek's prompt only, and it will be reported.
3. Drop Anthropic-only parts: `betas`, `fallbacks`, `cache_control`, `output_config.effort`. Map
   effort `high` to `reasoning_effort: "high"`.
4. `SYSTEM` and the per-card user text stay byte-identical across models. The card prompts are
   built once with the project code and frozen to disk with a sha256 per card. Both models read
   the same frozen files, so evidence can't drift between models or repeats. The DB has changed
   since 11-17, so the old v1–v5 runs did *not* see these exact inputs.
5. Every reply is validated locally with `jsonschema` against `SCHEMA`, whatever the server
   claims. An empty, truncated, non-JSON or schema-invalid reply, a refusal, or a timeout is
   **invalid. There are no retries in the measured runs** (transport errors before any response
   are retried up to 2 times and logged).
6. Opus runs for this test send **no `fallbacks`**, so every verdict comes from
   `claude-opus-5-5`. A refusal counts as invalid. Production `judge.py` is not changed.

## 2. Metrics

Unit: one card. Each model runs 3 repeats. Primary verdict per card = majority gate decision of
the 3 (pass/held always has a majority). Pooled rates over all 3×150 calls are reported too, with
CIs from a card-level cluster bootstrap.

- **False-pass rate** = reference-bad cards the model passes ÷ reference-bad cards (n = 24).
- **False-fail rate** = reference-good cards the model holds ÷ reference-good cards (n = 126).
- **Invalid output = failure in both directions**: on a bad card it counts as a false pass, and on
  a good card as a false fail. This is stricter than production, where an unchecked card is held.
- Agreement with reference: Cohen's κ and Gwet's AC1 (AC1 is primary, because classes are
  unbalanced 126:24). Card-bootstrap 95% CIs, 10,000 resamples, seed 20261119. Also given:
  6-class κ, the confusion matrix, and the same per group (news/UCC/CON/CHOW; small-n groups get
  counts only).
- Paired DeepSeek vs Opus: McNemar exact test on per-card correctness (gate decision) and a
  bootstrap CI on the difference in false-pass and false-fail rates.
- Self-consistency: share of cards with the same gate decision in all 3 repeats, per model, and
  Fleiss' κ across repeats.
- Cost per correct gate verdict (measured tokens × list price; off-peak price for DeepSeek if run
  off-peak), latency p50/p95 per call (wall clock, measured), and invalid-output rate.
- Every number in the report is tagged *measured* or *estimated*.

Reference labels for scoring = `labels.jsonl` + `hand_verdict()` against the frozen export,
**then overridden by your adjudication** on the disagreement set (`evals/adjudication.csv`). Final
numbers are computed only after adjudication. Pre-adjudication numbers are shown but marked
provisional.

## 3. Proposed acceptance rules (for your approval)

Primary model under test: `deepseek-flash`. The same rules apply to `deepseek-v4-pro` if it's run.

### (a) Tuning use: DeepSeek replaces Opus for `--only` / iteration runs. Opus still does the final full measurement and the gate.

All of these must hold:

| # | Rule | Why this number |
|---|---|---|
| T1 | False passes (majority verdict) ≤ 1 of 24, i.e. **Wilson 95% upper bound ≤ 20.2%** | the same as Opus v5 (1/24) |
| T2 | Cluster-bootstrap 95% **upper bound of (FP_DeepSeek − FP_Opus) ≤ +10 points** (pooled over repeats) | non-inferior by at most ~2 cards in 24 |
| T3 | False-fail point estimate ≤ 30% **and** AC1 lower 95% bound ≥ 0.50 | Opus v3–v5: 21–28% false fail, AC1 0.61–0.73 |
| T4 | Same gate decision in all 3 repeats on ≥ 90% of cards | Opus cross-prompt floor 91–95% |
| T5 | Invalid outputs ≤ 2% of calls (≤ 9 of 450) | more would waste the cost saving on reruns |

### (b) Deploy-gate use: DeepSeek decides which new cards ship

| # | Rule |
|---|---|
| G1 | **Wilson 95% upper bound on the false-pass rate ≤ 5%**, measured on a **blind holdout** of at least 73 bad cards (0 misses) or 110 (1 miss), labelled by a person without seeing any model output |
| G2 | T2–T5 also pass on that holdout |
| G3 | Screening on the 150 (necessary, not sufficient): 0 false passes by majority verdict, and no bad card passed in any single repeat that Opus held in all 3 |

On the 150 alone, (b) is **fail by default**: G1 can't be met there (§1.6). A result that clears G3
only earns the holdout.

## 4. Run plan (after approval)

1. Freeze inputs: export, card prompts and their sha256 to the scratchpad. Install `openai`,
   `jsonschema`, `scipy`, `statsmodels` and `scikit-learn` in a **separate scratchpad venv**, so the
   project venv is untouched. Production code, the DB and the site are only read.
2. Dry run: 1 card per model, with the raw request (key redacted) and raw response shown to you.
3. 10 cards × both models. Stop and show the results.
4. Full: 150 cards × 3 repeats × each model, with interleaved order (on card *i*, repeat *r*, the
   model called first alternates by *(i + r) mod 2*). DeepSeek only off-peak (now: weekdays
   outside 01–04 and 06–10 UTC).
5. Log per call (JSONL, scratchpad): run, repeat, card id, prompt sha, model requested, model and
   fingerprint served, request/response id, input/cached/output/reasoning tokens, latency, finish
   reason, raw response, schema valid y/n.

## 5. Cost (estimated, before any call)

From Opus's measured usage (~7.3k input and ~700 output tokens a card). DeepSeek output is a
guess of 1.5–3k tokens a card, because its reasoning is usually longer and billed as output.

| Item | Per full run (150 cards) | 3 repeats |
|---|---|---|
| deepseek-flash, off-peak | ~$0.25–0.45 | **~$0.8–1.4** |
| deepseek-v4-pro, off-peak | ~$1.2–1.6 | ~$3.6–4.8 |
| claude-opus-5-5, real-time | ~$5.5 (measured runs: $5.15–6.6) | **~$16.5** |
| claude-opus-5-5, Batch API | ~$2.75 | ~$8.3 (no latency figures, results within ~1 h) |

**The $3 cap covers DeepSeek Flash only.** Three same-prompt Opus repeats can't fit it. See §6.

## 6. Decisions needed from you

1. **Thresholds** in §3: approve, or change.
2. **Budget for Opus repeats.** Options: (i) raise the cap to ~$12 (Flash ×3 real-time plus Opus ×3
   batch, with no Opus latency); (ii) ~$19 (everything real-time); (iii) keep $3, with Flash ×3 and
   one Opus real-time run on 40 cards (~$1.5), and the noise floor stays an estimate.
3. **Which DeepSeek model**: Flash only (recommended within budget), or Pro as well.
4. **Reference labels.** They are Opus-written (§1.1). Options: (i) score against them and rely on
   adjudication of disagreements, plus a random 20 of the cards both models agree on, so shared
   errors show up; (ii) first have a person relabel the 24 bad cards and a random 30 good ones
   blind (about 1–2 h); (iii) skip to a fresh blind holdout.
5. **Data leaving for DeepSeek.** The card evidence includes article text, CMS owner rows (some
   are individual owners' names) and other cards. It would go to DeepSeek's servers in China under
   their retention terms. Cards whose buyer or seller is a private person are already out of the
   sample, but owner rows in the evidence are not filtered. OK to send as is, or strip individual
   owner rows from the DeepSeek copy? Stripping them would also mean stripping them from Opus's
   copy, to keep inputs identical.
6. `DEEPSEEK_API_KEY` is not set in this shell. Export it in the terminal you start Claude Code
   from, or run `! export DEEPSEEK_API_KEY=...` here.

## 7. What this cannot show, even if everything passes

- n = 24 bad cards: wide CIs, and gate safety can't be shown (§1.6).
- One gold set, one sampling seed, written by the same model family as one of the contestants,
  with a prompt tuned to it.
- No same-prompt Opus repeat exists before this run.
- Groups other than news have 2–4 bad cards each: no per-group claims.
- Holdout recommendation for the gate: a fresh seed, labelled by a person blind, with at least 73
  bad cards. At the ~16% bad rate of this sample that's ~460 random cards. Enriching with cards
  the current gate held is cheaper, but rates then have to be reweighted by stratum.

## 8. Side finding for Phase 4 (score.py cost constants)

`evals/score.py:181-182` already holds the correct Sonnet 5.5 list prices ($2 input / $10 output /
$0.10 cache read / $2.50 5-minute cache write). There's no constant to fix there. The estimate's
weak points are elsewhere: `chars/4 + 200` for article input and `OUT_PER_ARTICLE = 1500` are both
unmeasured, and the judge's estimate turned out 3.6× low the same way. Proposed for Phase 4:
calibrate both from the per-call `Extraction usage` log lines that `pipeline/extractor.py` now
writes (or from free `count_tokens` calls), then show the diff.

## Amendment 1 (2026-11-19, ~15:10 UTC): approved

The person approved §6 items 1–5 as proposed ("1-5 fine"), read as:
1. Thresholds T1–T5 and G1–G3 approved unchanged. This file is frozen from here on.
2. Budget: Opus repeats still to be picked from (i)/(ii)/(iii) before the full run. The dry run
   and the 10-card step (well under $3 together) go ahead now; the full run waits for that choice.
3. Model: `deepseek-flash` only.
4. Reference labels: option (i). Score against `labels.jsonl` + `hand_verdict()`, adjudicate the
   disagreements, plus a random 20 cards on which both models agree (seed 20261119).
5. Evidence sent to both models as is.
6. Key: `DEEPSEEK_API_KEY` from `.env`.

## Amendment 2 (2026-11-19, dry run)

DeepSeek answered the forced `tool_choice` with 400 "Thinking mode does not support this
tool_choice" (request b9e04bee-4d8d-4e48-80bf-53c0c699295a). As planned in §1.8 item 2:
`tool_choice: "auto"`, and DeepSeek's system prompt gets one appended line, "Call the submit_label
tool exactly once with your label; do not answer in plain text." The per-card user text is
unchanged and byte-identical across models. A reply without a tool call counts as invalid.

## Amendment 3 (2026-11-19, after the 10-card step, before the full run)

- 10-card step (seed 20261119): DeepSeek had 1 invalid reply out of 10 (`ccns: null` where the
  schema requires an array; strict mode didn't enforce it). With the dry run that makes 2 of 11.
  Measured cost per card: Flash $0.0023, Opus $0.025 (list price).
- Budget (person's choice): Flash ×3 real-time, plus **Opus ×3 through the Message Batches API**
  (half price). So Opus has no per-call latency and no interleaving (all 450 Opus requests go in
  one batch). p50/p95 latency is reported for DeepSeek only. Estimated total ≈ $7.
- Scoring (person's choice): primary = as frozen (invalid = failure). The report also gives a
  sensitivity view, labelled as a non-primary deviation, where `null` in an array field
  (`states`, `ccns`, `wrong_fields`, `unsure_fields`) is read as `[]` and the reply re-validated.
  Pass/fail is decided by the primary only.

## Amendment 4 (2026-11-19, after adjudication; before any holdout label exists)

**Reference correction.** 7 adjudicated UCC cards were marked wrong only because the seller wasn't
in the judge's evidence. All 7 sellers match `data/chow_cache/SNF_CHOW_2026.09.29.csv` exactly,
so they count as good (minor). The cards: 8a1e4682, 858bbb89, 534cbf08, 19534563, 94eb68e8,
f50ea1b8, f4b5b892. Cause: `evals/judge.py` evidence reads `chow_seen_records`, which has buyer
and date but no seller.

**Holdout (the person's choices).**
- Target: a 10% upper bound.
- Quotas: the gold quotas ×0.8 (news 56, UCC 28, CON 20, CHOW 16).
- Source: a fresh read-only export (2026-11-19, 5,214 cards) via `evals.sample.draw`, seed 20261219.
- Excluded: the gold 150, the 53 cards reviewed on 11-17, and individual filings.
- Files: `data/evals/holdout_2026-11-19/` (`cards.jsonl`, blind sheet `holdout_labels.csv` with no
  model output, rows shuffled).

**Labelling and scoring.**
- The person labels before any judge runs on these cards.
- Then the current judge runs once ×3 through Batch. Its cost is shown before the run.

**Rule H1 (10% gate bound).** The Wilson 95% upper bound on bad cards shipped (majority of 3) must
be ≤ 10%. That needs ≥ 35 bad cards with 0 shipped. With 120 cards the expected number of bad
cards is ~31, which caps the bound at about 11% even with 0 misses. If labelling finds fewer than
35 bad cards, the extension is drawn with seed 20261220 from the remaining pool, in the same
group shares, until 35 is reached.
