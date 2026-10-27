"""
ucc/mo.py

Missouri UCC debtor search (added 2026-10-27) on the SOS's bsd.sos.mo.gov,
logged in to the person's free account (searching needs one; the person
accepted its UCC Image Access Agreement themselves). No robots.txt; the
account terms ask for use "in a professional manner" and say nothing about
automated searching (checked 2026-10-27). Runs only in the person's own
Chrome (scripts/mo_ucc_browser.py).

The results list gives file number, debtor, address, filing date and lapse
status but no secured party. That is only on the filing's image, a scanned
PDF with no text layer, so the browser runner fetches the image of each
recent filing and the secured party is read from it by Claude here, at
ingest. Searches and images are free; the shopping cart is for certified
copies and isn't used.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime

from ucc.base import UCCFiling
from ucc.wi import search_name  # same "starts with" search, same suffix problem

logger = logging.getLogger(__name__)

SEARCH_PAGE = "https://bsd.sos.mo.gov/LienFiling/LienSearch.aspx"

__all__ = ["SEARCH_PAGE", "search_name", "secured_party_from_pdf", "rows_to_filings"]

_PROMPT = (
    "This is a scanned Missouri UCC financing statement (or amendment). Return only JSON: "
    '{"form": "UCC1" or "UCC3" or other, "secured_party": the secured party\'s name exactly as '
    'written (for a UCC3 assignment, the assignee), "collateral": the collateral description, '
    "at most 300 characters}. Use null for anything not on the form."
)


def secured_party_from_pdf(content: bytes, label: str = "") -> dict:
    """Claude reads the secured party off a filing image. Paid: one short call per filing."""
    from pipeline.pdf_text import client, config
    try:
        response = client.messages.create(
            model=config.claude_model,
            max_tokens=600,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                "data": base64.standard_b64encode(content).decode("ascii")}},
                {"type": "text", "text": _PROMPT},
            ]}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        return json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    except Exception as e:
        logger.warning(f"MO filing image unreadable for {label}: {e}")
        return {}


def _date(s: str):
    try:
        return datetime.strptime((s or "").strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def rows_to_filings(rows: list, query: str) -> list[UCCFiling]:
    """Rows from scripts/mo_ucc_browser_search.js, each already given a
    `parsed` dict by secured_party_from_pdf. Rows without a secured party
    (no image fetched, or unreadable) are skipped: the lender decides
    whether a filing counts."""
    filings = []
    for row in rows or []:
        party = ((row.get("parsed") or {}).get("secured_party") or "").strip()
        if not row.get("fileNo") or not row.get("debtor") or not party:
            continue
        form = ((row.get("parsed") or {}).get("form") or "UCC1").upper()
        filings.append(UCCFiling(
            state="MO",
            debtor_name=row["debtor"],
            secured_party_name=party,
            filing_number=row["fileNo"],
            filing_date=_date(row.get("date")),
            filing_type="UCC-3" if form.startswith("UCC3") else "UCC-1",
            collateral_description=(row.get("parsed") or {}).get("collateral"),
            status="lapsed" if (row.get("status") or "").lower() == "lapsed" else "active",
            source_url=SEARCH_PAGE,
            raw={"query_name": query, "address": row.get("address"), "lapse_status": row.get("status")},
        ))
    return filings
