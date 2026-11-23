"""
Hospice and inpatient rehab ownership changes from CMS's owner files
(first version, person's decision 2026-11-23: rehab = inpatient rehab
hospitals; sources = CMS files + news; same feed with a facility-type filter).

CMS publishes no change-of-ownership file for hospices, and its hospital CHOW
file had no rehab hospital sale in the last 12 months (checked 2026-11-23),
so both come from the All Owners files, the same way the nursing home
Ownership file does (scraper/cms_owner_changes.py):

  - Hospice All Owners (quarterly) + Hospice Enrollments (CCN, address)
  - Hospital All Owners (monthly) + Hospital Enrollments, kept to enrollments
    flagged SUBGROUP - REHABILITATION (freestanding rehab hospitals; rehab
    units inside general hospitals are left out, a question for Tyler)

A new 5%+ company owner (direct or indirect) whose association date is in
the last RECENCY_DAYS, with no older 5%+ owner still listed on that provider,
is a sale, unless the provider wasn't in CMS's release before that date
(a new hospice or hospital opening, not a sale) or the only prior owner was
a family trust (estate planning). Banks and fund managers holding 5% of a listed parent's shares
(Truist, T. Rowe Price, Vanguard...) are passive stakes, not buyers, and are
ignored on both sides. The seller is a company direct owner listed in the
release before the change but not the latest. Same-day changes sharing an owner are
one portfolio deal. Every candidate is recorded in cms_owner_change_seen
(hospice and hospital CCNs don't overlap nursing home ones), so each change
becomes a deal once.

Files are cached in data/cms_pac/; the release list is re-read from CMS's
catalog at most every REFRESH_MAX_AGE_DAYS. Sellers come from the release
dated just before each change, downloaded on demand.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from pipeline.run_health import health
from scraper.cms_owner_changes import (
    DIRECT_ROLES, INDIRECT_ROLES, PRIOR_OWNER_GRACE_DAYS, _NOT_A_BUYER, _family,
    _group_facility_changes, _pick_buyer, _portfolios,
)

logger = logging.getLogger(__name__)

DATA_JSON = "https://data.cms.gov/data.json"
CACHE = Path(__file__).resolve().parent.parent / "data" / "cms_pac"
REFRESH_MAX_AGE_DAYS = 25
RECENCY_DAYS = 365

KINDS = {
    "hospice": {
        "owners": "Hospice All Owners", "enrollments": "Hospice Enrollments",
        "source_name": "CMS Hospice Ownership Changes",
        "source_url": "https://data.cms.gov/provider-characteristics/hospitals-and-other-facilities/hospice-all-owners",
        "model": "cms_hospice_owner_change", "noun": ("hospice", "hospices"), "label": "Hospice All Owners file",
    },
    "rehab": {
        "owners": "Hospital All Owners", "enrollments": "Hospital Enrollments",
        "source_name": "CMS Inpatient Rehab Ownership Changes",
        "source_url": "https://data.cms.gov/provider-characteristics/hospitals-and-other-facilities/hospital-all-owners",
        "model": "cms_rehab_owner_change", "noun": ("rehab hospital", "rehab hospitals"), "label": "Hospital All Owners file",
    },
}
MODEL_FACILITY_TYPE = {"cms_hospice_owner_change": "hospice", "cms_rehab_owner_change": "rehab"}

# 5% holders of a listed parent's shares: not buyers or sellers
_PASSIVE = re.compile(
    r"\b(VANGUARD|BLACKROCK|T\.? ?ROWE PRICE|STATE STREET|FIDELITY|FMR\b|WELLINGTON MANAGEMENT|"
    r"DIMENSIONAL FUND|CAPITAL RESEARCH|INVESCO|GEODE CAPITAL|NORTHERN TRUST|JPMORGAN CHASE BANK|"
    r"TRUIST BANK|WELLS FARGO BANK|BANK OF AMERICA|MORGAN STANLEY|GOLDMAN SACHS|CHARLES SCHWAB|"
    r"JANUS HENDERSON|PRICE T ROWE|AMERIPRISE|BAILLIE GIFFORD|ALLIANCEBERNSTEIN|NUVEEN)\b", re.I)


# ── CMS files ────────────────────────────────────────────────────────────

def _releases(title: str, catalog: list[dict]) -> list[tuple[str, str]]:
    """(release title, csv url) for one dataset, newest first."""
    out = []
    for x in catalog:
        if x.get("title", "").startswith(title + " :"):
            url = next((z.get("downloadURL") for z in x.get("distribution", [])
                        if (z.get("downloadURL") or "").endswith(".csv")), None)
            if url:
                out.append((x["title"], url))
    return sorted(out, key=lambda t: t[0].split(" : ")[-1], reverse=True)


def _get(url: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / url.rsplit("/", 1)[-1]
    if not path.exists():
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        path.write_bytes(r.content)
        time.sleep(1)
    return path


def files(kind: str, catalog: list[dict] | None = None) -> dict:
    """{"latest": path, "enrollments": path, "release": fn(day) -> (owners path,
    enrollments path) of the last release dated before that day, or None}.
    Files are downloaded only when needed; the catalog is re-read when the
    cached release list is older than REFRESH_MAX_AGE_DAYS."""
    k = KINDS[kind]
    state = CACHE / f"{kind}.json"
    known = json.loads(state.read_text()) if state.exists() else {}
    stale = (not known or "enrollment_releases" not in known
             or (datetime.now(timezone.utc) - datetime.fromisoformat(known["checked"])).days >= REFRESH_MAX_AGE_DAYS)
    if stale:
        if catalog is None:
            catalog = requests.get(DATA_JSON, timeout=120).json()["dataset"]
        known = {"checked": datetime.now(timezone.utc).isoformat(),
                 "owners": [(t.split(" : ")[-1], u) for t, u in _releases(k["owners"], catalog)],
                 "enrollment_releases": [(t.split(" : ")[-1], u) for t, u in _releases(k["enrollments"], catalog)]}
        CACHE.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps(known, indent=1))
    owners, enrolls = known["owners"], known["enrollment_releases"]

    def release(day: date):
        o = next(((d, u) for d, u in owners[1:] if date.fromisoformat(d) < day), None)
        if not o:
            return None
        # the enrollment file published with it (dates differ by a day or two)
        e = min(enrolls, key=lambda x: abs((date.fromisoformat(x[0]) - date.fromisoformat(o[0])).days))
        return _get(o[1]), _get(e[1])
    return {"latest": _get(owners[0][1]), "enrollments": _get(enrolls[0][1]), "release": release}


def _rows(path: Path) -> list[dict]:
    with open(path, encoding="latin-1", newline="") as f:
        return list(csv.DictReader(f))


def _day(s: str) -> date | None:
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y").date()
    except (ValueError, AttributeError):
        return None


def _providers(kind: str, path: Path) -> dict[str, dict]:
    """enrollment id -> {ccn, name, state, city} for the providers in scope."""
    out = {}
    for r in _rows(path):
        if kind == "rehab" and r.get("SUBGROUP - REHABILITATION") != "Y":
            continue
        if not r.get("CCN"):
            continue
        out[r["ENROLLMENT ID"]] = {"ccn": r["CCN"], "name": (r.get("DOING BUSINESS AS NAME") or r["ORGANIZATION NAME"]).strip(),
                                   "legal": r["ORGANIZATION NAME"].strip(), "state": r.get("STATE"), "city": r.get("CITY")}
    return out


def _owner(r: dict) -> str:
    return (r.get("ORGANIZATION NAME - OWNER") or " ".join(
        x for x in (r.get("FIRST NAME - OWNER"), r.get("LAST NAME - OWNER")) if x)).strip()


def _passive(r: dict) -> bool:
    return r.get("FINANCIAL INSTITUTION - OWNER") == "Y" or bool(_PASSIVE.search(_owner(r)))


# ── changes ──────────────────────────────────────────────────────────────

def candidates(kind: str, fs: dict[str, Path], seen: set | None = None, today: date | None = None):
    """(rows, by_facility changes, sellers) for one kind, from the files alone."""
    providers = _providers(kind, fs["enrollments"])
    latest = [r for r in _rows(fs["latest"]) if r["ENROLLMENT ID"] in providers]
    roles = DIRECT_ROLES + INDIRECT_ROLES
    since = (today or date.today()) - timedelta(days=RECENCY_DAYS)
    seen = seen or set()

    stakes = defaultdict(list)          # enrollment -> (owner, start) of non-passive 5%+ owners
    for r in latest:
        if r["ROLE TEXT - OWNER"] in roles and not _passive(r) and _day(r["ASSOCIATION DATE - OWNER"]):
            stakes[r["ENROLLMENT ID"]].append((_owner(r).upper(), _day(r["ASSOCIATION DATE - OWNER"])))

    rows = []
    for r in latest:
        start = _day(r["ASSOCIATION DATE - OWNER"])
        name = _owner(r)
        if (r["TYPE - OWNER"] != "O" or r["ROLE TEXT - OWNER"] not in roles or not start or start < since
                or _passive(r) or _NOT_A_BUYER.search(name)):
            continue
        p = providers[r["ENROLLMENT ID"]]
        if (p["ccn"], name, start) in seen:
            continue
        pct = r.get("PERCENTAGE OWNERSHIP")
        rows.append({"ccn": p["ccn"], "enrollment": r["ENROLLMENT ID"], "owner_name": name,
                     "owner_role": r["ROLE TEXT - OWNER"], "ownership_start_date": start,
                     "ownership_percentage": float(pct) if pct not in (None, "") else None,
                     "provider_name": p["name"], "provider_legal": p["legal"], "provider_state": p["state"],
                     "provider_city": p["city"]})

    by_facility = _group_facility_changes(rows)
    enroll_of = {r["ccn"]: r["enrollment"] for r in rows}
    # an older 5%+ owner still listed: a restructure or a new parent above the old owners
    restructures = {k for k in by_facility
                    if any(s < k[1] - timedelta(days=PRIOR_OWNER_GRACE_DAYS) for _, s in stakes[enroll_of[k[0]]])}
    changes = {k: v for k, v in by_facility.items() if k not in restructures}

    # The release before each change: a CCN it doesn't list is a new provider,
    # not a sale. The seller is a company direct owner it lists that the latest
    # release doesn't (matched by CCN: a sale often brings a new enrollment ID).
    # A family trust handing a provider to a company is estate planning.
    sellers, new_providers, family = {}, set(), set()
    now_direct = {(providers[r["ENROLLMENT ID"]]["ccn"], _owner(r).upper())
                  for r in latest if r["ROLE TEXT - OWNER"] in DIRECT_ROLES}
    by_release = defaultdict(list)
    for key in changes:
        rel = fs["release"](key[1]) if fs.get("release") else None
        if rel:
            by_release[rel].append(key)
    for (owners_path, enroll_path), keys in by_release.items():
        ccn_of = {r["ENROLLMENT ID"]: r["CCN"] for r in _rows(enroll_path) if r.get("CCN")}
        wanted = {c for c, _ in keys}
        listed, before = set(), defaultdict(list)
        for r in _rows(owners_path):
            c = ccn_of.get(r["ENROLLMENT ID"])
            if c in wanted:
                listed.add(c)
                if (r["TYPE - OWNER"] == "O" and r["ROLE TEXT - OWNER"] in DIRECT_ROLES and not _passive(r)
                        and (c, _owner(r).upper()) not in now_direct):
                    before[c].append(_owner(r))
        for key in keys:
            c = key[0]
            if c not in listed:
                new_providers.add(key)
            elif before[c]:
                sellers[c] = before[c][0]
                if all(_NOT_A_BUYER.search(n) and re.search(r"\bTRUST\b", n, re.I) for n in before[c]):
                    family.add(key)
    changes = {k: v for k, v in changes.items() if k not in new_providers | family}
    logger.info(f"CMS {kind}: {len(new_providers)} new providers and {len(family)} family-trust transfers left out")
    return rows, changes, sellers, len(restructures)


def build(kind: str, rows: list[dict], changes: dict, sellers: dict) -> tuple[list[dict], int]:
    articles, internal = [], 0
    for group in sorted(_portfolios(changes), key=lambda g: g[0][1]):
        members = [(ccn, _pick_buyer(changes[(ccn, start)]), changes[(ccn, start)]) for ccn, start in group]
        counts = defaultdict(int)
        for _, b, _ in members:
            counts[b["owner_name"].upper()] += 1
        lead = max(counts, key=lambda n: (counts[n], -len(n)))
        members.sort(key=lambda m: m[1]["owner_name"].upper() != lead)
        if _family(lead) and _family(lead) in {_family(sellers[c]) for c, _, _ in members if c in sellers}:
            internal += len(members)
            continue
        articles.append(_article(kind, group[0][1], members, sellers))
    return articles, internal


def _article(kind: str, start: date, members: list, sellers: dict) -> dict:
    k = KINDS[kind]
    buyer = members[0][1]["owner_name"]
    ccns = [c for c, _, _ in members]
    names = [b["provider_name"] for _, b, _ in members]
    states = sorted({b["provider_state"] for _, b, _ in members if b["provider_state"]})
    others = sorted({r["owner_name"] for _, _, rs in members for r in rs} - {buyer})
    seller_names = [sellers[c] for c in ccns if c in sellers]
    seller = max(set(seller_names), key=seller_names.count) if seller_names else None
    one, many = k["noun"]
    noun = one if len(ccns) == 1 else many
    s = start.isoformat()
    slug = re.sub(r"[^a-z0-9]+", "-", buyer.lower()).strip("-")
    lines = [f"CMS {k['label']}: new owner", "",
             f"New owner: {buyer} ({'; '.join(sorted({b['owner_role'].lower() for _, b, _ in members}))}), associated since {s}",
             f"Prior owner no longer listed: {seller or 'not found in the earlier CMS release'}",
             f"{noun.capitalize()} ({len(ccns)}):",
             *[f"  - {b['provider_name']}, {b['provider_city']}, {b['provider_state']} (CCN {c})" for c, b, _ in members]]
    if others:
        lines.append(f"Other new owners on the same date: {'; '.join(others)}")
    return {
        "url": f"{k['source_url']}#owner-{slug}-{s}",
        "title": f"[CMS {kind.capitalize()} Ownership] {buyer} became owner of {len(ccns)} {noun} ({', '.join(states)})",
        "published_at": datetime.combine(start, datetime.min.time()).replace(tzinfo=timezone.utc),
        "raw_text": "\n".join(lines), "source_name": k["source_name"], "source_url": k["source_url"],
        "source_type": "chow", "pre_extracted": True, "extraction_model": k["model"],
        "acquiring_entity": buyer, "seller_entity": seller, "operator_names": [buyer] + others,
        "facility_names": names, "states": states, "facility_count": len(ccns), "deal_value_m": None,
        "acquisition_date": s, "financing_amount_m": None, "lender": None,
        "rationale": f"CMS {k['label']} lists {buyer} as a new owner of {len(ccns)} {noun} since {s}"
                     + (f"; prior owner {seller} no longer listed." if seller else "."),
        "ccns": ccns, "_ccn_names": {c: (b["provider_name"], b["provider_state"]) for c, b, _ in members},
        "_cms_change_id": f"{kind}|{buyer.upper()}|{s}",
    }


def _seen(conn) -> set:
    with conn.cursor() as cur:
        cur.execute("SELECT ccn, owner_name, start_date FROM cms_owner_change_seen")
        return set(cur.fetchall())


def _mark_seen(conn, rows: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO cms_owner_change_seen (ccn, owner_name, start_date) VALUES (%s, %s, %s) "
                        "ON CONFLICT DO NOTHING", [(r["ccn"], r["owner_name"], r["ownership_start_date"]) for r in rows])


def ensure_source(conn, kind: str) -> str:
    k = KINDS[kind]
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO sources (name, url, source_type, last_fetched_at) VALUES (%s, %s, 'chow', NOW())
                       ON CONFLICT (url) DO UPDATE SET last_fetched_at = NOW() RETURNING id""",
                    (k["source_name"], k["source_url"]))
        return cur.fetchone()[0]


def fetch_pac_owner_change_deals(conn, kinds=("hospice", "rehab"), mark_seen: bool = True) -> list[dict]:
    """New hospice and rehab hospital owner changes as pre-extracted articles
    (source_id set) ready for process_article."""
    out, catalog = [], None
    for kind in kinds:
        try:
            fs = files(kind, catalog)
            rows, changes, sellers, restructures = candidates(kind, fs, _seen(conn))
            articles, internal = build(kind, rows, changes, sellers)
            sid = ensure_source(conn, kind)
            for a in articles:
                a["source_id"] = sid
            if mark_seen:
                _mark_seen(conn, rows)
            logger.info(f"CMS {kind} ownership: {len(rows)} new owner rows -> {len(changes) + restructures} provider "
                        f"changes, {restructures + internal} restructures -> {len(articles)} deals")
            health.attempted(f"CMS {kind} ownership changes", len(articles))
            out += articles
        except Exception as e:
            logger.warning(f"CMS {kind} ownership changes skipped: {e}")
            health.source_failed(f"CMS {kind} ownership changes", e)
    return out
