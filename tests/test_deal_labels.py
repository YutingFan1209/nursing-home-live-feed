"""
Labels on real rows from the live site (tests/fixtures/deals_2026_10_12.json,
exported 2026-10-12; each row's `_before_site` is what the site showed
before pipeline/deal_labels.py).
"""
import json
from datetime import date
from pathlib import Path

import pytest

import scripts.export_deals as export
from collections import defaultdict

from pipeline.cms_confirm import CmsIndex, facility_key
from pipeline.deal_groups import group, same_deal
from pipeline.deal_labels import buyer_fields, facility_type, label, transaction_type
from pipeline.individual_owners import first_last

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "deals_2026_10_12.json").read_text())
TODAY = date(2026, 10, 12)


def raw(name):
    """The row as the export sees it before labeling: deal_type from the
    old rules, no label fields yet."""
    row = {k: v for k, v in FIXTURES[name].items() if not k.startswith("_")}
    return row


def fake_cms(events=None, facilities=None):
    """A CmsIndex without the database. events: ccn -> [(date, owner, source)];
    facilities: ccn -> (name, state, beds)."""
    cms = CmsIndex.__new__(CmsIndex)
    cms.events = defaultdict(list, events or {})
    cms.facility = dict(facilities or {})
    cms.by_name, cms.truncated = defaultdict(set), defaultdict(list)
    for ccn, (fname, state, _) in cms.facility.items():
        cms.by_name[(state, facility_key(fname))].add(ccn)
        if len(fname) >= 50:
            cms.truncated[state].append((facility_key(fname).rsplit(" ", 1)[0], ccn))
    return cms


def labeled(name, events=None):
    row = raw(name)
    return {**row, **label(row, fake_cms(events), export._resolved_facilities(row), today=TODAY)}


def text(headline):
    return "".join(t for t, _ in headline)


# ── 1. Buyer not disclosed ────────────────────────────────────────────────

def test_named_buyer_is_kept_even_when_it_looks_like_an_address():
    # The court-approved buyer really is "101 W State Street Holdings LLC"
    # (Maine CON LOI, AL SHPDA CO2026-067)
    d = labeled("genesis")
    assert d["buyer"] == "101 W State Street Holdings LLC"
    assert d["buyer_disclosure"] == "named"
    assert "acquired" not in text(d["headline"])


def test_lease_is_its_own_type_never_buyer_not_disclosed():
    d = labeled("razorback")
    assert d["deal_type"] == "lease"
    assert "Buyer not disclosed" not in text(d["headline"])
    assert text(d["headline"]) == "New lease: 2 facilities in AR"


def test_described_buyer_shows_the_description():
    d = labeled("blueprint_oh")
    assert d["buyer_disclosure"] == "described"
    assert d["buyer_description"] == "a national owner and a new operator"
    assert text(d["headline"]) == "Sale of 5 facilities in OH to a national owner and a new operator"


def test_description_in_the_name_field_moves_to_description():
    d = labeled("blueprint_dayton")
    assert d["buyer"] is None and d["buyer_description"] == "a regional owner/operator"
    reit = labeled("blueprint_reit")
    assert reit["buyer_description"] == "an Ohio-based provider" and reit["seller"] is None


def test_buyer_not_disclosed_only_when_the_source_says_so():
    d = labeled("rhinebeck")
    assert d["buyer_disclosure"] == "undisclosed"
    assert ["Buyer not disclosed", "undisclosed"] in d["headline"]
    assert d["status"] == "expected"


def test_unnamed_buyer_without_description_is_not_called_undisclosed():
    # Selectis' GA sale: the article doesn't name the buyer and CMS still
    # lists Selectis as owner, so there's no name to show and no CMS confirmation
    d = labeled("selectis_ga", {"115622": [(date(2020, 7, 1), "SELECTIS HEALTH INC", "CMS ownership data")]})
    assert d["buyer_disclosure"] == "not_named"
    assert "Buyer not disclosed" not in text(d["headline"])
    assert d["cms_confirmed_on"] is None and d["status"] == "reported"


def test_cms_new_owner_confirms_and_names_the_buyer():
    # no deal date on this row, so the window is measured from when it was found (2026-10-08)
    d = labeled("selectis_ga", {"115622": [(date(2026, 9, 1), "EASTMAN OPCO LLC", "CMS ownership data")]})
    assert d["cms_confirmed_on"] == "2026-09-01" and d["status"] == "confirmed"
    assert d["buyer"] == "EASTMAN OPCO LLC" and d["buyer_disclosure"] == "cms"
    assert "new owner per CMS: EASTMAN OPCO LLC" in text(d["headline"])
    assert "EASTMAN OPCO LLC as owner" in d["status_detail"]
    far = labeled("selectis_ga", {"115622": [(date(2025, 12, 1), "EASTMAN OPCO LLC", "CMS ownership data")]})
    assert far["cms_confirmed_on"] is None


def test_several_new_cms_owners_confirm_without_naming_a_buyer():
    # Oklahoma stock transfers: family trusts all become owners the same day
    d = labeled("selectis_ga", {"115622": [(date(2026, 9, 1), "BGM ESTATE LLC", "CMS ownership data"),
                                           (date(2026, 9, 1), "TIFFANY SEAY EXEMPT TR", "CMS ownership data")]})
    assert d["status"] == "confirmed" and "2 new owners" in d["status_detail"]
    assert d["buyer"] is None and d["buyer_disclosure"] == "not_named"


def test_state_filing_without_buyer_field_is_undisclosed():
    assert buyer_fields({"source_type": "con", "source_title": "[OK CON] 26-053 CN Acquisition: X"})["buyer_disclosure"] == "undisclosed"


# ── 2. Control change ─────────────────────────────────────────────────────

def test_eger_is_a_control_change_with_a_neutral_headline():
    d = labeled("eger")
    assert d["deal_type"] == "control_change"
    assert text(d["headline"]) == "ArchCare becomes active parent of Eger Health Care and Rehabilitation Center"
    assert d["buyer"] is None and d["buyer_disclosure"] is None


def test_maine_acquisition_of_control_is_still_a_sale():
    d = labeled("genesis_me_con")
    assert d["deal_type"] == "ownership_change"
    assert text(d["headline"]).startswith("101 W State Street Holdings LLC to acquire")


def test_control_change_rule_is_not_state_specific():
    assert transaction_type({"source_type": "con", "deal_type": "ownership_change",
                             "summary": "Sunrise Health becomes sole member through a member substitution in Ohio."}) == "control_change"


# ── 3. Individual UCC filings ─────────────────────────────────────────────

def individual(name):
    row = raw(name)
    export._add_labels(row, {}, {row["id"]: [tuple(h) for h in FIXTURES[name]["_homes"]]})
    return row


def in_default_financing_view(d):
    """The frontend's default Financing tab (App.jsx filterDeals + inTab):
    not an ownership event, not a refinancing, skilled-nursing related."""
    return (d["deal_type"] not in ("ownership_change", "control_change", "lease")
            and d["deal_type"] != "refinancing" and d["facility_type"] == "skilled_nursing")


def test_single_home_owner_links_to_that_home():
    d = individual("individual_single")
    assert d["match_type"] == "single_home"
    assert d["possible_homes"] == [{"ccn": "335396", "name": "MONTGOMERY NURSING AND REHABILITATION CENTER"}]
    assert d["owner_line"] == "Owner of Montgomery Nursing and Rehabilitation Center"


@pytest.mark.parametrize("name,n", [("individual_multi", 4), ("individual_11_homes", 11), ("individual_33_homes", 33)])
def test_multi_home_owner_lists_possible_homes(name, n):
    d = individual(name)
    assert d["match_type"] == "multi_home" and len(d["possible_homes"]) == n
    # every candidate kept, none presented as the one home
    assert sorted(h["ccn"] for h in d["possible_homes"]) == sorted(c for c, _ in FIXTURES[name]["_homes"])
    assert d["owner_line"].startswith("Owner of ")
    assert d["owner_line"].count(",") == 2
    if n > 3:
        assert d["owner_line"].endswith(f" +{n - 3} more")


def test_unmatched_filing_has_no_home():
    d = individual("individual_unmatched")
    assert d["match_type"] == "unmatched" and d["possible_homes"] == []
    assert d["owner_line"] == "Filed against an individual; no matching home in CMS ownership records."
    # the old fuzzy facility match (Bethel Health Care Center) is gone
    assert not d["facility_names"] and not d["ccns"]


@pytest.mark.parametrize("name", ["individual_single", "individual_multi", "individual_11_homes",
                                  "individual_33_homes", "individual_unmatched"])
def test_individual_filings_show_in_financing_with_the_tag(name):
    d = individual(name)
    assert d["individual_filing"] and in_default_financing_view(d)


def test_individual_names_can_be_withheld(monkeypatch):
    monkeypatch.setattr(export, "SHOW_INDIVIDUAL_NAMES", False)
    d = individual("individual_single")
    assert d["ucc_debtor"] is None and d["operator_names"] is None
    assert "ZACHARY" not in json.dumps(d).upper()
    assert text(d["headline"]).startswith("Individual owner")


def test_first_last_name_matching():
    assert first_last("ROBERT W HURLBUT") == first_last("ROBERT HURLBUT")


# ── 4. Financing classification ───────────────────────────────────────────

@pytest.mark.parametrize("name,deal_type", [
    ("pgim_acoya", "acquisition_financing"), ("cambridge_ky", "acquisition_financing"),
    ("lourdes", "refinancing"), ("cibc_53m", "refinancing")])
def test_financing_subtypes(name, deal_type):
    assert labeled(name)["deal_type"] == deal_type


# ── 5. Facility type ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name,ftype", [
    ("genesis", "skilled_nursing"), ("pgim_acoya", "senior_living"), ("lourdes", "senior_living"),
    ("strawberry_marshall", "skilled_nursing"),  # hospital campus with a 99-bed SNF
    ("eger", "skilled_nursing")])
def test_facility_types(name, ftype):
    assert facility_type(raw(name)) == ftype


def test_non_us_and_hospital_types():
    assert facility_type({"source_type": "rss", "states": [], "summary": "CareTrust acquired three UK care homes"}) == "non_us"
    assert facility_type({"source_type": "rss", "states": ["MO"], "summary": "plans to acquire a hospital campus"}) == "hospital"


# ── Headlines never say "acquired" ────────────────────────────────────────

def test_no_headline_says_acquired():
    for name in FIXTURES:
        assert " acquired " not in text(labeled(name)["headline"]), name


def test_sale_awaiting_court_approval_is_expected_not_reported():
    # the original Genesis summary: past tense, but approval still pending
    row = {**raw("genesis"), "summary": FIXTURES["genesis"]["summary"]}
    d = {**row, **label(row, None, [], today=TODAY)}
    assert d["status"] in ("expected", "unconfirmed")
    assert "acquire" not in text(d["headline"]) or "to acquire" in text(d["headline"]) or "to sell" in text(d["headline"])


# ── One card per transaction ──────────────────────────────────────────────

def test_genesis_news_and_maine_filing_are_one_card():
    deals = [labeled("genesis"), labeled("genesis_me_con")]
    counts = group(deals, {})
    assert counts["groups"] == 1
    lead = next(d for d in deals if not d.get("grouped_into"))
    assert {s["id"] for s in lead["sources"]} == {d["id"] for d in deals}


def _deal(**kw):
    base = {"id": kw.pop("id"), "deal_type": "ownership_change", "acquisition_date": "2025-10-31",
            "created_at": "2025-11-01", "status": "expected", "headline": [["x", ""]]}
    return {**base, **kw}


def test_similar_but_different_buyers_stay_apart():
    # Two propcos at the same facility: fuzzy scores put these at 90
    a = _deal(id="a", buyer="North Mobile SNF Property Holdings", ccns=["015379"])
    b = _deal(id="b", buyer="Mobile SNF Property Holdings", ccns=["015379"])
    assert same_deal(a, b) is None


def test_same_buyer_and_seller_with_different_facility_counts_stay_apart():
    a = _deal(id="a", buyer="National HealthCare", seller="National Health Investors", facility_count=5)
    b = _deal(id="b", buyer="National Healthcare", seller="National Health Investors", facility_count=35)
    assert same_deal(a, b) is None
    c = _deal(id="c", buyer="Saber Healthcare Group", seller="Ciena Healthcare", facility_count=None)
    d = _deal(id="d", buyer="Saber Healthcare", seller="Ciena Healthcare", facility_count=18)
    assert same_deal(c, d) == "same buyer and seller"


def test_merged_rows_are_listed_as_sources():
    deals = [labeled("eger")]
    group(deals, {deals[0]["id"]: [{"id": "m1", "source_type": "rss", "headline": "earlier story"}]})
    assert [s.get("merged") for s in deals[0]["sources"]] == [None, True]


# ── Beds ──────────────────────────────────────────────────────────────────

def test_beds_only_when_every_facility_is_matched():
    cms = fake_cms(facilities={"1": ("A", "NY", 100), "2": ("B", "NY", 50)})
    assert cms.beds(["1", "2"], facility_count=2) == 150
    assert cms.beds(["1"], facility_count=34) is None  # PACS: one match out of 34 homes
    assert cms.beds(["1"]) == 100


def test_truncated_cms_name_still_matches_exactly():
    cms = fake_cms(facilities={"335332": ("Archcare at Eger Health Care and Rehabilitation Ce", "NY", 378)})
    assert cms.ccns_by_name(["ArchCare at Eger Healthcare and Rehabilitation Center"], ["NY"]) == ["335332"]
    assert cms.ccns_by_name(["Eger Health Care Center"], ["NY"]) == []


# ── Overdue pending records ───────────────────────────────────────────────

def test_long_pending_filing_turns_unconfirmed():
    d = labeled("eger")  # PHHPC approval 2025-12-04, ten months before TODAY
    assert d["status"] == "unconfirmed"
    assert "over 6 months ago" in d["status_detail"] and "new owner" not in d["status_detail"]
    recent = labeled("rhinebeck")  # reported pending, found 2026
    assert recent["status"] == "expected"


# ── Rewritten-summary phrasing (2026-10-12) ───────────────────────────────

def _news(summary, **kw):
    return {"source_type": "rss", "deal_type": "ownership_change", "summary": summary, **kw}


def test_unnamed_buyer_is_not_undisclosed():
    d = buyer_fields(_news("Fallbrook Rehabilitation & Care Center sold for $15 million to an unnamed buyer."))
    assert d["buyer_disclosure"] == "not_named"
    d = buyer_fields(_news("A five-facility Ohio portfolio was sold to an unnamed national owner and a new operator "
                           "entering the Ohio market; the sale has closed."))
    assert d["buyer_description"] == "a national owner and a new operator"
    d = buyer_fields(_news("An owner-operator sold a Colorado community to an unnamed Utah-based buyer for $20.8 million."))
    assert d["buyer_description"] == "a Utah-based buyer"
    d = buyer_fields(_news("ArchCare plans to sell its Rhinebeck home to an undisclosed buyer."))
    assert d["buyer_disclosure"] == "undisclosed"


def test_buyer_descriptions_in_rewritten_phrasing():
    d = buyer_fields(_news("A private real estate investment group, using a New York-based operator, acquired a "
                           "four-community portfolio in western New York."))
    assert d["buyer_description"] == "a private real estate investment group"
    d = buyer_fields(_news("SLIB brokered the sale. The seller was a large operator; the buyer was a West Coast "
                           "owner partnering with a local operator."))
    assert d["buyer_description"] == "a West Coast owner"


def test_operator_takeover_under_landlord_lease_is_a_lease():
    s = ("PACS Group closed on its takeover of operations of 32 Florida nursing homes owned by Omega Healthcare "
         "Investors. The properties join Omega's master lease with PACS, with rent rising $26.1M.")
    assert transaction_type(_news(s)) == "lease"
    assert transaction_type(_news("A new lease commenced in July 2026 for a 96-bed skilled nursing facility in "
                                  "New Harmony, Indiana.")) == "lease"
    # a purchase whose buyer will operate under a lease stays a sale
    assert transaction_type(_news("Ensign announced the acquisition of four skilled nursing facilities; an "
                                  "affiliate will operate them under long-term triple net leases.")) == "ownership_change"


def test_will_operate_is_not_pending_and_took_over_is_done():
    from pipeline.deal_labels import status
    done = status(_news("Ensign announced the acquisition of four facilities; an affiliate will operate them."), None, TODAY)
    assert done["status"] == "reported"
    took = status(_news("Prestige took over operations of four homes on June 30, 2026. A fifth remains pending."), None, TODAY)
    assert took["status"] == "reported"
