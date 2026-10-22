"""
Check a verdict against what the tools actually returned, before it is
accepted. The model proposes; this decides what survives.

Reuses pipeline/grounding.py (the same "keep only what the source says"
test the extractor uses), with the filing plus every successful tool result
as the source text. Checks:

  1. facility_name, owner_entity: must appear in the source text, else blanked
  2. ccn: must appear verbatim in a tool result, else blanked
  3. evidence: each step_id must be a real, successful tool step
  4. link: a facility named without a direct tie to the debtor (link_basis other than
     "direct", e.g. only a shared name word) is capped at medium and sent to a person,
     unless the debtor and the facility's licensed company are a property-company /
     operator pair with the same name core ("North Dade SNF Real Estate Company" and
     "North Dade SNF Operating Company"). The rule, not the model, decides that; such a
     link is still capped at medium, and needs_human_review is left as the model set it
  5. timing: acquisition_financing / refinancing must agree with the
     deterministic rule in get_facility_ownership (pipeline/ucc_category.py);
     asserting either without having checked it caps confidence at medium

Anything removed is listed under `ungrounded`; any issue forces
needs_human_review. Nothing here calls a model.
"""
from __future__ import annotations

import json
import re

from agent.schemas import CATEGORIES, CONFIDENCE
from pipeline.grounding import _words, name_in_text

TIMING_CATEGORIES = ("acquisition_financing", "refinancing")

# Words that say which half of a property/operator split an entity is, plus legal
# suffixes. What is left is the name core the two halves share. Longest phrases first.
_ROLE_WORDS = re.compile(
    r"\b(real estate holdings?|real estate company|real estate|realty|propco|property holdings?|property|"
    r"properties|re|holdco|holdings?|landlord|owner|operating company|operations|operating|opco|operator|"
    r"tenant|rehabilitation and healthcare|rehab and healthcare|healthcare|health care|rehabilitation|rehab|"
    r"nursing|skilled nursing|care center|center|"
    r"llc|l l c|inc|corp|corporation|company|co|lp|l p|ltd|the|of|and)\b")


def name_core(name: str) -> str:
    """Name with role words, legal suffixes and bare numbers removed ("Mountain Top 5185 Realty" -> "mountain top")."""
    t = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower().replace("&", " and "))
    t = _ROLE_WORDS.sub(" ", " ".join(t.split()))
    return " ".join(w for w in t.split() if not w.isdigit())


def propco_opco_pair(debtor: str, company: str) -> bool:
    """Debtor and a facility's company differ only in role words: the usual split of
    one home into a property company and an operating company."""
    a, b = name_core(debtor), name_core(company)
    # one short generic word ("bryan", "sunset") is too weak to stand on
    return bool(a) and a == b and (len(a.split()) >= 2 or len(a) >= 6)


def _companies_for(trace, ccn) -> set:
    """Licensed company and owner names that a successful tool result gives for this CCN."""
    out = set()

    def walk(x):
        if isinstance(x, dict):
            if x.get("ccn") == ccn:
                out.update(str(x[k]) for k in ("legal_business_name", "owner_name") if x.get(k))
            for val in x.values():
                walk(val)
        elif isinstance(x, list):
            for val in x:
                walk(val)

    for s in trace.tool_steps():
        if s.error:
            continue
        walk(s.output)
        # get_facility_ownership lists owners under the facility, without a ccn on each row
        if isinstance(s.output, dict) and (s.output.get("facility") or {}).get("ccn") == ccn:
            out.update(str(o["owner_name"]) for o in s.output.get("owners") or [] if o.get("owner_name"))
    return out


def _source_text(trace) -> str:
    parts = [json.dumps(trace.filing, default=str)]
    for s in trace.tool_steps():
        if not s.error:
            parts.append(json.dumps(s.output, default=str))
    return " ".join(parts)


def _timing_signals(trace, ccn) -> list:
    """cms_signal values from get_facility_ownership calls for this CCN."""
    out = []
    for s in trace.tool_steps():
        if s.name == "get_facility_ownership" and not s.error and isinstance(s.output, dict):
            fac = (s.output.get("facility") or {})
            if ccn and fac.get("ccn") == ccn:
                out.append((s.output.get("timing") or {}).get("cms_signal"))
    return out


def verify_verdict(verdict: dict, trace) -> dict:
    v = dict(verdict)
    # What the model asked for, before any change below (the viewer shows it next to the result)
    v["proposed"] = {k: verdict.get(k) for k in
                     ("category", "facility_name", "ccn", "owner_entity", "link_basis", "confidence", "needs_human_review")}
    issues, ungrounded = [], []

    # An empty or cut-off submit_verdict (seen on a real run, OH OH00295739949) is an abstention
    if v.get("category") not in CATEGORIES or v.get("confidence") not in CONFIDENCE:
        issues.append("the verdict was incomplete (no valid category or confidence), treated as unresolved")
        v.update(category="unresolved", ccn=None, facility_name=None, owner_entity=None,
                 reasoning=v.get("reasoning") or "The agent submitted an incomplete verdict.")
    corpus = _source_text(trace)
    words = set(_words(corpus))

    for field in ("facility_name", "owner_entity"):
        val = v.get(field)
        if val and not name_in_text(val, words):
            ungrounded.append(f"{field} {val!r}")
            v[field] = None
    ccn = v.get("ccn")
    if ccn and str(ccn) not in corpus:
        ungrounded.append(f"ccn {ccn!r}")
        v["ccn"] = None
    if ungrounded:
        v["ungrounded"] = ungrounded
        issues.append("not in the filing or any tool result, removed: " + "; ".join(ungrounded))

    good_ids = {s.id for s in trace.tool_steps() if not s.error}
    evidence = v.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = [evidence]
    # the model sometimes returns bare strings instead of {claim, step_id}; those cite nothing
    kept = [e for e in evidence if isinstance(e, dict) and e.get("step_id") in good_ids]
    if len(kept) != len(evidence):
        issues.append(f"{len(evidence) - len(kept)} evidence item(s) cite no step, or one that doesn't exist or failed, removed")
    v["evidence"] = kept
    if v.get("confidence") == "high" and not kept:
        v["confidence"] = "medium"
        issues.append("high confidence with no valid evidence, lowered to medium")

    pair = None
    if v.get("ccn") and v.get("link_basis") != "direct":
        debtors = [d.strip() for d in str(trace.filing.get("debtor_name") or "").split(",") if d.strip()]
        pair = next((c for c in sorted(_companies_for(trace, v["ccn"]))
                     for d in [str(trace.filing.get("debtor_name") or "")] + debtors if propco_opco_pair(d, c)), None)
    if pair:
        v["link_basis"] = "propco_opco_pair"
        v["link_rule"] = f"debtor and {pair!r} share the name core {name_core(pair)!r}"
        if v.get("confidence") == "high":
            v["confidence"] = "medium"
    elif v.get("ccn") and v.get("link_basis") != "direct":
        issues.append("the debtor's link to this facility is not shown directly by a tool result "
                      f"(basis: {v.get('link_basis') or 'not stated'}), so a person should confirm it")
        if v.get("confidence") == "high":
            v["confidence"] = "medium"

    cat = v.get("category")
    if cat in TIMING_CATEGORIES:
        signals = _timing_signals(trace, v.get("ccn"))
        concrete = [s for s in signals if s]
        if not signals:
            issues.append(f"{cat} asserted without checking CMS ownership timing for the CCN")
            if v.get("confidence") == "high":
                v["confidence"] = "medium"
        elif concrete and cat not in concrete:
            issues.append(f"{cat} disagrees with the deterministic timing rule, which said {concrete[0]}")
            v["needs_human_review"] = True
        elif not concrete and v.get("confidence") == "high":
            v["confidence"] = "medium"
            issues.append("CMS timing gave no signal (owner data may lag), high confidence lowered to medium")

    if cat == "unresolved":
        v["confidence"] = "low"            # an abstention is not a claim to be confident in
        v["needs_human_review"] = True
    if ungrounded:
        v["confidence"] = "low"
    if issues:
        v["needs_human_review"] = True
    v["verification"] = {"passed": not issues, "issues": issues}
    return v
