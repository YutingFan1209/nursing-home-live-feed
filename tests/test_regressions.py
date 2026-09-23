"""
Regression tests for bugs that failed silently in production -- each one
let the pipeline log success while dropping data. No network or database:
I/O is monkeypatched.

Run with: venv/bin/python3 -m pytest tests/
"""
import logging
from datetime import date, datetime, timedelta, timezone

import pytest
import requests

import scraper.chow as chow
import scraper.rss as rss
from pipeline.dedup import make_dedup_hash
from pipeline.source_health import check_source_health
from scripts.export_deals import _news_deal_type, _ucc_fields
from ucc.lender_classifier import LenderCategory, classify_secured_party


# ── UCC dedup (fixed 2026-09-21) ──────────────────────────────
# New-signal UCC deals have no acquirer/count/value, so the generic hash
# collapsed to state+month and every later same-month filing was dropped.

def _ucc_signal(filing_number):
    return {"extraction_model": "ucc_filing", "_ucc_filing_number": filing_number,
            "states": ["OH"], "acquisition_date": "2026-03-10",
            "acquiring_entity": None, "facility_count": None, "deal_value_m": None}


def test_ucc_same_state_same_month_filings_get_distinct_hashes():
    assert make_dedup_hash(_ucc_signal("OH001")) != make_dedup_hash(_ucc_signal("OH002"))


def test_ucc_same_filing_hashes_stably():
    assert make_dedup_hash(_ucc_signal("OH001")) == make_dedup_hash(_ucc_signal("OH001"))


# ── Lender classifier (NJ fix, 2026-09-22) ────────────────────
# NJ's free search never returns a secured party; those filings must stay
# relevant, while a missing name elsewhere means unknown/possible bug.

def test_nj_missing_lender_is_still_relevant():
    assert classify_secured_party("", "NJ").is_acquisition_relevant


def test_missing_lender_outside_nj_is_not_relevant():
    assert not classify_secured_party("", "NY").is_acquisition_relevant


def test_equipment_vendor_excluded():
    result = classify_secured_party("SNAP-ON CREDIT LLC", "NY")
    assert result.category == LenderCategory.EQUIPMENT_VENDOR
    assert not result.is_acquisition_relevant


# ── CHOW freshness (fixed 2026-09-22) ─────────────────────────
# A 90-day effective-date window never matched anything because CHOW's
# effective dates lag publication by months. "New" now means "key never
# seen before", with old rows marked seen but not turned into deals.

def _chow_row(ccn, buyer, effective):
    return {"EFFECTIVE DATE": effective.strftime("%m/%d/%Y"), "ORGANIZATION NAME - BUYER": buyer,
            "ORGANIZATION NAME - SELLER": "Seller LLC", "CCN - BUYER": ccn,
            "ENROLLMENT STATE - BUYER": "OH", "CHOW TYPE TEXT": "CHANGE OF OWNERSHIP"}


@pytest.fixture
def chow_env(monkeypatch):
    seen = set()
    recent = date.today() - timedelta(days=200)  # outside the old 90-day window
    ancient = date.today() - timedelta(days=chow.CHOW_RECENCY_DAYS + 30)
    rows = [_chow_row("365001", "Recent Buyer LLC", recent), _chow_row("365002", "Old Buyer LLC", ancient)]
    monkeypatch.setattr(chow, "_discover_chow_csv_url", lambda: "https://example.test/chow.csv")
    monkeypatch.setattr(chow, "_download_chow_csv", lambda url: rows)
    monkeypatch.setattr(chow, "_load_seen_chow_keys", lambda conn: set(seen))
    monkeypatch.setattr(chow, "_mark_chow_keys_seen", lambda conn, keys: seen.update(keys))
    return seen


def test_chow_lagged_effective_date_still_becomes_a_deal(chow_env):
    deals = chow.fetch_chow_deals(conn=None)
    assert [d["acquiring_entity"] for d in deals] == ["Recent Buyer LLC"]


def test_chow_old_rows_are_marked_seen_without_becoming_deals(chow_env):
    chow.fetch_chow_deals(conn=None)
    assert {k[1] for k in chow_env} == {"Recent Buyer LLC", "Old Buyer LLC"}


def test_chow_second_run_returns_nothing_new(chow_env):
    chow.fetch_chow_deals(conn=None)
    assert chow.fetch_chow_deals(conn=None) == []


# ── RSS fetch (fixed 2026-09-23) ──────────────────────────────
# feedparser's own urllib fetch failed CERTIFICATE_VERIFY_FAILED on every
# feed and reported it as an empty feed; nothing was logged.

FEED_XML = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Operator acquires 5 nursing homes</title><link>https://example.test/a</link></item>
<item><title>Staffing survey results</title><link>https://example.test/b</link></item>
</channel></rss>"""


class _Resp:
    content = FEED_XML
    def raise_for_status(self): pass


def test_rss_fetch_failure_is_logged(monkeypatch, caplog):
    def boom(*a, **kw):
        raise requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")
    monkeypatch.setattr(rss.requests, "get", boom)
    with caplog.at_level(logging.ERROR):
        assert rss.fetch_feed("https://example.test/feed") == []
    assert "CERTIFICATE_VERIFY_FAILED" in caplog.text


def test_rss_parses_fetched_content_and_filters(monkeypatch):
    monkeypatch.setattr(rss.requests, "get", lambda *a, **kw: _Resp())
    assert [a["url"] for a in rss.fetch_feed("https://example.test/feed")] == ["https://example.test/a"]


# ── Source health ─────────────────────────────────────────────

class _FakeConn:
    def __init__(self, rows): self.rows = rows
    def cursor(self): return self
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def execute(self, *a): pass
    def fetchall(self): return self.rows


def test_source_health_flags_silent_and_stale_sources():
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    conn = _FakeConn([
        ("Skilled Nursing News", "https://skillednursingnews.com/feed/", "rss", now - timedelta(days=125)),
        ("Senior Housing News", "https://seniorhousingnews.com/feed/", "rss", None),
        ("Google Alerts (Gmail)", "gmail://googlealerts-noreply@google.com", "rss", now - timedelta(days=1)),
        ("Provider Magazine", "https://www.providermagazine.com/feed/", "rss", None),  # inactive
    ])
    warnings = check_source_health(conn, now=now)
    assert len(warnings) == 2
    assert any("Skilled Nursing News" in w and "125 days" in w for w in warnings)
    assert any("Senior Housing News" in w and "never" in w for w in warnings)


# ── Export deal typing ────────────────────────────────────────

def test_ucc_fields_amendment_and_lien_and_financing():
    base = {"ucc_state": "NY", "source_title": "UCC-1 filing: ACME SNF LLC / SOME BANK, N.A. (NY)"}
    assert _ucc_fields({**base, "lender": "SOME BANK, N.A."}, None, "UCC3")["deal_type"] == "amendment"
    assert _ucc_fields({**base, "lender": "SNAP-ON CREDIT LLC"}, None, "UCC-1")["deal_type"] == "non_deal_lien"
    fields = _ucc_fields({**base, "lender": "SOME BANK, N.A."}, None, "UCC1")
    assert fields["deal_type"] == "financing"
    assert fields["lender_category"] == "Bank"
    assert fields["ucc_debtor"] == "ACME SNF LLC"  # fell back to the title


def test_ucc_fields_nj_placeholder_lender_has_no_category():
    deal = {"ucc_state": "NJ", "lender": "Not available — NJ UCC search doesn't return lender names"}
    assert _ucc_fields(deal, "HAMILTON OPERATOR LLC", "UCC-1")["lender_category"] is None


def test_news_deal_type_financing_only_from_extracted_fields():
    assert _news_deal_type({"financing_amount_m": 27, "lender": "CIBC"}) == "financing"
    assert _news_deal_type({"financing_amount_m": 27, "acquiring_entity": "PACS"}) == "ownership_change"
    assert _news_deal_type({"source_title": "Bridge Logistics refinances"}) == "ownership_change"
