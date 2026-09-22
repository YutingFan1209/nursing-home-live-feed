"""
scraper/ucc.py
"""
from __future__ import annotations

import logging
from ucc.nj import NewJerseyUCCSource
from ucc.me import MaineUCCSource
from ucc.ny_playwright import search_ny_batch_cdp
from ucc.nj_playwright import search_nj
from ucc.oh_playwright import search_oh_batch
from ucc.ky_playwright import search_ky_batch
from ucc.pa_playwright import search_pa_batch
from ucc.ca_playwright import search_ca_batch
from ucc.lender_classifier import classify_secured_party
from ucc.base import UCCFiling

logger = logging.getLogger(__name__)


def _union_names(chow_names: list[str] | None, known_names: list[str]) -> list[str]:
    """Union a state's CHOW facility-name list with the live known_operator_names
    list (deals discovered since the CHOW CSV snapshot, e.g. via RSS/Gmail/EDGAR),
    deduped case-insensitively, CHOW names first. Confirmed 2026-09-21 that
    known_operator_names being used only as a fallback (never merged) left a
    real blind spot: 34 of 129 KY deal entity names had never been searched,
    and 4 real filings were found only once they were. See memory
    ucc_chow_name_blind_spot / docs/data-sources.md Known Issues."""
    chow_names = chow_names or []
    seen = {n.strip().upper() for n in chow_names}
    merged = list(chow_names)
    for n in known_names:
        key = n.strip().upper()
        if key not in seen:
            seen.add(key)
            merged.append(n)
    return merged

ENABLE_NJ_AUTOMATION = False
ENABLE_MAINE_AUTOMATION = False
ENABLE_NY_PLAYWRIGHT = True
ENABLE_NJ_PLAYWRIGHT = True  # re-enabled 2026-09-22 -- disabled 2026-06-23 because the
# free non-certified search doesn't return secured-party/lender name without paying
# per filing; user decided debtor-name/date-only is still worth having as a
# confirmation/discovery signal. See ucc/nj_playwright.py -- one fresh headless
# Chromium launch per name, no parallelism (unlike NY/KY/OH/PA), so a full run
# against known_operator_names (446 names as of 2026-09-22) would take hours.
ENABLE_OH_PLAYWRIGHT = True
ENABLE_KY_PLAYWRIGHT = True
ENABLE_PA_PLAYWRIGHT = True  # confirmed 2026-09-16: auto-launch works, no longer manual-only
ENABLE_CA_PLAYWRIGHT = False  # Incapsula-protected, needs Chrome CDP -- manual trigger only, same as PA


def _filing_to_article(filing: UCCFiling) -> dict:
    classification = classify_secured_party(filing.secured_party_name, state=filing.state)
    return {
        "url": f"ucc://{filing.state}/{filing.filing_number}",
        "title": f"UCC-1 filing: {filing.debtor_name} / {filing.secured_party_name} ({filing.state})",
        "published_at": filing.filing_date,
        "ucc_filing": True,
        "pre_extracted": True,
        "_ucc_filing_obj": filing,
        "_ucc_classification": classification,
    }


def fetch_ucc_filings(
    known_operator_names: list[str],
    ky_bulk_file_path: str = None,
    ky_search_names: list[str] = None,
    ny_search_names: list[str] = None,
    ny_individual_names: list[str] = None,
    oh_search_names: list[str] = None,
    oh_individual_names: list[str] = None,
    nj_search_names: list[str] = None,
    ca_search_names: list[str] = None,
    ca_individual_names: list[str] = None,
    states: list[str] = None,
) -> list[dict]:
    """states: optional list of 2-letter state codes (case-insensitive) to
    restrict this call to -- e.g. states=["NY"] runs only the NY fetcher,
    regardless of which ENABLE_*_PLAYWRIGHT flags are set. Lets a run be
    scoped to one state at a time (each state's real-portal automation has
    turned out to have very different failure modes and runtimes -- KY's
    same-day rate limit, NY's multi-hour individual-name list, OH's 429s --
    so bundling them into one all-or-nothing call, where nothing commits
    until every state finishes, has repeatedly cost hours of already-good
    work when one state hangs or gets blocked partway through). Omit (or
    pass None) to run every ENABLE_*_PLAYWRIGHT-flagged state, the
    original all-in-one-call behavior.
    """
    wanted = {s.upper() for s in states} if states else None

    def _enabled(flag: bool, code: str) -> bool:
        return flag and (wanted is None or code in wanted)

    filings: list[UCCFiling] = []

    # KY (Playwright, no Cloudflare, headless=True)
    # Use ky_search_names (CHOW facility-level LLCs) — the KY portal only
    # supports debtor search and these LLCs are the actual UCC debtors —
    # unioned with known_operator_names (live deals discovered since the
    # CHOW CSV snapshot) so post-CHOW entities aren't silently skipped.
    # Previously ky_search_names took exclusive precedence when non-empty,
    # which left 34/129 KY deal names never searched (fixed 2026-09-22).
    if _enabled(ENABLE_KY_PLAYWRIGHT, "KY"):
        ky_terms = _union_names(ky_search_names, known_operator_names)
        try:
            filings.extend(search_ky_batch(ky_terms))
        except Exception as e:
            logger.warning(f"KY UCC batch search failed: {e}")

    # PA (Chrome CDP, auto-launched -- confirmed 2026-09-16 this no longer
    # needs a manual browser session, same fix as NY. Still using the
    # generic national operator list rather than a PA-specific facility
    # name list (no ky_search_names/ny_search_names-style fix done for PA
    # yet) -- may under-hit the same way NY did before that fix.
    if _enabled(ENABLE_PA_PLAYWRIGHT, "PA"):
        try:
            filings.extend(search_pa_batch(known_operator_names))
        except Exception as e:
            logger.warning(f"PA UCC batch search failed: {e}")

    # CA (Chrome CDP required - Incapsula, manual only, not in automated pipeline)
    # CA's search API is a single unified index over debtor + secured-party
    # names (see ucc/ca_playwright.py docstring) -- no mode split needed,
    # so search and individual terms are just merged into one term list.
    if _enabled(ENABLE_CA_PLAYWRIGHT, "CA"):
        ca_terms = (ca_search_names or known_operator_names) + (ca_individual_names or [])
        try:
            filings.extend(search_ca_batch(ca_terms))
        except Exception as e:
            logger.warning(f"CA UCC batch search failed: {e}")

    # NY (Playwright over Chrome CDP — see ucc/ny_playwright.py module
    # docstring: the portal's Cloudflare Turnstile challenge blocks plain
    # headless Chrome entirely, confirmed 2026-09-15. search_ny_batch_cdp
    # auto-launches a real Chrome with a debug port if one isn't already
    # running, so this still runs unattended from cron.
    # Use ny_search_names (CHOW facility-level LLCs, same pattern as KY/OH)
    # unioned with known_operator_names -- known_operator_names alone is the
    # generic national parent-operator list and almost none of those are
    # NY-registered entities, so relying on it alone silently misses nearly
    # all real NY hits (confirmed 2026-09-15: 0 of the ~150 query names that
    # historically found real NY filings were even in that list). But
    # ny_search_names taking exclusive precedence over known_operator_names
    # has the opposite blind spot -- deals discovered after the CHOW CSV
    # snapshot never get searched either (confirmed for KY 2026-09-21,
    # same code path; fixed 2026-09-22).
    # ny_individual_names (CMS individual owner names, per Tyler's
    # methodology) run through the portal's separate Individual debtor
    # search — see ucc/ny_playwright.py:_search_one for why these can't
    # share a search mode.
    if _enabled(ENABLE_NY_PLAYWRIGHT, "NY"):
        ny_terms = _union_names(ny_search_names, known_operator_names)
        try:
            filings.extend(search_ny_batch_cdp(
                org_names=ny_terms,
                individual_names=ny_individual_names,
            ))
        except Exception as e:
            logger.warning(f"NY UCC batch search failed: {e}")
    
    # NJ (Playwright, sequential -- one fresh headless Chromium launch per
    # name, no batching/parallelism unlike NY/KY/OH/PA, so keep this list
    # small: nj_search_names (CHOW NJ buyer names, ~141 as of 2026-09-22)
    # unioned with known_operator_names would be the full national list
    # (446 as of 2026-09-22, hours to run sequentially) -- use nj_search_names
    # alone when available instead of unioning, to keep runtime bounded.
    if _enabled(ENABLE_NJ_PLAYWRIGHT, "NJ"):
        nj_terms = nj_search_names if nj_search_names else known_operator_names
        logger.info(f"NJ UCC: starting sequential search of {len(nj_terms)} names (no parallelism)")
        nj_found_total = 0
        for i, operator_name in enumerate(nj_terms, start=1):
            try:
                nj_results = search_nj(operator_name)
                nj_found_total += len(nj_results)
                filings.extend(nj_results)
                logger.info(f"NJ UCC [{i}/{len(nj_terms)}] {operator_name!r} -> {len(nj_results)} filings (running total: {nj_found_total})")
            except Exception as e:
                logger.warning(f"NJ UCC [{i}/{len(nj_terms)}] Playwright search failed for {operator_name!r}: {e}")

    # NJ (legacy - disabled)
    if _enabled(ENABLE_NJ_AUTOMATION, "NJ"):
        nj_source = NewJerseyUCCSource()
        for operator_name in known_operator_names:
            try:
                filings.extend(nj_source.search(operator_name))
            except Exception as e:
                logger.warning(f"NJ UCC search failed for {operator_name!r}: {e}")

    # OH (Playwright, hidden window)
    # Use oh_search_names (CHOW facility-level LLCs) unioned with
    # known_operator_names — the OH portal is a debtor search and
    # known_operator_names alone is PE/lender firm names, not the
    # facility-level LLC debtors that actually appear as debtors on OH
    # filings (same bug ky_search_names fixed for KY), but oh_search_names
    # taking exclusive precedence has the opposite blind spot: deals
    # discovered after the CHOW CSV snapshot never get searched (fixed
    # 2026-09-22, same fix as KY/NY).
    # oh_individual_names routes through the portal's separate Individual
    # debtor mode (personInd1) — see ucc/oh_playwright.py:_search_one.
    if _enabled(ENABLE_OH_PLAYWRIGHT, "OH"):
        oh_org_terms = _union_names(oh_search_names, known_operator_names)
        try:
            filings.extend(search_oh_batch(
                org_names=oh_org_terms,
                individual_names=oh_individual_names,
            ))
        except Exception as e:
            logger.warning(f"OH UCC batch search failed: {e}")

    # ME
    if _enabled(ENABLE_MAINE_AUTOMATION, "ME"):
        me_source = MaineUCCSource()
        for operator_name in known_operator_names:
            try:
                filings.extend(me_source.search(operator_name))
            except Exception as e:
                logger.warning(f"ME UCC search failed for {operator_name!r}: {e}")

    logger.info(f"UCC: fetched {len(filings)} raw filings across enabled states")
    articles = []
    excluded = 0
    for f in filings:
        art = _filing_to_article(f)
        if art["_ucc_classification"].is_acquisition_relevant:
            articles.append(art)
        else:
            excluded += 1
    if excluded:
        logger.info(f"UCC: pre-filtered {excluded} filings as non-RE/PE (equipment/vendor) before article queue")
    return articles
