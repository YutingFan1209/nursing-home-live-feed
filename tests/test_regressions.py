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


def test_news_loans_hash_on_lender_and_amount():
    # Dwight's $46.5M Ohio loan collided with an unrelated Dayton sale (2026-10-26)
    sale = {"acquiring_entity": None, "states": ["OH"], "acquisition_date": None, "facility_count": 1, "deal_value_m": None}
    loan = {**sale, "financing_amount_m": 46.5, "lender": "Dwight Capital"}
    assert make_dedup_hash(loan) != make_dedup_hash(sale)
    assert make_dedup_hash(loan) != make_dedup_hash({**loan, "financing_amount_m": 54.5})
    assert make_dedup_hash(loan) == make_dedup_hash({**loan, "lender": "Dwight Capital LLC"})
    # a deal with a buyer keeps its old key
    bought = {**sale, "acquiring_entity": "Ensign Group"}
    assert make_dedup_hash(bought) == make_dedup_hash({**bought, "financing_amount_m": 20, "lender": "CIBC"})


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
    monkeypatch.setattr(chow, "_load_home_states", lambda conn: {})
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
    status_code = 200  # rss._http_get checks for a 403 before falling back to curl_cffi
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


def test_news_refinancing_split_from_summary():
    assert _news_deal_type({"financing_amount_m": 53, "lender": "CIBC",
        "summary": "CIBC closed a $53M cash-out refinancing for a 9-facility portfolio"}) == "refinancing"
    assert _news_deal_type({"financing_amount_m": 32.45,
        "summary": "Ziegler closed a placement to refinance existing 2019 bonds"}) == "refinancing"
    assert _news_deal_type({"financing_amount_m": 11, "lender": "CIBC",
        "summary": "CIBC closed an $11M commercial mortgage for a skilled nursing facility"}) == "financing"
    # no amount: a portfolio loan split across deals, refinancing only if its first sentence says so
    assert _news_deal_type({"lender": "MONTICELLOAM",
        "summary": "MONTICELLOAM provided loans to refinance three Virginia SNFs. Part of $195.7M."}) == "refinancing"
    assert _news_deal_type({"lender": "MONTICELLOAM",
        "summary": "MONTICELLOAM financed the acquisition of four Indiana SNFs. It also includes a Virginia refinancing."}) == "ownership_change"


# ── Data-quality guards (2026-10-12) ──────────────────────────
# Non-US deals with no state, PACE programs, and street-number buyer names
# showing up in the feed.

from pipeline.al_mc_scope import is_out_of_scope
from pipeline.normalizer import normalize_deal, normalize_entity_name


def test_street_number_names_keep_legal_suffix():
    assert normalize_entity_name("101 W State Street Holdings LLC") == "101 W State Street Holdings LLC"
    assert normalize_entity_name("620 HEATHWOOD DRIVE OPCO LLC") == "620 Heathwood Drive Opco LLC"
    assert normalize_entity_name("CASCADIA HEALTHCARE INC") == "Cascadia Healthcare"


def test_stateless_foreign_deal_is_flagged_non_us():
    uk = normalize_deal({"acquiring_entity": "CareTrust", "states": [],
                         "rationale": "CareTrust acquired three UK care homes for $34 million"})
    assert uk.get("_non_us_only")
    us = normalize_deal({"acquiring_entity": "Sienna", "states": ["OH"],
                         "rationale": "Canadian operator Sienna acquired an Ohio SNF"})
    assert not us.get("_non_us_only")
    unknown = normalize_deal({"acquiring_entity": "Welltower", "states": [],
                              "rationale": "Welltower acquired 41 skilled nursing facilities"})
    assert not unknown.get("_non_us_only")
    new_england = normalize_deal({"acquiring_entity": "Athena", "states": [],
                                  "rationale": "Athena acquired four New England nursing homes"})
    assert not new_england.get("_non_us_only")
    lnt = normalize_deal({"acquiring_entity": "CareTrust", "states": [],
                          "seller_entity": "LNT Care Developments Holdings Limited"})
    assert lnt.get("_non_us_only")
    us_lp = normalize_deal({"acquiring_entity": "KTLA Properties Limited Partnership", "states": []})
    assert not us_lp.get("_non_us_only")


def test_pace_program_is_out_of_scope():
    assert is_out_of_scope({"acquiring_entity": "One Senior Care", "seller_entity": "PACE North",
                            "rationale": "expanding its all-inclusive care program"})
    assert not is_out_of_scope({"acquiring_entity": "Pace Healthcare Group", "facility_names": ["Oak Manor"],
                                "rationale": "acquired a 120-bed SNF"})


# ── Run health (2026-09-23) ───────────────────────────────────
# Failures used to be logged and forgotten; runs that lost whole sources
# still exited 0.

from pipeline.run_health import EXIT_UNHEALTHY, RunHealth


def test_run_health_clean_run_exits_zero():
    h = RunHealth()
    h.attempted("Article text fetch", 26)
    h.failed("Article text fetch", "https://example.test/paywalled")  # 1 of 26: tolerated
    assert h.problems() == []
    assert h.report() == 0


def test_run_health_source_failure_fails_run():
    h = RunHealth()
    h.source_failed("RSS feed https://example.test/feed", "CERTIFICATE_VERIFY_FAILED")
    assert h.report() == EXIT_UNHEALTHY


def test_run_health_high_failure_rate_fails_run():
    h = RunHealth()
    h.attempted("Claude extraction", 8)
    for i in range(2):
        h.failed("Claude extraction", f"article {i}: credit balance too low")
    assert len(h.problems()) == 1


def test_run_health_failure_without_known_attempts_fails_run():
    h = RunHealth()
    h.failed("UCC filing save", "OH/123: connection reset")
    assert h.problems()


def test_rss_fetch_failure_reports_source_failure(monkeypatch):
    from pipeline.run_health import health
    health.reset()
    def boom(*a, **kw):
        raise requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")
    monkeypatch.setattr(rss.requests, "get", boom)
    rss.fetch_feed("https://example.test/feed")
    assert any("example.test/feed" in p for p in health.problems())
    health.reset()


# ── CA OHCA notice parsing (2026-10-06) ───────────────────────

def test_ca_ohca_parses_rows_and_screens_snf_text():
    from scraper.con_ca import _SNF_RE, _parse_notices
    page = """<table><tr><th>MCN Parties</th><th>Type</th><th>Date</th><th>CMIR?</th></tr>
      <tr><td><a href="https://hcai.ca.gov/document/a/">Madera SNF Operations LLC</a></td>
          <td>Madera SNF Operations LLC is the licensee of Golden Madera Care Center, a skilled nursing facility.</td>
          <td>May 1, 2025</td><td>CMIR Waived</td></tr>
      <tr><td>No link</td><td>x</td><td>May 2, 2025</td><td></td></tr></table>"""
    notices = _parse_notices(page)
    assert len(notices) == 1
    assert notices[0]["submitted"] == date(2025, 5, 1)
    assert notices[0]["links"] == ["https://hcai.ca.gov/document/a/"]
    assert _SNF_RE.search(notices[0]["description"])
    assert not _SNF_RE.search("Abbott Laboratories is acquiring Exact Sciences Corporation")


def test_con_text_deals_from_different_filings_dont_collide():
    from main import _key_con_deals
    deal = {"acquiring_entity": "BQ Operations Holdings", "states": ["CA"], "acquisition_date": "2024-10-01"}
    a = _key_con_deals({"source_type": "con", "url": "https://x/alexandria"}, [dict(deal)])
    b = _key_con_deals({"source_type": "con", "url": "https://x/sharon"}, [dict(deal)])
    assert make_dedup_hash(a[0]) != make_dedup_hash(b[0])
    news = _key_con_deals({"source_type": "rss", "url": "https://x/n"}, [dict(deal)])
    assert "_con_id" not in news[0]


# ── UCC new-signal criteria (2026-10-06 audit) ────────────────

def test_unknown_lender_needs_nursing_home_debtor(monkeypatch):
    import main
    from types import SimpleNamespace
    from ucc.lender_classifier import classify_secured_party
    monkeypatch.setitem(main._CMS_NAMES_BY_STATE, "PA", {"EMBASSY PARK AVENUE", "ROBERT HURLBUT"})

    def ok(debtor, lender):
        f = SimpleNamespace(state="PA", debtor_name=debtor, secured_party_name=lender, filing_number="")
        return main._ucc_debtor_qualifies(f, classify_secured_party(lender, state="PA"), None)

    assert not ok("CARPINTERIA VALLEY ROOFING, INC.", "MONTECITO BANK & TRUST")
    assert ok("EMBASSY PARK AVENUE, LLC", "SIENA LENDING GROUP LLC")        # known name
    assert ok("ROBERT W HURLBUT", "HSBC BANK USA")                           # person, middle initial
    assert ok("SUNRISE NURSING AND REHABILITATION LLC", "SOME BANK, N.A.")   # nursing words
    assert ok("LEGACY NORTH ROYALTON OPERATING COMPANY", "SOME BANK, N.A.")  # opco
    assert ok("ANYTHING LLC", "CAPITAL FINANCE, LLC, AS AGENT")              # lender-search lender


def test_known_healthcare_lenders_are_recognized():
    for name in ["OHI ASSET (OH) SPRINGFIELD, LLC", "CAPITAL FINANCE, LLC, AS ADMINISTRATIVE AGENT",
                 "DMT SPE I LLC", "FORBRIGHT BANK, AS AGENT", "CIBC BANK USA",
                 "WHITE OAK HEALTHCARE FINANCE, LLC", "LANCASTER POLLARD MORTGAGE COMPANY"]:
        assert classify_secured_party(name).category == LenderCategory.REAL_ESTATE, name
    # General banks stay general
    assert classify_secured_party("TRUIST BANK, AS ADMINISTRATIVE AGENT").category == LenderCategory.BANK_GENERAL


# ── Same sale stored twice (2026-10-07) ───────────────────────

def test_same_sale_rules():
    from pipeline.same_sale import _filed_after_closing, _names_agree
    assert _names_agree("The Ensign Group", "Ensign Group, Inc.")
    assert _names_agree(None, "WEST BLOOMFIELD OPCO, LLC")          # blank = no info
    assert not _names_agree("Standard Bearer Healthcare", "The Ensign Group")
    con = {"source_type": "con", "extraction_model": "claude", "acquisition_date": date(2025, 11, 13)}
    chow = {"source_type": "chow", "extraction_model": "chow_direct", "acquisition_date": date(2025, 7, 1)}
    assert _filed_after_closing(con, chow)                           # filing 4 months after closing
    assert not _filed_after_closing({**con, "acquisition_date": date(2025, 6, 1)}, chow)


def test_same_sale_licensee_and_parent_merge_automatically():
    # CHOW names the licensee, the CMS ownership file its parent (2026-10-20)
    from pipeline.same_sale import _licensee_and_parent
    chow = {"extraction_model": "chow_direct", "acquisition_date": date(2025, 11, 1), "direct_ccns": {"115273"}}
    parent = {"extraction_model": "cms_owner_change", "acquisition_date": date(2025, 11, 1), "direct_ccns": {"115273"}}
    assert _licensee_and_parent(chow, parent)
    assert _licensee_and_parent(parent, {**chow, "acquisition_date": date(2025, 10, 31)})
    assert not _licensee_and_parent(chow, {**parent, "acquisition_date": date(2026, 4, 1)})   # months apart
    assert not _licensee_and_parent(chow, {**parent, "direct_ccns": set()})                    # name-guessed link only
    assert not _licensee_and_parent(chow, {**chow, "extraction_model": "con_direct"})          # CON stays in review


# ── Philadelphia notices (2026-10-08) ─────────────────────────

def test_phl_notices_dedupe_links_and_attach_exhibits_by_name():
    from scraper.con_phl import _parse_notices, _HOSPITAL
    page = """<main>
      <a href="/media/20260929151037/chapel-manor.pdf">Chapel Manor PDF</a>
      <a href="/media/20260929151037/chapel-manor.pdf"></a>
      <a href="/media/20260929151035/chapel-manor-exhibit-a.pdf">Chapel Manor, Exhibit A PDF</a>
      <a href="/media/20240516120907/Tulip-update.pdf">Tulip Special Care, LLC (5/15/24 update) PDF</a>
      <a href="/media/20240305091530/Tulip.pdf">Tulip Special Care, LLC PDF</a>
      <a href="/media/20221115092057/Hospital.pdf">Chestnut Hill Hospital PDF</a>
      <a href="/media/20220309104726/Restore.pdf">Restore Health at University City PDF</a></main>"""
    ns = {n["title"]: n for n in _parse_notices(page)}
    assert set(ns) == {"Chapel Manor", "Tulip Special Care, LLC", "Chestnut Hill Hospital", "Restore Health at University City"}
    assert ns["Chapel Manor"]["date"] == date(2026, 9, 29)
    assert ns["Chapel Manor"]["exhibits"] == ["Chapel Manor, Exhibit A"]
    assert ns["Tulip Special Care, LLC"]["exhibits"] == ["Tulip Special Care, LLC (5/15/24 update)"]
    assert _HOSPITAL.search("Chestnut Hill Hospital") and not _HOSPITAL.search("Restore Health at University City")


# ── UCC acquisition financing vs refinancing (2026-10-09) ─────

def test_ucc_category_and_repeats():
    from pipeline.ucc_category import categorize, collapse_repeats

    def ucc(debtor, lender, filed, ccns=("395001",), state="PA"):
        return {"source_type": "ucc", "deal_type": "financing", "ucc_state": state, "ucc_debtor": debtor,
                "lender": lender, "acquisition_date": filed, "ccns": list(ccns), "created_at": filed}
    owner_starts = {"395001": {date(2023, 5, 1)}}
    deals = [
        ucc("WEATHERWOOD REHABILITATION AND HEALTHCARE LLC", "GMCC II, LLC", "2023-05-08"),          # owner changed a week earlier
        ucc("WEATHERWOOD REHABILITATION AND HEALTHCARE LLC", "GREYSTONE SERVICING COMPANY LLC", "2026-03-27"),
        ucc("SPRUCE MANOR NURSING LLC", "CIBC BANK USA", "2018-02-08", ccns=()),
        ucc("SPRUCE MANOR NURSING LLC", "CIBC BANK USA", "2022-05-02", ccns=()),                     # same loan, filed again
        ucc("JEFFREY RUBIN", "YARDVILLE NATIONAL BANK", "2004-11-16", state="NY"),                     # a person
        ucc("JEFFREY RUBIN", "YARDVILLE NATIONAL BANK", "2006-04-19", state="NY"),
        ucc("EASTERN PINES CARE LLC", "Not available — NJ", "2022-01-05", ccns=(), state="NJ"),
        ucc("EASTERN PINES CARE LLC", "Not available — NJ", "2025-09-02", ccns=(), state="NJ"),
    ]
    categorize(deals, owner_starts)
    assert [d["deal_type"] for d in deals[:2]] == ["acquisition_financing", "refinancing"]
    assert deals[4]["deal_type"] == "financing"                       # persons stay uncategorised
    assert deals[7]["deal_type"] == "financing"                       # NJ: no lender, no history test
    out = collapse_repeats(deals)
    spruce = [d for d in out if d["ucc_debtor"].startswith("SPRUCE")]
    assert len(spruce) == 1 and spruce[0]["acquisition_date"] == "2022-05-02"
    assert spruce[0]["ucc_also_filed"] == ["2018-02-08"]
    assert len(out) == len(deals) - 1                                 # persons and NJ aren't collapsed


# ── Illinois HFSRB change-of-ownership projects (2026-10-12) ──────────────

from scraper.con_il import is_chow_text, is_ltc_title, list_projects, _project_year


def test_il_project_list_and_filters():
    page = ('<a href="/announcements/25-031-oak-hill-senior-living.html">25-031 Oak Hill Senior Living and '
            'Rehabilitation Center, Waterloo</a><a href="/announcements/26-002-advocate-lutheran-general-hospital.html">'
            '26-002 Advocate Lutheran General Hospital</a><a href="/announcements/E-031-24-premier.html">E-031-24 Premier '
            'Cardiac Surgery Center</a>')
    ps = list_projects(page)
    assert [p["code"] for p in ps] == ["25-031", "26-002", "E-031-24"]
    assert [is_ltc_title(p["title"]) for p in ps] == [True, False, False]
    assert _project_year("25-031") == 2025 and _project_year("E-031-24") == 2024


def test_il_sale_is_told_by_pre_and_post_transaction_owners():
    assert is_chow_text("Exact Legal Name of Site Owner: County of Stephenson (Pre-transaction) "
                        "Freeport Realty LLC (Post-transaction)")
    assert not is_chow_text("Exact Legal Name of Site Owner: County of DuPage")


# ── Hawaii SHPDA CON applications (2026-10-12) ────────────────────────────

from scraper.con_hi import is_nursing_acquisition, parse_rows


def test_hi_nursing_acquisitions_only():
    page = ("<table><tr><td>24-04A</td><td>Hawaii Health Systems Corporation-Oahu Region: Acquisition of Skilled "
            "Nursing Facility/Intermediate Care Facility services at 91-1204 Kealanani Avenue, Kapolei, Hawaii</td>"
            "<td><a href='/shpda/files/shd2404a.pdf'>shd2404a</a></td></tr>"
            "<tr><td>24-10A</td><td>PSH KM LLC: Addition of 140 SNF/ICF beds at 91-1360 Karayan Street</td><td></td></tr>"
            "<tr><td>24-16A</td><td>Kimura Home Health LLC: Acquisition of home health agency services</td><td></td></tr></table>")
    rows = parse_rows(page)
    assert [r["number"] for r in rows] == ["24-04A", "24-10A", "24-16A"]
    assert [is_nursing_acquisition(r) for r in rows] == [True, False, False]
    assert rows[0]["applicant"] == "Hawaii Health Systems Corporation-Oahu Region" and rows[0]["decision_url"]


def test_stuck_tab_close_doesnt_hang_the_ucc_run(monkeypatch):
    """2026-10-13: NY finished all its searches, then hung for 10 minutes
    on two tabs' page.close() (a Turnstile frame was still on them).
    A stuck tab is closed over CDP, then left behind with its results kept;
    a worker that raises keeps what it already found."""
    import threading
    import time
    import ucc.chrome_cdp as cdp
    closed = []
    monkeypatch.setattr(cdp, "close_target", lambda tid, port=9222: closed.append(tid) or True)
    never = threading.Event()

    def work(chunk, out, tab):
        tab["id"] = f"T-{chunk}"
        out.extend([chunk, chunk])
        tab["searched"] = time.time()
        if chunk == "stuck":
            never.wait()
        if chunk == "fails":
            raise RuntimeError("navigation interrupted")

    results = cdp.run_tab_workers(work, ["ok", "stuck", "fails"], "TEST", close_grace=0.5)
    assert sorted(results) == ["fails", "fails", "ok", "ok", "stuck", "stuck"]
    assert closed == ["T-stuck"]


def test_match_rows_for_one_owner_in_several_roles_are_stored_once():
    """CMS lists a person once per role; cms_matches has no role column, so
    those came out as identical rows (17,348 of them, 2026-10-13)."""
    import main
    stored = []

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, *a): pass

    class Conn:
        def cursor(self): return Cur()

    import psycopg2.extras
    orig = psycopg2.extras.execute_values
    psycopg2.extras.execute_values = lambda cur, sql, rows: stored.extend(rows)
    try:
        m = {"ccn": "055919", "provider_name": "X", "owner_name": "JOHNSON, MARC", "owner_type": "Individual",
             "provider_state": "CA", "ownership_start_date": None, "match_score": 90,
             "match_method": "facility_name_fuzzy", "matched_on_field": "facility_names"}
        main._store_cms_matches("d1", [m, dict(m), {**m, "owner_name": "LANE, JOANNE"}], Conn())
    finally:
        psycopg2.extras.execute_values = orig
    assert len(stored) == 2


def test_a_new_source_records_its_first_check():
    """Illinois CON ran once and showed 'Never recorded': the first insert
    of a source row didn't set last_fetched_at (2026-10-13)."""
    import inspect
    import main
    import scraper.chow
    import scraper.cms_owner_changes
    for fn in (main._ensure_source, scraper.chow.get_chow_source_id, scraper.cms_owner_changes.ensure_source):
        sql = inspect.getsource(fn)
        assert "last_fetched_at)" in sql and "NOW())" in sql, fn.__name__


def test_ucc_source_links_2026_10_15():
    """Every UCC state gets a real link: FL's per-filing page is built from
    the number, OR has a portal (its deals shipped raw ucc:// keys), and an
    NY spreadsheet deal (made-up NYX- key) links to the real filing only
    when it's unambiguous."""
    from scripts.export_deals import _ucc_display_fields, _match_ny_spreadsheet
    url, _ = _ucc_display_fields("ucc://FL/20260303070X", "", None)
    assert url.startswith("https://floridaucc.com/search/details?") and "uccNumber=20260303070X" in url
    url, title = _ucc_display_fields("ucc://OR/94372864", "", None)
    assert url.startswith("https://secure.sos.state.or.us/") and "#94372864" in title
    url, title = _ucc_display_fields("ucc://NY/NYX-723ed67e0fa1a2a3", "", None)
    assert url.startswith("https://ucc-efiling.dos.ny.gov/") and "NYX" not in title
    index = {("JANE ROE", "2017-02-24"): [("BANK A", "111", "u1")],
             ("JOHN DOE", "2020-01-01"): [("BANK A", "222", "u2"), ("BANK B", "333", "u3")]}
    assert _match_ny_spreadsheet(index, "Jane Roe", "2017-02-24", "anyone") == ("111", "u1")
    assert _match_ny_spreadsheet(index, "John Doe", "2020-01-01", "Bank B") == ("333", "u3")
    assert _match_ny_spreadsheet(index, "John Doe", "2020-01-01", "Bank C") is None
    assert _match_ny_spreadsheet(index, "Nobody", "2020-01-01", "Bank A") is None
    # the portal lists a middle initial the spreadsheet left out
    index[(("RONALD", "STERN"), "2013-06-03")] = [("", "444", "u4")]
    assert _match_ny_spreadsheet(index, "Ronald Stern", "2013-06-03", "Bank D") == ("444", "u4")


# ── Fuzzy dedup across facilities (fixed 2026-10-19) ──────────
# Same-chain opcos named after their towns fuzzy-matched on acquirer, so
# CHOW takeovers of different homes on the same day merged into one deal.

def test_fuzzy_dedup_keeps_same_day_takeovers_of_different_facilities():
    from pipeline.dedup import _ccns_overlap_ok
    assert not _ccns_overlap_ok({"285108"}, {"285124"})
    assert _ccns_overlap_ok({"015379"}, {"015379", "015380"})
    assert _ccns_overlap_ok({"285108"}, set())
    assert _ccns_overlap_ok(set(), {"285124"})


def test_chow_same_month_takeovers_get_distinct_hashes():
    base = {"extraction_model": "chow_direct", "acquiring_entity": "FRIO HOSPITAL DISTRICT",
            "states": ["TX"], "facility_count": 1}
    a = make_dedup_hash({**base, "ccn": "675233", "acquisition_date": "2024-12-15"})
    b = make_dedup_hash({**base, "ccn": "675700", "acquisition_date": "2024-12-25"})
    c = make_dedup_hash({**base, "ccn": "675469", "acquisition_date": "2024-12-25"})
    assert len({a, b, c}) == 3
    assert a == make_dedup_hash({**base, "ccn": "675233", "acquisition_date": "2024-12-15"})


# ── NY individual searches (fixed 2026-10-20) ─────────────────
# Reading every result page pulled in everyone sharing an owner's name:
# 192 old car, farm and solar loans in one run, next to real guarantor
# filings (CIBC as agent against an Elderwood owner).

def test_ny_individual_filter_keeps_recent_commercial_drops_old_and_consumer():
    from ucc.base import UCCFiling
    from ucc.ny_playwright import keep_individual_filing
    today = date(2026, 10, 20)
    f = lambda lender, filed: UCCFiling(state="NY", debtor_name="GARRICK MOSSLEY", secured_party_name=lender,
                                        filing_number="1", filing_date=filed)
    assert keep_individual_filing(f("CIBC BANK USA, AS ADMINISTRATIVE AGENT", date(2025, 12, 31)), today)
    assert keep_individual_filing(f("", None), today)
    assert not keep_individual_filing(f("CIBC BANK USA", date(2023, 3, 31)), today)
    for lender in ("SHEFFIELD FINANCIAL, A DIVISION OF TRUIST BANK", "GOODLEAP,LLC", "LOANPAL, LLC",
                   "CAPITAL RESORTS GROUP LLC", "AMERICU CREDOIT UNION", "DEERE & COMPANY"):
        assert not keep_individual_filing(f(lender, date(2026, 5, 1)), today), lender


# ── AR / NM lien portal lender search (added 2026-10-20) ──────

def test_ar_rows_split_debtor_and_lender_from_place():
    from ucc.lien_portal import rows_to_filings
    rows = {"3104524": {"DEBTOR": "BRYANT OPERATING SNF LLC - ROSENBERG, TX", "RECORD_NUM": "98260519289",
                        "UCC_RECORD_ID": 1900467, "RECORD_TYPE": "Lien Financing Statement",
                        "SEC_PARTY": "CAPITAL FUNDING, LLC, AS AGENT - BALTIMORE, MD", "STATUS": "Active",
                        "FILING_DATE": "8/11/2026 11:45 AM", "LAPSE_DATE": "8/11/2031 11:45 AM"}}
    f, = rows_to_filings(rows, "CAPITAL FUNDING")
    assert (f.debtor_name, f.secured_party_name) == ("BRYANT OPERATING SNF LLC", "CAPITAL FUNDING, LLC, AS AGENT")
    assert f.raw["address"] == "ROSENBERG, TX" and f.filing_number == "98260519289"
    assert f.filing_date == date(2026, 8, 11) and f.status == "active"


def test_lien_portal_splits_full_windows_and_checks_loose_matches():
    from ucc import lien_portal
    row = lambda n, lender: {"DEBTOR": f"HOME {n} SNF LLC - TOWN, NM", "RECORD_NUM": str(n), "SEC_PARTY": f"{lender} - X, MD",
                             "STATUS": "Active", "FILING_DATE": "5/1/2026 10:00 AM", "LAPSE_DATE": "5/1/2031 10:00 AM"}
    calls = []
    def fake(state, name, start, end, session):
        calls.append((start, end))
        if len(calls) == 1:                                   # the full 2-year window hits the cap
            return {str(i): row(i, "CAPITAL FUNDING, LLC") for i in range(100)}
        return {f"{start}-{i}": row(f"{start}{i}", "CAPITAL FUNDING, LLC" if i == 0 else "EXPRESS CAPITAL FUNDING, INC.")
                for i in range(3)}
    lien_portal._search_window, real = fake, lien_portal._search_window
    try:
        got = lien_portal.search_secured_party("NM", "Capital Funding", session=object(),
                                               today=date(2026, 10, 20), pause=lambda s: None)
    finally:
        lien_portal._search_window = real
    assert len(calls) == 3                                    # split once into two halves
    assert len(got) == 2 and all(f.secured_party_name == "CAPITAL FUNDING, LLC" for f in got)
    assert lien_portal.lender_matches("SECRETARY OF HOUSING AND URBAN DEVELOPMENT",
                                      "SECRETARY OF U.S. DEPARTMENT OF HOUSING & URBAN DEVELOPMENT")
    assert not lien_portal.lender_matches("CAPITAL FUNDING", "CLOUD CAPITAL FUNDING LLC")


# ── RI UCC lender search (2026-10-21) ──────────────────────────
from ucc import ri


def test_ri_results_and_history_parsing():
    results = """<table id="MainContent_grdSearchResults">
      <tr><th>Name</th><th>Name Type</th><th>City</th><th>State</th><th>Filing Type</th><th>Filing Number</th><th>Original Filing Number</th><th>Filing Date</th></tr>
      <tr><td><a href="UCCFilingHistory.aspx?SearchLapsed=False&UCC1=444600">CAPITAL FUNDING, LLC</a></td><td>SECURED PARTY</td><td>BALTIMORE</td><td>MD</td><td>UCC-1</td><td>202634846030</td><td>202634846030</td><td>10/13/2026</td></tr>
      <tr><td><a href="UCCFilingHistory.aspx?SearchLapsed=False&UCC1=387029">CAPITAL FUNDING ASAP LLC</a></td><td>SECURED PARTY</td><td>LAKEWOOD</td><td>NJ</td><td>UCC-1</td><td>202329088990</td><td>202329088990</td><td>05/19/2023</td></tr>
    </table>"""
    rows = ri.parse_results(results)
    assert [(r["ucc1"], r["filing_number"], r["date"]) for r in rows] == [
        ("444600", "202634846030", date(2026, 10, 13)), ("387029", "202329088990", date(2023, 5, 19))]
    history = """<table id="MainContent_tblFilingHistory">
      <tr><td align="center" colspan="4">UCC-1 Standard</td></tr>
      <tr><td colspan="4">Debtor(s)</td></tr>
      <tr><td colspan="2">ADVINIACARE PAWTUCKET PLEASANT REHAB CENTER, LLC<BR>544 PLEASANT STREET<BR>PAWTUCKET RI 02860</td><td colspan="2">&nbsp;</td></tr>
      <tr><td colspan="4">Secured Parties</td></tr>
      <tr><td colspan="2">CAPITAL FINANCE, LLC<BR>BALTIMORE MD 21230</td><td colspan="2">&nbsp;</td></tr>
      <tr><td align="center" colspan="4">UCC-3 TERMINATION</td></tr>
      <tr><td colspan="4">Debtor(s)</td></tr>
      <tr><td colspan="2">SOMEONE ELSE LLC<BR>X</td></tr>
    </table>"""
    got = ri.parse_history(history)
    assert got["debtors"] == [{"name": "ADVINIACARE PAWTUCKET PLEASANT REHAB CENTER, LLC",
                               "address": "544 PLEASANT STREET, PAWTUCKET RI 02860"}]
    assert got["terminated"]


def test_ri_results_url_sends_every_form_key():
    url = ri.results_url("CAPITAL FUNDING")
    for key in ("txtLast=", "chkDebtor=", "chkSecuredParty=S", "txtName=CAPITAL+FUNDING", "lstDisplay=10000"):
        assert key in url


# ── ID UCC debtor search (2026-10-21) ──────────────────────────
from ucc import id as id_ucc


def test_id_rows_keep_ucc_only_and_split_parties():
    rows = {
        "1": {"TITLE": ["SNF BOISE OPERATING COMPANY, LLC - WOODS CROSS, UT"], "SEC_PARTY": ["ECAPITAL HEALTHCARE CORP. - AVENTURA, FL"],
              "RECORD_NUM": "20261111111", "FILING_DATE": "04/30/2026", "LAPSE_DATE": "04/30/2031", "RECORD_TYPE": "UCC", "STATUS": "Active", "ID": 1},
        "2": {"TITLE": ["SNF BOISE OPERATING CO LLC - BOISE, ID"], "SEC_PARTY": ["IDAHO DEPARTMENT OF LABOR - BOISE, ID"],
              "RECORD_NUM": "20212222222", "FILING_DATE": "03/19/2021", "RECORD_TYPE": "State Lien", "STATUS": "Released", "ID": 2},
    }
    got = id_ucc.rows_to_filings(rows, "BOISE SNF")
    assert len(got) == 1
    f = got[0]
    assert (f.debtor_name, f.secured_party_name, f.filing_date) == (
        "SNF BOISE OPERATING COMPANY, LLC", "ECAPITAL HEALTHCARE CORP.", date(2026, 4, 30))
    assert f.raw["address"] == "WOODS CROSS, UT" and f.status == "active"
    assert id_ucc.NOT_A_HOME_OWNER.search("BOUNDARY COUNTY") and not id_ucc.NOT_A_HOME_OWNER.search("BOISE SNF OPERATIONS LLC")


# ── UCC facility linking: portfolio companions (2026-10-21, CO) ──
# Property companies, holdcos and out-of-state LLCs had no CMS link, so the
# export couldn't tell refinancing from acquisition financing.
from scripts import enrich_ucc_facility_names as enrich


def _co_maps():
    by_state, names = {}, {}
    for ccn, name, city in [("065399", "RIO GRANDE REHABILITATION AND HEALTHCARE CENTER", "DEL NORTE"),
                            ("065306", "VALLEY REHABILITATION AND HEALTHCARE CENTER, THE", "SPRINGFIELD"),
                            ("065347", "VALLEY VIEW CARE CENTER", "PUEBLO"),
                            ("065291", "ROCK CREEK REHABILITATION AND HEALTHCARE CENTER", "MONTE VISTA"),
                            ("065391", "COLORADO VETERANS COMMUNITY LIVING CTR AT HOMELAKE", "MONTE VISTA")]:
        names[ccn] = name
        by_state.setdefault("CO", []).append((ccn, name, enrich._entity_key(name), enrich._core_key(name)))
        by_state.setdefault(("city", "CO", city), []).append((ccn, name))
    return by_state, names


def test_portfolio_companions_link_propcos_towns_and_parents():
    by_state, names = _co_maps()
    pm = lambda debtor, comp: enrich._portfolio_match(debtor, "CO", comp, by_state, names)
    # "VALLEY" alone also starts VALLEY VIEW: the companions decide
    assert pm("THE VALLEY REALTY LLC", {"065306", "065399"}) == (
        "ucc_debtor_portfolio_name", [("065306", "VALLEY REHABILITATION AND HEALTHCARE CENTER, THE")])
    assert pm("RIO GRANDE PROPCO LLC", set()) is None
    # two homes in Monte Vista; only one is in the portfolio
    assert pm("Monte Vista 2277 Realty LLC", {"065291", "065399"})[0] == "ucc_debtor_portfolio_town"
    assert pm("CENTENNIAL 7 MASTER TENANT LLC", {"065306", "065399"})[1] == [
        ("065306", names["065306"]), ("065399", names["065399"])]
    # one companion isn't a portfolio
    assert pm("113 SOUTH ROUTE 73 PROPCO HOLDCO LLC", {"065399"}) is None
    assert enrich.NOT_PORTFOLIO_LENDER.match("SECRETARY OF HOUSING AND URBAN DEVELOPMENT")
    assert not enrich.NOT_PORTFOLIO_LENDER.match("CAPITAL FUNDING, LLC; SECRETARY OF HOUSING & URBAN DEVELOPMENT")


def test_other_state_link_needs_a_registered_company_and_a_unique_name():
    national = {enrich._entity_key("THE CENTER AT PARMER"): {("676487", "THE CENTER AT PARMER", "TX")},
                enrich._entity_key("HERITAGE PARK NURSING CENTER"): {("555514", "Heritage Park Nursing Center", "CA")}}
    assert enrich._other_state_exact("THE CENTER AT PARMER, LLC", "CO", national) == ("676487", "THE CENTER AT PARMER", "TX")
    assert enrich._other_state_exact("HERITAGE PARK NURSING CENTER", "FL", national) is None


# ── CMS owner data lags months: no CMS-based "refinancing" after it (2026-10-21) ──

def test_refinancing_needs_owner_data_that_covers_the_filing():
    from pipeline.ucc_category import categorize, complete_through
    # 40 homes a month change hands through 2025-12, then CMS has only 2 a month
    owner_starts = {f"c{y}{m}{i}": {date(y, m, 1)} for y, m in [(2025, k) for k in range(1, 13)] for i in range(40)}
    owner_starts.update({f"late{m}{i}": {date(2026, m, 1)} for m in range(1, 8) for i in range(2)})
    owner_starts["065355"] = {date(1995, 10, 11)}
    assert complete_through(owner_starts, today=date(2026, 10, 21)) == date(2025, 12, 31)

    def ucc(filed):
        return {"source_type": "ucc", "deal_type": "financing", "ucc_state": "CO", "ucc_debtor": "HIGH PLAINS POST ACUTE LLC",
                "lender": "DMT SPE I LLC", "acquisition_date": filed, "ccns": ["065355"]}
    deals = [ucc("2025-06-01"), ucc("2026-06-01")]
    categorize(deals, owner_starts, today=date(2026, 10, 21))
    assert [d["deal_type"] for d in deals] == ["refinancing", "financing"]


def test_state_notice_date_marks_acquisition_financing():
    from pipeline.ucc_category import categorize
    owner_starts = {"395917": {date(2014, 1, 1)}}
    deals = [{"source_type": "con", "deal_type": "ownership_change", "acquisition_date": "2026-07-08", "ccns": ["395917"]},
             {"source_type": "ucc", "deal_type": "financing", "ucc_state": "PA", "ucc_debtor": "BM REHAB & NURSING CENTER LLC",
              "lender": "DMT SPE I LLC", "acquisition_date": "2026-07-08", "ccns": ["395917"]}]
    categorize(deals, owner_starts, today=date(2026, 10, 21))
    assert deals[1]["deal_type"] == "refinancing"                  # matcher ccns on a notice don't count
    categorize(deals, owner_starts, today=date(2026, 10, 21), notices=[deals[0]])
    assert deals[1]["deal_type"] == "acquisition_financing"
    assert owner_starts == {"395917": {date(2014, 1, 1)}}          # caller's dict untouched


# ── Audit 2026-10-21: labels that misread what a filing says ──

def test_operator_as_secured_party_is_an_operations_transfer():
    from pipeline.ucc_category import categorize, is_operator
    assert is_operator("CUTLER BAY SNF OPERATING LLC", "EAST RIDGE RETIREMENT VILLAGE, INC.")
    assert not is_operator("HEALTHCARE FINANCING OF AMERICA, LLC", "INTEGRATIVE HEALTH & REHAB LLC")
    assert not is_operator("SOVRAN HEALTH & REHAB CENTER, LLC",            # the debtor itself
                           "The Laurels of Gahanna, LLC, Sovran Health & Rehab Center, LLC")
    assert not is_operator("WASHINGTON OPERATIONS ASSOCIATES LL", "EMRYS TALLOWAY")  # a person's filing
    deals = [{"source_type": "ucc", "deal_type": "financing", "ucc_state": "FL", "ucc_debtor": "EAST RIDGE RETIREMENT VILLAGE, INC.",
              "lender": "CUTLER BAY SNF OPERATING LLC", "acquisition_date": "2025-06-23", "ccns": ["105508"]}]
    categorize(deals, {"105508": {date(2010, 4, 19)}}, today=date(2025, 6, 30))
    assert deals[0]["deal_type"] == "operations_transfer"            # was "refinancing" of the 2010 owner


def test_trust_added_next_to_owner_is_not_a_sale():
    from pipeline.ucc_category import owner_start_dates
    rows = [("105910", "CORAL REEF CENTER HOLDINGS LLC", date(2021, 3, 2)),
            ("105910", "MZM 2017 TRUST", date(2025, 2, 20)),
            ("366432", "IRREVOCABLE TRUST AGREEMENT OF BARRY A. KOHN", date(2024, 2, 15)),
            ("366432", "CHASE M. KOHN IRREVOCABLE TRUST", date(2024, 11, 1)),
            ("777777", "OLD OWNER LLC", date(2010, 1, 1)),
            ("777777", "NEW BUYER LLC", date(2025, 1, 1)),
            ("777777", "BUYER FAMILY TRUST", date(2025, 1, 1))]
    assert owner_start_dates(rows) == {"105910": {date(2021, 3, 2)}, "366432": {date(2024, 2, 15)},
                                       "777777": {date(2010, 1, 1), date(2025, 1, 1)}}


def test_equipment_lessors_named_like_healthcare_lenders():
    for name in ("LEAF CAPITAL FUNDING, LLC AND/OR ITS ASSIGNS", "MED ONE CAPITAL FUNDING, LLC",
                 "FIRST AMERICAN CAPITAL GROUP CORPORATION", "COACTIV CAPITAL PARTNERS, INC."):
        assert classify_secured_party(name).category == LenderCategory.EQUIPMENT_VENDOR, name
    assert classify_secured_party("CAPITAL FUNDING, LLC, AS AGENT").category == LenderCategory.REAL_ESTATE


def test_alert_title_from_another_story_counts_as_snippet():
    url = "https://skillednursingnews.com/2026/06/temperature-check-financing-is-top-factor-behind-stalled-nursing-home-deals/"
    assert rss.looks_like_snippet("Charlotte-area nursing homes, bringing operations to 32 locations with more", url)
    assert not rss.looks_like_snippet("Temperature Check: Financing Is Top Factor Behind Stalled Nursing Home Deals", url)
    assert not rss.looks_like_snippet("Company Announcements", "https://markets.ft.com/data/announce/detail?dockey=600-2026")


def test_same_sale_pairs_news_and_8k_on_seller_and_price(monkeypatch):
    import pipeline.same_sale as same_sale
    row = lambda i, src, buyer, value: {"id": i, "acquiring_entity": buyer, "seller_entity": "National Health Investors",
                                        "operator_names": None, "facility_names": None, "states": [], "facility_count": 35,
                                        "deal_value_m": value, "acquisition_date": date(2026, 7, 1), "stage": "detected",
                                        "ucc_confirmed": False, "extraction_model": None, "created_at": None,
                                        "source_type": src, "url": ""}
    deals = {"a": row("a", "edgar", "NHC/Op, L.P", 560.0), "b": row("b", "rss", None, 560.0), "c": row("c", "rss", None, 19.0)}
    monkeypatch.setattr(same_sale, "_load", lambda conn: deals)
    auto, review = same_sale.find_pairs(None)
    assert [(x["id"], y["id"]) for x, y, _ in auto] == [("a", "b")] and not review


# ── Grounding and CCN guards (audit 2026-10-21) ───────────────
# Claude filled fields from its own knowledge (homes a notice never names, a
# buyer from a different story); CHOW's repeat-sale suffix made CCNs that
# match no home; news deals were linked to a namesake home in another town.

def test_grounding_drops_what_the_source_never_says():
    from pipeline.grounding import ground
    text = ("Cliveden Nursing and Rehabilitation Center and Tucker House Nursing and Rehabilitation "
            "Center in Philadelphia were sold by Cliveden Operating LLC to Gem Care Partners for $14,500,000.")
    deal = {"acquiring_entity": "Gem Care Partners", "seller_entity": "Cliveden Operating LLC",
            "facility_names": ["Cliveden Nursing and Rehabilitation Center", "Tucker House Nursing and Rehabilitation Center",
                               "Maplewood Nursing and Rehabilitation Center"], "deal_value_m": 14.5}
    named = list(deal["facility_names"])
    out = ground(deal, text)
    assert out["facility_names"] == named[:2] and out["deal_value_m"] == 14.5
    assert ground({"acquiring_entity": "PACS Group", "facility_names": [], "deal_value_m": None},
                  "Earnings call highlights: revenue grew 12%.") is None
    # An anonymous story stays
    assert ground({"acquiring_entity": None, "facility_names": [], "deal_value_m": None}, "A 32-unit community in Utah sold.")
    # A lender the story never names is dropped (Sabra, "Creekridge Capital")
    out = ground({"acquiring_entity": "Sabra Health Care REIT", "lender": "Creekridge Capital",
                  "facility_names": [], "deal_value_m": None}, "Sabra Health Care REIT bought two homes.")
    assert out["lender"] is None and out["acquiring_entity"] == "Sabra Health Care REIT"


def test_grounding_reads_prices_as_filings_write_them():
    from pipeline.grounding import value_in_text
    assert value_in_text(14.5, "purchase price of $14,500, excluding closing costs")   # SEC table, thousands
    assert value_in_text(0.11, "Consideration: $ _______111,688.89___")                # Alabama notice form
    assert value_in_text(0.47, "___468,957.14______$")
    assert value_in_text(30.0, "$18 million for the operations and $12 million for the real estate")
    assert not value_in_text(10.4, "sold for $15 million")


def test_chow_repeat_sale_suffix_is_the_same_home():
    assert chow.home_ccn("475025B") == "475025"
    assert chow.home_ccn("335331001") == "335331"
    assert chow.home_ccn(" 05a123 ") == "05A123"


def test_named_home_must_agree_with_linked_home():
    from pipeline.cms_confirm import facility_names_agree
    assert facility_names_agree("Optalis Health and Rehabilitation of Three", "OPTALIS HEALTH AND REHABILITATION OF THREE RIVERS")
    assert facility_names_agree("Vista Grande Villa", "VISTA GRANDE VILLA")
    assert not facility_names_agree("Moldaw Residences", "ESKATON VILLAGE CARMICHAEL")


def test_consistency_check_finds_the_audit_mistakes():
    from scripts.consistency_check import issues
    homes = {"075183": {"name": "ESKATON VILLAGE CARMICHAEL", "city": "CARMICHAEL", "state": "CA", "owners": []},
             "015147": {"name": "GLENWOOD POST ACUTE", "city": "BIRMINGHAM", "state": "AL", "owners": []}}
    deal = lambda i, **kw: {"id": i, "source_type": "rss", "states": ["CA"], "acquisition_date": "2026-06-01",
                            "facility_names": [], "ccns": [], **kw}
    found = {(k, i) for k, i, _ in issues({"facilities": homes, "deals": [
        deal("moldaw", facility_names=["Moldaw Residences"], ccns=["075183"]),
        deal("glenwood", source_type="con", states=["AL"], facility_names=["Glenwood Post Acute"], ccns=["015147"],
             acquiring_entity="211-213 Ana Drive Opco", seller_entity="211-213 Ana Drive Opco, LLC"),
        deal("chow-ut", source_type="chow", ccns=["015147"]),
    ]})}
    assert found == {("home_not_named", "moldaw"), ("buyer_is_seller", "glenwood"), ("home_in_other_state", "chow-ut")}


def test_cms_owner_must_be_the_buyer_side_to_confirm():
    from pipeline.deal_labels import cms_event
    class Cms:
        def __init__(self, owner, start): self.ev = {"date": start, "owner": owner, "owner_count": 1, "ccn": "145445",
                                                     "source": "CMS ownership data", "facility": None}
        def new_owner(self, ccns, when): return dict(self.ev)
    deal = {"deal_type": "ownership_change", "ccns": ["145445"], "acquisition_date": "2025-10-01",
            "acquiring_entity": "Accolade Healthcare of Waterloo", "seller_entity": "Monroe County, Illinois"}
    assert cms_event(deal, Cms("MONROE COUNTY", "2025-12-01")) is None          # the seller
    assert cms_event(deal, Cms("SH OPCO HOLDCO LLC", "2025-04-19")) is None     # unrelated, months before
    assert cms_event(deal, Cms("ACCOLADE HEALTHCARE OF WATERLOO LLC", "2025-04-19"))  # the buyer, early
    assert cms_event(deal, Cms("ARIA HOLDCO LLC", "2025-11-15"))                # a parent, after closing


def test_ucc_filing_is_on_file_not_confirmed():
    from pipeline.deal_labels import status
    assert status({"source_type": "ucc"}, None)["status"] == "on_file"


def test_news_lease_transition_is_a_lease_not_a_sale():
    from pipeline.deal_labels import transaction_type
    d = {"source_type": "rss", "deal_type": "ownership_change", "acquiring_entity": "Saber Healthcare",
         "seller_entity": "Ciena",
         "summary": "Omega Healthcare Investors completed a lease transition during Q2 2026, moving 18 leased "
                    "Laurels portfolio facilities from Ciena to Saber Healthcare's master lease. This was an "
                    "operator change, not a sale."}
    assert transaction_type(d) == "lease"


def test_true_filings_that_are_not_deals_are_non_deal_liens():
    from datetime import date
    from pipeline.deal_labels import is_not_a_deal
    today = date(2026, 10, 21)
    old = {"source_type": "ucc", "ucc_state": "NY", "individual_filing": True, "deal_type": "financing",
           "acquisition_date": "2007-05-14", "lender": "MANUFACTURERS & TRADERS TRUST COMPANY", "lender_category": "Bank"}
    assert is_not_a_deal(old, today)
    # A nursing home lender's filing against an owner stays (Elderwood)
    assert not is_not_a_deal({**old, "lender": "CAPITAL FINANCE, LLC", "lender_category": "Healthcare RE / HUD lender"}, today)
    assert not is_not_a_deal({**old, "lender": "BANK OF AMERICA, N.A., AS ADMINISTRATIVE AGENT"}, today)
    # So does one CMS dates put near a sale, and a recent one
    assert not is_not_a_deal({**old, "deal_type": "acquisition_financing"}, today)
    assert not is_not_a_deal({**old, "acquisition_date": "2025-09-25"}, today)
    # NY nursing home banks that sort as plain banks stay (fresh sample 2026-10-21)
    for lender in ("VNB NEW YORK, LLC", "BANK HAPOALIM B.M.", "SIGNATURE BANK", "THE PRIVATEBANK AND TRUST COMPANY"):
        assert not is_not_a_deal({**old, "lender": lender}, today)
    # Equipment and vendor liens don't
    assert is_not_a_deal({**old, "individual_filing": False, "lender": "RED-D-ARC INC"}, today)
    assert is_not_a_deal({**old, "individual_filing": False, "lender": "AIRX FERGUSON"}, today)
    fl = {"source_type": "ucc", "ucc_state": "FL", "ccns": None, "deal_type": "financing"}
    assert is_not_a_deal({**fl, "ucc_debtor": "ORLANDO SCHOOL OF NURSING LLC.", "lender": "TARGETED EDUCATION FINANCE FUND LLC"}, today)
    assert is_not_a_deal({**fl, "ucc_debtor": "JOY LIFE NURSING, INC., A FLORIDA CORPORATION", "lender": "DAMAS MIRIAM"}, today)
    assert is_not_a_deal({**fl, "ucc_debtor": "SKILLED NURSING SERVICES INC", "lender": "BITTY ADVANCE 2, LLC"}, today)
    # A new property company CMS doesn't list yet stays
    assert not is_not_a_deal({**fl, "ucc_debtor": "STUART FL PROPCO II LLC", "lender": "CAPITAL FUNDING, LLC"}, today)
    assert not is_not_a_deal({**fl, "ucc_debtor": "PINES NURSING HOME 2015, LLC", "lender": "OPTIMUMBANK"}, today)
    assert not is_not_a_deal({**fl, "ucc_debtor": "TAYLOR SNF PROPCO LLC", "lender": "FORBRIGHT BANK, AS AGENT"}, today)
    # A deal's assisted living and physician companies, in any state (spot check 2026-10-26)
    wo = {"source_type": "ucc", "ucc_state": "PA", "ccns": None, "deal_type": "financing",
          "lender": "WHITE OAK HEALTHCARE FINANCE, LLC, AS ADMINISTRATIVE AGENT"}
    for debtor in ("WSSH PHYSICIAN SERVICES MSO, LLC", "WSSH LTC ACO LEAD, LLC", "WSSH IP, LLC",
                   "BETHEL ASSISTED LIVING LLC", "MOUNTAIN HOME AL OPCO LLC"):
        assert is_not_a_deal({**wo, "ucc_debtor": debtor}, today)
    assert not is_not_a_deal({**wo, "ucc_debtor": "1770 BARLEY RD. OPCO, LLC"}, today)
    assert not is_not_a_deal({**wo, "ucc_debtor": "BETHEL SKILLED CARE LLC"}, today)
    # linked to a home, it stays
    assert not is_not_a_deal({**wo, "ucc_debtor": "BETHEL ASSISTED LIVING LLC", "ccns": ["555924"]}, today)


def test_oh_lender_list_drops_the_homes_own_entities():
    from scripts.export_deals import clean_lender
    assert clean_lender("OTTAWA SNF OPCO LLC\n\nMetropolitan Commercial Bank, as Agent") == "Metropolitan Commercial Bank, as Agent"
    assert clean_lender("LANCASTER POLLARD MORTGAGE COMPANY\n\nSECRETARY OF HOUSING AND URBAN DEVELOPMENT") == \
        "LANCASTER POLLARD MORTGAGE COMPANY; SECRETARY OF HOUSING AND URBAN DEVELOPMENT"
    assert clean_lender("FORBRIGHT BANK") == "FORBRIGHT BANK"


def test_ucc_match_needs_the_distinctive_words_to_agree():
    from datetime import date
    from ucc.base import UCCFiling
    from ucc.integrator import ExistingDeal, match_against_existing_deals
    f = lambda debtor: UCCFiling(state="PA", debtor_name=debtor, secured_party_name="X BANK",
                                 filing_number="1", filing_date=date(2025, 5, 21))
    deal = lambda name, **kw: ExistingDeal(id=1, operator_names=[name], facility_names=[],
                                           acquisition_date=kw.get("on", date(2025, 3, 1)), states=kw.get("states", ["PA"]))
    # Same words but the home's: Havencrest is not Oak Glen, Valley is not Apple Valley
    assert match_against_existing_deals(f("HAVENCREST REHABILITATION AND HEALTHCARE CENTER"),
                                        [deal("OAK GLEN HEALTHCARE AND REHABILITATION CENTER LLC")]) is None
    assert match_against_existing_deals(f("VALLEY POST-ACUTE AND REHAB LLC"), [deal("Apple Valley Post-Acute Rehab")]) is None
    # The same entity still matches across punctuation and OPCO/LLC
    assert match_against_existing_deals(f("4401 HAVERFORD AVENUE LLC"), [deal("4401 Haverford Avenue OPCO")])
    assert match_against_existing_deals(f("QUALITY LIFE SERVICES – SARVER, LLC"), [deal("QUALITY LIFE SERVICES SARVER LLC")])
    # A deal with no date and no state matches nothing (Sabra / Communicare)
    assert match_against_existing_deals(f("ONYX WELLNESS CENTER LLC"), [deal("ONYX WELLNESS CENTER LLC", on=None, states=[])]) is None


def test_several_debtors_on_one_filing_are_split_after_company_suffixes():
    from scripts.enrich_ucc_facility_names import split_debtors
    assert split_debtors("670 OH OPCO LLC, 600 NORTH BRUSH OPCO LLC") == ["670 OH OPCO LLC", "600 NORTH BRUSH OPCO LLC"]
    assert split_debtors("Schroer Properties of Newark South Inc, Altercare Newark South Inc") == \
        ["Schroer Properties of Newark South Inc", "Altercare Newark South Inc"]
    # A comma before the suffix is part of one name
    assert split_debtors("Oak Hills Manor, LLC") == ["Oak Hills Manor, LLC"]
    assert split_debtors("FORBRIGHT BANK") == ["FORBRIGHT BANK"]


def test_new_licensee_names_skip_buyers_chow_already_lists():
    from scraper.licensee_names import new_licensee_names

    class Cur:
        def __init__(self): self.n = 0
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, sql, params=None): self.n += 1
        def fetchall(self):
            return {1: [("365380", "670 OH OPCO LLC")],                       # legal name changed
                    2: [("365510", "600 NORTH BRUSH OPCO LLC"), ("366000", "OLD OWNER LLC")],
                    3: [("366000", "OLD OWNER, LLC")]}[self.n]                 # CHOW already lists it
    class Conn:
        def cursor(self): return self.c
    conn = Conn(); conn.c = Cur()
    assert new_licensee_names(conn, "OH") == ["600 NORTH BRUSH OPCO LLC", "670 OH OPCO LLC"]


def test_ucc_debtors_named_by_street_address_or_town_link_to_the_home():
    """MD operators name homes' companies by address ("9101 SECOND AVENUE
    OPERATIONS LLC") or town ("DENTON MD PROPCO LLC"); 79 of 164 MD
    filings went unlinked without these rules (2026-10-21)."""
    from scripts.enrich_ucc_facility_names import _address_key, _address_match, _town_candidates
    assert _address_key("9101 SECOND AVENUE OPERATIONS LLC") == _address_key("9101 SECOND AVE") == ("9101", "SECOND")
    assert _address_key("22 S. ATHOL STREET, LLC") == ("22", "ATHOL")
    assert _address_key("ACME OPCO LLC") is None
    by_state = {("address", "MD", ("9101", "SECOND")): [("215060", "WOODSIDE")],
                ("address", "MD", ("1", "MAIN")): [("A", "X"), ("B", "Y")],
                ("city", "MD", "DENTON"): [("215999", "DENTON NURSING")]}
    assert _address_match("9101 SECOND AVENUE OPERATIONS, LLC", "MD", by_state) == ("215060", "WOODSIDE")
    assert _address_match("1 MAIN STREET LLC", "MD", by_state) is None  # two homes there
    assert _address_match("9101 SECOND AVENUE OPERATIONS LLC", "VA", by_state) is None
    assert _town_candidates("DENTON MD PROPCO LLC", "MD", by_state) == {("215999", "DENTON NURSING")}


def test_every_ucc_state_on_the_site_has_a_portal_link():
    """AZ, CO and MD records shipped the raw ucc://XX/... key, which no
    browser opens (2026-10-21); any state with UCC records needs a page."""
    from scripts.export_deals import UCC_PORTAL_URLS
    for state in ("NY", "KY", "OH", "PA", "NJ", "CA", "FL", "OR", "AR", "NM", "RI", "ID", "AZ", "CO", "MD"):
        assert UCC_PORTAL_URLS[state].startswith("https://"), state


def test_ucc_debtor_matches_a_unique_cms_legal_name_2026_10_22():
    """The matcher never read legal business names: "EPWORTH REHABILITATION
    AND HEALTHCARE LLC" is Cedarwood's licensed company, but neither its
    facility nor its owner names match. Exactly one home in the state, or no
    legal-name answer."""
    from matcher import ownership

    class Cur:
        def __init__(self, rows): self.rows, self.description = rows, []
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, sql, params=None): self.sql = sql
        def fetchall(self): return self.rows if "cms_facilities" in self.sql else []

    class Conn:
        def __init__(self, rows): self.rows = rows
        def cursor(self): return Cur(self.rows)

    cedarwood = ("395393", "CEDARWOOD REHABILITATION & HEALTHCARE CENTER", "Epworth Rehabilitation and Healthcare, LLC", "PA")
    deal = {"extraction_model": "ucc_filing", "states": ["PA"],
            "operator_names": ["EPWORTH REHABILITATION AND HEALTHCARE L.L.C."]}
    m = ownership.match_deal(deal, Conn([cedarwood, ("395000", "OTHER", "Other Care LLC", "PA")]))
    assert [(x["ccn"], x["match_method"], x["match_score"]) for x in m] == [("395393", "legal_name_exact", 100)]

    # two homes with that legal name: no legal-name answer, fuzzy scoring runs instead
    twin = ("395999", "SECOND HOME", "EPWORTH REHABILITATION AND HEALTHCARE LLC", "PA")
    assert ownership._legal_name_match(deal, Conn([cedarwood, twin])) is None
    # a name that only shares words isn't equal
    assert ownership._legal_name_match({**deal, "operator_names": ["EPWORTH REALTY LLC"]}, Conn([cedarwood])) is None
    # a one-word name is too thin once the suffix is gone: Corporation vs LLC
    fair_oaks = ("555153", "FAIR OAKS HEALTHCARE CENTER", "MACKENZIE LLC", "CA")
    assert ownership._legal_name_match({**deal, "states": ["CA"], "operator_names": ["MACKENZIE CORPORATION"]},
                                       Conn([fair_oaks])) is None
    # only UCC deals use it
    assert ownership.match_deal({**deal, "extraction_model": "chow_direct"}, Conn([cedarwood])) == []
