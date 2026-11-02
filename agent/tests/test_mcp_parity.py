"""
The MCP server answers exactly what the agent's ToolBox answers.

Replays every distinct tool call the agent made in the frozen holdout runs
(agent/traces/holdout/run_*) twice, against today's database: once straight
through ToolBox.call, once through a real MCP session (in-process client →
server → typed model). Each MCP result must validate against its outputSchema
(mcp_models: extra="forbid", so a key missing from a model fails here) and equal
the direct answer. Free and deterministic: no model calls. lookup_property_owner
is left out, since it reads outside websites.

Needs the tracker database (DATABASE_URL); skips without it. Run:
    venv/bin/python3 -m pytest agent/tests/test_mcp_parity.py
agent/mcpb/build.py runs it with MCP_PARITY_REQUIRED=1, which turns every skip into a
failure: a bundle is never packed on a check that didn't run.
"""
import asyncio
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("mcp")

HOLDOUT = Path(__file__).resolve().parents[1] / "traces" / "holdout"


def _skip(reason: str):
    if os.environ.get("MCP_PARITY_REQUIRED"):
        pytest.fail(f"parity check required but can't run: {reason}")
    pytest.skip(reason)


def _recorded_calls() -> list:
    seen, calls = set(), []
    for path in sorted(HOLDOUT.glob("run_*/*.json")):
        trace = json.loads(path.read_text())
        f = trace["filing"]      # the filing itself, as get_ucc_filing would fetch it
        trace["steps"] = [{"kind": "tool", "name": "get_ucc_filing",
                           "input": {"state": f["state"], "filing_number": f["filing_number"]}}] + trace.get("steps", [])
        for step in trace["steps"]:
            if step.get("kind") != "tool" or step["name"] == "lookup_property_owner" or step.get("error"):
                continue
            key = (step["name"], json.dumps(step["input"], sort_keys=True))
            if key not in seen:
                seen.add(key)
                calls.append((step["name"], step["input"]))
    return calls


@pytest.fixture(scope="module")
def toolbox():
    from agent.tools import ToolBox
    try:
        return ToolBox.from_config()
    except Exception as e:  # no database here
        _skip(f"tracker database not reachable: {e}")


def test_every_recorded_call_matches_through_mcp(toolbox):
    from mcp import Client
    from agent.mcp_server import build_server

    calls = _recorded_calls()
    if not calls:      # agent/traces/holdout is git-ignored (debtor names)
        _skip("no holdout traces in this checkout")
    server = build_server(toolbox)
    tools = {t.name: t for t in asyncio.run(server.list_tools())}

    async def replay():
        mismatches = []
        async with Client(server) as client:
            for name, args in calls:
                res = await client.call_tool(name, args)
                assert not res.is_error, f"{name}({args}): {res.content[0].text[:300]}"
                # what came over MCP is the direct answer, typed by the tool's own model
                if res.structured_content != _typed(name, toolbox.call(name, args)):
                    mismatches.append((name, args))
        return mismatches

    def _typed(name, direct):
        out_schema = tools[name].output_schema
        assert out_schema and out_schema.get("type") == "object", f"{name} has no outputSchema"
        return _MODELS[name].model_validate(direct).model_dump(mode="json")

    assert asyncio.run(replay()) == []


from agent import mcp_models as _m  # noqa: E402

_MODELS = {
    "search_cms_facilities": _m.FacilitySearch, "search_cms_owners": _m.OwnerSearch,
    "search_chow_records": _m.ChowSearch, "search_cms_by_address": _m.AddressSearch,
    "get_facility_ownership": _m.FacilityOwnership, "get_related_ucc_filings": _m.RelatedFilings,
    "search_deals": _m.DealSearch, "classify_lender": _m.LenderClass, "get_ucc_filing": _m.OneFiling,
    "lookup_property_owner": _m.PropertyOwner,
}


def test_agent_toolbox_over_stdio_matches_direct(toolbox):
    """The MCPToolBox that `agent.run --via-mcp` uses: a real server subprocess over stdio."""
    from agent.mcp_toolbox import MCPToolBox

    calls = [c for c in _recorded_calls() if c[0] != "get_ucc_filing"][:25]
    if not calls:
        _skip("no holdout traces in this checkout")
    mcp_tb = MCPToolBox()
    try:
        assert [s["name"] for s in mcp_tb.schemas] == [s["name"] for s in toolbox.schemas]
        for name, args in calls:
            assert mcp_tb.call(name, args) == _MODELS[name].model_validate(toolbox.call(name, args)).model_dump(mode="json")
    finally:
        mcp_tb.close()
