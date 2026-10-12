"""
Entity name normalizer.
Cleans up raw legal entity names from CHOW, EDGAR, and news sources
into human-readable display names.
"""

import re

# Legal suffixes to strip from display names
LEGAL_SUFFIXES = [
    r'\bLLC\b', r'\bL\.L\.C\.\b',
    r'\bINC\b', r'\bINC\.\b', r'\bINCORPORATED\b',
    r'\bCORP\b', r'\bCORPORATION\b',
    r'\bLTD\b', r'\bL\.T\.D\.\b',
    r'\bLP\b', r'\bL\.P\.\b',
    r'\bPLLC\b', r'\bP\.L\.L\.C\.\b',
    r'\bREIT\b',
    r'\bCO\b',
    r'\bPTY\b',
]
# Operating/property-company markers. Stripped from "Cascadia Opco" but kept
# on names that start with a street number ("813 Keller Lane Opco"), which
# otherwise read as an address rather than a company.
OPCO_SUFFIXES = [r'\bOPCO\b', r'\bPROPCO\b']

# Known name mappings — raw legal name -> display name
# Add to this as you encounter common operators
NAME_MAP = {
    "COMMUNICARE HEALTH SERVICES": "Communicare Health Services",
    "WELLTOWER INC": "Welltower",
    "SABRA HEALTH CARE REIT INC": "Sabra Health Care REIT",
    "CARETRUST REIT INC": "CareTrust REIT",
    "OMEGA HEALTHCARE INVESTORS INC": "Omega Healthcare Investors",
    "THE ENSIGN GROUP INC": "The Ensign Group",
    "GENESIS HEALTHCARE INC": "Genesis Healthcare",
    "BROOKDALE SENIOR LIVING INC": "Brookdale Senior Living",
}


def normalize_entity_name(name: str) -> str:
    """
    Clean up a raw legal entity name into a readable display name.

    Examples:
      620 HEATHWOOD DRIVE OPCO LLC  ->  620 Heathwood Drive Opco LLC
      CASCADIA HEALTHCARE INC       ->  Cascadia Healthcare
      Welltower Inc.                ->  Welltower

    A name that starts with a street number keeps its legal suffixes:
    without them "101 W State Street Holdings LLC" or "6530 Democracy Blvd
    LLC" reads as an address, not a company.
    """
    if not name:
        return name

    cleaned = name.strip()

    # Check known name map first
    if cleaned.upper() in NAME_MAP:
        return NAME_MAP[cleaned.upper()]

    # Strip trailing punctuation and commas
    cleaned = cleaned.rstrip('.,;')

    # Remove legal suffixes, except from street-number names
    if not re.match(r'\d', cleaned):
        for suffix in LEGAL_SUFFIXES + OPCO_SUFFIXES:
            cleaned = re.sub(suffix, '', cleaned, flags=re.IGNORECASE).strip()
            cleaned = cleaned.rstrip('.,;').strip()

    # A suffix removed mid-string leaves its punctuation behind:
    # "First Atlantic Healthcare, Inc., First Atlantic Corporation, and LTC, LLC"
    # -> "First Atlantic Healthcare, ., First Atlantic , and LTC"
    cleaned = re.sub(r'\s+,', ',', cleaned)
    cleaned = re.sub(r',\s*\.', ',', cleaned)
    cleaned = re.sub(r'(,\s*)+,', ',', cleaned)
    cleaned = re.sub(r'[,\s]+(and|&)?$', '', cleaned, flags=re.IGNORECASE).strip()

    # Convert ALL CAPS to Title Case
    if cleaned.isupper():
        cleaned = _smart_title_case(cleaned)

    # Clean up extra whitespace
    cleaned = ' '.join(cleaned.split())

    return cleaned if cleaned else name


def _smart_title_case(text: str) -> str:
    """
    Title case that handles common exceptions.
    'OF THE AND' stay lowercase, abbreviations stay uppercase.
    """
    LOWERCASE_WORDS = {'of', 'the', 'and', 'at', 'in', 'for', 'to', 'a', 'an'}
    words = text.split()
    result = []
    for i, word in enumerate(words):
        # Legal suffixes kept on street-number names stay upper case
        if word.upper().strip('.,') in ('LLC', 'LP', 'LLP', 'PLLC'):
            result.append(word.upper())
        # Keep abbreviations uppercase (2-3 chars like LLC, SNF, NH)
        elif len(word) <= 3 and word.isalpha():
            result.append(word.upper() if i == 0 else word.lower()
                         if word.lower() in LOWERCASE_WORDS else word.capitalize())
        elif i == 0:
            result.append(word.capitalize())
        elif word.lower() in LOWERCASE_WORDS:
            result.append(word.lower())
        else:
            result.append(word.capitalize())
    return ' '.join(result)


# The 50 states plus DC. Claude extraction sometimes tags a news deal with a
# non-US region code (GB for a UK portfolio, NB/NL for Canadian provinces);
# four such deals reached the site and its "States covered" count.
US_STATE_CODES = frozenset("""
    AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO
    MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC
""".split())


# A deal Claude gives no US state for, whose summary places it abroad. The
# prompt says to skip non-US deals, but some got through with an empty
# states list (UK care homes, a German portfolio, an Ontario community).
NON_US_PLACE_RE = re.compile(
    r"\b(Germany|German|United Kingdom|UK|U\.K\.|Britain|(?<!New )England|Scotland|Wales|Ireland|"
    r"Canada|Canadian|Ontario|Quebec|British Columbia|Alberta|Australia|Australian|New Zealand|"
    r"Malaysia|Malaysian|Singapore|India|Japan|China|France|Spain|Italy|Netherlands|Belgium|"
    r"Sweden|Norway|Denmark|Finland)\b"
)


# Foreign company forms. "Limited Partnership" is a US form, so a bare
# "Limited" only counts at the end of the name (LNT Care Developments
# Holdings Limited, Chantry Retirement Homes Limited).
NON_US_ENTITY_RE = re.compile(r"\b(Limited|Ltd\.?|plc|GmbH|Sdn\.? Bhd\.?|Pty\.?)$", re.I)


def looks_non_us(deal: dict) -> bool:
    """No US state, and the summary names a foreign country or province or
    a party is a foreign company form. EDGAR deals have no summary, so the
    party names are the only clue there (CareTrust's UK purchase from LNT)."""
    if deal.get('states'):
        return False
    parties = [deal.get('acquiring_entity'), deal.get('seller_entity')]
    return (bool(NON_US_PLACE_RE.search(deal.get('rationale') or deal.get('summary') or ''))
            or any(p and NON_US_ENTITY_RE.search(p.strip().rstrip(',')) for p in parties))


def normalize_deal(deal: dict) -> dict:
    """Apply name normalization to all entity fields in a deal dict, and
    drop non-US state codes. A deal whose states were all non-US, or that
    has no state and a summary placing it abroad, gets _non_us_only so the
    caller can dismiss it."""
    if deal.get('states'):
        us = [s.upper() for s in deal['states'] if s and s.upper() in US_STATE_CODES]
        if not us:
            deal['_non_us_only'] = True
        deal['states'] = us
    elif looks_non_us(deal):
        deal['_non_us_only'] = True
    if deal.get('acquiring_entity'):
        deal['acquiring_entity'] = normalize_entity_name(deal['acquiring_entity'])
    if deal.get('seller_entity'):
        deal['seller_entity'] = normalize_entity_name(deal['seller_entity'])
    if deal.get('operator_names'):
        deal['operator_names'] = [normalize_entity_name(n) for n in deal['operator_names']]
    return deal
