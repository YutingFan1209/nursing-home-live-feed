"""
Spike: the simulator as a Strands multi-agent graph, to see whether
multi-agent earns its place. Runs offline: both LLM nodes use a scripted
stub model, so it shows the wiring and where data has to flow, not quality.

  red_team (LLM)  proposes harder debtor-name variants of existing cases
  materialize     code: a variant keeps its base case's known answer
  resolve         code: rules resolver on the real ToolBox over nh_sim
  score           code
  critic (LLM)    reads the scores, says "again" or "stop"; loops back to red_team

Strands isn't a project dependency. Run in the scratch venv that has it,
after `venv/bin/python3 -m agent.simulator.run` has loaded nh_sim:
  PYTHONPATH=. <strands-venv>/bin/python -m agent.simulator.strands_spike
"""
from __future__ import annotations

import json
from typing import Any

from strands import Agent, tool
from strands.agent.agent_result import AgentResult
from strands.multiagent import GraphBuilder
from strands.models.model import Model
from strands.telemetry.metrics import EventLoopMetrics

from agent.simulator import rules_resolver
from agent.simulator.load import SIM_DB, _with_db, sim_toolbox
from agent.simulator.score import grade, report, summarize
from agent.simulator.world import Case, build_world


class ScriptedModel(Model):
    """Plays back one scripted turn per call: a tool call, then text."""
    def __init__(self, turns):
        self.turns, self.config = list(turns), {}

    def update_config(self, **kw): self.config.update(kw)
    def get_config(self): return self.config
    def structured_output(self, *a, **k): raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kw):
        kind, payload = self.turns.pop(0)
        yield {"messageStart": {"role": "assistant"}}
        if kind == "tool":
            name, args = payload
            yield {"contentBlockStart": {"start": {"toolUse": {"name": name, "toolUseId": f"t{len(self.turns)}"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": payload}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}, "metrics": {"latencyMs": 0}}}


class CodeNode:
    """A plain function as a graph node (Strands' AgentBase protocol).
    Graph hands nodes only the previous nodes' text; anything structured
    has to travel in invocation_state, which every node shares."""
    def __init__(self, fn):
        self.fn = fn

    async def stream_async(self, prompt=None, invocation_state=None, **kw):
        text = self.fn(invocation_state)
        yield {"result": AgentResult(stop_reason="end_turn", state={}, metrics=EventLoopMetrics(),
                                     message={"role": "assistant", "content": [{"text": text}]})}

    async def invoke_async(self, prompt=None, **kw):
        async for e in self.stream_async(prompt, **kw):
            return e["result"]

    def __call__(self, prompt=None, **kw):
        raise NotImplementedError


def build_graph(state: dict, red_script, critic_script):
    @tool
    def propose_variants(variants: list[dict]) -> str:
        """Propose harder debtor names: [{"case_id": ..., "debtor_name": ...}]. The answer stays the base case's."""
        state["proposals"] = variants
        return f"{len(variants)} variants received"

    @tool
    def decide(again: bool, focus: str = "") -> str:
        """again=true for another red-team round, focused on `focus`."""
        state["again"], state["focus"] = again, focus
        return "noted"

    red = Agent(model=ScriptedModel(red_script), tools=[propose_variants], callback_handler=None,
                system_prompt="Propose debtor-name variants that a resolver would get wrong without changing who the debtor is.")
    critic = Agent(model=ScriptedModel(critic_script), tools=[decide], callback_handler=None,
                   system_prompt="Read the score table and decide whether another round is worth it.")

    def materialize(st):
        base = {c.case_id: c for c in st["world"].cases}
        st["batch"] = [Case(f"{p['case_id']}~v{i}", base[p["case_id"]].kind,
                            {**base[p["case_id"]].filing, "debtor_name": p["debtor_name"]},
                            base[p["case_id"]].ccn_ok, base[p["case_id"]].cat_ok, base[p["case_id"]].review_required)
                       for i, p in enumerate(st.pop("proposals", [])) if p.get("case_id") in base]
        return f"{len(st['batch'])} variant cases built"

    def resolve(st):
        st["results"] = [(c, rules_resolver.resolve(c.filing, st["tb"])) for c in st["batch"]]
        return f"resolved {len(st['results'])}"

    def score(st):
        st["rounds"].append([(c.case_id, c.filing["debtor_name"], grade(v, c)) for c, v in st["results"]])
        return report(summarize(st["results"]))

    b = GraphBuilder()
    b.add_node(red, "red_team")
    b.add_node(CodeNode(materialize), "materialize")
    b.add_node(CodeNode(resolve), "resolve")
    b.add_node(CodeNode(score), "score")
    b.add_node(critic, "critic")
    for a, z in [("red_team", "materialize"), ("materialize", "resolve"), ("resolve", "score"), ("score", "critic")]:
        b.add_edge(a, z)
    b.add_edge("critic", "red_team", condition=lambda gs: state.get("again", False))
    b.set_entry_point("red_team")
    b.reset_on_revisit(True)
    b.set_max_node_executions(12)
    return b.build()


def main() -> int:
    from config import get_config
    w = build_world(7)
    tb = sim_toolbox(_with_db(get_config().database_url, SIM_DB), w)
    by_kind = {}
    for c in w.cases:
        by_kind.setdefault(c.kind, c)
    acq, refi = by_kind["acq_exact"], by_kind["refi_abbrev"]
    core = lambda c: c.filing["debtor_name"].split()[0]
    # What an LLM red team might write: typos, reordering, a dropped core. Scripted here.
    round1 = [{"case_id": acq.case_id, "debtor_name": acq.filing["debtor_name"].upper().replace(" LLC", ", L.L.C.")},
              {"case_id": acq.case_id, "debtor_name": core(acq)[:-1] + " Acquisition LLC"},
              {"case_id": refi.case_id, "debtor_name": "The " + refi.filing["debtor_name"]}]
    round2 = [{"case_id": refi.case_id, "debtor_name": core(refi) + "-" + "Snf Opco LLC"},
              {"case_id": acq.case_id, "debtor_name": "SNF " + acq.filing["debtor_name"]}]
    red_script = [("tool", ("propose_variants", {"variants": round1})), ("text", "round 1 proposed"),
                  ("tool", ("propose_variants", {"variants": round2})), ("text", "round 2 proposed")]
    critic_script = [("tool", ("decide", {"again": True, "focus": "punctuation"})), ("text", "again"),
                     ("tool", ("decide", {"again": False})), ("text", "stop")]

    state = {"world": w, "tb": tb, "rounds": []}
    result = build_graph(state, red_script, critic_script)("Find debtor names the resolver gets wrong.", invocation_state=state)
    print("graph status:", result.status, "| node runs:", [n.node_id for n in result.execution_order])
    for i, rnd in enumerate(state["rounds"], 1):
        print(f"round {i}:")
        for cid, debtor, g in rnd:
            print(f"   {g:<16} {debtor!r:<48} ({cid})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
