"""
ucc/base.py

Common schema + base adapter interface for UCC filing ingestion.
Mirrors the pattern already used by scraper/rss.py and scraper/edgar.py
in nursing-home-live-feed: one adapter per source, normalized output,
hand off to pipeline/dedup.py and matcher/ownership.py downstream.

Two ingestion strategies exist depending on the state:
  1. SCRAPE  — query the state's public search UI directly (NJ, NY, ME)
  2. BULK    — download/parse a periodic flat-file export (KY)

Both strategies produce the same UCCFiling objects so the rest of the
pipeline (matcher, dedup, DB insert) doesn't need to know which path
a given record came from.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import date
from enum import Enum
from typing import Optional
import hashlib
import json


class IngestMethod(str, Enum):
    SCRAPE = "scrape"
    BULK = "bulk"


@dataclass
class UCCFiling:
    """Normalized UCC-1/UCC-3 record, common across all state sources."""

    state: str                     # USPS code, e.g. "KY"
    debtor_name: str
    secured_party_name: str
    filing_number: str
    filing_date: Optional[date] = None
    lapse_date: Optional[date] = None
    filing_type: str = "UCC-1"     # UCC-1, UCC-3 (amendment/continuation/termination)
    collateral_description: Optional[str] = None
    status: str = "active"         # active | lapsed | terminated
    source_url: Optional[str] = None
    raw: dict = field(default_factory=dict)  # original record, for debugging/audit
    co_debtors: list = field(default_factory=list)  # other co-debtor entities on the same filing
    is_related_party: bool = False  # True if a co-debtor is a family trust/LLC/holding entity
    source_confidence: Optional[str] = None  # carries a pre-existing identity-match confidence, if the source already did this (e.g. manual analysis imports) — distinct from lender_classifier's RE/PE relevance check

    def normalized_debtor(self) -> str:
        """Match the normalization already used in pipeline/dedup.py and
        matcher/ownership.py so the same name-variation problem
        ("HCR ManorCare" vs "ManorCare") is handled consistently."""
        return " ".join(self.debtor_name.lower().split())

    def normalized_secured_party(self) -> str:
        return " ".join(self.secured_party_name.lower().split())

    def dedup_hash(self) -> str:
        """Same SHA-256 pattern as pipeline/dedup.py's make_deal_hash,
        scoped to (state, filing_number) since that's the natural
        unique key for a UCC filing — avoids re-ingesting the same
        filing across repeated pulls."""
        key = json.dumps(
            {"state": self.state, "filing_number": self.filing_number},
            sort_keys=True,
        )
        return hashlib.sha256(key.encode()).hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["filing_date"] = self.filing_date.isoformat() if self.filing_date else None
        d["lapse_date"] = self.lapse_date.isoformat() if self.lapse_date else None
        return d


class UCCSource(ABC):
    """Base adapter. One subclass per state.

    Subclasses implement `search(debtor_name)` for the scrape path, or
    `bulk_ingest(file_path)` for the bulk-file path — whichever fits
    that state. A given subclass only needs to implement the method(s)
    relevant to its ingest_method.
    """

    state: str = ""
    ingest_method: IngestMethod = IngestMethod.SCRAPE
    is_free: bool = True  # set False for PA/DE once/if those get approved

    def search(self, debtor_name: str) -> list[UCCFiling]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support live search — "
            f"this state uses {self.ingest_method.value} ingestion."
        )

    def bulk_ingest(self, file_path: str) -> list[UCCFiling]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support bulk ingestion — "
            f"this state uses {self.ingest_method.value} ingestion."
        )
