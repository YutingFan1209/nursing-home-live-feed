"""
Pack the MCP server (agent/mcp_server.py) as a Claude Desktop extension (.mcpb).

    venv/bin/python3 -m agent.mcpb.build            # → agent/mcpb/dist/nursing-home-ownership.mcpb

Double-clicking the .mcpb in Finder installs it in Claude Desktop, which asks for
the database URL (stored in the OS keychain, never in the bundle). The bundle uses
the uv runtime: Desktop installs mcp/psycopg2/rapidfuzz from pyproject.toml itself,
so nothing compiled ships inside and the person needs no Python of their own.

Unsigned on purpose: mcpb 2.1.2 can't verify any signature (its `verify` calls node-forge's
PKCS#7 check, which throws "not yet implemented", and then reports "unsigned"), and
Desktop treats a self-signed one as unverified. Signing needs a CA-issued code-signing
certificate: `npx @anthropic-ai/mcpb sign <file> --cert cert.pem --key key.pem`.

Packing is gated: agent/tests/test_mcp_parity.py must pass against the tracker database
first (MCP_PARITY_REQUIRED=1, so a skipped check fails the build too).

Only the files the server imports are copied in. The manifest's tool list comes
from TOOL_SCHEMAS, so it can't drift from what the server actually serves.
Packing needs Node (npx); the mcpb CLI version is pinned below.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent.mcp_server import VERSION
from agent.tools import EXTRA_TOOLS, TOOL_SCHEMAS

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
STAGE = HERE / "dist" / "bundle"
OUT = HERE / "dist" / "nursing-home-ownership.mcpb"
MCPB_CLI = "@anthropic-ai/mcpb@2.1.2"

# Everything agent.mcp_server imports, directly or lazily, plus its two resources
FILES = [
    "config.py",
    "agent/__init__.py",
    "agent/mcp_server.py",
    "agent/mcp_models.py",
    "agent/prompts.py",
    "agent/tools.py",
    "agent/property_records.py",
    "pipeline/deal_labels.py",
    "pipeline/ucc_category.py",
    "pipeline/normalizer.py",
    "pipeline/individual_owners.py",
    "pipeline/run_health.py",
    "scraper/chow.py",            # search_chow_records reads (and first downloads) the CMS CHOW file
    "ucc/__init__.py",
    "ucc/lender_classifier.py",
    "docs/methodology.md",
    "docs/how-it-works.md",
]

PYPROJECT = f"""[project]
name = "nursing-home-ownership"
version = "{VERSION}"
description = "Read-only lookups over the nursing home acquisition tracker"
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.2,<3",
    "psycopg2-binary>=2.9",
    "python-dotenv>=1.0",
    "rapidfuzz>=3.0",
    "requests>=2.31",
    "tenacity>=8.0",
]
"""

# uv runs this from the bundle root, so `agent`, `pipeline`, `ucc` and `config` import as in the repo
SERVER = '''from agent.mcp_server import main

main()
'''


def manifest() -> dict:
    tools = [{"name": t["name"], "description": t["description"].split(". ")[0].rstrip(".") + "."}
             for t in TOOL_SCHEMAS]
    tools += [{"name": n, "description": "Fetch one UCC filing from our database by state and filing number."}
              for n in sorted(EXTRA_TOOLS)]
    return {
        "manifest_version": "0.4",
        "name": "nursing-home-ownership",
        "display_name": "Nursing Home Ownership",
        "version": VERSION,
        "description": "Read-only lookups over the nursing home acquisition tracker: CMS owners, "
                       "CHOW records, UCC-1 filings and deals, with typed results.",
        "long_description": "The UCC resolver's tools for Claude Desktop. Every tool reads the tracker's "
                            "Postgres database over a read-only connection; lookup_property_owner also "
                            "reads public county assessor websites.",
        "author": {"name": "YutingFan1209"},
        "icon": "icon.png",
        "homepage": "https://yutingfan1209.github.io/nursing-home-live-feed/",
        "server": {
            "type": "uv",
            "entry_point": "server.py",
            "mcp_config": {
                "command": "uv",
                "args": ["run", "--directory", "${__dirname}", "server.py"],
                "env": {"DATABASE_URL": "${user_config.database_url}"},
            },
        },
        "tools": tools,
        "prompts": [
            {"name": "resolve_ucc_filing", "arguments": ["state", "filing_number"],
             "description": "The tracker agent's method, applied to one UCC filing from the database.",
             "text": "Resolve UCC filing ${arguments.filing_number} from ${arguments.state} the way the "
                     "tracker's agent does."},
            {"name": "ownership_history", "arguments": ["ccn"],
             "description": "Who has owned one nursing home and when.",
             "text": "Explain who has owned the nursing home with CCN ${arguments.ccn} and when."},
        ],
        "user_config": {
            "database_url": {
                "type": "string",
                "title": "Database URL",
                "description": "Postgres connection string for the tracker database, "
                               "e.g. postgresql://user:password@localhost:5432/dbname",
                "required": True,
                "sensitive": True,
            },
        },
        "compatibility": {"platforms": ["darwin", "linux", "win32"], "runtimes": {"python": ">=3.11"}},
        "keywords": ["nursing homes", "CMS", "UCC", "ownership"],
    }


def parity_gate() -> None:
    """Every typed tool result over MCP must equal the direct ToolBox answer, or nothing is packed."""
    res = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          "agent/tests/test_mcp_parity.py"],
                         cwd=ROOT, env={**os.environ, "MCP_PARITY_REQUIRED": "1"})
    if res.returncode != 0:
        sys.exit("MCP parity check failed or couldn't run; not packing. "
                 "Is nh-test-db running and DATABASE_URL set?")


def build() -> Path:
    parity_gate()
    if STAGE.exists():
        shutil.rmtree(STAGE)
    for rel in FILES:
        dest = STAGE / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dest)
    (STAGE / "manifest.json").write_text(json.dumps(manifest(), indent=2) + "\n")
    (STAGE / "pyproject.toml").write_text(PYPROJECT)
    (STAGE / "server.py").write_text(SERVER)
    shutil.copy2(HERE / "icon.png", STAGE / "icon.png")

    subprocess.run(["npx", "-y", MCPB_CLI, "validate", str(STAGE / "manifest.json")], check=True)
    OUT.unlink(missing_ok=True)
    subprocess.run(["npx", "-y", MCPB_CLI, "pack", str(STAGE), str(OUT)], check=True)
    return OUT


if __name__ == "__main__":
    print(build())
