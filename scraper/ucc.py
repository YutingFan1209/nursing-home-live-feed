"""
scraper/ucc.py

Wraps ucc/ky.py, ucc/nj.py, ucc/me.py (and lender_classifier.py) so UCC
filings look like every other source to main.py — same "article" dict
shape that discover_articles() / process_article() already expect from
RSS, EDGAR, and CHOW.

UCC filings are flagged with "ucc_filing": True (parallel to CHOW's
"pre_extracted": True) so process_article can route them through
ucc/integrator.py's confirmation/new-signal logic instead of the plain
pre_extracted path — a UCC-1 alone doesn't tell you who's acquiring whom
the way a CHOW record does, so it needs the extra matching step against
existing deals before becoming (or not becoming) a deals row.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from ucc.ky import KentuckyUCCSource
from ucc.nj import NewJerseyUCCSource
from ucc.me import MaineUCCSource
from ucc.lender_classifier import classify_secured_party
from ucc.base import UCCFiling

logger = logging.getLogger(__name__)

# NJ's portal rejects automated requests (redirects to Default.aspx regardless
# of headers/fields). The web search requires individual debtor lookups through
# a multi-step wizard that doesn't respond to programmatic POST. Disabled until
# a headless-browser solution is available. Flip to True to re-test.
ENABLE_NJ_AUTOMATION = False

# Maine: robots.txt explicitly disallows /cgi-bin/ which is where Maine's UCC
# search lives. Do not enable without legal/policy sign-off.
ENABLE_MAINE_AUTOMATION = False


def _filing_to_article(filing: UCCFiling) -> dict:
    """Shape a UCCFiling as a pseudo-"article" — synthetic URL doubles as
    the dedup key against the `articles` table (same role `art["url"]`
    plays for RSS/EDGAR/CHOW), so re-running the pipeline won't reprocess
    the same filing twice."""
    classification = classify_secured_party(filing.secured_party_name)
    return {
        "url": f"ucc://{filing.state}/{filing.filing_number}",
        "title": f"UCC-1 filing: {filing.debtor_name} / {filing.secured_party_name} ({filing.state})",
        "published_at": filing.filing_date,
        "ucc_filing": True,
        "pre_extracted": True,  # reuses existing flag so non-UCC code paths still treat this as non-Claude
        # Raw filing + classification, used by main.py's UCC branch:
        "_ucc_filing_obj": filing,
        "_ucc_classification": classification,
    }


def fetch_ucc_filings(known_operator_names: list[str], ky_bulk_file_path: str = None) -> list[dict]:
    """Pull UCC filings from all enabled state sources, return as article
    dicts ready for discover_articles() to append to its results list.

    known_operator_names: NJ (and most states) can only search by debtor
    name, not secured party — pull this from your existing deals table
    (see get_known_operator_names in main.py) rather than guessing.
    """
    filings: list[UCCFiling] = []

    ky_source = KentuckyUCCSource()
    if ky_bulk_file_path:
        try:
            filings.extend(ky_source.bulk_ingest(ky_bulk_file_path))
            logger.info(f"KY UCC: bulk ingest from {ky_bulk_file_path}")
        except Exception as e:
            logger.warning(f"KY UCC bulk ingest failed: {e}")
    else:
        logger.info("KY UCC: no bulk file configured, falling back to web search")
        for operator_name in known_operator_names:
            try:
                filings.extend(ky_source.search(operator_name))
            except Exception as e:
                logger.warning(f"KY UCC web search failed for {operator_name!r}: {e}")
    if ENABLE_NJ_AUTOMATION:
        nj_source = NewJerseyUCCSource()
        for operator_name in known_operator_names:
            try:
                filings.extend(nj_source.search(operator_name))
            except Exception as e:
                logger.warning(f"NJ UCC search failed for {operator_name!r}: {e}")
    else:
        logger.debug("NJ UCC automation disabled — portal rejects programmatic requests")

    if ENABLE_MAINE_AUTOMATION:
        me_source = MaineUCCSource()
        for operator_name in known_operator_names:
            try:
                filings.extend(me_source.search(operator_name))
            except Exception as e:
                logger.warning(f"ME UCC search failed for {operator_name!r}: {e}")
    else:
        logger.debug("Maine UCC automation disabled — see ucc/me.py docstring")

    logger.info(f"UCC: fetched {len(filings)} raw filings across enabled states")
    return [_filing_to_article(f) for f in filings]
