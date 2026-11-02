"""
The resolver's tools as an MCP server, so any MCP client (Claude Desktop,
Claude Code) can ask the same questions the agent asks:

    "Who owns the nursing home behind this PA filing, and was the loan
     around the time the owner changed?"

Same ToolBox, same read-only connection, same timing rule: the agent and a
person using Claude Desktop see identical tool results (agent/tests/test_mcp_parity.py
checks this through a real MCP session). Unlike the agent, here the *client* owns the
loop, so there is no trace or verification step; the tool descriptions, server
instructions and the resolve_ucc_filing prompt carry the domain rules instead.

What it serves:
  tools       the ToolBox's 10 read-only tools, each with a typed outputSchema (agent/mcp_models.py)
  prompts     resolve_ucc_filing (the agent's own method), ownership_history
  resources   two docs, plus templates nursing-home://facility/{ccn} and
              nursing-home://ucc/{state}/{filing_number}

Run (stdio):  venv/bin/python3 -m agent.mcp_server
Run (HTTP):   venv/bin/python3 -m agent.mcp_server --http [--port 8765]
              → http://127.0.0.1:8765/mcp, Streamable HTTP. Loopback only: the server has no
              auth, so it refuses any other host, and the SDK's DNS-rebinding check is on.

Claude Desktop: install the .mcpb built by `venv/bin/python3 -m agent.mcpb.build`, or by
hand in claude_desktop_config.json, so imports and .env resolve:
  "nursing-home-ownership": {
    "command": "/bin/sh",
    "args": ["-c", "cd /ABSOLUTE/PATH/nursing-home-live-feed && venv/bin/python3 -m agent.mcp_server"]
  }

Needs `pip install -r agent/requirements.txt`. Written for the MCP Python SDK 2.x
(MCPServer); falls back to the 1.x name (FastMCP).
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

try:                                              # SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:                               # SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server
from mcp.types import ToolAnnotations

from agent import mcp_models as m
from agent.prompts import SYSTEM_PROMPT, build_user_prompt
from agent.tools import TOOL_SCHEMAS

VERSION = "0.2.0"
DOCS = Path(__file__).resolve().parent.parent / "docs"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
# Same, but the tool reads outside websites (public assessor records), not only our database
READ_ONLY_WEB = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

INSTRUCTIONS = """Read-only lookups over a research tracker of acquisitions of U.S. skilled nursing \
facilities (CMS data, UCC-1 financing statements, news/CHOW/SEC deals). Search the distinctive core of a \
name, not the full LLC string. get_facility_ownership(ccn, filing_date) applies the tracker's timing rule: \
CMS owner data lags real sales by months, so a null cms_signal means 'CMS can't say', not 'refinancing'. \
Cite what the tools return; do not fill gaps from background knowledge. Readers are scholars and policy \
staff: say 'can't tell' rather than guess. To resolve one UCC filing the way the tracker's agent does, \
use the resolve_ucc_filing prompt."""

# The agent's prompt speaks of its trace step ids and its submit_verdict tool, neither of
# which an MCP client has; everything else carries over word for word.
_AGENT_ONLY = {
    "Every claim in your verdict cites the step_id of the result that shows it.":
        "Every claim in your verdict names the tool call whose result shows it.",
    "Finish by calling submit_verdict exactly once.":
        "Finish with a verdict: category, CCN (or none), owner entity, confidence (low/medium/high), "
        "link_basis (direct or name_similarity), needs_human_review, and the tool result behind each claim.",
}


def _client_method() -> str:
    text = SYSTEM_PROMPT
    for agent_line, client_line in _AGENT_ONLY.items():
        assert agent_line in text, f"agent/prompts.py no longer says {agent_line!r}; update _AGENT_ONLY"
        text = text.replace(agent_line, client_line)
    return text


def _desc(name: str) -> str:
    return next(t["description"] for t in TOOL_SCHEMAS if t["name"] == name)


def build_server(toolbox) -> "_Server":
    server = _Server("nursing-home-ownership", title="Nursing Home Ownership", version=VERSION,
                     instructions=INSTRUCTIONS,
                     website_url="https://yutingfan1209.github.io/nursing-home-live-feed/")

    def call(name: str, args: dict) -> dict:
        return toolbox.call(name, args)

    # ── tools: return annotations become each tool's outputSchema ──
    @server.tool(title="Search CMS facilities", description=_desc("search_cms_facilities"), annotations=READ_ONLY)
    def search_cms_facilities(name: str, state: Optional[str] = None) -> m.FacilitySearch:
        return m.FacilitySearch.model_validate(call("search_cms_facilities", {"name": name, "state": state}))

    @server.tool(title="Search CMS owners", description=_desc("search_cms_owners"), annotations=READ_ONLY)
    def search_cms_owners(owner_name: str, state: Optional[str] = None) -> m.OwnerSearch:
        return m.OwnerSearch.model_validate(call("search_cms_owners", {"owner_name": owner_name, "state": state}))

    @server.tool(title="Search change-of-ownership records", description=_desc("search_chow_records"),
                 annotations=READ_ONLY)
    def search_chow_records(name: str, state: Optional[str] = None) -> m.ChowSearch:
        return m.ChowSearch.model_validate(call("search_chow_records", {"name": name, "state": state}))

    @server.tool(title="Find facilities at an address", description=_desc("search_cms_by_address"),
                 annotations=READ_ONLY)
    def search_cms_by_address(address: str, state: Optional[str] = None) -> m.AddressSearch:
        return m.AddressSearch.model_validate(call("search_cms_by_address", {"address": address, "state": state}))

    @server.tool(title="Facility ownership and timing", description=_desc("get_facility_ownership"),
                 annotations=READ_ONLY)
    def get_facility_ownership(ccn: str, filing_date: Optional[str] = None) -> m.FacilityOwnership:
        return m.FacilityOwnership.model_validate(
            call("get_facility_ownership", {"ccn": ccn, "filing_date": filing_date}))

    @server.tool(title="Related UCC filings", description=_desc("get_related_ucc_filings"), annotations=READ_ONLY)
    def get_related_ucc_filings(debtor_name: str, state: Optional[str] = None) -> m.RelatedFilings:
        return m.RelatedFilings.model_validate(
            call("get_related_ucc_filings", {"debtor_name": debtor_name, "state": state}))

    @server.tool(title="Search tracked deals", description=_desc("search_deals"), annotations=READ_ONLY)
    def search_deals(name: str, state: Optional[str] = None, include_ucc_signals: bool = False) -> m.DealSearch:
        return m.DealSearch.model_validate(
            call("search_deals", {"name": name, "state": state, "include_ucc_signals": include_ucc_signals}))

    @server.tool(title="Classify a lender", description=_desc("classify_lender"), annotations=READ_ONLY)
    def classify_lender(name: str, state: Optional[str] = None) -> m.LenderClass:
        return m.LenderClass.model_validate(call("classify_lender", {"name": name, "state": state}))

    @server.tool(title="Recorded property owner", description=_desc("lookup_property_owner"),
                 annotations=READ_ONLY_WEB)
    def lookup_property_owner(ccn: str) -> m.PropertyOwner:
        return m.PropertyOwner.model_validate(call("lookup_property_owner", {"ccn": ccn}))

    @server.tool(title="Get one UCC filing",
                 description="Fetch one UCC filing from our database by state and filing number.",
                 annotations=READ_ONLY)
    def get_ucc_filing(state: str, filing_number: str) -> m.OneFiling:
        return m.OneFiling.model_validate(call("get_ucc_filing", {"state": state, "filing_number": filing_number}))

    # ── prompts ──
    @server.prompt(title="Resolve a UCC filing",
                   description="The tracker agent's own method, applied to one filing from our database: "
                               "which nursing home it relates to, who owns it, and what kind of loan it is.")
    def resolve_ucc_filing(state: str, filing_number: str) -> str:
        filing = call("get_ucc_filing", {"state": state.upper(), "filing_number": filing_number})["filing"]
        if filing is None:
            return (f"There is no UCC filing {filing_number} from {state.upper()} in the tracker's database. "
                    "Say so, and ask for the state and filing number again.")
        return _client_method() + "\n\n" + build_user_prompt(filing)

    @server.prompt(title="Ownership history of a home",
                   description="Who has owned one nursing home (by CCN) and when, from CMS owner and "
                               "change-of-ownership records and the deals the tracker has.")
    def ownership_history(ccn: str) -> str:
        return (f"Explain who has owned the nursing home with CCN {ccn} and when. Start with "
                f"get_facility_ownership(ccn='{ccn}'), then search_chow_records and search_deals on the "
                "owner and facility names it returns. Give a dated timeline, newest first, and cite the "
                "tool result behind each line. CMS owner data lags real sales by months, so say where the "
                "record may be behind. Use only what the tools return; say 'can't tell' rather than guess.")

    # ── resources: two docs + one template per record type ──
    @server.resource("nursing-home://methodology", name="methodology", mime_type="text/markdown",
                     description="How the tracker's data is built and what its labels mean.")
    def methodology() -> str:
        return (DOCS / "methodology.md").read_text()

    @server.resource("nursing-home://how-it-works", name="how-it-works", mime_type="text/markdown",
                     description="Plain-language overview of sources, limits and confidence levels.")
    def how_it_works() -> str:
        return (DOCS / "how-it-works.md").read_text()

    @server.resource("nursing-home://facility/{ccn}", name="facility", mime_type="application/json",
                     description="One nursing home by CCN: CMS details, owners with start dates, "
                                 "change-of-ownership events.")
    def facility(ccn: str) -> str:
        return get_facility_ownership(ccn).model_dump_json(indent=2)

    @server.resource("nursing-home://ucc/{state}/{filing_number}", name="ucc-filing",
                     mime_type="application/json", description="One UCC filing by state and filing number.")
    def ucc_filing(state: str, filing_number: str) -> str:
        return get_ucc_filing(state.upper(), filing_number).model_dump_json(indent=2)

    return server


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Serve the resolver's read-only tools over MCP.")
    ap.add_argument("--http", action="store_true", help="Streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    if args.http and args.host not in LOOPBACK:
        ap.error("this server has no auth, so it only listens on this machine (127.0.0.1, localhost, ::1)")

    from agent.tools import ToolBox
    server = build_server(ToolBox.from_config())
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run()   # stdio; never print to stdout here


if __name__ == "__main__":
    main()
