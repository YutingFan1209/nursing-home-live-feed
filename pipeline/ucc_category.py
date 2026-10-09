"""
Acquisition financing vs refinancing for UCC deals, and repeats of one loan.

Tyler's call (2026-10-09, docs/tyler-questions.md): keep refinancing UCC
filings but show them as their own category, and don't show the same loan
twice. Derived when deals.json is written (scripts/export_deals.py), so
nothing is stored and the rules can be retuned freely.

Category of a UCC-1 against a company debtor:
  - acquisition_financing: the facility's CMS direct owner (or a CHOW)
    starts within ACQUISITION_WINDOW of the filing -- a loan to the buyer
    around closing (Weatherwood: GMCC II filed 2023-05-08, owner since
    2023-05-01).
  - refinancing: the owner was already in place more than
    ACQUISITION_WINDOW before the filing, or the same debtor had an earlier
    filing with a different lender (Weatherwood's Greystone filing, 2026).
    NJ never returns a lender name, so only the CMS test applies there.
  - financing: neither test says anything (no CMS match or owner dates).
Filings against a person (NY's individual-owner track, often personal
loans) stay 'financing' until Tyler decides what they are.

A repeat is the same state + debtor + lender filed again. Only 4% of
refilings come within 6 months (median gap ~1.6 years, many at the 5-year
lapse), so there's no window: the group shows as one row, dated by its
latest filing, with the earlier dates listed and the category taken from
the first filing (when the loan was made).
"""

import re
from collections import defaultdict
from datetime import date, timedelta

ACQUISITION_WINDOW = timedelta(days=183)

FINANCING_TYPES = ("financing", "acquisition_financing", "refinancing")

_SUFFIX = re.compile(r"\b(LLC|L L C|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LP|L P|LLP|NA|N A|"
                     r"NATIONAL ASSOCIATION|THE|AS AGENT|AS ADMINISTRATIVE AGENT|AS COLLATERAL AGENT|AGENT)\b")
# A debtor with none of these words is a person ("JEFFREY RUBIN")
_ENTITY = re.compile(r"\b(LLC|L\.?L\.?C|INC|CORP|CORPORATION|COMPANY|CO|LP|L\.?P|LTD|ASSOCIATES|CENTER|HOME|"
                     r"HEALTH|CARE|NURSING|REHAB\w*|OPERATIONS|OPCO|PROPCO|REALTY|HOLDINGS|TRUST|FUND\w*|"
                     r"SERVICES|MANOR|VILLAGE|HOSPITAL|PAVILION|RESIDENCE|GROUP|PARTNERS|ASSOCIATION|"
                     r"SOCIETY|HDFC|FACILITY|SNF)\b", re.I)


def norm(name: str | None) -> str:
    s = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper())
    return " ".join(_SUFFIX.sub(" ", s).split())


def is_person(debtor: str | None) -> bool:
    return bool(debtor) and not _ENTITY.search(debtor)


def _lender_known(deal: dict) -> bool:
    lender = deal.get("lender") or ""
    return bool(lender) and not lender.startswith("Not available")


def _day(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _cms_signal(filed: date, ccns, owner_starts: dict) -> str | None:
    starts = set().union(*(owner_starts.get(c, set()) for c in ccns or [] if c))
    if any(abs(s - filed) <= ACQUISITION_WINDOW for s in starts):
        return "acquisition_financing"
    if any(s < filed - ACQUISITION_WINDOW for s in starts):
        return "refinancing"
    return None  # every owner start is later: the filing was a previous owner's


def categorize(deals: list[dict], owner_starts: dict) -> None:
    """Set deal_type on UCC financing deals. owner_starts: CCN -> set of
    dates a current direct owner started (CMS) or a CHOW took effect."""
    ucc = [d for d in deals if d.get("source_type") == "ucc" and d.get("deal_type") in FINANCING_TYPES]
    by_debtor = defaultdict(list)
    for d in ucc:
        filed = _day(d.get("acquisition_date"))
        if filed and d.get("ucc_debtor"):
            by_debtor[(d.get("ucc_state"), norm(d["ucc_debtor"]))].append((filed, norm(d.get("lender"))))

    for d in ucc:
        filed, debtor = _day(d.get("acquisition_date")), d.get("ucc_debtor")
        d["deal_type"] = "financing"
        if not filed or not debtor or is_person(debtor):
            continue
        signal = _cms_signal(filed, d.get("ccns"), owner_starts)
        if not signal and _lender_known(d):
            lender = norm(d.get("lender"))
            if any(f < filed and l != lender for f, l in by_debtor[(d.get("ucc_state"), norm(debtor))]):
                signal = "refinancing"
        d["deal_type"] = signal or "financing"


def collapse_repeats(deals: list[dict]) -> list[dict]:
    """One row per state + debtor + lender. The latest filing is kept,
    with ucc_also_filed = the earlier dates and the first filing's
    category. Person debtors and NJ (no lender names) aren't collapsed."""
    groups = defaultdict(list)
    for d in deals:
        if (d.get("source_type") == "ucc" and d.get("deal_type") in FINANCING_TYPES
                and _lender_known(d) and d.get("ucc_debtor") and not is_person(d["ucc_debtor"])
                and _day(d.get("acquisition_date"))):
            groups[(d.get("ucc_state"), norm(d["ucc_debtor"]), norm(d["lender"]))].append(d)

    drop = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda d: (d["acquisition_date"], d.get("created_at") or ""))
        keep = group[-1]
        keep["deal_type"] = group[0]["deal_type"]
        keep["ucc_also_filed"] = [d["acquisition_date"] for d in group[:-1]]
        keep["ccns"] = sorted({c for d in group for c in (d.get("ccns") or []) if c})
        drop.update(id(d) for d in group[:-1])
    return [d for d in deals if id(d) not in drop]
