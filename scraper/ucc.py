"""
scraper/ucc.py
"""
from __future__ import annotations

import logging
from ucc.ky import KentuckyUCCSource
from ucc.nj import NewJerseyUCCSource
from ucc.me import MaineUCCSource
from ucc.ny_playwright import search_ny
from ucc.lender_classifier import classify_secured_party
from ucc.base import UCCFiling

logger = logging.getLogger(__name__)

ENABLE_NJ_AUTOMATION = False
ENABLE_MAINE_AUTOMATION = False
ENABLE_NY_PLAYWRIGHT = True


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


def fetch_ucc_filings(known_operator_names: list[str], ky_bulk_file_path: str = None) -> list[dict]:
    filings: list[UCCFiling] = []

    # KY
    ky_source = KentuckyUCCSource()
    if ky_bulk_file_path:
        try:
            filings.extend(ky_source.bulk_ingest(ky_bulk_file_path))
        except Exception as e:
            logger.warning(f"KY UCC bulk ingest failed: {e}")
    else:
        for operator_name in known_operator_names:
            try:
                filings.extend(ky_source.search(operator_name))
            except Exception as e:
                logger.warning(f"KY UCC web search failed for {operator_name!r}: {e}")

    # NY (Playwright)
    if ENABLE_NY_PLAYWRIGHT:
        for operator_name in known_operator_names:
            try:
                filings.extend(search_ny(operator_name))
            except Exception as e:
                logger.warning(f"NY UCC Playwright search failed for {operator_name!r}: {e}")
    
    # NJ
    if ENABLE_NJ_AUTOMATION:
        nj_source = NewJerseyUCCSource()
        for operator_name in known_operator_names:
            try:
                filings.extend(nj_source.search(operator_name))
            except Exception as e:
                logger.warning(f"NJ UCC search failed for {operator_name!r}: {e}")

    # ME
    if ENABLE_MAINE_AUTOMATION:
        me_source = MaineUCCSource()
        for operator_name in known_operator_names:
            try:
                filings.extend(me_source.search(operator_name))
            except Exception as e:
                logger.warning(f"ME UCC search failed for {operator_name!r}: {e}")

    logger.info(f"UCC: fetched {len(filings)} raw filings across enabled states")
    return [_filing_to_article(f) for f in filings]
