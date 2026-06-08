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
    r'\bOPCO\b',  # operating company — common in PE deals
]

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
      620 HEATHWOOD DRIVE OPCO LLC  ->  620 Heathwood Drive
      CASCADIA HEALTHCARE INC       ->  Cascadia Healthcare
      Welltower Inc.                ->  Welltower
    """
    if not name:
        return name

    cleaned = name.strip()

    # Check known name map first
    if cleaned.upper() in NAME_MAP:
        return NAME_MAP[cleaned.upper()]

    # Strip trailing punctuation and commas
    cleaned = cleaned.rstrip('.,;')

    # Remove legal suffixes
    for suffix in LEGAL_SUFFIXES:
        cleaned = re.sub(suffix, '', cleaned, flags=re.IGNORECASE).strip()
        cleaned = cleaned.rstrip('.,;').strip()

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
        # Keep abbreviations uppercase (2-3 chars like LLC, SNF, NH)
        if len(word) <= 3 and word.isalpha():
            result.append(word.upper() if i == 0 else word.lower()
                         if word.lower() in LOWERCASE_WORDS else word.capitalize())
        elif i == 0:
            result.append(word.capitalize())
        elif word.lower() in LOWERCASE_WORDS:
            result.append(word.lower())
        else:
            result.append(word.capitalize())
    return ' '.join(result)


def normalize_deal(deal: dict) -> dict:
    """Apply name normalization to all entity fields in a deal dict."""
    if deal.get('acquiring_entity'):
        deal['acquiring_entity'] = normalize_entity_name(deal['acquiring_entity'])
    if deal.get('seller_entity'):
        deal['seller_entity'] = normalize_entity_name(deal['seller_entity'])
    if deal.get('operator_names'):
        deal['operator_names'] = [normalize_entity_name(n) for n in deal['operator_names']]
    return deal
