"""
Context for UCC financing cards: what kind of lender it is, what the loan
probably replaced, how long the owner has held the home, whether it's one
of a lender's same-day portfolio filings, and what the collateral probably
is (2026-11-09; the user's design picks: loan history, lender-change story,
portfolio grouping and collateral, as facts plus a rule-based label, with
collateral inferred from the lender type and marked as inferred).

Everything here is derived from fields the records already have, when
deals.json is written, so the rules can be retuned freely. Labels say
"Likely" because they are read from the lender switch, not from a loan
document. The card shows `ucc_context["line"]`; Details shows the rest.
A lender of no known kind (often an affiliate or landlord company) gets no
lender-switch story or loan history.

Lender kinds (by name; first match wins):
  agency       HUD-insured loans: the Secretary of HUD named as secured
               party (often next to the lender), or a lender that mainly
               originates HUD 232 loans
  reit         a healthcare REIT: as secured party it usually secures the
               operator's lease, not a loan
  receivables  asset-based lenders: a working-capital line on accounts
               receivable, which sits alongside a mortgage, not instead of it
  bridge       private healthcare real estate lenders, mostly bridge loans
               that a HUD loan later takes out
  bank         a bank
"""

import re
from collections import defaultdict
from datetime import date

from pipeline.ucc_category import FINANCING_TYPES, _day, _lender_known, is_person, norm

_KINDS = [
    ("agency", re.compile(r"SECRETARY OF (THE )?(U\.?S\.? )?(DEPARTMENT OF )?HOUSING|\bHUD\b|HOUSING (AND|&) URBAN|"
                          r"HOUSING (AND|&) HEALTHCARE FINANCE|GREYSTONE (FUNDING|SERVICING)|\bLUMENT\b|NEWPOINT|"
                          r"BERKADIA|LANCASTER POLLARD|ORIX REAL ESTATE|DWIGHT CAPITAL")),
    ("bridge", re.compile(r"WELLTOWER LENDING")),
    ("reit", re.compile(r"WELLTOWER|OMEGA HEALTHCARE|\bSABRA\b|CARETRUST|\bCTW\b|LTC PROPERTIES|NATIONAL HEALTH INVESTORS|"
                        r"\bVENTAS\b|STRAWBERRY FIELDS|AEDIFICA|\bNHI\b")),
    ("receivables", re.compile(r"\bECAPITAL\b|\bMIDCAP\b|CAPITAL FINANCE\b|GEMINO|SIENA LENDING|IKARIA ABL|"
                               r"WHITE OAK HEALTHCARE|CNH FINANCE|SCM SPECIALTY|ROSENTHAL|\bABL\b|RECEIVABLE|"
                               r"HEALTHCARE FINANCING OF AMERICA")),
    ("bridge", re.compile(r"CAPITAL FUNDING|MONTICELLO|\bGMCC\b|DMT SPE|VIUM CAPITAL|GREYSTONE SELECT|ONB DHF|DBF SPE|"
                          r"OXFORD FINANCE|BLOOMFIELD|\bMEDIPLEX\b|DEERFIELD|HARBORVIEW")),
    ("bank", re.compile(r"\bBANK\b|\bN\.?A\.?$|NATIONAL ASSOCIATION|\bVNB\b|BANCORP|TRUST COMPANY|CREDIT UNION")),
]

KIND_LABELS = {
    "agency": "HUD / agency lender",
    "reit": "Healthcare REIT (landlord)",
    "receivables": "Receivables / working-capital lender",
    "bridge": "Bridge / private healthcare lender",
    "bank": "Bank",
}

# Inferred collateral, shown labelled as inferred, only where the filing's
# own collateral text isn't on the record
COLLATERAL_GUESS = {
    "agency": "Likely the home's equipment, accounts and revenue, backing a HUD-insured mortgage",
    "reit": "Likely the operator's equipment and accounts, securing its lease",
    "receivables": "Likely accounts receivable (a working-capital line)",
    "bridge": "Likely all assets, backing a bridge or mortgage loan",
    "bank": "Likely all business assets",
}

_SHORT_KIND = {"agency": "HUD", "bridge": "bridge", "bank": "bank", "receivables": "working-capital", "reit": "REIT"}

_AGENT = re.compile(r",?\s*\b(AS (ADMINISTRATIVE |COLLATERAL )?AGENT|ITS SUCCESSORS.*|AND/OR ITS.*|AS ADMINISTRATIVE.*)$", re.I)
_SUFFIX = re.compile(r",?\s*\b(LLC|L\.L\.C\.|INC\.?|CORP(ORATION)?|N\.?A\.?|LP|L\.P\.|NATIONAL ASSOCIATION|COMPANY)\b\.?", re.I)

# One lender group under several names: a filing by one after another is
# the same relationship (GMCC after Greystone Monticello), not a switch
_FAMILIES = [re.compile(r"GREYSTONE|MONTICELLO|\bGMCC\b"), re.compile(r"WELLTOWER"), re.compile(r"\bMIDCAP\b"),
             re.compile(r"WHITE OAK"), re.compile(r"\bCIBC\b|PRIVATEBANK"), re.compile(r"\bVNB\b|VALLEY NATIONAL"),
             re.compile(r"\bDMT\b|DWIGHT"), re.compile(r"OXFORD FINANCE"), re.compile(r"FORBRIGHT"),
             re.compile(r"METROPOLITAN COMMERCIAL"), re.compile(r"BANKWELL"), re.compile(r"HAPOALIM"),
             re.compile(r"CONGRESSIONAL BANK")]

PORTFOLIO_MIN = 3      # same lender, state and day, this many borrowers
MAX_PRIOR = 4
MAX_BORROWERS = 8


def lender_kind(lender: str | None) -> str | None:
    up = (lender or "").upper()
    if not up or up.startswith("NOT AVAILABLE"):
        return None
    return next((k for k, rx in _KINDS if rx.search(up)), None)


def short_lender(lender: str | None) -> str:
    """'CAPITAL FUNDING, LLC, ITS SUCCESSORS AND ASSIGNS' -> 'Capital Funding';
    anything naming the Secretary of HUD -> 'HUD' (or 'Greystone / HUD')."""
    s = " ".join((lender or "").split())
    hud = re.search(r"SECRETARY OF", s, re.I)
    if hud:
        before = _SUFFIX.sub("", _AGENT.sub("", s[:hud.start()])).strip(" ,;/-")
        return f"{_title(before)} / HUD" if before else "HUD"
    s = _SUFFIX.sub("", _AGENT.sub("", s)).strip(" ,;/-")
    return _title(s)


_ACRONYMS = {"CIBC", "VNB", "BOKF", "GMCC", "DMT", "SPE", "ONB", "DHF", "DBF", "CNH", "SCM", "ABL", "HUD", "M&T",
             "BMO", "NHI", "LTC", "CTW", "USA", "US", "II", "III", "IV", "XLII", "OP", "BM", "NY"}


def family(lender: str | None) -> str:
    up = (lender or "").upper()
    return next((rx.pattern for rx in _FAMILIES if rx.search(up)), norm(lender))


def _title(s: str) -> str:
    return " ".join(w if w.upper() in _ACRONYMS else "-".join(p.capitalize() for p in w.split("-")) for w in s.split())


def _month(d: date) -> str:
    return f"{d:%b} {d.year}"


def index(deals: list[dict]) -> dict:
    """Built from every UCC financing filing before repeats are collapsed:
    filings per borrower (state + debtor) and per lender-day (state + lender
    + date)."""
    by_debtor, by_day = defaultdict(list), defaultdict(set)
    for d in deals:
        if d.get("source_type") != "ucc" or d.get("deal_type") not in FINANCING_TYPES:
            continue
        filed, debtor = _day(d.get("acquisition_date")), d.get("ucc_debtor")
        if not filed or not debtor or is_person(debtor) or not _lender_known(d):
            continue
        by_debtor[(d.get("ucc_state"), norm(debtor))].append((filed, d["lender"]))
        by_day[(d.get("ucc_state"), norm(d["lender"]), filed)].add(debtor)
    return {"by_debtor": by_debtor, "by_day": by_day}


def context(d: dict, idx: dict, owner_starts: dict) -> dict | None:
    if d.get("source_type") != "ucc" or d.get("deal_type") not in FINANCING_TYPES:
        return None
    filed, debtor = _day(d.get("acquisition_date")), d.get("ucc_debtor")
    if not filed or not debtor or is_person(debtor):
        return None
    lender = d.get("lender") if _lender_known(d) else None
    kind = lender_kind(lender)
    me, my_family = norm(lender), family(lender)

    # Earlier filings for the same borrower with other lenders, newest first
    earlier = sorted({(f, l) for f, l in idx["by_debtor"].get((d.get("ucc_state"), norm(debtor)), [])
                      if kind and f < filed and family(l) != my_family and lender_kind(l)}, reverse=True)
    prior, seen = [], set()
    for f, l in earlier:
        if family(l) not in seen:
            seen.add(family(l))
            prior.append({"date": f.isoformat(), "lender": short_lender(l), "kind": lender_kind(l)})
        if len(prior) == MAX_PRIOR:
            break

    # Latest CMS owner start on or before the filing, when the record is
    # tied to at most 3 homes (more is a guess between possible homes)
    ccns = [c for c in d.get("ccns") or [] if c]
    starts = [s for c in ccns[:3] for s in owner_starts.get(c[:6], ()) if s <= filed] if len(ccns) <= 3 else []
    owner_since = max(starts) if starts else None

    same_day = sorted(idx["by_day"].get((d.get("ucc_state"), me, filed), set())) if lender else []
    portfolio = ({"count": len(same_day), "lender": short_lender(lender), "date": filed.isoformat(),
                  "borrowers": [b for b in same_day if norm(b) != norm(debtor)][:MAX_BORROWERS]}
                 if len(same_day) >= PORTFOLIO_MIN else None)

    label, facts = _story(d, kind, lender, prior, owner_since, filed), []
    replaced = next((p for p in prior if p["kind"] != "receivables"), None) if kind != "receivables" else None
    if replaced:
        facts.append(f"replaces {replaced['lender']} ({replaced['date'][:4]})")
    elif kind == "receivables" and prior:
        facts.append(f"alongside {prior[0]['lender']} ({prior[0]['date'][:4]})")
    if owner_since and d.get("deal_type") != "acquisition_financing":
        facts.append(f"owner since {_month(owner_since)}")
    if portfolio:
        facts.append(f"1 of {portfolio['count']} same-day filings")
    line = " · ".join([x for x in [label] if x] + facts[:2]) or None

    collateral_guess = None if d.get("ucc_collateral") else COLLATERAL_GUESS.get(kind)
    return {"line": line, "label": label, "lender_kind": KIND_LABELS.get(kind), "prior": prior,
            "owner_since": owner_since.isoformat() if owner_since else None, "portfolio": portfolio,
            "collateral_guess": collateral_guess}


def _story(d, kind, lender, prior, owner_since, filed) -> str | None:
    dt = d.get("deal_type")
    prev = next((p for p in prior if p["kind"] != "receivables"), None)
    if kind == "reit":
        return ("Likely a new operator's lease with " if dt == "acquisition_financing" else "Likely lease security for ") \
            + short_lender(lender)
    if dt == "acquisition_financing":
        return "Likely purchase financing"
    if dt == "operations_transfer":
        return "Possible sale to the operator taking security"
    if kind == "receivables":
        return "Likely a working-capital line"
    if prev and kind == "agency" and prev["kind"] != "agency":
        return f"Likely HUD takeout of a {_SHORT_KIND.get(prev['kind'], 'earlier')} loan" if prev["kind"] else "Likely HUD takeout of an earlier loan"
    if prev and prev["kind"] == "agency" and kind != "agency":
        return "Likely refinancing out of a HUD loan"
    if prev:
        return "Likely refinancing with a new lender"
    if dt == "refinancing" and owner_since:
        years = (filed - owner_since).days / 365.25
        if kind == "agency" and 1.5 <= years <= 4:
            return "Likely HUD takeout after an earlier purchase loan"
        return "Likely new loan for an existing owner"
    if dt == "refinancing":
        return "Likely new loan for an existing owner"
    return None


def annotate(deals: list[dict], idx: dict, owner_starts: dict) -> int:
    n = 0
    for d in deals:
        ctx = context(d, idx, owner_starts)
        if ctx:
            d["ucc_context"] = ctx
            n += 1
    return n
