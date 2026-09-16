"""
Claude-powered deal extractor.
Takes raw article text and returns a list of structured deal dicts.
One article can contain multiple deals (e.g. Dealbook roundups).
"""

import json
import logging
import re
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

This article was published on: {published_date}

Extract ALL distinct deals mentioned in the article. A single article may describe multiple separate transactions.

For each deal, return a JSON object. Return an array even if there is only one deal. If there are no
qualifying deals (see Scope and Staleness rules below), return an empty array [].

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

Scope — only extract deals where the acquired asset is one of: a nursing home, skilled nursing
facility (SNF), assisted living facility, memory care community, continuing care retirement
community (CCRC), or a senior/post-acute housing portfolio containing such facilities.
Do NOT extract a deal whose acquired asset is:
- healthcare technology/software (EHR, practice management, care-coordination platforms)
- a hospital, physician practice, or medical group with no SNF/AL/memory-care component
- a hospice-only or home-health-only provider with no SNF/AL/memory-care facilities involved
- anything outside healthcare entirely (real estate, retail, fitness, etc. mentioned only in
  passing or by a shared address/developer)
If unsure whether the asset qualifies, exclude it rather than guess.

Staleness — only extract a deal if the article is reporting it as current news (an announcement,
a closing, a filing). Do NOT extract a deal that the article mentions only as past background — e.g.
an executive recounting "we acquired X earlier this year / last year / in [past month]" during an
interview, retrospective, or profile piece that isn't primarily about that transaction.

This exclusion is UNCONDITIONAL and applies even when the deal is described in specific, enthusiastic,
or detailed terms — an executive fondly recapping a months-old deal in a podcast/interview is still
NOT news, no matter how much color they give. Concretely: subtract the deal's stated or implied date
from the published date above. If the gap is more than ~60 days, you MUST exclude the deal — do not
extract it "just in case," do not include it with a low confidence, leave it out of the array entirely.
Example: an August 2026 podcast interview where the COO says "we completed a large acquisition in
December 2025, adding 14 campuses" — this is old news being recapped, not new news. Exclude it, even
though the acquirer, target, and facility count are all clearly stated.
Two exceptions where past-tense phrasing does NOT mean stale:
1. An article explicitly structured as a news roundup/dealbook of recent transactions
   (e.g. "Skilled Nursing Dealbook: ...") reporting deals from the last ~1-2 weeks — those are
   current news even without an exact date, and should be extracted normally.
2. A company's own routine financial disclosure — an earnings release, earnings call transcript,
   10-Q, or 8-K exhibit — reporting investment activity it completed "during the quarter" or
   "subsequent to quarter end." That is the normal, current way REITs and operators report deals;
   don't treat "closed on," "completed," or "acquired" language in this context as backstory just
   because it's phrased in the past tense. The staleness rule targets a DIFFERENT pattern: a deal
   invoked as background color in an interview/profile/feature article about some other current
   topic, months or years after the fact (the December-2025-Kingston-deal-recapped-in-an-August-
   2026-podcast example above). A same-quarter or "subsequent to quarter end" earnings disclosure
   is not that pattern — extract it normally.

Outside those two exceptions, don't let a missing exact date become an excuse to include a stale
deal: phrases like "earlier this year," "last year," "previously acquired," "since acquiring,"
"following its acquisition of," or a deal named only as backstory for why a company is now doing
something else, are ALL signals of staleness on their own, with or without a resolvable date —
exclude those deals too.

Rules:
- Split broker/lender announcements from acquisition deals (they are separate deals)
- If the acquirer is described as "unnamed" or "undisclosed", use null for acquiring_entity
- Include parent company names (e.g. "Welltower" even if the actual seller was "an affiliate of Welltower")
- States should be 2-letter codes only: ["VA", "CO", "AL"]
- deal_value_m and financing_amount_m must be numbers, not strings
- Return ONLY the JSON array, no markdown, no explanation

Article:
{article_text}"""


def extract_deals(article_text: str, article_url: str = "", published_at=None) -> list[dict]:
    """
    Extract structured deal data from article text using Claude.
    Returns list of deal dicts. Returns empty list if text is too short
    (likely paywalled) or if extraction fails after retries.

    published_at (datetime/date/str, optional): the article's publish date,
    passed to Claude so it can tell fresh news apart from a stale deal
    mentioned as past background (see EXTRACTION_PROMPT's Staleness rule).
    Falls back to "unknown" when not available.
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
    published_date_str = str(published_at)[:10] if published_at else "unknown"

    try:
        deals = _call_claude_with_retry(truncated_text, article_url, published_date_str)
    except json.JSONDecodeError as e:
        logger.error(f"Claude returned invalid JSON for {article_url}: {e}")
        return []
    except Exception as e:
        logger.error(f"Extraction failed after retries for {article_url}: {e}")
        return []

    normalized = []
    for deal in deals:
        n = _normalize_deal(deal)
        if not n:
            continue
        if _looks_stale(n.get("rationale")):
            logger.info(
                f"Dropping stale-sounding deal ({n.get('acquiring_entity')!r}) from {article_url}: "
                f"rationale flagged by staleness backstop — {n.get('rationale')!r}"
            )
            continue
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
def _call_claude_with_retry(article_text: str, article_url: str, published_date_str: str = "unknown") -> list[dict]:
    """Call Claude API with exponential backoff retry on rate limits."""
    response = client.messages.create(
        model=config.claude_model,
        max_tokens=config.claude_max_tokens,
        messages=[{
            "role": "user",
            "content": EXTRACTION_PROMPT.format(article_text=article_text, published_date=published_date_str)
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


# Regex backstop for staleness: the prompt asks Claude to exclude deals it
# recognizes as past background (see EXTRACTION_PROMPT), but that instruction
# isn't followed 100% reliably — Claude sometimes writes a rationale that
# plainly says the deal is old ("earlier this year", "earlier in 2026") yet
# still returns it. Catch those phrasings here as a deterministic second pass
# rather than trusting prompt compliance alone.
_STALE_PHRASE_RE = re.compile(
    r"\b("
    r"earlier (this|that) year"
    r"|earlier in \d{4}"
    r"|last year"
    r"|previously acquired"
    r"|since (its|their|the) acquisition of"
    r"|since acquiring"
    r"|following (its|their) acquisition of"
    r"|completed (the |its |their )?acquisition (of|in) .{0,40}\b(19|20)\d{2}\b"
    r")\b",
    re.IGNORECASE,
)


def _looks_stale(rationale: Optional[str]) -> bool:
    if not rationale:
        return False
    return bool(_STALE_PHRASE_RE.search(rationale))


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
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return None
