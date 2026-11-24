"""CA hospice licence diff (scraper/licence_lists.py, 2026-11-24)."""
import csv
import io

from pipeline.deal_labels import facility_type
from scraper.licence_lists import (detect_ca_hospice_changes, parse_ca_hospice_rows, ca_hospice_to_deal,
                                   ca_hospice_record_url)

COLS = ["FAC_TYPE_CODE", "CCN", "LICENSE_STATUS_DESCRIPTION", "BUSINESS_NAME", "FACNAME", "CITY", "ISFACMAIN"]


def _csv(rows):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=COLS)
    w.writeheader()
    for r in rows:
        w.writerow(dict(zip(COLS, r)))
    return out.getvalue()


def test_parse_keeps_active_hospices_with_ccn_one_row_each():
    text = _csv([
        ("HOSPICE", "551743", "ACTIVE", "KAISER FOUNDATION HOSPITALS", "KFH RIVERSIDE HOSPICE", "RIVERSIDE", "Y"),
        ("HOSPICE", "551743", "ACTIVE", "KAISER FOUNDATION HOSPITALS", "KFH RIVERSIDE BRANCH", "CORONA", "N"),
        ("HOFA", "051234", "ACTIVE", "HOSPICE HOME INC", "HOSPICE HOME", "FRESNO", "Y"),
        ("HOSPICE", "", "ACTIVE", "NO CCN HOSPICE LLC", "NO CCN HOSPICE", "LOS ANGELES", "Y"),
        ("HOSPICE", "551999", "CLOSED", "OLD HOSPICE LLC", "OLD HOSPICE", "LOS ANGELES", "Y"),
        ("SNF", "055077", "ACTIVE", "SOME SNF LLC", "SOME SNF", "LONG BEACH", "Y"),
    ])
    rows = {r["ccn"]: r for r in parse_ca_hospice_rows(text)}
    assert set(rows) == {"551743", "051234"}
    assert rows["551743"]["facility"] == "KFH RIVERSIDE HOSPICE"  # parent row, not the branch


def test_first_read_is_baseline_only():
    rows = [{"ccn": "551743", "licensee": "A HOSPICE LLC", "licence_date": None, "facility": "A", "city": "X"}]
    changes, snapshot = detect_ca_hospice_changes(rows, {}, lambda url: False)
    assert changes == [] and snapshot == rows


def test_changed_licensee_becomes_a_hospice_card():
    rows = [{"ccn": "551743", "licensee": "NEW OWNER HOSPICE LLC", "licence_date": None,
             "facility": "SUNRISE HOSPICE", "city": "VAN NUYS"},
            {"ccn": "051234", "licensee": "Same Hospice, Inc.", "licence_date": None,
             "facility": "SAME", "city": "FRESNO"}]
    previous = {"551743": "OLD OWNER HOSPICE INC", "051234": "SAME HOSPICE INC"}
    changes, snapshot = detect_ca_hospice_changes(rows, previous, lambda url: False)
    assert [c["ccn"] for c in changes] == ["551743"]
    assert [s["ccn"] for s in snapshot] == ["051234"]  # changed row waits until stored
    deal = ca_hospice_to_deal(changes[0])
    assert deal["seller_entity"] == "OLD OWNER HOSPICE INC"
    assert deal["ccns"] == ["551743"] and deal["states"] == ["CA"]
    assert facility_type({**deal, "source_type": "con"}) == "hospice"
    assert "#hospice-licensee-551743-" in deal["url"]


def test_known_record_is_not_reported_again():
    row = {"ccn": "551743", "licensee": "NEW OWNER HOSPICE LLC", "licence_date": None, "facility": "S", "city": "V"}
    known = {ca_hospice_record_url(row)}
    changes, _ = detect_ca_hospice_changes([row], {"551743": "OLD OWNER INC"}, lambda url: url in known)
    assert changes == []
