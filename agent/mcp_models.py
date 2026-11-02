"""
Output types for the MCP server's tools (agent/mcp_server.py).

Each tool's return annotation is one of these models, so the server publishes an
outputSchema per tool and every result goes out as validated structuredContent,
not only a JSON string. extra="forbid": a key the ToolBox starts returning that
isn't declared here fails agent/tests/test_mcp_parity.py instead of being dropped
without a word. Dates are real dates in the schema; numbers the database hands
back as Decimal strings are coerced to floats.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── search_cms_facilities ─────────────────────────────────────
class FacilityMatch(_Out):
    ccn: str
    provider_name: Optional[str]
    legal_business_name: Optional[str]
    provider_city: Optional[str]
    provider_state: Optional[str]
    bed_count: Optional[int]
    five_star_rating: Optional[int]
    score: float = Field(description="word similarity of the query to the name, 0-1")


class FacilitySearch(_Out):
    matches: list[FacilityMatch]


# ── search_cms_owners ─────────────────────────────────────────
class OwnerMatch(_Out):
    ccn: str
    provider_name: Optional[str]
    provider_state: Optional[str]
    owner_name: Optional[str]
    owner_role: Optional[str]
    owner_type: Optional[str]
    ownership_percentage: Optional[float]
    ownership_start_date: Optional[date]
    score: float


class OwnerSearch(_Out):
    matches: list[OwnerMatch]


# ── search_chow_records ───────────────────────────────────────
class ChowMatch(_Out):
    ccn: Optional[str]
    effective_date: Optional[str] = Field(description="as CMS writes it, MM/DD/YYYY")
    chow_type: Optional[str]
    matched_as: Literal["buyer", "seller"]
    buyer: Optional[str]
    buyer_dba: Optional[str]
    seller: Optional[str]
    seller_dba: Optional[str]
    state: Optional[str]
    score: float


class ChowSearch(_Out):
    matches: list[ChowMatch]
    note: Optional[str] = None


# ── search_cms_by_address ─────────────────────────────────────
class AddressMatch(_Out):
    ccn: str
    provider_name: Optional[str]
    legal_business_name: Optional[str]
    provider_address: Optional[str]
    provider_city: Optional[str]
    provider_state: Optional[str]
    bed_count: Optional[int]
    street_words_matched: list[str]


class AddressSearch(_Out):
    matches: list[AddressMatch]
    street_words_searched: list[str] = []
    note: Optional[str] = None


# ── get_facility_ownership ────────────────────────────────────
class Facility(_Out):
    ccn: str
    provider_name: Optional[str]
    legal_business_name: Optional[str]
    provider_city: Optional[str]
    provider_state: Optional[str]
    bed_count: Optional[int]
    five_star_rating: Optional[int]
    sff_flag: Optional[bool] = Field(description="CMS Special Focus Facility")


class Owner(_Out):
    owner_name: Optional[str]
    owner_type: Optional[str]
    owner_role: Optional[str]
    ownership_percentage: Optional[float]
    ownership_start_date: Optional[date]


class ChowEvent(_Out):
    buyer_name: Optional[str]
    effective_date: date


class OwnerStart(_Out):
    date: date
    days_from_filing: int


class Timing(_Out):
    filing_date: date
    window_days: int
    owner_starts_near_filing: list[OwnerStart]
    cms_signal: Optional[Literal["acquisition_financing", "refinancing"]] = Field(
        description="null = CMS data can't say (it lags real sales by months), NOT refinancing")
    cms_owner_data_complete_through: date


class FacilityOwnership(_Out):
    facility: Optional[Facility]
    owners: list[Owner] = []
    chow_events: list[ChowEvent] = []
    timing: Optional[Timing] = Field(default=None, description="only when filing_date is given")
    note: Optional[str] = None


# ── UCC filings ───────────────────────────────────────────────
class UccFiling(_Out):
    state: str
    filing_number: str
    debtor_name: Optional[str]
    secured_party: Optional[str]
    filing_date: Optional[date]
    filing_type: Optional[str]
    status: Optional[str]
    lender_label: Optional[str]
    detail_url: Optional[str]


class UccFilingDetail(UccFiling):
    collateral_description: Optional[str]


class RelatedFilings(_Out):
    filings: list[UccFiling]


class OneFiling(_Out):
    filing: Optional[UccFilingDetail]


# ── search_deals ──────────────────────────────────────────────
class Deal(_Out):
    id: str
    acquiring_entity: Optional[str]
    seller_entity: Optional[str]
    operator_names: Optional[list[str]]
    facility_names: Optional[list[str]]
    states: Optional[list[str]]
    acquisition_date: Optional[date]
    lender: Optional[str]
    stage: Optional[str]
    extraction_model: Optional[str] = Field(description="the source: ucc_filing, cms_owner_change, a Claude model, ...")


class DealSearch(_Out):
    deals: list[Deal]


# ── classify_lender ───────────────────────────────────────────
class LenderClass(_Out):
    secured_party: str
    category: str
    label: Optional[str]
    confidence: float
    matched_signal: Optional[str]
    is_acquisition_relevant: bool
    nursing_home_bank: bool
    small_business_or_filing_agent: bool


# ── lookup_property_owner ─────────────────────────────────────
class PropertyRecord(_Out):
    owner_name: Optional[str]
    owner2: Optional[str]
    # ArcGIS layers return some of these as numbers
    parcel_id: Optional[str | int]
    account: Optional[str | int]
    situs_address: Optional[str]
    situs_city: Optional[str]
    last_sale_date: Optional[str | int] = Field(description="as the assessor writes it")
    city_matches: bool
    source_url: str
    ccn: str
    county: Optional[str] = None
    book_page: Optional[str] = None
    use: Optional[str] = None


class PropertyOwner(_Out):
    ccn: str
    facility: Optional[str] = None
    facility_address: Optional[str] = None
    records: list[PropertyRecord]
    source: Optional[str] = None
    attribution: Optional[str] = None
    note: Optional[str] = None
