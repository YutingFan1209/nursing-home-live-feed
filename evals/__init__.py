

# ---- which prompt versions have been measured ----------------------------
# evals/measured.json holds a fingerprint of the extraction prompt and of
# the judge prompt as of their last full gold run. tests/test_prompt_measured.py
# fails when a prompt no longer matches, so a prompt edit can't be committed
# (or deployed) without a fresh measurement.
import hashlib as _hashlib
import json as _json
from pathlib import Path as _Path

MEASURED = _Path(__file__).resolve().parent / "measured.json"


def fingerprint(kind: str) -> str:
    if kind == "extract":
        from pipeline import extractor
        text = extractor.EXTRACTION_PROMPT + extractor.config.claude_model
    else:
        from evals import judge
        text = judge.SYSTEM + _json.dumps(judge.SCHEMA, sort_keys=True) + judge.MODEL
    return _hashlib.sha256(text.encode()).hexdigest()[:16]


def record_measurement(kind: str, score: str, note: str = "") -> None:
    from datetime import date
    data = _json.loads(MEASURED.read_text()) if MEASURED.exists() else {}
    data[kind] = {"fingerprint": fingerprint(kind), "date": str(date.today()), "score": score, "note": note}
    MEASURED.write_text(_json.dumps(data, indent=2) + "\n")
