"""
The agent loop: Claude picks tools, we run them, log every step, and stop
when it calls submit_verdict. The verdict is then verified (verify.py).

The loop is plain messages.create + tool_use, written out here (rather
than hidden in a framework) so every decision point is visible:
  - which tools the model is offered           -> tools.TOOL_SCHEMAS + schemas.VERDICT_TOOL
  - what it is told about the domain           -> SYSTEM_PROMPT
  - what it sees from each call                -> the JSON built in _tool_result_content
  - when it must stop                          -> max_steps, or submit_verdict
  - what happens when it can't finish          -> an "unresolved" verdict for a person
"""
from __future__ import annotations

import json
import time
from typing import Optional

from agent.audit import apply_audit, run_audit
from agent.schemas import VERDICT_TOOL
from agent.trace import Trace
from agent.verify import verify_verdict

MAX_TOOL_CHARS = 12000

SYSTEM_PROMPT = """You resolve one UCC-1 financing statement for a research tracker of private-equity \
and operator acquisitions of U.S. skilled nursing facilities. The tracker's readers are scholars and \
senators, so a wrong answer is worse than "I can't tell".

A UCC-1 names a debtor (often an LLC named for a facility, a holding company, or a person) and a \
secured party (the lender). Decide:
  1. which CMS-certified facility it relates to (CCN), if any
  2. who the legal-entity owner/operator is
  3. the category:
     - acquisition_financing: a loan to the buyer around closing (CMS shows an owner starting near the filing)
     - refinancing: the owner was already in place well before the filing
     - operations_transfer: the secured party is itself a nursing home operator (an incoming operator taking security: a sale signal)
     - financing: a nursing home loan, but timing says nothing either way
     - not_a_nursing_home_deal: equipment, vendor, tax or consumer lien
     - unresolved: you cannot tell from the data

Rules:
- Use only what tool results and the filing say. Do not use background knowledge about companies, owners or facility names. Every claim in your verdict cites the step_id of the result that shows it.
- Debtor names are noisy. Search the distinctive core ("Havencrest"), not the full legal string; if the debtor looks like a holding company, search owners.
- A debtor that isn't a current CMS owner may have sold its home: search_chow_records finds it as a seller (a direct tie to that CCN before the sale date). A debtor named for a street address ("5860 College Rd Holdings"): search_cms_by_address. A matching address alone is name_similarity, not direct.
- classify_lender's nursing_home_bank and small_business_or_filing_agent flags are the tracker's own rules: the first is a bank known to lend to nursing homes, the second a lender or filing agent that isn't nursing home deal financing.
- Timing comes from get_facility_ownership(ccn, filing_date). Do not compute dates yourself. CMS owner data lags real sales by months, so a null cms_signal does NOT mean refinancing; say the data cannot show it and lower your confidence.
- Say how the debtor is tied to the facility (link_basis). direct only when a tool result itself shows it. A shared name word, the same lender, or two records sitting side by side is name_similarity, and a person will check it. Being honest here is worth more than a confident answer.
- If several facilities match about equally, do not pick one: submit unresolved with needs_human_review true.
- A debtor that is a person's name: do not try to identify the person beyond the data; use financing or unresolved and set needs_human_review.
- Be economical: usually 3-6 tool calls. Finish by calling submit_verdict exactly once."""


def build_user_prompt(filing: dict) -> str:
    keep = ("state", "filing_number", "filing_date", "filing_type", "debtor_name",
            "secured_party", "collateral_description", "status")
    body = {k: filing.get(k) for k in keep if filing.get(k) not in (None, "")}
    return "Resolve this filing:\n" + json.dumps(body, indent=2, default=str)


def _default_client():
    import anthropic
    from config import get_config
    return anthropic.Anthropic(api_key=get_config().anthropic_api_key)


def _tool_result_content(step_id: str, output, error: Optional[str]) -> str:
    # The model gets the step id back so it can cite it as evidence
    payload = {"step_id": step_id, **({"error": error} if error else {"result": output})}
    text = json.dumps(payload, default=str)
    if len(text) > MAX_TOOL_CHARS:
        text = text[:MAX_TOOL_CHARS] + ' ... [truncated]"}'
    return text


def resolve_filing(filing: dict, toolbox, client=None, model: Optional[str] = None,
                   effort: Optional[str] = None, max_steps: int = 8, max_tokens: int = 4000,
                   audit: bool = False, system_prompt: str = SYSTEM_PROMPT):
    """Returns (verdict, trace). `toolbox` needs .schemas and .call(name, args).
    audit=True adds one fresh-context model call that checks each evidence
    claim against the step it cites (agent/audit.py)."""
    if client is None:
        client = _default_client()
    if model is None or effort is None:
        from config import get_config
        cfg = get_config()
        model, effort = model or cfg.claude_model, effort or cfg.claude_effort

    trace = Trace(filing=filing, model=model)
    tools = list(toolbox.schemas) + [VERDICT_TOOL]
    messages = [{"role": "user", "content": build_user_prompt(filing)}]
    verdict, nudged = None, False

    for _ in range(max_steps):
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, output_config={"effort": effort},
            system=system_prompt, tools=tools, messages=messages,
        )
        trace.add_usage(resp.usage)
        if resp.stop_reason == "refusal":
            trace.add_note("model refused this request")
            break

        for block in resp.content:
            if block.type == "text" and block.text.strip():
                trace.add_thought(block.text)
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        # Hand back the assistant turn exactly as received (keeps thinking blocks intact)
        messages.append({"role": "assistant", "content": resp.content})

        if not tool_uses:
            if nudged:
                trace.add_note("model stopped without submitting a verdict")
                break
            nudged = True
            messages.append({"role": "user", "content": "Call submit_verdict now with your best answer; use unresolved if you cannot tell."})
            continue

        results = []
        for tu in tool_uses:
            if tu.name == "submit_verdict":
                verdict = dict(tu.input)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": "Verdict received."})
                continue
            step_id = trace.new_tool_id()
            t0, out, err = time.perf_counter(), None, None
            try:
                out = toolbox.call(tu.name, dict(tu.input))
            except Exception as e:  # a bad call is information for the model, not a crash
                err = f"{type(e).__name__}: {e}"
            ms = int((time.perf_counter() - t0) * 1000)
            trace.add_tool(step_id, tu.name, dict(tu.input), out, err, ms)
            block = {"type": "tool_result", "tool_use_id": tu.id, "content": _tool_result_content(step_id, out, err)}
            if err:
                block["is_error"] = True
            results.append(block)
        if verdict is not None:
            break
        messages.append({"role": "user", "content": results})

    if verdict is None:
        verdict = {
            "category": "unresolved", "confidence": "low", "needs_human_review": True,
            "facility_name": None, "ccn": None, "owner_entity": None, "evidence": [],
            "reasoning": f"The agent did not submit a verdict within {max_steps} model calls.",
        }
    trace.raw_verdict = dict(verdict)      # kept so verify/audit can be re-scored offline (ablations)
    verified = verify_verdict(verdict, trace)
    # Nothing to audit when the answer is "can't tell" or has no evidence left
    if audit and verified.get("category") != "unresolved" and verified.get("evidence"):
        try:
            trace.audit_answer = run_audit(verified, trace, client, model, effort)
            verified = apply_audit(verified, trace.audit_answer)
        except Exception as e:  # an audit that can't run is a reason for review, not a crash
            trace.add_note(f"audit failed to run: {type(e).__name__}: {e}")
            verified = apply_audit(verified, None)
    trace.verdict = verified
    return trace.verdict, trace
