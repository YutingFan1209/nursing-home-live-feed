"""
ucc/lender_search.py

Lender-side UCC search: look up a state's filings by *secured party* for
lenders that mostly finance nursing homes, instead of by debtor names we
already know. Debtor search can only find filings for companies already in
the tracker or the CHOW file; this finds borrowers we've never heard of.

Portals that support it (docs/ucc-feasibility.md): PA (SEARCH_TYPE
SECURED_PARTY, wired up here), CA (one index over debtors and secured
parties), FL, AZ, NC, OR, WV.

A lender search returns every filing that lender has in the state: CIBC
alone has 534 in PA, and HUD 1,879, most of them apartments and old loans.
filter_lender_hits keeps only recent filings whose debtor looks like a
nursing home entity.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, timedelta

from ucc.base import UCCFiling

logger = logging.getLogger(__name__)

# Secured-party name roots. PA matches from the start of the name, so these
# are prefixes ("GREYSTONE" covers "GREYSTONE MONTICELLO FUNDING SH-77 LLC").
# Chosen from the most frequent secured parties on 2023-2026 filings already
# in ucc_filings plus ucc/lender_classifier.py's healthcare lender list.
LENDER_TERMS = [
    # HUD Section 232 (nursing home mortgage insurance) and its lenders
    "SECRETARY OF HOUSING AND URBAN DEVELOPMENT",
    "UNITED STATES DEPARTMENT OF HOUSING",
    "GREYSTONE", "LUMENT", "NEWPOINT", "BERKADIA", "LANCASTER POLLARD",
    "ORIX REAL ESTATE CAPITAL", "DWIGHT CAPITAL",
    # Healthcare bridge and working-capital lenders
    "MONTICELLO", "GMCC", "CAPITAL FUNDING", "CAPITAL FINANCE", "MIDCAP",
    "WHITE OAK HEALTHCARE", "OXFORD FINANCE", "CIBC", "BANKWELL",
    "METROPOLITAN COMMERCIAL BANK",
    # Healthcare REITs (lease and loan collateral)
    "OMEGA HEALTHCARE", "SABRA", "CTW INVESTMENT", "CARETRUST",
    "NATIONAL HEALTH INVESTORS", "LTC PROPERTIES", "WELLTOWER", "VENTAS",
]

# Only recent filings are a current signal (72% of UCC deals on the site
# were from filings over 3 years old as of 2026-10-05)
RECENT_DAYS = 730

_HEALTHCARE_WORDS = re.compile(
    r"\b(NURSING|REHAB\w*|SKILLED|SNF|POST[- ]?ACUTE|CONVALESCENT|HEALTH ?CARE|"
    r"CARE CENTER|LONG[- ]TERM CARE|EXTENDED CARE|OPCO|PROPCO)\b"
)
_SUFFIXES = re.compile(r"\b(LLC|L L C|INC|INCORPORATED|LP|L P|LLP|CORP|CORPORATION|CO|COMPANY|LTD|THE)\b")


def norm_name(name: str) -> str:
    name = re.sub(r"[^A-Z0-9 ]+", " ", (name or "").upper())
    return " ".join(_SUFFIXES.sub(" ", name).split())


# Stricter test for a statewide feed (FL's daily files), where "HEALTH CARE"
# matches every medical practice and "REHAB" every physical therapy or
# addiction clinic: rehab only counts next to a nursing/health/center word,
# and clinic words disqualify a name that has no CMS match.
_STRICT_WORDS = re.compile(
    r"\b(NURSING|SKILLED (NURSING|CARE)|SNF|POST[- ]?ACUTE|CONVALESCENT|LONG[- ]TERM CARE|EXTENDED CARE)\b"
    r"|\bREHAB\w* (CENTER|CTR|AND HEALTH|& HEALTH)"
    r"|\b(HEALTH|HEALTHCARE|HEALTH CARE|NURSING|CARE) (AND|&) REHAB"
)
_NOT_NURSING_HOME = re.compile(
    r"\b(CHIROPRACT\w*|PHYSICAL|THERAP\w*|SPINE|SPORTS|NEURO|ELECTRODIAG\w*|RECOVERY|"
    r"HOSP|HOSPITAL|MEDICAL|DENTAL|PAIN|ORTHO\w*|PROFESSIONAL|ASSISTED LIVING|BEHAVIORAL|"
    r"VETERINAR\w*|ANIMAL|PLLC)\b"
)


def looks_like_nursing_home(debtor: str, cms_names: set[str], strict: bool = False) -> bool:
    if norm_name(debtor) in cms_names:
        return True
    name = debtor.upper()
    if strict:
        return bool(_STRICT_WORDS.search(name)) and not _NOT_NURSING_HOME.search(name)
    return bool(_HEALTHCARE_WORDS.search(name))


def filter_lender_hits(filings: list[UCCFiling], cms_names: set[str], today: date = None) -> list[UCCFiling]:
    """Keep recent filings whose debtor looks like a nursing home entity: a
    CMS owner or facility name (normalized), or nursing words in the name.
    A filing made the same day with the same lender as a qualifying one is
    kept too, which catches a portfolio's property companies ("RICHFIELD 631
    REALTY LLC" filed alongside "ROLLING HILLS REHABILITATION AND
    HEALTHCARE CENTER" by CIBC on 2026-09-03)."""
    since = (today or date.today()) - timedelta(days=RECENT_DAYS)
    recent = [f for f in filings if f.filing_date and f.filing_date >= since and f.debtor_name]
    recent = list({(f.state, f.filing_number): f for f in recent}.values())

    clusters = defaultdict(list)
    for f in recent:
        clusters[(norm_name(f.secured_party_name), f.filing_date)].append(f)

    kept = []
    for members in clusters.values():
        if any(looks_like_nursing_home(f.debtor_name, cms_names) for f in members):
            for f in members:
                f.raw["lender_search"] = True
                kept.append(f)
    logger.info(
        f"Lender search: {len(filings)} filings -> {len(recent)} from the last "
        f"{RECENT_DAYS // 365} years -> {len(kept)} with nursing home debtors"
    )
    return kept
