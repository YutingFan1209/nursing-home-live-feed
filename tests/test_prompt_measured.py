"""A prompt change must be measured before it ships.

evals/measured.json records the fingerprint of the extraction and judge
prompts as of their last full gold run (written by `python -m evals.score
extract --yes` and `python -m evals.judge gold <export> --yes`). If this
fails, a prompt, field rule (pipeline/field_rules.py) or model changed:
rerun the matching measurement and compare with the score recorded here.
"""
import json

import pytest

from evals import MEASURED, fingerprint


@pytest.mark.parametrize("kind,cmd", [
    ("extract", "venv/bin/python3 -m evals.score extract --yes  (~$1.70)"),
    ("judge", "venv/bin/python3 -m evals.judge gold <export.json> --yes  (~$5)"),
])
def test_prompt_was_measured(kind, cmd):
    rec = json.loads(MEASURED.read_text()).get(kind)
    assert rec, f"the {kind} prompt has never been measured: run {cmd}"
    assert rec["fingerprint"] == fingerprint(kind), (
        f"the {kind} prompt changed since it was measured on {rec['date']} ({rec['score']}): run {cmd}")
