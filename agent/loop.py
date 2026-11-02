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
from agent.prompts import SYSTEM_PROMPT, build_user_prompt  # noqa: F401 (re-exported for agent.run)
from agent.schemas import VERDICT_TOOL
from agent.trace import Trace
from agent.verify import verify_verdict

MAX_TOOL_CHARS = 12000


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


def _model_and_effort(model, effort):
    if model is None or effort is None:
        from config import get_config
        cfg = get_config()
        model, effort = model or cfg.claude_model, effort or cfg.claude_effort
    return model, effort


def run_loop(record: dict, user_prompt: str, toolbox, client, model: str, effort: str,
             max_steps: int, max_tokens: int, system_prompt: str, verdict_tool: dict = VERDICT_TOOL):
    """The tool loop itself, shared by filings and deals (agent/deals.py).
    Returns (raw verdict or None, trace); checking it is the caller's job."""
    trace = Trace(filing=record, model=model)
    tools = list(toolbox.schemas) + [verdict_tool]
    messages = [{"role": "user", "content": user_prompt}]
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
            messages.append({"role": "user", "content": f"Call {verdict_tool['name']} now with your best answer; use unresolved if you cannot tell."})
            continue

        results = []
        for tu in tool_uses:
            if tu.name == verdict_tool["name"]:
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
    return verdict, trace


def resolve_filing(filing: dict, toolbox, client=None, model: Optional[str] = None,
                   effort: Optional[str] = None, max_steps: int = 8, max_tokens: int = 4000,
                   audit: bool = False, system_prompt: str = SYSTEM_PROMPT):
    """Returns (verdict, trace). `toolbox` needs .schemas and .call(name, args).
    audit=True adds one fresh-context model call that checks each evidence
    claim against the step it cites (agent/audit.py)."""
    client = client or _default_client()
    model, effort = _model_and_effort(model, effort)
    verdict, trace = run_loop(filing, build_user_prompt(filing), toolbox, client, model, effort,
                              max_steps, max_tokens, system_prompt)

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
