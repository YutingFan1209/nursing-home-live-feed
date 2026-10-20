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
    f = lambda lender, filed: UCCFiling(state="NY", debtor_name="WARREN COLE", secured_party_name=lender,
                                        filing_number="1", filing_date=filed)
    assert keep_individual_filing(f("CIBC BANK USA, AS ADMINISTRATIVE AGENT", date(2025, 12, 31)), today)
    assert keep_individual_filing(f("", None), today)
    assert not keep_individual_filing(f("CIBC BANK USA", date(2023, 3, 31)), today)
    for lender in ("SHEFFIELD FINANCIAL, A DIVISION OF TRUIST BANK", "GOODLEAP,LLC", "LOANPAL, LLC",
                   "CAPITAL RESORTS GROUP LLC", "AMERICU CREDOIT UNION", "DEERE & COMPANY"):
        assert not keep_individual_filing(f(lender, date(2026, 5, 1)), today), lender
