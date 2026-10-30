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
  - operations_transfer: the secured party is itself a nursing home
    operator (East Ridge Retirement Village <- Cutler Bay SNF Operating,
    FL 2025): an incoming operator taking security from the outgoing one,
    a sale signal. Unless CMS already shows the sale (acquisition_financing).
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

FINANCING_TYPES = ("financing", "acquisition_financing", "refinancing", "operations_transfer")

# A secured party that runs nursing homes, not one that lends to them
_OPERATOR = re.compile(r"\b(OPERATING|OPERATIONS|OPCO|OPERATOR|NURSING|REHAB\w*|CARE CENTER)\b")
_NOT_OPERATOR = re.compile(r"\b(FUND\w*|FINANC\w*|CAPITAL|MONEY|HOLDINGS|PROPCO|REALTY|PROPERTIES|BANK|"
                           r"CREDIT|LENDING|MORTGAGE|PHARMACY|OMNICARE)\b")
# Owners added for estate planning, not a sale
_TRUST = re.compile(r"\b(TRUST|TR|ESTATE|FAMILY|FAM|DYNASTY)\b")

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


def is_operator(lender: str | None, debtor: str | None) -> bool:
    l = (lender or "").upper()
    return (bool(_OPERATOR.search(l)) and not _NOT_OPERATOR.search(l)
            and not is_person(debtor) and norm(l) not in norm(debtor))


def owner_start_dates(rows) -> dict:
    """CCN -> set of dates a current direct owner started, from (ccn,
    owner_name, start) rows. A date on which only trusts or family entities
    were added, while an earlier owner stays on, is left out: estate
    planning, not a sale (Coral Reef Subacute, FL: MZM 2017 Trust added
    2025-02-20 next to the 2021 owner, read as acquisition financing)."""
    by_day = defaultdict(list)
    for ccn, owner, start in rows:
        if start:
            by_day[(ccn, start)].append(owner or "")
    first = {}
    for ccn, start in by_day:
        first[ccn] = min(first.get(ccn, start), start)
    out = defaultdict(set)
    for (ccn, start), owners in by_day.items():
        if start > first[ccn] and all(_TRUST.search(o.upper()) for o in owners):
            continue
        out[ccn].add(start)
    return dict(out)


def complete_through(owner_starts: dict, today: date = None) -> date:
    """Last day of the newest month in which the owner data records a
    normal number of new owners: at least half the median of the 12 months
    before it. CMS records a sale months after closing, so later filings
    can't be called refinancing just because the old owner is still listed
    (2026-10-21: 33-77 homes a month through 2025-12, then 19, 9, 16 and
    1-8 a month; DMT SPE's 2026-06 loans to new holding companies for
    Larchwood and High Plains read as refinancing of 2014/1995 owners)."""
    today = today or date.today()
    per_month = defaultdict(set)
    for ccn, starts in owner_starts.items():
        for s in starts:
            if s and s <= today:
                per_month[(s.year, s.month)].add(ccn)
    months = [(today.year, today.month)]
    for _ in range(36):
        y, m = months[-1]
        months.append((y, m - 1) if m > 1 else (y - 1, 12))
    for i, (y, m) in enumerate(months[:-12]):
        before = sorted(len(per_month[k]) for k in months[i + 1:i + 13])
        if len(per_month[(y, m)]) >= before[6] / 2:
            nxt = date(y + (m == 12), m % 12 + 1, 1)
            return nxt - timedelta(days=1)
    # no normal month in two years: trust nothing after the oldest one checked
    return date(*months[-13], 1) - timedelta(days=1)


def _cms_signal(filed: date, ccns, owner_starts: dict, known_through: date = None) -> str | None:
    starts = set().union(*(owner_starts.get(c, set()) for c in ccns or [] if c))
    if any(abs(s - filed) <= ACQUISITION_WINDOW for s in starts):
        return "acquisition_financing"
    if any(s < filed - ACQUISITION_WINDOW for s in starts) and (known_through is None or filed <= known_through):
        return "refinancing"
    return None  # every owner start is later (a previous owner's filing), or a sale CMS can't show yet


def categorize(deals: list[dict], owner_starts: dict, today: date = None, notices=(), known_through: date = None) -> None:
    """Set deal_type on UCC financing deals. owner_starts: CCN -> set of
    dates a current direct owner started (CMS) or a CHOW took effect.
    notices: state ownership notices (CON deals) with ccns = the homes they
    name exactly; their dates count as owner starts too, but not toward
    complete_through, since they come months before CMS (BM Rehab & Nursing
    Center, PA: DOH notice and loan both 2026-07-08). Not the matcher's
    fuzzy ccns: Covenant Care's "Courtyard Health Care Center" (Davis) also
    matched Courtyard Care Center, San Jose, a different owner's home."""
    ucc = [d for d in deals if d.get("source_type") == "ucc" and d.get("deal_type") in FINANCING_TYPES]
    # known_through: pass one computed from direct owners only; holding
    # companies' dates are recorded unevenly and made 2026-01..03 look complete
    known_through = known_through or complete_through(owner_starts, today)
    owner_starts = {ccn: set(starts) for ccn, starts in owner_starts.items()}
    for d in notices:
        notice = _day(d.get("acquisition_date"))
        for ccn in (d.get("ccns") or []) if notice else []:
            owner_starts.setdefault(ccn, set()).add(notice)
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
        signal = _cms_signal(filed, d.get("ccns"), owner_starts, known_through)
        if signal:
            d["ucc_category_basis"] = "cms"
        if not signal and _lender_known(d):
            lender = norm(d.get("lender"))
            if any(f < filed and l != lender for f, l in by_debtor[(d.get("ucc_state"), norm(debtor))]):
                signal = "refinancing"
                d["ucc_category_basis"] = "earlier_loan"
        if signal != "acquisition_financing" and is_operator(d.get("lender"), debtor):
            signal = "operations_transfer"
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
        # Collateral read from one filing's PDF (NY 202507180258050) still
        # shows when a later sibling is the row kept, labelled with its source
        if not keep.get("ucc_collateral"):
            donor = next((d for d in reversed(group[:-1]) if d.get("ucc_collateral")), None)
            if donor:
                keep["ucc_collateral"] = donor["ucc_collateral"]
                keep["ucc_collateral_source"] = donor.get("ucc_collateral_source")
                keep["ucc_collateral_from"] = donor.get("ucc_filing_number")
        drop.update(id(d) for d in group[:-1])
    return [d for d in deals if id(d) not in drop]
