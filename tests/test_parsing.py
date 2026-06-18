"""
tests/test_parsing.py

Verifies the parts of ky.py/nj.py that don't depend on live network
access: the table-parsing logic, given realistic mock HTML shaped like
what these ASP.NET results pages typically return. The actual `search()`
methods (which fetch live pages) still need verification once field
names are confirmed via discover_form_fields.py — but the parsing logic
itself is correct now, independent of that.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ucc.base import UCCFiling
from ucc.ky import KentuckyUCCSource
from ucc.nj import NewJerseyUCCSource
from ucc.me import MaineUCCSource


def test_ucc_filing_dedup_hash_consistent():
    f1 = UCCFiling(state="KY", debtor_name="ABC NURSING LLC",
                    secured_party_name="Omega Healthcare", filing_number="2024-001")
    f2 = UCCFiling(state="KY", debtor_name="ABC NURSING LLC",
                    secured_party_name="Different Party", filing_number="2024-001")
    # Same (state, filing_number) -> same hash, even if other fields differ
    assert f1.dedup_hash() == f2.dedup_hash()


def test_ucc_filing_dedup_hash_differs_across_states():
    f1 = UCCFiling(state="KY", debtor_name="X", secured_party_name="Y", filing_number="001")
    f2 = UCCFiling(state="NJ", debtor_name="X", secured_party_name="Y", filing_number="001")
    assert f1.dedup_hash() != f2.dedup_hash()


def test_normalized_debtor_handles_whitespace_case():
    f = UCCFiling(state="KY", debtor_name="  HCR   ManorCare ",
                   secured_party_name="X", filing_number="1")
    assert f.normalized_debtor() == "hcr manorcare"


KY_MOCK_HTML = """
<html><body>
<table id="gvResults">
  <tr><th>Filing #</th><th>Date</th><th>Debtor</th><th>Secured Party</th></tr>
  <tr><td>2024-0001234</td><td>03/15/2024</td><td>SUNSET MANOR LLC</td><td>OMEGA HEALTHCARE INVESTORS</td></tr>
  <tr><td>2024-0005678</td><td>07/22/2024</td><td>SUNSET MANOR LLC</td><td>DE LAGE LANDEN FINANCIAL</td></tr>
</table>
</body></html>
"""

NJ_MOCK_HTML = """
<html><body>
<table id="gvSearchResults">
  <tr><th>Filing #</th><th>Date</th><th>Debtor</th><th>Secured Party</th><th>Status</th></tr>
  <tr><td>NJ-998877</td><td>01/10/2025</td><td>RIVERVIEW CARE CENTER INC</td><td>CARETRUST REIT</td><td>Active</td></tr>
</table>
</body></html>
"""


def test_ky_parser_extracts_two_filings():
    source = KentuckyUCCSource()
    filings = source._parse_search_results(KY_MOCK_HTML, "SUNSET MANOR LLC")
    assert len(filings) == 2
    assert filings[0].filing_number == "2024-0001234"
    assert filings[0].secured_party_name == "OMEGA HEALTHCARE INVESTORS"
    assert filings[0].filing_date.isoformat() == "2024-03-15"


def test_nj_parser_extracts_one_filing_with_status():
    source = NewJerseyUCCSource()
    filings = source._parse_search_results(NJ_MOCK_HTML)
    assert len(filings) == 1
    assert filings[0].status == "active"
    assert filings[0].secured_party_name == "CARETRUST REIT"


def test_parser_returns_empty_list_when_table_missing():
    source = KentuckyUCCSource()
    filings = source._parse_search_results("<html><body>No results</body></html>", "X")
    assert filings == []


ME_MOCK_HTML_WITH_SECURED_PARTY = """
<html><body>
<table id="results">
  <tr><th>Filing #</th><th>Debtor</th><th>Date</th><th>Secured Party</th></tr>
  <tr><td>ME-445566</td><td>PINE TREE CARE CENTER</td><td>05/02/2025</td><td>SABRA HEALTH CARE</td></tr>
</table>
</body></html>
"""

# Tests the limitation flagged in me.py's docstring: free tier may not
# include secured-party name at all
ME_MOCK_HTML_WITHOUT_SECURED_PARTY = """
<html><body>
<table id="results">
  <tr><th>Filing #</th><th>Debtor</th><th>Date</th></tr>
  <tr><td>ME-998811</td><td>COASTAL REHAB LLC</td><td>11/19/2024</td></tr>
</table>
</body></html>
"""


def test_me_parser_extracts_filing_with_secured_party():
    source = MaineUCCSource()
    filings = source._parse_search_results(ME_MOCK_HTML_WITH_SECURED_PARTY)
    assert len(filings) == 1
    assert filings[0].secured_party_name == "SABRA HEALTH CARE"
    assert filings[0].raw["secured_party_confirmed"] is True


def test_me_parser_degrades_gracefully_without_secured_party():
    """Confirms the adapter doesn't crash if Maine's free tier omits
    secured-party data — leaves it empty rather than raising, per the
    'may not include lender name' limitation noted in me.py."""
    source = MaineUCCSource()
    filings = source._parse_search_results(ME_MOCK_HTML_WITHOUT_SECURED_PARTY)
    assert len(filings) == 1
    assert filings[0].secured_party_name == ""
    assert filings[0].raw["secured_party_confirmed"] is False
    assert filings[0].debtor_name == "COASTAL REHAB LLC"


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
