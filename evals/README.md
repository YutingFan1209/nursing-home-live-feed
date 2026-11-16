# Gold-set evals

A hand-labelled sample of site cards, used to measure accuracy field by field
and to test prompt or model changes before they ship.

## What's in it

`gold/labels.jsonl`: 150 cards labelled on 2026-11-16 against their source
documents (article text, filing, CMS row). Sampled from the live export with
seed 20261116 (`evals/sample.py`), stratified by source and spread over months:

| Group | Cards | Why this many |
|---|---|---|
| news + SEC | 70 | weakest source (6 of 8 wrong in the 2026-10-21 audit); enough for a per-field rate |
| UCC | 35 | mostly checks noise and the acquisition/refinancing label |
| CON + licence lists | 25 | |
| CHOW + CMS owner changes | 20 | transcribed from CMS, so expected near 100% |

Each record holds the **correct** value of every scored field, the fields the
site had wrong, and a short note. Only IDs and correct values are stored:
the full sample (with article summaries that name people) stays in
`data/evals/`, which git ignores. Cards whose buyer or seller is a private
person were left out of the sample.

Verdicts: `right`, `wrong` (a fact is wrong), `minor` (formatting: a
truncated name, a description in a name field), `duplicate` (the same deal
is already on the site as another card), `noise` (true, but not a deal),
`uncertain` (the labeller couldn't settle a field; that field is never scored).

## Running it

```bash
# Free: score what an export shows now
venv/bin/python3 scripts/export_deals.py /tmp/live.json
venv/bin/python3 -m evals.score stored /tmp/live.json

# Paid: re-extract the 64 gold news articles with the current prompt + model.
# Prints the estimate (about $1.70) and stops unless --yes is given.
venv/bin/python3 -m evals.score extract
venv/bin/python3 -m evals.score extract --yes
```

`stored` is the monthly audit: run it after an export and compare with the
last run. `extract` is for prompt or model changes: run it before and after.

## Rules

- Don't edit a label to make a score go up. Change a label only when the
  label itself was wrong, and say why in its note.
- A fix built from these 150 cards can't be measured on them. Check it on a
  fresh sample (new seed) before quoting a rate.
- Add new labelled cards as new files (`gold/labels_<date>.jsonl`) rather
  than rewriting this one.

## Guards (2026-11-16)

- **One set of field rules.** `pipeline/field_rules.py` holds what each field
  means and the site's display conventions. The extraction prompt and the
  judge both read it, and hand labels follow it. Change a rule there, not in
  one prompt.
- **A prompt change must be measured.** `evals/measured.json` keeps a
  fingerprint of the extraction and judge prompts from their last full gold
  run; `tests/test_prompt_measured.py` fails when either changed since. Rerun
  `evals.score extract --yes` (~$1.70) or `evals.judge gold <export> --yes`
  (~$5); each records itself on a full run.
- **New cards are checked before they go live.** `scripts/deploy.py` sends
  cards not yet on the site to the judge (about $0.035 each, capped by
  `--judge-max`, default $5). right/minor ship; wrong, duplicate, noise,
  uncertain or unchecked are held (`python -m evals.holds` lists them,
  `release <id>` ships one).
- **A lower gold score stops the deploy**, unless `--accept-drop "<why>"`
  (kept in the score history) when a gold label was corrected.
