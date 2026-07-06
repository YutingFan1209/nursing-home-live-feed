"""
scraper/ucc.py
"""
from __future__ import annotations

import logging
from ucc.nj import NewJerseyUCCSource
from ucc.me import MaineUCCSource
from ucc.ny_playwright import search_ny_batch
from ucc.nj_playwright import search_nj
from ucc.oh_playwright import search_oh_batch
from ucc.ky_playwright import search_ky_batch
from ucc.pa_playwright import search_pa_batch
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
    ny_individual_names: list[str] = None,
    oh_search_names: list[str] = None,
    oh_individual_names: list[str] = None,
) -> list[dict]:
    filings: list[UCCFiling] = []

    # KY (Playwright, no Cloudflare, headless=True)
    # Use ky_search_names (CHOW facility-level LLCs) when available — the KY portal
    # only supports debtor search and these LLCs are the actual UCC debtors.
    # Falls back to known_operator_names if ky_search_names is not provided.
    if ENABLE_KY_PLAYWRIGHT:
        ky_terms = ky_search_names if ky_search_names else known_operator_names
        try:
            filings.extend(search_ky_batch(ky_terms))
        except Exception as e:
            logger.warning(f"KY UCC batch search failed: {e}")

    # PA (Chrome CDP required - manual only, not in automated pipeline)
    if ENABLE_PA_PLAYWRIGHT:
        try:
            filings.extend(search_pa_batch(known_operator_names))
        except Exception as e:
            logger.warning(f"PA UCC batch search failed: {e}")

    # NY (Playwright)
    # known_operator_names always run through Organization search.
    # ny_individual_names (CMS individual owner names, per Tyler's
    # methodology) run through the portal's separate Individual debtor
    # search — see ucc/ny_playwright.py:_search_one for why these can't
    # share a search mode.
    if ENABLE_NY_PLAYWRIGHT:
        try:
            filings.extend(search_ny_batch(
                org_names=known_operator_names,
                individual_names=ny_individual_names,
            ))
        except Exception as e:
            logger.warning(f"NY UCC batch search failed: {e}")
    
    # NJ (Playwright)
    if ENABLE_NJ_PLAYWRIGHT:
        for operator_name in known_operator_names:
            try:
                filings.extend(search_nj(operator_name))
            except Exception as e:
                logger.warning(f"NJ UCC Playwright search failed for {operator_name!r}: {e}")

    # NJ (legacy - disabled)
    if ENABLE_NJ_AUTOMATION:
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
    if ENABLE_OH_PLAYWRIGHT:
        oh_org_terms = oh_search_names if oh_search_names else known_operator_names
        try:
            filings.extend(search_oh_batch(
                org_names=oh_org_terms,
                individual_names=oh_individual_names,
            ))
        except Exception as e:
            logger.warning(f"OH UCC batch search failed: {e}")

    # ME
    if ENABLE_MAINE_AUTOMATION:
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
