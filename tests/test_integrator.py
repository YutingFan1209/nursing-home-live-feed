import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date
from ucc.base import UCCFiling
from ucc.integrator import route_filing, ExistingDeal, RoutingDecision


EXISTING_DEALS = [
    ExistingDeal(id=101, operator_names=["HCR ManorCare"], facility_names=["Sunset Manor Nursing"],
                 acquisition_date=date(2024, 3, 1)),
    ExistingDeal(id=102, operator_names=["Genesis Healthcare"], facility_names=["Riverview Care Center"],
                 acquisition_date=date(2024, 6, 10)),
]


def test_confirmation_when_name_and_date_match():
    filing = UCCFiling(
        state="KY", debtor_name="HCR ManorCare LLC", secured_party_name="Omega Healthcare Investors",
        filing_number="001", filing_date=date(2024, 3, 10),
    )
    result = route_filing(filing, EXISTING_DEALS)
    assert result.decision == RoutingDecision.CONFIRMATION
    assert result.matched_deal_id == 101


def test_new_signal_when_no_matching_deal():
    filing = UCCFiling(
        state="KY", debtor_name="Brand New Operator Holdings LLC", secured_party_name="Formation Capital",
        filing_number="002", filing_date=date(2024, 9, 1),
    )
    result = route_filing(filing, EXISTING_DEALS)
    assert result.decision == RoutingDecision.NEW_SIGNAL
    assert result.matched_deal_id is None


def test_excluded_when_not_acquisition_relevant():
    filing = UCCFiling(
        state="KY", debtor_name="HCR ManorCare LLC", secured_party_name="De Lage Landen Financial",
        filing_number="003", filing_date=date(2024, 3, 12),
    )
    result = route_filing(filing, EXISTING_DEALS)
    assert result.decision == RoutingDecision.EXCLUDED
    assert result.matched_deal_id is None


def test_no_match_when_date_too_far_apart():
    """Same operator name, but filing date is way outside the matching
    window -- should NOT confirm, since it's plausibly an unrelated,
    later financing event for the same long-running operator."""
    filing = UCCFiling(
        state="KY", debtor_name="HCR ManorCare LLC", secured_party_name="Omega Healthcare Investors",
        filing_number="004", filing_date=date(2026, 1, 1),  # ~22 months after the deal
    )
    result = route_filing(filing, EXISTING_DEALS)
    assert result.decision == RoutingDecision.NEW_SIGNAL


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
