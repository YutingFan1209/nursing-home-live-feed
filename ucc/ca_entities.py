"""
ucc/ca_entities.py

New California business entities that look like nursing home acquisition
vehicles, from the Secretary of State's free weekly business entity bulk data
(bizfile Online -> Data Requests -> "BE Bulk Order - Weekly Data"; zips hold
Filings, Principals and Agents, asterisk-pipe delimited).

A buyer usually forms an operating company and a property company before a
change of ownership ("Primrose OpCo LLC" + "Primrose PropCo LLC", Los
Angeles, 2026-08-17, "healthcare assisted living facility"). Their names have
no nursing words and aren't CMS names yet, so a UCC-1 against them would be
missed. Entities are sorted into two tiers:

- watch: a nursing name (SNF, skilled, post-acute, nursing center ...), or an
  OpCo/PropCo/leasing/master-tenant name whose stated type of business is a
  care facility. ucc/ca_download.py treats these as nursing home debtors.
- review: an OpCo/PropCo/leasing/master-tenant name with no type of business
  ("LaHabra OpCo BSD LLC"). Listed for a person; not matched, since most such
  names are other industries (a haircare brand, apartment buyers).

Professional nursing corporations, staffing, home care and hospice are left
out, and so are care facilities without an acquisition-style name: most are
six-bed board-and-care homes (RCFEs), which are assisted living.
"""
from __future__ import annotations

import io
import logging
import os
import re
import zipfile
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)

_NURSING_NAME = re.compile(
    r"\b(SNF|SKILLED|POST[- ]?ACUTE|CONVALESCENT|NURSING (CENTER|HOME|FACILITY)|NURSING (AND|&) REHAB\w*|"
    r"REHABILITATION CENTER|HEALTH ?CARE CENTER|CARE CENTER)\b"
)
_SHELL_NAME = re.compile(r"\b(OPCO|PROPCO|LEASING CO|MASTER TENANT)\b")
_CARE_BUSINESS = re.compile(
    r"\b(SKILLED NURSING|NURSING (HOME|FACILITY)|SNF|POST[- ]?ACUTE|LONG[- ]TERM CARE|CONVALESCENT|"
    r"ASSISTED LIVING|SENIOR LIVING|MEMORY CARE|RCFE|RESIDENTIAL CARE|HEALTH ?CARE|CARE FACILITY)\b"
)
_NOT = re.compile(
    r"\b(NURSING CORP\w*|PROFESSIONAL|PRACTITIONER|STAFFING|ANESTHESIA|PC|P C|SCHOOL|EDUCATION|ACADEMY|"
    r"HOME CARE|HOME HEALTH|HOSPICE|IN-HOME|NON-MEDICAL|TRADES)\b"
)


def is_be_zip(path: str) -> bool:
    with zipfile.ZipFile(path) as zf:
        return "Principals.csv" in zf.namelist()


def _rows(text: str):
    lines = text.splitlines()
    head = lines[0].split("*|*")
    for line in lines[1:]:
        yield dict(zip(head, (v.strip("*").strip() for v in line.split("*|*"))))


def _read(path: str, name: str) -> str:
    with zipfile.ZipFile(path) as zf, zf.open(name) as fh:
        return io.TextIOWrapper(fh, encoding="utf-8", errors="replace").read()


def _date(s: str):
    try:
        return datetime.strptime(s, "%m/%d/%Y").date()
    except ValueError:
        return None


def classify(name: str, business: str) -> tuple[str, str] | None:
    name, business = name.upper(), business.upper()
    if _NOT.search(name) or _NOT.search(business):
        return None
    if _NURSING_NAME.search(name):
        return "watch", "nursing name"
    if _SHELL_NAME.search(name):
        if _CARE_BUSINESS.search(business):
            return "watch", "OpCo/PropCo name, care business"
        if not business:
            return "review", "OpCo/PropCo name, no business type"
    return None


def read_week(path: str) -> list[dict]:
    managers = defaultdict(list)
    for r in _rows(_read(path, "Principals.csv")):
        who = r.get("ORG_NAME") or " ".join(p for p in (r.get("FIRST_NAME"), r.get("LAST_NAME")) if p)
        if who:
            managers[r["ENTITY_NUM"]].append(f"{who} ({r['POSITION_TYPE']})" if r.get("POSITION_TYPE") else who)
    out = []
    for r in _rows(_read(path, "Filings.csv")):
        if r.get("ENTITY_TYPE", "").startswith("Name Reservation"):
            continue
        hit = classify(r["ENTITY_NAME"], r.get("TYPE_OF_BUSINESS", ""))
        if hit:
            out.append({
                "entity_num": r["ENTITY_NUM"], "entity_name": r["ENTITY_NAME"],
                "filed_on": _date(r.get("INITIAL_FILING_DATE", "")), "entity_type": r.get("ENTITY_TYPE"),
                "type_of_business": r.get("TYPE_OF_BUSINESS") or None,
                "principal_city": r.get("PRINCIPAL_CITY"), "principal_state": r.get("PRINCIPAL_STATE"),
                "managers": "; ".join(dict.fromkeys(managers.get(r["ENTITY_NUM"], []))) or None,
                "tier": hit[0], "reason": hit[1], "source_file": os.path.basename(path),
            })
    return out


def save(conn, entities: list[dict]) -> list[dict]:
    """Insert entities not seen before; returns the new ones."""
    new = []
    with conn.cursor() as cur:
        for e in entities:
            cur.execute(f"""
                INSERT INTO ca_entity_watch ({", ".join(e)}) VALUES ({", ".join(["%s"] * len(e))})
                ON CONFLICT (entity_num) DO NOTHING RETURNING entity_num""", list(e.values()))
            if cur.fetchone():
                new.append(e)
    conn.commit()
    return new


def watch_names(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT entity_name FROM ca_entity_watch WHERE tier = 'watch'")
        return [r[0] for r in cur.fetchall()]
