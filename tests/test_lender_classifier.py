"""
tests/test_lender_classifier.py

These don't touch the network at all, so unlike the state adapters,
this is fully testable right now in any environment.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ucc.lender_classifier import classify_secured_party, LenderCategory


def test_known_reit():
    result = classify_secured_party("Omega Healthcare Investors, Inc.")
    assert result.category == LenderCategory.REAL_ESTATE
    assert result.is_acquisition_relevant
    assert result.confidence > 0.8


def test_known_pe_sponsor():
    result = classify_secured_party("Formation Capital LLC")
    assert result.category == LenderCategory.PRIVATE_EQUITY
    assert result.is_acquisition_relevant


def test_re_suffix_heuristic():
    result = classify_secured_party("Cardinal Healthcare Properties LLC")
    assert result.category == LenderCategory.REAL_ESTATE
    assert result.is_acquisition_relevant


def test_pe_suffix_heuristic():
    result = classify_secured_party("Bluepeak Capital Partners III, LP")
    assert result.category == LenderCategory.PRIVATE_EQUITY
    assert result.is_acquisition_relevant


def test_equipment_vendor_excluded():
    result = classify_secured_party("De Lage Landen Financial Services, Inc.")
    assert result.category == LenderCategory.EQUIPMENT_VENDOR
    assert not result.is_acquisition_relevant


def test_pharmacy_excluded():
    result = classify_secured_party("Omnicare Pharmacy Solutions")
    assert result.category == LenderCategory.EQUIPMENT_VENDOR
    assert not result.is_acquisition_relevant


def test_generic_bank_flagged_not_dropped():
    result = classify_secured_party("Wells Fargo Bank, N.A.")
    assert result.category == LenderCategory.BANK_GENERAL
    assert result.is_acquisition_relevant  # flagged for review, not silently dropped


def test_unknown_defaults_to_relevant():
    """Unrecognized lender names should default to relevant=True —
    better a false positive surfaced for human review than a missed
    real acquisition signal."""
    result = classify_secured_party("Random Unrecognized Lender XYZ")
    assert result.category == LenderCategory.UNKNOWN
    assert result.is_acquisition_relevant


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
