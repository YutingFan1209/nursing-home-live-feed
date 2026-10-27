"""
A free, deterministic stand-in for the agent: fixed rules over the same
ToolBox calls. It is the floor the agent has to beat on the simulator, and
lets the simulator run without API calls.
"""
from __future__ import annotations

import re

_SUFFIX = re.compile(r"\b(llc|l\.l\.c\.?|inc|lp|corp|pllc|holdings?|operating|operations?|opco|ops|op|company|"
                     r"acquisition|care|realty|snf|rehab|nrsg|group|the)\b\.?", re.I)
_ENTITY = re.compile(r"\b(llc|l\.l\.c|inc|lp|corp|pllc|trust|company)\b", re.I)


def _core(debtor: str) -> str:
    words = [w for w in re.split(r"[\s,]+", _SUFFIX.sub(" ", debtor)) if len(w) > 2]
    return words[0] if words else ""


def resolve(filing: dict, tb) -> dict:
    debtor, state, filed = filing["debtor_name"], filing["state"], filing["filing_date"]
    out = {"category": "unresolved", "ccn": None, "confidence": "low", "needs_human_review": True}

    lender = tb.call("classify_lender", {"name": filing["secured_party"], "state": state})
    if not lender["is_acquisition_relevant"]:
        return {**out, "category": "not_a_nursing_home_deal", "confidence": "medium", "needs_human_review": False}
    if not _ENTITY.search(debtor):                     # a person: never identified beyond the data
        return {**out, "category": "financing"}

    ccn, review = None, False
    if re.match(r"^\d+\s", debtor):
        hits = tb.call("search_cms_by_address", {"address": debtor, "state": state})["matches"]
        if len(hits) == 1 and hits[0]["street_words_matched"]:
            ccn, review = hits[0]["ccn"], True
    else:
        core = _core(debtor)
        hits = [m for m in tb.call("search_cms_facilities", {"name": core, "state": state})["matches"]
                if core.lower() in (m["provider_name"] + " " + (m["legal_business_name"] or "")).lower()]
        if len(hits) == 1:
            ccn = hits[0]["ccn"]
        elif not hits:
            sold = {m["ccn"] for m in tb.call("search_chow_records", {"name": core, "state": state})["matches"]}
            ccn = sold.pop() if len(sold) == 1 else None
    if not ccn:
        return out

    signal = tb.call("get_facility_ownership", {"ccn": ccn, "filing_date": filed})["timing"]["cms_signal"]
    return {"category": signal or "financing", "ccn": ccn, "confidence": "medium" if signal else "low",
            "needs_human_review": review}
