"""
ucc/lender_classifier.py

Per the 6/17 meeting scope: only RE (real estate) and PE (private equity)
secured parties matter as acquisition signals. Most UCC-1 filings against
a nursing home are NOT acquisition-related (equipment leases, vendor
financing, pharmacy/supply liens, etc.) — this module filters those out
before a filing ever reaches matcher/ownership.py.

This is intentionally a layered approach, not a single regex:
  1. Known-entity lookup (highest confidence) — maintain a small seed list
     of known healthcare RE/PE lenders (CareTrust, Omega, Sabra, Ventas,
     etc. for RE; common PE sponsors active in SNF space)
  2. Keyword/suffix heuristics (medium confidence) — catches lenders not
     in the seed list
  3. Explicit exclusion list (filters out near-certain non-acquisition
     filings — equipment finance cos, pharmacy suppliers, etc.)

Returns a confidence-scored classification rather than a hard yes/no,
since this list will need tuning once real filings start coming in —
flag for review instead of silently dropping borderline cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class LenderCategory(str, Enum):
    REAL_ESTATE = "real_estate"
    PRIVATE_EQUITY = "private_equity"
    BANK_GENERAL = "bank_general"      # ambiguous — could be acquisition financing or working capital
    EQUIPMENT_VENDOR = "equipment_vendor"  # near-certain exclude
    UNKNOWN = "unknown"


@dataclass
class LenderClassification:
    category: LenderCategory
    confidence: float          # 0.0–1.0
    matched_signal: str        # what triggered the classification, for debugging
    is_acquisition_relevant: bool


# Seed list — known healthcare REITs and PE sponsors active in the SNF
# space. Extend this as real filings surface unfamiliar names; this is
# meant to be a living list, not exhaustive on day one.
KNOWN_HEALTHCARE_REITS = {
    "ctw investment", "caretrust reit", "omega healthcare", "sabra health care",
    "ventas", "national health investors", "lt c properties", "medical properties trust",
    "healthpeak", "healthcare trust of america", "diversified healthcare trust",
    "greystone", "midcap funding", "midcap financial",
    "gmcc", "monticello",
    "welltower", "capital funding",
    "berkadia", "keybank", "regions bank healthcare",
    "ares capital", "benefit street", "owl rock",
}

KNOWN_PE_SPONSORS = {
    "formation capital", "blue mountain capital", "portopiccolo group",
    "national healthcare corp", "kindred healthcare", "genesis healthcare",
    "thearle", "infinity healthcare management", "general partners",
}

RE_SUFFIX_PATTERNS = [
    r"\breit\b", r"\breal estate\b", r"\bproperty (holdings|trust|group)\b",
    r"\brealty\b", r"\bproperties\b",
]

PE_SUFFIX_PATTERNS = [
    r"\bcapital partners\b", r"\bequity partners\b", r"\bcapital management\b",
    r"\bprivate equity\b", r"\binvestment partners\b", r"\bholdings, ?lp\b",
    r"\bfund [ivx]+\b",  # "Fund III", "Fund IV" — common PE fund naming
]

# Near-certain exclude — these show up constantly in nursing home UCC
# filings but represent equipment/vendor financing, not ownership signals
EXCLUDE_PATTERNS = [
    r"\bmedical equipment\b", r"\bpharmacy\b", r"\bleasing corp\b",
    r"\bfinancial services\b.*\bequipment\b", r"\bdialysis\b", r"\bhealthcare staffing\b",
    r"\bde lage landen\b",
    r"\bgeneral electric capital\b", r"\bus bank equipment finance\b",
    r"\bhewlett.?packard\b",       # equipment/tech vendor
    r"\bct corporation system\b",  # registered agent, not a real lender
    r"\bcorporation service company\b",  # registered agent
    r"\bnational registered agents\b",   # registered agent
    # Equipment leasing companies — any name containing "leasing" is near-certainly
    # an equipment/vehicle lessor, not a nursing home acquisition financier
    r"\bleasing\b",
    # Explicit equipment finance patterns not caught by the above
    r"\bequipment finance\b",      # "Old National Equipment Finance", "Popular Equipment Finance"
    # Known medical device / imaging vendors that file UCC-1s for equipment
    r"\bstryker\b",                # Stryker Sales Corp, Flex Financial/Stryker
    r"\bagfa\b",                   # AGFA Finance Corporation (imaging)
    r"\bkarl storz\b",             # Karl Storz Capital (endoscopy)
    r"\bolympus america\b",        # Olympus America Inc. (endoscopy equipment)
    r"\bzimmer\b",                 # Zimmer US / Zimmer Biomet (orthopedic devices)
    r"\bglobus medical\b",         # Globus Medical (spine surgical equipment)
    r"\bb\.? braun\b",             # B. Braun Medical (medical devices)
    r"\bortho.?clinical\b",        # Ortho-Clinical Diagnostics
    r"\bquidelortho\b",            # QuidelOrtho Sales
    r"\bmed one capital\b",        # Med One Capital Funding (healthcare equipment finance)
    r"\basd specialty healthcare\b",  # ASD Specialty Healthcare (specialty pharma)
    r"\bameriseourcebergen\b",     # AmerisourceBergen (drug distributor)
    r"\bmaster trustee\b",         # Bond trustee role — hospital bond, not RE acquisition
    r"\bheidel.?berg materials\b", # Construction aggregate (clearly wrong sector)
    r"\bintegrated commercialization\b",  # Specialty pharma distribution
    r"\bbanyan capital solutions\b",      # Healthcare equipment leasing agent
    r"\bsolar mosaic\b",            # Residential solar financing — personal, not acquisition
    r"\bcredit union\b",            # Personal/consumer banking, not healthcare acquisition financing
    r"\bfcu\b",                     # Abbreviated credit union names ("XYZ FCU")
    r"\bdll finance\b",             # DLL Finance LLC — equipment financing
    r"\bsmall business administration\b",  # SBA — generic small-business, not healthcare-RE-specific
]


def classify_secured_party(name: str) -> LenderClassification:
    """Classify a UCC secured-party name as RE / PE / general bank /
    equipment-vendor / unknown, with a confidence score and the signal
    that drove the decision (for debugging false positives later)."""

    if not name:
        return LenderClassification(LenderCategory.UNKNOWN, 0.0, "no secured party name", False)

    normalized = " ".join(name.lower().strip().split())

    # 1. Known-entity lookup (highest confidence)
    for known in KNOWN_HEALTHCARE_REITS:
        if known in normalized:
            return LenderClassification(
                LenderCategory.REAL_ESTATE, 0.95, f"known REIT: {known}", True
            )
    for known in KNOWN_PE_SPONSORS:
        if known in normalized:
            return LenderClassification(
                LenderCategory.PRIVATE_EQUITY, 0.9, f"known PE sponsor: {known}", True
            )

    # 2. Near-certain exclusions — check before generic keyword matching
    for pattern in EXCLUDE_PATTERNS:
        if re.search(pattern, normalized):
            return LenderClassification(
                LenderCategory.EQUIPMENT_VENDOR, 0.85, f"exclude pattern: {pattern}", False
            )

    # 3. Keyword/suffix heuristics
    for pattern in RE_SUFFIX_PATTERNS:
        if re.search(pattern, normalized):
            return LenderClassification(
                LenderCategory.REAL_ESTATE, 0.6, f"RE pattern: {pattern}", True
            )
    for pattern in PE_SUFFIX_PATTERNS:
        if re.search(pattern, normalized):
            return LenderClassification(
                LenderCategory.PRIVATE_EQUITY, 0.6, f"PE pattern: {pattern}", True
            )

    # 4. Generic bank — ambiguous, flag for human review rather than drop
    if re.search(r"\bbank\b|\bbanking\b|\bn\.?a\.?\b", normalized):
        return LenderClassification(
            LenderCategory.BANK_GENERAL, 0.3, "generic bank — needs review", True
        )

    return LenderClassification(LenderCategory.UNKNOWN, 0.0, "no match", True)
    # NOTE: is_acquisition_relevant defaults True for UNKNOWN — better to
    # surface a maybe-relevant filing for human review than silently drop
    # a real acquisition signal because the lender name wasn't recognized.


def to_confidence_label(classification: LenderClassification) -> str:
    """Convert a LenderClassification to HIGH / MEDIUM / LOW string label.
    Used for storing confidence in ucc_filings.confidence column."""
    if classification.category == LenderCategory.EQUIPMENT_VENDOR:
        return "LOW"
    if not classification.is_acquisition_relevant:
        return "LOW"
    if classification.confidence >= 0.6:
        return "HIGH"
    if classification.confidence >= 0.3:
        return "MEDIUM"
    return "LOW"

# Runtime patch: add IRS and government tax liens to exclusions
EXCLUDE_PATTERNS.extend([
    r"\bsiemens\b",
    r"\bdiagnostic\b",
    r"\binternal revenue service\b",
    r"\birs\b",
    r"\bdepartment of treasury\b",
    r"\bnew york state tax\b",
    # Office/IT equipment vendors — file UCC-1s for copiers, printers, software
    r"\bduplicator\b",              # Duplicator Sales and Service (copier company)
    r"\binnoserv\b",                # Innoserv Solutions LLC (IT/software vendor)
    r"\bcanon financial\b",         # Canon Financial Services (copier/printer leasing)
    r"\bricoh\b",                   # Ricoh USA (office equipment)
    r"\bxerox\b",                   # Xerox Financial Services (copier leasing)
    r"\bkonica minolta\b",          # Konica Minolta Business Solutions
    r"\bpitney bowes\b",            # Pitney Bowes (postage/mailing equipment)
    r"\bdell financial\b",          # Dell Financial Services (IT equipment)
    r"\bhp financial\b",            # HP Financial Services (IT equipment)
    r"\bcisco systems\b",           # Cisco Systems Capital (networking)
    r"\bcrown credit\b",            # Crown Credit Company (forklift/industrial)
    r"\bmatco tools\b",             # Matco Tools Corp (hand tools, seen in ROBERT BUTLER deals)
    r"\bsnap.?on credit\b",         # Snap-on Credit LLC (tools)
    r"\bnew holland credit\b",      # New Holland Credit (farm/construction equipment)
    r"\bgreenpoint credit\b",       # GreenPoint Credit (consumer auto/personal)
    r"\bvanderbilt mortgage\b",     # Vanderbilt Mortgage & Finance (manufactured housing)
    r"\bmortgage electronic registration\b",  # MERS — residential mortgage registry
    r"\busaa federal savings\b",    # USAA (military personal banking, not healthcare RE)
    r"\bautomotive finance\b",      # Automotive Finance Corporation (dealer floor plan)
    # Registered agent abbreviations — file as secured party on behalf of real lender
    r"^csc[,\s]",                   # CSC (Corporation Service Company abbreviation)
    r"\bcsc as representative\b",
])

# HUD loans are healthcare RE financing — acquisition relevant
KNOWN_HEALTHCARE_REITS.update([
    "secretary of housing and urban development",
    "hud",
    "federal housing administration",
])
