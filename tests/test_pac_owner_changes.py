"""Hospice / rehab owner-change detection from CMS All Owners files (no network)."""
import csv
from datetime import date

from scraper import pac_owner_changes as p

OWNER_COLS = ["ENROLLMENT ID", "ASSOCIATE ID", "ORGANIZATION NAME", "ASSOCIATE ID - OWNER", "TYPE - OWNER",
              "ROLE CODE - OWNER", "ROLE TEXT - OWNER", "ASSOCIATION DATE - OWNER", "FIRST NAME - OWNER",
              "LAST NAME - OWNER", "ORGANIZATION NAME - OWNER", "PERCENTAGE OWNERSHIP",
              "FINANCIAL INSTITUTION - OWNER"]
ENROLL_COLS = ["ENROLLMENT ID", "CCN", "ORGANIZATION NAME", "DOING BUSINESS AS NAME", "CITY", "STATE",
               "SUBGROUP - REHABILITATION"]
DIRECT = "5% OR GREATER DIRECT OWNERSHIP INTEREST"


def _write(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return path


def _own(enr, owner, when, kind="O", role=DIRECT, **kw):
    return {"ENROLLMENT ID": enr, "TYPE - OWNER": kind, "ROLE TEXT - OWNER": role,
            "ASSOCIATION DATE - OWNER": when, "ORGANIZATION NAME - OWNER": owner, "PERCENTAGE OWNERSHIP": "100", **kw}


def _enr(enr, ccn, name, rehab=""):
    return {"ENROLLMENT ID": enr, "CCN": ccn, "ORGANIZATION NAME": name, "CITY": "X", "STATE": "TX",
            "SUBGROUP - REHABILITATION": rehab}


def _run(tmp_path, latest, before, enroll_now, enroll_before, kind="hospice"):
    fs = {"latest": _write(tmp_path / "now.csv", OWNER_COLS, latest),
          "enrollments": _write(tmp_path / "en_now.csv", ENROLL_COLS, enroll_now),
          "release": lambda day: (_write(tmp_path / "before.csv", OWNER_COLS, before),
                                  _write(tmp_path / "en_before.csv", ENROLL_COLS, enroll_before))}
    rows, changes, sellers, _ = p.candidates(kind, fs, today=date(2026, 11, 23))
    return p.build(kind, rows, changes, sellers)[0]


def test_sale_with_seller_found_by_ccn_across_new_enrollment(tmp_path):
    arts = _run(tmp_path,
                latest=[_own("E2", "NEWCO HOSPICE LLC", "03/01/2026")],
                before=[_own("E1", "OLDCO HOSPICE INC", "01/01/2015")],
                enroll_now=[_enr("E2", "451501", "SUNSET HOSPICE")],
                enroll_before=[_enr("E1", "451501", "SUNSET HOSPICE")])
    assert len(arts) == 1
    a = arts[0]
    assert (a["acquiring_entity"], a["seller_entity"], a["ccns"]) == ("NEWCO HOSPICE LLC", "OLDCO HOSPICE INC", ["451501"])
    assert a["extraction_model"] == "cms_hospice_owner_change" and a["source_type"] == "chow"


def test_new_provider_is_not_a_sale(tmp_path):
    arts = _run(tmp_path,
                latest=[_own("E2", "NEWCO HOSPICE LLC", "03/01/2026")],
                before=[],
                enroll_now=[_enr("E2", "451501", "SUNSET HOSPICE")],
                enroll_before=[])
    assert arts == []


def test_family_trust_transfer_and_passive_bank_left_out(tmp_path):
    arts = _run(tmp_path,
                latest=[_own("E2", "STARCO MANAGEMENT LLC", "03/01/2026"),
                        _own("E2", "TRUIST BANK", "03/01/2026")],
                before=[_own("E2", "STERN FAMILY 2019 TRUST", "01/01/2019")],
                enroll_now=[_enr("E2", "451501", "SUNSET HOSPICE")],
                enroll_before=[_enr("E2", "451501", "SUNSET HOSPICE")])
    assert arts == []


def test_restructure_when_old_owner_stays(tmp_path):
    arts = _run(tmp_path,
                latest=[_own("E2", "NEW PARENT HOLDINGS LLC", "03/01/2026",
                             role="5% OR GREATER INDIRECT OWNERSHIP INTEREST"),
                        _own("E2", "OLDCO HOSPICE INC", "01/01/2015")],
                before=[_own("E2", "OLDCO HOSPICE INC", "01/01/2015")],
                enroll_now=[_enr("E2", "451501", "SUNSET HOSPICE")],
                enroll_before=[_enr("E2", "451501", "SUNSET HOSPICE")])
    assert arts == []


def test_rehab_keeps_only_rehab_hospitals(tmp_path):
    latest = [_own("E1", "REHABCO LLC", "03/01/2026"), _own("E3", "ACUTECO LLC", "03/01/2026")]
    before = [_own("E1", "OLD REHAB INC", "01/01/2010"), _own("E3", "OLD ACUTE INC", "01/01/2010")]
    enroll = [_enr("E1", "453030", "TX REHAB HOSPITAL", rehab="Y"), _enr("E3", "450001", "TX GENERAL HOSPITAL")]
    arts = _run(tmp_path, latest, before, enroll, enroll, kind="rehab")
    assert [a["acquiring_entity"] for a in arts] == ["REHABCO LLC"]
    assert arts[0]["extraction_model"] == "cms_rehab_owner_change"


def test_facility_type_from_model():
    from pipeline.deal_labels import facility_type
    assert facility_type({"extraction_model": "cms_hospice_owner_change", "source_type": "chow"}) == "hospice"
    assert facility_type({"extraction_model": "cms_rehab_owner_change", "source_type": "chow"}) == "rehab"
    assert facility_type({"source_type": "rss", "summary": "Buyer acquires a hospice agency in Ohio"}) == "hospice"
    assert facility_type({"source_type": "rss", "summary": "Encompass opens an inpatient rehabilitation hospital"}) == "rehab"


def test_cms_cards_skip_the_judge():
    from evals.holds import auto_approved
    assert auto_approved({"source_type": "chow"})
    assert not any(auto_approved({"source_type": t}) for t in ("rss", "ucc", "con", "edgar", "gmail"))
    # licence-list cards read straight off the list (2026-11-25); WA/CT/IA infer the buyer
    for title in ("[FL AL licence] SONATA VIERA: new owner SONATA VIERA TRS, LLC",
                  "[CA licence] INGLEWOOD HEALTHCARE CENTRE: new licensee INGLEWOOD POST ACUTE CARE LLC",
                  "[CA hospice licence] X Hospice: new licensee Y LLC"):
        assert auto_approved({"source_type": "con", "source_title": title})
    for title in ("[WA licence] Parkside: new licence #1234", "[IA licence] X: new licence 01/02/2026",
                  "[NC CON] 5066 Acquisition of a Facility: Golden Years Rest Home"):
        assert not auto_approved({"source_type": "con", "source_title": title})


# ── re-enrollment: same CCN, new enrollment ID (2026-11-24) ─────────────

def _reenroll(tmp_path, monkeypatch, enroll_new, enroll_old, owners_new, owners_old, pos=None, seen=None):
    monkeypatch.setattr(p, "_get", lambda url: url)
    fs = {"enrollment_releases": [("2026-10-01", _write(tmp_path / "en_new.csv", ENROLL_COLS, enroll_new)),
                                  ("2026-07-01", _write(tmp_path / "en_old.csv", ENROLL_COLS, enroll_old))],
          "owner_releases": [("2026-10-01", _write(tmp_path / "own_new.csv", OWNER_COLS, owners_new)),
                             ("2026-07-01", _write(tmp_path / "own_old.csv", OWNER_COLS, owners_old))]}
    return p.reenrollments("hospice", fs, seen or set(), pos or {}, today=date(2026, 11, 24))


def test_new_enrollment_on_same_ccn_is_a_sale(tmp_path, monkeypatch):
    events, internal = _reenroll(tmp_path, monkeypatch,
                                 enroll_new=[_enr("E2", "371660", "ANTHEM HOSPICE OF OKLAHOMA CITY LLC")],
                                 enroll_old=[_enr("E1", "371660", "GOOD JOURNEY HOSPICE LLC")],
                                 owners_new=[_own("E2", "CENTRIX FUND I LP", "01/01/2020")],  # old date: owner diff misses it
                                 owners_old=[_own("E1", "JOURNEY HOLDINGS INC", "01/01/2018")],
                                 pos={"371660": date(2026, 2, 1)})
    assert internal == 0 and len(events) == 1
    e = events[0]
    assert (e["old_legal"], e["new_legal"], e["owners"], e["date"]) == (
        "GOOD JOURNEY HOSPICE LLC", "ANTHEM HOSPICE OF OKLAHOMA CITY LLC", ["CENTRIX FUND I LP"], date(2026, 2, 1))
    a = p._reenrollment_articles("hospice", events)[0]
    assert (a["acquiring_entity"], a["seller_entity"], a["ccns"], a["acquisition_date"]) == (
        "CENTRIX FUND I LP", "GOOD JOURNEY HOSPICE LLC", ["371660"], "2026-02-01")
    assert a["extraction_model"] == "cms_hospice_owner_change"


def test_shared_owner_or_same_system_is_internal(tmp_path, monkeypatch):
    events, internal = _reenroll(tmp_path, monkeypatch,
                                 enroll_new=[_enr("E2", "511523", "AMEDISYS HOSPICE, L.L.C."),
                                             _enr("E4", "421540", "PRISMA HEALTH-MIDLANDS")],
                                 enroll_old=[_enr("E1", "511523", "TENDER LOVING CARE OF WV, LLC"),
                                             _enr("E3", "421540", "PRISMA HEALTH TUOMEY")],
                                 owners_new=[_own("E2", "AMEDISYS INC", "01/01/2010")],
                                 owners_old=[_own("E1", "AMEDISYS INC", "01/01/2010")])
    assert events == [] and internal == 2


def test_buyer_keeping_the_name_is_still_a_sale(tmp_path, monkeypatch):
    events, internal = _reenroll(tmp_path, monkeypatch,
                                 enroll_new=[_enr("E2", "451600", "HOSPICE OF LUBBOCK LLC")],
                                 enroll_old=[_enr("E1", "451600", "HOSPICE OF LUBBOCK INC")],
                                 owners_new=[_own("E2", "CP TX HOLDINGS LLC", "02/01/2026")],
                                 owners_old=[])
    # same legal key ("HOSPICE OF LUBBOCK") -> a re-papering, left out
    assert events == [] and internal == 1
    events, internal = _reenroll(tmp_path, monkeypatch,
                                 enroll_new=[_enr("E2", "451600", "LUBBOCK HOSPICE OPCO LLC")],
                                 enroll_old=[_enr("E1", "451600", "HOSPICE OF LUBBOCK INC")],
                                 owners_new=[_own("E2", "CP TX HOLDINGS LLC", "02/01/2026")],
                                 owners_old=[])
    assert len(events) == 1 and internal == 0


def test_already_seen_sale_is_not_repeated(tmp_path, monkeypatch):
    kw = dict(enroll_new=[_enr("E2", "371660", "ANTHEM HOSPICE LLC")],
              enroll_old=[_enr("E1", "371660", "GOOD JOURNEY HOSPICE LLC")],
              owners_new=[], owners_old=[])
    events, _ = _reenroll(tmp_path, monkeypatch, seen={("371660", "SOMEONE", date(2026, 8, 1))}, **kw)
    assert events == []  # an owner-diff card for this CCN within 120 days
