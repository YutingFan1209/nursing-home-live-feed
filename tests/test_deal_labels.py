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
from pipeline.deal_labels import buyer_fields, facility_type, label, transaction_type
from pipeline.individual_owners import first_last

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "deals_2026_10_12.json").read_text())
TODAY = date(2026, 10, 12)


def raw(name):
    """The row as the export sees it before labeling: deal_type from the
    old rules, no label fields yet."""
    row = {k: v for k, v in FIXTURES[name].items() if not k.startswith("_")}
    return row


def labeled(name, owner_starts=None):
    row = raw(name)
    return {**row, **label(row, owner_starts or {}, export._resolved_facilities(row), today=TODAY)}


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
    d = labeled("selectis_ga", owner_starts={"115622": {date(2020, 7, 1)}})
    assert d["buyer_disclosure"] == "not_named"
    assert "Buyer not disclosed" not in text(d["headline"])
    assert d["cms_confirmed_on"] is None and d["status"] == "reported"


def test_cms_confirmation_needs_a_new_owner_near_the_deal_date():
    # no deal date on this row, so the window is measured from when it was found (2026-10-08)
    d = labeled("selectis_ga", owner_starts={"115622": {date(2026, 9, 1)}})
    assert d["cms_confirmed_on"] == "2026-09-01" and d["status"] == "confirmed"
    far = labeled("selectis_ga", owner_starts={"115622": {date(2025, 12, 1)}})
    assert far["cms_confirmed_on"] is None


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
    d = {**row, **label(row, {}, [], today=TODAY)}
    assert d["status"] == "expected"
    assert "acquire" not in text(d["headline"]) or "to acquire" in text(d["headline"]) or "to sell" in text(d["headline"])
