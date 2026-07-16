"""
ucc/integrator.py

Per 6/17 clarification: UCC filings run alongside the existing pipeline
BOTH as confirmation of deals you already have AND as a new source for
acquisitions CHOW/EDGAR haven't surfaced yet. This module is the routing
layer that decides, per filing, which of those two paths applies.

Flow per filing:
  1. Classify the secured party (lender_classifier.py) — skip entirely if
     not RE/PE relevant (equipment/vendor financing noise).
  2. Fuzzy-match the debtor name against existing deals within a date
     window (your real matcher/ownership.py likely already has this exact
     logic for cross-referencing CHOW vs EDGAR — swap in the real function,
     see NOTE in match_against_existing_deals below).
  3a. Match found  -> CONFIRMATION: attach to existing deal, set
      ucc_confirmed=true, leave confidence_level alone (already 'confirmed'
      from the original CHOW/EDGAR source).
  3b. No match     -> NEW_SIGNAL: this filing becomes the seed of a new
      preliminary deal, confidence_level='unconfirmed'.

This intentionally returns a decision object rather than writing to the
DB directly — keeps this testable without a live database connection,
and keeps the actual INSERT logic in main.py where your DB connection
pattern already lives.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Optional

from .base import UCCFiling
from .lender_classifier import classify_secured_party, LenderClassification

logger = logging.getLogger(__name__)

try:
    from rapidfuzz import fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False


class RoutingDecision(str, Enum):
    CONFIRMATION = "confirmation"
    NEW_SIGNAL = "new_signal"
    EXCLUDED = "excluded"  # not RE/PE relevant, dropped before matching


@dataclass
class ExistingDeal:
    """Shape matching your real `deals` table columns (operator_names and
    facility_names are arrays, per main.py's _store_deal/recheck_pending
    queries — not singular strings as an earlier draft of this assumed)."""
    id: int
    operator_names: list[str]
    facility_names: list[str]
    acquisition_date: Optional[date]
    lender: Optional[str] = None


@dataclass
class RoutingResult:
    filing: UCCFiling
    classification: LenderClassification
    decision: RoutingDecision
    matched_deal_id: Optional[int] = None
    match_score: Optional[float] = None


# How close a name match needs to be, and how wide a date window counts
# as "the same deal" — tune these once you see real match/non-match cases.
NAME_MATCH_THRESHOLD = 85  # rapidfuzz token_sort_ratio, 0-100
DATE_WINDOW_DAYS = 180     # UCC-1 filings often lag/lead the actual deal


def route_filing(
    filing: UCCFiling,
    existing_deals: list[ExistingDeal],
) -> RoutingResult:
    """Single entry point — call this for every UCC filing you ingest."""

    classification = classify_secured_party(filing.secured_party_name)

    if not classification.is_acquisition_relevant:
        return RoutingResult(filing, classification, RoutingDecision.EXCLUDED)

    match = match_against_existing_deals(filing, existing_deals)

    if match is not None:
        deal_id, score = match
        return RoutingResult(
            filing, classification, RoutingDecision.CONFIRMATION,
            matched_deal_id=deal_id, match_score=score,
        )

    return RoutingResult(filing, classification, RoutingDecision.NEW_SIGNAL)


def match_against_existing_deals(
    filing: UCCFiling,
    existing_deals: list[ExistingDeal],
) -> Optional[tuple[int, float]]:
    """Returns (deal_id, match_score) for the best match above threshold,
    or None if nothing qualifies.

    NOTE: this is a standalone implementation so it's testable here without
    your real matcher module. Your actual matcher/ownership.py almost
    certainly already solves "HCR ManorCare" vs "ManorCare" — if its fuzzy
    match function is importable, replace this function's body with a call
    to that instead of maintaining two separate fuzzy-match implementations.
    """
    if not _HAS_RAPIDFUZZ:
        raise ImportError(
            "rapidfuzz not installed — pip install rapidfuzz, or swap this "
            "function out for your existing matcher/ownership.py logic."
        )

    best_match: Optional[tuple[int, float]] = None
    filing_name = filing.normalized_debtor()

    for deal in existing_deals:
        if filing.filing_date and deal.acquisition_date:
            delta = abs((filing.filing_date - deal.acquisition_date).days)
            if delta > DATE_WINDOW_DAYS:
                continue  # too far apart in time to plausibly be the same deal

        candidate_names = list(deal.operator_names or []) + list(deal.facility_names or [])
        for candidate_name in candidate_names:
            if not candidate_name:
                continue
            score = fuzz.token_sort_ratio(filing_name, candidate_name.lower())
            if score >= NAME_MATCH_THRESHOLD:
                if best_match is None or score > best_match[1]:
                    best_match = (deal.id, score)

    return best_match
