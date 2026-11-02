"""
A ToolBox whose every call goes through a live MCP server, so the resolver agent
can be run end to end over MCP:

    venv/bin/python3 -m agent.run --filings agent/traces/holdout/holdout.csv --audit --via-mcp \
        --model claude-sonnet-5-5 --out agent/traces/holdout/run_mcp

It launches the server as a subprocess over stdio (`python -m agent.mcp_server`, or a
built bundle with `uv run`), exactly as Claude Desktop would, and offers the agent the
tools as the server lists them: MCP's own input schemas and descriptions, not
TOOL_SCHEMAS. Results come back as each tool's typed structuredContent. Same
interface as ToolBox (.schemas, .call), so agent.loop needs no change.

The MCP client is async and the agent loop is not, so the session lives on its own
event loop in a background thread and each call is handed to it.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from agent.tools import EXTRA_TOOLS, TOOL_SCHEMAS

ROOT = Path(__file__).resolve().parent.parent
CALL_TIMEOUT_S = 120      # search_chow_records downloads the CMS CHOW file on its first call


class MCPToolBox:
    def __init__(self, bundle_dir: Optional[str] = None):
        from mcp import StdioServerParameters
        env = {k: os.environ[k] for k in ("DATABASE_URL", "PATH", "HOME") if k in os.environ}
        if bundle_dir:      # an unpacked .mcpb, run the way Desktop runs it
            self.params = StdioServerParameters(command="uv", args=["run", "--directory", bundle_dir, "server.py"],
                                                env=env)
        else:
            self.params = StdioServerParameters(command=sys.executable, args=["-m", "agent.mcp_server"],
                                                env=env, cwd=str(ROOT))
        self._loop = asyncio.new_event_loop()
        self._ready, self._stop = threading.Event(), None
        self._error: Optional[BaseException] = None
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._session_done = asyncio.run_coroutine_threadsafe(self._hold_session(), self._loop)
        self._ready.wait(timeout=CALL_TIMEOUT_S)
        if self._error or not self._ready.is_set():
            raise RuntimeError(f"MCP server didn't start: {self._error}")
        listed = {t.name: t for t in self._run(self._client.list_tools()).tools}
        missing = {t["name"] for t in TOOL_SCHEMAS} - set(listed)
        assert not missing, f"MCP server lacks tools the agent uses: {missing}"
        # the agent's tools in its usual order, as MCP describes them; get_ucc_filing stays off
        # the list, as it does for the agent (the filing is in its prompt)
        self.schemas = [{"name": n, "description": listed[n].description, "input_schema": listed[n].input_schema}
                        for n in (t["name"] for t in TOOL_SCHEMAS) if n not in EXTRA_TOOLS]

    async def _hold_session(self) -> None:
        from mcp import Client
        self._stop = asyncio.Event()
        try:
            async with Client(self.params) as client:
                self._client = client
                self._ready.set()
                await self._stop.wait()
        except BaseException as e:      # surfaced in __init__
            self._error = e
            self._ready.set()

    def _run(self, coro) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=CALL_TIMEOUT_S)

    def call(self, name: str, args: dict) -> Any:
        res = self._run(self._client.call_tool(name, args or {}))
        if res.is_error:
            raise RuntimeError(res.content[0].text if res.content else "MCP tool error")
        return res.structured_content

    def close(self) -> None:
        if self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
            self._session_done.result(timeout=30)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
