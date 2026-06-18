import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from ucc.ny import NewYorkUCCSource


def make_test_xlsx(path):
    rows = [
        # One filing: secured party + co-debtor (related party) sharing same owner/date/lapse
        dict(Owner="TEST OWNER A", **{"Associate ID": "111"}, **{"Facility(ies)": "TEST FACILITY"},
             **{"Match Confidence": "HIGH - Likely true match"}, **{"Match Reason": "x"}, Role="Organization Secured Party",
             **{"Counterparty (Lender / Co-Debtor)": "TEST BANK"}, **{"Counterparty Address": "addr"},
             **{"UCC Status": "Active"}, **{"Date Filed": "1/1/20"}, **{"Lapse Date": "1/1/25"},
             **{"Matched Individual Debtors": "TEST OWNER A"}, **{"Related Party?": None}, **{"Non-Healthcare Lender?": None}),
        dict(Owner="TEST OWNER A", **{"Associate ID": "111"}, **{"Facility(ies)": "TEST FACILITY"},
             **{"Match Confidence": "HIGH - Likely true match"}, **{"Match Reason": "x"}, Role="Organization Co-Debtor",
             **{"Counterparty (Lender / Co-Debtor)": "SMITH FAMILY TRUST"}, **{"Counterparty Address": "addr"},
             **{"UCC Status": "Active"}, **{"Date Filed": "1/1/20"}, **{"Lapse Date": "1/1/25"},
             **{"Matched Individual Debtors": "TEST OWNER A"}, **{"Related Party?": "YES"}, **{"Non-Healthcare Lender?": None}),
        # A LOW-confidence false positive — should be dropped entirely
        dict(Owner="TEST OWNER B", **{"Associate ID": "222"}, **{"Facility(ies)": "OTHER FACILITY"},
             **{"Match Confidence": "LOW - Likely false positive"}, **{"Match Reason": "x"}, Role="Organization Secured Party",
             **{"Counterparty (Lender / Co-Debtor)": "JOHN DEERE CAPITAL"}, **{"Counterparty Address": "addr"},
             **{"UCC Status": "Inactive"}, **{"Date Filed": "1/1/15"}, **{"Lapse Date": "1/1/20"},
             **{"Matched Individual Debtors": "TEST OWNER B"}, **{"Related Party?": None}, **{"Non-Healthcare Lender?": "YES"}),
        # Exact duplicate of the first row — should be deduped
        dict(Owner="TEST OWNER A", **{"Associate ID": "111"}, **{"Facility(ies)": "TEST FACILITY"},
             **{"Match Confidence": "HIGH - Likely true match"}, **{"Match Reason": "x"}, Role="Organization Secured Party",
             **{"Counterparty (Lender / Co-Debtor)": "TEST BANK"}, **{"Counterparty Address": "addr"},
             **{"UCC Status": "Active"}, **{"Date Filed": "1/1/20"}, **{"Lapse Date": "1/1/25"},
             **{"Matched Individual Debtors": "TEST OWNER A"}, **{"Related Party?": None}, **{"Non-Healthcare Lender?": None}),
    ]
    pd.DataFrame(rows).to_excel(path, sheet_name="Flagged Data", index=False)


def test_low_confidence_dropped_and_duplicates_removed():
    path = "/tmp/test_ny_import.xlsx"
    make_test_xlsx(path)
    filings = NewYorkUCCSource().bulk_ingest_from_xlsx(path)

    # Only TEST OWNER A's secured-party row should survive (LOW dropped, dup removed)
    assert len(filings) == 1, f"expected 1 filing, got {len(filings)}"
    f = filings[0]
    assert f.debtor_name == "TEST OWNER A"
    assert f.secured_party_name == "TEST BANK"
    assert f.co_debtors == ["SMITH FAMILY TRUST"]
    assert f.is_related_party is True
    assert f.source_confidence.startswith("HIGH")
    assert f.state == "NY"


def test_dedup_hash_stable_across_reimport():
    path = "/tmp/test_ny_import.xlsx"
    make_test_xlsx(path)
    filings1 = NewYorkUCCSource().bulk_ingest_from_xlsx(path)
    filings2 = NewYorkUCCSource().bulk_ingest_from_xlsx(path)
    assert filings1[0].dedup_hash() == filings2[0].dedup_hash(), \
        "re-running the import should produce the same hash, so a second run is a no-op against the DB"


if __name__ == "__main__":
    tests = [test_low_confidence_dropped_and_duplicates_removed, test_dedup_hash_stable_across_reimport]
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
