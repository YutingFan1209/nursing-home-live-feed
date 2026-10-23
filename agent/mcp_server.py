"""
The resolver's tools as an MCP server, so any MCP client (Claude Desktop,
Claude Code) can ask the same questions the agent asks:

    "Who owns the nursing home behind this PA filing, and was the loan
     around the time the owner changed?"

Same ToolBox, same read-only connection, same timing rule: the agent and a
person using Claude Desktop see identical tool results. Unlike the agent, here
the *client* owns the loop, so there is no trace or verification step; the
tool descriptions and server instructions carry the domain rules instead.

Run (stdio):  venv/bin/python3 -m agent.mcp_server

Claude Desktop (claude_desktop_config.json), so imports and .env resolve:
  "nursing-home-ownership": {
    "command": "/bin/sh",
    "args": ["-c", "cd /ABSOLUTE/PATH/nursing-home-live-feed && venv/bin/python3 -m agent.mcp_server"]
  }

Needs `pip install -r agent/requirements.txt`. Written for the MCP Python SDK 2.x
(MCPServer); falls back to the 1.x name (FastMCP).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

try:                                              # SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:                               # SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server
from mcp.types import ToolAnnotations

from agent.tools import TOOL_SCHEMAS

DOCS = Path(__file__).resolve().parent.parent / "docs"
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
# Same, but the tool reads outside websites (public assessor records), not only our database
READ_ONLY_WEB = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

INSTRUCTIONS = """Read-only lookups over a research tracker of acquisitions of U.S. skilled nursing \
facilities (CMS data, UCC-1 financing statements, news/CHOW/SEC deals). Search the distinctive core of a \
name, not the full LLC string. get_facility_ownership(ccn, filing_date) applies the tracker's timing rule: \
CMS owner data lags real sales by months, so a null cms_signal means 'CMS can't say', not 'refinancing'. \
Cite what the tools return; do not fill gaps from background knowledge. Readers are scholars and policy \
staff: say 'can't tell' rather than guess."""


def _desc(name: str) -> str:
    return next(t["description"] for t in TOOL_SCHEMAS if t["name"] == name)


def build_server(toolbox) -> "_Server":
    server = _Server("nursing-home-ownership", instructions=INSTRUCTIONS)

    @server.tool(description=_desc("search_cms_facilities"), annotations=READ_ONLY)
    def search_cms_facilities(name: str, state: Optional[str] = None) -> dict:
        return toolbox.call("search_cms_facilities", {"name": name, "state": state})

    @server.tool(description=_desc("search_cms_owners"), annotations=READ_ONLY)
    def search_cms_owners(owner_name: str, state: Optional[str] = None) -> dict:
        return toolbox.call("search_cms_owners", {"owner_name": owner_name, "state": state})

    @server.tool(description=_desc("search_chow_records"), annotations=READ_ONLY)
    def search_chow_records(name: str, state: Optional[str] = None) -> dict:
        return toolbox.call("search_chow_records", {"name": name, "state": state})

    @server.tool(description=_desc("search_cms_by_address"), annotations=READ_ONLY)
    def search_cms_by_address(address: str, state: Optional[str] = None) -> dict:
        return toolbox.call("search_cms_by_address", {"address": address, "state": state})

    @server.tool(description=_desc("get_facility_ownership"), annotations=READ_ONLY)
    def get_facility_ownership(ccn: str, filing_date: Optional[str] = None) -> dict:
        return toolbox.call("get_facility_ownership", {"ccn": ccn, "filing_date": filing_date})

    @server.tool(description=_desc("get_related_ucc_filings"), annotations=READ_ONLY)
    def get_related_ucc_filings(debtor_name: str, state: Optional[str] = None) -> dict:
        return toolbox.call("get_related_ucc_filings", {"debtor_name": debtor_name, "state": state})

    @server.tool(description=_desc("search_deals"), annotations=READ_ONLY)
    def search_deals(name: str, state: Optional[str] = None, include_ucc_signals: bool = False) -> dict:
        return toolbox.call("search_deals", {"name": name, "state": state, "include_ucc_signals": include_ucc_signals})

    @server.tool(description=_desc("classify_lender"), annotations=READ_ONLY)
    def classify_lender(name: str, state: Optional[str] = None) -> dict:
        return toolbox.call("classify_lender", {"name": name, "state": state})

    @server.tool(description=_desc("lookup_property_owner"), annotations=READ_ONLY_WEB)
    def lookup_property_owner(ccn: str) -> dict:
        return toolbox.call("lookup_property_owner", {"ccn": ccn})

    @server.tool(description="Fetch one UCC filing from our database by state and filing number.", annotations=READ_ONLY)
    def get_ucc_filing(state: str, filing_number: str) -> dict:
        return toolbox.call("get_ucc_filing", {"state": state, "filing_number": filing_number})

    @server.resource("nursing-home://methodology", name="methodology", mime_type="text/markdown",
                     description="How the tracker's data is built and what its labels mean.")
    def methodology() -> str:
        return (DOCS / "methodology.md").read_text()

    @server.resource("nursing-home://how-it-works", name="how-it-works", mime_type="text/markdown",
                     description="Plain-language overview of sources, limits and confidence levels.")
    def how_it_works() -> str:
        return (DOCS / "how-it-works.md").read_text()

    return server


def main() -> None:
    from agent.tools import ToolBox
    build_server(ToolBox.from_config()).run()   # stdio; never print to stdout here


if __name__ == "__main__":
    main()
