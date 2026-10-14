"""
ucc/ca_download.py

California UCC filings from the Secretary of State's weekly bulk data, not a
name search. The name search throttles a session after ~55 searches, even in
a person's own Chrome (2026-10-14). The weekly download is free: bizfile
Online, logged in -> Data Requests -> + New Data Request -> "UCC Bulk Order -
Weekly Data & Images" -> Data, one week (bizfile Online manual section 19).
A master unload of all filings is $100. There is no download API, so a
person requests and downloads each week's zip.

Each zip holds pipe-delimited, quoted CSVs keyed by UCC1_NUM: Filings
(one row per UCC-1 and per amendment, UCC3_NUM = UCC1_NUM on the original),
Debtors, SecuredParties and FilingAmendments. About 12,800 original UCC-1s a
week statewide (2026-10).

Same selection as FL (ucc/fl_download.py): an original UCC-1 is kept when a
debtor looks like a nursing home entity (CMS owner or facility name, CA CHOW
buyer, CA deal name, or nursing words, strict), plus other filings made the
same day with the same nursing home lender (a portfolio's property
companies). State tax and judgment liens and amendments are skipped.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from collections import defaultdict
from datetime import datetime

from ucc.base import UCCFiling
from ucc.fl_download import _is_lender
from ucc.lender_search import looks_like_nursing_home, norm_name

logger = logging.getLogger(__name__)

PORTAL_URL = "https://bizfileonline.sos.ca.gov/search/ucc"

# Debtors that match a CMS owner name but aren't homes: hospital systems
# (Adventist Health System/West, Sutter Health, PIH Health -- their filings
# were Roche, Dext Capital and drug-wholesaler equipment loans) and banks
# named as debtor (Wells Fargo, BMO Bank N.A.) in the first nine weeks
_NOT_A_HOME = re.compile(
    r"\b(BANK|WELLS FARGO|HEALTH SYSTEM|HOSPITAL\w*|HEALTH ASSOCIATION|ANESTHESIA|CONSULTING)\b"
    r"|\bHEALTH(,? INC\.?)?$"
)


def _parse_date(s: str):
    try:
        return datetime.strptime((s or "").strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _rows(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as fh:
        yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"), delimiter="|")


def read_week(path: str) -> tuple[dict, dict, dict]:
    """filings: UCC1_NUM -> Filings row (original UCC-1s only); debtors and
    secureds: UCC1_NUM -> organization names."""
    with zipfile.ZipFile(path) as zf:
        filings = {
            r["UCC1_NUM"]: r for r in _rows(zf, "Filings.csv")
            if r["UCC1_NUM"] == r["UCC3_NUM"] and r["ACTION_TYPE"] == "Lien Financing Stmt"
            and r["FILING_TYPE_ID"] == "UCC"
        }
        debtors, secureds = defaultdict(list), defaultdict(list)
        for r in _rows(zf, "Debtors.csv"):
            # Individuals can't be nursing home entities
            if r["UCC1_NUM"] in filings and r["DEBTOR_TYPE"] == "Organization" and r["ORG_NAME"].strip():
                debtors[r["UCC1_NUM"]].append(r["ORG_NAME"].strip())
        for r in _rows(zf, "SecuredParties.csv"):
            if r["UCC1_NUM"] in filings:
                name = r["ORG_NAME"].strip() or " ".join(
                    p for p in (r["FIRST_NAME"], r["LAST_NAME"]) if p.strip()).strip()
                if name:
                    secureds[r["UCC1_NUM"]].append(name)
    return filings, debtors, secureds


def select_filings(filings: dict, debtors: dict, secureds: dict, names: set[str]) -> list[UCCFiling]:
    def home(d):
        return looks_like_nursing_home(d, names, strict=True) and not _NOT_A_HOME.search(d.upper())

    def nursing(num):
        return any(home(d) for d in debtors.get(num, []))

    keep = {num for num in filings if nursing(num)}
    by_lender_day = defaultdict(set)
    for num, row in filings.items():
        for sp in secureds.get(num, []):
            if _is_lender(sp):
                by_lender_day[(norm_name(sp), row["FILING_DATE"][:10])].add(num)
    for nums in by_lender_day.values():
        if nums & keep:
            keep |= nums

    out = []
    for num in sorted(keep):
        row = filings[num]
        debs = debtors.get(num, [])
        if not debs:
            # Same-day filings by the lender against people only (guarantors)
            continue
        primary = next((d for d in debs if home(d)), debs[0])
        sps = secureds.get(num, [])
        out.append(UCCFiling(
            state="CA",
            debtor_name=primary,
            secured_party_name=sps[0] if sps else "",
            filing_number=num,
            filing_date=_parse_date(row["FILING_DATE"]),
            lapse_date=_parse_date(row.get("LAPSE_DATE")),
            filing_type="UCC",
            status="active",
            source_url=PORTAL_URL,
            co_debtors=[d for d in debs if d != primary],
            raw={"query_name": "CA weekly file", "secured_parties": sps},
        ))
    return out


def load_week(path: str, names: set[str]) -> list[UCCFiling]:
    filings, debtors, secureds = read_week(path)
    kept = select_filings(filings, debtors, secureds, names)
    logger.info(f"CA UCC {path}: {len(filings)} UCC-1s -> {len(kept)} nursing home")
    return kept
