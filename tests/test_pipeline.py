"""
Basic test suite.
Run with: pytest tests/
"""

import pytest
from pipeline.extractor import extract_deals, _normalize_deal, _clean_states
from pipeline.dedup import make_dedup_hash, deduplicate_batch


# ── Extractor unit tests ──────────────────────────────────────

def test_normalize_deal_valid():
    raw = {
        "acquiring_entity": "Hill Valley Healthcare",
        "seller_entity": "Welltower",
        "operator_names": ["Hill Valley Healthcare"],
        "facility_names": ["Lakeside Health & Rehabilitation"],
        "states": ["VA"],
        "facility_count": 2,
        "deal_value_m": 82.4,
        "acquisition_date": "2026-01-13",
        "financing_amount_m": None,
        "lender": None,
        "rationale": "Hill Valley acquired two Virginia SNFs from Welltower.",
    }
    result = _normalize_deal(raw)
    assert result is not None
    assert result["acquiring_entity"] == "Hill Valley Healthcare"
    assert result["deal_value_m"] == 82.4
    assert result["states"] == ["VA"]


def test_normalize_deal_null_acquirer():
    """Deals with no identifying info should return None."""
    raw = {
        "acquiring_entity": None,
        "seller_entity": None,
        "operator_names": [],
        "facility_names": [],
        "states": [],
    }
    result = _normalize_deal(raw)
    assert result is None


def test_clean_states():
    assert _clean_states(["VA", "CO", "al"]) == ["VA", "CO", "AL"]
    assert _clean_states(["Virginia"]) == ["VI"]   # truncates to 2 chars
    assert _clean_states([]) == []
    assert _clean_states(None) == []


# ── Dedup tests ───────────────────────────────────────────────

def test_dedup_hash_stable():
    deal = {
        "acquiring_entity": "Hill Valley Healthcare LLC",
        "states": ["VA"],
        "acquisition_date": "2026-01-13",
        "facility_count": 2,
        "deal_value_m": 82.4,
    }
    h1 = make_dedup_hash(deal)
    h2 = make_dedup_hash(deal)
    assert h1 == h2


def test_dedup_hash_strips_legal_suffixes():
    deal_llc = {"acquiring_entity": "Hill Valley Healthcare LLC", "states": ["VA"],
                "acquisition_date": "2026-01-13", "facility_count": None, "deal_value_m": None}
    deal_inc = {"acquiring_entity": "Hill Valley Healthcare INC", "states": ["VA"],
                "acquisition_date": "2026-01-13", "facility_count": None, "deal_value_m": None}
    assert make_dedup_hash(deal_llc) == make_dedup_hash(deal_inc)


def test_dedup_hash_different_deals():
    deal_a = {"acquiring_entity": "Hill Valley Healthcare", "states": ["VA"],
              "acquisition_date": "2026-01-13", "facility_count": 2, "deal_value_m": 82.4}
    deal_b = {"acquiring_entity": "Capital Funding Group", "states": ["CO", "AL", "AZ"],
              "acquisition_date": "2026-02-02", "facility_count": 4, "deal_value_m": 51.2}
    assert make_dedup_hash(deal_a) != make_dedup_hash(deal_b)


def test_deduplicate_batch_removes_dupes():
    deal = {"acquiring_entity": "Hill Valley Healthcare", "states": ["VA"],
            "acquisition_date": "2026-01-13", "facility_count": 2, "deal_value_m": 82.4}
    batch = [deal, dict(deal)]  # same deal twice
    result = deduplicate_batch(batch)
    assert len(result) == 1


def test_deduplicate_batch_keeps_different():
    deal_a = {"acquiring_entity": "Hill Valley Healthcare", "states": ["VA"],
              "acquisition_date": "2026-01-13", "facility_count": 2, "deal_value_m": 82.4}
    deal_b = {"acquiring_entity": "Capital Funding Group", "states": ["CO"],
              "acquisition_date": "2026-02-02", "facility_count": 4, "deal_value_m": 51.2}
    result = deduplicate_batch([deal_a, deal_b])
    assert len(result) == 2
