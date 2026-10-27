"""
A seeded synthetic world for stress-testing the UCC resolver: CMS homes,
their owners, CHOW records and UCC filings, plus test cases whose right
answers are known because the world was built to produce them.

Every name is made up (syllable cores like "Quorwick"), so the model can't
answer from background knowledge, and nothing real is copied. Lender names
are real lenders, because the resolver's lender classifier is real code.

Scores from this world are stress tests, never accuracy claims: the
cases are the failure modes we already know about, written by us.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

TODAY = date(2026, 10, 27)
CHOW_THROUGH = date(2026, 6, 30)        # newest CHOW CSV covers sales up to here
CMS_LAG_FROM = date(2026, 5, 1)         # sales after this are recorded by CMS less and less often
STATES = {"NY": "33", "PA": "39", "OH": "36"}

HOME_LENDERS = ["Capital Funding, LLC", "Oxford Finance LLC", "MidCap Financial Trust", "CIBC Bank USA"]
VENDOR_LENDERS = ["De Lage Landen Financial Services", "Medline Industries, LP"]

_HEADS = ["Quor", "Zel", "Brin", "Vask", "Ombr", "Tal", "Kest", "Fen", "Mard", "Lys", "Cor", "Ith",
          "Pell", "Dov", "Arn", "Sib", "Wex", "Hal", "Nim", "Rook"]
_MIDS = ["", "a", "en", "o"]
_TAILS = ["wick", "mere", "dale", "holt", "brook", "crest", "ford", "ridge", "vale", "haven", "stead", "mont"]
_KINDS = ["Center for Rehabilitation and Nursing", "Nursing and Rehabilitation Center", "Manor",
          "Gardens", "Health Care Center", "Care Center", "Skilled Nursing Facility"]
_STREETS = ["Elm", "College", "Ridge", "Mill", "Orchard", "Lake", "Park", "Church", "Spring", "Quarry"]
_STYPES = ["Road", "Street", "Avenue", "Drive", "Lane"]
_FIRST = ["Avery", "Jordan", "Rowan", "Casey", "Morgan", "Quinn", "Reese", "Skyler"]
_LAST = ["Testwell", "Fabrikam", "Synthor", "Mockley", "Placeholt", "Dummond"]
_PARENTS = ["Healthcare Holdings LLC", "Senior Living Group LLC", "Care Partners LLC", "Health Ventures LLC"]
_OPCO = ["Operating LLC", "Operations LLC", "Opco LLC", "Operating Company LLC"]

DIRECT = "5% OR GREATER DIRECT OWNERSHIP INTEREST"
INDIRECT = "5% OR GREATER INDIRECT OWNERSHIP INTEREST"
MANAGER = "OPERATIONAL/MANAGERIAL CONTROL"


@dataclass
class Home:
    ccn: str
    state: str
    name: str            # CMS provider_name
    core: str            # distinctive word, e.g. "Quorwick"
    legal: str           # current opco, also the CMS legal business name
    address: str
    city: str
    owner_since: date
    parent: str
    seller: str | None = None          # previous opco, when sold in 2019+
    cms_shows_sale: bool = True        # False while CMS hasn't recorded the sale yet


@dataclass
class Case:
    case_id: str
    kind: str
    filing: dict
    ccn_ok: frozenset                   # CCNs that count as right; None = "no single home"
    cat_ok: frozenset                   # categories that count as right
    review_required: bool = False       # right only if also flagged for a person
    note: str = ""


@dataclass
class World:
    seed: int
    homes: list = field(default_factory=list)
    owners: list = field(default_factory=list)       # cms_ownership_records rows
    chow_csv: list = field(default_factory=list)     # rows shaped like the CMS CHOW CSV
    chow_seen: list = field(default_factory=list)    # chow_seen_records rows
    filings: list = field(default_factory=list)      # ucc_filings rows (cases + background)
    cases: list = field(default_factory=list)


def _day(rng, lo: date, hi: date) -> date:
    return lo + timedelta(days=rng.randrange((hi - lo).days + 1))


def build_world(seed: int = 7, n_homes: int = 600, per_kind: int = 3) -> World:
    rng = random.Random(seed)
    w = World(seed=seed)
    cores = [h + m + t for h in _HEADS for m in _MIDS for t in _TAILS]
    rng.shuffle(cores)
    # A few cores are used twice in one state ("Zelholt Manor", "Zelholt Gardens"): the
    # twins a resolver must not pick between on a shared word
    twins = cores[:12]
    singles = cores[12:12 + n_homes - 2 * len(twins)]
    serial = {st: 1000 for st in STATES}
    parents = [f"{rng.choice(cores[-40:])} {p}" for p in _PARENTS for _ in range(3)]

    def add_home(core: str, state: str) -> Home:
        serial[state] += rng.randrange(1, 9)
        kind = rng.choice(_KINDS)
        since = _day(rng, date(2001, 1, 1), date(2018, 12, 31))
        h = Home(ccn=STATES[state] + str(serial[state]), state=state, name=f"{core} {kind}", core=core,
                 legal=f"{core} {rng.choice(_OPCO)}",
                 address=f"{rng.randrange(10, 9900)} {rng.choice(_STREETS)} {rng.choice(_STYPES)}",
                 city=f"{rng.choice(cores[-60:])}ville", owner_since=since, parent=rng.choice(parents))
        # About 60% of homes changed hands since 2019; sales spread evenly by month,
        # so the timing rule's "complete through" month comes out where CMS lag puts it
        if rng.random() < 0.6:
            sold = _day(rng, date(2019, 1, 1), TODAY - timedelta(days=20))
            h.seller, h.legal, h.owner_since = h.legal, f"{core} {rng.choice(['Acquisition', 'Op', 'Care'])} LLC", sold
            lag = (sold - CMS_LAG_FROM).days
            h.cms_shows_sale = lag < 0 or rng.random() < max(0.05, 1 - lag / 120)
        w.homes.append(h)
        return h

    for core in twins:
        st = rng.choice(list(STATES))
        add_home(core, st), add_home(core, st)
    for core in singles:
        add_home(core, rng.choice(list(STATES)))

    for h in w.homes:
        _record_owners(w, h, rng)
    for i in range(n_homes // 3):               # background filings: old loans on random homes
        h = rng.choice(w.homes)
        w.filings.append(_filing(h.state, f"BG-{i}", _day(rng, date(2015, 1, 1), TODAY), h.legal,
                                 rng.choice(HOME_LENDERS + ["Huntington National Bank"])))

    by_core = {}
    for h in w.homes:
        by_core.setdefault((h.state, h.core), []).append(h)
    unique = [h for h in w.homes if len(by_core[(h.state, h.core)]) == 1]
    _USED.clear()
    for kind, make in CASE_KINDS.items():
        for i in range(per_kind):
            case = make(w, rng, unique, by_core, f"{kind}-{i}")
            if case:
                w.cases.append(case)
                w.filings.append(case.filing)
    return w


def _record_owners(w: World, h: Home, rng) -> None:
    shown_since = h.owner_since
    shown_opco = h.legal
    if h.seller and not h.cms_shows_sale:      # CMS still lists the old owner
        shown_opco, shown_since = h.seller, _day(rng, date(2001, 1, 1), date(2018, 12, 31))
    w.owners += [
        {"ccn": h.ccn, "owner_name": shown_opco, "owner_type": "Organization", "owner_role": DIRECT,
         "ownership_percentage": 100, "ownership_start_date": shown_since},
        {"ccn": h.ccn, "owner_name": h.parent, "owner_type": "Organization", "owner_role": INDIRECT,
         "ownership_percentage": 100, "ownership_start_date": shown_since},
        {"ccn": h.ccn, "owner_name": f"{rng.choice(_FIRST)} {rng.choice(_LAST)}", "owner_type": "Individual",
         "owner_role": INDIRECT, "ownership_percentage": 50, "ownership_start_date": shown_since},
    ]
    if h.seller and h.owner_since <= CHOW_THROUGH:
        eff = h.owner_since.strftime("%m/%d/%Y")
        w.chow_csv.append({
            "CCN - BUYER": h.ccn, "CCN - SELLER": h.ccn, "EFFECTIVE DATE": eff, "CHOW TYPE TEXT": "CHOW",
            "ORGANIZATION NAME - BUYER": h.legal.upper(), "DOING BUSINESS AS NAME - BUYER": h.name.upper(),
            "ORGANIZATION NAME - SELLER": h.seller.upper(), "DOING BUSINESS AS NAME - SELLER": h.name.upper(),
            "ENROLLMENT STATE - BUYER": h.state, "ENROLLMENT STATE - SELLER": h.state})
        w.chow_seen.append({"ccn": h.ccn, "buyer_name": h.legal.upper(), "effective_date": eff})


def _filing(state, number, filed: date, debtor, lender, collateral="All assets") -> dict:
    return {"state": state, "filing_number": number, "filing_date": filed.isoformat(), "filing_type": "UCC-1",
            "debtor_name": debtor, "secured_party": lender, "collateral_description": collateral,
            "status": "Active"}


def _pick(rng, homes, test, used):
    pool = [h for h in homes if test(h) and h.ccn not in used]
    if not pool:
        return None
    h = rng.choice(pool)
    used.add(h.ccn)
    return h


_USED: set = set()     # homes already used by a case in this build


def _acq_exact(w, rng, homes, by_core, cid):
    h = _pick(rng, homes, lambda h: h.seller and h.cms_shows_sale and h.owner_since < date(2026, 3, 1), _USED)
    if h:
        f = _filing(h.state, cid, h.owner_since + timedelta(days=rng.randrange(-30, 45)), h.legal, rng.choice(HOME_LENDERS))
        return Case(cid, "acq_exact", f, frozenset({h.ccn}), frozenset({"acquisition_financing"}))


def _refi_abbrev(w, rng, homes, by_core, cid):
    h = _pick(rng, homes, lambda h: not h.seller, _USED)
    if h:
        debtor = f"{h.core} {rng.choice(['Snf', 'Rehab', 'Nrsg'])} {rng.choice(['Opco', 'Ops'])}, L.L.C."
        f = _filing(h.state, cid, _day(rng, date(2022, 1, 1), date(2025, 12, 31)), debtor, rng.choice(HOME_LENDERS))
        return Case(cid, "refi_abbrev", f, frozenset({h.ccn}), frozenset({"refinancing"}))


def _address_propco(w, rng, homes, by_core, cid):
    h = _pick(rng, homes, lambda h: not h.seller, _USED)
    if h:
        f = _filing(h.state, cid, _day(rng, date(2022, 1, 1), date(2025, 12, 31)), f"{h.address} Realty LLC",
                    rng.choice(HOME_LENDERS), "Real property and fixtures")
        return Case(cid, "address_propco", f, frozenset({h.ccn}), frozenset({"refinancing", "financing"}),
                    review_required=True, note="address alone is name_similarity: a person must check it")


def _seller_debtor(w, rng, homes, by_core, cid):
    h = _pick(rng, homes, lambda h: h.seller and h.owner_since <= CHOW_THROUGH and h.owner_since > date(2021, 6, 1), _USED)
    if h:
        f = _filing(h.state, cid, h.owner_since - timedelta(days=rng.randrange(400, 900)), h.seller, rng.choice(HOME_LENDERS))
        return Case(cid, "seller_debtor", f, frozenset({h.ccn}), frozenset({"financing", "refinancing"}),
                    note="debtor is the previous owner; only the CHOW file ties it to the home")


def _renamed_home(w, rng, homes, by_core, cid):
    h = _pick(rng, homes, lambda h: not h.seller, _USED)
    if h:
        old = h.name
        h.name = f"{rng.choice(_HEADS)}{rng.choice(_TAILS)} {rng.choice(_KINDS)}"   # CMS now shows a new name
        f = _filing(h.state, cid, _day(rng, date(2022, 1, 1), date(2025, 12, 31)), h.legal, rng.choice(HOME_LENDERS))
        return Case(cid, "renamed_home", f, frozenset({h.ccn}), frozenset({"refinancing"}),
                    note=f"provider name changed from {old!r}; legal name still matches")


def _twin_homes(w, rng, homes, by_core, cid):
    pairs = [v for v in by_core.values() if len(v) == 2 and not ({x.ccn for x in v} & _USED)]
    if pairs:
        a, b = rng.choice(pairs)
        _USED.update({a.ccn, b.ccn})
        f = _filing(a.state, cid, _day(rng, date(2023, 1, 1), date(2025, 12, 31)), f"{a.core} Holdings LLC", rng.choice(HOME_LENDERS))
        return Case(cid, "twin_homes", f, frozenset({None}), frozenset({"unresolved"}), review_required=True,
                    note="two homes share the name word; neither is tied to the debtor")


def _cms_lag(w, rng, homes, by_core, cid):
    h = _pick(rng, homes, lambda h: h.seller and not h.cms_shows_sale and h.owner_since > date(2026, 8, 1), _USED)
    if h:
        f = _filing(h.state, cid, h.owner_since + timedelta(days=rng.randrange(0, 15)), h.seller, rng.choice(HOME_LENDERS))
        return Case(cid, "cms_lag", f, frozenset({h.ccn}), frozenset({"financing"}),
                    note="really an acquisition, but neither CMS nor CHOW shows it yet: timing must stay silent")


def _vendor_lien(w, rng, homes, by_core, cid):
    h = _pick(rng, homes, lambda h: True, _USED)
    if h:
        f = _filing(h.state, cid, _day(rng, date(2023, 1, 1), date(2026, 9, 1)), h.legal, rng.choice(VENDOR_LENDERS),
                    "Equipment leased under lease no. 4471")
        return Case(cid, "vendor_lien", f, frozenset({h.ccn, None}), frozenset({"not_a_nursing_home_deal"}))


def _person_debtor(w, rng, homes, by_core, cid):
    owner = rng.choice([o for o in w.owners if o["owner_type"] == "Individual"])
    st = next(h.state for h in w.homes if h.ccn == owner["ccn"])
    f = _filing(st, cid, _day(rng, date(2024, 1, 1), date(2026, 9, 1)), owner["owner_name"], rng.choice(HOME_LENDERS))
    theirs = {o["ccn"] for o in w.owners if o["owner_name"] == owner["owner_name"]}
    return Case(cid, "person_debtor", f, frozenset({None} | theirs), frozenset({"financing", "unresolved"}),
                review_required=True)


def _operations_transfer(w, rng, homes, by_core, cid):
    h = _pick(rng, homes, lambda h: h.seller and h.cms_shows_sale and h.owner_since < date(2026, 3, 1), _USED)
    if h:
        f = _filing(h.state, cid, h.owner_since - timedelta(days=rng.randrange(0, 20)), h.seller, h.legal,
                    "Accounts receivable, per operations transfer agreement")
        return Case(cid, "operations_transfer", f, frozenset({h.ccn}), frozenset({"operations_transfer"}),
                    note="the secured party is the incoming operator")


def _decoy(w, rng, homes, by_core, cid):
    core = rng.choice(_HEADS) + rng.choice(_TAILS)
    f = _filing(rng.choice(list(STATES)), cid, _day(rng, date(2024, 1, 1), date(2026, 9, 1)),
                f"{core} Dental Group PLLC", "Huntington National Bank")
    return Case(cid, "decoy", f, frozenset({None}), frozenset({"not_a_nursing_home_deal", "unresolved"}),
                note="no nursing home; may share a core with one")


def _holdco(w, rng, homes, by_core, cid):
    parent = rng.choice(sorted({h.parent for h in w.homes}))
    st = rng.choice([h.state for h in w.homes if h.parent == parent])
    f = _filing(st, cid, _day(rng, date(2023, 1, 1), date(2026, 9, 1)), parent, rng.choice(HOME_LENDERS))
    return Case(cid, "holdco", f, frozenset({None}), frozenset({"unresolved", "financing"}), review_required=True,
                note="parent of several homes: a portfolio loan, not one CCN")


CASE_KINDS = {
    "acq_exact": _acq_exact, "refi_abbrev": _refi_abbrev, "address_propco": _address_propco,
    "seller_debtor": _seller_debtor, "renamed_home": _renamed_home, "twin_homes": _twin_homes,
    "cms_lag": _cms_lag, "vendor_lien": _vendor_lien, "person_debtor": _person_debtor,
    "operations_transfer": _operations_transfer, "decoy": _decoy, "holdco": _holdco,
}
