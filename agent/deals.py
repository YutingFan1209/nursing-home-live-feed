"""
Deal mode: the same loop and tools, with a deal from news, EDGAR or a state
notice as the input instead of a UCC filing.

A news story often covers several homes, or none by name ("a 5-community
portfolio in Texas"), so the verdict is a list of facilities, each with its
own tie to the deal, rather than one CCN. The checks are rules, as in verify.py:

  1. each CCN must appear in a tool result, with the facility's name and state
  2. the facility's state must be one of the deal's states (when the deal has any)
  3. its tie to the deal must be shown, not asserted:
       named_in_deal  - the CMS name of the home is in the deal text
       party_is_owner - a buyer/seller/operator the deal names is the home's CMS
                        owner, licensed company or CHOW party (same legal name, or
                        same name core as in verify.propco_opco_pair)
     anything else is name_similarity, capped at medium and sent to a person
  4. evidence must cite real, successful steps
  5. more facilities than the deal says it has goes to a person

A facility that fails 1 or 2 is removed; any issue forces review. No audit pass
yet: agent/audit.py is written for filings.
"""
from __future__ import annotations

import json
from typing import Optional

from agent.loop import _default_client, _model_and_effort, run_loop
from agent.schemas import CONFIDENCE
from agent.verify import name_core
from matcher.ownership import legal_name_key
from pipeline.grounding import _words, name_in_text

OUTCOMES = [
    "linked",                  # one or more named CMS facilities
    "portfolio_not_named",     # a real deal, but the source names no home a tool can find
    "not_a_nursing_home_deal", # assisted living only, hospital, wrong industry
    "unresolved",              # can't tell -- send to a person
]
DEAL_LINK_BASIS = ["named_in_deal", "party_is_owner", "name_similarity"]

DEAL_FIELDS = ("deal_id", "source", "title", "acquiring_entity", "seller_entity", "operator_names",
               "facility_names", "states", "facility_count", "acquisition_date", "deal_value_m",
               "lender", "stage", "summary")

DEAL_SYSTEM_PROMPT = """You link one deal from a research tracker of private-equity and operator \
acquisitions of U.S. skilled nursing facilities to the CMS-certified homes it covers (CCNs). The deal \
was extracted from a news story, an SEC filing or a state notice. The tracker's readers are scholars \
and senators, so a wrong link is worse than none.

Decide which CMS facilities the deal covers, and how each is tied to it:
  - named_in_deal: the deal text names the home and a CMS search finds it in the deal's state
  - party_is_owner: the buyer, seller or operator the deal names is the home's CMS owner, \
licensed company, or a CHOW buyer/seller for that CCN, in a tool result
  - name_similarity: anything weaker (a shared word, the same city). A person will check it.

Rules:
- Use only what the deal and tool results say. No background knowledge about companies or homes. \
Every facility cites the step_id of the result that shows it.
- Search each named home by its distinctive core ("Reche Canyon"), with the deal's state. Then the buyer \
and seller: search_cms_owners, search_chow_records (buyers and sellers of homes, with dates).
- A portfolio the deal does not name home by home: list only homes a tool result ties to a named party \
in the deal's states and around its date; do not list a party's whole chain. If none can be tied, \
use portfolio_not_named.
- Assisted living, memory care, independent living, hospitals and home health are not nursing homes: \
not_a_nursing_home_deal, unless the deal also names a skilled nursing home.
- If several homes match one name about equally, leave it out and set needs_human_review.
- Be economical: usually 3-8 tool calls. Finish by calling submit_verdict exactly once."""

DEAL_VERDICT_TOOL = {
    "name": "submit_verdict",
    "description": (
        "Submit your final answer for this deal. Call exactly once, last. Each facility "
        "cites the step_id of a tool result that shows it; list no CCN a tool didn't return."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "enum": OUTCOMES},
            "facilities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ccn": {"type": "string"},
                        "facility_name": {"type": "string", "description": "CMS provider name, as a tool returned it."},
                        "link_basis": {"type": "string", "enum": DEAL_LINK_BASIS},
                        "evidence": {
                            "type": "array",
                            "items": {"type": "object",
                                      "properties": {"claim": {"type": "string"}, "step_id": {"type": "string"}},
                                      "required": ["claim", "step_id"]},
                        },
                    },
                    "required": ["ccn", "facility_name", "link_basis", "evidence"],
                },
            },
            "confidence": {"type": "string", "enum": CONFIDENCE},
            "needs_human_review": {"type": "boolean"},
            "reasoning": {"type": "string", "description": "2-4 sentences a researcher can read."},
        },
        "required": ["outcome", "facilities", "confidence", "needs_human_review", "reasoning"],
    },
}


def deal_record(row: dict) -> dict:
    """A deals row (plus article title / source type) as the agent sees it. state and
    filing_number are set so Trace.save names the file <state>_deal<id>.json."""
    rec = {k: row.get(k) for k in DEAL_FIELDS if row.get(k) not in (None, "", [])}
    rec["state"] = (row.get("states") or ["XX"])[0]
    rec["filing_number"] = f"deal{str(row.get('deal_id'))[:8]}"
    rec["filing_date"] = row.get("acquisition_date")
    rec["debtor_name"] = row.get("acquiring_entity")      # shown by Trace.render_text
    return rec


def build_deal_prompt(deal: dict) -> str:
    body = {k: deal.get(k) for k in DEAL_FIELDS if deal.get(k) not in (None, "", [])}
    return "Link this deal:\n" + json.dumps(body, indent=2, default=str)


def _deal_text(deal: dict) -> str:
    return " ".join(str(deal.get(k)) for k in ("title", "summary", "facility_names", "operator_names",
                                               "acquiring_entity", "seller_entity") if deal.get(k))


def _parties(deal: dict) -> list:
    out = [deal.get("acquiring_entity"), deal.get("seller_entity")] + list(deal.get("operator_names") or [])
    return [str(p) for p in out if p]


def _facts_for(trace, ccn: str) -> dict:
    """What successful tool results say about one CCN: names, states, related companies."""
    facts = {"seen": False, "names": set(), "states": set(), "companies": set()}

    def take(d):
        facts["seen"] = True
        if d.get("provider_name"):
            facts["names"].add(str(d["provider_name"]))
        for k in ("provider_state", "state"):
            if d.get(k):
                facts["states"].add(str(d[k]))
        for k in ("legal_business_name", "owner_name", "buyer", "seller", "buyer_dba", "seller_dba", "buyer_name"):
            if d.get(k):
                facts["companies"].add(str(d[k]))
        for k in ("buyer_dba", "seller_dba"):
            if d.get(k):
                facts.setdefault("dba", set()).add(str(d[k]))

    def walk(x):
        if isinstance(x, dict):
            if x.get("ccn") == ccn:
                take(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for s in trace.tool_steps():
        if s.error:
            continue
        walk(s.output)
        # get_facility_ownership: owners and CHOW events sit under the facility, without a ccn each
        if isinstance(s.output, dict) and (s.output.get("facility") or {}).get("ccn") == ccn:
            for row in (s.output.get("owners") or []) + (s.output.get("chow_events") or []):
                take({k: v for k, v in row.items() if k != "provider_name"})
    if not facts["names"]:
        # a CHOW record names the home only by its doing-business-as name
        facts["names"] = set(facts.get("dba") or ())
    facts.pop("dba", None)
    return facts


def _same_party(a: str, b: str) -> bool:
    if legal_name_key(a) and legal_name_key(a) == legal_name_key(b):
        return True
    ca, cb = name_core(a), name_core(b)
    return bool(ca) and ca == cb and (len(ca.split()) >= 2 or len(ca) >= 6)


def verify_deal_verdict(verdict: dict, trace) -> dict:
    v = dict(verdict)
    deal = trace.filing
    v["proposed"] = {k: verdict.get(k) for k in ("outcome", "facilities", "confidence", "needs_human_review")}
    issues = []

    if v.get("outcome") not in OUTCOMES or v.get("confidence") not in CONFIDENCE:
        issues.append("the verdict was incomplete (no valid outcome or confidence), treated as unresolved")
        v.update(outcome="unresolved", facilities=[], reasoning=v.get("reasoning") or "Incomplete verdict.")

    deal_words = set(_words(_deal_text(deal)))
    deal_states = set(deal.get("states") or [])
    parties = _parties(deal)
    good_ids = {s.id for s in trace.tool_steps() if not s.error}
    kept, seen = [], set()
    facilities = v.get("facilities") or []
    if not isinstance(facilities, list):
        facilities = []
    for f in facilities:
        if not isinstance(f, dict):
            continue
        ccn = str(f.get("ccn") or "")
        facts = _facts_for(trace, ccn) if ccn else None
        if not ccn or not facts["seen"]:
            issues.append(f"CCN {ccn or '(blank)'} is not in any tool result, removed")
            continue
        if ccn in seen:
            continue
        seen.add(ccn)
        if deal_states and facts["states"] and not facts["states"] & deal_states:
            issues.append(f"CCN {ccn} is in {sorted(facts['states'])}, not the deal's states {sorted(deal_states)}, removed")
            continue
        if facts["names"]:
            f = dict(f, facility_name=sorted(facts["names"])[0])   # the CMS name, not the model's spelling
        ev = [e for e in (f.get("evidence") or []) if isinstance(e, dict) and e.get("step_id") in good_ids]
        if len(ev) != len(f.get("evidence") or []):
            issues.append(f"CCN {ccn}: evidence citing no real successful step removed")
        f["evidence"] = ev

        named = any(name_in_text(n, deal_words) for n in facts["names"])
        owner = next((c for c in sorted(facts["companies"]) for p in parties if _same_party(p, c)), None)
        if named:
            f["link_basis"], f["link_rule"] = "named_in_deal", "the CMS name of the home is in the deal text"
        elif owner:
            f["link_basis"], f["link_rule"] = "party_is_owner", f"{owner!r} is a party the deal names"
        else:
            if f.get("link_basis") != "name_similarity":
                issues.append(f"CCN {ccn}: neither its name nor a party the deal names shows in the results "
                              f"(model said {f.get('link_basis')}), so a person should confirm it")
            f["link_basis"] = "name_similarity"
        kept.append(f)

    v["facilities"] = kept
    if v.get("outcome") == "linked" and not kept:
        issues.append("outcome linked, but no facility survived the checks; now unresolved")
        v["outcome"] = "unresolved"
    if kept and v.get("outcome") != "linked":
        issues.append(f"outcome {v.get('outcome')} with facilities listed")
    count = deal.get("facility_count")
    if count and len(kept) > int(count):
        issues.append(f"{len(kept)} facilities listed, the deal says {count}")
    if any(f["link_basis"] == "name_similarity" for f in kept) and v.get("confidence") == "high":
        v["confidence"] = "medium"
    if v.get("outcome") == "unresolved":
        v["confidence"] = "low"
        v["needs_human_review"] = True
    if issues:
        v["needs_human_review"] = True
    v["verification"] = {"passed": not issues, "issues": issues}
    return v


def resolve_deal(deal: dict, toolbox, client=None, model: Optional[str] = None, effort: Optional[str] = None,
                 max_steps: int = 10, max_tokens: int = 4000, system_prompt: str = DEAL_SYSTEM_PROMPT):
    """Returns (verdict, trace) for a deal_record(). Same loop as resolve_filing."""
    client = client or _default_client()
    model, effort = _model_and_effort(model, effort)
    verdict, trace = run_loop(deal, build_deal_prompt(deal), toolbox, client, model, effort,
                              max_steps, max_tokens, system_prompt, verdict_tool=DEAL_VERDICT_TOOL)
    if verdict is None:
        verdict = {"outcome": "unresolved", "facilities": [], "confidence": "low", "needs_human_review": True,
                   "reasoning": f"The agent did not submit a verdict within {max_steps} model calls."}
    trace.raw_verdict = dict(verdict)
    trace.verdict = verify_deal_verdict(verdict, trace)
    return trace.verdict, trace


def sample_deals(conn, n: int, source: Optional[str] = None, ids: Optional[list] = None) -> list:
    """Non-UCC, non-CMS deals the matcher never tied to a CCN (or the given deal ids)."""
    from psycopg2.extras import RealDictCursor
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT d.id AS deal_id, s.source_type AS source, a.title, d.acquiring_entity, d.seller_entity,
                   d.operator_names, d.facility_names, d.states, d.facility_count,
                   d.acquisition_date::text AS acquisition_date, d.deal_value_m, d.lender, d.stage, d.summary
            FROM deals d
            LEFT JOIN articles a ON a.id = d.article_id
            LEFT JOIN sources s ON s.id = a.source_id
            WHERE (%(ids)s::text[] IS NOT NULL AND d.id::text = ANY(%(ids)s::text[]))
               OR (%(ids)s::text[] IS NULL
                   AND d.stage <> 'dismissed' AND d.merged_into IS NULL
                   AND COALESCE(d.extraction_model, '') NOT IN ('ucc_filing', 'chow_direct', 'cms_owner_change')
                   AND NOT EXISTS (SELECT 1 FROM cms_matches m WHERE m.deal_id = d.id)
                   AND (%(src)s::text IS NULL OR s.source_type = %(src)s))
            ORDER BY random() LIMIT %(n)s
        """, {"ids": ids, "src": source, "n": n})
        return [deal_record(dict(r)) for r in cur.fetchall()]
