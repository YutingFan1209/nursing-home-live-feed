"""Scorer logic for the gold-set evals (evals/score.py). No DB, no API."""
from evals.score import load_gold, same, score_records, wilson


def test_names_ignore_suffix_and_case():
    assert same("buyer", "Clearview Land LLC", "CLEARVIEW LAND")
    assert not same("seller", "National Health", "National Health Corporation x")


def test_truncated_name_is_a_miss():
    # The shown seller dropped "Propco LLC": that must count as wrong.
    assert not same("seller", "Arabella Health & Wellness of Bessemer",
                    "Arabella Health & Wellness of Bessemer Propco LLC")


def test_nj_lender_placeholder_equals_none():
    assert same("lender", "Not available — NJ UCC search doesn't return lender names", None)


def test_numbers_dates_lists():
    assert same("deal_value_m", "10.50", 10.5)
    assert not same("deal_value_m", "10.40", None)
    assert same("acquisition_date", "2026-07-01T00:00:00", "2026-07-01")
    assert same("states", ["TX", "FL"], ["FL", "TX"])
    assert not same("facility_count", 43, 41)


def _gold(verdict, **gold):
    return {"id": "x", "group": "news", "verdict": verdict, "gold": gold,
            "wrong_fields": [], "unsure_fields": [], "note": "n"}


def test_duplicate_is_fixed_only_when_hidden():
    g = [_gold("duplicate", buyer="A")]
    assert score_records(g, {"x": {"buyer": "A"}}, ["buyer"])["records"]["news"] == [0, 1]
    assert score_records(g, {}, ["buyer"])["records"]["news"] == [1, 1]


def test_unsure_fields_are_not_scored():
    g = [_gold("uncertain", buyer="A")]
    g[0]["unsure_fields"] = ["buyer"]
    res = score_records(g, {"x": {"buyer": "B"}}, ["buyer"])
    assert res["records"]["news"] == [1, 1] and "buyer" not in res["fields"]


def test_wilson_bounds():
    lo, hi = wilson(6, 8)
    assert 0.40 < lo < 0.42 and 0.92 < hi < 0.94


def test_gold_file_is_well_formed():
    gold = load_gold()
    assert len(gold) == 150 and len({g["id"] for g in gold}) == 150
    for g in gold:
        assert g["verdict"] in {"right", "wrong", "minor", "duplicate", "noise", "uncertain"}
        assert set(g["wrong_fields"]) <= set(g["gold"]) | {"facility_names"}
