"""
Claude-powered deal extractor.
Takes raw article text and returns a list of structured deal dicts.
One article can contain multiple deals (e.g. Dealbook roundups).
"""

import json
import logging
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import anthropic

from config import get_config

logger = logging.getLogger(__name__)
config = get_config()

client = anthropic.Anthropic(api_key=config.anthropic_api_key)

MIN_TEXT_WORDS = 100        # articles under this are likely paywalled/truncated
MIN_TEXT_CHARS = 150        # hard minimum

EXTRACTION_PROMPT = """You are a health policy research assistant extracting nursing home acquisition deals from news articles.

Extract ALL distinct deals mentioned in the article. A single article may describe multiple separate transactions.

For each deal, return a JSON object. Return an array even if there is only one deal.

Each deal object must have these fields (use null if not mentioned):
{{
  "acquiring_entity": "name of buyer/acquirer",
  "seller_entity": "name of seller",
  "operator_names": ["list of operator or management company names"],
  "facility_names": ["list of specific facility names"],
  "states": ["2-letter state codes where facilities are located"],
  "facility_count": <integer number of facilities>,
  "deal_value_m": <deal price in millions as a number, e.g. 82.4>,
  "acquisition_date": "YYYY-MM-DD if mentioned, else null",
  "financing_amount_m": <financing/loan amount in millions if mentioned>,
  "lender": "name of lender or financing entity if mentioned",
  "rationale": "1-2 sentence summary of this specific deal"
}}

Rules:
- Split broker/lender announcements from acquisition deals (they are separate deals)
- If the acquirer is described as "unnamed" or "undisclosed", use null for acquiring_entity
- Include parent company names (e.g. "Welltower" even if the actual seller was "an affiliate of Welltower")
- States should be 2-letter codes only: ["VA", "CO", "AL"]
- deal_value_m and financing_amount_m must be numbers, not strings
- Return ONLY the JSON array, no markdown, no explanation

Article:
{article_text}"""


def extract_deals(article_text: str, article_url: str = "") -> list[dict]:
    """
    Extract structured deal data from article text using Claude.
    Returns list of deal dicts. Returns empty list if text is too short
    (likely paywalled) or if extraction fails after retries.
    """
    if not article_text:
        logger.warning(f"Empty article text: {article_url}")
        return []

    word_count = len(article_text.split())
    char_count = len(article_text.strip())

    if char_count < MIN_TEXT_CHARS:
        logger.warning(f"Article too short ({char_count} chars), skipping: {article_url}")
        return []

    if word_count < MIN_TEXT_WORDS:
        logger.warning(
            f"Article likely paywalled/truncated ({word_count} words < {MIN_TEXT_WORDS}), "
            f"flagging: {article_url}"
        )
        # Still attempt extraction — partial text sometimes has enough signal —
        # but tag the result so callers know to treat it with lower confidence.
        truncated = True
    else:
        truncated = False

    truncated_text = article_text[:8000]

    try:
        deals = _call_claude_with_retry(truncated_text, article_url)
    except json.JSONDecodeError as e:
        logger.error(f"Claude returned invalid JSON for {article_url}: {e}")
        return []
    except Exception as e:
        logger.error(f"Extraction failed after retries for {article_url}: {e}")
        return []

    normalized = []
    for deal in deals:
        n = _normalize_deal(deal)
        if n:
            if truncated:
                n["confidence"] = "low"   # downgrade confidence on truncated text
            normalized.append(n)

    logger.info(
        f"Extracted {len(normalized)} deal(s) from {article_url}"
        + (" [truncated]" if truncated else "")
    )
    return normalized


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError)),
    reraise=True,
)
def _call_claude_with_retry(article_text: str, article_url: str) -> list[dict]:
    """Call Claude API with exponential backoff retry on rate limits."""
    response = client.messages.create(
        model=config.claude_model,
        max_tokens=config.claude_max_tokens,
        messages=[{
            "role": "user",
            "content": EXTRACTION_PROMPT.format(article_text=article_text)
        }]
    )

    raw = response.content[0].text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    deals = json.loads(raw)
    if not isinstance(deals, list):
        deals = [deals]
    return deals


def _normalize_deal(raw: dict) -> Optional[dict]:
    """Clean and validate a raw deal dict from Claude."""
    # Must have at least some identifying info to be useful
    has_entity = any([
        raw.get("acquiring_entity"),
        raw.get("seller_entity"),
        raw.get("operator_names"),
        raw.get("facility_names"),
    ])
    has_location = bool(raw.get("states"))

    if not (has_entity or has_location):
        return None

    return {
        "acquiring_entity": _clean_str(raw.get("acquiring_entity")),
        "seller_entity":    _clean_str(raw.get("seller_entity")),
        "operator_names":   _clean_list(raw.get("operator_names")),
        "facility_names":   _clean_list(raw.get("facility_names")),
        "states":           _clean_states(raw.get("states")),
        "facility_count":   _clean_int(raw.get("facility_count")),
        "deal_value_m":     _clean_float(raw.get("deal_value_m")),
        "acquisition_date": _clean_date(raw.get("acquisition_date")),
        "financing_amount_m": _clean_float(raw.get("financing_amount_m")),
        "lender":           _clean_str(raw.get("lender")),
        "rationale":        _clean_str(raw.get("rationale")),
        "extraction_model": config.claude_model,
    }


def _clean_str(val) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s and s.lower() not in ("null", "none", "unknown", "unnamed", "undisclosed") else None


def _clean_list(val) -> list[str]:
    if not val:
        return []
    if isinstance(val, str):
        return [val.strip()] if val.strip() else []
    return [str(v).strip() for v in val if v and str(v).strip()]


def _clean_states(val) -> list[str]:
    states = _clean_list(val)
    # Ensure 2-letter uppercase codes only
    return [s.upper()[:2] for s in states if len(s.strip()) >= 2]


def _clean_int(val) -> Optional[int]:
    try:
        return int(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _clean_float(val) -> Optional[float]:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _clean_date(val) -> Optional[str]:
    if not val:
        return None
    s = str(val).strip()
    # Basic YYYY-MM-DD validation
    import re
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return None
