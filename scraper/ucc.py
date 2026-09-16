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

ENABLE_NJ_AUTOMATION = False
ENABLE_MAINE_AUTOMATION = False
ENABLE_NY_PLAYWRIGHT = True
ENABLE_NJ_PLAYWRIGHT = False
ENABLE_OH_PLAYWRIGHT = True
ENABLE_KY_PLAYWRIGHT = True
ENABLE_PA_PLAYWRIGHT = False
ENABLE_CA_PLAYWRIGHT = False  # Incapsula-protected, needs Chrome CDP -- manual trigger only, same as PA


def _filing_to_article(filing: UCCFiling) -> dict:
    classification = classify_secured_party(filing.secured_party_name)
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
    # Use ky_search_names (CHOW facility-level LLCs) when available — the KY portal
    # only supports debtor search and these LLCs are the actual UCC debtors.
    # Falls back to known_operator_names if ky_search_names is not provided.
    if _enabled(ENABLE_KY_PLAYWRIGHT, "KY"):
        ky_terms = ky_search_names if ky_search_names else known_operator_names
        try:
            filings.extend(search_ky_batch(ky_terms))
        except Exception as e:
            logger.warning(f"KY UCC batch search failed: {e}")

    # PA (Chrome CDP required - manual only, not in automated pipeline)
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
    # when available -- known_operator_names is the generic national
    # parent-operator list and almost none of those are NY-registered
    # entities, so relying on it alone silently misses nearly all real NY
    # hits (confirmed 2026-09-15: 0 of the ~150 query names that
    # historically found real NY filings were even in that list).
    # ny_individual_names (CMS individual owner names, per Tyler's
    # methodology) run through the portal's separate Individual debtor
    # search — see ucc/ny_playwright.py:_search_one for why these can't
    # share a search mode.
    if _enabled(ENABLE_NY_PLAYWRIGHT, "NY"):
        ny_terms = ny_search_names if ny_search_names else known_operator_names
        try:
            filings.extend(search_ny_batch_cdp(
                org_names=ny_terms,
                individual_names=ny_individual_names,
            ))
        except Exception as e:
            logger.warning(f"NY UCC batch search failed: {e}")
    
    # NJ (Playwright)
    if _enabled(ENABLE_NJ_PLAYWRIGHT, "NJ"):
        for operator_name in known_operator_names:
            try:
                filings.extend(search_nj(operator_name))
            except Exception as e:
                logger.warning(f"NJ UCC Playwright search failed for {operator_name!r}: {e}")

    # NJ (legacy - disabled)
    if _enabled(ENABLE_NJ_AUTOMATION, "NJ"):
        nj_source = NewJerseyUCCSource()
        for operator_name in known_operator_names:
            try:
                filings.extend(nj_source.search(operator_name))
            except Exception as e:
                logger.warning(f"NJ UCC search failed for {operator_name!r}: {e}")

    # OH (Playwright, hidden window)
    # Use oh_search_names (CHOW facility-level LLCs) when available — the OH
    # portal is a debtor search and known_operator_names is PE/lender firm
    # names, not the facility-level LLC debtors that actually appear as
    # debtors on OH filings (same bug ky_search_names fixed for KY).
    # oh_individual_names routes through the portal's separate Individual
    # debtor mode (personInd1) — see ucc/oh_playwright.py:_search_one.
    if _enabled(ENABLE_OH_PLAYWRIGHT, "OH"):
        oh_org_terms = oh_search_names if oh_search_names else known_operator_names
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
